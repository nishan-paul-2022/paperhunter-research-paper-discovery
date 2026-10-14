#!/usr/bin/env python3
import json
import sys
from pathlib import Path
from collections import defaultdict, Counter
sys.path.insert(0, str(Path(__file__).parent))
from c_config import *
def load_stats(stats_file):
    with open(stats_file, 'r', encoding='utf-8') as f:
        return json.load(f)
def main():
    work_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else OUTPUT_DIR
    if not work_dir.exists():
        print(f"ERROR: Directory not found: {work_dir}")
        sys.exit(1)
    stats_files = list(work_dir.glob('*_stats.json'))
    if not stats_files:
        print(f"No stats files found in {work_dir}")
        print("Run stage3 first to generate statistics.")
        sys.exit(0)
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║          Summary: All Filtered Papers                                ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print()
    print(f"Found {len(stats_files)} processed CSV files")
    print()
    total_stats = {
        'total_papers': 0,
        'by_tier': Counter(),
        'by_role': Counter(),
        'by_contribution': Counter(),
        'baseline_count': 0,
        'survey_count': 0,
        'benchmark_count': 0,
        'evaluation_count': 0,
        'files_processed': 0
    }
    file_summaries = []
    for stats_file in sorted(stats_files):
        stats = load_stats(stats_file)
        total_stats['total_papers'] += stats['total']
        total_stats['by_tier'].update(stats['by_tier'])
        total_stats['by_role'].update(stats['by_role'])
        total_stats['by_contribution'].update(stats['by_contribution'])
        total_stats['baseline_count'] += len(stats.get('baseline_papers', []))
        total_stats['survey_count'] += len(stats.get('survey_papers', []))
        total_stats['benchmark_count'] += len(stats.get('benchmark_papers', []))
        total_stats['evaluation_count'] += len(stats.get('evaluation_papers', []))
        total_stats['files_processed'] += 1
        file_summaries.append({
            'file': stats_file.stem.replace('_stats', ''),
            'total': stats['total'],
            'core': stats['by_tier'].get('core', 0),
            'related': stats['by_tier'].get('related', 0),
            'baseline': len(stats.get('baseline_papers', []))
        })
    print("━" * 70)
    print("OVERALL STATISTICS")
    print("━" * 70)
    print(f"Total papers processed:  {total_stats['total_papers']:>6}")
    print()
    print("By Tier:")
    for tier in ['core', 'related', 'needs_manual', 'reject']:
        count = total_stats['by_tier'][tier]
        pct = count / total_stats['total_papers'] * 100 if total_stats['total_papers'] > 0 else 0
        print(f"  {tier:20s} {count:>6}  ({pct:5.1f}%)")
    print()
    print("By Role:")
    for role, count in sorted(total_stats['by_role'].items(), key=lambda x: -x[1]):
        print(f"  {role:20s} {count:>6}")
    print()
    print(f"Baseline papers:    {total_stats['baseline_count']:>6}")
    print(f"Survey papers:      {total_stats['survey_count']:>6}")
    print(f"Benchmark papers:   {total_stats['benchmark_count']:>6}")
    print(f"Evaluation papers:  {total_stats['evaluation_count']:>6}")
    print("━" * 70)
    print()
    print("━" * 70)
    print("PER-FILE BREAKDOWN")
    print("━" * 70)
    print(f"{'File':<30} {'Total':>8} {'Core':>8} {'Related':>8} {'Baseline':>8}")
    print("─" * 70)
    for summary in sorted(file_summaries, key=lambda x: -x['core']):
        print(f"{summary['file']:<30} {summary['total']:>8} {summary['core']:>8} {summary['related']:>8} {summary['baseline']:>8}")
    print("━" * 70)
    print()
    if total_stats['baseline_count'] > 0:
        print("✓ Found baseline papers! Review these carefully.")
        print()
    if total_stats['by_tier']['needs_manual'] > 0:
        print(f"⚠  {total_stats['by_tier']['needs_manual']} papers need manual review.")
        print()
    print("Output files:")
    print(f"  Core papers:     {work_dir}/*_core.csv")
    print(f"  Related papers:  {work_dir}/*_related.csv")
    print(f"  All papers:      {work_dir}/*_all.csv")
    print()
if __name__ == '__main__':
    main()
