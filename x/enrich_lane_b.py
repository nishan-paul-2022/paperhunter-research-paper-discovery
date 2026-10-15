import sys, time
from pathlib import Path
sys.path.insert(0, "/home/nishan/Documents/paperhunter-research-paper-discovery/paper-hunter")
from b_io import Run, now
from d_network import openalex_batch
from e_enrich import _merge_one

def main():
    r = Run("q1-2023")
    papers = r.papers()
    lane_b = [p for p in papers if p.get("lane") == "B" and p.get("doi") and not p.get("abstract")]
    print(f"Lane B papers to enrich: {len(lane_b)}")
    dois = [p["doi"] for p in lane_b]
    t0 = time.time()
    oa_res = openalex_batch(dois)
    print(f"OpenAlex matched {len(oa_res)} in {time.time()-t0:.1f}s")


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

    print(f"Updated {updated} Lane B papers with OpenAlex metadata.")
    r.save_papers()
    print("Saved papers.jsonl successfully.")

if __name__ == "__main__":
    main()
