#!/usr/bin/env python3
"""a_cli.py: CLI for PAPER-HUNTER v3.

Argument parsing and dossier rendering only. No policy.
"""

import argparse
import json
import sqlite3
import sys
import textwrap
from pathlib import Path

import b_db
import c_schema
import d_network
import e_fetch
import f_cues
import g_prior
import h_flow
import i_export

TOOLS_DIR = Path(__file__).resolve().parent


def die(msg, code=2):
    print("ERROR: " + msg, file=sys.stderr)
    sys.exit(code)


def wrap(text, width=110, indent=""):
    return textwrap.fill(text, width=width, initial_indent=indent, subsequent_indent=indent)


# --------------------------------------------------------------------------- doctor

def cmd_doctor(a):
    """Check prerequisites: SQLite version, WAL support, network hosts, pdftotext, CPU count."""
    import multiprocessing
    import shutil
    import urllib.request
    
    report = {"ok": True}
    
    # SQLite version check
    try:
        ver_tuple = tuple(int(x) for x in sqlite3.sqlite_version.split("."))
        report["sqlite_version"] = sqlite3.sqlite_version
        report["sqlite_ok"] = ver_tuple >= (3, 35)
        if not report["sqlite_ok"]:
            report["ok"] = False
            report["sqlite_error"] = "Need >= 3.35 for RETURNING; found %s" % sqlite3.sqlite_version
    except Exception as e:
        report["sqlite_version"] = sqlite3.sqlite_version
        report["sqlite_ok"] = False
        report["sqlite_error"] = str(e)
        report["ok"] = False
    
    # WAL support
    try:
        tmp = b_db.TOOLS_DIR / ".doctor_test.db"
        conn = sqlite3.connect(str(tmp))
        conn.execute("PRAGMA journal_mode=WAL")
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        tmp.unlink()
        report["wal_ok"] = mode.lower() == "wal"
        if not report["wal_ok"]:
            report["ok"] = False
            report["wal_error"] = "WAL mode not available"
    except Exception as e:
        report["wal_ok"] = False
        report["wal_error"] = str(e)
        report["ok"] = False
    
    # Network hosts
    hosts = [d_network.URL_OPENALEX, d_network.URL_S2, d_network.URL_CROSSREF, d_network.URL_ARXIV]
    report["network"] = {}
    for url in hosts:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "paper-hunter/3.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                report["network"][url] = {"ok": True, "status": resp.status}
        except Exception as e:
            report["network"][url] = {"ok": False, "error": str(e)}
    
    # pdftotext
    pdftotext_path = shutil.which("pdftotext")
    report["pdftotext"] = pdftotext_path is not None
    if not pdftotext_path:
        report["pdftotext_warning"] = "pdftotext not found; fulltext extraction from PDFs will fail"
    
    # CPU count
    report["cpu_count"] = multiprocessing.cpu_count()
    
    print(json.dumps(report, indent=2))
    if not report["ok"]:
        sys.exit(1)


# --------------------------------------------------------------------------- init

def cmd_init(a):
    """Initialize database from CSV."""
    csv_path, db_path = b_db.run_paths(a.csv)
    
    if db_path.exists() and not a.force:
        # Check version
        ver = b_db.user_version(db_path)
        if ver < b_db.SCHEMA_VERSION:
            legacy = db_path.with_name(db_path.stem + ".legacy.db")
            print("Renaming old DB (v%d) to %s" % (ver, legacy))
            db_path.rename(legacy)
            if a.reuse_abstracts_from:
                a.reuse_abstracts_from = str(legacy)
        elif ver == b_db.SCHEMA_VERSION:
            # Resume
            conn = b_db.open_db(db_path)
            existing_csv = b_db.meta_get(conn, "csv_path")
            if str(csv_path) != existing_csv:
                die("DB exists for a different CSV: %s (wanted %s); use --force or a new DB" % (existing_csv, csv_path))
            print("Resuming existing run: %d papers" % b_db.count_papers(conn, canonical=False))
            conn.close()
            return
        else:
            die("DB has unknown version %d (expected %d)" % (ver, b_db.SCHEMA_VERSION))
    
    # Create fresh
    if db_path.exists() and a.force:
        db_path.unlink()
    
    conn = b_db.open_db(db_path, create=True)
    handle_cols = a.handle_cols.split(",") if a.handle_cols else ("ee", "url", "doi")
    stats = b_db.init_from_csv(csv_path, db_path, handle_cols=handle_cols, fresh=True)
    print("Initialized: %(rows)d rows, %(papers)d papers, %(duplicates)d duplicates" % stats)
    
    if a.reuse_abstracts_from:
        copied = b_db.reuse_abstracts(conn, a.reuse_abstracts_from)
        print("Reused %d abstracts from legacy DB" % copied)
    
    # Write .current
    (b_db.TOOLS_DIR / ".current").write_text(str(csv_path))
    conn.close()


# --------------------------------------------------------------------------- fetch

def cmd_fetch(a):
    """Run fetch passes."""
    conn = b_db.open_db(b_db.run_paths(a.csv)[1])
    
    if a.deep:
        print("Running deep fetch (tier >= 1, no abstract)...")
    else:
        print("Running initial fetch (all DOIs)...")
    
    result = e_fetch.run_fetch(conn, deep=a.deep, workers=a.workers)
    
    if result.get("aborted"):
        print("ABORTED: early network probe failed")
        sys.exit(1)
    
    print("Candidates: %(candidates)d, abstracts found: %(abstracts_found)d" % result)
    if result.get("by_src"):
        print("  By source: %s" % ", ".join("%s=%d" % (k, v) for k, v in sorted(result["by_src"].items())))
    
    conn.close()


# --------------------------------------------------------------------------- triage

def cmd_triage(a):
    """Compute cues, tier, prior for all canonical papers."""
    conn = b_db.open_db(b_db.run_paths(a.csv)[1])
    
    print("Running triage (cues, tier, prior)...")
    result = f_cues.run_triage(conn, workers=a.workers)
    
    print("Triaged: %(processed)d papers processed" % result)
    if result.get("tiers"):
        print("Tier histogram:")
        for tier in sorted(result["tiers"].keys()):
            print("  tier %d: %d" % (tier, result["tiers"][tier]))
    
    conn.close()


# --------------------------------------------------------------------------- run-deterministic

def cmd_run_deterministic(a):
    """Convenience: init (if needed) → fetch → triage → deep fetch → triage → advance."""
    csv_path, db_path = b_db.run_paths(a.csv)
    
    # Init if needed
    if not db_path.exists():
        print("=== INIT ===")
        a_init = argparse.Namespace(csv=a.csv, force=False, reuse_abstracts_from=None, handle_cols=None)
        cmd_init(a_init)
    
    conn = b_db.open_db(db_path)
    
    # Initial fetch
    print("\n=== FETCH (initial) ===")
    r1 = e_fetch.run_fetch(conn, deep=False, workers=a.workers)
    if r1.get("aborted"):
        print("ABORTED: network probe failed")
        conn.close()
        sys.exit(1)
    print("Abstracts found: %(abstracts_found)d" % r1)
    
    # Triage
    print("\n=== TRIAGE ===")
    r2 = f_cues.run_triage(conn, workers=a.workers)
    print("Triaged: %(computed)d papers" % r2)
    
    # Deep fetch
    print("\n=== FETCH (deep) ===")
    r3 = e_fetch.run_fetch(conn, deep=True, workers=a.workers)
    print("Deep candidates: %(candidates)d, found: %(abstracts_found)d" % r3)
    
    # Triage again
    print("\n=== TRIAGE (incremental) ===")
    r4 = f_cues.run_triage(conn, workers=a.workers)
    print("Triaged: %(computed)d additional papers" % r4)
    
    # Advance once
    print("\n=== ADVANCE ===")
    status = h_flow.advance(conn)
    print(json.dumps(status, indent=2))
    
    conn.close()


# --------------------------------------------------------------------------- advance

def cmd_advance(a):
    """Create tasks, finalize reviews, print status."""
    conn = b_db.open_db(b_db.run_paths(a.csv)[1])
    status = h_flow.advance(conn)
    print(json.dumps(status))
    conn.close()


# --------------------------------------------------------------------------- claim

def cmd_claim(a):
    """Atomic lease of tasks; print dossiers."""
    conn = b_db.open_db(b_db.run_paths(a.csv)[1])
    
    pids = b_db.claim(conn, a.stage, a.owner, n=a.n, needs_web=a.needs_web)
    
    if not pids:
        print("# No tasks available")
        conn.close()
        return
    
    dossiers = b_db.dossiers(conn, pids, blind=(a.stage == "blind"), with_reviews=(a.stage == "final"))
    
    for d in dossiers:
        print(_render_dossier(d, a.stage))
        print()
    
    conn.close()


def _render_dossier(d, stage):
    """Render a dossier for a reviewer."""
    lines = []
    lines.append("### %d  stage=%s  tier=%s" % (d["id"], stage, d.get("tier", "?")))
    lines.append("TITLE: %s" % d["title"])
    
    if d.get("abstract"):
        src = "[%s]" % d["abstract_src"] if d.get("abstract_src") else ""
        lines.append("ABSTRACT%s: %s" % (src, d["abstract"][:500]))
        if len(d["abstract"]) > 500:
            lines.append("  [... %d more chars]" % (len(d["abstract"]) - 500))
    else:
        handle = d.get("fetch_handle", "")
        lines.append("ABSTRACT: NONE  fetch_handle: %s" % handle)
    
    if not d.get("blind", False):
        if d.get("cues"):
            lines.append("CUES: %s" % d["cues"])
        if d.get("prior"):
            lines.append("PRIOR: %s (advisory)" % d["prior"])
    
    ft = d.get("fulltext")
    if ft:
        lines.append("FULLTEXT: stored (%d chars)" % len(ft))
    else:
        lines.append("FULLTEXT: not stored")
    
    if d.get("reviews"):
        lines.append("\nEARLIER REVIEWS:")
        for r in d["reviews"]:
            lines.append("  %s: %s p=%.2f role=%s" % (r["stage"], r["verdict"], r["p"], r.get("role") or r.get("rclass")))
            if r.get("quote"):
                lines.append("    quote: %s" % r["quote"][:80])
            lines.append("    reason: %s" % r["reason"])
    
    return "\n".join(lines)


# --------------------------------------------------------------------------- submit

def cmd_submit(a):
    """Validate and store reviews from a JSON file."""
    conn = b_db.open_db(b_db.run_paths(a.csv)[1])
    
    if a.file:
        records = json.loads(Path(a.file).read_text())
    else:
        records = json.load(sys.stdin)
    
    if not isinstance(records, list):
        die("Input must be a JSON array of review records")
    
    results = h_flow.submit_reviews(conn, records, a.owner, a.stage)
    
    for r in results:
        if r["ok"]:
            print("OK: paper %d" % r["id"])
        else:
            print("FAILED: paper %d" % r["id"])
            for err in r["errors"]:
                print("  - %s" % err)
    
    conn.close()
    
    failed = sum(1 for r in results if not r["ok"])
    if failed:
        sys.exit(1)


# --------------------------------------------------------------------------- fill

def cmd_fill(a):
    """Store a web-fetched abstract."""
    conn = b_db.open_db(b_db.run_paths(a.csv)[1])
    
    text = Path(a.abstract_file).read_text(encoding="utf-8")
    text = text.strip()
    
    if len(text) < b_db.MIN_ABSTRACT:
        die("Abstract too short: %d chars (need >= %d)" % (len(text), b_db.MIN_ABSTRACT))
    
    b_db.put_abstract(conn, a.id, text, "web", replace=True)
    print("Stored abstract for paper %d (%d chars)" % (a.id, len(text)))
    
    if a.url:
        b_db.meta_set(conn, "fill_%d_url" % a.id, a.url)
    
    conn.close()


# --------------------------------------------------------------------------- fulltext

def cmd_fulltext(a):
    """Fetch or import full text; print outline + excerpts."""
    conn = b_db.open_db(b_db.run_paths(a.csv)[1])
    
    if a.file:
        text = Path(a.file).read_text(encoding="utf-8")
        src = "file"
    elif a.url:
        # Fetch from URL (simplified; real implementation would use web tools)
        die("URL fetching not yet implemented; use --file for now")
    else:
        # Try to fetch from stored handles
        paper = b_db.get_paper(conn, a.id)
        if not paper:
            die("Paper %d not found" % a.id)
        
        # For now, just fail
        die("Automatic fetching not yet implemented; use --file or --url")
    
    if len(text) < 3000:
        die("Text too short: %d chars (need >= 3000)" % len(text))
    
    b_db.put_fulltext(conn, a.id, text, src)
    print("Stored fulltext for paper %d (%d chars)" % (a.id, len(text)))
    
    # Print outline (first 2000 chars as a simple implementation)
    print("\nOUTLINE:")
    print(text[:2000])
    if len(text) > 2000:
        print("\n[... %d more chars]" % (len(text) - 2000))
    
    conn.close()


# --------------------------------------------------------------------------- show

def cmd_show(a):
    """Display dossier for any paper."""
    conn = b_db.open_db(b_db.run_paths(a.csv)[1])
    
    dossiers = b_db.dossiers(conn, [a.id], blind=a.blind, with_reviews=True)
    
    if not dossiers:
        die("Paper %d not found" % a.id)
    
    print(_render_dossier(dossiers[0], "show"))
    
    conn.close()


# --------------------------------------------------------------------------- status

def cmd_status(a):
    """Print phase counts, task counts, tier histogram, result counts."""
    conn = b_db.open_db(b_db.run_paths(a.csv)[1])
    
    n_papers = b_db.count_papers(conn, canonical=True)
    n_abstracts = conn.execute("SELECT COUNT(*) FROM abstract a JOIN paper p ON a.paper_id=p.id WHERE p.dup_of IS NULL").fetchone()[0]
    n_fulltext = conn.execute("SELECT COUNT(*) FROM fulltext f JOIN paper p ON f.paper_id=p.id WHERE p.dup_of IS NULL").fetchone()[0]
    
    print("=== DATABASE ===")
    print("Papers (canonical): %d" % n_papers)
    print("Abstracts: %d (%.1f%%)" % (n_abstracts, 100 * n_abstracts / n_papers if n_papers else 0))
    print("Fulltext: %d" % n_fulltext)
    
    tier_hist = b_db.tier_histogram(conn)
    if tier_hist:
        print("\n=== TIER HISTOGRAM ===")
        for tier in sorted(tier_hist.keys()):
            print("Tier %d: %d" % (tier, tier_hist[tier]))
    
    task_counts = b_db.task_counts(conn)
    if task_counts:
        print("\n=== TASKS ===")
        for stage in ("review", "blind", "final"):
            if stage in task_counts:
                print("%s: %s" % (stage, ", ".join("%s=%d" % (st, n) for st, n in sorted(task_counts[stage].items()))))
    
    results = conn.execute("SELECT verdict, COUNT(*) FROM result GROUP BY verdict").fetchall()
    if results:
        print("\n=== RESULTS ===")
        for verdict, count in results:
            print("%s: %d" % (verdict, count))
        
        roles = conn.execute("SELECT role, COUNT(*) FROM result WHERE verdict='accept' GROUP BY role").fetchall()
        if roles:
            print("Accepted by role:")
            for role, count in roles:
                print("  %s: %d" % (role, count))
    
    conn.close()


# --------------------------------------------------------------------------- export

def cmd_export(a):
    """Write the filtered CSV to paper-reference/."""
    conn = b_db.open_db(b_db.run_paths(a.csv)[1])
    
    try:
        result = i_export.export(conn, out_dir=a.out_dir)
        print("Exported to: %(path)s" % result)
        print("Total input rows: %(total)d" % result)
        print("Accepted: %(accepted)d" % result)
        print("By role:")
        for role in ("baseline", "benchmark", "evaluation", "survey"):
            count = result["by_role"].get(role, 0)
            if count:
                print("  %s: %d" % (role, count))
    except i_export.ExportError as e:
        die(str(e))
    
    conn.close()


# --------------------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser(description="PAPER-HUNTER v3 CLI")
    p.add_argument("--csv", help="CSV path (default: from .current)")
    
    sub = p.add_subparsers(dest="command", required=True)
    
    # doctor
    sub.add_parser("doctor", help="Check prerequisites")
    
    # init
    p_init = sub.add_parser("init", help="Initialize database from CSV")
    p_init.add_argument("--force", action="store_true", help="Recreate DB even if it exists")
    p_init.add_argument("--reuse-abstracts-from", metavar="DB", help="Copy abstracts from legacy DB")
    p_init.add_argument("--handle-cols", default="ee,url,doi", help="CSV columns to search for DOI/links")
    
    # fetch
    p_fetch = sub.add_parser("fetch", help="Fetch abstracts from providers")
    p_fetch.add_argument("--deep", action="store_true", help="Deep fetch for tier>=1 papers without abstracts")
    p_fetch.add_argument("--workers", type=int, default=16, help="Thread count")
    
    # triage
    p_triage = sub.add_parser("triage", help="Compute cues, tier, prior")
    p_triage.add_argument("--workers", type=int, default=0, help="Process count (0=auto)")
    
    # run-deterministic
    p_rd = sub.add_parser("run-deterministic", help="Run init→fetch→triage→deep→triage→advance")
    p_rd.add_argument("--workers", type=int, default=16, help="Thread/process count")
    
    # advance
    sub.add_parser("advance", help="Create tasks, finalize reviews")
    
    # claim
    p_claim = sub.add_parser("claim", help="Claim tasks for review")
    p_claim.add_argument("--stage", required=True, choices=["review", "blind", "final"], help="Stage")
    p_claim.add_argument("--owner", required=True, help="Reviewer name")
    p_claim.add_argument("--n", type=int, default=8, help="Batch size")
    p_claim.add_argument("--needs-web", type=int, choices=[0, 1], help="Filter by needs_web flag")
    
    # submit
    p_submit = sub.add_parser("submit", help="Submit review records")
    p_submit.add_argument("--owner", required=True, help="Reviewer name")
    p_submit.add_argument("--stage", default="review", choices=["review", "blind", "final"], help="Stage")
    p_submit.add_argument("--file", help="JSON file (default: stdin)")
    
    # fill
    p_fill = sub.add_parser("fill", help="Store web-fetched abstract")
    p_fill.add_argument("id", type=int, help="Paper ID")
    p_fill.add_argument("--abstract-file", required=True, help="Text file with abstract")
    p_fill.add_argument("--url", help="Source URL (optional)")
    
    # fulltext
    p_ft = sub.add_parser("fulltext", help="Fetch or import full text")
    p_ft.add_argument("id", type=int, help="Paper ID")
    p_ft.add_argument("--url", help="Fetch from URL")
    p_ft.add_argument("--file", help="Read from file")
    
    # show
    p_show = sub.add_parser("show", help="Display paper dossier")
    p_show.add_argument("id", type=int, help="Paper ID")
    p_show.add_argument("--blind", action="store_true", help="Hide cues and prior")
    
    # status
    sub.add_parser("status", help="Print statistics")
    
    # export
    p_export = sub.add_parser("export", help="Write filtered CSV")
    p_export.add_argument("--out-dir", help="Output directory (default: paper-reference/)")
    
    a = p.parse_args()
    
    # Dispatch
    cmd = "cmd_" + a.command.replace("-", "_")
    if cmd in globals():
        globals()[cmd](a)
    else:
        die("Command not implemented: %s" % a.command)


if __name__ == "__main__":
    main()
