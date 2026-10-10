"""
Command-line interface for building dblp database and CSV export.
"""
import argparse
import os
import sys
from typing import List, Optional

from .config import XML
from .downloader import prepare
from .builder import build
from .exporter import export_and_report

DOC = """
index.py - build a paper database of CORE A* conferences from dblp.

Usage:
    pip install lxml            # once
    python3 index.py            # does everything

Options:
    --refresh        delete local dblp files and download the newest dump
    --from 2023      first year to keep (default 2023)
    --to 2026        last year to keep  (default 2026)

Outputs: papers.db (SQLite) and papers.csv
Safe to re-run: the database is rebuilt from scratch each time.
"""


def build_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser."""
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


def main(argv: Optional[List[str]] = None) -> None:
    """Main CLI execution flow."""
    parser = build_parser()
    args = parser.parse_args(argv)

    prepare(args.refresh)
    print(f"Parsing {XML} ({args.y0}-{args.y1}) ...")
    build(args.y0, args.y1)
    export_and_report()


if __name__ == "__main__":
    main()
