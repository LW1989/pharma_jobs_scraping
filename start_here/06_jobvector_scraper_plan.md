# Jobvector Scraper — Plan

## What is jobvector.de?

[jobvector.de](https://www.jobvector.de) is Germany's specialist job board for MINT
(science, engineering, IT, medicine) roles. It is a strong complement to pharmiweb.jobs:

| | pharmiweb.jobs | jobvector.de |
|---|---|---|
| Focus | Global pharma/CRO | German-market MINT + life sciences |
| Language | English | German |
| Salary data | Rarely shown | Almost always shown (estimated or exact) |
| Skills/tags | Not on listings | Shown on every listing card |
| Remote label | "Homeworking" keyword | "Home Office (optional)" / "Home Office (100 %)" |

---

## URL structure (tested & confirmed)

### Listing URLs — discipline-specific with date sort

```
https://www.jobvector.de/jobs/{discipline}/?sort=dateStart&filter=301&filter=322
```

- `sort=dateStart` — newest jobs appear first ✅
- `filter=301` — Festanstellung (permanent) ✅
- `filter=322` — Vollzeit (full time) ✅
- `filter=331` — Akademischer Abschluss (academic degree) — **intentionally omitted**
  from discipline URLs because pharma/biotech disciplines already imply academic
  backgrounds and adding it may silently drop some relevant jobs.

### Catch-all URL — all disciplines, date sorted

```
https://www.jobvector.de/jobs?sort=dateStart&pn=1
https://www.jobvector.de/jobs?sort=dateStart&pn=2
https://www.jobvector.de/jobs?sort=dateStart&pn=3
```

`pn=N` is the **working** server-side pagination parameter. `page=N` is silently ignored.

### Job detail pages

```
https://www.jobvector.de/job/{slug}-{hash}/
```
Example: `https://www.jobvector.de/job/biologe-26725ecec6db5369/`

**Job ID:** the 16-character hex hash at the end of the slug — `26725ecec6db5369`.

---

## Pagination — key findings

| Approach | Result |
|---|---|
| `?page=N` | ❌ silently ignored — all values return the same first page |
| `?pn=N` | ✅ true server-side pagination — 0 overlap between pn=1, pn=2, pn=3 |
| `?pn=N` on discipline URLs | ⚠️ works structurally but page 2 returns off-topic noise (the discipline has < 40 jobs so the server falls back to similar disciplines) — **use pn=1 only on discipline URLs** |
| Playwright headless browser | ❌ blocked by Cloudflare Turnstile bot detection |
| Internal API `/api/jobvector/` | ❌ analytics events only — no job listing data |

---

## Data available

### From the listing page (no detail page visit needed)

| Field | Example | DB column |
|---|---|---|
| `job_id` | `26725ecec6db5369` | `job_id` (PK) |
| `title` | Biologe, Chemiker als Projektmanager - Klinische Studien (m/w/d) | `title` |
| `employer` | Frankfurter Institut für Klinische Krebsforschung IKF GmbH | `employer` |
| `location` | Frankfurt am Main, Hessen | `location` |
| `remote` | True / False (whether "Home Office" appears in card) | append to `location` |
| `date` | 26.02.2026 | `start_date` |
| `salary` | 60.981 €/Jahr (geschätzt) | `salary` |
| `tags` | Klinische Studien, GCP, AMG, CTR | prepend to `job_details` |
| `url` | https://www.jobvector.de/job/biologe-26725ecec6db5369/ | `url` (new column) |
| `discipline` | `klinische-studien` | `discipline` (new column) |

> **Tags decision:** No new `tags` column needed. Prepend the tags as a comma-separated
> line at the top of `job_details` — e.g. `"Tags: Klinische Studien, GCP, AMG\n\n<description>"`.
> This makes tags visible to the LLM evaluator without schema changes.

> **`discipline` column:** Add as `VARCHAR(64) DEFAULT NULL`. Tracks which URL the job
> came from. Useful for debugging and future filtering. Catch-all jobs get
> `discipline = 'catchall'`.

### Additional fields from the detail page

| Field | Example | DB column |
|---|---|---|
| `job_details` | Full German job description | `job_details` |
| `contract_type` | Festanstellung | `contract_type` |
| `hours` | Vollzeit | `hours` |
| `experience_level` | Fachkraft / Senior / Bereichs- & Abteilungsleitung | `experience_level` |

---

## HTML structure (BeautifulSoup targets)

> **All selectors confirmed from live HTML.** Site uses Vue.js (`data-v-*`) but
> the semantic CSS classes are stable for `requests` + `BeautifulSoup`.

### Listing page — each job card

```python
cards = soup.select("article[data-jobid]")
```

| Field | Selector | Notes |
|---|---|---|
| `job_id` | `card["data-jobid"]` | 16-char hex |
| `url` | `card.select_one("a.vacancy-title-anchor")["href"]` | |
| `title` | `card.select_one("h2").get_text(strip=True)` | |
| `employer` | `card.select_one("span.company-name-text").get_text(strip=True)` | |
| `location` | `card.select_one(".locations-loop-inside-wrapper").get_text(strip=True)` | |
| `remote` | `"Home Office" in card.get_text()` | bool |
| `date` | `card.select_one("span.date span").get_text(strip=True)` | always `DD.MM.YYYY` dots |
| `salary` | first `span.inline-item` containing `€` | raw string |
| `tags` | `[d.get_text(strip=True) for d in card.select("div.entity.entity-background")]` | list |

**Date parsing:** always `DD.MM.YYYY` with dots in the actual HTML. The `.replace("/", ".")`
normalisation is kept as a safety guard but is usually a no-op.

```python
date_str = date_raw.replace("/", ".")
dt = datetime.strptime(date_str, "%d.%m.%Y").date()
```

### Pagination — page count for catch-all URL

```python
# Use pn= not page=
pn_links = soup.select("a[href*='pn=']")
max_pn = max(int(re.search(r'pn=(\d+)', a["href"]).group(1)) for a in pn_links)
```

### Detail page

```python
body_text = soup.get_text(" ", strip=True)

# Contract type + hours: "Vollzeit , Festanstellung" near the bottom
contract_match = re.search(r'(Vollzeit|Teilzeit)[^\n]*?(Festanstellung|Befristete Anstellung)', body_text)

# Experience level
exp_match = re.search(r'(Fachkraft|Senior|Führungskraft|Bereichs.*?leitung|Berufseinsteiger)', body_text)

# JV integer ID — note two spaces before the number
jv_id_match = re.search(r'jobvector ID\s+(\d+)', body_text)

# Description: main content div
desc_div = soup.select_one("div.job-details") or soup.select_one("main")
```

---

## Integration into the existing codebase

### Compatibility analysis vs. existing pharmiweb schema

The schema is defined in `scraper/db.py` (`CREATE_TABLE_SQL`). Cross-checking each planned column against it:

| Column | Status | Notes |
|---|---|---|
| `url` | **Already exists** (`TEXT NOT NULL`) | Do NOT add via migration — it is already in `insert_job` |
| `discipline` | **Already exists** (`TEXT`) | Pharmiweb stores the professional label ("Project Management"); jobvector will store the URL slug ("klinische-studien"). Mixed formats but neither the evaluator nor reporter uses this field for logic — stored for display only. |
| `source` | **Missing — add via migration** | Must also be added to the `insert_job` SQL in `scraper/db.py` |

### DB changes — one new column only

```sql
-- Migration to add to scripts/migrate_db.py
ALTER TABLE jobs
  ADD COLUMN IF NOT EXISTS source VARCHAR(32) DEFAULT 'pharmiweb';
```

- Existing pharmiweb rows get `source = 'pharmiweb'` automatically via the DEFAULT.
- `discipline` and `url` are already present — no change needed.

The `job_id` primary key stays unique: pharmiweb uses integers (`2138823`),
jobvector uses hex hashes (`26725ecec6db5369`).

### ⚠️ Critical: pharmiweb scraper "mark inactive" logic must be scoped

`run_scraper.py` currently:
1. Calls `get_all_job_ids()` → returns **every** job in the DB (including future jobvector jobs).
2. Computes `gone_ids = db_ids - live_pharmiweb_ids`.
3. Calls `mark_jobs_inactive(gone_ids)`.

**This would silently mark every jobvector job as inactive** on each pharmiweb run, because no jobvector job ever appears in pharmiweb search results.

**Fix required in `scraper/db.py`:** Add a source-scoped query:
```python
def get_job_ids_by_source(source: str) -> set[str]:
    """Return job IDs for a specific source only."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT job_id FROM jobs WHERE source = %s OR (source IS NULL AND %s = 'pharmiweb')",
            (source, source),
        )
        return {row["job_id"] for row in cur.fetchall()}
```

Then in `run_scraper.py` replace `get_all_job_ids()` with `get_job_ids_by_source('pharmiweb')`.

### ⚠️ Critical: jobvector cannot use the same "mark inactive" approach

Pharmiweb scrapes ALL pages, so a missing job_id genuinely means the job is gone.
Jobvector only scrapes page 1 of each discipline — a job not seen today may simply have scrolled off page 1 but still be live.

**Strategy for jobvector `job_active` tracking:**
- Never call `mark_jobs_inactive` from `run_scraper_jobvector.py`.
- Instead, add a cleanup step (either in `run_scraper_jobvector.py` or `run_pipeline.sh`):
  ```sql
  UPDATE jobs SET job_active = FALSE
  WHERE source = 'jobvector'
    AND last_seen < NOW() - INTERVAL '30 days';
  ```
- Jobs re-seen on a future run will have their `last_seen` refreshed and can be re-activated via `mark_jobs_active`.

### `insert_job` in `scraper/db.py` must accept `source`

The current `insert_job` INSERT SQL does not include `source`. Options:
- **Preferred**: Add `source` to the INSERT with `job.get("source", "pharmiweb")` as the default.
  Pharmiweb passes no `source` key → defaults to `'pharmiweb'`; jobvector passes `source='jobvector'`.
- This keeps one shared function, backward-compatible.

### New files

```
scraper/
├── scraper.py               ← existing pharmiweb scraper (unchanged)
└── jobvector_scraper.py     ← new

run_scraper_jobvector.py     ← new entry point (mirrors run_scraper.py, WITHOUT mark_inactive step)
```

### Evaluator / reporter changes

**None required.** Both modules query `jobs` regardless of `source`; the `source` column is just metadata.
The LLM handles German job descriptions natively.

The existing `location_keywords` prescreener filter already works:
`"Home"` matches both `"Homeworking"` (pharmiweb) and `"Home Office"` (jobvector).

---

## Full URL lists for implementation

```python
REQUEST_DELAY_SECONDS = 1.0

DISCIPLINE_URLS = [
    # Core pharma / biotech
    "https://www.jobvector.de/jobs/biochemie/?sort=dateStart&filter=301&filter=322",
    "https://www.jobvector.de/jobs/biotechnologie/?sort=dateStart&filter=301&filter=322",
    "https://www.jobvector.de/jobs/biologie/?sort=dateStart&filter=301&filter=322",
    "https://www.jobvector.de/jobs/chemie/?sort=dateStart&filter=301&filter=322",
    "https://www.jobvector.de/jobs/pharmazie/?sort=dateStart&filter=301&filter=322",
    "https://www.jobvector.de/jobs/bioinformatik/?sort=dateStart&filter=301&filter=322",
    "https://www.jobvector.de/jobs/molekularbiologie/?sort=dateStart&filter=301&filter=322",
    # Clinical research
    "https://www.jobvector.de/jobs/klinische-studien/?sort=dateStart&filter=301&filter=322",
    "https://www.jobvector.de/jobs/clinical-research/?sort=dateStart&filter=301&filter=322",
    "https://www.jobvector.de/jobs/medizintechnik/?sort=dateStart&filter=301&filter=322",
    "https://www.jobvector.de/jobs/medical-affairs/?sort=dateStart&filter=301&filter=322",
    # Quality / regulatory
    "https://www.jobvector.de/jobs/qualitaetsmanagement/?sort=dateStart&filter=301&filter=322",
    "https://www.jobvector.de/jobs/qualitaetssicherung/?sort=dateStart&filter=301&filter=322",
    "https://www.jobvector.de/jobs/regulatory-affairs/?sort=dateStart&filter=301&filter=322",
    "https://www.jobvector.de/jobs/laborant/?sort=dateStart&filter=301&filter=322",
    # Project management / cross-functional
    "https://www.jobvector.de/jobs/projektmanagement/?sort=dateStart&filter=301&filter=322",
    "https://www.jobvector.de/jobs/medizin/?sort=dateStart&filter=301&filter=322",
]

# 3 catch-all pages — captures pharma jobs in disciplines not listed above
# ~5/21 pharma-relevant per page, rest filtered by prescreener (no LLM cost)
CATCHALL_URLS = [
    "https://www.jobvector.de/jobs?sort=dateStart&pn=1",
    "https://www.jobvector.de/jobs?sort=dateStart&pn=2",
    "https://www.jobvector.de/jobs?sort=dateStart&pn=3",
]
```

**Expected daily volume:**
- 17 discipline URLs × ~20 cards = ~340 raw
- 3 catch-all pages × ~21 cards = ~63 raw
- **~400 raw total → ~180–220 unique after deduplication**

### Slugs excluded (with reasons)

| Slug | Reason |
|---|---|
| `klinische-forschung` | Returns identical results to `klinische-studien` |
| `studiomanagement` | Returns unrelated general jobs despite the name |
| `physik` | Physics/optics, minimal pharma overlap |
| `lebensmitteltechnologie` | Food industry focus |
| `gesundheitsmanagement` | Hospital/nursing management |
| `verfahrenstechnik` | Chemical/petrochemical process engineering |
| `nachhaltigkeit` | Sustainability sector |
| `toxikologie` | Only 1 job total |
| `pharmakologie` | Only 3 jobs total |

---

## Implementation todos (for coding agent)

1. **DB migration** — add only `source VARCHAR(32) DEFAULT 'pharmiweb'` in `scripts/migrate_db.py`
   - `url` and `discipline` already exist — do NOT re-add them
2. **Update `scraper/db.py`**:
   - Add `get_job_ids_by_source(source: str) -> set[str]` function
   - Add `source` to `insert_job` INSERT SQL (use `job.get("source", "pharmiweb")` as default)
3. **Update `run_scraper.py`**:
   - Replace `get_all_job_ids()` with `get_job_ids_by_source('pharmiweb')` so pharmiweb's
     "mark inactive" step does NOT touch jobvector jobs
4. **Write `scraper/jobvector_scraper.py`**:
   - Loop over `DISCIPLINE_URLS` + `CATCHALL_URLS`
   - Parse listing cards with BeautifulSoup selectors above
   - Fetch each detail page for `job_details`, `contract_type`, `hours`, `experience_level`
   - Prepend tags to `job_details`: `f"Tags: {', '.join(tags)}\n\n{description}"`
   - Set `source='jobvector'`, `discipline=<slug>` (or `'catchall'`) on every insert
   - Use `insert_job` (new jobs) + `mark_jobs_active` (seen again); never call `mark_jobs_inactive`
5. **Write `run_scraper_jobvector.py`** — mirrors `run_scraper.py` but **no mark-inactive step**;
   instead, runs a 30-day age-based deactivation query at the end
6. **Update `deploy/run_pipeline.sh`** — add `python run_scraper_jobvector.py` before `run_evaluator.py`
7. **Update `requirements.yaml`** — no location filter changes needed; `"Home"` already matches both sources
8. **Test** — run against 2 discipline URLs + 1 catch-all page, verify deduplication, inspect 3 jobs in DB
