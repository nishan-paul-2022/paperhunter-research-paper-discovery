#!/bin/bash
#
# Stage 2: AI-Powered Semantic Screening
# 
# This script orchestrates the AI agent to process batches of papers.
# It loops until all papers are processed.
#
# Usage: ./stage2_ai_screen.sh <fuzzy_json> <pass_name>
#

set -e

if [ $# -lt 2 ]; then
    echo "Usage: $0 <fuzzy_json> <pass_name>"
    echo ""
    echo "Example:"
    echo "  ./stage2_ai_screen.sh work/q4-2026_fuzzy.json screen1"
    echo ""
    echo "Pass names: screen1, screen2, verify"
    exit 1
fi

FUZZY_JSON="$1"
PASS_NAME="$2"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BATCH_MGR="$SCRIPT_DIR/stage2_batch_manager.py"
PROMPT_FILE="$SCRIPT_DIR/ai_screening_prompt.md"
WORK_DIR="$SCRIPT_DIR/work"

# Ensure fuzzy JSON exists
if [ ! -f "$FUZZY_JSON" ]; then
    echo "ERROR: Fuzzy JSON file not found: $FUZZY_JSON"
    exit 1
fi

echo ""
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║          Stage 2: AI-Powered Semantic Screening                      ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""
echo "Fuzzy JSON: $FUZZY_JSON"
echo "Pass name:  $PASS_NAME"
echo ""

# Show initial status
python3 "$BATCH_MGR" status "$FUZZY_JSON" --pass "$PASS_NAME"

# Main loop
BATCH_NUM=1
while true; do
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Batch $BATCH_NUM"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    # Get next batch
    BATCH_JSON=$(python3 "$BATCH_MGR" next "$FUZZY_JSON" --pass "$PASS_NAME" --n 10)
    
    # Check if empty (all done)
    if [ "$BATCH_JSON" = "[]" ]; then
        echo ""
        echo "✓ All papers processed!"
        echo ""
        python3 "$BATCH_MGR" status "$FUZZY_JSON" --pass "$PASS_NAME"
        break
    fi
    
    # Save batch to temp file
    BATCH_INPUT="$WORK_DIR/batch_input_${BATCH_NUM}.json"
    BATCH_OUTPUT="$WORK_DIR/batch_output_${BATCH_NUM}.json"
    
    echo "$BATCH_JSON" > "$BATCH_INPUT"
    
    echo ""
    echo "Papers in this batch:"
    echo "$BATCH_JSON" | jq -r '.[] | "  [\(.id)] \(.title)"'
    echo ""
    
    # Prepare prompt for AI agent
    # Combine the prompt template with the batch papers
    FULL_PROMPT="$WORK_DIR/batch_prompt_${BATCH_NUM}.txt"
    cat "$PROMPT_FILE" > "$FULL_PROMPT"
    echo "" >> "$FULL_PROMPT"
    echo "## INPUT BATCH" >> "$FULL_PROMPT"
    echo "" >> "$FULL_PROMPT"
    echo '```json' >> "$FULL_PROMPT"
    echo "$BATCH_JSON" >> "$FULL_PROMPT"
    echo '```' >> "$FULL_PROMPT"
    
    echo "──────────────────────────────────────────────────────────────────────"
    echo "Invoking AI agent to classify papers..."
    echo ""
    echo "⚠️  MANUAL STEP REQUIRED:"
    echo ""
    echo "1. Read the prompt file: $FULL_PROMPT"
    echo "2. Process the papers according to the instructions"
    echo "3. Save your JSON output to: $BATCH_OUTPUT"
    echo "4. Press ENTER when ready to continue..."
    echo ""
    
    # TODO: Replace this with actual AI agent command
    # For now, we pause for manual processing
    #
    # Example AI agent command (update this based on your setup):
    # antigravity --prompt "$FULL_PROMPT" --output "$BATCH_OUTPUT"
    #
    read -r
    
    # Check if output exists
    if [ ! -f "$BATCH_OUTPUT" ]; then
        echo "ERROR: Batch output not found: $BATCH_OUTPUT"
        echo "Please create the output file and try again."
        exit 1
    fi
    
    echo ""
    echo "Submitting decisions..."
    
    # Submit decisions
    if python3 "$BATCH_MGR" submit "$FUZZY_JSON" "$BATCH_OUTPUT" --pass "$PASS_NAME"; then
        echo "✓ Batch $BATCH_NUM completed"
        BATCH_NUM=$((BATCH_NUM + 1))
    else
        echo ""
        echo "ERROR: Batch rejected. Please fix the issues in $BATCH_OUTPUT and re-run:"
        echo "  python3 $BATCH_MGR submit $FUZZY_JSON $BATCH_OUTPUT --pass $PASS_NAME"
        exit 1
    fi
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✓ Stage 2 ($PASS_NAME) Complete!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
