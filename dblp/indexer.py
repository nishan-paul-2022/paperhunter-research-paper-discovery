"""
Database index management for papers SQLite database.
"""
import sqlite3
from typing import List, Tuple


def create_indexes(db: sqlite3.Connection) -> None:
    """Create primary indexes for efficient venue and year filtering."""
    db.execute("CREATE INDEX IF NOT EXISTS idx_venue_year ON papers(venue, year)")


def ensure_all_indexes(db: sqlite3.Connection) -> None:
    """Create recommended indexes for queries across venue, year, and key."""
    db.execute("CREATE INDEX IF NOT EXISTS idx_venue_year ON papers(venue, year)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_venue ON papers(venue)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_year ON papers(year)")


def list_indexes(db: sqlite3.Connection) -> List[Tuple[str, str]]:
    """Return all index names and associated tables in the database."""
    cur = db.execute("SELECT name, tbl_name FROM sqlite_master WHERE type = 'index'")
    return cur.fetchall()
