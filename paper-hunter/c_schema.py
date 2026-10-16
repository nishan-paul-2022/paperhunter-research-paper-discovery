"""Schema validation and review record validation.

Validates review records against evidence provenance rules:
  * level=abstract requires abstract row in database
  * level=fulltext requires fulltext row in database
  * quotes must match stored text (fuzzy match for title/abstract, shingle for fulltext)
  * verdict/role/p consistency enforced

Content only: reads title, abstract, fulltext, tier, prior. No bibliographic fields.
"""

import difflib
import re
import unicodedata

import b_db

ROLES = ("baseline", "benchmark", "evaluation", "survey")
REJECT_CLASSES = ("off_domain", "security_other", "defensive_only", "vuln_detection_or_code_scan",
                  "generic_llm_security", "static_qa_benchmark", "llm_no_offense", "frontmatter")
CRIT_VALUES = ("y", "p", "n", "u")
LEVELS = ("title", "abstract", "fulltext")
STAGES = ("review", "blind", "final")
VERDICTS = ("accept", "reject")
VS_PRIOR = ("agree", "override")

CRITERIA_KEYS = (("agent", "c_agent"), ("offense", "c_off"),
                 ("black_box", "c_bb"), ("long_horizon", "c_lh"))

QUOTE_MIN_WORDS, QUOTE_MAX_WORDS = 4, 45
REASON_MIN_CHARS, REASON_MAX_WORDS = 10, 60
MAX_SAME_REASON = 5
TITLE_ACCEPT_MIN_P = 0.6
PLACEHOLDER_QUOTES = ("research paper on", "paper on", "front matter and")

MATCH_RATIO = 0.92
SHINGLE = 4
FUZZY_MAX_CHARS = 20_000  # above this, difflib is never run (quadratic)

_DEPTH = {"title": 0, "abstract": 1, "fulltext": 2}
_WORD = re.compile(r"\w+")
_CHAR_MAP = {
    **dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"), "-"),
    **dict.fromkeys(map(ord, "\u2018\u2019\u201a\u201b\u2032\u0060\u00b4"), "'"),
    **dict.fromkeys(map(ord, "\u201c\u201d\u201e\u201f\u2033"), '"'),
    0x00AD: None,
}


# --------------------------------------------------------------------------- text matching

def normtext(s):
    """NFKC, casefold, dash and quote unification, collapsed whitespace."""
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFKC", s).casefold().translate(_CHAR_MAP)
    return " ".join(s.split())


def _grams(words):
    return zip(*(words[i:] for i in range(SHINGLE)))


def _shingle_coverage(q, h):
    """Fraction of the quote's word 4-grams that occur in the text (linear time)."""
    qw = _WORD.findall(q)
    if len(qw) < SHINGLE:
        return 0.0
    qg = list(_grams(qw))
    have = set(_grams(_WORD.findall(h)))
    return sum(g in have for g in qg) / len(qg)


def quote_in(quote, text, fulltext=None):
    """True when `quote` occurs in `text` after normalisation.

    Exact substring first. Then, for short texts, a difflib fallback (longest common block
    >= 92% of the quote); for full text, 4-gram word-shingle coverage >= 92%. `fulltext=None`
    picks the shingle method automatically when the text exceeds FUZZY_MAX_CHARS.
    """
    q, h = normtext(quote), normtext(text)
    if not q or not h:
        return False
    if q in h:
        return True
    if fulltext is None:
        fulltext = len(h) > FUZZY_MAX_CHARS
    if fulltext:
        return _shingle_coverage(q, h) >= MATCH_RATIO
    sm = difflib.SequenceMatcher(None, q, h, autojunk=False)
    return sm.find_longest_match(0, len(q), 0, len(h)).size >= MATCH_RATIO * len(q)


# --------------------------------------------------------------------------- record mapping

def _tok(v):
    """Strip strings (empty becomes None); pass anything else through for the validator."""
    if isinstance(v, str):
        return v.strip() or None
    return v


def to_row(rec, stage, owner):
    """Map a submitted JSON record to review-table columns (b_db.REVIEW_COLS). Never raises."""
    rec = rec if isinstance(rec, dict) else {}
    crit = rec.get("criteria")
    crit = crit if isinstance(crit, dict) else {}
    p = rec.get("p")
    reason = rec.get("reason")
    reason = reason.strip() if isinstance(reason, str) else ("" if reason is None else reason)
    row = {
        "paper_id": rec.get("id"),
        "stage": stage,
        "by": owner,
        "verdict": _tok(rec.get("verdict")),
        "p": float(p) if isinstance(p, (int, float)) and not isinstance(p, bool) else None,
        "role": _tok(rec.get("role")),
        "rclass": _tok(rec.get("reject_class")),
        "lvl": _tok(rec.get("level")),
        "qsrc": _tok(rec.get("quote_src")),
        "quote": _tok(rec.get("quote")),
        "vs_prior": _tok(rec.get("vs_prior")),
        "reason": reason,
        "at": b_db.now(),
    }
    for key, col in CRITERIA_KEYS:
        row[col] = _tok(crit.get(key))
    return row


# --------------------------------------------------------------------------- validation

def _one(conn, sql, *args):
    return conn.execute(sql, args).fetchone()


def _title_has_cues(title):
    """True when the title alone carries both the `ai` and `off_strong` cues."""
    try:
        from f_cues import cues
        found = cues(title, "")
    except Exception:
        return False
    return "ai" in found and "off_strong" in found


def _check_quote(row, errs, *, required, paper_title, abstract, fulltext):
    quote, qsrc, lvl = row["quote"], row["qsrc"], row["lvl"]
    if quote is None:
        if required:
            errs.append("quote is required (verbatim %d-%d words from the paper)"
                        % (QUOTE_MIN_WORDS, QUOTE_MAX_WORDS))
        return
    if not isinstance(quote, str):
        errs.append("quote must be a string")
        return
    n = len(quote.split())
    if not QUOTE_MIN_WORDS <= n <= QUOTE_MAX_WORDS:
        errs.append("quote has %d words; it must be %d-%d" % (n, QUOTE_MIN_WORDS, QUOTE_MAX_WORDS))
    if normtext(quote).startswith(PLACEHOLDER_QUOTES):
        errs.append("quote is a placeholder, not text copied from the paper")
    if qsrc not in LEVELS:
        errs.append("quote_src must be one of %s when a quote is given" % (LEVELS,))
        return
    if lvl in LEVELS and _DEPTH[qsrc] > _DEPTH[lvl]:
        errs.append("quote_src '%s' is deeper than level '%s'; raise level only after reading that text"
                    % (qsrc, lvl))
        return
    stored = {"title": paper_title, "abstract": abstract, "fulltext": fulltext}[qsrc]
    if stored is None:
        errs.append("quote_src '%s' has no stored text for this paper" % qsrc)
    elif not quote_in(quote, stored, fulltext=(qsrc == "fulltext")):
        errs.append("quote not found in the stored %s text; copy it verbatim" % qsrc)


def validate_review(conn, rec, stage, owner):
    """Return every problem with `rec` as short messages; [] means the record may be stored."""
    errs = []
    row = to_row(rec, stage, owner)
    rec = rec if isinstance(rec, dict) else {}
    if rec.get("criteria") is not None and not isinstance(rec["criteria"], dict):
        errs.append("criteria must be an object with agent/offense/black_box/long_horizon")

    verdict, p, role, rclass = row["verdict"], row["p"], row["role"], row["rclass"]
    lvl, qsrc = row["lvl"], row["qsrc"]
    accept, reject = verdict == "accept", verdict == "reject"

    if stage not in STAGES:
        errs.append("stage must be one of %s" % (STAGES,))
    pid = row["paper_id"]
    if not isinstance(pid, int) or isinstance(pid, bool):
        errs.append("id must be an integer paper id")
        pid = None
    if not (accept or reject):
        errs.append("verdict must be 'accept' or 'reject'")
    if p is None or not 0 <= p <= 1:
        errs.append("p must be a number in [0,1]")
    if lvl not in LEVELS:
        errs.append("level must be one of %s" % (LEVELS,))
    for key, col in CRITERIA_KEYS:
        if row[col] is not None and row[col] not in CRIT_VALUES:
            errs.append("criteria.%s must be one of %s" % (key, CRIT_VALUES))
    if row["vs_prior"] is not None and row["vs_prior"] not in VS_PRIOR:
        errs.append("vs_prior must be 'agree' or 'override'")

    if accept:
        if role not in ROLES:
            errs.append("accept needs role in %s" % (ROLES,))
        if rclass is not None:
            errs.append("accept must not carry reject_class")
        if p is not None and p < 0.5:
            errs.append("accept needs p >= 0.5")
        if row["c_agent"] != "y" or row["c_off"] != "y":
            errs.append("accept needs criteria.agent='y' and criteria.offense='y'")
        if role == "baseline":
            for key, col in CRITERIA_KEYS[2:]:
                if row[col] == "n":
                    errs.append("baseline must not have criteria.%s='n' (use 'p' or 'u' unless contradicted)" % key)
    elif reject:
        if rclass not in REJECT_CLASSES:
            errs.append("reject needs reject_class in %s" % (REJECT_CLASSES,))
        if role is not None:
            errs.append("reject must not carry role")
        if p is not None and p >= 0.5:
            errs.append("reject needs p < 0.5")

    reason = row["reason"]
    if not isinstance(reason, str):
        errs.append("reason must be a string")
    else:
        words = len(reason.split())
        if len(reason) < REASON_MIN_CHARS:
            errs.append("reason must be at least %d characters" % REASON_MIN_CHARS)
        elif words > REASON_MAX_WORDS:
            errs.append("reason has %d words; the limit is %d" % (words, REASON_MAX_WORDS))

    if pid is None:
        return errs
    paper = _one(conn, "SELECT id, title, dup_of FROM paper WHERE id=?", pid)
    if paper is None:
        return errs + ["unknown paper id %d" % pid]
    if paper["dup_of"] is not None:
        return errs + ["paper %d is a duplicate of %d and is not reviewed" % (pid, paper["dup_of"])]

    if stage in STAGES:
        task = _one(conn, "SELECT state, owner FROM task WHERE paper_id=? AND stage=?", pid, stage)
        if task is None:
            errs.append("no '%s' task exists for paper %d" % (stage, pid))
        elif task["state"] != "leased":
            errs.append("the '%s' task for paper %d is %s, not leased" % (stage, pid, task["state"]))
        elif task["owner"] != owner:
            errs.append("the '%s' task for paper %d is leased to another owner" % (stage, pid))

    if isinstance(reason, str) and reason and _one(
            conn, "SELECT COUNT(*) FROM review WHERE by=? AND reason=?", owner, reason)[0] > MAX_SAME_REASON:
        errs.append("this exact reason was already submitted more than %d times; write a specific reason"
                    % MAX_SAME_REASON)

    abs_row = _one(conn, "SELECT text FROM abstract WHERE paper_id=?", pid)
    has_ft = _one(conn, "SELECT 1 FROM fulltext WHERE paper_id=?", pid) is not None
    tri = _one(conn, "SELECT tier, prior FROM triage WHERE paper_id=?", pid)
    tier = tri["tier"] if tri else 0
    prior = tri["prior"] if tri else None

    if lvl == "abstract" and abs_row is None:
        errs.append("level 'abstract' but no abstract is stored for paper %d" % pid)
    elif lvl == "fulltext" and not has_ft:
        errs.append("level 'fulltext' but no full text is stored for paper %d" % pid)
    elif lvl == "title":
        if reject and abs_row is not None:
            errs.append("level 'title' is not allowed for a reject: an abstract is stored; read it and use level 'abstract'")
        elif accept:
            if stage != "final":
                errs.append("an accept at level 'title' is allowed only at stage 'final'")
            if qsrc != "title":
                errs.append("an accept at level 'title' needs quote_src='title'")
            if p is not None and p < TITLE_ACCEPT_MIN_P:
                errs.append("an accept at level 'title' needs p >= %s" % TITLE_ACCEPT_MIN_P)
            if not _title_has_cues(paper["title"]):
                errs.append("an accept at level 'title' needs a title with both AI and strong-offence cues")

    _check_quote(
        row, errs, required=accept or (reject and tier >= 2),
        paper_title=paper["title"],
        abstract=abs_row["text"] if abs_row else None,
        fulltext=(_one(conn, "SELECT text FROM fulltext WHERE paper_id=?", pid)["text"]
                  if has_ft and qsrc == "fulltext" else None),
    )

    if stage == "blind":
        if row["vs_prior"] is not None:
            errs.append("vs_prior must be absent at stage 'blind'")
    elif stage in ("review", "final") and prior is not None and row["vs_prior"] is None:
        errs.append("vs_prior ('agree' or 'override') is required: this paper has an advisory prior")

    return errs
