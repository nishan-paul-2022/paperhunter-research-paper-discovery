# Conference Matching Guide: DBLP ↔ CORE

## Objective

Match DBLP conferences to CORE rankings using:
1. **b_match.py** - Intelligent matching algorithm
2. **Web search** - Verify conference names and acronyms
3. **Your intelligence** - Handle edge cases

---

## Strategy: Hybrid Algorithm + AI Review

### Step 1: Run b_match.py
```bash
cd /home/nishan/Documents/paperhunter-research-paper-discovery
python conference-hunter/b_match.py
```

Generates `conference-supplement/4-conf-matched.csv` with HIGH and NONE confidence entries.

### Step 2: Review Algorithm Output

Check `conference-supplement/4-conf-matched.csv`:
- **HIGH confidence** → Likely correct, spot-check
- **NONE (NO_MATCH)** → Need your review

### Step 3: AI Manual Review

Focus on **NO_MATCH entries**:

1. **Search DBLP key online:**
   - Visit: `https://dblp.org/db/conf/[acronym]/`
   - Get: Full conference name, homepage, organizer

2. **Search conference info:**
   - Query: `"[conference_name]" CORE ranking`
   - Query: `"[acronym]" conference computer science`

3. **Check if in CORE pool:**
   ```bash
   grep -i "[acronym]" conference-supplement/3-core-pool.csv
   ```

4. **Common issues:**
   - Conference renamed (NeurIPS was NIPS)
   - Acronym changed (ESEC/FSE → FSE)
   - Multiple conferences with similar names
   - Workshop vs Main conference

---

## Input Files

### 1. `conference-supplement/2-conf-todo.csv`
Columns: `dblp_key`, `dblp_acronym`

DBLP conferences not matched by exact acronym.

### 2. `conference-supplement/3-core-pool.csv`
Columns: `title`, `acronym`, `ranking`

CORE conferences available (A*, A, B, C).

### 3. `conference-supplement/4-conf-matched.csv`
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
Example: "AAAI" (main) vs "AAAI-Asia" (regional)
- CORE only lists main conference
- Regional variants → NO_MATCH

---

## Workflow

### Phase 1: Algorithmic Matching
```bash
python conference-hunter/b_match.py
```
Output: `conference-supplement/4-conf-matched.csv`

### Phase 2: AI Review
1. **Count NO_MATCH:**
   ```bash
   grep "NO_MATCH" conference-supplement/4-conf-matched.csv | wc -l
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
python conference-hunter/c_merge.py
```

Merges:
- `conference-supplement/1-conf-exact-matched.csv`
- `conference-supplement/4-conf-matched.csv` (HIGH only)

Output: `conference-context/list-of-conferences.csv`

---

## Output Format

Update `conference-supplement/4-conf-matched.csv` for NO_MATCH entries:

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

- **HIGH confidence**: ~400-500 conferences
- **NO_MATCH**: ~300-400 (workshops, regional, not ranked)
- **Total matched**: ~400-500 conferences

---

## Example Session

```bash
# Step 1: Run algorithm
python conference-hunter/b_match.py
# Output: 123 HIGH, 456 NONE

# Step 2: Review NO_MATCH
grep "NO_MATCH" conference-supplement/4-conf-matched.csv | head -5

# Step 3: Search one
# conf/neurips,NEURIPS,NO_MATCH,...
# Google: "NeurIPS conference CORE"
# Found: "Advances in Neural Information Processing Systems (was NIPS)" - A*

# Step 4: Check CORE pool
grep -i "neurips\|nips" conference-supplement/3-core-pool.csv
# Result: Advances in Neural Information Processing Systems (was NIPS),NeurIPS,A*

# Step 5: Update conference-supplement/4-conf-matched.csv
# Change: conf/nips,NIPS,NO_MATCH,...
# To: conf/nips,NIPS,Advances in Neural Information Processing Systems (was NIPS),NeurIPS,A*,HIGH,Conference renamed
```

---

## Final Output

**File**: `context/list-of-conferences.csv`

**Columns**: `dblp_key`, `dblp_acronym`, `title`, `ranking`

**Use**: Authoritative list for extracting papers from DBLP conferences.
