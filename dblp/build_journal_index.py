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
_ABBREV = {
    r"\bTrans\b\.?":       "Transactions",
    r"\bJ\b\.?":           "Journal",
    r"\bInt\b\.?":         "International",
    r"\bComput\b\.?":      "Computer",
    r"\bSyst\b\.?":        "Systems",
    r"\bEng\b\.?":         "Engineering",
    r"\bSci\b\.?":         "Science",
    r"\bTechnol\b\.?":     "Technology",
    r"\bAppl\b\.?":        "Applications",
    r"\bProc\b\.?":        "Proceedings",
    r"\bMach\b\.?":        "Machine",
    r"\bIntell\b\.?":      "Intelligence",
    r"\bArtif\b\.?":       "Artificial",
    r"\bInf\b\.?":         "Information",
    r"\bCommun\b\.?":      "Communications",
    r"\bAnal\b\.?":        "Analysis",
    r"\bRecogn\b\.?":      "Recognition",
    r"\bVis\b\.?":         "Vision",
    r"\bPattern\b":        "Pattern",
    r"\bNeural\b":         "Neural",
    r"\bNetw\b\.?":        "Networks",
    r"\bProc\b\.?":        "Processing",
    r"\bSignal\b":         "Signal",
    r"\bImage\b":          "Image",
    r"\bParallel\b":       "Parallel",
    r"\bDistrib\b\.?":     "Distributed",
}

def normalize(text: str) -> str:
    """
    Normalise a journal name for fuzzy comparison:
    expand abbreviations, lowercase, strip punctuation and extra spaces.
    """
    for pattern, replacement in _ABBREV.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)   # remove punctuation
    text = re.sub(r"\s+", " ", text).strip()
    return text


def similarity(a: str, b: str) -> float:
    """Return SequenceMatcher ratio between two normalised strings."""
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


# ── step 1: scan XML for journal key → name mapping ──────────────────────────

def scan_xml(xml_path: Path) -> dict[str, str]:
    """
    Stream dblp.xml and collect unique:
        key_prefix  →  journal_name
    from every <article> record that has both a key and a <journal> child.

    Returns a dict  {  "journals/tods": "ACM Trans. Database Syst.", ... }
    """
    print(f"Scanning {xml_path}  ({xml_path.stat().st_size // 1_000_000} MB)...")
    index: dict[str, str] = {}       # key_prefix → journal_name
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
        if key and journal:
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
            score = similarity(norm_dblp, norm_sjr)
            if score > best_score:
                best_score = score
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
    ap.add_argument("--threshold", default=0.82, type=float,
                    help="Fuzzy match threshold 0-1  (default: 0.82)")
    args = ap.parse_args()

    xml_path = Path(args.xml)
    sjr_path = Path(args.sjr)
    out_path = Path(args.out)

    if not xml_path.exists():
        sys.exit(f"dblp.xml not found: {xml_path}\nRun the downloader first.")
    if not sjr_path.exists():
        sys.exit(f"SJR clean CSV not found: {sjr_path}\nRun clean_sjr.py first.")

    dblp_index = scan_xml(xml_path)
    sjr_rows   = load_sjr(sjr_path)
    results    = match_journals(dblp_index, sjr_rows, args.threshold)
    write_index(results, out_path)


if __name__ == "__main__":
    main()
