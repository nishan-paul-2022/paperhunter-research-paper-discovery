#!/usr/bin/env python3
"""
build_dblp_db.py - build a paper database of CORE A* conferences from dblp.

Usage:
    pip install lxml            # once
    python3 build_dblp_db.py    # does everything

Options:
    --refresh        delete local dblp files and download the newest dump
    --from 2023      first year to keep (default 2023)
    --to 2026        last year to keep  (default 2026)

Outputs: papers.db (SQLite) and papers.csv
Safe to re-run: the database is rebuilt from scratch each time.
"""
import argparse
import csv
import gzip
import os
import shutil
import sqlite3
import sys
import urllib.request

try:
    from lxml import etree
except ImportError:
    sys.exit("Missing lxml. Run:  pip install lxml")

BASE = "https://dblp.org/xml/"
DTD, XML_GZ, XML = "dblp.dtd", "dblp.xml.gz", "dblp.xml"
DB, CSV = "papers.db", "papers.csv"

# dblp key prefix -> venue acronym (conference proceedings = <inproceedings>)
CONF = {
    "conf/sigcomm": "SIGCOMM", "conf/ccs": "CCS", "conf/sigecom": "EC",
    "conf/kdd": "KDD", "conf/mobicom": "MOBICOM", "conf/sigir": "SIGIR",
    "conf/sigsoft": "FSE", "conf/isca": "ISCA", "conf/mm": "ACMMM",
    "conf/siggraph": "SIGGRAPH", "conf/pods": "PODS", "conf/sosp": "SOSP",
    "conf/sigmod": "SIGMOD", "conf/podc": "PODC", "conf/stoc": "STOC",
    "conf/uist": "UIST", "conf/popl": "POPL", "conf/pldi": "PLDI",
    "conf/hri": "HRI", "conf/soda": "SODA", "conf/crypto": "CRYPTO",
    "conf/nips": "NeurIPS", "conf/asplos": "ASPLOS", "conf/acl": "ACL",
    "conf/kbse": "ASE", "conf/cav": "CAV", "conf/colt": "COLT",
    "conf/dac": "DAC", "conf/emnlp": "EMNLP", "conf/eccv": "ECCV",
    "conf/cvpr": "CVPR", "conf/vr": "VR", "conf/infocom": "INFOCOM",
    "conf/iccv": "ICCV", "conf/icdm": "ICDM", "conf/percom": "PERCOM",
    "conf/icra": "ICRA", "conf/focs": "FOCS", "conf/lics": "LICS",
    "conf/sp": "SP", "conf/ismar": "ISMAR", "conf/icalp": "ICALP",
    "conf/aips": "ICAPS", "conf/icaps": "ICAPS",  # ICAPS moved aips -> icaps
    "conf/icde": "ICDE", "conf/chi": "CHI", "conf/iclr": "ICLR",
    "conf/icml": "ICML", "conf/icse": "ICSE", "conf/kr": "KR",
    "conf/eurocrypt": "EuroCrypt", "conf/ijcai": "IJCAI",
    "conf/compgeom": "SoCG", "conf/hpca": "HPCA", "conf/micro": "MICRO",
    "conf/www": "WWW", "conf/sigmetrics": "SIGMETRICS", "conf/aaai": "AAAI",
    "conf/siggrapha": "SiggraphA", "conf/ndss": "NDSS", "conf/uss": "USENIX-Security",
    "conf/osdi": "OSDI",
}


def classify(el, key):
    """Return venue acronym for a record, or None if not an A* paper."""
    prefix = "/".join(key.split("/")[:2])
    if el.tag == "inproceedings":
        venue = CONF.get(prefix)
        if venue == "ICAPS" and el.findtext("booktitle") != "ICAPS":
            return None
        return venue
    # <article>: venues published in journals
    num = el.findtext("number")
    if prefix == "journals/pvldb":
        return "VLDB"
    if prefix == "journals/pacmpl":       # POPL / PLDI issues
        return num if num in ("POPL", "PLDI") else None
    if prefix == "journals/pacmse":       # FSE issues (skip ISSTA)
        return "FSE" if num == "FSE" else None
    if prefix == "journals/pacmmod":      # SIGMOD (PODS papers are mixed in)
        return "SIGMOD"
    if prefix == "journals/pomacs":       # SIGMETRICS
        return "SIGMETRICS"
    if prefix == "journals/tog":          # issue 4 = SIGGRAPH, 6 = SIGGRAPH Asia
        return {"4": "SIGGRAPH", "6": "SiggraphA"}.get(num)
    return None


def download(url, dest):
    print(f"Downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as r, open(dest + ".part", "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done * 100 // total}%", end="", flush=True)
    os.replace(dest + ".part", dest)
    print()


def prepare(refresh):
    if refresh:
        for f in (DTD, XML_GZ, XML):
            if os.path.exists(f):
                os.remove(f)
    if not os.path.exists(DTD):
        download(BASE + DTD, DTD)
    if not os.path.exists(XML):
        if not os.path.exists(XML_GZ):
            download(BASE + XML_GZ, XML_GZ)
        print("Unzipping (a few GB)...")
        with gzip.open(XML_GZ, "rb") as s, open(XML, "wb") as d:
            shutil.copyfileobj(s, d, 1 << 20)
        os.remove(XML_GZ)


def build(y0, y1):
    tmp = DB + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    db.execute("""CREATE TABLE papers(
        key TEXT PRIMARY KEY, venue TEXT, year INT, title TEXT, authors TEXT,
        booktitle TEXT, pages TEXT, ee TEXT, doi TEXT)""")

    size = os.path.getsize(XML)
    f = open(XML, "rb")
    ctx = etree.iterparse(f, events=("end",), tag=("inproceedings", "article"),
                          load_dtd=True, resolve_entities=True, huge_tree=True)
    rows, kept, seen = [], 0, 0
    for _, el in ctx:
        seen += 1
        key = el.get("key", "")
        year = el.findtext("year") or ""
        if year.isdigit() and y0 <= int(year) <= y1:
            venue = classify(el, key)
            if venue:
                ee = el.findtext("ee") or ""
                doi = ee.split("doi.org/", 1)[1] if "doi.org/" in ee else ""
                t = el.find("title")
                rows.append((
                    key, venue, int(year),
                    "".join(t.itertext()) if t is not None else "",
                    "; ".join(a.text or "" for a in el.findall("author")),
                    el.findtext("booktitle") or el.findtext("journal"),
                    el.findtext("pages"), ee, doi))
                kept += 1
        if len(rows) >= 5000:
            db.executemany("INSERT OR IGNORE INTO papers VALUES(?,?,?,?,?,?,?,?,?)", rows)
            db.commit()
            rows.clear()
        if seen % 500000 == 0:
            print(f"\r  {f.tell() * 100 // size}% scanned, {kept} papers kept",
                  end="", flush=True)
        el.clear()
        while el.getprevious() is not None:
            del el.getparent()[0]
    db.executemany("INSERT OR IGNORE INTO papers VALUES(?,?,?,?,?,?,?,?,?)", rows)
    db.execute("CREATE INDEX idx_venue_year ON papers(venue, year)")
    db.commit()
    db.close()
    f.close()
    os.replace(tmp, DB)
    print(f"\r  100% scanned, {kept} papers kept        ")


def export_and_report():
    db = sqlite3.connect(DB)
    cur = db.execute("SELECT * FROM papers ORDER BY venue, year, key")
    with open(CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([d[0] for d in cur.description])
        w.writerows(cur)
    total = db.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    print(f"\n{total} papers -> {DB}, {CSV}\n")
    per = {}
    for v, y, c in db.execute(
            "SELECT venue, year, COUNT(*) FROM papers GROUP BY 1,2 ORDER BY 1,2"):
        per.setdefault(v, []).append(f"{y}={c}")
    for v in sorted(per):
        print(f"{v:16} " + "  ".join(per[v]))
    missing = sorted((set(CONF.values()) | {"VLDB"}) - set(per))
    if missing:
        print("\nNo papers found for:", ", ".join(missing))
    db.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true",
                    help="re-download the newest dblp dump")
    ap.add_argument("--from", dest="y0", type=int, default=2023)
    ap.add_argument("--to", dest="y1", type=int, default=2026)
    a = ap.parse_args()

    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    prepare(a.refresh)
    print(f"Parsing {XML} ({a.y0}-{a.y1}) ...")
    build(a.y0, a.y1)
    export_and_report()


if __name__ == "__main__":
    main()
