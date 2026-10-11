#!/usr/bin/env python3
"""
Merge ISSN-matched and AI-matched journals into final list.

Reads:
  - context/1-issn-matched.csv (ISSN auto-matches)
  - context/4-ai-matched.csv (AI/algorithm matches, HIGH confidence only)

Writes:
  - context/list-of-journals.csv (final combined list)
"""

import csv
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTEXT_DIR = REPO_ROOT / "journal-context"
SUPPLEMENT_DIR = REPO_ROOT / "journal-supplement"

IN_ISSN = SUPPLEMENT_DIR / "1-issn-matched.csv"
IN_AI = SUPPLEMENT_DIR / "4-ai-matched.csv"
OUT_FINAL = CONTEXT_DIR / "list-of-journals.csv"

OUT_FIELDS = ["dblp_key", "dblp_name", "title", "issn_1", "issn_2", "quartile"]


def main():
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║            paperhunter  ·  Merge Journals into Final List            ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print()
    
    # Read ISSN matches (all are valid)
    print("  Reading 1-issn-matched.csv...")
    issn_rows = []
    with open(IN_ISSN, encoding='utf-8') as f:
        for row in csv.DictReader(f):
            issn_rows.append({
                'dblp_key': row['dblp_key'],
                'dblp_name': row['dblp_name'],
                'title': row['title'],
                'issn_1': row['issn_1'],
                'issn_2': row['issn_2'],
                'quartile': row['quartile'],
            })
    print(f"  ✓ {len(issn_rows)} ISSN-matched journals loaded\n")
    
    # Read AI matches (only HIGH confidence)
    print("  Reading 4-ai-matched.csv (HIGH confidence only)...")
    ai_rows = []
    skipped = 0
    with open(IN_AI, encoding='utf-8') as f:
        for row in csv.DictReader(f):
            if row['confidence'] == 'HIGH' and row['sjr_title'] != 'NO_MATCH':
                ai_rows.append({
                    'dblp_key': row['dblp_key'],
                    'dblp_name': row['dblp_name'],
                    'title': row['sjr_title'],
                    'issn_1': row['issn_1'],
                    'issn_2': row['issn_2'],
                    'quartile': row['quartile'],
                })
            else:
                skipped += 1
    print(f"  ✓ {len(ai_rows)} HIGH confidence matches loaded")
    print(f"  ⚠ {skipped} entries skipped (NONE/MEDIUM/LOW confidence)\n")
    
    # Combine (ISSN first, then AI)
    all_journals = issn_rows + ai_rows
    
    # Check for duplicates by dblp_key
    seen_keys = set()
    duplicates = []
    for row in all_journals:
        if row['dblp_key'] in seen_keys:
            duplicates.append(row['dblp_key'])
        seen_keys.add(row['dblp_key'])
    
    if duplicates:
        print(f"  ⚠ WARNING: {len(duplicates)} duplicate dblp_keys found:")
        for dk in duplicates[:10]:
            print(f"      - {dk}")
        if len(duplicates) > 10:
            print(f"      ... and {len(duplicates) - 10} more")
        print()
    
    # Sort by quartile then title
    order = {"Q1": 0, "Q2": 1, "Q3": 2, "Q4": 3}
    all_journals = sorted(all_journals,
                         key=lambda r: (order.get(r["quartile"], 9), r["title"].lower()))
    
    # Write final CSV
    print("  Writing list-of-journals.csv...")
    OUT_FINAL.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_FINAL, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=OUT_FIELDS, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        writer.writerows(all_journals)
    
    qtrs = Counter(r['quartile'] for r in all_journals)
    
    print(f"  ✓ {len(all_journals)} total journals written\n")
    print("  " + "─" * 66)
    print(f"  Final Statistics:")
    print(f"  ├─ Total journals: {len(all_journals)}")
    print(f"  ├─ Q1: {qtrs['Q1']}   Q2: {qtrs['Q2']}   Q3: {qtrs['Q3']}   Q4: {qtrs['Q4']}")
    print(f"  ├─ From ISSN matching: {len(issn_rows)}")
    print(f"  ├─ From AI matching: {len(ai_rows)}")
    print(f"  └─ Output: {OUT_FINAL}")
    print("  " + "─" * 66)
    print()
    print("  ✅ Done! Use list-of-journals.csv for paper extraction.")
    print()


if __name__ == '__main__':
    main()
