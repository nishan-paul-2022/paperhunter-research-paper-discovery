#!/usr/bin/env python3
import argparse
import difflib
import json
import re
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = SCRIPT_DIR.parent / "archive" / "paperhunter.db"
LEASE_SECONDS = 1800

ROLES = ("baseline", "benchmark", "evaluation", "survey")
REJECT_CLASSES = ("off_domain", "security_other", "defensive_only", "vuln_detection_or_code_scan",
                  "generic_llm_security", "static_qa_benchmark", "llm_no_offense", "frontmatter")
CRITERIA = ("agent", "offense", "black_box", "long_horizon")
QUOTE_SOURCES = ("title", "abstract", "tldr")
QUOTE_MIN_WORDS, QUOTE_MAX_WORDS = 4, 45
REASON_MIN_CHARS, REASON_MAX_WORDS = 10, 60
MATCH_RATIO = 0.92

_UNDECIDED = ("NOT EXISTS (SELECT 1 FROM paper_accepted a WHERE a.paper_id = p.id) "
              "AND NOT EXISTS (SELECT 1 FROM paper_rejected r WHERE r.paper_id = p.id)")
_FETCHED = "EXISTS (SELECT 1 FROM paper_source s WHERE s.paper_id = p.id)"


def open_db():
    if not DB_PATH.exists():
        sys.exit(f"ERROR: database not found at {DB_PATH} (run archive/schema.py first)")
    conn = sqlite3.connect(DB_PATH, timeout=300, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_CHAR_MAP = {
    **dict.fromkeys(map(ord, "‐‑‒–—―−"), "-"),
    **dict.fromkeys(map(ord, "‘’‚‛′`´"), "'"),
    **dict.fromkeys(map(ord, "“”„‟″"), '"'),
    0x00AD: None,
}


def normtext(s):
    s = unicodedata.normalize("NFKC", s or "").casefold().translate(_CHAR_MAP)
    return " ".join(s.split())


def quote_in(quote, text):
    q, h = normtext(quote), normtext(text)
    if not q or not h:
        return False
    if q in h:
        return True
    sm = difflib.SequenceMatcher(None, q, h, autojunk=False)
    return sm.find_longest_match(0, len(q), 0, len(h)).size >= MATCH_RATIO * len(q)


def dossier(conn, pid):
    p = conn.execute("SELECT id, title FROM dblp_paper WHERE id = ?", (pid,)).fetchone()
    d = conn.execute("SELECT * FROM paper_detail WHERE paper_id = ?", (pid,)).fetchone()
    terms = {}
    for kind, name in conn.execute("SELECT kind, name FROM paper_term WHERE paper_id = ? "
                                   "ORDER BY kind, score DESC, name", (pid,)):
        terms.setdefault(kind, []).append(name)
    out = {"id": p["id"], "title": p["title"].rstrip(".")}
    if d is not None:
        out.update(abstract=d["abstract"], tldr=d["tldr"], work_type=d["work_type"],
                   is_paratext=bool(d["is_paratext"]) if d["is_paratext"] is not None else None)
    else:
        out.update(abstract=None, tldr=None)
    out.update(keywords=terms.get("keyword", []), topics=terms.get("topic", []),
               fields=terms.get("field", []))
    return out


def cmd_claim(a):
    conn = open_db()
    now = int(time.time())
    where, args = [_UNDECIDED, _FETCHED, "NOT EXISTS (SELECT 1 FROM review_claim c WHERE c.paper_id = p.id "
                   "AND c.until > ? AND c.owner <> ?)"], [now, a.owner]
    if a.slice:
        where.append(f"p.id IN (SELECT id FROM paper_inventory WHERE slice IN ({','.join('?' * len(a.slice))}))")
        args += a.slice
    conn.execute("BEGIN IMMEDIATE")
    ids = [r[0] for r in conn.execute(
        f"SELECT p.id FROM dblp_paper p LEFT JOIN review_claim c ON c.paper_id = p.id AND c.owner = ? "
        f"WHERE {' AND '.join(where)} ORDER BY c.paper_id IS NULL, p.id LIMIT ?",
        [a.owner, *args, a.n])]
    conn.executemany("INSERT OR REPLACE INTO review_claim (paper_id, owner, until) VALUES (?, ?, ?)",
                     [(pid, a.owner, now + LEASE_SECONDS) for pid in ids])
    conn.execute("COMMIT")
    print(json.dumps([dossier(conn, pid) for pid in ids], indent=1, ensure_ascii=False))
    if not ids:
        print("no papers left to claim", file=sys.stderr)


def validate(conn, rec, owner):
    if not isinstance(rec, dict):
        return ["each record must be a JSON object"]
    errs = []
    pid = rec.get("id")
    if not isinstance(pid, int) or isinstance(pid, bool):
        return ["id must be the integer paper id from claim"]
    claim = conn.execute("SELECT owner, until FROM review_claim WHERE paper_id = ?", (pid,)).fetchone()
    if claim is None or claim["owner"] != owner:
        errs.append(f"paper {pid} is not claimed by {owner}; claim it first")
    verdict, p = rec.get("verdict"), rec.get("p")
    if verdict not in ("accept", "reject"):
        errs.append("verdict must be 'accept' or 'reject'")
    if not isinstance(p, (int, float)) or isinstance(p, bool) or not 0 <= p <= 1:
        errs.append("p must be a number in [0, 1]")
        p = None
    reason = rec.get("reason")
    if not isinstance(reason, str) or len(reason.strip()) < REASON_MIN_CHARS:
        errs.append(f"reason must be at least {REASON_MIN_CHARS} characters")
    elif len(reason.split()) > REASON_MAX_WORDS:
        errs.append(f"reason has {len(reason.split())} words; the limit is {REASON_MAX_WORDS}")

    if verdict == "accept":
        if rec.get("role") not in ROLES:
            errs.append(f"accept needs role in {ROLES}")
        if p is not None and p < 0.5:
            errs.append("accept needs p >= 0.5")
        crit = rec.get("criteria")
        crit = crit if isinstance(crit, dict) else {}
        for k in CRITERIA:
            if crit.get(k) not in ("y", "p", "n", "u"):
                errs.append(f"criteria.{k} must be y, p, n or u")
        if crit.get("agent") != "y" or crit.get("offense") != "y":
            errs.append("accept needs criteria.agent = 'y' and criteria.offense = 'y'")
        if rec.get("role") == "baseline" and "n" in (crit.get("black_box"), crit.get("long_horizon")):
            errs.append("baseline must not have black_box or long_horizon = 'n'")
        quote, src = rec.get("quote"), rec.get("quote_src")
        if src not in QUOTE_SOURCES:
            errs.append(f"quote_src must be one of {QUOTE_SOURCES}")
        elif not isinstance(quote, str) or not QUOTE_MIN_WORDS <= len(quote.split()) <= QUOTE_MAX_WORDS:
            errs.append(f"quote must be {QUOTE_MIN_WORDS}-{QUOTE_MAX_WORDS} words copied from the {src}")
        else:
            text = dossier(conn, pid).get(src)
            if not text:
                errs.append(f"this paper has no stored {src}; quote another source")
            elif not quote_in(quote, text):
                errs.append(f"quote not found in the stored {src}; copy it verbatim")
    elif verdict == "reject":
        if rec.get("reject_class") not in REJECT_CLASSES:
            errs.append(f"reject needs reject_class in {REJECT_CLASSES}")
        if p is not None and p >= 0.5:
            errs.append("reject needs p < 0.5")
    return errs


def store(conn, rec, owner):
    now = int(time.time())
    if rec["verdict"] == "accept":
        c = rec["criteria"]
        conn.execute(
            "INSERT INTO paper_accepted (paper_id, role, p, agent, offense, black_box, long_horizon, quote, "
            "quote_src, reason, by, at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (rec["id"], rec["role"], float(rec["p"]), c["agent"], c["offense"], c["black_box"],
             c["long_horizon"], rec["quote"].strip(), rec["quote_src"], rec["reason"].strip(), owner, now))
    else:
        conn.execute("INSERT INTO paper_rejected (paper_id, reject_class, p, reason, by, at) VALUES (?,?,?,?,?,?)",
                     (rec["id"], rec["reject_class"], float(rec["p"]), rec["reason"].strip(), owner, now))
    conn.execute("DELETE FROM review_claim WHERE paper_id = ?", (rec["id"],))


def cmd_submit(a):
    try:
        records = json.loads(Path(a.file).read_text())
    except (OSError, ValueError) as e:
        sys.exit(f"ERROR: cannot read {a.file}: {e}")
    if isinstance(records, dict):
        records = [records]
    conn = open_db()
    results = []
    for rec in records:
        pid = rec.get("id") if isinstance(rec, dict) else None
        conn.execute("BEGIN IMMEDIATE")
        try:
            decided = isinstance(pid, int) and conn.execute(
                "SELECT 1 FROM paper_accepted WHERE paper_id = ? UNION ALL "
                "SELECT 1 FROM paper_rejected WHERE paper_id = ?", (pid, pid)).fetchone()
            errs = ["paper is already decided (use reopen to change it)"] if decided else validate(conn, rec, a.owner)
            if not errs:
                store(conn, rec, a.owner)
            conn.execute("COMMIT")
        except sqlite3.Error as e:
            conn.execute("ROLLBACK")
            errs = [f"database: {e}"]
        results.append({"id": pid, "ok": not errs, **({"errors": errs} if errs else {})})
    print(json.dumps(results, indent=1))
    bad = sum(not r["ok"] for r in results)
    print(f"{len(results) - bad} stored, {bad} rejected by validation", file=sys.stderr)
    sys.exit(1 if bad else 0)


def cmd_reopen(a):
    conn = open_db()
    conn.execute("BEGIN IMMEDIATE")
    n = 0
    for key in a.dblp_key:
        row = conn.execute("SELECT id FROM dblp_paper WHERE dblp_key = ?", (key,)).fetchone()
        if row is None:
            print(f"unknown dblp key {key}", file=sys.stderr)
            continue
        n += conn.execute("DELETE FROM paper_accepted WHERE paper_id = ?", (row[0],)).rowcount
        n += conn.execute("DELETE FROM paper_rejected WHERE paper_id = ?", (row[0],)).rowcount
    conn.execute("COMMIT")
    print(f"{n} decision(s) removed")


def cmd_status(a):
    conn = open_db()
    q = lambda sql, *args: conn.execute(sql, args).fetchone()[0]
    fetched = q("SELECT count(DISTINCT paper_id) FROM paper_source")
    acc, rej = q("SELECT count(*) FROM paper_accepted"), q("SELECT count(*) FROM paper_rejected")
    print(f"papers fetched     {fetched:>10,}")
    print(f"decided            {acc + rej:>10,}")
    print(f"  accepted         {acc:>10,}")
    for role, n in conn.execute("SELECT role, count(*) FROM paper_accepted GROUP BY role ORDER BY role"):
        print(f"    {role:14} {n:>10,}")
    print(f"  rejected         {rej:>10,}")
    for cls, n in conn.execute("SELECT reject_class, count(*) FROM paper_rejected GROUP BY 1 ORDER BY 2 DESC"):
        print(f"    {cls:28} {n:>10,}")
    print(f"open claims        {q('SELECT count(*) FROM review_claim WHERE until > ?', int(time.time())):>10,}")
    print(f"left to review     {q(f'SELECT count(*) FROM dblp_paper p WHERE {_FETCHED} AND {_UNDECIDED}'):>10,}")


def cmd_accepted(a):
    conn = open_db()
    rows = conn.execute(
        "SELECT a.role, a.p, d.dblp_key, d.year, d.venue, d.title, d.doi FROM paper_accepted a "
        "JOIN dblp_paper d ON d.id = a.paper_id "
        "ORDER BY CASE a.role WHEN 'baseline' THEN 0 WHEN 'benchmark' THEN 1 WHEN 'evaluation' THEN 2 ELSE 3 END, "
        "a.p DESC, d.dblp_key").fetchall()
    for r in rows:
        print(f"{r['role']:10} {r['p']:.2f}  {r['dblp_key']:32} {r['year']}  {r['title']}")
    print(f"{len(rows)} accepted papers", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("claim", help="lease undecided papers and print their details")
    c.add_argument("--owner", required=True)
    c.add_argument("--n", type=int, default=20)
    c.add_argument("--slice", action="append", help="only papers of these paper_inventory slices")
    s = sub.add_parser("submit", help="store decisions from a JSON file")
    s.add_argument("--owner", required=True)
    s.add_argument("--file", required=True)
    r = sub.add_parser("reopen", help="remove decisions so the papers are reviewed again")
    r.add_argument("dblp_key", nargs="+")
    sub.add_parser("status", help="progress and counts")
    sub.add_parser("accepted", help="list the accepted papers")
    a = ap.parse_args()
    {"claim": cmd_claim, "submit": cmd_submit, "reopen": cmd_reopen, "status": cmd_status,
     "accepted": cmd_accepted}[a.cmd](a)


if __name__ == "__main__":
    main()
