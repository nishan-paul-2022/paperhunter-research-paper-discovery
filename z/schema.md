# Paper Hunter Schema

## Tables

### paper_abstract

| Column | Type |
|---|---|
| paper_id | INTEGER |
| abstract | TEXT |
| source | TEXT |

### paper_abstract_hit

| Column | Type |
|---|---|
| paper_id | INTEGER |
| source | TEXT |
| lookup_by | TEXT |
| request | TEXT |
| response | TEXT |
| called_at | TEXT |

### paper_abstract_miss

| Column | Type |
|---|---|
| paper_id | INTEGER |
| source | TEXT |
| lookup_by | TEXT |
| request | TEXT |
| response | TEXT |
| called_at | TEXT |

### paper_abstract_status

| Column | Type |
|---|---|
| paper_id | INTEGER |
| has_abstract | INTEGER |
| hit_count | INTEGER |
| miss_count | INTEGER |

### paper_arxiv_candidate

| Column | Type |
|---|---|
| paper_id | INTEGER |
| dblp_key | TEXT |
| title_sim | REAL |
| author_jaccard | REAL |

### paper_arxiv_match

| Column | Type |
|---|---|
| paper_id | INTEGER |
| arxiv_id | TEXT |
| method | TEXT |
| source | TEXT |
| reason | TEXT |
| decided_at | TEXT |

### paper_corr

| Column | Type |
|---|---|
| dblp_key | TEXT |
| arxiv_id | TEXT |
| title | TEXT |
| year | INTEGER |
| authors | TEXT |

### paper_dblp

| Column | Type |
|---|---|
| paper_id | INTEGER |
| dblp_key | TEXT |
| venue_key | TEXT |
| record_type | TEXT |
| title | TEXT |
| authors | TEXT |
| year | INTEGER |
| venue | TEXT |
| volume | TEXT |
| number | TEXT |
| pages | TEXT |
| doi | TEXT |
| arxiv_id | TEXT |
| ee | TEXT |
| url | TEXT |
| crossref | TEXT |
| mdate | TEXT |

### paper_embedding

| Column | Type |
|---|---|
| paper_id | INTEGER |
| vector | BLOB |

### paper_embedding_model

| Column | Type |
|---|---|
| id | INTEGER |
| model | TEXT |
| dim | INTEGER |

### paper_screen

| Column | Type |
|---|---|
| paper_id | INTEGER |
| embed_basis | TEXT |
| embed_hash | TEXT |
| similarity | REAL |
| passed_by | TEXT |
| screened | INTEGER |
| passed | INTEGER |

### paper_screen_threshold

| Column | Type |
|---|---|
| embed_basis | TEXT |
| threshold | REAL |
| computed_at | TEXT |

### paper_seed

| Column | Type |
|---|---|
| paper_id | INTEGER |
| category | TEXT |
| evidence | TEXT |

### paper_verdict_with_abstract

| Column | Type |
|---|---|
| paper_id | INTEGER |
| reviewed | INTEGER |
| verdict | TEXT |
| reason | TEXT |

### paper_verdict_without_abstract

| Column | Type |
|---|---|
| paper_id | INTEGER |
| reviewed | INTEGER |
| verdict | TEXT |
| reason | TEXT |

## Views

### paper_list

| Column | Type |
|---|---|
| paper_id | INTEGER |
| dblp_key | TEXT |
| venue_key | TEXT |
| record_type | TEXT |
| title | TEXT |
| authors | TEXT |
| year | INTEGER |
| venue | TEXT |
| volume | TEXT |
| number | TEXT |
| pages | TEXT |
| doi | TEXT |
| arxiv_id | TEXT |
| ee | TEXT |
| url | TEXT |
| crossref | TEXT |
| mdate | TEXT |
| category | TEXT |
| reason | TEXT |
| decided_from |  |
| venue_name |  |
| venue_rank |  |

### paper_lookup_queue

| Column | Type |
|---|---|
| paper_id | INTEGER |
| similarity | REAL |
| miss_count | INTEGER |
