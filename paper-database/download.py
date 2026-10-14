import argparse
import gzip
import hashlib
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
DB_DIR = Path(__file__).resolve().parent.parent / "paper-database"
GZ_PATH = DB_DIR / "dblp.xml.gz"
XML_PATH = DB_DIR / "dblp.xml"
DTD_PATH = DB_DIR / "dblp.dtd"
MD5_PATH = DB_DIR / "dblp.xml.gz.md5"
BASE = "https://dblp.org/xml"
URL_GZ = f"{BASE}/dblp.xml.gz"
URL_DTD = f"{BASE}/dblp.dtd"
URL_MD5 = f"{BASE}/dblp.xml.gz.md5"
HEADERS = {"User-Agent": "paperhunter/1.0 (research tool; non-commercial)"}
CHUNK = 1024 * 1024
def _bar(done: int, total: int, width: int = 30) -> str:
    if total <= 0:
        return f"[{'?' * width}]"
    filled = int(width * done / total)
    return f"[{'█' * filled}{'░' * (width - filled)}]"
def _human(n_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024:
            return f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024
    return f"{n_bytes:.1f} TB"
def _download(url: str, dest: Path, label: str) -> None:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
    except urllib.error.URLError as e:
        sys.exit(f"\n  ✗  Could not reach {url}\n     {e}")
    total = int(resp.headers.get("Content-Length", 0))
    done = 0
    t_start = time.monotonic()
    print(f"  Downloading  {label}")
    print(f"  URL          {url}")
    if total:
        print(f"  Size         {_human(total)}")
    print()
    with open(dest, "wb") as f:
        while True:
            chunk = resp.read(CHUNK)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            elapsed = time.monotonic() - t_start or 0.001
            speed = done / elapsed
            eta_s = int((total - done) / speed) if total and speed else 0
            eta = f"ETA {eta_s // 60:02d}:{eta_s % 60:02d}" if total else ""
            bar = _bar(done, total)
            pct = f"{100 * done // total:3d}%" if total else ""
            print(
                f"\r  {bar} {pct}  {_human(done):>10}"
                f"  @ {_human(int(speed))}/s  {eta}   ",
                end="", flush=True,
            )
    print(f"\r  {'█' * 30}  ✓  {_human(done)} downloaded                         ")
    print()
def _verify_md5(gz_path: Path, md5_path: Path) -> None:
    expected = md5_path.read_text().strip().split()[0].lower()
    print("  Verifying MD5 checksum …", end="\r", flush=True)
    h = hashlib.md5()
    with open(gz_path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK * 8), b""):
            h.update(chunk)
    got = h.hexdigest()
    if got == expected:
        print(f"  ✓  MD5 OK  ({got})\n")
    else:
        print(f"  ✗  MD5 MISMATCH!\n     expected: {expected}\n     got:      {got}")
        sys.exit("Download may be corrupt. Delete the .gz file and re-run.")
def _extract(gz_path: Path, xml_path: Path) -> None:
    gz_size = gz_path.stat().st_size
    done = 0
    t_start = time.monotonic()
    print(f"  Extracting   {gz_path.name}  →  {xml_path.name}")
    print(f"  Compressed   {_human(gz_size)}")
    print(f"  (Extracted XML will be ~25 GB — make sure you have space)\n")
    with gzip.open(gz_path, "rb") as src, open(xml_path, "wb") as dst:
        while True:
            chunk = src.read(CHUNK * 4)
            if not chunk:
                break
            dst.write(chunk)
            done += len(chunk)
            elapsed = time.monotonic() - t_start or 0.001
            speed = done / elapsed
            print(
                f"\r  {_bar(0, 0)}  written {_human(done):>10}"
                f"  @ {_human(int(speed))}/s   ",
                end="", flush=True,
            )
    elapsed = time.monotonic() - t_start
    print(f"\r  {'█' * 30}  ✓  {_human(done)} extracted in {elapsed:.0f}s              ")
    print()
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="Re-download files even if they already exist")
    ap.add_argument("--no-extract", action="store_true",
                    help="Skip extraction; only download the .gz and .dtd")
    args = ap.parse_args()
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║              paperhunter  ·  DBLP Setup                              ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print()
    DB_DIR.mkdir(parents=True, exist_ok=True)
    print("━" * 70)
    print("  Step 1 – dblp.dtd")
    print("━" * 70)
    if DTD_PATH.exists() and not args.force:
        print(f"  ✓  Already exists: {DTD_PATH.name}  (use --force to re-download)\n")
    else:
        _download(URL_DTD, DTD_PATH, "dblp.dtd")
    print("━" * 70)
    print("  Step 2 – MD5 checksum")
    print("━" * 70)
    _download(URL_MD5, MD5_PATH, "dblp.xml.gz.md5")
    print("━" * 70)
    print("  Step 3 – dblp.xml.gz  (~3 GB, may take 10–30 min)")
    print("━" * 70)
    if GZ_PATH.exists() and not args.force:
        print(f"  ✓  Already exists: {GZ_PATH.name}  ({_human(GZ_PATH.stat().st_size)})")
        print(f"     Use --force to re-download.\n")
    else:
        _download(URL_GZ, GZ_PATH, "dblp.xml.gz")
    print("━" * 70)
    print("  Step 4 – Verify checksum")
    print("━" * 70)
    _verify_md5(GZ_PATH, MD5_PATH)
    if args.no_extract:
        print("  --no-extract set. Skipping extraction.")
    else:
        print("━" * 70)
        print("  Step 5 – Extract  (~25 GB uncompressed, may take 5–15 min)")
        print("━" * 70)
        if XML_PATH.exists() and not args.force:
            print(f"  ✓  Already exists: {XML_PATH.name}  ({_human(XML_PATH.stat().st_size)})")
            print(f"     Use --force to re-extract.\n")
        else:
            _extract(GZ_PATH, XML_PATH)
    DB_DIR.mkdir(parents=True, exist_ok=True)
    print("━" * 70)
    print("  Setup complete.")
    print(f"  dblp.xml  →  {XML_PATH}")
    print(f"  dblp.dtd  →  {DTD_PATH}")
    print()
    print("  Next step: python3 hunter/b_journal.py")
    print("━" * 70)
    print()
if __name__ == "__main__":
    main()
