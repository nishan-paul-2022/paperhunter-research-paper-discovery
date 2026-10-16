"""Shared pytest fixtures for PAPER-HUNTER v3.

Tests never touch real data: every database lives in a pytest tmp dir and every
CSV is generated here. No test may use the network.
"""

import csv
import sys
from pathlib import Path

import pytest

HUNTER = Path(__file__).resolve().parent.parent
if str(HUNTER) not in sys.path:
    sys.path.insert(0, str(HUNTER))

import b_db  # noqa: E402

# (title, abstract or "")  - first rows are in-scope, the rest are clear negatives
SAMPLE_PAPERS = [
    ("Incalmo: an Autonomous LLM-Assisted System for Red Teaming Multi-Host Networks",
     "Security operators use red teams to simulate real attackers. We present Incalmo, an LLM agent "
     "that autonomously performs multi-stage attacks against multi-host networks using high-level tasks."),
    ("HackWorld: Evaluating Computer-Use Agents on Exploiting Web Application Vulnerabilities", ""),
    ("MARS: Exploiting Multi-Level Parallelism for DNN Workloads on Adaptive Multi-Accelerator Systems",
     "Along with the fast evolution of deep neural networks, the hardware system is also developing."),
    ("Balancing exploration and exploitation in reinforcement learning", ""),
    ("Caching in Automated Data Centric Vehicles for Edge Computing Scenarios", ""),
]


@pytest.fixture
def make_csv(tmp_path):
    """make_csv(rows=None, extra_cols=True) -> Path of a CSV with dblp-style columns."""

    def _make(rows=None, name="sample.csv", extra_cols=True):
        rows = rows or SAMPLE_PAPERS
        p = tmp_path / name
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["dblp_key", "title", "authors", "year", "venue", "ee", "url"])
            for i, (title, _abs) in enumerate(rows, 1):
                w.writerow([
                    "conf/x/K%d" % i, title, "A. Author; B. Author", 2020 + i % 7, "VENUE%d" % i,
                    "https://doi.org/10.1000/test.%d" % i, "db/conf/x/x.html#K%d" % i,
                ])
        return p

    return _make


@pytest.fixture
def tmp_db(tmp_path, make_csv):
    """A fresh v3 database built from SAMPLE_PAPERS, abstracts stored where given.

    Returns an open connection. csv path is in meta('csv_path').
    """
    csv_path = make_csv()
    db_path = tmp_path / "t.db"
    b_db.init_from_csv(csv_path, db_path)
    conn = b_db.open_db(db_path)
    items = [(i, ab, "openalex") for i, (_t, ab) in enumerate(SAMPLE_PAPERS, 1) if ab]
    b_db.put_abstracts(conn, items)
    yield conn
    conn.close()
