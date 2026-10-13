#!/usr/bin/env python3
"""
Stage 3: Aggregate screening results and generate reports

This script:
1. Loads decisions from screen1 and screen2 passes
2. Reconciles disagreements (papers accepted by at least one screener)
3. Generates filtered CSV files by tier
4. Creates statistics and reports
"""

import csv
import json
import sys
from pathlib import Path
from collections import defaultdict, Counter

sys.path.insert(0, str(Path(__file__).parent))
from config import *


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
    # Index screen1 decisions
    decisions_by_id = {}
    
    for decision in screen1_decisions:
        decisions_by_id[decision['id']] = {
            'screen1': decision,
            'screen2': None,
            'reconciled': None
        }
    
    # Add screen2 if available
    if screen2_decisions:
        for decision in screen2_decisions:
            if decision['id'] in decisions_by_id:
                decisions_by_id[decision['id']]['screen2'] = decision
            else:
                # Paper only in screen2 (shouldn't happen, but handle it)
                decisions_by_id[decision['id']] = {
                    'screen1': None,
                    'screen2': decision,
                    'reconciled': None
                }
    
    # Reconcile each paper
    for paper_id, screens in decisions_by_id.items():
        s1 = screens['screen1']
        s2 = screens['screen2']
        
        if s2 is None:
            # Only screen1 available
            screens['reconciled'] = s1
            screens['reconciled']['reconciliation'] = 'screen1_only'
        else:
            # Both screens available - reconcile
            screens['reconciled'] = reconcile_single_paper(s1, s2)
    
    return decisions_by_id


def reconcile_single_paper(s1, s2):
    """
    Reconcile a single paper from two screenings.
    
    Priority: more inclusive (core > related > reject)
    """
    tier_priority = ['core', 'related', 'needs_manual', 'reject']
    
    # Determine final tier (most inclusive)
    s1_tier = s1['tier']
    s2_tier = s2['tier']
    
    if tier_priority.index(s1_tier) < tier_priority.index(s2_tier):
        final_tier = s1_tier
        source = 's1'
    else:
        final_tier = s2_tier
        source = 's2'
    
    # Merge roles (union)
    roles = list(set(s1.get('roles', []) + s2.get('roles', [])))
    
    # Use decision from more inclusive screener as base
    base = s1 if source == 's1' else s2
    
    reconciled = base.copy()
    reconciled['tier'] = final_tier
    reconciled['roles'] = roles
    reconciled['reconciliation'] = 'agreement' if s1_tier == s2_tier else f'disagreement_{source}_chosen'
    reconciled['screen1_tier'] = s1_tier
    reconciled['screen2_tier'] = s2_tier
    reconciled['screen1_confidence'] = s1.get('confidence', 0)
    reconciled['screen2_confidence'] = s2.get('confidence', 0)
    
    return reconciled


def generate_filtered_csv(decisions_by_id, output_path, tier_filter=None):
    """
    Generate filtered CSV file.
    
    tier_filter: list of tiers to include, or None for all
    """
    rows = []
    
    for paper_id, screens in decisions_by_id.items():
        decision = screens['reconciled']
        
        if tier_filter and decision['tier'] not in tier_filter:
            continue
        
        row = {
            'id': decision['id'],
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
            'llm_agent': decision.get('criteria', {}).get('llm_agent', ''),
            'offensive_security': decision.get('criteria', {}).get('offensive_security', ''),
            'black_box': decision.get('criteria', {}).get('black_box', ''),
            'long_horizon': decision.get('criteria', {}).get('long_horizon', ''),
            'evidence': decision.get('evidence', ''),
            'reason': decision.get('reason', ''),
            'confidence': decision.get('confidence', 0),
            'reconciliation': decision.get('reconciliation', ''),
        }
        rows.append(row)
    
    # Sort by tier priority, then by confidence (descending)
    tier_order = {'core': 0, 'related': 1, 'needs_manual': 2, 'reject': 3}
    rows.sort(key=lambda r: (tier_order.get(r['tier'], 99), -r['confidence']))
    
    # Write CSV
    if rows:
        with open(output_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        
        print(f"  ✓ {output_path.name}: {len(rows)} papers")
    else:
        print(f"  ✓ {output_path.name}: 0 papers (not created)")
    
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
            
            # Collect papers by role
            paper_info = {
                'id': decision['id'],
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


def main():
    if len(sys.argv) < 2:
        print("Usage: python stage3_aggregate.py <fuzzy_json_stem>")
        print("\nExample:")
        print("  python stage3_aggregate.py work/q4-2026_fuzzy")
        print("\nThis will load:")
        print("  work/q4-2026_fuzzy_screen1_decisions.jsonl")
        print("  work/q4-2026_fuzzy_screen2_decisions.jsonl (optional)")
        sys.exit(1)
    
    fuzzy_stem = sys.argv[1]
    if fuzzy_stem.endswith('.json'):
        fuzzy_stem = fuzzy_stem[:-5]
    
    screen1_file = Path(f"{fuzzy_stem}_screen1_decisions.jsonl")
    screen2_file = Path(f"{fuzzy_stem}_screen2_decisions.jsonl")
    
    if not screen1_file.exists():
        print(f"ERROR: Screen1 decisions not found: {screen1_file}")
        sys.exit(1)
    
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║          Stage 3: Aggregate & Report                                 ║")
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
        print(f"  (no screen2 file found - using screen1 only)")
    
    print()
    print("Reconciling decisions...")
    decisions_by_id = reconcile_screens(screen1_decisions, screen2_decisions)
    print(f"  ✓ {len(decisions_by_id)} papers reconciled")
    
    # Generate output CSVs
    print()
    print("Generating filtered CSV files...")
    
    output_base = OUTPUT_DIR / Path(screen1_file.stem.replace('_screen1_decisions', ''))
    
    generate_filtered_csv(decisions_by_id, output_base.with_name(output_base.name + '_all.csv'))
    generate_filtered_csv(decisions_by_id, output_base.with_name(output_base.name + '_core.csv'), ['core'])
    generate_filtered_csv(decisions_by_id, output_base.with_name(output_base.name + '_related.csv'), ['related'])
    generate_filtered_csv(decisions_by_id, output_base.with_name(output_base.name + '_baseline.csv'), ['core'])  # Will be filtered by role
    
    # Generate statistics
    stats = generate_statistics(decisions_by_id)
    
    # Save stats to JSON
    stats_file = output_base.with_name(output_base.name + '_stats.json')
    with open(stats_file, 'w', encoding='utf-8') as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    print(f"  ✓ {stats_file.name}: statistics saved")
    
    # Print statistics
    print_statistics(stats)
    
    print("━" * 70)
    print("✓ Aggregation complete!")
    print("━" * 70)
    print()


if __name__ == '__main__':
    main()
