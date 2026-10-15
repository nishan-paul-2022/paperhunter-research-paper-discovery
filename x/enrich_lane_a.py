import sys, time
from pathlib import Path
sys.path.insert(0, "/home/nishan/Documents/paperhunter-research-paper-discovery/paper-hunter")
from b_io import Run, now
from d_network import openalex_batch
from e_enrich import _merge_one

def main():
    r = Run("q1-2023")
    papers = r.papers()
    lane_a = [p for p in papers if p.get("lane") == "A" and p.get("doi") and not p.get("abstract")]
    print(f"Lane A papers to enrich: {len(lane_a)}")
    dois = [p["doi"] for p in lane_a]
    t0 = time.time()
    oa_res = openalex_batch(dois)
    print(f"OpenAlex matched {len(oa_res)} in {time.time()-t0:.1f}s")
    
    # Merge into papers
    updated = 0
    for p in papers:
        doi = p.get("doi")
        if doi in oa_res:
            rec = oa_res[doi]
            enrichment = p.setdefault("enrichment", {})
            enrichment["openalex"] = rec
            enrichment["done"] = True
            enrichment["fetched_at"] = now()
            m = p.get("manual") or {}
            _merge_one(p, enrichment, m)
            updated += 1
            
    print(f"Updated {updated} papers with OpenAlex metadata.")
    r.save_papers()
    print("Saved papers.jsonl successfully.")

if __name__ == "__main__":
    main()
