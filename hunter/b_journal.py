import argparse
import csv
import os
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

try:
    from lxml import etree
except ImportError:
    sys.exit("Missing lxml.  Run:  pip install lxml")

HUNTER_DIR = Path(__file__).resolve().parent
REPO_ROOT = HUNTER_DIR.parent
DATA_DIR = REPO_ROOT / "database"
CONTEXT_DIR = REPO_ROOT / "context"
SJR_SRC = CONTEXT_DIR / "SJR-WoS-2025-CS.csv"
XML_PATH = DATA_DIR / "dblp.xml"
OUT_CSV = CONTEXT_DIR / "List-of-Journal.csv"

OUT_FIELDS = ["dblp_key", "dblp_name", "title", "issn_1", "issn_2",
              "quartile", "coverage", "match_method", "match_score"]

VALID_QUARTILES = {"Q1", "Q2", "Q3", "Q4"}
DEFAULT_THRESHOLD = 0.80

_ABBREV = [
    (r"\bTrans\b\.?", "Transactions"),
    (r"\bJ\b\.?", "Journal"),
    (r"\bInt\b\.?", "International"),
    (r"\bComput\b\.?", "Computer"),
    (r"\bSyst\b\.?", "Systems"),
    (r"\bEng\b\.?", "Engineering"),
    (r"\bSci\b\.?", "Science"),
    (r"\bTechnol\b\.?", "Technology"),
    (r"\bAppl\b\.?", "Applied"),
    (r"\bApplic\b\.?", "Applications"),
    (r"\bMach\b\.?", "Machine"),
    (r"\bIntell\b\.?", "Intelligence"),
    (r"\bArtif\b\.?", "Artificial"),
    (r"\bInf\b\.?", "Information"),
    (r"\bCommun\b\.?", "Communications"),
    (r"\bAnal\b\.?", "Analysis"),
    (r"\bRecogn\b\.?", "Recognition"),
    (r"\bVis\b\.?", "Vision"),
    (r"\bNetw\b\.?", "Networks"),
    (r"\bDistrib\b\.?", "Distributed"),
    (r"\bAuton\b\.?", "Autonomous"),
    (r"\bEduc\b\.?", "Education"),
    (r"\bLett\b\.?", "Letters"),
    (r"\bManag\b\.?", "Management"),
    (r"\bAdv\b\.?", "Advanced"),
    (r"\bAI\b", "Artificial Intelligence"),
    (r"\bMed\b\.?", "Medical"),
    (r"\bBiol\b\.?", "Biological"),
    (r"\bBioinform\b\.?", "Bioinformatics"),
    (r"\bNeur\b\.?", "Neural"),
    (r"\bRobot\b\.?", "Robotics"),
    (r"\bOptim\b\.?", "Optimization"),
    (r"\bSimul\b\.?", "Simulation"),
    (r"\bSoftw\b\.?", "Software"),
    (r"\bAlgor\b\.?", "Algorithms"),
    (r"\bArchit\b\.?", "Architecture"),
    (r"\bSecur\b\.?", "Security"),
    (r"\bBehav\b\.?", "Behavior"),
    (r"\bCogn\b\.?", "Cognitive"),
    (r"\bRes\b\.?", "Research"),
    (r"\bPract\b\.?", "Practice"),
    (r"\bProc\b\.?", "Proceedings"),
    (r"\bMin\b\.?", "Mining"),
    (r"\bMath\b\.?", "Mathematics"),
    (r"\bProcess\b\.?", "Processing"),
    (r"\bSoc\b\.?", "Society"),
    (r"\bDigit\b\.?", "Digital"),
    (r"\bElectron\b\.?", "Electronics"),
    (r"\bLearn\b\.?", "Learning"),
    (r"\bModel\b\.?", "Modeling"),
    (r"\bKnowl\b\.?", "Knowledge"),
    (r"\bInteract\b\.?", "Interaction"),
    (r"\bRev\b\.?", "Review"),
    (r"\bAutom\b\.?", "Automation"),
    (r"\bLog\b\.?", "Logic"),
    (r"\bLang\b\.?", "Language"),
    (r"\bFound\b\.?", "Foundations"),
    (r"\bOper\b\.?", "Operations"),
    (r"\bControl\b\.?", "Control"),
    (r"\bHum\b\.?", "Human"),
    (r"\bProgram\b\.?", "Programming"),
    (r"\bServ\b\.?", "Services"),
    (r"\bMultim\b\.?", "Multimedia"),
    (r"\bMob\b\.?", "Mobile"),
    (r"\bDecis\b\.?", "Decision"),
    (r"\bWirel\b\.?", "Wireless"),
    (r"\bDes\b\.?", "Design"),
    (r"\bBus\b\.?", "Business"),
    (r"\bMag\b\.?", "Magazine"),
    (r"\bComb\b\.?", "Combinatorics"),
    (r"\bGraph\b\.?", "Graphics"),
    (r"\bBull\b\.?", "Bulletin"),
    (r"\bEnviron\b\.?", "Environment"),
    (r"\bSens\b\.?", "Sensors"),
    (r"\bStat\b\.?", "Statistics"),
    (r"\bDev\b\.?", "Development"),
    (r"\bIntegr\b\.?", "Integration"),
    (r"\bBiomed\b\.?", "Biomedical"),
    (r"\bLibr\b\.?", "Library"),
    (r"\bTech\b\.?", "Technical"),
    (r"\bPerform\b\.?", "Performance"),
    (r"\bNat\b\.?", "Natural"),
    (r"\bDiscret\b\.?", "Discrete"),
    (r"\bGeom\b\.?", "Geometry"),
    (r"\bTheor\b\.?", "Theoretical"),
    (r"\bComputat\b\.?", "Computational"),
]

# Reduced stopwords - preserve important prepositions that distinguish journals
_STOP = {
    "a", "an", "the", "&",
}


def normalize(text: str, preserve_order: bool = True) -> str:
    """Normalize journal names for matching.
    
    Args:
        text: Journal name to normalize
        preserve_order: If True, keep words in original order (more conservative)
    """
    for pat, rep in _ABBREV:
        text = re.sub(pat, rep, text, flags=re.IGNORECASE)
    text = text.lower()
    # Less aggressive stemming - only normalize clear variants
    text = re.sub(r"\b(electronic|electronics)\b", "electronics", text)
    text = re.sub(r"\b(technology|technologies)\b", "technology", text)
    # Remove punctuation and extra spaces
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    
    tokens = [t for t in text.split() if t not in _STOP]
    
    if preserve_order:
        return " ".join(tokens)
    else:
        # Fallback: sort tokens for more lenient matching
        return " ".join(sorted(tokens))


def match_score(a: str, b: str) -> float:
    """Calculate similarity score between two normalized journal names.
    
    Returns score between 0.0 and 1.0, with higher being more similar.
    """
    ta = set(a.split())
    tb = set(b.split())
    if not ta or not tb:
        return 0.0
    
    shared = len(ta & tb)
    if not shared:
        return 0.0
    
    # Calculate both Jaccard and containment
    jaccard = shared / len(ta | tb)
    containment = shared / min(len(ta), len(tb))
    
    # Prefer containment for subset matches (e.g., abbreviated vs full name)
    token_sim = containment if containment >= 0.90 else jaccard
    
    # Early exit if token similarity too low
    if token_sim < 0.65:
        return 0.0
    
    # Sequence similarity to account for word order
    seq = SequenceMatcher(None, a, b, autojunk=False).ratio()
    
    # Weighted combination: prioritize sequence match more heavily
    # to avoid matching journals with same words in different order
    return 0.4 * token_sim + 0.6 * seq


def _format_issn(raw: str) -> str:
    """Format ISSN to standard XXXX-XXXX format."""
    s = raw.strip().replace("-", "").replace(" ", "")
    return f"{s[:4]}-{s[4:]}" if len(s) == 8 and s.isdigit() else raw.strip()


def _extract_issns(text: str) -> set[str]:
    """Extract all ISSNs from text, returning standardized format."""
    # Match ISSN patterns: XXXX-XXXX or XXXXXXXX
    issn_pattern = r'\b\d{4}-?\d{3}[\dXx]\b'
    matches = re.findall(issn_pattern, text)
    return {_format_issn(m) for m in matches}


def _split_issns(cell: str) -> tuple[str, str]:
    parts = [p.strip() for p in cell.split(",") if p.strip()]
    return (
        _format_issn(parts[0]) if parts else "",
        _format_issn(parts[1]) if len(parts) > 1 else "",
    )


def phase_clean(sjr_path: Path) -> tuple[list[dict], dict[str, dict]]:
    """Clean SJR CSV and build ISSN index.
    
    Returns:
        - List of valid SJR journal records
        - Dict mapping ISSN -> SJR record (for fast ISSN lookup)
    """
    print("━" * 70)
    print("  Phase 1 – Cleaning SJR CSV")
    print("━" * 70)

    rows = []
    issn_index = {}
    dropped = 0

    with open(sjr_path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            q = row.get("SJR Best Quartile", "").strip()
            if q not in VALID_QUARTILES:
                dropped += 1
                continue
            i1, i2 = _split_issns(row.get("Issn", ""))
            record = {
                "title": row["Title"].strip(),
                "issn_1": i1,
                "issn_2": i2,
                "quartile": q,
                "coverage": row["Coverage"].strip(),
                "norm": normalize(row["Title"].strip()),
            }
            rows.append(record)
            
            # Build ISSN index for fast lookup
            if i1:
                issn_index[i1] = record
            if i2:
                issn_index[i2] = record

    print(f"  ✓  {len(rows)} valid journals  |  {dropped} malformed rows dropped")
    print(f"  ✓  {len(issn_index)} ISSNs indexed for fast lookup\n")
    return rows, issn_index


def _scan_xml(xml_path: Path) -> dict[str, tuple[str, set[str]]]:
    """Scan DBLP XML and extract journal info including ISSNs.
    
    Returns:
        Dict mapping journal key prefix -> (journal name, set of ISSNs)
    """
    size = os.path.getsize(xml_path)
    seen = index = 0
    result: dict[str, tuple[str, set[str]]] = {}

    f = open(xml_path, "rb")
    ctx = etree.iterparse(
        f, events=("end",), tag="article",
        load_dtd=True, resolve_entities=True, huge_tree=True,
    )
    for _, el in ctx:
        seen += 1
        key = el.get("key", "")
        journal = el.findtext("journal") or ""
        
        if key.startswith("journals/") and journal:
            prefix = "/".join(key.split("/")[:2])
            if prefix not in result:
                # Extract ISSNs from <ee> tags (DOI URLs, etc.)
                issns = set()
                for ee_elem in el.findall("ee"):
                    ee_text = ee_elem.text or ""
                    issns.update(_extract_issns(ee_text))
                
                result[prefix] = (journal.strip(), issns)
                index += 1

        if seen % 500_000 == 0:
            pct = f.tell() * 100 // size
            print(f"  {pct:3}%  |  articles scanned: {seen:,}"
                  f"  |  journal keys found: {index:,}",
                  end="\r", flush=True)

        el.clear()
        while el.getprevious() is not None:
            del el.getparent()[0]

    f.close()
    print(f"  100%  |  articles scanned: {seen:,}  |  journal keys found: {index:,}")
    return result


def phase_index(xml_path: Path) -> dict[str, tuple[str, set[str]]]:
    """Build DBLP journal index with ISSNs.
    
    Returns:
        Dict mapping journal key -> (journal name, set of ISSNs)
    """
    print("━" * 70)
    print("  Phase 2 – DBLP Journal Index")
    print("━" * 70)
    print(f"  Scanning {xml_path.name}  ({xml_path.stat().st_size // 1_000_000} MB)")
    print("  (This takes ~15–20 min.)\n")
    idx = _scan_xml(xml_path)
    
    issn_count = sum(1 for _, issns in idx.values() if issns)
    print(f"\n  ✓  {len(idx):,} unique journal keys found")
    print(f"  ✓  {issn_count:,} journals have ISSN data\n")
    return idx


def phase_match(
    dblp_index: dict[str, tuple[str, set[str]]],
    sjr_rows: list[dict],
    sjr_issn_index: dict[str, dict],
    threshold: float,
) -> list[dict]:
    """Match DBLP and SJR journals using ISSN-first, then fuzzy matching.
    
    Uses bidirectional matching to ensure mutual best matches.
    """
    print("━" * 70)
    print(f"  Phase 3 – Multi-Strategy Matching  (threshold = {threshold})")
    print("━" * 70)

    # Stage 1: ISSN-based exact matching
    print("\n  Stage 3.1 – ISSN Exact Matching")
    print("  " + "─" * 66)
    
    issn_matched = []
    dblp_matched_keys = set()
    sjr_matched_titles = set()
    
    for dblp_key, (dblp_name, dblp_issns) in dblp_index.items():
        if not dblp_issns:
            continue
        
        # Check if any DBLP ISSN matches SJR
        for issn in dblp_issns:
            if issn in sjr_issn_index:
                sjr_record = sjr_issn_index[issn]
                issn_matched.append({
                    "dblp_key": dblp_key,
                    "dblp_name": dblp_name,
                    "title": sjr_record["title"],
                    "issn_1": sjr_record["issn_1"],
                    "issn_2": sjr_record["issn_2"],
                    "quartile": sjr_record["quartile"],
                    "coverage": sjr_record["coverage"],
                    "match_method": "ISSN",
                    "match_score": 1.0,
                })
                dblp_matched_keys.add(dblp_key)
                sjr_matched_titles.add(sjr_record["title"])
                print(f"  ✓  {sjr_record['quartile']}  {dblp_name[:50]}")
                break  # Found match, move to next DBLP journal
    
    print(f"\n  ✓  {len(issn_matched)} journals matched by ISSN\n")
    
    # Stage 2: Fuzzy matching for remaining journals
    print("  Stage 3.2 – Fuzzy Name Matching (Bidirectional)")
    print("  " + "─" * 66)
    
    # Filter out already matched journals
    remaining_dblp = {
        k: v for k, v in dblp_index.items() 
        if k not in dblp_matched_keys
    }
    remaining_sjr = [
        r for r in sjr_rows 
        if r["title"] not in sjr_matched_titles
    ]
    
    # Precompute normalized names
    sjr_norms = [(r["norm"], r) for r in remaining_sjr]
    
    # Forward pass: For each DBLP, find best SJR match
    dblp_to_sjr = {}  # dblp_key -> (sjr_record, score)
    
    for dblp_key, (dblp_name, _) in remaining_dblp.items():
        nd = normalize(dblp_name)
        
        best_score = 0.0
        best_row = None
        
        for ns, sr in sjr_norms:
            s = match_score(nd, ns)
            if s > best_score:
                best_score = s
                best_row = sr
        
        if best_score >= threshold and best_row is not None:
            dblp_to_sjr[dblp_key] = (best_row, best_score)
    
    # Reverse pass: For each SJR, find best DBLP match
    sjr_to_dblp = {}  # sjr_title -> (dblp_key, dblp_name, score)
    
    for ns, sr in sjr_norms:
        best_score = 0.0
        best_dblp_key = None
        best_dblp_name = None
        
        for dblp_key, (dblp_name, _) in remaining_dblp.items():
            nd = normalize(dblp_name)
            s = match_score(nd, ns)
            
            if s > best_score:
                best_score = s
                best_dblp_key = dblp_key
                best_dblp_name = dblp_name
        
        if best_score >= threshold and best_dblp_key is not None:
            sjr_to_dblp[sr["title"]] = (best_dblp_key, best_dblp_name, best_score)
    
    # Bidirectional match: Keep only mutual best matches
    fuzzy_matched = []
    conflict_count = 0
    
    for dblp_key, (sjr_record, fwd_score) in dblp_to_sjr.items():
        sjr_title = sjr_record["title"]
        
        # Check if this SJR's best match is also this DBLP journal
        if sjr_title in sjr_to_dblp:
            rev_dblp_key, rev_dblp_name, rev_score = sjr_to_dblp[sjr_title]
            
            if rev_dblp_key == dblp_key:
                # Mutual best match!
                fuzzy_matched.append({
                    "dblp_key": dblp_key,
                    "dblp_name": remaining_dblp[dblp_key][0],
                    "title": sjr_record["title"],
                    "issn_1": sjr_record["issn_1"],
                    "issn_2": sjr_record["issn_2"],
                    "quartile": sjr_record["quartile"],
                    "coverage": sjr_record["coverage"],
                    "match_method": "Fuzzy",
                    "match_score": round(fwd_score, 3),
                })
                print(f"  ✓  {sjr_record['quartile']}  {remaining_dblp[dblp_key][0][:45]} (score: {fwd_score:.3f})")
            else:
                # Conflict: DBLP thinks SJR is best, but SJR prefers different DBLP
                conflict_count += 1
    
    print(f"\n  ✓  {len(fuzzy_matched)} journals matched by fuzzy matching")
    print(f"  ⚠  {conflict_count} conflicting matches excluded (not mutual best)\n")
    
    # Combine results
    all_matched = issn_matched + fuzzy_matched
    
    total_dblp = len(dblp_index)
    total_sjr = len(sjr_rows)
    print(f"  Summary:")
    print(f"  ├─ DBLP journals in XML: {total_dblp}")
    print(f"  ├─ SJR journals (valid Q1-Q4): {total_sjr}")
    print(f"  ├─ Matched (ISSN): {len(issn_matched)}")
    print(f"  ├─ Matched (Fuzzy): {len(fuzzy_matched)}")
    print(f"  ├─ Total matched: {len(all_matched)}")
    print(f"  ├─ DBLP unmatched: {total_dblp - len(all_matched)}")
    print(f"  └─ SJR unmatched: {total_sjr - len(all_matched)}\n")
    
    return all_matched


def phase_write(rows: list[dict], out_path: Path) -> None:
    print("━" * 70)
    print("  Phase 4 – Writing List-of-Journal.csv")
    print("━" * 70)

    by_title = defaultdict(list)
    for r in rows:
        by_title[r["title"]].append(r)

    deduped = []
    dupes_removed = 0
    
    for title, group in by_title.items():
        if len(group) == 1:
            deduped.append(group[0])
        else:
            # Multiple DBLP journals matched to same SJR journal
            # Prefer: 1) ISSN match, 2) Higher fuzzy score, 3) Shorter key
            best = max(
                group,
                key=lambda r: (
                    1 if r["match_method"] == "ISSN" else 0,
                    r["match_score"],
                    -len(r["dblp_key"]),
                ),
            )
            deduped.append(best)
            dupes_removed += len(group) - 1
            
            if dupes_removed <= 20:  # Show first few conflicts
                print(f"  ⚠  Duplicate: '{title}' matched by multiple DBLP journals")
                for r in group:
                    mark = "→" if r == best else " "
                    print(f"      {mark} {r['dblp_key']} ({r['match_method']}, score={r['match_score']:.3f})")

    if dupes_removed:
        print(f"\n  Deduplication: removed {dupes_removed} lower-scoring duplicate matches")

    # Sort by quartile, then score descending, then title
    order = {"Q1": 0, "Q2": 1, "Q3": 2, "Q4": 3}
    deduped = sorted(deduped,
                     key=lambda r: (
                         order.get(r["quartile"], 9),
                         -r["match_score"],  # Higher score first
                         r["title"].lower()
                     ))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUT_FIELDS, delimiter=",",
                                quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        writer.writerows(deduped)

    qtrs = Counter(r["quartile"] for r in deduped)
    methods = Counter(r["match_method"] for r in deduped)
    
    print(f"\n  Output → {out_path}")
    print(f"  Total  : {len(deduped)} journals")
    print(f"  Q1: {qtrs['Q1']}   Q2: {qtrs['Q2']}   Q3: {qtrs['Q3']}   Q4: {qtrs['Q4']}")
    print(f"  Match methods: ISSN={methods['ISSN']}   Fuzzy={methods['Fuzzy']}\n")
    
    # Quality metrics
    avg_score = sum(r["match_score"] for r in deduped) / len(deduped) if deduped else 0
    low_confidence = sum(1 for r in deduped if r["match_score"] < 0.90)
    
    print(f"  Quality Metrics:")
    print(f"  ├─ Average match score: {avg_score:.3f}")
    print(f"  └─ Low confidence matches (<0.90): {low_confidence}\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xml", default=str(XML_PATH),
                    help=f"Path to dblp.xml  (default: {XML_PATH.name})")
    ap.add_argument("--sjr", default=str(SJR_SRC),
                    help=f"Path to SJR CSV   (default: {SJR_SRC.name})")
    ap.add_argument("--out", default=str(OUT_CSV),
                    help=f"Output CSV        (default: {OUT_CSV.name})")
    ap.add_argument("--threshold", default=DEFAULT_THRESHOLD, type=float,
                    help=f"Match threshold 0-1  (default: {DEFAULT_THRESHOLD})")
    args = ap.parse_args()

    xml_path = Path(args.xml)
    sjr_path = Path(args.sjr)
    out_path = Path(args.out)

    if not sjr_path.exists():
        sys.exit(f"SJR CSV not found: {sjr_path}")
    if not xml_path.exists():
        sys.exit(f"dblp.xml not found: {xml_path}\nRun the downloader first.")

    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║         paperhunter  ·  Journal List Builder (Enhanced)             ║")
    print("║                                                                      ║")
    print("║  Features: ISSN matching, bidirectional fuzzy matching,             ║")
    print("║            conflict resolution, quality scoring                      ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print()

    sjr_rows, sjr_issn_index = phase_clean(sjr_path)
    dblp_index = phase_index(xml_path)
    matched = phase_match(dblp_index, sjr_rows, sjr_issn_index, args.threshold)
    phase_write(matched, out_path)

    print("━" * 70)
    print("  Done.  Run the extractor next to pull papers from dblp.xml.")
    print("━" * 70)
    print()


if __name__ == "__main__":
    main()
