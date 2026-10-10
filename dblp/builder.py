"""
Database builder parsing dblp XML dump into SQLite database.
"""
import os
import sqlite3
import sys
from typing import Optional

try:
    from lxml import etree
except ImportError:
    sys.exit("Missing lxml. Run:  pip install lxml")

from .config import DB, XML, CONF
from .classifier import classify


def create_indexes(db: sqlite3.Connection) -> None:
    """Create indexes for efficient venue and year filtering."""
    db.execute("CREATE INDEX IF NOT EXISTS idx_venue_year ON papers(venue, year)")


def build(
    y0: int,
    y1: int,
    xml_path: str = XML,
    db_path: str = DB,
    conf: Optional[dict] = None,
) -> None:
    """Parse dblp XML and build SQLite database of papers matching year range and venues."""
    if conf is None:
        conf = CONF

    tmp = db_path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)

    db = sqlite3.connect(tmp)
    db.execute("""CREATE TABLE papers(
        key TEXT PRIMARY KEY, venue TEXT, year INT, title TEXT, authors TEXT,
        booktitle TEXT, pages TEXT, ee TEXT, doi TEXT)""")

    size = os.path.getsize(xml_path)
    f = open(xml_path, "rb")
    ctx = etree.iterparse(
        f,
        events=("end",),
        tag=("inproceedings", "article"),
        load_dtd=True,
        resolve_entities=True,
        huge_tree=True,
    )
    rows, kept, seen = [], 0, 0
    for _, el in ctx:
        seen += 1
        key = el.get("key", "")
        year = el.findtext("year") or ""
        if year.isdigit() and y0 <= int(year) <= y1:
            venue = classify(el, key, conf=conf)
            if venue:
                ee = el.findtext("ee") or ""
                doi = ee.split("doi.org/", 1)[1] if "doi.org/" in ee else ""
                t = el.find("title")
                rows.append((
                    key,
                    venue,
                    int(year),
                    "".join(t.itertext()) if t is not None else "",
                    "; ".join(a.text or "" for a in el.findall("author")),
                    el.findtext("booktitle") or el.findtext("journal"),
                    el.findtext("pages"),
                    ee,
                    doi,
                ))
                kept += 1
        if len(rows) >= 5000:
            db.executemany("INSERT OR IGNORE INTO papers VALUES(?,?,?,?,?,?,?,?,?)", rows)
            db.commit()
            rows.clear()
        if seen % 500000 == 0:
            print(
                f"\r  {f.tell() * 100 // size}% scanned, {kept} papers kept",
                end="",
                flush=True,
            )
        el.clear()
        while el.getprevious() is not None:
            del el.getparent()[0]

    db.executemany("INSERT OR IGNORE INTO papers VALUES(?,?,?,?,?,?,?,?,?)", rows)
    create_indexes(db)
    db.commit()
    db.close()
    f.close()
    os.replace(tmp, db_path)
    print(f"\r  100% scanned, {kept} papers kept        ")
