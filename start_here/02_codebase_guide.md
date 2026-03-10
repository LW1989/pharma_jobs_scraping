# Codebase Guide

## File map

```
pharma_jobs_scraping/
│
├── run_scraper.py          ← Daily: scrape pharmiweb.jobs into DB
├── run_evaluator.py        ← Daily: score jobs against CV (run after scraper)
├── run_reporter.py         ← Daily: send email + Telegram digest (run after evaluator)
│
├── scraper/
│   ├── config.py           ← All settings loaded from .env
│   ├── db.py               ← Scraper DB operations (jobs table writes)
│   └── scraper.py          ← HTTP fetching and HTML parsing
│
├── evaluator/
│   ├── prescreener.py      ← Rule-based filtering (no LLM, no network)
│   ├── llm_client.py       ← OpenAI API wrapper + prompt builder
│   └── db.py               ← Evaluator DB operations (eval column writes)
│
├── reporter/
│   ├── db.py               ← Fetch unsent jobs; mark_as_sent()
│   ├── formatter.py        ← Build HTML email body + Telegram text
│   ├── email_sender.py     ← smtplib wrapper (Gmail SMTP, TLS port 587)
│   └── telegram_sender.py  ← requests wrapper for Telegram Bot API
│
├── scripts/                ← One-off / maintenance scripts
│   ├── setup_db.py         ← One-time schema creation
│   ├── migrate_db.py       ← Additive schema migrations; safe to re-run
│   ├── synthesize_cv.py    ← Combine input_data/cv/*.txt → cv_matching.txt
│   ├── test_eval.py        ← Smoke test for the evaluation module
│   └── test_reporter.py    ← Smoke test for the reporter module
│
├── notebooks/
│   └── explore_db.ipynb
│
├── input_data/cv/          ← Drop raw CV .txt files here (git-ignored)
│
├── requirements.yaml       ← User-editable filters, scoring, and reporting config
├── .env                    ← Git-ignored; your actual credentials
├── .env.example            ← Template for Hetzner production
├── .env.local.example      ← Template for local Docker dev (port 5433)
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
| `REQUEST_DELAY_SECONDS` | Sleep between HTTP requests (0.5s) |
| `REQUEST_TIMEOUT_SECONDS` | HTTP timeout (30s) |
| `HEADERS` | Browser-like User-Agent header |
| `OPENAI_API_KEY` | OpenAI API key (required for run_evaluator.py) |
| `OPENAI_MODEL` | Default model (gpt-5-mini); overridden by requirements.yaml |
| `OPENAI_PRICING` | Dict of input/output costs per model for cost estimation |
| `SMTP_HOST/PORT/USER/PASSWORD` | Gmail SMTP credentials (required for run_reporter.py) |
| `REPORT_TO` | Comma-separated recipient email addresses |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Target chat/group ID for the Telegram digest |

---

## scraper/scraper.py

Two public functions used by `run_scraper.py`:

### `scrape_all_job_links() -> dict[str, str]`
Returns `{job_id: full_url}` for every job currently listed on the site.

1. Fetches page 1 of the search URL.
2. Reads `<a aria-label="Last page">` to find total page count.
3. Loops pages 1–N, extracts all `/job/{id}/` hrefs via regex, sleeps 0.5s between pages.

### `scrape_job_detail(job_id, url) -> dict`
Returns a dict ready to pass to `db.insert_job()`.

1. Fetches the job page.
2. Extracts title from `<h1>`.
3. Extracts structured metadata (employer, location, salary, dates, discipline, hours, contract type, experience level) by matching `<dt>`/`<dd>` pairs.
4. Extracts job description body from a `job-detail` div → `section` → `article` → largest `<div>` fallback.

---

## scraper/db.py

All functions use the `get_cursor()` context manager (opens connection, auto-commits, always closes).

| Function | What it does |
|---|---|
| `create_schema()` | Creates the `jobs` table if it doesn't exist (idempotent) |
| `get_all_job_ids()` | Returns `set[str]` of every `job_id` in the DB |
| `get_all_active_job_ids()` | Same but only `job_active = TRUE` rows |
| `insert_job(job_dict)` | Inserts new row; silently skips on conflict |
| `mark_jobs_active(ids)` | Bulk-updates `job_active=TRUE` and `last_seen=today` |
| `mark_jobs_inactive(ids)` | Bulk-updates `job_active=FALSE` |

---

## run_scraper.py

Orchestrates the daily scraping cycle:

```
Step 1  scrape_all_job_links()       → live_ids
Step 2  get_all_job_ids()            → db_ids

        new_ids   = live_ids - db_ids  → scrape detail page + INSERT
        known_ids = live_ids & db_ids  → update last_seen, job_active=true
        gone_ids  = db_ids  - live_ids → job_active=false

Step 3  scrape_job_detail() + insert_job() for each new job
Step 4  mark_jobs_active(known_ids)
Step 5  mark_jobs_inactive(gone_ids)
```

---

## requirements.yaml

User-editable YAML controlling both pre-screening and LLM scoring.

```yaml
filters:
  contract_types:        # allowed values for jobs.contract_type
  hours:                 # allowed values for jobs.hours
  location_keywords:     # any keyword must appear in jobs.location (OR logic)
  experience_levels:     # allowed values for jobs.experience_level
  exclude_title_keywords: # any match in title → immediate reject

scoring:
  should_apply_min_score: 70    # threshold for should_apply = true
  model: gpt-4o-mini            # overrides OPENAI_MODEL from .env
```

Empty lists (`[]`) disable any individual filter entirely.

---

## evaluator/prescreener.py

### `prescreen(job, filters) -> tuple[bool, str]`

Runs all configured filters against a single job dict. Returns `(True, "Passed pre-screening")` or `(False, "Pre-screened out: <reason>")`. Checks run in order; short-circuits on the first failure. Zero network calls, zero tokens.

Filter order: contract_type → hours → location keywords → experience_level → exclude title keywords.

---

## evaluator/llm_client.py

### `evaluate(job, cv_text, model, threshold) -> EvalResult`

Calls the OpenAI API to score a job against the CV. Returns an `EvalResult` dataclass:

```python
@dataclass
class EvalResult:
    score: int             # 0–100
    score_reasoning: str   # 2-3 sentence explanation
    should_apply: bool     # score >= threshold
    tokens_input: int
    tokens_output: int
    tokens_total: int
```

**Prompt design** — four techniques applied:
1. Role-first persona activation (domain expert recruiter)
2. Rubric with explicit score anchors per dimension
3. Guided per-dimension analysis before the final weighted score
4. Three few-shot examples (strong / moderate / poor match)

Uses `response_format` with `strict=True` (OpenAI Structured Outputs) to guarantee JSON schema compliance. Retries up to 3 times with exponential backoff via `tenacity`.

---

## evaluator/db.py

| Function | What it does |
|---|---|
| `get_jobs_to_evaluate(cv_version)` | Returns active jobs where `evaluated=FALSE` OR `cv_version` differs |
| `save_prescreening_fail(job_id, reason, cv_version)` | Writes rejection result; `score=0`, `passed_prescreening=false` |
| `save_evaluation(job_id, cv_version, score, reasoning, should_apply)` | Writes LLM result; `passed_prescreening=true` |
| `insert_evaluation_run(...)` | Inserts one row into `evaluation_runs` with token/cost stats |

---

## run_evaluator.py

```
Step 1  Auto-synthesize cv_matching.txt if missing/outdated
        Load cv_matching.txt → cv_version = MD5(cv_text)
        Load requirements.yaml → requirements_hash = MD5(str(requirements))

Step 2  get_jobs_to_evaluate(cv_version) → jobs list

Step 3  For each job:
          prescreen(job, filters)
            FAIL → save_prescreening_fail()   [no LLM call]
            PASS → evaluate() → save_evaluation()

Step 4  insert_evaluation_run(tokens, cost, counts, model, cv_version)

Step 5  Log summary
```

**To re-score all jobs** after updating CV files: drop new `.txt` files in `input_data/cv/` and run `python run_evaluator.py` — synthesis and re-evaluation happen automatically.

---

## reporter/db.py

| Function | What it does |
|---|---|
| `fetch_unsent_jobs(limit, min_score)` | Returns top-N evaluated jobs where `job_sent=FALSE`, ordered by score DESC |
| `mark_as_sent(job_ids)` | Sets `job_sent=TRUE` and `job_sent_at=NOW()` for given IDs |
| `count_evaluated_today()` | Returns totals for report header (total evaluated, apply count, review count) |

---

## reporter/formatter.py

Two public functions:

### `build_email_html(jobs, stats) -> str`
Returns a complete `<!DOCTYPE html>` email with one card per job. Each card shows the score badge with dot-meter (●●●●●○○○○○), score colour (green/amber/grey), title, employer, location, contract details, closing date, AI reasoning, and a "View Job →" link button.

### `build_telegram_text(jobs, top_n) -> str`
Returns an HTML-formatted string (Telegram `parse_mode=HTML`) with numbered emoji entries for the top-N jobs. Stays well within Telegram's 4 096-character limit for a top-5 digest.

---

## run_reporter.py

```
Step 1  fetch_unsent_jobs(limit=top_email, min_score=0) → jobs

        If no jobs → log and exit cleanly

Step 2  build_email_html(jobs) → send via Gmail SMTP (TLS port 587)
          On SMTP failure → log error + exit 1 (jobs NOT marked sent)

Step 3  build_telegram_text(jobs, top_n=top_telegram) → POST to Bot API
          On Telegram failure → log warning only (email already delivered)

Step 4  mark_as_sent(job_ids)
```

Jobs are only marked sent after the email delivers successfully, so a network failure on any given day will retry the same jobs the next day.

---

## Database schema quick reference

```sql
-- Scraper-managed (run_scraper.py)
job_id, url, title, employer, location, salary,
start_date, closing_date, discipline, hours,
contract_type, experience_level, job_details,
first_seen, last_seen, job_active

-- Evaluator-managed (run_evaluator.py)
passed_prescreening  BOOLEAN    -- NULL=pending, TRUE=passed, FALSE=filtered
evaluated            BOOLEAN    DEFAULT false
evaluated_at         TIMESTAMP
cv_version           VARCHAR(64)  -- MD5 of cv_matching.txt
score                FLOAT        -- 0–100
score_reasoning      TEXT
should_apply         BOOLEAN    DEFAULT false
applied              BOOLEAN    DEFAULT false  -- set manually

-- Reporter-managed (run_reporter.py)
job_sent             BOOLEAN    DEFAULT false
job_sent_at          TIMESTAMP

-- evaluation_runs table (one row per run_evaluator.py execution)
run_id, run_at, model, cv_version, requirements_hash,
jobs_total, jobs_prefiltered, jobs_evaluated, jobs_should_apply,
tokens_input, tokens_output, tokens_total, estimated_cost_usd,
run_success, error_message
```

**Useful queries:**
```sql
-- Top 10 jobs to apply for
SELECT job_id, title, employer, location, score, score_reasoning
FROM jobs WHERE should_apply = TRUE AND job_active = TRUE
ORDER BY score DESC LIMIT 10;

-- Cost history
SELECT run_at::date, model, jobs_evaluated, estimated_cost_usd
FROM evaluation_runs ORDER BY run_at DESC;

-- Pre-screening effectiveness
SELECT run_at::date,
       jobs_prefiltered,
       jobs_evaluated,
       ROUND(100.0 * jobs_prefiltered / NULLIF(jobs_total,0), 1) AS pct_filtered
FROM evaluation_runs ORDER BY run_at DESC;
```

---

## Adding a new module

1. Create a new top-level script (e.g. `distribute.py`) or sub-package.
2. Import DB helpers from `scraper/db.py` or `evaluator/db.py` — add new functions there as needed.
3. Add new env variables to `scraper/config.py` and both `.env.example` files.
4. Never duplicate DB connection logic or HTTP session setup.
