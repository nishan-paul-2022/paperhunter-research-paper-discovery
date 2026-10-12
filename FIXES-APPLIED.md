# Fixes Applied to Paper Extraction Workflow

## Date: 2026-01-12

## Issues Fixed

### Issue 1: FSE/SIGSOFT Aliasing (FIXED ✅)
**Problem:** DBLP uses `conf/sigsoft` for modern FSE papers (2018+), but we only had `conf/fse` in our conference list.

**Solution:** Added explicit mapping in `conference-hunter/b_match.py`
```python
'conf/sigsoft': ('ACM International Conference on the Foundations of Software Engineering...', 'HIGH', 
                 'SIGSOFT is DBLP key for modern FSE papers (2018+), conf/fse is for older papers')
```

**Impact:**
- Will recover hackingBuddyGPT paper (FSE 2023)
- Will recover ~3,717 SIGSOFT papers (includes modern FSE)

---

### Issue 2: Journal-Published Conferences (FIXED ✅)

**Problem:** Some conferences publish papers in journals (as `<article>` tags), not as `<inproceedings>`. These were being extracted as journals but needed to be added to our journal matching.

**Solution:** Added explicit mappings in `journal-hunter/b_match.py`
```python
'journals/pacmse': ('ACM Transactions on Software Engineering and Methodology', 'HIGH', 
                    'PACMSE includes FSE conference papers published as journal articles'),
'journals/pacmpl': ('Proceedings of the ACM on Programming Languages', 'HIGH', 
                    'PACMPL includes POPL/PLDI conference papers published as journal articles'),
'journals/pacmmod': ('Proceedings of the ACM on Management of Data', 'HIGH', 
                     'PACMMOD includes SIGMOD conference papers published as journal articles'),
'journals/pomacs': ('Proceedings of the ACM on Measurement and Analysis of Computing Systems', 'HIGH', 
                    'POMACS includes SIGMETRICS conference papers published as journal articles'),
```

**Impact:**
- Will recover thousands of conference papers from:
  - FSE (journals/pacmse)
  - POPL/PLDI (journals/pacmpl)
  - SIGMOD (journals/pacmmod)
  - SIGMETRICS (journals/pomacs)
  - VLDB (journals/pvldb - already existed)
  - SIGGRAPH (journals/tog - already existed)

---

## Not Fixed (By Design)

### Issue 3: MDPI Applied Sciences ❌ **CANNOT FIX**

**Problem:** MDPI *Applied Sciences* is Q2 in SJR but papers cannot be found in DBLP.

**Root Cause:** DBLP does not index MDPI journals (policy decision). MDPI journals are open-access predatory-adjacent publishers often excluded from DBLP.

**Verification:**
```bash
grep "2076-3417" dblp.xml    # ISSN not found
grep "journals/applsci" dblp.xml  # Journal key not found
```

**Conclusion:** This is expected behavior. Papers from MDPI journals cannot be extracted because they don't exist in DBLP XML.

---

## Testing the Fixes

### Step 1: Re-run Conference Extraction
```bash
cd /home/nishan/Documents/paperhunter-research-paper-discovery
python3 conference-hunter/a_extract.py
```

**Expected:** No change (still 656 exact matches from Phase 1)

### Step 2: Re-run Conference Fuzzy Matching
```bash
python3 conference-hunter/b_match.py
```

**Expected:** `conf/sigsoft` will now match → +1 HIGH confidence match

### Step 3: Re-run Conference Merge
```bash
python3 conference-hunter/c_merge.py
```

**Expected:** `list-of-conferences.csv` will include conf/sigsoft entries

### Step 4: Re-run Journal Extraction
```bash
python3 journal-hunter/a_extract.py
```

**Expected:** Will now extract pacmse, pacmpl, pacmmod, pomacs journal articles

### Step 5: Re-run Journal Fuzzy Matching
```bash
python3 journal-hunter/b_match.py
```

**Expected:** These 4 journals will match to SJR Q1 entries

### Step 6: Re-run Journal Merge
```bash
python3 journal-hunter/c_merge.py
```

**Expected:** `list-of-journals.csv` will include these 4 journal-conferences

### Step 7: Re-run Paper Extraction
```bash
python3 database/paper.py
```

**Expected:**
- `a-star-2023.csv` will include hackingBuddyGPT paper
- `q1-*.csv` files will include FSE/POPL/PLDI/SIGMOD/SIGMETRICS papers
- Total papers: ~1,100,000+ (up from 1,024,885)

---

## Validation

### Test for hackingBuddyGPT Paper
```bash
grep -i "getting pwn" papers/a-star-2023.csv
```

**Expected:** Should find the paper

### Test for FSE Journal Papers
```bash
grep "journals/pacmse" papers/q1-*.csv | wc -l
```

**Expected:** Should find FSE papers

### Test for POPL/PLDI Papers
```bash
grep "journals/pacmpl" papers/q1-*.csv | wc -l
```

**Expected:** Should find POPL/PLDI papers

---

## Summary

| Fix | File Modified | Lines Added | Impact |
|-----|--------------|-------------|--------|
| conf/sigsoft alias | conference-hunter/b_match.py | 1 | +3,717 conference papers |
| journals/pacmse | journal-hunter/b_match.py | 1 | +FSE journal papers |
| journals/pacmpl | journal-hunter/b_match.py | 1 | +POPL/PLDI papers |
| journals/pacmmod | journal-hunter/b_match.py | 1 | +SIGMOD papers |
| journals/pomacs | journal-hunter/b_match.py | 1 | +SIGMETRICS papers |

**Total Impact:** ~75,000+ papers recovered (estimated)

---

## Next Steps

1. Run full workflow:
```bash
# Conferences
python3 conference-hunter/a_extract.py
python3 conference-hunter/b_match.py
python3 conference-hunter/c_merge.py

# Journals
python3 journal-hunter/a_extract.py
python3 journal-hunter/b_match.py
python3 journal-hunter/c_merge.py

# Papers
python3 database/paper.py
```

2. Verify fixes:
```bash
grep -i "getting pwn" papers/a-star-2023.csv
grep "journals/pacmse" papers/q1-*.csv | wc -l
```

3. Update documentation with actual results

---

**Status:** ✅ All fixable issues addressed in code
**Ready to test:** YES
