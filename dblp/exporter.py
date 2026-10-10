"""
Export and reporting utilities for the papers database.
"""
import csv
import sqlite3
from typing import Optional
from .config import DB, CSV, CONF


def export_to_csv(db_path: str = DB, csv_path: str = CSV) -> int:
    """Export all papers to CSV file and return the total count."""
    db = sqlite3.connect(db_path)
    cur = db.execute("SELECT * FROM papers ORDER BY venue, year, key")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([d[0] for d in cur.description])
        w.writerows(cur)
    total = db.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    db.close()
    return total


def report_stats(db_path: str = DB, conf: Optional[dict] = None) -> None:
    """Print statistical breakdown by venue and year, noting any missing venues."""
    if conf is None:
        conf = CONF

    db = sqlite3.connect(db_path)
    total = db.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    per = {}
    for v, y, c in db.execute(
        "SELECT venue, year, COUNT(*) FROM papers GROUP BY 1,2 ORDER BY 1,2"
    ):
        per.setdefault(v, []).append(f"{y}={c}")

    for v in sorted(per):
        print(f"{v:16} " + "  ".join(per[v]))

    missing = sorted((set(conf.values()) | {"VLDB"}) - set(per))
    if missing:
        print("\nNo papers found for:", ", ".join(missing))
    db.close()


def export_and_report(
    db_path: str = DB,
    csv_path: str = CSV,
    conf: Optional[dict] = None,
) -> None:
    """Export papers to CSV and print venue/year summary report."""
    if conf is None:
        conf = CONF

    total = export_to_csv(db_path=db_path, csv_path=csv_path)
    print(f"\n{total} papers -> {db_path}, {csv_path}\n")
    report_stats(db_path=db_path, conf=conf)
