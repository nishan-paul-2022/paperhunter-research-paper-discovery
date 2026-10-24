# Paper Hunter Schema

## Tables

### paper_dblp [archive/dblp.xml, conf_list, journal_list]

| Column | Type |
|---|---|
| paper_id | INTEGER |
| dblp_key | TEXT |
| venue_key | TEXT |
| title | TEXT |
| authors | TEXT |
| year | INTEGER |
| venue | TEXT |
| doi | TEXT |

### paper_field_arxiv

| Column | Type |
|---|---|
| paper_id | INTEGER |
| arxiv_id | TEXT |

### paper_field_abstract

| Column | Type |
|---|---|
| paper_id | INTEGER |
| abstract | TEXT |

### paper_details_arxiv

| Column | Type |
|---|---|
| paper_id | INTEGER |
| request | TEXT |
| response | TEXT |

### paper_details_openalex

| Column | Type |
|---|---|
| paper_id | INTEGER |
| request | TEXT |
| response | TEXT |

### paper_details_s2

| Column | Type |
|---|---|
| paper_id | INTEGER |
| request | TEXT |
| response | TEXT |

### paper_semantic_model

| Column | Type |
|---|---|
| id | INTEGER |
| model | TEXT |
| dim | INTEGER |

### paper_semantic_keyword

| Column | Type |
|---|---|
| keyword | TEXT |

### paper_semantic_query

| Column | Type |
|---|---|
| paper_id | INTEGER |
| category | TEXT |
| evidence | TEXT |

### paper_semantic_seed

| Column | Type |
|---|---|
| paper_id | INTEGER |
| category | TEXT |
| evidence | TEXT |

### paper_semantic_vector

| Column | Type |
|---|---|
| paper_id | INTEGER |
| vector | BLOB |

### paper_semantic_shortlist

| Column | Type |
|---|---|
| paper_id | INTEGER |

### paper_semantic_threshold

| Column | Type |
|---|---|
| embed_basis | TEXT |
| threshold | REAL |

### paper_ai_shortlist

| Column | Type |
|---|---|
| paper_id | INTEGER |
| category | TEXT |

## Views

### paper_absent_abstract [paper_field_abstract]

| Column | Type |
|---|---|
| paper_id | INTEGER |

### paper_list [paper_dblp, paper_field_arxiv, paper_field_abstract, paper_ai_shortlist, conf_list, journal_list]

| Column | Type |
|---|---|
| paper_id | INTEGER |
| dblp_key | TEXT |
| venue_key | TEXT |
| title | TEXT |
| authors | TEXT |
| year | INTEGER |
| venue | TEXT |
| doi | TEXT |
| arxiv_id | TEXT |
| abstract | TEXT |
| category | TEXT |
| venue_name | TEXT |
| venue_rank | TEXT |

### paper_summary [paper_dblp, paper_field_arxiv, paper_field_abstract, paper_details_arxiv, paper_details_openalex, paper_details_s2, conf_list, journal_list, paper_list]

| Column | Type |
|---|---|
| paper_id | INTEGER |
| dblp_key | TEXT |
| venue_key | TEXT |
| title | TEXT |
| authors | TEXT |
| year | INTEGER |
| venue | TEXT |
| doi | TEXT |
| arxiv_id | TEXT |
| abstract | TEXT |
| request_arxiv | TEXT |
| response_arxiv | TEXT |
| request_openalex | TEXT |
| response_openalex | TEXT |
| request_s2 | TEXT |
| response_s2 | TEXT |
| venue_name | TEXT |
| venue_rank | TEXT |
| category | TEXT |

a_extract.py => complete paper_dblp and populate paper_id in paper_field_arxiv, paper_field_abstract, paper_details_arxiv, paper_details_openalex, paper_details_s2
b_arxiv.py => put all possible values in paper_field_arxiv
c_fetch.py => put all possible values in paper_field_arxiv, paper_field_abstract, paper_details_arxiv, paper_details_openalex, paper_details_s2
d_abstract.md => put all possible values in paper_field_abstract
e_embedding.py => complete paper_semantic_vector
f_semantic.py => complete paper_semantic_shortlist
g_search.md => complete paper_ai_shortlist
