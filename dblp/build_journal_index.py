"""
build_journal_index.py  -  Build a dblp_key → journal_name index from dblp.xml.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHY THIS EXISTS
    DBLP's web API is protected by bot-detection and cannot be used
    programmatically. Instead, we read the local dblp.xml (already
    downloaded) to build the mapping we need — no internet required.

WHAT IT DOES
    Streams through dblp.xml and collects every <article> record.
    For each article it extracts:
        key prefix  →  e.g.  journals/tods
        journal name →  e.g.  ACM Trans. Database Syst.

    Then it fuzzy-matches those journal names against the full titles
    in SJR-WoS-2025-CS-clean.csv to produce the final lookup table:
        dblp_key_prefix , journal_name_in_dblp , sjr_title , quartile

    Output: journal_index.csv  (used by the paper extractor)

OUTPUTS
    dblp/journal_index.csv
        Columns: dblp_key, dblp_journal_name, sjr_title, quartile
        - dblp_key          e.g.  journals/tods
        - dblp_journal_name exact text from <journal> element in XML
        - sjr_title         matched title from SJR CSV (empty = no match)
        - quartile          Q1/Q2/Q3/Q4 from SJR CSV (empty = no match)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Usage:
    python3 dblp/build_journal_index.py
    python3 dblp/build_journal_index.py --xml /path/to/dblp.xml
    python3 dblp/build_journal_index.py --threshold 0.85
"""

import argparse
import csv
import os
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

try:
    from lxml import etree
except ImportError:
    sys.exit("Missing lxml.  Run:  pip install lxml")

# ── paths ────────────────────────────────────────────────────────────────────
DBLP_DIR  = Path(__file__).resolve().parent
REPO_ROOT = DBLP_DIR.parent

XML_PATH   = DBLP_DIR  / "dblp.xml"
SJR_CLEAN  = DBLP_DIR  / "SJR-WoS-2025-CS-clean.csv"
OUT_INDEX  = DBLP_DIR  / "journal_index.csv"

OUT_FIELDS = ["dblp_key", "dblp_journal_name", "sjr_title", "quartile"]

# ── text normalisation ────────────────────────────────────────────────────────

# Common DBLP abbreviations → full words
# Applied BEFORE lowercasing so word-boundary matching works correctly.
_ABBREV = [
    (r"\bTrans\b\.?",       "Transactions"),
    (r"\bJ\b\.?",           "Journal"),
    (r"\bInt\b\.?",         "International"),
    (r"\bComput\b\.?",      "Computer"),
    (r"\bSyst\b\.?",        "Systems"),
    (r"\bEng\b\.?",         "Engineering"),
    (r"\bSci\b\.?",         "Science"),
    (r"\bTechnol\b\.?",     "Technology"),
    (r"\bAppl\b\.?",        "Applied"),
    (r"\bProc\b\.?",        "Proceedings"),
    (r"\bMach\b\.?",        "Machine"),
    (r"\bIntell\b\.?",      "Intelligence"),
    (r"\bArtif\b\.?",       "Artificial"),
    (r"\bInf\b\.?",         "Information"),
    (r"\bCommun\b\.?",      "Communications"),
    (r"\bAnal\b\.?",        "Analysis"),
    (r"\bRecogn\b\.?",      "Recognition"),
    (r"\bVis\b\.?",         "Vision"),
    (r"\bNetw\b\.?",        "Networks"),
    (r"\bSignal\b",         "Signal"),
    (r"\bImage\b",          "Image"),
    (r"\bParallel\b",       "Parallel"),
    (r"\bDistrib\b\.?",     "Distributed"),
    (r"\bAuton\b\.?",       "Autonomous"),
    (r"\bEduc\b\.?",        "Education"),
    (r"\bLett\b\.?",        "Letters"),
    (r"\bManag\b\.?",       "Management"),
    (r"\bAdv\b\.?",         "Advanced"),
    (r"\bAI\b",             "Artificial Intelligence"),
    (r"\bACM\b",            "ACM"),
    (r"\bIEEE\b",           "IEEE"),
    (r"\bProc\b\.?",        "Proceedings"),
    (r"\bMed\b\.?",         "Medical"),
    (r"\bBiol\b\.?",        "Biological"),
    (r"\bBioinform\b\.?",   "Bioinformatics"),
    (r"\bNeur\b\.?",        "Neural"),
    (r"\bRobot\b\.?",       "Robotics"),
    (r"\bOptim\b\.?",       "Optimization"),
    (r"\bSimul\b\.?",       "Simulation"),
    (r"\bSoftw\b\.?",       "Software"),
    (r"\bAlgor\b\.?",       "Algorithms"),
    (r"\bArchit\b\.?",      "Architecture"),
    (r"\bSecur\b\.?",       "Security"),
    (r"\bBehav\b\.?",       "Behavior"),
    (r"\bCogn\b\.?",        "Cognitive"),
    (r"\bRes\b\.?",         "Research"),
    (r"\bPract\b\.?",       "Practice"),
]

_STOP_WORDS = {
    "a", "an", "the", "and", "or", "of", "in", "on", "at", "to",
    "for", "with", "by", "from", "its", "&",
}

def normalize(text: str) -> str:
    """
    Normalise a journal name for fuzzy comparison:
    1. Expand common abbreviations  (done BEFORE lowercasing)
    2. Lowercase and strip punctuation
    3. Remove stop-words  (so 'Journal of X' and 'Journal X' compare equally)
    """
    for pattern, replacement in _ABBREV:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    tokens = [t for t in text.split() if t not in _STOP_WORDS]
    return " ".join(tokens)


def score(a: str, b: str) -> float:
    """
    Combined similarity designed for abbreviated DBLP names vs full SJR titles.

    Strategy:
      token_sim  = max(symmetric_jaccard, min_side_containment)
      final      = min(token_sim, seq_ratio)

    symmetric_jaccard    — high when both strings share most tokens (full-name matches)
    min_side_containment — high when the SHORTER string's tokens are mostly in the longer
                           string; handles abbreviated → full expansion correctly while
                           still rejecting true subset false-positives:
                             'Bioinformatics' ⊂ 'Advances in Bioinformatics'
                             containment of shorter in longer = 1.0 BUT
                             containment of longer in shorter = 0.33  → min = 0.33 ✗

    seq_ratio            — guards against token-set collisions on short strings
    """
    ta = set(a.split())
    tb = set(b.split())
    if not ta or not tb:
        return 0.0

    # Symmetric Jaccard
    jaccard = len(ta & tb) / len(ta | tb)

    # Min-side containment: fraction of the SMALLER set covered by the LARGER
    # (penalises cases where one string is a true subset of the other)
    shared = len(ta & tb)
    contain_a_in_b = shared / len(ta)   # how much of a is in b
    contain_b_in_a = shared / len(tb)   # how much of b is in a
    containment = min(contain_a_in_b, contain_b_in_a)   # symmetric: both must cover each other

    token_sim = max(jaccard, containment)

    # Character-level SequenceMatcher as a sanity gate
    seq = SequenceMatcher(None, a, b, autojunk=False).ratio()

    return min(token_sim, seq)


# ── step 1: scan XML for journal key → name mapping ──────────────────────────

CACHE_PATH = DBLP_DIR / "journal_key_cache.json"   # saved after first XML scan

def scan_xml(xml_path: Path) -> dict[str, str]:
    """
    Stream dblp.xml and collect unique:
        key_prefix  →  journal_name
    from every <article> record that has both a key and a <journal> child.

    Returns a dict  {  "journals/tods": "ACM Trans. Database Syst.", ... }
    """
    print(f"Scanning {xml_path}  ({xml_path.stat().st_size // 1_000_000} MB)...")
    index: dict[str, str] = {}
    seen = 0

    size = os.path.getsize(xml_path)
    f = open(xml_path, "rb")
    ctx = etree.iterparse(
        f,
        events=("end",),
        tag="article",
        load_dtd=True,
        resolve_entities=True,
        huge_tree=True,
    )

    for _, el in ctx:
        seen += 1
        key = el.get("key", "")
        journal = el.findtext("journal") or ""
        if key.startswith("journals/") and journal:
            prefix = "/".join(key.split("/")[:2])   # e.g. journals/tods
            if prefix not in index:
                index[prefix] = journal.strip()

        if seen % 500_000 == 0:
            pct = f.tell() * 100 // size
            print(f"  {pct:3}%  scanned  |  {len(index):,} journal keys found",
                  end="\r", flush=True)

        el.clear()
        while el.getprevious() is not None:
            del el.getparent()[0]

    f.close()
    print(f"\n  100%  done  |  {len(index):,} unique journal key prefixes found\n")
    return index


def load_dblp_index(xml_path: Path) -> dict[str, str]:
    """
    Return the dblp_key → journal_name mapping.
    Loads from JSON cache if available (avoids re-scanning the 5 GB XML).
    Runs the full XML scan and saves cache on first call.
    """
    import json
    if CACHE_PATH.exists():
        print(f"Loading XML scan cache from {CACHE_PATH.name}  (delete to re-scan)...")
        with open(CACHE_PATH, encoding="utf-8") as f:
            index = json.load(f)
        print(f"  {len(index):,} journal keys loaded from cache\n")
        return index

    index = scan_xml(xml_path)

    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    print(f"Cache saved → {CACHE_PATH.name}\n")
    return index


# ── step 2: load SJR titles ───────────────────────────────────────────────────

def load_sjr(sjr_path: Path) -> list[dict]:
    """
    Load SJR-WoS-2025-CS-clean.csv.
    Returns list of dicts with keys: Title, Issn_1, Issn_2, Quartile, Coverage.
    Also adds a 'norm' key with the normalised title for matching.
    """
    rows = []
    with open(sjr_path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter=","):
            row["norm"] = normalize(row["Title"])
            rows.append(row)
    print(f"Loaded {len(rows)} SJR journal titles from {sjr_path.name}\n")
    return rows


# ── step 3: fuzzy match ───────────────────────────────────────────────────────

def match_journals(
    dblp_index: dict[str, str],
    sjr_rows: list[dict],
    threshold: float,
) -> list[dict]:
    """
    For each DBLP key prefix, find the best-matching SJR title.
    Returns list of output rows (all DBLP keys, matched or not).
    """
    # Pre-normalise SJR titles
    sjr_norms = [(r["norm"], r) for r in sjr_rows]

    results = []
    matched = 0
    total   = len(dblp_index)

    for n, (dblp_key, dblp_jname) in enumerate(sorted(dblp_index.items()), 1):
        norm_dblp = normalize(dblp_jname)

        best_score = 0.0
        best_row   = None
        for norm_sjr, sjr_row in sjr_norms:
            s = score(norm_dblp, norm_sjr)
            if s > best_score:
                best_score = s
                best_row   = sjr_row

        if best_score >= threshold and best_row is not None:
            sjr_title = best_row["Title"]
            quartile  = best_row["Quartile"]
            matched  += 1
        else:
            sjr_title = ""
            quartile  = ""

        results.append({
            "dblp_key":          dblp_key,
            "dblp_journal_name": dblp_jname,
            "sjr_title":         sjr_title,
            "quartile":          quartile,
        })

        if n % 500 == 0 or n == total:
            print(f"  Matched {n}/{total} keys  ({matched} with SJR entry)",
                  end="\r", flush=True)

    print(f"\n  Done.  {matched}/{total} DBLP journal keys matched to SJR titles.")
    return results


# ── step 4: write output ──────────────────────────────────────────────────────

def write_index(rows: list[dict], out_path: Path) -> None:
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUT_FIELDS, delimiter=",",
                                quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nIndex written → {out_path}")
    print("Tip: open journal_index.csv, review 'sjr_title' matches,")
    print("     correct any wrong matches manually, then run the extractor.")


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--xml",       default=str(XML_PATH),
                    help=f"Path to dblp.xml  (default: {XML_PATH})")
    ap.add_argument("--sjr",       default=str(SJR_CLEAN),
                    help=f"Path to cleaned SJR CSV  (default: {SJR_CLEAN.name})")
    ap.add_argument("--out",       default=str(OUT_INDEX),
                    help=f"Output index CSV  (default: {OUT_INDEX.name})")
    ap.add_argument("--threshold", default=0.80, type=float,
                    help="Fuzzy match threshold 0-1  (default: 0.80, empirically validated)")
    args = ap.parse_args()

    xml_path = Path(args.xml)
    sjr_path = Path(args.sjr)
    out_path = Path(args.out)

    if not xml_path.exists() and not CACHE_PATH.exists():
        sys.exit(f"dblp.xml not found: {xml_path}\nRun the downloader first.")
    if not sjr_path.exists():
        sys.exit(f"SJR clean CSV not found: {sjr_path}\nRun clean_sjr.py first.")

    dblp_index = load_dblp_index(xml_path)
    sjr_rows   = load_sjr(sjr_path)
    results    = match_journals(dblp_index, sjr_rows, args.threshold)
    write_index(results, out_path)


if __name__ == "__main__":
    main()
