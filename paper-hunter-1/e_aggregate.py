#!/usr/bin/env python3
"""
e_aggregate.py — Aggregate & Report

Loads decisions from screen1 (and optional screen2) passes,
reconciles disagreements, and generates filtered CSV files by tier.
"""
import csv
import json
import sys
from pathlib import Path
from collections import defaultdict, Counter

sys.path.insert(0, str(Path(__file__).parent))
from b_config import *


def load_decisions(decisions_jsonl):
    """Load decisions from JSONL file."""
    decisions = []
    with open(decisions_jsonl, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                decisions.append(json.loads(line))
    return decisions


def reconcile_screens(screen1_decisions, screen2_decisions=None):
    """
    Reconcile two screening passes.
    Rules:
    - If a paper is 'core' or 'related' in either pass, keep it
    - If both reject, it's rejected
    - If disagreement on roles/criteria, take the more inclusive one
    Returns: dict mapping paper_id -> reconciled_decision
    """
    decisions_by_id = {}
    for decision in screen1_decisions:
        decisions_by_id[decision['id']] = {
            'screen1': decision,
            'screen2': None,
            'reconciled': None
        }

    if screen2_decisions:
        for decision in screen2_decisions:
            if decision['id'] in decisions_by_id:
                decisions_by_id[decision['id']]['screen2'] = decision
            else:
                decisions_by_id[decision['id']] = {
                    'screen1': None,
                    'screen2': decision,
                    'reconciled': None
                }

    for paper_id, screens in decisions_by_id.items():
        s1 = screens['screen1']
        s2 = screens['screen2']
        if s2 is None:
            screens['reconciled'] = dict(s1)
            screens['reconciled']['reconciliation'] = 'screen1_only'
        else:
            screens['reconciled'] = reconcile_single_paper(s1, s2)

    return decisions_by_id


def reconcile_single_paper(s1, s2):
    """
    Reconcile a single paper from two screenings.
    Priority: more inclusive (core > related > needs_manual > reject)
    """
    tier_priority = ['core', 'related', 'needs_manual', 'reject']

    s1_tier = s1['tier']
    s2_tier = s2['tier']

    if tier_priority.index(s1_tier) <= tier_priority.index(s2_tier):
        final_tier = s1_tier
        source = 's1'
    else:
        final_tier = s2_tier
        source = 's2'

    # Merge roles (union of both screens)
    roles = list(set(s1.get('roles', []) + s2.get('roles', [])))

    base = s1 if source == 's1' else s2
    reconciled = dict(base)
    reconciled['tier'] = final_tier
    reconciled['roles'] = roles
    reconciled['reconciliation'] = 'agreement' if s1_tier == s2_tier else f'disagreement_{source}_chosen'
    reconciled['screen1_tier'] = s1_tier
    reconciled['screen2_tier'] = s2_tier
    reconciled['screen1_confidence'] = s1.get('confidence', 0)
    reconciled['screen2_confidence'] = s2.get('confidence', 0)
    return reconciled


def generate_filtered_csv(decisions_by_id, output_path, tier_filter=None, role_filter=None):
    """
    Generate filtered CSV file.
    tier_filter: list of tiers to include, or None for all
    role_filter: list of roles — include paper if it has ANY of these roles
    """
    rows = []
    for paper_id, screens in decisions_by_id.items():
        decision = screens['reconciled']

        if tier_filter and decision['tier'] not in tier_filter:
            continue

        if role_filter:
            paper_roles = decision.get('roles', [])
            if not any(r in paper_roles for r in role_filter):
                continue

        criteria = decision.get('criteria', {})
        row = {
            'id': decision.get('id', ''),
            'dblp_key': decision.get('dblp_key', ''),
            'title': decision.get('title', ''),
            'authors': decision.get('authors', ''),
            'year': decision.get('year', ''),
            'venue': decision.get('venue', ''),
            'doi': decision.get('doi', ''),
            'url': decision.get('url', ''),
            'tier': decision['tier'],
            'roles': ', '.join(decision.get('roles', [])),
            'primary_contribution': decision.get('primary_contribution', ''),
            'publication_status': decision.get('publication_status', ''),
            'llm_agent': criteria.get('llm_agent', ''),
            'offensive_security': criteria.get('offensive_security', ''),
            'black_box': criteria.get('black_box', ''),
            'long_horizon': criteria.get('long_horizon', ''),
            'evidence': decision.get('evidence', ''),
            'reason': decision.get('reason', ''),
            'abstract_source': decision.get('abstract_source', ''),
            'doi_verified': decision.get('doi_verified', ''),
            'confidence': decision.get('confidence', 0),
            'reconciliation': decision.get('reconciliation', ''),
        }
        rows.append(row)

    # Sort: tier priority first, then confidence descending
    tier_order = {'core': 0, 'related': 1, 'needs_manual': 2, 'reject': 3}
    rows.sort(key=lambda r: (tier_order.get(r['tier'], 99), -float(r.get('confidence', 0) or 0)))

    if rows:
        with open(output_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print(f"  ✓ {output_path.name}: {len(rows)} papers")
    else:
        print(f"  ✓ {output_path.name}: 0 papers (not written)")

    return len(rows)


def generate_statistics(decisions_by_id):
    """Generate statistics report."""
    stats = {
        'total': len(decisions_by_id),
        'by_tier': Counter(),
        'by_role': Counter(),
        'by_contribution': Counter(),
        'by_reconciliation': Counter(),
        'baseline_papers': [],
        'survey_papers': [],
        'benchmark_papers': [],
        'evaluation_papers': [],
    }

    for paper_id, screens in decisions_by_id.items():
        decision = screens['reconciled']
        stats['by_tier'][decision['tier']] += 1
        stats['by_contribution'][decision.get('primary_contribution', 'unknown')] += 1
        stats['by_reconciliation'][decision.get('reconciliation', 'unknown')] += 1

        for role in decision.get('roles', []):
            stats['by_role'][role] += 1
            paper_info = {
                'id': decision.get('id', ''),
                'title': decision.get('title', ''),
                'authors': decision.get('authors', ''),
                'venue': decision.get('venue', ''),
                'year': decision.get('year', ''),
                'confidence': decision.get('confidence', 0)
            }
            if role == 'baseline':
                stats['baseline_papers'].append(paper_info)
            elif role == 'survey':
                stats['survey_papers'].append(paper_info)
            elif role == 'benchmark':
                stats['benchmark_papers'].append(paper_info)
            elif role == 'evaluation':
                stats['evaluation_papers'].append(paper_info)

    return stats


def print_statistics(stats):
    """Print statistics to console."""
    print()
    print("━" * 70)
    print("STATISTICS")
    print("━" * 70)
    print(f"Total papers processed:  {stats['total']}")
    print()

    print("By Tier:")
    for tier in ['core', 'related', 'needs_manual', 'reject']:
        count = stats['by_tier'][tier]
        pct = count / stats['total'] * 100 if stats['total'] > 0 else 0
        print(f"  {tier:20s} {count:>6}  ({pct:5.1f}%)")
    print()

    print("By Role:")
    for role, count in sorted(stats['by_role'].items(), key=lambda x: -x[1]):
        print(f"  {role:20s} {count:>6}")
    print()

    print("By Primary Contribution:")
    for contrib, count in sorted(stats['by_contribution'].items(), key=lambda x: -x[1]):
        print(f"  {contrib:20s} {count:>6}")
    print()

    print("Reconciliation:")
    for recon, count in sorted(stats['by_reconciliation'].items(), key=lambda x: -x[1]):
        print(f"  {recon:20s} {count:>6}")
    print()

    print(f"Baseline papers:    {len(stats['baseline_papers'])}")
    print(f"Survey papers:      {len(stats['survey_papers'])}")
    print(f"Benchmark papers:   {len(stats['benchmark_papers'])}")
    print(f"Evaluation papers:  {len(stats['evaluation_papers'])}")
    print("━" * 70)
    print()

    if stats['baseline_papers']:
        print("🤖 BASELINE papers found:")
        for p in stats['baseline_papers']:
            print(f"  [{p['year']}] {p['title'][:70]}...")
        print()

    if stats['survey_papers']:
        print("📊 SURVEY papers found:")
        for p in stats['survey_papers']:
            print(f"  [{p['year']}] {p['title'][:70]}...")
        print()

    if stats['benchmark_papers']:
        print("🏆 BENCHMARK papers found:")
        for p in stats['benchmark_papers']:
            print(f"  [{p['year']}] {p['title'][:70]}...")
        print()

    if stats['evaluation_papers']:
        print("🔬 EVALUATION papers found:")
        for p in stats['evaluation_papers']:
            print(f"  [{p['year']}] {p['title'][:70]}...")
        print()


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 e_aggregate.py <prepared_json_or_stem>")
        print("\nExamples:")
        print("  python3 e_aggregate.py work/q4-2026-prepared.json")
        print("  python3 e_aggregate.py work/q4-2026-prepared")
        print("\nThis will load:")
        print("  work/q4-2026-prepared-screen1-decisions.jsonl")
        print("  work/q4-2026-prepared-screen2-decisions.jsonl  (optional)")
        sys.exit(1)

    prepared_stem = sys.argv[1]
    if prepared_stem.endswith('.json'):
        prepared_stem = prepared_stem[:-5]

    screen1_file = Path(f"{prepared_stem}-screen1-decisions.jsonl")
    screen2_file = Path(f"{prepared_stem}-screen2-decisions.jsonl")

    if not screen1_file.exists():
        print(f"ERROR: Screen1 decisions not found: {screen1_file}")
        sys.exit(1)

    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║          Aggregate & Report                                          ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print()

    print(f"Loading: {screen1_file}")
    screen1_decisions = load_decisions(screen1_file)
    print(f"  ✓ {len(screen1_decisions)} decisions from screen1")

    screen2_decisions = None
    if screen2_file.exists():
        print(f"Loading: {screen2_file}")
        screen2_decisions = load_decisions(screen2_file)
        print(f"  ✓ {len(screen2_decisions)} decisions from screen2")
    else:
        print(f"  (no screen2 file found — using screen1 only)")

    print()
    print("Reconciling decisions...")
    decisions_by_id = reconcile_screens(screen1_decisions, screen2_decisions)
    print(f"  ✓ {len(decisions_by_id)} papers reconciled")

    print()
    print("Generating filtered CSV files...")

    # Derive output base name from stem
    stem_name = Path(prepared_stem).name
    output_base = OUTPUT_DIR / stem_name

    generate_filtered_csv(decisions_by_id, output_base.with_name(output_base.name + '-all.csv'))
    generate_filtered_csv(decisions_by_id, output_base.with_name(output_base.name + '-core.csv'),
                          tier_filter=['core'])
    generate_filtered_csv(decisions_by_id, output_base.with_name(output_base.name + '-related.csv'),
                          tier_filter=['related'])
    generate_filtered_csv(decisions_by_id, output_base.with_name(output_base.name + '-needs_manual.csv'),
                          tier_filter=['needs_manual'])
    generate_filtered_csv(decisions_by_id, output_base.with_name(output_base.name + '-baseline.csv'),
                          role_filter=['baseline'])
    generate_filtered_csv(decisions_by_id, output_base.with_name(output_base.name + '-survey.csv'),
                          role_filter=['survey'])
    generate_filtered_csv(decisions_by_id, output_base.with_name(output_base.name + '-benchmark.csv'),
                          role_filter=['benchmark'])
    generate_filtered_csv(decisions_by_id, output_base.with_name(output_base.name + '-evaluation.csv'),
                          role_filter=['evaluation'])

    stats = generate_statistics(decisions_by_id)
    stats_file = output_base.with_name(output_base.name + '-stats.json')

    # Make Counter serializable
    serializable_stats = dict(stats)
    serializable_stats['by_tier'] = dict(stats['by_tier'])
    serializable_stats['by_role'] = dict(stats['by_role'])
    serializable_stats['by_contribution'] = dict(stats['by_contribution'])
    serializable_stats['by_reconciliation'] = dict(stats['by_reconciliation'])

    with open(stats_file, 'w', encoding='utf-8') as f:
        json.dump(serializable_stats, f, indent=2, ensure_ascii=False)
    print(f"  ✓ {stats_file.name}: statistics saved")

    print_statistics(stats)

    print("━" * 70)
    print("✓ Aggregation complete!")
    print(f"✓ Output files in: {OUTPUT_DIR}/")
    print("━" * 70)
    print()


if __name__ == '__main__':
    main()
