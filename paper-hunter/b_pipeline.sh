#!/bin/bash
#
# Master Pipeline Runner
# 
# Runs the complete 3-stage pipeline for filtering research papers
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    cat << EOF
Usage: $0 <command> [options]

Commands:
  full <csv_file>           Run complete pipeline (prepare + screen1 + aggregate)
  prepare <csv_file>        Prepare papers for screening (NO filtering)
  screen <prepared_json>    Run AI screening on prepared papers
  aggregate <prepared_stem> Aggregate screening results
  status <prepared_json>    Show processing status
  batch-all                 Prepare all CSV files in paper-list/

Examples:
  # Run complete pipeline on one CSV
  $0 full ../paper-list/q4-2026.csv

  # Prepare all CSVs
  $0 batch-all

  # Continue screening after manual work
  $0 screen work/q4-2026-prepared.json

  # Aggregate results
  $0 aggregate work/q4-2026_prepared

  # Check status
  $0 status work/q4-2026-prepared.json

EOF
}

cmd_full() {
    local csv_file="$1"
    
    if [ -z "$csv_file" ]; then
        echo "ERROR: CSV file required"
        usage
        exit 1
    fi
    
    if [ ! -f "$csv_file" ]; then
        echo "ERROR: File not found: $csv_file"
        exit 1
    fi
    
    echo ""
    echo "╔══════════════════════════════════════════════════════════════════════╗"
    echo "║          Full Pipeline: Paper Filtering                              ║"
    echo "╚══════════════════════════════════════════════════════════════════════╝"
    echo ""
    echo "Input CSV: $csv_file"
    echo ""
    echo "⚠️  NOTE: ALL papers will be processed (no filtering)"
    echo "         Fuzzy scores only determine processing order"
    echo ""
    
    # Stage 1: Prepare papers (with prioritization)
    echo "Running Prepare: Prepare Papers for Screening..."
    python3 "$SCRIPT_DIR/d_prepare_for_screening.py" "$csv_file"
    
    local csv_stem=$(basename "$csv_file" .csv)
    local prepared_json="$SCRIPT_DIR/work/${csv_stem}-prepared.json"
    
    # Stage 2: AI screening (screen1)
    echo ""
    echo "Running Screen: AI Screening (screen1)..."
    "$SCRIPT_DIR/h_screen.sh" "$prepared_json" screen1
    
    # Optional: Independent screen2
    echo ""
    read -p "Run independent screen2 pass? (y/n) " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        "$SCRIPT_DIR/h_screen.sh" "$prepared_json" screen2
    fi
    
    # Stage 3: Aggregation
    echo ""
    echo "Running Aggregate: Aggregation..."
    python3 "$SCRIPT_DIR/f_aggregate.py" "$prepared_json"
    
    echo ""
    echo "╔══════════════════════════════════════════════════════════════════════╗"
    echo "║          ✓ Full Pipeline Complete!                                   ║"
    echo "╚══════════════════════════════════════════════════════════════════════╝"
    echo ""
    echo "Output files in: $SCRIPT_DIR/output/"
    echo ""
}

cmd_stage1() {
    local csv_file="$1"
    
    if [ -z "$csv_file" ]; then
        echo "ERROR: CSV file required"
        usage
        exit 1
    fi
    
    python3 "$SCRIPT_DIR/d_prepare.py" "$csv_file"
}

cmd_stage2() {
    local prepared_json="$1"
    local pass_name="${2:-screen1}"
    
    if [ -z "$prepared_json" ]; then
        echo "ERROR: Prepared JSON file required"
        usage
        exit 1
    fi
    
    "$SCRIPT_DIR/h_screen.sh" "$prepared_json" "$pass_name"
}

cmd_stage3() {
    local prepared_stem="$1"
    
    if [ -z "$prepared_stem" ]; then
        echo "ERROR: Prepared JSON stem required"
        usage
        exit 1
    fi
    
    python3 "$SCRIPT_DIR/f_aggregate.py" "$prepared_stem"
}

cmd_status() {
    local prepared_json="$1"
    local pass_name="${2:-screen1}"
    
    if [ -z "$prepared_json" ]; then
        echo "ERROR: Prepared JSON file required"
        usage
        exit 1
    fi
    
    python3 "$SCRIPT_DIR/e_batch.py" status "$prepared_json" --pass "$pass_name"
}

cme_batch_all() {
    local papers_dir="$(dirname "$SCRIPT_DIR")/paper-list"
    
    if [ ! -d "$papers_dir" ]; then
        echo "ERROR: Papers directory not found: $papers_dir"
        exit 1
    fi
    
    echo ""
    echo "╔══════════════════════════════════════════════════════════════════════╗"
    echo "║          Batch Processing: All CSV Files                             ║"
    echo "╚══════════════════════════════════════════════════════════════════════╝"
    echo ""
    
    local csv_files=("$papers_dir"/*.csv)
    local total=${#csv_files[@]}
    local current=0
    
    echo "Found $total CSV files to process"
    echo ""
    echo "⚠️  NOTE: ALL papers will be prepared for screening (no filtering)"
    echo ""
    
    for csv_file in "${csv_files[@]}"; do
        current=$((current + 1))
        
        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "Processing [$current/$total]: $(basename "$csv_file")"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        
        python3 "$SCRIPT_DIR/d_prepare.py" "$csv_file"
    done
    
    echo ""
    echo "╔══════════════════════════════════════════════════════════════════════╗"
    echo "║          ✓ Batch Stage 1 Complete!                                   ║"
    echo "╚══════════════════════════════════════════════════════════════════════╝"
    echo ""
    echo "Prepared JSON files in: $SCRIPT_DIR/work/"
    echo ""
    echo "Next steps:"
    echo "  1. Review prepared files"
    echo "  2. Run screening on selected files:"
    echo "     $0 screen work/<file>-prepared.json"
    echo ""
}

# Main command dispatcher
CMD="${1:-}"

case "$CMD" in
    full)
        shift
        cmd_full "$@"
        ;;
    prepare)
        shift
        cmd_stage1 "$@"
        ;;
    screen)
        shift
        cmd_stage2 "$@"
        ;;
    aggregate)
        shift
        cmd_stage3 "$@"
        ;;
    status)
        shift
        cmd_status "$@"
        ;;
    batch-all)
        cme_batch_all
        ;;
    "")
        usage
        ;;
    *)
        echo "ERROR: Unknown command: $CMD"
        echo ""
        usage
        exit 1
        ;;
esac
