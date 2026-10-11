"""
clean_sjr.py  -  Clean SJR-WoS CSV to the 5 columns needed by paperhunter.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Input:   SJR-WoS-2025-CS.csv          (semicolon-delimited, SCImago export)
Output:  SJR-WoS-2025-CS-clean.csv    (comma-delimited, UTF-8)

Output columns:
    Title      - full journal name
    Issn_1     - primary ISSN formatted as XXXX-XXXX
    Issn_2     - secondary ISSN formatted as XXXX-XXXX  (empty if none)
    Quartile   - SJR Best Quartile: Q1 / Q2 / Q3 / Q4
    Coverage   - year range indexed by SJR, e.g. "1983-2026"

Note on dblp_key:
    DBLP's API is protected by bot-detection and cannot be queried
    programmatically. The journal → DBLP key mapping is built offline
    by build_journal_index.py, which scans the local dblp.xml directly.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Usage:
    python3 dblp/clean_sjr.py
"""

import csv
from pathlib import Path

# ── paths ───────────────────────────────────────────────────────────────────
DBLP_DIR = Path(__file__).resolve().parent
SRC      = DBLP_DIR / "SJR-WoS-2025-CS.csv"
DST      = DBLP_DIR / "SJR-WoS-2025-CS-clean.csv"

# ── constants ────────────────────────────────────────────────────────────────
VALID_QUARTILES = {"Q1", "Q2", "Q3", "Q4"}
OUT_FIELDS      = ["Title", "Issn_1", "Issn_2", "Quartile", "Coverage"]


# ── helpers ──────────────────────────────────────────────────────────────────

def format_issn(raw: str) -> str:
    """'15580008'  →  '1558-0008'.  Returns raw value if not exactly 8 digits."""
    s = raw.strip().replace("-", "")
    return f"{s[:4]}-{s[4:]}" if len(s) == 8 and s.isdigit() else raw.strip()


def split_issns(cell: str) -> tuple[str, str]:
    """'15580008, 07338716'  →  ('1558-0008', '0733-8716')."""
    parts = [p.strip() for p in cell.split(",") if p.strip()]
    issn1 = format_issn(parts[0]) if len(parts) > 0 else ""
    issn2 = format_issn(parts[1]) if len(parts) > 1 else ""
    return issn1, issn2


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    kept = dropped = 0

    with open(SRC, encoding="utf-8", newline="") as src_f, \
         open(DST, "w", encoding="utf-8", newline="") as dst_f:

        reader = csv.DictReader(src_f, delimiter=";")
        writer = csv.DictWriter(dst_f, fieldnames=OUT_FIELDS, delimiter=",",
                                quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()

        for row in reader:
            quartile = row.get("SJR Best Quartile", "").strip()
            if quartile not in VALID_QUARTILES:
                dropped += 1
                continue

            issn1, issn2 = split_issns(row.get("Issn", ""))
            writer.writerow({
                "Title":    row["Title"].strip(),
                "Issn_1":   issn1,
                "Issn_2":   issn2,
                "Quartile": quartile,
                "Coverage": row["Coverage"].strip(),
            })
            kept += 1

    print(f"Done.  Kept {kept} journals, dropped {dropped} malformed rows.")
    print(f"Output → {DST}")
    print(f"\nNext step: python3 dblp/build_journal_index.py")


if __name__ == "__main__":
    main()
