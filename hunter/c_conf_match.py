#!/usr/bin/env python3
"""
Conference Fuzzy Matcher - Match remaining DBLP conferences to CORE by name/acronym similarity.

Uses:
  - Acronym expansion dictionary
  - Title normalization
  - Curated mappings for known conference name changes
"""

import csv
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SUPPLEMENT_DIR = REPO_ROOT / "supplement"

DBLP_CSV = SUPPLEMENT_DIR / "2-conf-todo.csv"
CORE_CSV = SUPPLEMENT_DIR / "3-core-pool.csv"
OUT_CSV = SUPPLEMENT_DIR / "4-conf-matched.csv"

STOPWORDS = {
    'of', 'the', 'and', 'in', 'on', 'for', 'to', 'a', 'an', '&', 'at', 'by', 'with',
}

# Known conference name changes and variations
EXPLICIT_MAP = {
    'conf/nips': ('Advances in Neural Information Processing Systems (was NIPS)', 'HIGH', 'Conference renamed to NeurIPS'),
    'conf/emnlp': ('Empirical Methods in Natural Language Processing', 'HIGH', 'Official acronym match'),
    'conf/naacl': ('North American Chapter of the Association for Computational Linguistics', 'HIGH', 'Official acronym match'),
    'conf/coling': ('International Conference on Computational Linguistics', 'HIGH', 'Official acronym match'),
    'conf/eccv': ('European Conference on Computer Vision', 'HIGH', 'Official acronym match'),
    'conf/iccv': ('International Conference on Computer Vision', 'HIGH', 'Official acronym match'),
    'conf/cvpr': ('Computer Vision and Pattern Recognition', 'HIGH', 'Official acronym match'),
    'conf/icml': ('International Conference on Machine Learning', 'HIGH', 'Official acronym match'),
    'conf/iclr': ('International Conference on Learning Representations', 'HIGH', 'Official acronym match'),
    'conf/acl': ('Association for Computational Linguistics', 'HIGH', 'Official acronym match'),
    'conf/sigir': ('ACM International Conference on Research and Development in Information Retrieval', 'HIGH', 'Official acronym match'),
    'conf/kdd': ('ACM International Conference on Knowledge Discovery and Data Mining', 'HIGH', 'Official acronym match'),
    'conf/www': ('International World Wide Web Conference', 'HIGH', 'Official acronym match'),
    'conf/sigmod': ('ACM Special Interest Group on Management of Data Conference', 'HIGH', 'Official acronym match'),
    'conf/vldb': ('International Conference on Very Large Data Bases', 'HIGH', 'Official acronym match'),
    'conf/icde': ('IEEE International Conference on Data Engineering', 'HIGH', 'Official acronym match'),
    'conf/sosp': ('ACM SIGOPS Symposium on Operating Systems Principles', 'HIGH', 'Official acronym match'),
    'conf/osdi': ('USENIX Symposium on Operating Systems Design and Implementation', 'HIGH', 'Official acronym match'),
    'conf/nsdi': ('USENIX Symposium on Networked Systems Design and Implementation', 'HIGH', 'Official acronym match'),
    'conf/isca': ('ACM International Symposium on Computer Architecture', 'HIGH', 'Official acronym match'),
    'conf/asplos': ('Architectural Support for Programming Languages and Operating Systems', 'HIGH', 'Official acronym match'),
    'conf/micro': ('IEEE/ACM International Symposium on Microarchitecture', 'HIGH', 'Official acronym match'),
    'conf/pldi': ('ACM-SIGPLAN Conference on Programming Language Design and Implementation', 'HIGH', 'Official acronym match'),
    'conf/popl': ('ACM-SIGACT Symposium on Principles of Programming Languages', 'HIGH', 'Official acronym match'),
    'conf/oopsla': ('Conference on Object-Oriented Programming Systems, Languages, and Applications', 'HIGH', 'Official acronym match'),
    'conf/icse': ('International Conference on Software Engineering', 'HIGH', 'Official acronym match'),
    'conf/fse': ('ACM International Conference on the Foundations of Software Engineering (was ESEC/FSE, changed 2024; duplicate previously listed as ESEC, removed from DB)', 'HIGH', 'Official acronym match'),
    'conf/ase': ('Automated Software Engineering Conference', 'HIGH', 'Official acronym match'),
    'conf/ccs': ('ACM Conference on Computer and Communications Security', 'HIGH', 'Official acronym match'),
    'conf/sp': ('IEEE Symposium on Security and Privacy', 'HIGH', 'Official acronym match'),
    'conf/usenixsec': ('USENIX Security Symposium', 'HIGH', 'Official acronym match'),
    'conf/ndss': ('Network and Distributed System Security Symposium', 'HIGH', 'Official acronym match'),
    'conf/crypto': ('Advances in Cryptology', 'HIGH', 'Official acronym match'),
    'conf/eurocrypt': ('European Cryptology Conference', 'HIGH', 'Official acronym match'),
}


def strip_accents(s: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')


def clean_str(s: str) -> str:
    s = strip_accents(s).lower()
    s = re.sub(r'[^\w\s]', ' ', s)
    return ' '.join(s.split())


def tokenize(s: str) -> list[str]:
    s = strip_accents(s).lower()
    tokens = re.findall(r'[a-z0-9]+', s)
    return [t for t in tokens if t not in STOPWORDS]


def token_match(dt: str, ct: str) -> bool:
    """Check if DBLP token matches CORE token (with abbreviation handling)."""
    if dt == ct:
        return True
    # Partial match for long words
    if len(dt) >= 4 and len(ct) >= 4 and (dt.startswith(ct) or ct.startswith(dt)):
        return True
    return False


def tokens_match(d_toks: list[str], c_toks: list[str]) -> bool:
    """Check if token sequences match allowing for some flexibility."""
    if len(d_toks) != len(c_toks):
        return False
    return all(token_match(dt, ct) for dt, ct in zip(d_toks, c_toks))


def title_similarity(dblp_title: str, core_title: str) -> float:
    """Calculate similarity between titles (0.0 to 1.0)."""
    d_clean = clean_str(dblp_title)
    c_clean = clean_str(core_title)
    
    # Exact match
    if d_clean == c_clean:
        return 1.0
    
    # Token overlap
    d_tokens = set(tokenize(dblp_title))
    c_tokens = set(tokenize(core_title))
    
    if not d_tokens or not c_tokens:
        return 0.0
    
    overlap = len(d_tokens & c_tokens)
    if overlap == 0:
        return 0.0
    
    # Jaccard similarity
    jaccard = overlap / len(d_tokens | c_tokens)
    
    # Containment (for abbreviated titles)
    containment = overlap / min(len(d_tokens), len(c_tokens))
    
    return max(jaccard, containment * 0.8)


def build_matcher():
    with open(DBLP_CSV, encoding='utf-8') as f:
        dblp_rows = list(csv.DictReader(f))
    with open(CORE_CSV, encoding='utf-8') as f:
        core_rows = list(csv.DictReader(f))

    core_by_clean = {clean_str(r['title']): r for r in core_rows}
    core_by_acronym = {r['acronym'].lower(): r for r in core_rows}

    matched_dblp = {}
    used_core_titles = set()

    # Pass 1: Explicit curated mappings
    for d in dblp_rows:
        key = d['dblp_key']
        if key in EXPLICIT_MAP:
            target_title, conf, reason = EXPLICIT_MAP[key]
            for c in core_rows:
                if clean_str(c['title']) == clean_str(target_title):
                    if c['title'] not in used_core_titles:
                        matched_dblp[key] = (c, conf, reason)
                        used_core_titles.add(c['title'])
                    break

    # Pass 2: Exact normalized title match
    for d in dblp_rows:
        key = d['dblp_key']
        if key in matched_dblp:
            continue
        
        # Try matching by DBLP acronym to CORE acronym
        dblp_acronym_lower = d['dblp_acronym'].lower()
        if dblp_acronym_lower in core_by_acronym:
            c = core_by_acronym[dblp_acronym_lower]
            if c['title'] not in used_core_titles:
                matched_dblp[key] = (c, 'HIGH', f"Acronym match: {d['dblp_acronym']}")
                used_core_titles.add(c['title'])

    # Pass 3: Title similarity matching
    threshold = 0.7
    for d in dblp_rows:
        key = d['dblp_key']
        if key in matched_dblp:
            continue
        
        best_score = 0.0
        best_core = None
        
        for c in core_rows:
            if c['title'] in used_core_titles:
                continue
            
            score = title_similarity(d['dblp_acronym'], c['title'])
            if score > best_score:
                best_score = score
                best_core = c
        
        if best_score >= threshold and best_core is not None:
            matched_dblp[key] = (best_core, 'HIGH', f"Title similarity: {best_score:.2f}")
            used_core_titles.add(best_core['title'])

    # Prepare output
    output_rows = []
    high_count = 0
    none_count = 0

    for d in dblp_rows:
        key = d['dblp_key']
        acronym = d['dblp_acronym']
        
        if key in matched_dblp:
            c, conf, reason = matched_dblp[key]
            high_count += 1
            output_rows.append({
                'dblp_key': key,
                'dblp_acronym': acronym,
                'core_title': c['title'],
                'acronym': c['acronym'],
                'ranking': c['ranking'],
                'confidence': conf,
                'reasoning': reason[:100],
            })
        else:
            none_count += 1
            output_rows.append({
                'dblp_key': key,
                'dblp_acronym': acronym,
                'core_title': 'NO_MATCH',
                'acronym': '',
                'ranking': '',
                'confidence': 'NONE',
                'reasoning': 'No matching conference found in CORE pool',
            })

    # Write output
    fieldnames = [
        'dblp_key', 'dblp_acronym', 'core_title', 'acronym', 'ranking',
        'confidence', 'reasoning'
    ]
    with open(OUT_CSV, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"Generated {len(output_rows)} total rows in {OUT_CSV}")
    print(f"  HIGH confidence matches:   {high_count}")
    print(f"  NONE (NO_MATCH):           {none_count}")
    print(f"  Total matched conferences: {high_count}")
    print(f"  Distinct CORE matched:     {len(used_core_titles)}")


if __name__ == '__main__':
    build_matcher()
