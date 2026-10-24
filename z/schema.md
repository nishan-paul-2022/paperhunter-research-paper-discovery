# Paper Hunter Schema

## Tables

### paper_list_dblp

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

### paper_screen_vector

| Column | Type |
|---|---|
| paper_id | INTEGER |
| vector | BLOB |

### paper_screen_model

| Column | Type |
|---|---|
| id | INTEGER |
| model | TEXT |
| dim | INTEGER |

### paper_screen_shortlist

| Column | Type |
|---|---|
| paper_id | INTEGER |

### paper_screen_threshold

| Column | Type |
|---|---|
| embed_basis | TEXT |
| threshold | REAL |

### paper_screen_seed

| Column | Type |
|---|---|
| paper_id | INTEGER |
| category | TEXT |
| evidence | TEXT |

## Views

### paper_absent_abstract

| Column | Type |
|---|---|
| paper_id | INTEGER |

### paper_list

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
| category | TEXT |
| venue_name | TEXT |
| venue_rank | TEXT |

### paper_summary

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
