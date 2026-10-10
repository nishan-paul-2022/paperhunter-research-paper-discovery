"""
dblp package - pipeline modules for dblp XML download, parsing, and export.
"""

from .config import BASE, DTD, XML_GZ, XML, DB, CSV, CONF
from .classifier import classify
from .downloader import download, prepare
from .builder import build, create_indexes
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
    "build",
    "create_indexes",
    "export_to_csv",
    "report_stats",
    "export_and_report",
    "main",
    "build_parser",
]
