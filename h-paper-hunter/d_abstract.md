# Master Prompt: D · Find Missing Abstracts (Paper Hunter)

You are an autonomous research assistant with shell and web search access. Complete this task end to end with no human help and no questions to the user.

**Goal.** Move every paper at status `C` to status `D`. For each paper without an abstract, find its own abstract online and store it, but only when you are certain the text is the abstract of that exact paper. A missing abstract is a correct outcome; a wrong one is not.

## Ground rules

- Work from the repository root `/home/nishan/Documents/paperhunter-research-paper-discovery`. The database is `a-database/paperhunter.db`.
- Change the database only with the commands in Step 1 and Step 2c. They write only `paper_field_abstract.abstract` and `paper_status.status`.
- Read pages only with the fetch command in Step 2b. Never take abstract text from web search result snippets or from any tool that summarizes or rewrites pages: the text must be copied exactly from the page.
- Never get around a bot check, login, paywall or captcha. A blocked page is a dead end: move on.
- Do not use OpenAlex, Semantic Scholar, arXiv or Crossref: step C already tried them.
- Never guess, paraphrase, translate, summarize, complete or generate an abstract.

## Step 1: Move papers that already have an abstract

```sh
sqlite3 a-database/paperhunter.db "
PRAGMA foreign_keys = ON;
UPDATE paper_status SET status = 'D'
WHERE status = 'C' AND paper_id NOT IN (SELECT paper_id FROM paper_review);
SELECT changes() || ' papers with an abstract moved C → D';"
mkdir -p /tmp/paperhunter-d
```

## Step 2: Find the missing abstracts, one paper at a time

### 2a. Read the next batch

```sh
sqlite3 -readonly -separator ' | ' a-database/paperhunter.db "
SELECT d.paper_id, coalesce(d.doi, '-'), d.year, d.venue, d.title,
       coalesce(json_extract(d.authors, '\$[0]'), '-') || coalesce(', ' || json_extract(d.authors, '\$[1]'), '') || coalesce(', ' || json_extract(d.authors, '\$[2]'), '')
         || CASE WHEN json_array_length(d.authors) > 3 THEN ' +' || (json_array_length(d.authors) - 3) ELSE '' END,
       coalesce((SELECT group_concat(value, ' ') FROM json_each(d.ee) WHERE value NOT LIKE 'https://doi.org/%'), '-')
FROM paper_status s JOIN paper_field_abstract a USING (paper_id) JOIN paper_dblp d USING (paper_id)
WHERE s.status = 'C' AND a.abstract IS NULL
ORDER BY s.paper_id LIMIT 20;"
```

Columns: `paper_id | doi | year | venue | title | first 3 authors (+ how many more) | the paper's own links other than its DOI`. Work through the 20 papers in order, then read the next batch. Stop when the batch is empty.

### 2b. Look for the abstract

Try these sources in order and stop at the first that passes the certainty test (2c):

1. The paper's own links (last column).
2. Its DOI page `https://doi.org/<doi>`. Skip it for DOIs starting `10.1016/` (Elsevier), `10.1007/` (Springer) and `10.1109/` (IEEE): those sites always block.
3. **One** web search: the exact title in double quotes. Open at most **2** results, best first:
   - author, lab or university pages and repositories (eprints, DSpace, Pure, HAL, institutional PDFs), the publisher's own page, library catalogs, conference sites, PDFs of the paper;
   - never ResearchGate, Academia.edu, Google Scholar, Semantic Scholar, OpenAlex, arXiv, Crossref, or sites that list many papers with generated summaries.

At most 4 fetches per paper in total. Fetch each page with this command, putting the URL in `u`:

```sh
u='<url>'; f=/tmp/paperhunter-d/page; rm -f $f
curl -sL --compressed --max-time 40 -A 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36' -o $f -w 'HTTP %{http_code} | %{url_effective} | %{content_type} | %{size_download} bytes\n' "$u"
if file -b $f | grep -q PDF; then pdftotext -l 2 $f - 2>/dev/null | tr -s ' \n\r\t' ' ' | LC_ALL=C grep -oiE '.{0,300}abstract.{0,2500}' | head -c 2800
else tr -s '\r\n\t' '   ' < $f | LC_ALL=C grep -oiE '<title>[^<]{0,300}|<meta [^>]*(citation_title|citation_author"|citation_doi|description|abstract)[^>]*>|<[a-z0-9]+ [^>]*(id|class)="[^"]*abstract[^"]*"[^>]*>.{0,3000}|<h[1-6][^>]*> *(abstract|summary) *</h[1-6]>.{0,3000}|"abstract" ?: ?"[^"]{0,3000}' | sed -E '/^<meta|^<title/!{s/<[^>]+>/ /g; s/ +/ /g}' | cut -c1-2500 | head -n 16; fi
```

It prints the HTTP status, final URL and size, then the page's title, author and DOI tags and every abstract candidate (for a PDF: the text around "Abstract" on the first two pages). If the output is empty or shows a bot check ("Client Challenge", "Just a moment", "Access denied", "Redirecting", 0 bytes, HTTP 202 or 403), the page is blocked: go to the next source. If a candidate is cut off at 2500 characters, fetch a better source rather than piecing it together.

### 2c. Certainty test, then record

Store an abstract only if **all four** hold:

1. **Same paper:** the page's title matches the paper's title (ignoring case, punctuation and a trailing period), or the page shows the paper's DOI.
2. **Same authors:** at least one author surname on the page matches one of the listed authors. A PDF's first page counts.
3. **Its own abstract:** the text is this paper's abstract, not a related paper's, a book or proceedings description, a journal's scope text, a table of contents, or a search snippet.
4. **Complete:** it starts at the beginning of the abstract and ends at its last sentence, not cut off and not ending in "..." or "…".

If it passes, write the abstract as one line to `/tmp/paperhunter-d/abstract.txt`, copied exactly from the fetch output. The only changes allowed: drop a leading "Abstract", "Abstract:" or "Summary" label, turn HTML entities into their characters (`&amp;` → `&`), join PDF line breaks and hyphenated line ends, and leave out trailing keywords, copyright lines and "Introduction". Use a quoted heredoc so the shell leaves the text untouched:

```sh
cat > /tmp/paperhunter-d/abstract.txt <<'ABSTRACT'
<the exact abstract on one line>
ABSTRACT
```

Then save it and move the paper to `D` in one transaction, putting the paper's ID in `id`:

```sh
id=<paper_id>
sqlite3 a-database/paperhunter.db "
PRAGMA foreign_keys = ON;
BEGIN;
CREATE TEMP TABLE new AS SELECT trim(replace(replace(replace(CAST(readfile('/tmp/paperhunter-d/abstract.txt') AS TEXT), char(13), ' '), char(10), ' '), char(9), ' ')) AS t;
UPDATE paper_field_abstract SET abstract = (SELECT t FROM new)
WHERE paper_id = $id AND abstract IS NULL
  AND paper_id IN (SELECT paper_id FROM paper_status WHERE status = 'C')
  AND (SELECT length(t) - length(replace(t, ' ', '')) >= 19 AND t NOT LIKE '%...' AND t NOT LIKE '%…' FROM new);
SELECT 'abstract saved: ' || changes();
UPDATE paper_status SET status = 'D'
WHERE paper_id = $id AND status = 'C'
  AND paper_id IN (SELECT paper_id FROM paper_field_abstract WHERE abstract IS NOT NULL);
SELECT 'moved to D: ' || changes();
COMMIT;"
rm -f /tmp/paperhunter-d/abstract.txt
```

It must print `abstract saved: 1` and `moved to D: 1`. `0` means the text was rejected (under 20 words or cut off): fix it from the page, or record the paper as not found.

If no source passes the test, move the paper to `D` without an abstract:

```sh
id=<paper_id>
sqlite3 a-database/paperhunter.db "
UPDATE paper_status SET status = 'D' WHERE paper_id = $id AND status = 'C';
SELECT 'moved to D without abstract: ' || changes();"
```

An interrupted session resumes where it stopped: finished papers are already at `D`, so 2a lists only the rest.

## Finish

```sh
sqlite3 -readonly a-database/paperhunter.db "
SELECT status, count(*) FROM paper_status GROUP BY status;
SELECT 'at D with an abstract', count(*) FROM paper_status s JOIN paper_field_abstract a USING (paper_id) WHERE s.status = 'D' AND a.abstract IS NOT NULL;
SELECT 'at D without an abstract', count(*) FROM paper_status s JOIN paper_field_abstract a USING (paper_id) WHERE s.status = 'D' AND a.abstract IS NULL;"
```

Report the counts, how many abstracts this session found, and how many papers ended without one.

**Next:** run `h-paper-hunter/e_embed.py`.
