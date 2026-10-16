"""CSV export: pass-through with Role column appended.

Re-reads original input CSV. Keeps header and all fields exactly as parsed.
Selects accepted papers, adds Role column. No column except Role is interpreted.
Row IDs are 1-based ordinals matching init_from_csv().
"""

import csv
import hashlib
import io
import os
from pathlib import Path

import b_db

ROLE_COL = "Role"
ROLES = ("baseline", "benchmark", "evaluation", "survey")


class ExportError(RuntimeError):
    """The export cannot be produced safely (input file missing or changed since init)."""


def _read_records(csv_path, sha1):
    path = Path(csv_path)
    if not path.exists():
        raise ExportError("input CSV not found: %s" % path)
    raw = path.read_bytes()
    if hashlib.sha1(raw).hexdigest() != sha1:
        raise ExportError("input CSV changed since init (sha1 mismatch): %s; refusing to export" % path)
    text = raw.decode("utf-8-sig", errors="replace")
    records = [r for r in csv.reader(io.StringIO(text)) if r != []]
    if not records:
        raise ExportError("input CSV has no header row: %s" % path)
    return records[0], records[1:]


def _with_role(row, width, idx, role):
    """Return `row` with Role at column `idx` (overwrite) or after the `width` header columns."""
    if idx is not None:
        out = row + [""] * (idx + 1 - len(row))
        out[idx] = role
        return out
    row = row + [""] * (width - len(row))
    return row[:width] + [role] + row[width:]


def export(conn, out_dir=None):
    """Write <out_dir or b_db.REFERENCE_DIR>/<stem>.csv with the accepted rows plus `Role`.

    Returns {"path", "total" (input data rows), "accepted" (rows written), "by_role"}.
    """
    csv_path = b_db.meta_get(conn, "csv_path")
    sha1 = b_db.meta_get(conn, "csv_sha1")
    if not csv_path or not sha1:
        raise ExportError("database has no csv_path/csv_sha1 (was it created by init?)")
    header, rows = _read_records(csv_path, sha1)
    idx = header.index(ROLE_COL) if ROLE_COL in header else None
    out_header = header if idx is not None else header + [ROLE_COL]

    out_rows, by_role = [], dict.fromkeys(ROLES, 0)
    for acc in b_db.accepted(conn):
        if 1 <= acc["id"] <= len(rows):
            out_rows.append(_with_role(rows[acc["id"] - 1], len(header), idx, acc["role"]))
            by_role[acc["role"]] += 1

    out = Path(out_dir) if out_dir else b_db.REFERENCE_DIR
    out.mkdir(parents=True, exist_ok=True)
    target = out / ("%s.csv" % Path(csv_path).stem)
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(out_header)
        w.writerows(out_rows)
    os.replace(tmp, target)
    return {"path": str(target), "total": len(rows), "accepted": len(out_rows), "by_role": by_role}
