import gzip
import hashlib
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ARCHIVE_DIR = Path(__file__).resolve().parent.parent / "archive"
GZ_PATH = ARCHIVE_DIR / "paper-dblp.xml.gz"
XML_PATH = ARCHIVE_DIR / "paper-dblp.xml"
DTD_PATH = ARCHIVE_DIR / "dblp.dtd"
MD5_PATH = ARCHIVE_DIR / "paper-dblp.xml.gz.md5"

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


def _fetch_remote_md5() -> str | None:
    req = urllib.request.Request(URL_MD5, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8").strip()
            return text.split()[0].lower()
    except Exception:
        return None


def _calc_local_md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK * 8), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def _download(url: str, dest: Path, label: str) -> None:
    tmp_dest = dest.with_suffix(dest.suffix + ".tmp")
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
    except urllib.error.URLError as e:
        sys.exit(f"\nCould not reach {url}\n{e}")

    total = int(resp.headers.get("Content-Length", 0))
    done = 0
    t_start = time.monotonic()
    size_str = f" ({_human(total)})" if total else ""
    print(f"Downloading {label}{size_str}...")

    try:
        with open(tmp_dest, "wb") as f:
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
                    f"\r{bar} {pct}  {_human(done):>10}  @ {_human(int(speed))}/s  {eta}   ",
                    end="",
                    flush=True,
                )
        tmp_dest.replace(dest)
        bar_full = _bar(1, 1)
        print(f"\r{bar_full} 100%  {_human(done)} downloaded.                         \n")
    except (Exception, KeyboardInterrupt) as e:
        if tmp_dest.exists():
            tmp_dest.unlink()
        if isinstance(e, KeyboardInterrupt):
            sys.exit("\nDownload aborted.")
        sys.exit(f"\nDownload failed: {e}")


def _extract(gz_path: Path, xml_path: Path) -> None:
    tmp_xml = xml_path.with_suffix(xml_path.suffix + ".tmp")
    done = 0
    t_start = time.monotonic()
    print(f"Extracting {gz_path.name} → {xml_path.name}...")

    try:
        with gzip.open(gz_path, "rb") as src, open(tmp_xml, "wb") as dst:
            while True:
                chunk = src.read(CHUNK * 4)
                if not chunk:
                    break
                dst.write(chunk)
                done += len(chunk)
                elapsed = time.monotonic() - t_start or 0.001
                speed = done / elapsed
                print(
                    f"\rExtracted: {_human(done):>10}  @ {_human(int(speed))}/s   ",
                    end="",
                    flush=True,
                )
        tmp_xml.replace(xml_path)
        elapsed = time.monotonic() - t_start
        print(f"\rExtracted: {_human(done)} in {elapsed:.0f}s.                    \n")
    except (Exception, KeyboardInterrupt) as e:
        if tmp_xml.exists():
            tmp_xml.unlink()
        if isinstance(e, KeyboardInterrupt):
            sys.exit("\nExtraction aborted.")
        sys.exit(f"\nExtraction failed: {e}")


def main() -> None:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    remote_md5 = _fetch_remote_md5()

    if not remote_md5:
        if XML_PATH.exists() and DTD_PATH.exists():
            print("Could not reach DBLP to check for updates. Using existing local archive.")
            return
        elif GZ_PATH.exists() and DTD_PATH.exists():
            print("Could not reach DBLP to check for updates. Extracting existing local zip...")
            _extract(GZ_PATH, XML_PATH)
            return
        else:
            sys.exit("Error: Could not reach DBLP and required local files are missing.")

    local_md5 = None
    if MD5_PATH.exists():
        local_md5 = MD5_PATH.read_text().strip().split()[0].lower()
    elif GZ_PATH.exists():
        print("Calculating checksum of existing local zip...", end="\r", flush=True)
        local_md5 = _calc_local_md5(GZ_PATH)
        MD5_PATH.write_text(f"{local_md5}  paper-dblp.xml.gz\n")
        print(" " * 60, end="\r")

    need_new_gz = (not GZ_PATH.exists()) or (local_md5 != remote_md5)

    if need_new_gz:
        if GZ_PATH.exists():
            print("New DBLP release detected. Downloading update...")
        _download(URL_GZ, GZ_PATH, "paper-dblp.xml.gz")

        downloaded_md5 = _calc_local_md5(GZ_PATH)
        if downloaded_md5 != remote_md5:
            GZ_PATH.unlink(missing_ok=True)
            sys.exit(f"Checksum mismatch on downloaded zip!\nExpected: {remote_md5}\nGot: {downloaded_md5}")

        MD5_PATH.write_text(f"{remote_md5}  paper-dblp.xml.gz\n")

    if not DTD_PATH.exists() or need_new_gz:
        _download(URL_DTD, DTD_PATH, "dblp.dtd")

    if not XML_PATH.exists() or need_new_gz:
        _extract(GZ_PATH, XML_PATH)

    if not need_new_gz and DTD_PATH.exists() and XML_PATH.exists():
        print("DBLP archive is up to date.")


if __name__ == "__main__":
    main()
