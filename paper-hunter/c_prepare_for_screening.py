#!/usr/bin/env python3
"""
Prepare CSV for AI screening (NO FILTERING - all papers processed)

This script:
1. Loads ALL papers from input CSV
2. Assigns unique IDs
3. Optionally adds fuzzy scores for prioritization (NOT filtering)
4. Outputs JSON ready for AI screening

IMPORTANT: No papers are discarded. Fuzzy matching only helps with ordering.
"""

import csv
import json
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
from b_config import *


def normalize_text(text):
    """Normalize text for matching."""
    if not text:
        return ""
    return text.lower().strip()


def contains_keyword(text, keywords):
    """Check if text contains any keyword from list."""
    text = normalize_text(text)
    for keyword in keywords:
        if keyword in text:
            return True
    return False


def compute_fuzzy_score(paper):
    """
    Compute fuzzy relevance score (0-100) for prioritization.
    
    Higher score = more likely relevant, process first.
    But ALL papers will be processed eventually.
    
    Returns: (score: int, matched_categories: list)
    """
    title = paper.get('title', '')
    
    combined_text = title  # Only title available at this stage
    
    # Score components
    score = 0
    matched_categories = []
    
    # Check each category (each match adds points)
    for category, keywords in KEYWORDS.items():
        if contains_keyword(combined_text, keywords):
            matched_categories.append(category)
            
            # Weight by importance
            if category in ["ai_agent", "offensive_security"]:
                score += 30  # Critical categories
            elif category in ["ctf", "testing_approach"]:
                score += 20  # Important categories
            else:
                score += 10  # Supporting categories
    
    # Penalty for exclusion keywords (but don't reject)
    for keyword in EXCLUSION_KEYWORDS:
        if keyword in normalize_text(combined_text):
            score -= 20  # Lower priority, but still process
            matched_categories.append('exclusion_match')
    
    # Ensure score is in 0-100 range
    score = max(0, min(100, score))
    
    return score, matched_categories


def process_csv(input_csv_path, prioritize=True):
    """
    Process CSV and prepare ALL papers for screening.
    
    Args:
        input_csv_path: Path to input CSV
        prioritize: If True, compute fuzzy scores for ordering
    
    Returns: (all_papers, stats)
    """
    all_papers = []
    stats = {
        'total': 0,
        'in_date_range': 0,
        'out_of_date_range': 0,
        'score_distribution': defaultdict(int),
        'categories': defaultdict(int)
    }
    
    with open(input_csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        
        for row in reader:
            stats['total'] += 1
            
            # Check year (still filter by date range)
            try:
                year = int(row.get('year', 0))
                if year < MIN_YEAR or year > MAX_YEAR:
                    stats['out_of_date_range'] += 1
                    continue  # Only date filtering
            except (ValueError, TypeError):
                stats['out_of_date_range'] += 1
                continue
            
            stats['in_date_range'] += 1
            
            # Create paper dict with unique ID
            paper = {
                'id': f"{Path(input_csv_path).stem}_{stats['in_date_range']:04d}",
                'dblp_key': row.get('dblp_key', ''),
                'title': row.get('title', ''),
                'authors': row.get('authors', ''),
                'year': row.get('year', ''),
                'venue': row.get('venue', ''),
                'doi': row.get('ee', ''),
                'url': row.get('url', ''),
            }
            
            # Compute fuzzy score for prioritization (optional)
            if prioritize:
                score, categories = compute_fuzzy_score(paper)
                paper['fuzzy_score'] = score
                paper['fuzzy_categories'] = categories
                
                # Track statistics
                score_bucket = (score // 10) * 10
                stats['score_distribution'][score_bucket] += 1
                
                for cat in categories:
                    stats['categories'][cat] += 1
            else:
                paper['fuzzy_score'] = 50  # Neutral score
                paper['fuzzy_categories'] = []
            
            all_papers.append(paper)
    
    # Sort by fuzzy score (highest first) for prioritization
    if prioritize:
        all_papers.sort(key=lambda p: p['fuzzy_score'], reverse=True)
    
    return all_papers, stats


def main():
    if len(sys.argv) < 2:
        print("Usage: python c_prepare_for_screening.py <input_csv> [--no-prioritize]")
        print("\nExample:")
        print("  python c_prepare_for_screening.py ../paper-list/q4-2026.csv")
        print("  python c_prepare_for_screening.py ../paper-list/q4-2026.csv --no-prioritize")
        print("\nNote: NO papers are filtered out (except date range).")
        print("      Fuzzy scores only help with processing order.")
        sys.exit(1)
    
    input_csv = Path(sys.argv[1])
    prioritize = '--no-prioritize' not in sys.argv
    
    if not input_csv.exists():
        print(f"ERROR: File not found: {input_csv}")
        sys.exit(1)
    
    # Determine output path
    output_json = WORK_DIR / f"{input_csv.stem}_prepared.json"
    
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║          Prepare Papers for AI Screening                             ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print()
    print(f"Input:  {input_csv}")
    print(f"Output: {output_json}")
    print()
    print(f"Research topic: {RESEARCH_TOPIC}")
    print(f"Year range: {MIN_YEAR}-{MAX_YEAR}")
    print(f"Prioritization: {'Enabled' if prioritize else 'Disabled'}")
    print()
    print("⚠️  IMPORTANT: All papers will be processed (no filtering)")
    if prioritize:
        print("   Fuzzy scores only determine processing order (high score first)")
    print()
    
    # Process
    all_papers, stats = process_csv(input_csv, prioritize)
    
    # Save results
    output_json.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump({
            'source_csv': str(input_csv),
            'timestamp': __import__('datetime').datetime.now().isoformat(),
            'prioritization_enabled': prioritize,
            'stats': dict(stats),
            'papers': all_papers
        }, f, indent=2, ensure_ascii=False)
    
    # Print statistics
    print("━" * 70)
    print("STATISTICS")
    print("━" * 70)
    print(f"Total papers in CSV:      {stats['total']:>6}")
    print(f"In date range:            {stats['in_date_range']:>6}  ({stats['in_date_range']/stats['total']*100:.1f}%)")
    print(f"Out of date range:        {stats['out_of_date_range']:>6}  ({stats['out_of_date_range']/stats['total']*100:.1f}%)")
    print()
    
    if prioritize and stats['in_date_range'] > 0:
        print("Fuzzy Score Distribution (for prioritization only):")
        for score_bucket in sorted(stats['score_distribution'].keys(), reverse=True):
            count = stats['score_distribution'][score_bucket]
            pct = count / stats['in_date_range'] * 100
            bar = '█' * int(pct / 2)
            print(f"  {score_bucket:3d}-{score_bucket+9:3d}: {count:>5} {bar}")
        
        print()
        print("Category matches (indicates potential relevance):")
        for category, count in sorted(stats['categories'].items(), key=lambda x: -x[1])[:10]:
            print(f"  {category:25s} {count:>6}")
        
        print()
        print("Note: High-score papers will be processed first, but ALL papers")
        print("      will eventually be screened. No papers are discarded.")
    
    print()
    print("━" * 70)
    print(f"✓ All {stats['in_date_range']} papers prepared for screening")
    print(f"✓ Saved to: {output_json}")
    print("━" * 70)
    print()
    print("Next step:")
    print(f"  python stage2_batch_manager.py next {output_json} --pass screen1")
    print()
    print("Or use the master runner:")
    print(f"  ./a_run_pipeline.sh prepare {input_csv}")
    print()


if __name__ == '__main__':
    main()
