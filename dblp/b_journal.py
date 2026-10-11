import argparse
import csv
import os
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

try:
    from lxml import etree
except ImportError:
    sys.exit("Missing lxml.  Run:  pip install lxml")

DBLP_DIR = Path(__file__).resolve().parent
SJR_SRC = DBLP_DIR / "SJR-WoS-2025-CS.csv"
XML_PATH = DBLP_DIR / "dblp.xml"
OUT_CSV = DBLP_DIR / "List-of-Journal.csv"

OUT_FIELDS = ["dblp_key", "dblp_name", "title", "issn_1", "issn_2",
              "quartile", "coverage"]

VALID_QUARTILES = {"Q1", "Q2", "Q3", "Q4"}
DEFAULT_THRESHOLD = 0.80

_ABBREV = [
    (r"\bTrans\b\.?", "Transactions"),
    (r"\bJ\b\.?", "Journal"),
    (r"\bInt\b\.?", "International"),
    (r"\bComput\b\.?", "Computer"),
    (r"\bSyst\b\.?", "Systems"),
    (r"\bEng\b\.?", "Engineering"),
    (r"\bSci\b\.?", "Science"),
    (r"\bTechnol\b\.?", "Technology"),
    (r"\bAppl\b\.?", "Applied"),
    (r"\bMach\b\.?", "Machine"),
    (r"\bIntell\b\.?", "Intelligence"),
    (r"\bArtif\b\.?", "Artificial"),
    (r"\bInf\b\.?", "Information"),
    (r"\bCommun\b\.?", "Communications"),
    (r"\bAnal\b\.?", "Analysis"),
    (r"\bRecogn\b\.?", "Recognition"),
    (r"\bVis\b\.?", "Vision"),
    (r"\bNetw\b\.?", "Networks"),
    (r"\bDistrib\b\.?", "Distributed"),
    (r"\bAuton\b\.?", "Autonomous"),
    (r"\bEduc\b\.?", "Education"),
    (r"\bLett\b\.?", "Letters"),
    (r"\bManag\b\.?", "Management"),
    (r"\bAdv\b\.?", "Advanced"),
    (r"\bAI\b", "Artificial Intelligence"),
    (r"\bMed\b\.?", "Medical"),
    (r"\bBiol\b\.?", "Biological"),
    (r"\bBioinform\b\.?", "Bioinformatics"),
    (r"\bNeur\b\.?", "Neural"),
    (r"\bRobot\b\.?", "Robotics"),
    (r"\bOptim\b\.?", "Optimization"),
    (r"\bSimul\b\.?", "Simulation"),
    (r"\bSoftw\b\.?", "Software"),
    (r"\bAlgor\b\.?", "Algorithms"),
    (r"\bArchit\b\.?", "Architecture"),
    (r"\bSecur\b\.?", "Security"),
    (r"\bBehav\b\.?", "Behavior"),
    (r"\bCogn\b\.?", "Cognitive"),
    (r"\bRes\b\.?", "Research"),
    (r"\bPract\b\.?", "Practice"),
    (r"\bProc\b\.?", "Proceedings"),
]

_STOP = {
    "a", "an", "the", "and", "or", "of", "in", "on", "at", "to",
    "for", "with", "by", "from", "its", "&",
}


def normalize(text: str) -> str:
    for pat, rep in _ABBREV:
        text = re.sub(pat, rep, text, flags=re.IGNORECASE)
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return " ".join(t for t in text.split() if t not in _STOP)


def match_score(a: str, b: str) -> float:
    ta = set(a.split())
    tb = set(b.split())
    if not ta or not tb:
        return 0.0
    shared = len(ta & tb)
    jaccard = shared / len(ta | tb)
    containment = min(shared / len(ta), shared / len(tb))
    token_sim = max(jaccard, containment)
    seq = SequenceMatcher(None, a, b, autojunk=False).ratio()
    return min(token_sim, seq)


def _format_issn(raw: str) -> str:
    s = raw.strip().replace("-", "")
    return f"{s[:4]}-{s[4:]}" if len(s) == 8 and s.isdigit() else raw.strip()


def _split_issns(cell: str) -> tuple[str, str]:
    parts = [p.strip() for p in cell.split(",") if p.strip()]
    return (
        _format_issn(parts[0]) if parts else "",
        _format_issn(parts[1]) if len(parts) > 1 else "",
    )


def phase_clean(sjr_path: Path) -> list[dict]:
    print("━" * 70)
    print("  Phase 1 – Cleaning SJR CSV")
    print("━" * 70)

    rows = []
    dropped = 0

    with open(sjr_path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            q = row.get("SJR Best Quartile", "").strip()
            if q not in VALID_QUARTILES:
                dropped += 1
                continue
            i1, i2 = _split_issns(row.get("Issn", ""))
            rows.append({
                "title": row["Title"].strip(),
                "issn_1": i1,
                "issn_2": i2,
                "quartile": q,
                "coverage": row["Coverage"].strip(),
                "norm": normalize(row["Title"].strip()),
            })

    print(f"  ✓  {len(rows)} valid journals  |  {dropped} malformed rows dropped\n")
    return rows


def _scan_xml(xml_path: Path) -> dict[str, str]:
    size = os.path.getsize(xml_path)
    seen = index = 0
    result: dict[str, str] = {}

    f = open(xml_path, "rb")
    ctx = etree.iterparse(
        f, events=("end",), tag="article",
        load_dtd=True, resolve_entities=True, huge_tree=True,
    )
    for _, el in ctx:
        seen += 1
        key = el.get("key", "")
        journal = el.findtext("journal") or ""
        if key.startswith("journals/") and journal:
            prefix = "/".join(key.split("/")[:2])
            if prefix not in result:
                result[prefix] = journal.strip()
                index += 1

        if seen % 500_000 == 0:
            pct = f.tell() * 100 // size
            print(f"  {pct:3}%  |  articles scanned: {seen:,}"
                  f"  |  journal keys found: {index:,}",
                  end="\r", flush=True)

        el.clear()
        while el.getprevious() is not None:
            del el.getparent()[0]

    f.close()
    print(f"  100%  |  articles scanned: {seen:,}  |  journal keys found: {index:,}")
    return result


def phase_index(xml_path: Path) -> dict[str, str]:
    print("━" * 70)
    print("  Phase 2 – DBLP Journal Index")
    print("━" * 70)
    print(f"  Scanning {xml_path.name}  ({xml_path.stat().st_size // 1_000_000} MB)")
    print("  (This takes ~15–20 min.)\n")
    idx = _scan_xml(xml_path)
    print(f"\n  ✓  {len(idx):,} unique journal keys found\n")
    return idx


def phase_match(
    dblp_index: dict[str, str],
    sjr_rows: list[dict],
    threshold: float,
) -> list[dict]:
    print("━" * 70)
    print(f"  Phase 3 – Fuzzy Matching  (threshold = {threshold})")
    print("━" * 70)

    sjr_norms = [(r["norm"], r) for r in sjr_rows]
    matched = []
    total = len(dblp_index)

    for n, (dblp_key, dblp_name) in enumerate(sorted(dblp_index.items()), 1):
        nd = normalize(dblp_name)

        best_score = 0.0
        best_row = None
        for ns, sr in sjr_norms:
            s = match_score(nd, ns)
            if s > best_score:
                best_score = s
                best_row = sr

        if best_score >= threshold and best_row is not None:
            matched.append({
                "dblp_key": dblp_key,
                "dblp_name": dblp_name,
                "title": best_row["title"],
                "issn_1": best_row["issn_1"],
                "issn_2": best_row["issn_2"],
                "quartile": best_row["quartile"],
                "coverage": best_row["coverage"],
            })
            tick = "✓"
        else:
            tick = "·"

        label = f"{matched[-1]['quartile']}  {dblp_name[:35]}" if tick == "✓" else dblp_name[:45]
        print(f"  [{n:4}/{total}]  {tick}  {label}", end="\n" if tick == "✓" else "\r")

    print()
    print(f"\n  ✓  {len(matched)} journals matched to DBLP"
          f"  |  {total - len(matched)} had no SJR entry (excluded)\n")
    return matched


def phase_write(rows: list[dict], out_path: Path) -> None:
    print("━" * 70)
    print("  Phase 4 – Writing List-of-Journal.csv")
    print("━" * 70)

    by_title = defaultdict(list)
    for r in rows:
        by_title[r["title"]].append(r)

    deduped = []
    dupes_removed = 0
    for title, group in by_title.items():
        if len(group) == 1:
            deduped.append(group[0])
        else:
            best = max(
                group,
                key=lambda r: (
                    match_score(normalize(r["dblp_name"]), normalize(r["title"])),
                    -len(r["dblp_key"]),
                ),
            )
            deduped.append(best)
            dupes_removed += len(group) - 1

    if dupes_removed:
        print(f"  Deduplication: removed {dupes_removed} lower-scoring duplicate matches")

    order = {"Q1": 0, "Q2": 1, "Q3": 2, "Q4": 3}
    deduped = sorted(deduped,
                     key=lambda r: (order.get(r["quartile"], 9), r["title"].lower()))

    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUT_FIELDS, delimiter=",",
                                quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        writer.writerows(deduped)

    qtrs = Counter(r["quartile"] for r in deduped)
    print(f"  Output → {out_path}")
    print(f"  Total  : {len(deduped)} journals")
    print(f"  Q1: {qtrs['Q1']}   Q2: {qtrs['Q2']}   Q3: {qtrs['Q3']}   Q4: {qtrs['Q4']}\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xml", default=str(XML_PATH),
                    help=f"Path to dblp.xml  (default: {XML_PATH.name})")
    ap.add_argument("--sjr", default=str(SJR_SRC),
                    help=f"Path to SJR CSV   (default: {SJR_SRC.name})")
    ap.add_argument("--out", default=str(OUT_CSV),
                    help=f"Output CSV        (default: {OUT_CSV.name})")
    ap.add_argument("--threshold", default=DEFAULT_THRESHOLD, type=float,
                    help=f"Match threshold 0-1  (default: {DEFAULT_THRESHOLD})")
    args = ap.parse_args()

    xml_path = Path(args.xml)
    sjr_path = Path(args.sjr)
    out_path = Path(args.out)

    if not sjr_path.exists():
        sys.exit(f"SJR CSV not found: {sjr_path}")
    if not xml_path.exists():
        sys.exit(f"dblp.xml not found: {xml_path}\nRun the downloader first.")

    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║              paperhunter  ·  Journal List Builder                   ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print()

    sjr_rows = phase_clean(sjr_path)
    dblp_index = phase_index(xml_path)
    matched = phase_match(dblp_index, sjr_rows, args.threshold)
    phase_write(matched, out_path)

    print("━" * 70)
    print("  Done.  Run the extractor next to pull papers from dblp.xml.")
    print("━" * 70)
    print()


if __name__ == "__main__":
    main()
