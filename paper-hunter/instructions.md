# Paper Filtering Pipeline

**Automated pipeline for filtering research papers based on semantic matching with your research domain.**

## Research Domain

> **"LLM-Based Agentic Framework for Autonomous Black-Box Penetration Testing over Long-Horizon Attack Chains"**

This pipeline filters 30,000+ papers from DBLP (across 32 CSV files) down to ~50-200 highly relevant papers for your literature review.

---

## Pipeline Overview

```
┌─────────────────┐
│  Input CSV      │  1013 papers from q4-2026.csv
│  (DBLP data)    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  STAGE 1        │  AI-powered semantic screening (ALL papers)
│  AI Screening   │  → Fetches abstracts via DOI when missing
└────────┬────────┘  → Applies 4 criteria (llm_agent, offensive_security, etc.)
         │            → Classifies: baseline/survey/benchmark/evaluation
         ▼            → Optional: fuzzy keywords for prioritization only
    ~50-200 papers
         │
         ▼
┌─────────────────┐
│  STAGE 2        │  Independent screen2 (optional, for high confidence)
│  AI Screening   │  → Fresh session, no looking at screen1 results
└────────┬────────┘  → Same criteria, different screener perspective
         │
         ▼
┌─────────────────┐
│  STAGE 3        │  Reconcile & aggregate results
│  Aggregation    │  → Merge screen1 + screen2
└────────┬────────┘  → Generate filtered CSVs by tier
         │            → Statistics & reports
         ▼
    Final output:
    - core papers (baseline candidates)
    - related papers
    - needs_manual review
```

**Key change**: Fuzzy filtering is **OPTIONAL** for prioritization only - ALL papers go to AI screening.

---

## Installation

### Prerequisites

```bash
# Python 3.8+
python3 --version

# Required packages
pip install lxml
```

### Setup

```bash
cd /home/nishan/Documents/paperhunter-research-paper-discovery/pipeline

# Make scripts executable
chmod +x run_pipeline.sh stage2_ai_screen.sh
```

---

## Quick Start

### Option 1: Process a Single CSV

```bash
# Run complete pipeline on one CSV file (ALL papers processed)
./a_run_pipeline.sh full ../paper-list/q4-2026.csv
```

### Option 2: Process All CSVs (32 files)

```bash
# Step 1: Prepare all CSVs (NO filtering)
./run_pipeline.sh batch-all

# Step 2: Pick a prepared file and run screening
./run_pipeline.sh screen work/q4-2026_prepared.json

# Step 3: Aggregate results
./run_pipeline.sh aggregate work/q4-2026_prepared
```

---

## Detailed Usage

### Prepare: Load ALL Papers (NO Filtering)

**Loads all papers with optional prioritization scores.**

```bash
python d_prepare_for_screening.py ../paper-list/q4-2026.csv
```

**What it does:**
- Loads ALL papers from CSV (only filters by date range 2023-2026)
- Assigns unique IDs
- Optionally computes fuzzy scores for prioritization (NOT filtering)
- High-score papers processed first for efficiency

**Output:**
- `work/q4-2026_prepared.json` - ALL papers ready for AI screening

**Configuration:**
- Edit `a_config.py` to customize prioritization keywords

---

### Screen: AI-Powered Semantic Screening

**AI agent classifies ALL papers using semantic understanding.**

```bash
./h_ai_screen.sh work/q4-2026_prepared.json screen1
```

**What it does:**
- Loads fuzzy-filtered papers in batches of 10
- For each batch:
  1. Prepares prompt with paper details
  2. **MANUAL STEP**: You process with your AI agent (antigravity, etc.)
  3. Submit decisions back to pipeline
- Validates decisions against strict criteria
- Tracks progress (resumable)

**The 4 Criteria:**
1. **llm_agent** (STRICT) - LLM in action loop with tools/planning
2. **offensive_security** (STRICT) - Autonomous offensive operations on targets
3. **black_box** (SOFT) - Black-box or gray-box settings
4. **long_horizon** (SOFT) - Multi-step attack chains or planning mechanisms

**Roles:**
- **baseline** - Novel agent systems (all 4 criteria = yes)
- **survey** - Surveys/SoKs on LLM offensive security
- **benchmark** - Benchmarks/datasets for LLM agents
- **evaluation** - Empirical studies
- **background** - Relevant context

**Tiers:**
- **core** - Qualifies for a role (llm_agent=yes AND offensive_security=yes)
- **related** - Adjacent/borderline but useful
- **reject** - Out of scope
- **needs_manual** - Ambiguous, requires human review

**Output:**
- `work/q4-2026_prepared_screen1_decisions.jsonl` - Decisions in JSONL format
- `work/q4-2026_prepared_screen1_state.json` - Progress tracking

**AI Agent Integration:**

Currently manual - you need to:
1. Read `work/batch_prompt_N.txt`
2. Process with your AI agent
3. Save JSON output to `work/batch_output_N.json`

**To automate with your AI agent:**

Edit `h_ai_screen.sh` line ~62 and replace the manual pause with:

```bash
# Example for antigravity or similar CLI agent
your-ai-agent-cli --prompt "$FULL_PROMPT" --output "$BATCH_OUTPUT" --format json
```

---

### Screen (Optional): Independent Screen2

**Run a second independent screening pass for higher confidence.**

```bash
# IMPORTANT: Run in a FRESH SESSION - don't open screen1 files!
./h_ai_screen.sh work/q4-2026_prepared.json screen2
```

**Why screen2?**
- Independent second opinion reduces bias
- Reconciliation keeps papers accepted by either screener (more inclusive)
- Increases confidence in decisions

---

### Aggregate: Reconcile & Report

**Reconcile screens and generate final filtered CSVs.**

```bash
python stage2_aggregate.py work/q4-2026_prepared
```

**What it does:**
- Loads decisions from screen1 (and screen2 if available)
- Reconciles disagreements (more inclusive tier wins)
- Merges roles (union of both screens)
- Generates filtered CSV files by tier
- Creates statistics report

**Output:**
- `output/q4-2026_prepared_all.csv` - All decisions
- `output/q4-2026_prepared_core.csv` - Core papers only
- `output/q4-2026_prepared_related.csv` - Related papers
- `output/q4-2026_prepared_stats.json` - Statistics

**CSV Fields:**
```
id, dblp_key, title, authors, year, venue, doi, url,
tier, roles, primary_contribution,
llm_agent, offensive_security, black_box, long_horizon,
evidence, reason, confidence, reconciliation
```

---

## Status & Monitoring

### Check processing status

```bash
./b_run_pipeline.sh status work/q4-2026_prepared.json
```

Shows:
- Total papers
- Processed count
- Remaining count
- Progress percentage
- Batches completed

---

## Processing All 32 CSV Files

**Recommended workflow:**

```bash
# 1. Prepare all CSVs (fast, ~3 minutes, NO filtering)
./b_run_pipeline.sh batch-all

# 2. Review prepared results - check which CSVs have papers
ls -lh work/*_prepared.json

# 3. For each CSV, run screening
for prepared in work/*_prepared.json; do
    echo "Processing $prepared..."
    ./b_run_pipeline.sh screen "$prepared" screen1
done

# 4. Aggregate each one
for prepared in work/*_prepared.json; do
    stem="${prepared%.json}"
    ./b_run_pipeline.sh aggregate "$stem"
done

# 5. Merge all core papers into master list
cat output/*_core.csv > output/master_core_papers.csv
```

---

## Configuration

### Customize Keywords (c_config.py)

Edit `c_config.py` to tune prioritization:

```python
KEYWORDS = {
    "ai_agent": ["llm", "large language model", "agentic", ...],
    "offensive_security": ["penetration test", "red team", ...],
    # Add more categories
}

# Used for lowering priority, not excluding
EXCLUSION_KEYWORDS = [
    "intrusion detection", "firewall", ...  # Defensive terms
]
```

### Customize AI Screening Criteria

Edit `ai_screening_prompt.md` to:
- Adjust criterion definitions
- Change role requirements
- Add domain-specific rules

---

## File Structure

```
pipeline/
├── instructions.md                # Pipeline usage guide
├── prompt.md                      # AI agent prompt template
├── a_config.py                    # Configuration
├── z_run_pipeline.sh             # Master runner
│
├── b_prepare_for_screening.py    # Prepare: Load ALL papers
├── h_ai_screen.sh                # Screen: AI screening orchestrator
├── c_batch_manager.py            # Screen: Batch manager
├── d_aggregate.py                # Aggregate: Aggregation
├── e_summarize_all.py            # Utility: Cross-CSV summary
│
├── work/                          # Working directory (gitignored)
│   ├── *_prepared.json           # Prepared papers
│   ├── *_screen1_decisions.jsonl # Screen1 decisions
│   ├── *_screen2_decisions.jsonl # Screen2 decisions (optional)
│   ├── *_state.json              # Processing state
│   ├── batch_input_*.json        # Batch inputs
│   ├── batch_output_*.json       # Batch outputs
│   └── batch_prompt_*.txt        # Batch prompts
│
└── output/                        # Final output (gitignored)
    ├── *_all.csv                 # All decisions
    ├── *_core.csv                # Core papers
    ├── *_related.csv             # Related papers
    └── *_stats.json              # Statistics
```

---

## Troubleshooting

### Issue: Stage 2 batch rejected

**Error:** `REJECTED: Validation errors found`

**Solution:**
1. Check the error message for specific issues
2. Fix the `batch_output_N.json` file
3. Resubmit manually:
   ```bash
   python stage2_batch_manager.py submit work/q4-2026_fuzzy.json work/batch_output_N.json --pass screen1
   ```

### Issue: AI agent produces invalid JSON

**Error:** JSON parsing errors

**Solution:**
- Ensure your AI agent outputs ONLY the JSON array
- No markdown code blocks (```json)
- No comments or trailing commas
- Validate JSON: `jq . work/batch_output_N.json`

### Issue: Missing abstracts

**Problem:** Papers have no abstract in CSV

**Solution:**
- Stage 2 should fetch from DOI automatically
- Set `abstract_source: "web"` when fetched
- If DOI fails, set `abstract_source: "none"` and `tier: "needs_manual"`

---

## Performance Expectations

| Stage | Input | Output | Time (per CSV) |
|-------|-------|--------|----------------|
| Prepare | 1000 papers | 1000 papers | ~5 seconds |
| Screen | 1000 papers | 20-50 papers | ~8-16 hours (AI-dependent) |
| Aggregate | 20-50 decisions | CSV + stats | ~1 second |

**For 32 CSVs:**
- Prepare: ~3 minutes total
- Screen: ~256-512 hours total (parallelizable across CSVs)
- Aggregate: ~30 seconds total

---

## Tips & Best Practices

### 1. Optimize Prioritization
- Start with conservative keywords (high recall)
- Review prepared results before screening
- Adjust `config.py` keywords based on patterns

### 2. Parallelize Screening
- Run multiple CSVs in parallel (different terminals)
- Each CSV has independent state
- Merge outputs at the end

### 3. Use Screen2 for Critical CSVs
- Run screen2 on high-value conferences (A*, A-tier)
- Skip screen2 for low-yield journals (saves time)
- Reconciliation increases confidence

### 4. Batch Processing
- Process top-tier conferences first (a-star-*.csv)
- Check if patterns emerge to refine prioritization
- Use statistics to validate approach

### 5. Human Review
- Always review `core` papers manually (especially baselines)
- Check `needs_manual` tier for edge cases
- Low confidence (<0.6) papers need verification

---

## Next Steps After Pipeline

Once you have filtered papers:

1. **Verify baseline papers** - Fetch full texts, confirm criteria
2. **Update your baseline list** - Add to `docs/core/02-papers.md`
3. **Identify gaps** - What's missing from your research domain?
4. **Track new papers** - Run pipeline periodically on new DBLP data

---

## Support & Customization

Need to customize the pipeline? Edit these files:

- **Prioritization keywords**: `a_config.py`
- **Screening criteria**: `prompt.md`
- **Batch size/timeouts**: `a_config.py` (BATCH_SIZE)
- **AI agent integration**: `h_ai_screen.sh` (line ~62)

---

## Summary

This pipeline transforms 32,000+ papers into ~100-300 highly relevant papers through:

1. **Prepare ALL papers** with optional prioritization scores
2. **AI-powered semantic screening** on EVERY paper
3. **Automated reconciliation & reporting**

**Key advantage:** No papers discarded before AI review. Reproducible, resumable, and scalable.

Good luck with your literature review! 🚀
