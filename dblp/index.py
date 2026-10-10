#!/usr/bin/env python3
"""
dblp/index.py - Unified index and entry point for building the dblp paper database.

Usage:
    pip install lxml            # once
    python3 dblp/index.py       # does everything

Options:
    --refresh        delete local dblp files and download the newest dump
    --from 2023      first year to keep (default 2023)
    --to 2026        last year to keep  (default 2026)

Outputs: papers.db (SQLite) and papers.csv
Safe to re-run: the database is rebuilt from scratch each time.
"""
import argparse
import os
import sys
from pathlib import Path

# Ensure dblp directory is on sys.path for direct execution
pkg_dir = Path(__file__).resolve().parent
if str(pkg_dir) not in sys.path:
    sys.path.insert(0, str(pkg_dir))

try:
    from .config import BASE, DTD, XML_GZ, XML, DB, CSV, CONF
    from .classifier import classify
    from .downloader import download, prepare
    from .builder import build, create_indexes
    from .exporter import export_to_csv, report_stats, export_and_report
except (ImportError, ValueError):
    from config import BASE, DTD, XML_GZ, XML, DB, CSV, CONF
    from classifier import classify
    from downloader import download, prepare
    from builder import build, create_indexes
    from exporter import export_to_csv, report_stats, export_and_report

DOC = """
dblp/index.py - build a paper database of CORE A* conferences from dblp.

Usage:
    pip install lxml            # once
    python3 dblp/index.py       # does everything

Options:
    --refresh        delete local dblp files and download the newest dump
    --from 2023      first year to keep (default 2023)
    --to 2026        last year to keep  (default 2026)

Outputs: papers.db (SQLite) and papers.csv
Safe to re-run: the database is rebuilt from scratch each time.
"""


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=DOC,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--refresh",
        action="store_true",
        help="re-download the newest dblp dump",
    )
    ap.add_argument("--from", dest="y0", type=int, default=2023)
    ap.add_argument("--to", dest="y1", type=int, default=2026)
    return ap


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    prepare(args.refresh)
    print(f"Parsing {XML} ({args.y0}-{args.y1}) ...")
    build(args.y0, args.y1)
    export_and_report()


if __name__ == "__main__":
    main(sys.argv[1:])
