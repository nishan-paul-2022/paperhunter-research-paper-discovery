"""CSV export: accepted papers with their DBLP record and a Role column.

Rows come from dblp_paper in archive/paperhunter.db, looked up by each paper's dblp_key.
No column except Role is interpreted.
"""

import csv
import os
from pathlib import Path

import b_db

ROLE_COL = "Role"
ROLES = ("baseline", "benchmark", "evaluation", "survey")
COLUMNS = ("dblp_key", "title", "authors", "year", "venue", "ee", "url")


class ExportError(RuntimeError):
    """The export cannot be produced safely (run or source papers missing)."""


def export(conn, out_dir=None):
    """Write <out_dir or b_db.REFERENCE_DIR>/<run>.csv with the accepted papers plus `Role`.

    Returns {"path", "total" (papers in the run), "accepted" (rows written), "by_role"}.
    """
    run = b_db.meta_get(conn, "run")
    if not run:
        raise ExportError("database has no run name (was it created by init?)")
    if not b_db.SOURCE_DB.exists():
        raise ExportError("paper source not found: %s" % b_db.SOURCE_DB)
    acc = b_db.accepted(conn)
    keys = {r["id"]: r["dblp_key"] for r in conn.execute(
        "SELECT id, dblp_key FROM paper WHERE id IN (SELECT paper_id FROM result WHERE verdict='accept')")}
    rows = b_db.source_rows(keys.values(), COLUMNS[1:])
    missing = [keys[a["id"]] for a in acc if keys.get(a["id"]) not in rows]
    if missing:
        raise ExportError("%d accepted papers are no longer in dblp_paper (e.g. %s); re-run "
                          "run/d_extract.py or re-check the venue lists" % (len(missing), missing[0]))

    out_rows, by_role = [], dict.fromkeys(ROLES, 0)
    for a in acc:
        key = keys[a["id"]]
        out_rows.append([key, *rows[key], a["role"]])
        by_role[a["role"]] += 1

    out = Path(out_dir) if out_dir else b_db.REFERENCE_DIR
    out.mkdir(parents=True, exist_ok=True)
    target = out / ("%s.csv" % run)
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(list(COLUMNS) + [ROLE_COL])
        w.writerows(out_rows)
    os.replace(tmp, target)
    total = int(b_db.meta_get(conn, "n_rows", 0) or 0)
    return {"path": str(target), "total": total, "accepted": len(out_rows), "by_role": by_role}
