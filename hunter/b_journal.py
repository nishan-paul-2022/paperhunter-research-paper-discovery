import argparse
import csv
import os
import re
import sys
from collections import Counter, defaultdict
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
SUPPLEMENT_DIR = REPO_ROOT / "supplement"
SJR_SRC = CONTEXT_DIR / "sjr-2025-cs.csv"
XML_PATH = DATA_DIR / "dblp.xml"

# Output files with indexed names
OUT_1_ISSN = SUPPLEMENT_DIR / "1-issn-matched.csv"
OUT_2_DBLP_TODO = SUPPLEMENT_DIR / "2-dblp-todo.csv"
OUT_3_SJR_POOL = SUPPLEMENT_DIR / "3-sjr-pool.csv"
OUT_FINAL = CONTEXT_DIR / "list-of-journals.csv"

OUT_FIELDS = ["dblp_key", "dblp_name", "title", "issn_1", "issn_2", "quartile"]
OUT_FIELDS_DBLP = ["dblp_key", "dblp_name"]
OUT_FIELDS_SJR = ["title", "issn_1", "issn_2", "quartile"]

VALID_QUARTILES = {"Q1", "Q2", "Q3", "Q4"}


def _format_issn(raw: str) -> str:
    s = raw.strip().replace("-", "").replace(" ", "")
    return f"{s[:4]}-{s[4:]}" if len(s) == 8 and s.isdigit() else raw.strip()


def _extract_issns(text: str) -> set[str]:
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
            }
            rows.append(record)
            
            if i1:
                issn_index[i1] = record
            if i2:
                issn_index[i2] = record

    print(f"  ✓  {len(rows)} valid journals  |  {dropped} malformed rows dropped")
    print(f"  ✓  {len(issn_index)} ISSNs indexed for fast lookup\n")
    return rows, issn_index


def _scan_xml(xml_path: Path) -> dict[str, tuple[str, set[str]]]:
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
) -> tuple[list[dict], list[dict], list[dict]]:
    """Match DBLP and SJR journals using ISSN only.
    
    Returns:
        - issn_matched: ISSN exact matches (auto-approved)
        - dblp_unmatched: DBLP journals without ISSN match (for AI review)
        - sjr_available: SJR journals without ISSN match (pool for AI)
    """
    print("━" * 70)
    print("  Phase 3 – ISSN Matching Only")
    print("━" * 70)

    print("\n  ISSN Exact Matching (Auto-Approved)")
    print("  " + "─" * 66)
    
    issn_matched = []
    dblp_matched_keys = set()
    sjr_matched_titles = set()
    
    for dblp_key, (dblp_name, dblp_issns) in dblp_index.items():
        if not dblp_issns:
            continue
        
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
                })
                dblp_matched_keys.add(dblp_key)
                sjr_matched_titles.add(sjr_record["title"])
                print(f"  ✓  {sjr_record['quartile']}  {dblp_name[:50]}")
                break
    
    print(f"\n  ✓  {len(issn_matched)} journals matched by ISSN (AUTO-APPROVED)\n")
    
    # Collect unmatched DBLP journals
    dblp_unmatched = [
        {"dblp_key": k, "dblp_name": dblp_name}
        for k, (dblp_name, _) in dblp_index.items()
        if k not in dblp_matched_keys
    ]
    
    # Collect available SJR journals (not yet matched)
    sjr_available = [
        {
            "title": r["title"],
            "issn_1": r["issn_1"],
            "issn_2": r["issn_2"],
            "quartile": r["quartile"],
        }
        for r in sjr_rows
        if r["title"] not in sjr_matched_titles
    ]
    
    total_dblp = len(dblp_index)
    total_sjr = len(sjr_rows)
    
    print(f"  Unmatched Journals (Need AI Review)")
    print(f"  " + "─" * 66)
    print(f"  ├─ DBLP unmatched: {len(dblp_unmatched)}")
    print(f"  └─ SJR available pool: {len(sjr_available)}")
    print()
    print(f"  Summary Statistics:")
    print(f"  ├─ Total DBLP journals in XML: {total_dblp:,}")
    print(f"  ├─ Total SJR journals (Q1-Q4): {total_sjr:,}")
    print(f"  ├─ ISSN auto-matched: {len(issn_matched):,} ({len(issn_matched)/total_dblp*100:.1f}% of DBLP)")
    print(f"  ├─ DBLP needs matching: {len(dblp_unmatched):,} ({len(dblp_unmatched)/total_dblp*100:.1f}% of DBLP)")
    print(f"  ├─ SJR available for matching: {len(sjr_available):,} ({len(sjr_available)/total_sjr*100:.1f}% of SJR)")
    print(f"  └─ Potential comparisons: {len(dblp_unmatched):,} × {len(sjr_available):,} = {len(dblp_unmatched) * len(sjr_available):,}\n")
    
    return issn_matched, dblp_unmatched, sjr_available


def phase_write(
    issn_matched: list[dict],
    dblp_unmatched: list[dict],
    sjr_available: list[dict],
    out_issn: Path,
    out_dblp: Path,
    out_sjr: Path
) -> None:
    print("━" * 70)
    print("  Phase 4 – Writing Output Files")
    print("━" * 70)

    # File 1: ISSN-matched journals (auto-approved)
    order = {"Q1": 0, "Q2": 1, "Q3": 2, "Q4": 3}
    issn_sorted = sorted(issn_matched,
                        key=lambda r: (order.get(r["quartile"], 9), r["title"].lower()))
    
    out_issn.parent.mkdir(parents=True, exist_ok=True)
    with open(out_issn, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUT_FIELDS, delimiter=",",
                                quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        writer.writerows(issn_sorted)
    
    qtrs = Counter(r["quartile"] for r in issn_matched)
    print(f"\n  File 1: 1-issn-matched.csv (AUTO-APPROVED)")
    print(f"  → {out_issn}")
    print(f"  Total: {len(issn_matched)} journals")
    print(f"  Q1: {qtrs['Q1']}   Q2: {qtrs['Q2']}   Q3: {qtrs['Q3']}   Q4: {qtrs['Q4']}\n")
    
    # File 2: DBLP journals needing matching
    dblp_sorted = sorted(dblp_unmatched, key=lambda r: r["dblp_key"])
    
    with open(out_dblp, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUT_FIELDS_DBLP, delimiter=",",
                                quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        writer.writerows(dblp_sorted)
    
    print(f"  File 2: 2-dblp-todo.csv (Need Matching)")
    print(f"  → {out_dblp}")
    print(f"  Total: {len(dblp_unmatched)} journals\n")
    
    # File 3: SJR pool for matching
    sjr_sorted = sorted(sjr_available,
                       key=lambda r: (order.get(r["quartile"], 9), r["title"].lower()))
    
    with open(out_sjr, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUT_FIELDS_SJR, delimiter=",",
                                quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        writer.writerows(sjr_sorted)
    
    qtrs_sjr = Counter(r["quartile"] for r in sjr_available)
    print(f"  File 3: 3-sjr-pool.csv (Available for Matching)")
    print(f"  → {out_sjr}")
    print(f"  Total: {len(sjr_available)} journals")
    print(f"  Q1: {qtrs_sjr['Q1']}   Q2: {qtrs_sjr['Q2']}   Q3: {qtrs_sjr['Q3']}   Q4: {qtrs_sjr['Q4']}\n")
    
    print(f"  NEXT STEP:")
    print(f"  → Run c_match.py to generate matches")
    print(f"  → Or use AI prompt with files 2 & 3\n")
    
    print(f"  Overall Statistics:")
    print(f"  ├─ 1-issn-matched.csv: {len(issn_matched):,} journals (AUTO-APPROVED)")
    print(f"  ├─ 2-dblp-todo.csv: {len(dblp_unmatched):,} journals (NEED MATCHING)")
    print(f"  ├─ 3-sjr-pool.csv: {len(sjr_available):,} journals (AVAILABLE)")
    print(f"  └─ Expected final output: ~{len(issn_matched) + int(len(dblp_unmatched)*0.5):,} matched journals\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xml", default=str(XML_PATH),
                    help=f"Path to dblp.xml  (default: {XML_PATH.name})")
    ap.add_argument("--sjr", default=str(SJR_SRC),
                    help=f"Path to SJR CSV   (default: {SJR_SRC.name})")
    ap.add_argument("--out", default=str(OUT_CSV),
                    help=f"Output CSV        (default: {OUT_CSV.name})")
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
    print("║         paperhunter  ·  Journal List Builder (AI-Ready)             ║")
    print("║                                                                      ║")
    print("║  Strategy: ISSN auto-match + AI-assisted manual matching            ║")
    print("║  Output: 3 files (ISSN matches, DBLP unmatched, SJR pool)           ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print()

    sjr_rows, sjr_issn_index = phase_clean(sjr_path)
    dblp_index = phase_index(xml_path)
    issn_matched, dblp_unmatched, sjr_available = phase_match(dblp_index, sjr_rows, sjr_issn_index)
    phase_write(
        issn_matched, dblp_unmatched, sjr_available,
        OUT_1_ISSN, OUT_2_DBLP_TODO, OUT_3_SJR_POOL
    )

    print("━" * 70)
    print("  Done.")
    print("  → 1-issn-matched.csv ready to use")
    print("  → Run c_match.py to process 2-dblp-todo.csv + 3-sjr-pool.csv")
    print("━" * 70)
    print()


if __name__ == "__main__":
    main()
