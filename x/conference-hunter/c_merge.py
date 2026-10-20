#!/usr/bin/env python3
import csv
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTEXT_DIR = REPO_ROOT / "conference-context"
SUPPLEMENT_DIR = REPO_ROOT / "conference-supplement"
IN_EXACT = SUPPLEMENT_DIR / "1-exact-matched.csv"
IN_FUZZY = SUPPLEMENT_DIR / "4-fuzzy-matched.csv"
OUT_FINAL = CONTEXT_DIR / "list-of-conferences.csv"
OUT_FIELDS = ["dblp_key", "dblp_acronym", "title", "ranking"]


def main():
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║        paperhunter  ·  Merge Conferences into Final List             ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print()
    print("  Reading 1-exact-matched.csv...")
    exact_rows = []
    with open(IN_EXACT, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            exact_rows.append(
                {
                    "dblp_key": row["dblp_key"],
                    "dblp_acronym": row["dblp_acronym"],
                    "title": row["title"],
                    "ranking": row["ranking"],
                }
            )
    print(f"  ✓ {len(exact_rows)} exact-matched conferences loaded\n")
    print("  Reading 4-fuzzy-matched.csv (HIGH confidence only)...")
    fuzzy_rows = []
    skipped = 0
    with open(IN_FUZZY, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["confidence"] == "HIGH" and row["core_title"] != "NO_MATCH":
                fuzzy_rows.append(
                    {
                        "dblp_key": row["dblp_key"],
                        "dblp_acronym": row["dblp_acronym"],
                        "title": row["core_title"],
                        "ranking": row["ranking"],
                    }
                )
            else:
                skipped += 1
    print(f"  ✓ {len(fuzzy_rows)} HIGH confidence matches loaded")
    print(f"  ⚠ {skipped} entries skipped (NONE confidence)\n")
    all_conferences = exact_rows + fuzzy_rows
    seen_keys = set()
    duplicates = []
    for row in all_conferences:
        if row["dblp_key"] in seen_keys:
            duplicates.append(row["dblp_key"])
        seen_keys.add(row["dblp_key"])
    if duplicates:
        print(f"  ⚠ WARNING: {len(duplicates)} duplicate dblp_keys found:")
        for dk in duplicates[:10]:
            print(f"      - {dk}")
        if len(duplicates) > 10:
            print(f"      ... and {len(duplicates) - 10} more")
        print()
    order = {"A*": 0, "A": 1, "B": 2, "C": 3}
    all_conferences = sorted(
        all_conferences, key=lambda r: (order.get(r["ranking"], 9), r["title"].lower())
    )
    print("  Writing list-of-conferences.csv...")
    OUT_FINAL.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_FINAL, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUT_FIELDS, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        writer.writerows(all_conferences)
    ranks = Counter(r["ranking"] for r in all_conferences)
    print(f"  ✓ {len(all_conferences)} total conferences written\n")
    print("  " + "─" * 66)
    print("  Final Statistics:")
    print(f"  ├─ Total conferences: {len(all_conferences)}")
    print(
        f"  ├─ A*: {ranks['A*']}   A: {ranks['A']}   B: {ranks['B']}   C: {ranks['C']}"
    )
    print(f"  ├─ From exact matching: {len(exact_rows)}")
    print(f"  ├─ From fuzzy matching: {len(fuzzy_rows)}")
    print(f"  └─ Output: {OUT_FINAL}")
    print("  " + "─" * 66)
    print()
    print("  ✅ Done! Use list-of-conferences.csv for paper extraction.")
    print()


if __name__ == "__main__":
    main()
