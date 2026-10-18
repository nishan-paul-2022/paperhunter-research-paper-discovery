#!/usr/bin/env python3
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

HUNTER_DIR = Path(__file__).resolve().parent
REPO_ROOT = HUNTER_DIR.parent
ARCHIVE_DIR = REPO_ROOT / "archive"
SUPPLEMENT_DIR = REPO_ROOT / "conference-supplement"
CORE_MASTER_CSV = ARCHIVE_DIR / "publication-core-2026.csv"
VENUES_CSV = SUPPLEMENT_DIR / "1-dblp-venues.csv"
CANDIDATES_CSV = SUPPLEMENT_DIR / "2-exact-matched.csv"
OUT_CSV = SUPPLEMENT_DIR / "5-fuzzy-matched.csv"
OUT_REVIEW = SUPPLEMENT_DIR / "6-needs-review.csv"
DECISIONS_CSV = SUPPLEMENT_DIR / "7-decisions.csv"
DECISION_FIELDS = ["dblp_key", "core_title", "decision", "reason"]
DECISIONS = {"accept", "reject", "absent"}
VALID_RANKINGS = {"A*", "A", "B", "C"}
REVIEW_FIELDS = [
    "issue",
    "dblp_key",
    "dblp_acronym",
    "core_title",
    "acronym",
    "ranking",
    "detail",
    "papers",
    "years",
    "booktitles",
    "proceedings_titles",
]
OUT_FIELDS = [
    "dblp_key",
    "dblp_acronym",
    "core_title",
    "acronym",
    "ranking",
    "confidence",
    "reasoning",
]


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_core() -> dict[str, dict]:
    core = {}
    for r in read_csv(CORE_MASTER_CSV):
        if r["Ranking"].strip() in VALID_RANKINGS:
            core[r["Title"].strip()] = {
                "title": r["Title"].strip(),
                "acronym": r["Acronym"].strip(),
                "ranking": r["Ranking"].strip(),
            }
    return core


def load_decisions(core: dict, venues: dict) -> tuple[dict, dict, dict, list[str]]:
    accept: dict[tuple[str, str], str] = {}
    reject: dict[tuple[str, str], str] = {}
    absent: dict[str, str] = {}
    errors = []
    if not DECISIONS_CSV.exists():
        with open(DECISIONS_CSV, "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow(DECISION_FIELDS)
    seen = Counter()
    for i, r in enumerate(read_csv(DECISIONS_CSV), start=2):
        key, title = (r.get("dblp_key") or "").strip(), (r.get("core_title") or "").strip()
        decision, reason = (r.get("decision") or "").strip(), (r.get("reason") or "").strip()
        where = f"{DECISIONS_CSV.name}:{i}"
        if decision not in DECISIONS:
            errors.append(f"{where}: unknown decision {decision!r}")
            continue
        if title not in core:
            errors.append(f"{where}: core_title not in CORE A*–C: {title!r}")
            continue
        if not reason:
            errors.append(f"{where}: reason is required")
        seen[(key, title)] += 1
        if seen[(key, title)] > 1:
            errors.append(f"{where}: duplicate decision for {key or 'absent'} → {title}")
        if decision == "absent":
            if key:
                errors.append(f"{where}: 'absent' rows must have an empty dblp_key")
            absent[title] = reason
            continue
        if key not in venues:
            errors.append(f"{where}: {key} has no inproceedings in the DBLP index")
            continue
        (accept if decision == "accept" else reject)[(key, title)] = reason
    for title in absent:
        if any(t == title for _, t in accept):
            errors.append(f"CORE venue marked absent but also accepted: {title!r}")
    return accept, reject, absent, errors


def build_matcher() -> None:
    for path in (VENUES_CSV, CANDIDATES_CSV, CORE_MASTER_CSV):
        if not path.exists():
            sys.exit(f"Missing input: {path}  (run a-extract.py first)")
    core = load_core()
    venues = {r["dblp_key"]: r for r in read_csv(VENUES_CSV)}
    candidates = read_csv(CANDIDATES_CSV)
    accept, reject, absent, errors = load_decisions(core, venues)

    rows_by_key: dict[str, list[dict]] = defaultdict(list)
    candidate_pairs = set()
    for c in candidates:
        pair = (c["dblp_key"], c["title"])
        candidate_pairs.add(pair)
        evidence = f"score {c['score']}: {c['proceedings_title']}"
        if pair in accept:
            confidence, reasoning = "HIGH", f"Reviewed evidence match ({accept[pair]}); {evidence}"
        elif pair in reject:
            confidence, reasoning = "REJECTED", f"{reject[pair]}; {evidence}"
        else:
            confidence, reasoning = "UNREVIEWED", f"Needs decision in 7-decisions.csv; {evidence}"
        rows_by_key[c["dblp_key"]].append((c["title"], confidence, reasoning))
    for (key, title), reason in accept.items():
        if (key, title) not in candidate_pairs:
            rows_by_key[key].append((title, "HIGH", f"Reviewed manual match: {reason}"))

    output_rows = []
    for key in sorted(venues):
        acronym = venues[key]["dblp_acronym"]
        entries = rows_by_key.get(key, [])
        for title, confidence, reasoning in entries:
            c = core[title]
            output_rows.append(
                {
                    "dblp_key": key,
                    "dblp_acronym": acronym,
                    "core_title": title,
                    "acronym": c["acronym"],
                    "ranking": c["ranking"],
                    "confidence": confidence,
                    "reasoning": reasoning,
                }
            )
        if not any(conf in {"HIGH", "UNREVIEWED"} for _, conf, _ in entries):
            output_rows.append(
                {
                    "dblp_key": key,
                    "dblp_acronym": acronym,
                    "core_title": "NO_MATCH",
                    "acronym": "",
                    "ranking": "",
                    "confidence": "NONE",
                    "reasoning": "No CORE A*–C venue supported by DBLP evidence",
                }
            )

    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUT_FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)

    counts = Counter(r["confidence"] for r in output_rows)
    high_keys = Counter(r["dblp_key"] for r in output_rows if r["confidence"] == "HIGH")
    conflicts = [k for k, n in high_keys.items() if n > 1]
    covered = {r["core_title"] for r in output_rows if r["confidence"] == "HIGH"}
    uncovered = [t for t in core if t not in covered and t not in absent]
    stale_absent = [t for t in absent if t in covered]
    unreviewed = [r for r in output_rows if r["confidence"] == "UNREVIEWED"]

    review = [{"issue": "ERROR", "detail": e} for e in errors]
    review += [{"issue": "ERROR", "dblp_key": k, "detail": "accepted for more than one CORE venue"} for k in conflicts]
    review += [{"issue": "ERROR", "core_title": t, "detail": "marked absent but has an accepted key"} for t in stale_absent]
    review += [
        {
            "issue": "UNREVIEWED",
            "dblp_key": r["dblp_key"],
            "dblp_acronym": r["dblp_acronym"],
            "core_title": r["core_title"],
            "acronym": r["acronym"],
            "ranking": r["ranking"],
            "detail": r["reasoning"],
            "papers": venues[r["dblp_key"]]["papers"],
            "years": f"{venues[r['dblp_key']]['first_year']}–{venues[r['dblp_key']]['last_year']}",
            "booktitles": venues[r["dblp_key"]]["booktitles"],
            "proceedings_titles": venues[r["dblp_key"]]["proceedings_titles"],
        }
        for r in unreviewed
    ]
    review += [
        {
            "issue": "UNCOVERED",
            "core_title": t,
            "acronym": core[t]["acronym"],
            "ranking": core[t]["ranking"],
            "detail": "No accepted DBLP key and not marked absent: search 1-dblp-venues.csv",
        }
        for t in uncovered
    ]
    with open(OUT_REVIEW, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REVIEW_FIELDS, restval="")
        writer.writeheader()
        writer.writerows(review)

    print(f"Generated {len(output_rows)} rows in {OUT_CSV.name}")
    print(f"  HIGH (accepted):           {counts['HIGH']}")
    print(f"  REJECTED (reviewed):       {counts['REJECTED']}")
    print(f"  UNREVIEWED:                {counts['UNREVIEWED']}")
    print(f"  NONE (NO_MATCH):           {counts['NONE']}")
    print(f"  CORE venues covered:       {len(covered)} / {len(core)}  (+{len(absent)} documented absent)")
    print(f"  Items needing review:      {len(review)}  → {OUT_REVIEW.name}")
    for r in review[:30]:
        print(f"  ? {r['issue']} {r.get('dblp_key', '')} {r.get('core_title', '')} {r['detail'][:120]}")
    if len(review) > 30:
        print(f"  ... and {len(review) - 30} more in {OUT_REVIEW.name}")
    if review:
        print("\n  ✗ Review needed: resolve every row of 6-needs-review.csv in 7-decisions.csv, then re-run b-match.py")
        sys.exit(1)
    print("\n  ✓ All candidates reviewed and every CORE venue accounted for")


if __name__ == "__main__":
    build_matcher()
