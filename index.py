#!/usr/bin/env python3
"""
index.py - Unified index and entry point for building the dblp paper database.

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
import os
import sys

from dblp import (
    BASE,
    DTD,
    XML_GZ,
    XML,
    DB,
    CSV,
    CONF,
    classify,
    download,
    prepare,
    create_indexes,
    ensure_all_indexes,
    list_indexes,
    build,
    export_to_csv,
    report_stats,
    export_and_report,
    main,
    build_parser,
)

__all__ = [
    "BASE",
    "DTD",
    "XML_GZ",
    "XML",
    "DB",
    "CSV",
    "CONF",
    "classify",
    "download",
    "prepare",
    "create_indexes",
    "ensure_all_indexes",
    "list_indexes",
    "build",
    "export_to_csv",
    "report_stats",
    "export_and_report",
    "main",
    "build_parser",
]

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main(sys.argv[1:])
