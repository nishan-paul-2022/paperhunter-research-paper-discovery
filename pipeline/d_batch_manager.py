#!/usr/bin/env python3
"""
Stage 2 Batch Manager: Prepares batches for AI-powered semantic screening

This script manages the batch processing workflow:
1. Loads fuzzy-filtered papers
2. Fetches abstracts for papers (via DOI if missing)
3. Creates batches for AI agent processing
4. Tracks progress across batches
5. Collects and validates AI decisions
"""

import csv
import json
import sys
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
from b_config import *


class BatchManager:
    def __init__(self, prepared_json_path, pass_name="screen1"):
        self.prepared_json_path = Path(prepared_json_path)
        self.pass_name = pass_name  # screen1 | screen2 | verify
        
        # Load prepared papers (all papers, possibly prioritized)
        with open(self.prepared_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            self.papers = data['papers']
            self.source_csv = data.get('source_csv', '')
        
        # State files
        self.state_file = WORK_DIR / f"{self.prepared_json_path.stem}_{pass_name}_state.json"
        self.decisions_file = WORK_DIR / f"{self.prepared_json_path.stem}_{pass_name}_decisions.jsonl"
        
        # Load or initialize state
        self.load_state()
    
    def load_state(self):
        """Load processing state or initialize new."""
        if self.state_file.exists():
            with open(self.state_file, 'r', encoding='utf-8') as f:
                self.state = json.load(f)
        else:
            self.state = {
                'pass': self.pass_name,
                'total_papers': len(self.papers),
                'processed_ids': [],
                'current_batch': 0,
                'started_at': datetime.now().isoformat(),
                'updated_at': datetime.now().isoformat()
            }
            self.save_state()
    
    def save_state(self):
        """Save current state."""
        self.state['updated_at'] = datetime.now().isoformat()
        with open(self.state_file, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, indent=2)
    
    def get_next_batch(self, n=BATCH_SIZE):
        """
        Get next batch of unprocessed papers.
        
        Returns: list of paper dicts, or empty list if all done
        """
        processed_set = set(self.state['processed_ids'])
        
        batch = []
        for paper in self.papers:
            if paper['id'] not in processed_set:
                batch.append(paper)
                if len(batch) >= n:
                    break
        
        return batch
    
    def get_remaining_count(self):
        """Count how many papers remain unprocessed."""
        processed_set = set(self.state['processed_ids'])
        return len([p for p in self.papers if p['id'] not in processed_set])
    
    def submit_decisions(self, decisions):
        """
        Submit batch decisions and update state.
        
        decisions: list of decision dicts
        
        Returns: (success: bool, errors: list)
        """
        errors = []
        
        # Validate each decision
        for decision in decisions:
            error = self.validate_decision(decision)
            if error:
                errors.append(f"Paper {decision.get('id', '?')}: {error}")
        
        if errors:
            return False, errors
        
        # Append to decisions file (JSONL format)
        with open(self.decisions_file, 'a', encoding='utf-8') as f:
            for decision in decisions:
                f.write(json.dumps(decision, ensure_ascii=False) + '\n')
        
        # Update state
        for decision in decisions:
            self.state['processed_ids'].append(decision['id'])
        
        self.state['current_batch'] += 1
        self.save_state()
        
        return True, []
    
    def validate_decision(self, decision):
        """
        Validate a decision object.
        
        Returns: error message or None
        """
        # Check required fields
        required = ['id', 'tier', 'roles', 'primary_contribution', 'criteria', 'confidence']
        for field in required:
            if field not in decision:
                return f"Missing required field: {field}"
        
        # Check tier value
        if decision['tier'] not in ['core', 'related', 'reject', 'needs_manual']:
            return f"Invalid tier: {decision['tier']}"
        
        # Check confidence range
        try:
            conf = float(decision['confidence'])
            if conf < 0 or conf > 1:
                return "Confidence must be between 0.0 and 1.0"
        except (ValueError, TypeError):
            return "Invalid confidence value"
        
        # Check criteria structure
        criteria = decision.get('criteria', {})
        for criterion in ['llm_agent', 'offensive_security', 'black_box', 'long_horizon']:
            if criterion not in criteria:
                return f"Missing criterion: {criterion}"
            if criteria[criterion] not in ['yes', 'partial', 'no', 'unknown']:
                return f"Invalid value for {criterion}: {criteria[criterion]}"
        
        # Baseline role validation (strict rules from guide)
        if 'baseline' in decision.get('roles', []):
            if decision['primary_contribution'] != 'agent_system':
                return "baseline role requires primary_contribution=agent_system"
            
            if self.pass_name == 'verify':  # Only enforce in verify pass
                for criterion in ['llm_agent', 'offensive_security', 'black_box', 'long_horizon']:
                    if criteria[criterion] != 'yes':
                        return f"baseline role requires all criteria=yes, but {criterion}={criteria[criterion]}"
                
                pub_status = decision.get('publication_status', '')
                if pub_status not in ['published', 'workshop_archival_toplevel']:
                    return f"baseline role requires published/workshop_archival_toplevel, got: {pub_status}"
        
        return None
    
    def get_status(self):
        """Return current status as dict."""
        return {
            'pass': self.pass_name,
            'total': self.state['total_papers'],
            'processed': len(self.state['processed_ids']),
            'remaining': self.get_remaining_count(),
            'progress_pct': len(self.state['processed_ids']) / self.state['total_papers'] * 100 if self.state['total_papers'] > 0 else 0,
            'batches_completed': self.state['current_batch'],
            'started_at': self.state['started_at'],
            'updated_at': self.state['updated_at']
        }


def cmd_next_batch(args):
    """Get next batch of papers to process."""
    manager = BatchManager(args.prepared_json, args.pass_name)
    
    batch = manager.get_next_batch(args.n)
    remaining = manager.get_remaining_count()
    
    # Print to stderr for logging
    print(f"[Batch {manager.state['current_batch'] + 1}] Papers remaining: {remaining}", file=sys.stderr)
    
    # Print batch as JSON to stdout
    print(json.dumps(batch, indent=2, ensure_ascii=False))


def cmd_submit(args):
    """Submit decisions from batch processing."""
    manager = BatchManager(args.prepared_json, args.pass_name)
    
    # Load decisions from file
    with open(args.batch_output, 'r', encoding='utf-8') as f:
        decisions = json.load(f)
    
    if not isinstance(decisions, list):
        print("ERROR: Batch output must be a JSON array", file=sys.stderr)
        sys.exit(1)
    
    success, errors = manager.submit_decisions(decisions)
    
    if not success:
        print("REJECTED: Validation errors found:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        sys.exit(1)
    
    print(f"✓ Accepted {len(decisions)} decisions")
    
    remaining = manager.get_remaining_count()
    if remaining == 0:
        print(f"✓ All papers processed for {args.pass_name} pass!")
    else:
        print(f"  {remaining} papers remaining")


def cmd_status(args):
    """Show current processing status."""
    manager = BatchManager(args.prepared_json, args.pass_name)
    status = manager.get_status()
    
    print()
    print("━" * 70)
    print(f"Status: {args.pass_name} Pass")
    print("━" * 70)
    print(f"Total papers:        {status['total']}")
    print(f"Processed:           {status['processed']}")
    print(f"Remaining:           {status['remaining']}")
    print(f"Progress:            {status['progress_pct']:.1f}%")
    print(f"Batches completed:   {status['batches_completed']}")
    print(f"Started:             {status['started_at']}")
    print(f"Last update:         {status['updated_at']}")
    print("━" * 70)
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Manage batch processing for semantic screening",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command')
    
    # next_batch command
    next_parser = subparsers.add_parser('next', help='Get next batch of papers')
    next_parser.add_argument('prepared_json', type=str, help='Path to prepared papers JSON')
    next_parser.add_argument('--pass', dest='pass_name', default='screen1', 
                             choices=['screen1', 'screen2', 'verify'], help='Pass name')
    next_parser.add_argument('--n', type=int, default=BATCH_SIZE, help='Batch size')
    
    # submit command
    submit_parser = subparsers.add_parser('submit', help='Submit batch decisions')
    submit_parser.add_argument('prepared_json', type=str, help='Path to prepared papers JSON')
    submit_parser.add_argument('batch_output', type=str, help='Path to batch output JSON')
    submit_parser.add_argument('--pass', dest='pass_name', default='screen1',
                               choices=['screen1', 'screen2', 'verify'], help='Pass name')
    
    # status command
    status_parser = subparsers.add_parser('status', help='Show processing status')
    status_parser.add_argument('prepared_json', type=str, help='Path to prepared papers JSON')
    status_parser.add_argument('--pass', dest='pass_name', default='screen1',
                               choices=['screen1', 'screen2', 'verify'], help='Pass name')
    
    args = parser.parse_args()
    
    if args.command == 'next':
        cmd_next_batch(args)
    elif args.command == 'submit':
        cmd_submit(args)
    elif args.command == 'status':
        cmd_status(args)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
