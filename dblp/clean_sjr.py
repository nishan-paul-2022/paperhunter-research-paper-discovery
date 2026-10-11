"""
clean_sjr.py - Strip SJR-WoS CSV down to only the columns needed for paperhunter.

Input:  SJR-WoS-2025-CS.csv  (semicolon-delimited, from SCImago/WoS export)
Output: SJR-WoS-2025-CS-clean.csv  (comma-delimited, UTF-8, well-organised)

Output columns:
    Title           - journal name
    Issn_1          - first ISSN, formatted as XXXX-XXXX
    Issn_2          - second ISSN if present, formatted as XXXX-XXXX (else empty)
    Quartile        - SJR Best Quartile: Q1 / Q2 / Q3 / Q4
    Coverage        - year range(s) indexed in DBLP/SJR, e.g. "1983-2026"

Fixes applied:
    - Rows where the Quartile column holds an SJR score (parsing artifact) are dropped.
    - Rows with '-' quartile are dropped.
    - Raw 8-digit ISSNs are formatted with a hyphen at position 4.
"""
import csv
from pathlib import Path

DBLP_DIR = Path(__file__).resolve().parent

SRC = DBLP_DIR / "SJR-WoS-2025-CS.csv"
DST = DBLP_DIR / "SJR-WoS-2025-CS-clean.csv"

VALID_QUARTILES = {"Q1", "Q2", "Q3", "Q4"}
OUT_FIELDS = ["Title", "Issn_1", "Issn_2", "Quartile", "Coverage"]


def format_issn(raw: str) -> str:
    """Turn '15580008' into '1558-0008'. Return as-is if not 8 digits."""
    s = raw.strip().replace("-", "")
    return f"{s[:4]}-{s[4:]}" if len(s) == 8 and s.isdigit() else raw.strip()


def split_issns(cell: str) -> tuple[str, str]:
    """Split a cell like '15580008, 07338716' into two formatted ISSNs."""
    parts = [p.strip() for p in cell.split(",") if p.strip()]
    issn1 = format_issn(parts[0]) if len(parts) > 0 else ""
    issn2 = format_issn(parts[1]) if len(parts) > 1 else ""
    return issn1, issn2


def main() -> None:
    kept = 0
    dropped = 0

    with open(SRC, encoding="utf-8", newline="") as src_f, \
         open(DST, "w", encoding="utf-8", newline="") as dst_f:

        reader = csv.DictReader(src_f, delimiter=";")
        writer = csv.DictWriter(
            dst_f,
            fieldnames=OUT_FIELDS,
            delimiter=",",
            quoting=csv.QUOTE_MINIMAL,
        )
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

    print(f"Done.  Kept {kept} journals, dropped {dropped} malformed/invalid rows.")
    print(f"Output: {DST}")


if __name__ == "__main__":
    main()

