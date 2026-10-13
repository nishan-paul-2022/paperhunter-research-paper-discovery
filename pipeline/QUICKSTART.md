# 🚀 Quick Start Guide

## What This Pipeline Does

Filters **30,000+ papers** from DBLP → **~100 highly relevant papers** for your research on:

> **"LLM-Based Agentic Framework for Autonomous Black-Box Penetration Testing"**

---

## ⚡ 3-Minute Quick Start

### 1️⃣ Test on One CSV (5 seconds)

```bash
cd pipeline
./run_pipeline.sh stage1 ../papers/a-star-2024.csv
```

Expected output: `work/a-star-2024_fuzzy.json` with ~5-20 candidate papers

### 2️⃣ Process with AI Agent (30 minutes)

```bash
./run_pipeline.sh stage2 work/a-star-2024_fuzzy.json screen1
```

For each batch:
1. Script shows you papers to classify
2. **YOU**: Read `work/batch_prompt_N.txt` 
3. **YOU**: Process with your AI agent (antigravity, ChatGPT, Claude, etc.)
4. **YOU**: Save JSON output to `work/batch_output_N.json`
5. Press ENTER to continue
6. Script validates and saves decisions

### 3️⃣ Generate Results (1 second)

```bash
./run_pipeline.sh stage3 work/a-star-2024_fuzzy
```

Expected output:
- `output/a-star-2024_fuzzy_core.csv` - Your baseline candidates!
- `output/a-star-2024_fuzzy_related.csv` - Related papers
- `output/a-star-2024_fuzzy_stats.json` - Statistics

---

## 📊 Process All 32 CSVs

### Method 1: Batch All (Recommended)

```bash
# Step 1: Fuzzy filter all 32 CSVs (~3 minutes)
./run_pipeline.sh batch-all

# Step 2: Check which files have candidates
ls -lh work/*_fuzzy.json | grep -v '": \[\]'

# Step 3: Process each with AI (16-32 hours total, can parallelize)
for fuzzy in work/*_fuzzy.json; do
    ./run_pipeline.sh stage2 "$fuzzy" screen1
done

# Step 4: Aggregate all
for fuzzy in work/*_fuzzy.json; do
    stem="${fuzzy%.json}"
    ./run_pipeline.sh stage3 "$stem"
done

# Step 5: View summary
python summarize_all.py
```

### Method 2: Process Selectively

Target high-value conferences first:

```bash
# A* conferences (highest quality)
./run_pipeline.sh full ../papers/a-star-2024.csv
./run_pipeline.sh full ../papers/a-star-2025.csv

# A-tier conferences
./run_pipeline.sh full ../papers/a-2024.csv
./run_pipeline.sh full ../papers/a-2025.csv

# Q1 journals (top tier)
./run_pipeline.sh full ../papers/q1-2024.csv
./run_pipeline.sh full ../papers/q1-2025.csv
```

---

## 🤖 Automate AI Agent (Optional)

If you have a CLI-based AI agent, edit `stage2_ai_screen.sh` (line ~62):

**Before (manual):**
```bash
read -r  # Wait for human
```

**After (automated):**
```bash
# Example: using antigravity or similar
antigravity --prompt "$FULL_PROMPT" --output "$BATCH_OUTPUT" --format json

# Or with ChatGPT/Claude CLI
chatgpt --system-prompt "$(cat $PROMPT_FILE)" --input "$BATCH_INPUT" > "$BATCH_OUTPUT"
```

---

## 📁 Output Files

After processing, you'll have:

```
pipeline/
├── work/                              # Intermediate files
│   └── *_fuzzy.json                   # Stage 1 output
│
└── output/                            # Final results
    ├── *_core.csv                     # ⭐ Core papers (baseline candidates)
    ├── *_related.csv                  # Related/background papers
    ├── *_all.csv                      # All decisions
    └── *_stats.json                   # Statistics
```

**CSV Fields:**
- `title`, `authors`, `year`, `venue`, `doi`
- `tier` (core/related/reject/needs_manual)
- `roles` (baseline/survey/benchmark/evaluation)
- `llm_agent`, `offensive_security`, `black_box`, `long_horizon` (yes/partial/no)
- `evidence` (quote from paper)
- `confidence` (0.0-1.0)

---

## 🎯 What Papers Pass?

### ✅ CORE Papers (Baseline Candidates)

Must have ALL:
- ✅ LLM-based agent (action loop with tools/planning)
- ✅ Autonomous offensive operations (pentesting, CTF-solving, red-teaming)
- ✅ Black-box or gray-box settings
- ✅ Multi-step attack chains or planning mechanisms
- ✅ Published in peer-reviewed venue

**Examples:**
- PentestGPT (USENIX Security 2024)
- hackingBuddyGPT (ACM FSE 2023)
- EnIGMA (ICML 2025)

### ✅ RELATED Papers

Adjacent research:
- LLM agents for CTF challenges (short chains)
- Benchmarks for AI-based pentesting
- Surveys on LLM security

### ❌ REJECTED Papers

- Intrusion detection (defensive)
- Static vulnerability detection (no agent loop)
- Generic LLM security (jailbreak, prompt injection)
- Non-LLM pentesting tools

---

## 🔧 Customize

### Adjust Keywords

Edit `config.py`:

```python
KEYWORDS = {
    "ai_agent": ["llm", "gpt", "agent", ...],
    "offensive_security": ["pentest", "red team", ...],
}

EXCLUSION_KEYWORDS = ["intrusion detection", "firewall", ...]
```

### Adjust Screening Criteria

Edit `ai_screening_prompt.md` to modify:
- Criterion definitions
- Role requirements
- Scoring rules

---

## 💡 Tips

### 1. Start Small
Test on 1-2 CSVs first to validate approach

### 2. Parallelize
Run multiple CSVs in separate terminal windows

### 3. Use Screen2 for High-Value
Run independent `screen2` pass on A*/A-tier conferences for higher confidence

### 4. Check Statistics
After each file:
```bash
python summarize_all.py
```

### 5. Review Low Confidence
Papers with `confidence < 0.6` need manual verification

---

## 🆘 Common Issues

### No papers passed Stage 1
→ Check the CSV source (might be unrelated domain)
→ Adjust keywords in `config.py`

### AI agent produces invalid JSON
→ Ensure output is pure JSON array (no markdown, no comments)
→ Validate: `jq . work/batch_output_N.json`

### Batch rejected in Stage 2
→ Read error message
→ Fix `batch_output_N.json`
→ Resubmit: `python stage2_batch_manager.py submit ...`

---

## 📈 Expected Results (Per CSV)

| Conference Tier | Input | Stage 1 Pass | Stage 2 Pass (Core) |
|----------------|-------|--------------|---------------------|
| A* (Security) | 1000 | 50-100 | 10-30 |
| A (Security) | 1000 | 30-80 | 5-20 |
| B/C (Security) | 1000 | 10-50 | 2-10 |
| Q1 (AI/Security) | 1000 | 20-60 | 3-15 |
| Other | 1000 | 0-20 | 0-5 |

**Total across 32 CSVs: ~50-200 core papers**

---

## ✅ Next Steps

After completing the pipeline:

1. **Review core papers** - Read abstracts, fetch PDFs
2. **Verify baseline candidates** - Check full text for all 4 criteria
3. **Update your literature** - Add to `docs/core/02-papers.md`
4. **Identify gaps** - What's missing? What can you contribute?
5. **Run periodically** - Rerun pipeline on new DBLP data quarterly

---

## 🎉 You're Ready!

Start filtering papers:

```bash
cd pipeline
./run_pipeline.sh full ../papers/a-star-2024.csv
```

For full documentation, see [README.md](README.md)
