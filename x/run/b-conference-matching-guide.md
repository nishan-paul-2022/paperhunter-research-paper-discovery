# Conference Matching Guide: DBLP ↔ CORE

## Objective

Build a comprehensive list of CORE-ranked conferences by matching DBLP database entries to CORE rankings.

## Workflow Overview

**Automated Steps (Python):**
1. `a-extract.py` - Scan DBLP XML, extract conferences, match by acronym → Generates exact matches
2. `b-match.py` - Fuzzy match remaining conferences → Generates HIGH/NONE confidence matches
3. `c-merge.py` - Combine exact + HIGH confidence matches → Final conference list

**Your Role (AI):**  
Between steps 2 and 3, review `b-match.py` output and improve matching quality using:
- Algorithm results as starting point
- Web search to verify conference details
- Your intelligence to resolve edge cases (renamed conferences, aliases, ambiguous matches)

**Goal:** Maximize correct matches, minimize false positives.

---

## Strategy: Hybrid Algorithm + AI Review

### Step 1: Run a-extract.py (Initial Extraction)
```bash
cd /home/nishan/Documents/paperhunter-research-paper-discovery
python3 x/conference-hunter/a-extract.py
```

**This extracts conferences from DBLP XML (`x/archive/paper-dblp.xml`) and CORE rankings (`x/archive/publication-core-2026.csv`), performing Phase 1 acronym matching.**

Generates:
- `x/conference-supplement/1-exact-matched.csv` - Exact acronym matches (auto-approved)
- `x/conference-supplement/2-dblp-unmatched.csv` - DBLP conferences needing matching
- `x/conference-supplement/3-source-pool.csv` - CORE conferences available for matching

### Step 2: Run b-match.py (Fuzzy Matching)
```bash
python3 x/conference-hunter/b-match.py
```

Generates `x/conference-supplement/4-fuzzy-matched.csv` with HIGH and NONE confidence entries.

### Step 3: Review Algorithm Output

Check `x/conference-supplement/4-fuzzy-matched.csv`:
- **HIGH confidence** → Likely correct, spot-check
- **NONE (NO_MATCH)** → Need your review

### Step 4: AI Manual Review

Focus on **NO_MATCH entries**:

1. **Search DBLP key online:**
   - Visit: `https://dblp.org/db/conf/[acronym]/`
   - Get: Full conference name, homepage, organizer

2. **Search conference info:**
   - Query: `"[conference_name]" CORE ranking`
   - Query: `"[acronym]" conference computer science`

3. **Check if in CORE pool:**
   ```bash
   grep -i "[acronym]" x/conference-supplement/3-source-pool.csv
   ```

4. **Common issues:**
   - Conference renamed (NeurIPS was NIPS)
   - Acronym changed (ESEC/FSE → FSE)
   - Multiple conferences with similar names
   - Workshop vs Main conference

---

## Input Files

### 1. `x/conference-supplement/2-dblp-unmatched.csv`
Columns: `dblp_key`, `dblp_acronym`

DBLP conferences not matched by exact acronym.

### 2. `x/conference-supplement/3-source-pool.csv`
Columns: `title`, `acronym`, `ranking`

CORE conferences available (A*, A, B, C).

### 3. `x/conference-supplement/4-fuzzy-matched.csv`
Columns: `dblp_key`, `dblp_acronym`, `core_title`, `acronym`, `ranking`, `confidence`, `reasoning`

Algorithm's matching results.

---

## Matching Rules

### SAME Conference:
- **Same acronym** (case-insensitive)
- **Same organizing body** (ACM, IEEE, USENIX, etc.)
- **Same topic/scope**

### NOT the Same:
- Workshops vs Main conference (ICML vs ICML Workshop)
- Regional variations (AAAI vs AAAI-Asia)
- Different years of renamed conference (count as same if renamed)

### Confidence Levels

**HIGH** - Acronym match OR verified via official website  
**MEDIUM** - Strong name similarity but uncertain  
**LOW** - Weak match, needs expert review  
**NONE** - No match in CORE pool

---

## Special Cases

### Case 1: Conference Renamed
Example: "NIPS" → "NeurIPS" (2018)
- CORE lists as: "Advances in Neural Information Processing Systems (was NIPS)"
- Match DBLP `conf/nips` to this entry

### Case 2: Merged Conferences
Example: "ESEC/FSE" → "FSE"
- CORE notes: "(was ESEC/FSE, changed 2024)"
- Match both `conf/fse` and historical keys

### Case 3: Workshop Series
Example: "ICML Workshop on AutoML"
- Not in CORE (workshops not ranked)
- Mark as NO_MATCH

### Case 4: Ambiguous Acronyms
Example: "AI" could be multiple conferences
- Check DBLP key: `conf/ai` vs `conf/aaai`
- Visit dblp.org to see full name

### Case 5: Regional Conferences
Example: "AAAI" (main) vs "AAAI-Asia"
- CORE only lists main conference
- Regional variants → NO_MATCH

---

## Workflow

### Phase 1: Algorithmic Matching
```bash
python3 x/conference-hunter/b-match.py
```
Output: `x/conference-supplement/4-fuzzy-matched.csv`

### Phase 2: AI Review
1. **Count NO_MATCH:**
   ```bash
   grep "NO_MATCH" x/conference-supplement/4-fuzzy-matched.csv | wc -l
   ```

2. **For each NO_MATCH:**
   - Search conference online
   - Check DBLP.org for full name
   - Look for CORE entry
   - Update if match found

3. **Quality check HIGH confidence:**
   - Sample 20 entries
   - Verify acronyms match
   - Flag suspicious ones

### Phase 3: Generate Final List
```bash
python3 x/conference-hunter/c-merge.py
```

Merges:
- `x/conference-supplement/1-exact-matched.csv`
- `x/conference-supplement/4-fuzzy-matched.csv` (HIGH only)

Output: `x/archive/list-of-conferences.csv`

---

## Output Format

Update `x/conference-supplement/4-fuzzy-matched.csv` for NO_MATCH entries:

```csv
dblp_key,dblp_acronym,core_title,acronym,ranking,confidence,reasoning
conf/icml,ICML,International Conference on Machine Learning,ICML,A*,HIGH,Verified via DBLP
conf/unknown,UNKNOWN,NO_MATCH,,,NONE,Not in CORE rankings (regional workshop)
```

---

## Tools & Resources

### Recommended Searches:
- `"[conference_acronym]" CORE ranking`
- `site:dblp.org/db/conf/[acronym]`
- `"[full_name]" computer science conference`

### Useful Sites:
- **DBLP**: https://dblp.org/db/conf/
- **CORE Portal**: http://portal.core.edu.au/conf-ranks/
- **Conference websites** (check organizers, history)

---

## Quality Checklist

- [ ] All HIGH confidence spot-checked (sample 20)
- [ ] NO_MATCH entries searched online
- [ ] No duplicate CORE conferences assigned
- [ ] Conference name changes documented
- [ ] Reasoning filled for all entries

---

## Expected Results

- **HIGH confidence**: Majority of remaining conferences after exact matching
- **NO_MATCH**: Workshops, regional conferences, unranked venues
- **Total matched**: High coverage of CORE-ranked conferences

---

## Example Session

```bash
# Step 1: Run algorithm
python3 x/conference-hunter/b-match.py
# Output: 123 HIGH, 456 NONE

# Step 2: Review NO_MATCH
grep "NO_MATCH" x/conference-supplement/4-fuzzy-matched.csv | head -5

# Step 3: Search one
# conf/neurips,NEURIPS,NO_MATCH,...
# Google: "NeurIPS conference CORE"
# Found: "Advances in Neural Information Processing Systems (was NIPS)" - A*

# Step 4: Check CORE pool
grep -i "neurips\|nips" x/conference-supplement/3-source-pool.csv
# Result: Advances in Neural Information Processing Systems (was NIPS),NeurIPS,A*

# Step 5: Update x/conference-supplement/4-fuzzy-matched.csv
# Change: conf/nips,NIPS,NO_MATCH,...
# To: conf/nips,NIPS,Advances in Neural Information Processing Systems (was NIPS),NeurIPS,A*,HIGH,Conference renamed
```

---

## Final Output

**File**: `x/archive/list-of-conferences.csv`

**Columns**: `dblp_key`, `dblp_acronym`, `title`, `ranking`

**Use**: Authoritative list for extracting papers from DBLP conferences.
