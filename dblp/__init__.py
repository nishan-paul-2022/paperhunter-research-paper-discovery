"""
dblp package - pipeline modules for dblp XML download, parsing, indexing, and export.
"""

from .config import BASE, DTD, XML_GZ, XML, DB, CSV, CONF
from .classifier import classify
from .downloader import download, prepare
from .indexer import create_indexes, ensure_all_indexes, list_indexes
from .builder import build
from .exporter import export_to_csv, report_stats, export_and_report
from .cli import main, build_parser

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
