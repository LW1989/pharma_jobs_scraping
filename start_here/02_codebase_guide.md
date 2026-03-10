# Codebase Guide

## File map

```
pharma_jobs_scraping/
│
├── run_scraper.py          ← Daily entry point (called by cron or manually)
├── setup_db.py             ← One-time schema creation; safe to re-run
│
├── scraper/
│   ├── config.py           ← All settings loaded from .env
│   ├── db.py               ← Every database operation lives here
│   └── scraper.py          ← Every HTTP request and HTML parse lives here
│
├── .env                    ← Git-ignored; your actual credentials
├── .env.example            ← Template for Hetzner production
├── .env.local.example      ← Template for local Docker dev
├── requirements.txt
└── start_here/             ← You are here
```

---

## scraper/config.py

Loads `.env` via `python-dotenv` at import time. Exposes:

| Variable | Description |
|---|---|
| `DB_HOST/PORT/NAME/USER/PASSWORD` | PostgreSQL connection details |
| `SEARCH_BASE_URL` | The pharmiweb.jobs Europe search URL (no page param) |
| `JOB_BASE_URL` | `https://www.pharmiweb.jobs/job/` |
| `REQUEST_DELAY_SECONDS` | Sleep between HTTP requests (currently `0.5`) |
| `REQUEST_TIMEOUT_SECONDS` | HTTP timeout (30s) |
| `HEADERS` | Browser-like User-Agent header |

---

## scraper/scraper.py

Two public functions used by `run_scraper.py`:

### `scrape_all_job_links() -> dict[str, str]`
Returns `{job_id: full_url}` for every job currently listed on the site.

How it works:
1. Fetches page 1 of the search URL.
2. Reads `<a aria-label="Last page">` to find the total page count (currently 49).
3. Loops pages 1–N, extracts all `/job/{id}/` hrefs with a regex, sleeps 0.5s between pages.

### `scrape_job_detail(job_id, url) -> dict`
Returns a dict ready to pass to `db.insert_job()`.

How it works:
1. Fetches the individual job page.
2. Extracts title from `<h1>`.
3. Extracts structured metadata (employer, location, salary, dates, discipline, hours, contract type, experience level) by matching `<dt>`/`<dd>` pairs — this is how pharmiweb.jobs renders them.
4. Extracts the job description body by looking for a `job-detail` div, then a `section`, then an `article`, falling back to the largest `<div>` on the page.

---

## scraper/db.py

All functions use the `get_cursor()` context manager which opens a connection, auto-commits on success, and always closes the connection.

| Function | What it does |
|---|---|
| `create_schema()` | Creates the `jobs` table if it doesn't exist (idempotent) |
| `get_all_job_ids()` | Returns a `set[str]` of every `job_id` in the DB |
| `get_all_active_job_ids()` | Same but only `job_active = TRUE` rows |
| `insert_job(job_dict)` | Inserts a new row; silently skips if `job_id` already exists (`ON CONFLICT DO NOTHING`) |
| `mark_jobs_active(ids)` | Bulk-updates `job_active=TRUE` and `last_seen=today` |
| `mark_jobs_inactive(ids)` | Bulk-updates `job_active=FALSE` |

---

## run_scraper.py

Orchestrates the full daily cycle in 5 steps:

```
Step 1  scrape_all_job_links()        → live_ids (set of IDs on site today)
Step 2  get_all_job_ids()             → db_ids   (set of IDs already in DB)

        new_ids   = live_ids - db_ids   → scrape detail + INSERT
        known_ids = live_ids & db_ids   → update last_seen
        gone_ids  = db_ids  - live_ids  → mark inactive

Step 3  For each new_id: scrape_job_detail() → insert_job()
Step 4  mark_jobs_active(known_ids)
Step 5  mark_jobs_inactive(gone_ids)
```

Logs a summary line at the end: `+N new | ~N updated | -N deactivated`.

---

## Database schema quick reference

```sql
-- Scraper-managed columns (written by run_scraper.py)
job_id, url, title, employer, location, salary,
start_date, closing_date, discipline, hours,
contract_type, experience_level, job_details,
first_seen, last_seen, job_active

-- CV evaluator columns (written by Module 2 — not yet implemented)
evaluated        BOOLEAN   DEFAULT false
evaluated_at     TIMESTAMP
cv_version       VARCHAR(64)   -- hash of the CV txt file used
score            FLOAT         -- 0–100
score_reasoning  TEXT
should_apply     BOOLEAN   DEFAULT false
applied          BOOLEAN   DEFAULT false
```

To re-score all jobs after updating your CV: `UPDATE jobs SET evaluated = false`.  
To re-score only jobs not scored with the current CV: `WHERE cv_version != '<new_hash>'`.

---

## Adding a new module

1. Create a new top-level script (e.g. `evaluate.py`) or a new sub-package.
2. Import DB helpers from `scraper/db.py` — add new helper functions there if needed.
3. Read config from `scraper/config.py` — add new env variables there if needed.
4. Never duplicate DB connection logic or HTTP session setup.
