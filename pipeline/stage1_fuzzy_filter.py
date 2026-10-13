#!/usr/bin/env python3
"""
Stage 1: Fuzzy/Keyword-based Filtering

Fast first pass using keyword matching to reduce papers from 1000+ to ~50-100 candidates.
Optimized for RECALL (don't miss potential papers).
"""

import csv
import json
import sys
from pathlib import Path
from collections import defaultdict

# Add pipeline dir to path
sys.path.insert(0, str(Path(__file__).parent))
from config import *


def normalize_text(text):
    """Normalize text for fuzzy matching."""
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


def check_exclusions(title, abstract=""):
    """Check if paper should be excluded based on exclusion keywords."""
    combined = normalize_text(title + " " + abstract)
    for keyword in EXCLUSION_KEYWORDS:
        if keyword in combined:
            return True
    return False


def fuzzy_match_paper(paper):
    """
    Apply fuzzy matching rules.
    
    Returns: (matched: bool, matched_categories: list, score: int)
    """
    title = paper.get('title', '')
    # We don't have abstracts yet, but check if available
    abstract = paper.get('abstract', '')
    
    combined_text = title + " " + abstract
    
    # Check exclusions first
    if check_exclusions(title, abstract):
        return False, [], 0
    
    # Check year range
    try:
        year = int(paper.get('year', 0))
        if year < MIN_YEAR or year > MAX_YEAR:
            return False, [], 0
    except (ValueError, TypeError):
        return False, [], 0
    
    # Match against keyword categories
    matched_categories = []
    total_matches = 0
    
    for category, keywords in KEYWORDS.items():
        if contains_keyword(combined_text, keywords):
            matched_categories.append(category)
            total_matches += 1
    
    # Check required combinations
    has_ai = any(cat in matched_categories for cat in REQUIRED_CATEGORIES["must_have_ai"])
    has_security = any(cat in matched_categories for cat in REQUIRED_CATEGORIES["must_have_security"])
    
    # Must have at least AI + Security to pass
    if has_ai and has_security:
        return True, matched_categories, total_matches
    
    return False, matched_categories, total_matches


def process_csv(input_csv_path):
    """
    Process a single CSV file through fuzzy filter.
    
    Returns: (passed_papers, stats)
    """
    passed = []
    stats = {
        'total': 0,
        'passed': 0,
        'rejected': 0,
        'out_of_range': 0,
        'excluded': 0,
        'categories': defaultdict(int)
    }
    
    with open(input_csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        
        for row in reader:
            stats['total'] += 1
            
            # Create paper dict with unique ID
            paper = {
                'id': f"{Path(input_csv_path).stem}_{stats['total']:04d}",
                'dblp_key': row.get('dblp_key', ''),
                'title': row.get('title', ''),
                'authors': row.get('authors', ''),
                'year': row.get('year', ''),
                'venue': row.get('venue', ''),
                'doi': row.get('ee', ''),  # 'ee' field contains DOI
                'url': row.get('url', ''),
            }
            
            # Apply fuzzy matching
            matched, categories, score = fuzzy_match_paper(paper)
            
            if matched:
                paper['fuzzy_categories'] = categories
                paper['fuzzy_score'] = score
                passed.append(paper)
                stats['passed'] += 1
                
                for cat in categories:
                    stats['categories'][cat] += 1
            else:
                stats['rejected'] += 1
    
    return passed, stats


def main():
    if len(sys.argv) < 2:
        print("Usage: python stage1_fuzzy_filter.py <input_csv> [output_json]")
        print("\nExample:")
        print("  python stage1_fuzzy_filter.py ../papers/q4-2026.csv")
        print("  python stage1_fuzzy_filter.py ../papers/q4-2026.csv output/q4-2026_fuzzy.json")
        sys.exit(1)
    
    input_csv = Path(sys.argv[1])
    
    if not input_csv.exists():
        print(f"ERROR: File not found: {input_csv}")
        sys.exit(1)
    
    # Determine output path
    if len(sys.argv) >= 3:
        output_json = Path(sys.argv[2])
    else:
        output_json = WORK_DIR / f"{input_csv.stem}_fuzzy.json"
    
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║          Stage 1: Fuzzy/Keyword Filtering                            ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print()
    print(f"Input:  {input_csv}")
    print(f"Output: {output_json}")
    print()
    print(f"Research topic: {RESEARCH_TOPIC}")
    print(f"Year range: {MIN_YEAR}-{MAX_YEAR}")
    print()
    
    # Process
    passed, stats = process_csv(input_csv)
    
    # Save results
    output_json.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump({
            'source_csv': str(input_csv),
            'timestamp': __import__('datetime').datetime.now().isoformat(),
            'stats': dict(stats),
            'papers': passed
        }, f, indent=2, ensure_ascii=False)
    
    # Print statistics
    print("━" * 70)
    print("STATISTICS")
    print("━" * 70)
    print(f"Total papers scanned:     {stats['total']:>6}")
    print(f"Papers passed:            {stats['passed']:>6}  ({stats['passed']/stats['total']*100:.1f}%)")
    print(f"Papers rejected:          {stats['rejected']:>6}  ({stats['rejected']/stats['total']*100:.1f}%)")
    print()
    print("Category matches:")
    for category, count in sorted(stats['categories'].items(), key=lambda x: -x[1]):
        print(f"  {category:25s} {count:>6}")
    print()
    print("━" * 70)
    print(f"✓ Filtered papers saved to: {output_json}")
    print("━" * 70)
    print()
    print("Next step:")
    print(f"  python stage2_semantic_screening.py {output_json}")
    print()


if __name__ == '__main__':
    main()
