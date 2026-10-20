#!/usr/bin/env python3
import argparse
import csv
import os
import sys
from collections import Counter
from pathlib import Path
from lxml import etree

HUNTER_DIR = Path(__file__).resolve().parent
REPO_ROOT = HUNTER_DIR.parent
DATA_DIR = REPO_ROOT / "paper-database"
CONTEXT_DIR = REPO_ROOT / "conference-context"
SUPPLEMENT_DIR = REPO_ROOT / "conference-supplement"
CORE_SRC = CONTEXT_DIR / "core-2026.csv"
XML_PATH = DATA_DIR / "dblp.xml"
OUT_1_EXACT = SUPPLEMENT_DIR / "1-exact-matched.csv"
OUT_2_TODO = SUPPLEMENT_DIR / "2-dblp-unmatched.csv"
OUT_3_CORE_POOL = SUPPLEMENT_DIR / "3-source-pool.csv"
OUT_FINAL = CONTEXT_DIR / "list-of-conferences.csv"
OUT_FIELDS = ["dblp_key", "dblp_acronym", "title", "ranking"]
OUT_FIELDS_DBLP = ["dblp_key", "dblp_acronym"]
OUT_FIELDS_CORE = ["title", "acronym", "ranking"]
VALID_RANKINGS = {"A*", "A", "B", "C"}


def phase_clean(core_path: Path) -> tuple[list[dict], dict[str, dict]]:
    print("━" * 70)
    print("  Phase 1 – Cleaning CORE CSV")
    print("━" * 70)
    rows = []
    acronym_index = {}
    dropped = 0
    with open(core_path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            ranking = row.get("Ranking", "").strip()
            if ranking not in VALID_RANKINGS:
                dropped += 1
                continue
            title = row["Title"].strip()
            acronym = row["Acronym"].strip()
            record = {
                "title": title,
                "acronym": acronym,
                "ranking": ranking,
            }
            rows.append(record)
            acronym_lower = acronym.lower()
            if acronym_lower not in acronym_index:
                acronym_index[acronym_lower] = record
    print(f"  ✓  {len(rows)} valid conferences  |  {dropped} invalid rows dropped")
    print(f"  ✓  {len(acronym_index)} unique acronyms indexed\n")
    return rows, acronym_index


def _scan_xml(xml_path: Path) -> dict[str, str]:
    size = os.path.getsize(xml_path)
    seen = indexed = 0
    result: dict[str, str] = {}
    f = open(xml_path, "rb")
    ctx = etree.iterparse(
        f,
        events=("end",),
        tag="inproceedings",
        load_dtd=True,
        resolve_entities=True,
        huge_tree=True,
    )
    for _, el in ctx:
        seen += 1
        key = el.get("key", "")
        if key.startswith("conf/"):
            parts = key.split("/")
            if len(parts) >= 2:
                prefix = "/".join(parts[:2])
                if prefix not in result:
                    acronym = parts[1].upper()
                    result[prefix] = acronym
                    indexed += 1
        if seen % 500_000 == 0:
            pct = f.tell() * 100 // size
            print(
                f"  {pct:3}%  |  papers scanned: {seen:,}"
                f"  |  conferences found: {indexed:,}",
                end="\r",
                flush=True,
            )
        el.clear()
        while el.getprevious() is not None:
            del el.getparent()[0]
    f.close()
    print(f"  100%  |  papers scanned: {seen:,}  |  conferences found: {indexed:,}")
    return result


def phase_index(xml_path: Path) -> dict[str, str]:
    print("━" * 70)
    print("  Phase 2 – DBLP Conference Index")
    print("━" * 70)
    print(f"  Scanning {xml_path.name}  ({xml_path.stat().st_size // 1_000_000} MB)")
    print("  (This takes ~15–20 min.)\n")
    idx = _scan_xml(xml_path)
    print(f"\n  ✓  {len(idx):,} unique conferences found\n")
    return idx


def phase_match(
    dblp_index: dict[str, str],
    core_rows: list[dict],
    core_acronym_index: dict[str, dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    print("━" * 70)
    print("  Phase 3 – Acronym Matching")
    print("━" * 70)
    exact_matched = []
    dblp_matched_keys = set()
    core_matched_acronyms = set()
    for dblp_key, dblp_acronym in dblp_index.items():
        acronym_lower = dblp_acronym.lower()
        if acronym_lower in core_acronym_index:
            core_record = core_acronym_index[acronym_lower]
            exact_matched.append(
                {
                    "dblp_key": dblp_key,
                    "dblp_acronym": dblp_acronym,
                    "title": core_record["title"],
                    "ranking": core_record["ranking"],
                }
            )
            dblp_matched_keys.add(dblp_key)
            core_matched_acronyms.add(acronym_lower)
            print(
                f"  ✓  {core_record['ranking']}  {dblp_acronym} → {core_record['title'][:50]}"
            )
    print(f"\n  ✓  {len(exact_matched)} conferences matched by acronym\n")
    dblp_unmatched = [
        {"dblp_key": k, "dblp_acronym": v}
        for k, v in dblp_index.items()
        if k not in dblp_matched_keys
    ]
    core_available = [
        {
            "title": r["title"],
            "acronym": r["acronym"],
            "ranking": r["ranking"],
        }
        for r in core_rows
        if r["acronym"].lower() not in core_matched_acronyms
    ]
    total_dblp = len(dblp_index)
    total_core = len(core_rows)
    print("  Summary Statistics:")
    print(f"  ├─ Total DBLP conferences in XML: {total_dblp:,}")
    print(f"  ├─ Total CORE conferences (A*-C): {total_core:,}")
    print(
        f"  ├─ Acronym matched: {len(exact_matched):,} ({len(exact_matched) / total_dblp * 100:.1f}% of DBLP)"
    )
    print(
        f"  ├─ DBLP needs matching: {len(dblp_unmatched):,} ({len(dblp_unmatched) / total_dblp * 100:.1f}% of DBLP)"
    )
    print(
        f"  ├─ CORE available: {len(core_available):,} ({len(core_available) / total_core * 100:.1f}% of CORE)"
    )
    print(f"  └─ Match rate: {len(exact_matched) / total_dblp * 100:.1f}%\n")
    return exact_matched, dblp_unmatched, core_available


def phase_write(
    exact_matched: list[dict],
    dblp_unmatched: list[dict],
    core_available: list[dict],
    out_exact: Path,
    out_dblp: Path,
    out_core: Path,
) -> None:
    print("━" * 70)
    print("  Phase 4 – Writing Output Files")
    print("━" * 70)
    order = {"A*": 0, "A": 1, "B": 2, "C": 3}
    exact_sorted = sorted(
        exact_matched, key=lambda r: (order.get(r["ranking"], 9), r["title"].lower())
    )
    out_exact.parent.mkdir(parents=True, exist_ok=True)
    with open(out_exact, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=OUT_FIELDS, delimiter=",", quoting=csv.QUOTE_MINIMAL
        )
        writer.writeheader()
        writer.writerows(exact_sorted)
    ranks = Counter(r["ranking"] for r in exact_matched)
    print("\n  File 1: 1-exact-matched.csv (AUTO-APPROVED)")
    print(f"  → {out_exact}")
    print(f"  Total: {len(exact_matched)} conferences")
    print(
        f"  A*: {ranks['A*']}   A: {ranks['A']}   B: {ranks['B']}   C: {ranks['C']}\n"
    )
    dblp_sorted = sorted(dblp_unmatched, key=lambda r: r["dblp_key"])
    with open(out_dblp, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=OUT_FIELDS_DBLP, delimiter=",", quoting=csv.QUOTE_MINIMAL
        )
        writer.writeheader()
        writer.writerows(dblp_sorted)
    print("  File 2: 2-dblp-unmatched.csv (Need Matching)")
    print(f"  → {out_dblp}")
    print(f"  Total: {len(dblp_unmatched)} conferences\n")
    core_sorted = sorted(
        core_available, key=lambda r: (order.get(r["ranking"], 9), r["title"].lower())
    )
    with open(out_core, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=OUT_FIELDS_CORE, delimiter=",", quoting=csv.QUOTE_MINIMAL
        )
        writer.writeheader()
        writer.writerows(core_sorted)
    ranks_core = Counter(r["ranking"] for r in core_available)
    print("  File 3: 3-source-pool.csv (Available for Matching)")
    print(f"  → {out_core}")
    print(f"  Total: {len(core_available)} conferences")
    print(
        f"  A*: {ranks_core['A*']}   A: {ranks_core['A']}   B: {ranks_core['B']}   C: {ranks_core['C']}\n"
    )
    print("  NEXT STEP:")
    print("  → Use 1-exact-matched.csv directly")
    print("  → Run b_match.py to match remaining\n")
    max_possible = len(exact_matched) + len(core_available)
    estimated = len(exact_matched) + int(len(core_available) * 0.3)
    print("  Overall Statistics:")
    print(
        f"  ├─ 1-exact-matched.csv: {len(exact_matched):,} conferences (AUTO-APPROVED)"
    )
    print(
        f"  ├─ 2-dblp-unmatched.csv: {len(dblp_unmatched):,} conferences (NEED MATCHING)"
    )
    print(f"  ├─ 3-source-pool.csv: {len(core_available):,} conferences (AVAILABLE)")
    print(f"  ├─ Expected: ~{estimated:,} conferences (realistic)")
    print(f"  └─ Maximum possible: {max_possible:,} conferences (all CORE matched)\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--xml",
        default=str(XML_PATH),
        help=f"Path to dblp.xml  (default: {XML_PATH.name})",
    )
    ap.add_argument(
        "--core",
        default=str(CORE_SRC),
        help=f"Path to CORE CSV  (default: {CORE_SRC.name})",
    )
    args = ap.parse_args()
    xml_path = Path(args.xml)
    core_path = Path(args.core)
    if not core_path.exists():
        sys.exit(f"CORE CSV not found: {core_path}")
    if not xml_path.exists():
        sys.exit(f"dblp.xml not found: {xml_path}\nRun the downloader first.")
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║         paperhunter  ·  Conference List Builder                      ║")
    print("║                                                                      ║")
    print("║  Strategy: Acronym matching (A*, A, B, C rankings)                   ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print()
    core_rows, core_acronym_index = phase_clean(core_path)
    dblp_index = phase_index(xml_path)
    exact_matched, dblp_unmatched, core_available = phase_match(
        dblp_index, core_rows, core_acronym_index
    )
    phase_write(
        exact_matched,
        dblp_unmatched,
        core_available,
        OUT_1_EXACT,
        OUT_2_TODO,
        OUT_3_CORE_POOL,
    )
    print("━" * 70)
    print("  Done.")
    print("  → Use 1-exact-matched.csv directly")
    print("  → Run b_match.py for remaining matches")
    print("━" * 70)
    print()


if __name__ == "__main__":
    main()
