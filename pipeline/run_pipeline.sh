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
  full <csv_file>           Run complete pipeline (stage1 + stage2 + stage3)
  stage1 <csv_file>         Run stage 1 only (fuzzy filtering)
  stage2 <fuzzy_json>       Run stage 2 only (AI screening)
  stage3 <fuzzy_stem>       Run stage 3 only (aggregation)
  status <fuzzy_json>       Show processing status
  batch-all                 Run stage1 on all CSV files in papers/

Examples:
  # Run complete pipeline on one CSV
  $0 full ../papers/q4-2026.csv

  # Run stage 1 on all CSVs
  $0 batch-all

  # Continue stage 2 after manual work
  $0 stage2 work/q4-2026_fuzzy.json

  # Aggregate results
  $0 stage3 work/q4-2026_fuzzy

  # Check status
  $0 status work/q4-2026_fuzzy.json

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
    
    # Stage 1: Fuzzy filtering
    echo "Running Stage 1: Fuzzy Filtering..."
    python3 "$SCRIPT_DIR/stage1_fuzzy_filter.py" "$csv_file"
    
    local csv_stem=$(basename "$csv_file" .csv)
    local fuzzy_json="$SCRIPT_DIR/work/${csv_stem}_fuzzy.json"
    
    # Stage 2: AI screening (screen1)
    echo ""
    echo "Running Stage 2: AI Screening (screen1)..."
    "$SCRIPT_DIR/stage2_ai_screen.sh" "$fuzzy_json" screen1
    
    # Optional: Stage 2 screen2 (independent second opinion)
    echo ""
    read -p "Run independent screen2 pass? (y/n) " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        "$SCRIPT_DIR/stage2_ai_screen.sh" "$fuzzy_json" screen2
    fi
    
    # Stage 3: Aggregation
    echo ""
    echo "Running Stage 3: Aggregation..."
    python3 "$SCRIPT_DIR/stage3_aggregate.py" "$fuzzy_json"
    
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
    
    python3 "$SCRIPT_DIR/stage1_fuzzy_filter.py" "$csv_file"
}

cmd_stage2() {
    local fuzzy_json="$1"
    local pass_name="${2:-screen1}"
    
    if [ -z "$fuzzy_json" ]; then
        echo "ERROR: Fuzzy JSON file required"
        usage
        exit 1
    fi
    
    "$SCRIPT_DIR/stage2_ai_screen.sh" "$fuzzy_json" "$pass_name"
}

cmd_stage3() {
    local fuzzy_stem="$1"
    
    if [ -z "$fuzzy_stem" ]; then
        echo "ERROR: Fuzzy JSON stem required"
        usage
        exit 1
    fi
    
    python3 "$SCRIPT_DIR/stage3_aggregate.py" "$fuzzy_stem"
}

cmd_status() {
    local fuzzy_json="$1"
    local pass_name="${2:-screen1}"
    
    if [ -z "$fuzzy_json" ]; then
        echo "ERROR: Fuzzy JSON file required"
        usage
        exit 1
    fi
    
    python3 "$SCRIPT_DIR/stage2_batch_manager.py" status "$fuzzy_json" --pass "$pass_name"
}

cmd_batch_all() {
    local papers_dir="$(dirname "$SCRIPT_DIR")/papers"
    
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
    
    for csv_file in "${csv_files[@]}"; do
        current=$((current + 1))
        
        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "Processing [$current/$total]: $(basename "$csv_file")"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        
        python3 "$SCRIPT_DIR/stage1_fuzzy_filter.py" "$csv_file"
    done
    
    echo ""
    echo "╔══════════════════════════════════════════════════════════════════════╗"
    echo "║          ✓ Batch Stage 1 Complete!                                   ║"
    echo "╚══════════════════════════════════════════════════════════════════════╝"
    echo ""
    echo "Fuzzy-filtered JSON files in: $SCRIPT_DIR/work/"
    echo ""
    echo "Next steps:"
    echo "  1. Review fuzzy results"
    echo "  2. Run stage 2 on selected files:"
    echo "     $0 stage2 work/<file>_fuzzy.json"
    echo ""
}

# Main command dispatcher
CMD="${1:-}"

case "$CMD" in
    full)
        shift
        cmd_full "$@"
        ;;
    stage1)
        shift
        cmd_stage1 "$@"
        ;;
    stage2)
        shift
        cmd_stage2 "$@"
        ;;
    stage3)
        shift
        cmd_stage3 "$@"
        ;;
    status)
        shift
        cmd_status "$@"
        ;;
    batch-all)
        cmd_batch_all
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
