"""
Downloader and extraction utilities for dblp XML and DTD.
"""
import gzip
import os
import shutil
import urllib.request
from typing import Optional

try:
    from .config import BASE, DTD, XML_GZ, XML
except (ImportError, ValueError):
    from config import BASE, DTD, XML_GZ, XML


def download(url: str, dest: str) -> None:
    """Download a file over HTTP with progress indicator and atomic rename."""
    print(f"Downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    part_dest = dest + ".part"
    with urllib.request.urlopen(req) as r, open(part_dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done * 100 // total}%", end="", flush=True)
    os.replace(part_dest, dest)
    print()


def prepare(
    refresh: bool = False,
    base_url: str = BASE,
    dtd_file: str = DTD,
    xml_gz_file: str = XML_GZ,
    xml_file: str = XML,
) -> None:
    """Ensure DTD and XML files are present locally, downloading/unzipping if needed."""
    if refresh:
        for f in (dtd_file, xml_gz_file, xml_file):
            if os.path.exists(f):
                os.remove(f)
    if not os.path.exists(dtd_file):
        download(base_url + dtd_file, dtd_file)
    if not os.path.exists(xml_file):
        if not os.path.exists(xml_gz_file):
            download(base_url + xml_gz_file, xml_gz_file)
        print("Unzipping (a few GB)...")
        with gzip.open(xml_gz_file, "rb") as s, open(xml_file, "wb") as d:
            shutil.copyfileobj(s, d, 1 << 20)
        os.remove(xml_gz_file)
