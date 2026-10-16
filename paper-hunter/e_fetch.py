"""e_fetch.py: abstract fetch passes for PAPER-HUNTER v3 (content only).

Pass 1 (`deep=False`): OpenAlex batches feed an S2 batch worker; both run concurrently.
Pass 2 (`deep=True`): per-paper provider chain for papers whose title triage tier is >= 1.
Resumability is `fetch_state.tried` (a provider that answered "nothing" is marked, a provider
that could not be reached is not). Only retrieval handles (doi, link, pdf, arXiv id) and
title/abstract text are used.
"""

import queue
import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import b_db
import d_network as net

PROBE_N = 6      # this many network failures before any success aborts the run
CHUNK = 300      # papers per DB commit
EVERY = 200      # progress interval (papers)
_DONE = object()


class _Probe:
    """Early network probe: abort when the first PROBE_N requests all fail."""

    def __init__(self):
        self.lock, self.ok_n, self.fail_n = threading.Lock(), 0, 0
        self.stop = threading.Event()

    def ok(self):
        with self.lock:
            self.ok_n += 1

    def fail(self):
        with self.lock:
            self.fail_n += 1
            if self.ok_n == 0 and self.fail_n >= PROBE_N:
                self.stop.set()


class _Sink:
    """Thread-safe buffered writer: abstracts + fetch_state marks, committed in chunks."""

    def __init__(self, conn, total, progress):
        self.conn, self.total, self.progress = conn, total, progress
        self.lock, self.found, self.marks = threading.Lock(), [], []
        self.by_src, self.processed = Counter(), 0

    def add(self, found, marks, papers):
        """found: [(pid, text, src)]; marks: [(pid, bits, pdf, arxiv)]; papers: papers completed."""
        with self.lock:
            self.found += found
            self.marks += marks
            self.by_src.update(src for _, _, src in found)
            before, self.processed = self.processed, self.processed + papers
            if len(self.marks) >= CHUNK:
                self._flush()
            if self.processed // EVERY > before // EVERY:
                self.progress("fetch: %d/%d papers, %d abstracts"
                              % (self.processed, self.total, sum(self.by_src.values())))

    def flush(self):
        with self.lock:
            self._flush()

    def _flush(self):
        if not (self.found or self.marks):
            return
        with b_db.tx(self.conn):
            b_db.put_abstracts(self.conn, self.found)
            b_db.mark_fetched(self.conn, self.marks)
        self.found, self.marks = [], []

    def counts(self, candidates, probe):
        return {"candidates": candidates, "abstracts_found": sum(self.by_src.values()),
                "by_src": dict(self.by_src), "aborted": probe.stop.is_set()}


def _usable(rec):
    """Abstract text of a provider result if long enough to store, else None."""
    text = re.sub(r"\s+", " ", (rec or {}).get("abstract") or "").strip()
    return text if len(text) >= b_db.MIN_ABSTRACT else None


def _abort_message(progress):
    progress("ERROR: early network probe failed (%d requests, none succeeded). Check the "
             "internet connection and re-run. Nothing was marked as tried." % PROBE_N)


def _shutdown(ex):
    ex.shutdown(wait=True, cancel_futures=True)


# ----------------------------------------------------------------------------- pass 1

def _pass1(conn, workers, progress):
    cands = b_db.fetch_candidates(conn, deep=False)
    by_doi = {}
    for c in cands:
        by_doi.setdefault(c["doi"].lower(), []).append(c["id"])
    dois = sorted(by_doi)
    progress("fetch: %d papers (%d DOIs) without abstract" % (len(cands), len(dois)))
    probe, sink = _Probe(), _Sink(conn, len(cands), progress)
    if not dois:
        return sink.counts(0, probe)
    oa_handles = {}          # doi -> (pdf, arxiv) from OpenAlex, kept until S2 has answered
    s2_queue = queue.Queue()

    def handles(doi, rec):
        pdf, arx = oa_handles.get(doi, (None, None))
        return (pdf or (rec or {}).get("pdf"), arx or (rec or {}).get("arxiv"))

    def s2_send(batch):
        if probe.stop.is_set():
            return
        try:
            out = net.s2_batch(batch)
        except net.NetworkError:
            probe.fail()
            # Not tried: keep the handles only, so a re-run repeats OpenAlex + S2 for these.
            sink.add([], [(pid, 0, *handles(d, None)) for d in batch for pid in by_doi[d]],
                     sum(len(by_doi[d]) for d in batch))
            return
        probe.ok()
        found, marks = [], []
        for d in batch:
            rec, text = out.get(d), _usable(out.get(d))
            for pid in by_doi[d]:
                if text:
                    found.append((pid, text, "s2"))
                marks.append((pid, b_db.TRIED_OPENALEX | b_db.TRIED_S2, *handles(d, rec)))
        sink.add(found, marks, len(marks))

    def s2_worker():
        buf = []
        while True:
            item = s2_queue.get()
            if item is not _DONE:
                buf.append(item)
            if len(buf) >= net.S2_BATCH or (item is _DONE and buf):
                s2_send(buf)
                buf = []
            if item is _DONE:
                return

    def oa_task(batch):
        if probe.stop.is_set():
            return None
        try:
            out = net.openalex_batch(batch)
        except net.NetworkError:
            probe.fail()
            return None
        probe.ok()
        return out

    batches = [dois[i:i + net.OA_BATCH] for i in range(0, len(dois), net.OA_BATCH)]
    s2_ex, oa_ex = ThreadPoolExecutor(1), ThreadPoolExecutor(max(1, workers))
    s2_fut = s2_ex.submit(s2_worker)
    try:
        futs = {oa_ex.submit(oa_task, b): b for b in batches}
        for fut in as_completed(futs):
            out = fut.result()
            if out is None:
                continue
            found, marks = [], []
            for d in futs[fut]:
                rec, text = out.get(d), _usable(out.get(d))
                if text:
                    pids = by_doi[d]
                    found += [(pid, text, "openalex") for pid in pids]
                    marks += [(pid, b_db.TRIED_OPENALEX, rec.get("pdf"), rec.get("arxiv")) for pid in pids]
                else:
                    if rec:
                        oa_handles[d] = (rec.get("pdf"), rec.get("arxiv"))
                    s2_queue.put(d)
            if found:
                sink.add(found, marks, len(marks))
    finally:
        _shutdown(oa_ex)
        s2_queue.put(_DONE)
        s2_fut.result()
        _shutdown(s2_ex)
        sink.flush()
    if probe.stop.is_set():
        _abort_message(progress)
    return sink.counts(len(cands), probe)


# ----------------------------------------------------------------------------- pass 2

def _plan(c):
    """Providers still to try for candidate c: [(bit, callable)] in order."""
    tried, doi, tier = c["tried"], c["doi"], c["tier"] or 0
    plan = []
    if not doi and not tried & b_db.TRIED_OPENALEX:
        plan.append((b_db.TRIED_OPENALEX, lambda: net.openalex_title(c["title"])))
    if not tried & b_db.TRIED_CROSSREF:
        plan.append((b_db.TRIED_CROSSREF, lambda: net.crossref(doi=doi, title=None if doi else c["title"])))
    url = c["link"] or (("https://doi.org/" + doi) if doi else None)
    if url and not tried & b_db.TRIED_LANDING:
        plan.append((b_db.TRIED_LANDING, lambda: net.landing(url)))
    if tier >= 2 and not tried & b_db.TRIED_ARXIV:
        plan.append((b_db.TRIED_ARXIV, lambda: net.arxiv(c["title"])))
    return plan


_SRC = {b_db.TRIED_OPENALEX: "openalex", b_db.TRIED_CROSSREF: "crossref",
        b_db.TRIED_LANDING: "landing", b_db.TRIED_ARXIV: "arxiv"}


def _deep_one(c, plan, probe):
    """Run the provider chain for one paper -> (found, marks) ready for the sink, or None if aborted."""
    bits, pdf, arx, found = 0, None, None, []
    for bit, call in plan:
        if probe.stop.is_set():
            return None
        try:
            rec = call()
        except net.NetworkError:
            probe.fail()
            continue
        probe.ok()
        bits |= bit
        if rec:
            pdf, arx = pdf or rec.get("pdf"), arx or rec.get("arxiv")
        text = _usable(rec)
        if text:
            found.append((c["id"], text, _SRC[bit]))
            break
    return found, [(c["id"], bits, pdf, arx)] if (bits or pdf or arx) else []


def _pass2(conn, workers, progress):
    probe = _Probe()
    if not conn.execute("SELECT 1 FROM triage LIMIT 1").fetchone():
        progress("fetch --deep needs triage first: run `triage` (no triage rows found).")
        return _Sink(conn, 0, progress).counts(0, probe)
    todo = [(c, p) for c in b_db.fetch_candidates(conn, deep=True, min_tier=1) if (p := _plan(c))]
    progress("fetch --deep: %d tier>=1 papers without abstract" % len(todo))
    sink = _Sink(conn, len(todo), progress)
    ex = ThreadPoolExecutor(max(1, workers))
    try:
        futs = [ex.submit(_deep_one, c, p, probe) for c, p in todo]
        for fut in as_completed(futs):
            res = fut.result()
            if res:
                sink.add(res[0], res[1], 1)
    finally:
        _shutdown(ex)
        sink.flush()
    if probe.stop.is_set():
        _abort_message(progress)
    else:
        b_db.meta_set(conn, "deep_fetch_done", "1")
    return sink.counts(len(todo), probe)


def run_fetch(conn, *, deep=False, workers=16, progress=print):
    """Fetch missing abstracts. Returns {"candidates", "abstracts_found", "by_src", "aborted"}.

    deep=False: OpenAlex then S2 batches by DOI. deep=True: Crossref, landing page, arXiv (and
    OpenAlex title search) for tier >= 1 papers; sets meta `deep_fetch_done` on a complete run.
    """
    return (_pass2 if deep else _pass1)(conn, workers, progress)
