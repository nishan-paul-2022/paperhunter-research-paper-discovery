# Journal Matching Task: DBLP ↔ SJR

## Objective

Match each DBLP journal in `DBLP-unmatched.csv` to exactly ONE journal in `SJR-available.csv` (or mark as NO_MATCH if none exists).

---

## Task Requirements

### Input Files
1. **DBLP-unmatched.csv** - Columns: `dblp_key`, `dblp_name`
2. **SJR-available.csv** - Columns: `title`, `issn_1`, `issn_2`, `quartile`, `coverage`

### Output File
**AI-matched-journals.csv** with columns:
- `dblp_key` - From DBLP-unmatched.csv
- `dblp_name` - From DBLP-unmatched.csv  
- `sjr_title` - Matched SJR journal title (or "NO_MATCH")
- `issn_1` - From matched SJR entry (or empty)
- `issn_2` - From matched SJR entry (or empty)
- `quartile` - From matched SJR entry (or empty)
- `coverage` - From matched SJR entry (or empty)
- `confidence` - Your confidence: HIGH, MEDIUM, LOW, NONE
- `reasoning` - Brief explanation (max 100 chars)

---

## Matching Rules

### 1. Definition of "SAME Journal"
Two journals are the SAME if they share:
- **Same editorial scope and content**
- **Same publisher** (or successor after acquisition)
- **Same ISSN** (if available through search)
- **Same publication history** (accounting for name changes)

### 2. What Counts as DIFFERENT
- Regional editions (IEEE Communications US vs IEEE Communications Europe)
- Sister publications (Journal of AI vs Journal of AI Research)
- Proceedings vs Journal (VLDB Conference vs VLDB Journal)
- Transactions on different topics (IEEE Trans. on AI vs IEEE Trans. on Robotics)

### 3. Abbreviation Matching
Common patterns to recognize:
- `Trans.` = `Transactions`
- `J.` = `Journal`
- `Int.` = `International`
- `Comput.` = `Computer` / `Computing` / `Computation`
- `Syst.` = `Systems`
- `ACM` = `Association for Computing Machinery`
- `IEEE` = `Institute of Electrical and Electronics Engineers`

**IMPORTANT:** Abbreviations alone are NOT sufficient. Always verify the full scope matches.

---

## Verification Methods

Use ALL available methods to verify matches:

### Method 1: ISSN Lookup (HIGHEST PRIORITY)
- Search: `"[dblp_name]" ISSN`
- Compare found ISSN with SJR's `issn_1` or `issn_2`
- If ISSNs match → HIGH confidence
- Resources: CrossRef, Ulrichsweb, journal websites

### Method 2: Publisher Website
- Search official journal website
- Verify: full title, abbreviations used, ISSN, scope
- Check "About" or "Journal Information" pages

### Method 3: DBLP Website
- Visit: `https://dblp.org/db/journals/[dblp_key]/`
- Check: journal's full name, alternate names, homepage link
- Review sample paper titles to understand scope

### Method 4: Google Scholar / Semantic Scholar
- Search journal name
- Verify publications, editorial board, publisher
- Check if scope aligns with SJR journal description

### Method 5: Name Variations
- Check SJR `coverage` field for year ranges
- Historical name changes are common (journal renamed over time)
- Search: `"[old_name]" renamed "[new_name]"`

---

## Confidence Levels

### HIGH
- ISSN verified match, OR
- Official publisher website confirms both names refer to same journal, OR
- DBLP homepage link points to SJR journal's official site

### MEDIUM  
- Strong name similarity + same publisher + same scope, BUT no ISSN confirmation, OR
- Historical name change documented but not on official source

### LOW
- Name similarity exists but scope uncertain, OR
- Abbreviation matches but full title differs significantly, OR
- Publisher uncertain

### NONE
- No plausible match found in SJR-available.csv
- Use `sjr_title = "NO_MATCH"`

---

## Workflow

For each DBLP journal:

1. **Read DBLP journal name** from DBLP-unmatched.csv

2. **Quick scan SJR-available.csv** for obvious exact/near-exact matches

3. **If potential match found:**
   - Search online for DBLP journal ISSN
   - Verify ISSN against SJR entry
   - Check publisher website or DBLP page
   - Verify scope alignment

4. **If no obvious match:**
   - Expand search to abbreviation variants
   - Check for historical name changes
   - Search DBLP key on dblp.org for more context
   - If still no match → mark as NO_MATCH

5. **Record decision** with confidence and reasoning

---

## Special Cases

### Case 1: Multiple Plausible Matches
- Pick the ONE with strongest evidence (prefer ISSN match)
- If genuinely ambiguous → choose LOW confidence and note ambiguity in reasoning

### Case 2: Journal Name Changed
- Example: "Journal of Machine Learning Research" was formerly "Machine Learning"
- Check SJR `coverage` years - if coverage starts later, might be successor
- Search: "[old name] renamed [new name]"

### Case 3: Extremely Abbreviated DBLP Names
- Example: "CACM" → "Communications of the ACM"
- Search DBLP key on dblp.org first to get full name
- Then match full name to SJR

### Case 4: Non-English Journals
- Search journal's native language name + "ISSN"
- Many have English translated titles in SJR
- Check publisher country (from web search) aligns with SJR journal

### Case 5: Proceedings Confusion
- DBLP key starts with "journals/" but some are actually conference proceedings
- SJR only lists journals (not conferences)
- If DBLP entry is actually proceedings → mark NO_MATCH

---

## Output Format

Create CSV with this exact header:
```
dblp_key,dblp_name,sjr_title,issn_1,issn_2,quartile,coverage,confidence,reasoning
```

### Example Rows:

```csv
journals/cacm,Commun. ACM,Communications of the ACM,0001-0782,1557-7317,Q1,1958-2026,HIGH,ISSN verified via ACM website
journals/ai,Artif. Intell.,Artificial Intelligence,0004-3702,,Q1,"1968, 1970-2026",HIGH,ISSN match + publisher confirmed
journals/jmlr,J. Mach. Learn. Res.,Journal of Machine Learning Research,1532-4435,1533-7928,Q1,2001-2026,MEDIUM,Name match + scope verified but no ISSN found for DBLP
journals/obscure,Obscure J.,NO_MATCH,,,,,NONE,No matching journal found in SJR pool
```

---

## Quality Checklist

Before finalizing, verify:

- [ ] Every DBLP journal has exactly ONE output row
- [ ] No duplicate SJR journals assigned (each SJR journal matched at most once)
- [ ] HIGH confidence matches have verification evidence
- [ ] NO_MATCH used conservatively (searched thoroughly first)
- [ ] Reasoning is concise but informative
- [ ] CSV is properly formatted (no missing columns)

---

## Tools & Resources

### Recommended Search Queries:
- `"[journal_name]" ISSN site:crossref.org`
- `"[journal_name]" publisher ISSN`
- `site:dblp.org/db/journals/[dblp_key]`
- `"[journal_abbreviation]" full name computer science`

### Useful Websites:
- DBLP: https://dblp.org
- CrossRef: https://search.crossref.org
- Google Scholar: https://scholar.google.com
- Semantic Scholar: https://semanticscholar.org
- Journal websites (search publisher name)

---

## Notes

- **Precision over Recall**: If uncertain, prefer NO_MATCH over incorrect match
- **One-to-One Mapping**: Each SJR journal can only match ONE DBLP journal (first come, first served)
- **Document Ambiguity**: If two DBLP journals compete for same SJR journal, pick best match and mark other as NO_MATCH
- **Time Estimate**: ~1-3 minutes per journal depending on complexity
- **Batch Processing**: Process in alphabetical order by dblp_key for consistency

---

## Final Output Location

Save completed CSV as:
```
context/AI-matched-journals.csv
```

This file will be merged with `List-of-Journal-ISSN.csv` to create the final comprehensive journal list.
