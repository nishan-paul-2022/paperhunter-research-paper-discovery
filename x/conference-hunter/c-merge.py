#!/usr/bin/env python3
import csv
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = REPO_ROOT / "archive"
SUPPLEMENT_DIR = REPO_ROOT / "conference-supplement"
IN_FUZZY = SUPPLEMENT_DIR / "5-fuzzy-matched.csv"
IN_VENUES = SUPPLEMENT_DIR / "1-dblp-venues.csv"
CORE_MASTER_CSV = ARCHIVE_DIR / "publication-core-2026.csv"
DECISIONS_CSV = SUPPLEMENT_DIR / "7-decisions.csv"
OUT_FINAL = ARCHIVE_DIR / "list-of-conferences.csv"
OUT_FIELDS = ["dblp_key", "dblp_acronym", "title", "ranking"]
VALID_RANKINGS = {"A*", "A", "B", "C"}


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def main():
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║        paperhunter  ·  Merge Conferences into Final List             ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print()
    core = {
        r["Title"].strip(): r["Ranking"].strip()
        for r in read_csv(CORE_MASTER_CSV)
        if r["Ranking"].strip() in VALID_RANKINGS
    }
    venues = {r["dblp_key"] for r in read_csv(IN_VENUES)}
    absent = {
        r["core_title"].strip() for r in read_csv(DECISIONS_CSV) if r["decision"].strip() == "absent"
    }
    rows = read_csv(IN_FUZZY)
    errors = []
    unreviewed = [r for r in rows if r["confidence"] == "UNREVIEWED"]
    if unreviewed:
        errors.append(f"{len(unreviewed)} UNREVIEWED candidates in {IN_FUZZY.name} (run b-match.py)")
    accepted = [r for r in rows if r["confidence"] == "HIGH"]
    print(f"  ✓ {len(accepted)} accepted (HIGH) matches loaded from {IN_FUZZY.name}\n")

    final = []
    for r in accepted:
        key, title, ranking = r["dblp_key"], r["core_title"], r["ranking"]
        if key not in venues:
            errors.append(f"{key}: not a DBLP conference key with papers")
        if title not in core:
            errors.append(f"{key}: title not in CORE A*–C: {title!r}")
        elif core[title] != ranking:
            errors.append(f"{key}: ranking {ranking} != CORE {core[title]} for {title!r}")
        final.append(
            {"dblp_key": key, "dblp_acronym": r["dblp_acronym"], "title": title, "ranking": ranking}
        )
    for key, n in Counter(r["dblp_key"] for r in final).items():
        if n > 1:
            errors.append(f"{key}: mapped to {n} CORE venues")
    covered = {r["title"] for r in final}
    for title in core:
        if title not in covered and title not in absent:
            errors.append(f"CORE venue neither matched nor documented absent: {title!r}")

    if errors:
        print("  ✗ Validation failed — list-of-conferences.csv NOT written:")
        for e in errors[:50]:
            print(f"      - {e}")
        if len(errors) > 50:
            print(f"      ... and {len(errors) - 50} more")
        sys.exit(1)

    order = {"A*": 0, "A": 1, "B": 2, "C": 3}
    final.sort(key=lambda r: (order[r["ranking"]], r["title"].lower(), r["dblp_key"]))
    print("  Writing list-of-conferences.csv...")
    OUT_FINAL.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_FINAL, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUT_FIELDS, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        writer.writerows(final)
    ranks = Counter(r["ranking"] for r in final)
    print(f"  ✓ {len(final)} DBLP keys written\n")
    print("  " + "─" * 66)
    print("  Final Statistics:")
    print(f"  ├─ DBLP keys: {len(final)}")
    print(f"  ├─ A*: {ranks['A*']}   A: {ranks['A']}   B: {ranks['B']}   C: {ranks['C']}")
    print(f"  ├─ CORE venues covered: {len(covered)} / {len(core)}  ({len(absent)} documented absent from DBLP)")
    print(f"  └─ Output: {OUT_FINAL}")
    print("  " + "─" * 66)
    print()
    print("  ✅ Done! Validated list-of-conferences.csv ready for paper extraction.")
    print()


if __name__ == "__main__":
    main()
