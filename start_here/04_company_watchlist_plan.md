# Company Watchlist Scraper — Plan

## What this module does

Periodically checks a curated list of small NRW pharma/biotech companies that
have their own career pages (not listed on pharmiweb.jobs). Extracts any open
positions, evaluates them against the CV using the existing LLM evaluator, and
delivers them through the same email + Telegram reporter.

The source list comes from the Google Sheet of manually researched companies:
https://docs.google.com/spreadsheets/d/1Ty6fRQIxwxY9cV-s7Cuv9-L59BW2Pzdsx7Mxpt82m54

---

## Why this is different from the pharmiweb scraper

| | pharmiweb.jobs | Company watchlist |
|---|---|---|
| Format | Structured, consistent | Every site is different |
| Volume | ~966 jobs / day | ~5–20 jobs / week total |
| Frequency | Daily | Weekly (jobs don't change daily) |
| Extraction | CSS selectors + regex | LLM reads raw HTML |
| ATS | One platform | Mix: Workday, Personio, Workable, Recruitee, custom |

---

## Company inventory (from Google Sheet, 46 companies)

Grouped by career page type to inform implementation:

### ATS platforms (structured APIs available)
| Company | City | ATS | Career URL |
|---|---|---|---|
| Alvotech | Jülich | Workday | https://alvotech.wd103.myworkdayjobs.com/Alvotech_Careers |
| Allucent | Köln | Workable | https://apply.workable.com/allucent/ |
| Prosion | Köln | Personio | https://prosion-gmbh.jobs.personio.de |
| Cellex | Cologne | Recruitee | https://cellexgmbh.recruitee.com |
| Evotec | Köln/Hamburg | Custom | https://careers.evotec.com/en |
| Labcorp | Münster | Custom | https://de-careers.labcorp.com/global/en |

### Custom career pages (NRW core)
| Company | City | Career URL |
|---|---|---|
| A&M Stabtest | Bergheim | https://www.am-labor.de/en/careers |
| BioEcho | Köln | https://www.bioecho.com/About-Us/Career/ |
| BetaSense | Bochum | https://www.beta-sense.de/en/career/ |
| DNTOX | Düsseldorf | https://dntox.de/career/ |
| Cube BioTech | Monheim | https://cube-biotech.com/information/job-opportunities/ |
| Chimera BioTec | Dortmund | https://www.chimera-biotec.com/about-us/career/ |
| Autodisplay Biotech | Düsseldorf | http://www.autodisplay-biotech.com/html/job_opportunities.html |
| Lead Discovery Center | Dortmund | https://www.lead-discovery.de/de/careers/ |
| Seregen | Dortmund | https://serengen.com/career/ |
| Resolve Bioscience | Monheim | https://resolvebiosciences.com/careers/ |
| Protagene | Dortmund | https://careers.protagene.com/ |
| Priavoid | Düsseldorf | https://priavoid.com/contact/ (Careers section; announcements) |
| Trans Immune | Düsseldorf | https://transimmune.com/career-opportunities/ |
| YMC Europe | — | https://ymc.eu/recruitment.html |
| Evoxx | Monheim | https://evoxx.com/people/jobs-and-career/ |
| Axplora | Leverkusen | https://www.axplora.com/work-at-axplora/ |
| Hözel Diagnostika | Köln | https://www.hoelzel-biotech.com/de/jobs |
| MLM Labs | Mönchengladbach | https://www.mlm-labs.com/careers-at-mlm/ |
| Profil | Neuss | https://www.profil.com/organization/open-positions |
| Saltigo (LANXESS) | Leverkusen | https://career.lanxess.com |
| Enzymaster | Düsseldorf | https://enzymaster.de/contactcareers/ |
| AdhexPharma | Langenfeld | https://www.adhexpharma.com/de/karriere |
| Acromion | Köln | https://career-acromion-gmbh.com/ |
| Apontis | Monheim | https://apontis-pharma.de/karriere-bei-apontis-pharma |
| atlas | Bochum | https://laserrobotarm.com/en/karriere/ |
| b.fab | Köln | https://bfab.bio/about-us/jobs |
| BioSolveIT | Sankt Augustin | https://www.biosolveit.de/career/ |
| ClinStat | Hürth | https://clinstat.eu/careers/ |
| CUREosity | Düsseldorf | https://www.cureosity.com/about/career |
| Detechgene | Köln | https://www.detechgene.de/career |
| Dolorgiet | Sankt Augustin | https://www.dolorgiet.de/karriere |
| Eurocor | Bonn | https://eurocor.de/eurocor/jobs-career |
| Wuxi Biologics | Wuppertal | https://www.wuxibiologics.com/proud/ |
| Diagenics | Essen | https://diagenics.com |
| Evidenze | Köln | https://evidenze.com |
| Arensia | Düsseldorf | https://www.arensia-em.com |

### Companies with no scraping target (no jobs page / no positions)
Life And Brain (Bonn), PAIA biotech (Köln), Disco Pharma (Köln), Artes (Langenfeld)

---

## Architecture

### New files
```
input_data/
├── companies.yaml              ← Auto-generated from Google Sheet (git-tracked)
└── google_credentials.json     ← Google service account key (git-ignored)

scraper/
└── company_scraper.py          ← Fetches career pages + LLM extraction

scripts/
└── sync_companies_from_sheet.py  ← Syncs Google Sheet → companies.yaml (run manually)

run_company_checker.py          ← Weekly entry point (not in daily cron)
```

### Existing files unchanged
The existing `jobs` table, `evaluator/`, and `reporter/` are reused as-is. Company jobs
flow through the same evaluation and reporting pipeline using `source='company_direct'`.

### DB change — one new column
```sql
-- Already planned in migrate_db.py (add if not present)
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS source VARCHAR(32) DEFAULT 'pharmiweb';
```

Company jobs are inserted with `source='company_direct'`.

---

## Extraction approach

### Problem
Each career page is a different website. Simple CSS selectors won't work across 36+ sites.
Some are JavaScript-rendered (require `requests-html` or `playwright`). Some are plain HTML.

### Solution: LLM-based extraction
1. Fetch the page with `requests` (plain HTTP, same as current scraper).
2. Strip HTML to readable text using `BeautifulSoup.get_text()` (keeps job titles/links visible).
3. Send the stripped text to the LLM with a prompt: "Extract all job listings on this page.
   For each job return: title, location, url (if present), and a brief description."
4. LLM returns structured JSON (OpenAI Structured Outputs, same pattern as evaluator).
5. For each extracted job, call the full evaluator (prescreen → score) and insert into `jobs`.

### ATS platform shortcuts (avoid LLM cost)
Some platforms expose structured JSON APIs — use these instead of LLM extraction:

| Platform | Endpoint pattern | Notes |
|---|---|---|
| **Workable** | `https://apply.workable.com/api/v3/accounts/{slug}/jobs` | JSON, no auth |
| **Personio** | `https://{slug}.jobs.personio.de/api/v1/jobs` | JSON, no auth |
| **Recruitee** | `https://{slug}.recruitee.com/api/offers` | JSON, no auth |
| **Workday** | No public API | Fall back to LLM on HTML |

---

## Config format: `input_data/companies.yaml`

```yaml
companies:
  - name: BioSolveIT
    city: Sankt Augustin
    country: Germany
    career_url: https://www.biosolveit.de/career/
    source_type: html          # html | workable | personio | recruitee

  - name: Prosion
    city: Köln
    country: Germany
    career_url: https://prosion-gmbh.jobs.personio.de
    source_type: personio
    slug: prosion-gmbh          # used for API endpoint

  - name: Allucent
    city: Köln
    country: Germany
    career_url: https://apply.workable.com/allucent/
    source_type: workable
    slug: allucent

  - name: Cellex
    city: Cologne
    country: Germany
    career_url: https://cellexgmbh.recruitee.com
    source_type: recruitee
    slug: cellexgmbh
```

---

## `scraper/company_scraper.py` public API

```python
def fetch_jobs(company: dict) -> list[dict]:
    """
    Fetch job listings from a company's career page.
    Returns a list of partial job dicts ready for insert_job().
    Each dict has: title, url, employer, location, job_details, source='company_direct'.
    Missing fields (salary, contract_type, etc.) are set to None.
    """
```

Dispatcher logic:
```python
if source_type == "personio":   return _fetch_personio(company)
if source_type == "workable":   return _fetch_workable(company)
if source_type == "recruitee":  return _fetch_recruitee(company)
else:                           return _fetch_html_llm(company)
```

---

## `run_company_checker.py` flow

```
Step 1  Load input_data/companies.yaml
Step 2  For each company:
          fetch_jobs(company) → raw_jobs list
          For each raw_job:
            job_id = hash(company.name + job.title)  ← stable dedup key
            if job_id already in DB → skip (already seen)
            else → prescreen → score → insert_job()
Step 3  reporter: send digest of NEW company jobs found this week
```

**Deduplication:** job_id is `MD5(company_name + job_title)[:16]`. Same job re-appearing
next week is silently skipped (already in DB with `job_active=TRUE`).

**Inactive tracking:** On each run, any company job NOT seen today is marked
`job_active=FALSE` after 30 days (same age-based strategy as planned for jobvector).

---

## Integration with existing pipeline

`run_company_checker.py` runs **daily**, as part of the same cron chain as the
pharmiweb scraper. Since most company pages change rarely, repeat visits for
companies with no new jobs are cheap (one HTTP request, no LLM call if nothing is new).

```cron
0 6 * * *  cd ~/pharma_jobs_scraping \
           && .venv/bin/python run_scraper.py \
           && .venv/bin/python run_company_checker.py \
           && .venv/bin/python run_nrw_major_checker.py \
           && .venv/bin/python run_evaluator.py \
           && .venv/bin/python run_reporter.py
```

- **Pharmiweb scrape** uses Germany + Benelux only (`PHARMIWEB_LOCATION_IDS`, default `127,148,115`). Override in `.env` if needed.
- **`run_nrw_major_checker.py`** loads `input_data/nrw_major_employers.yaml` (Miltenyi/SmartRecruiters, Bayer–Henkel/SuccessFactors, QIAGEN–J&J/Covestro/Workday, UCB). Jobs are filtered to **remote (DE/EU)** or **hybrid in NRW** via `input_data/nrw_eligibility.yaml`. Workday/UCB need `pip install playwright && playwright install chromium`.
- **Reporter** (`requirements.yaml` → `reporting`): by default **pharmiweb** digest lists only `should_apply` rows; other evaluated pharmiweb jobs are marked sent in bulk. See `reporting` keys there.

**Why daily is fine:**
- Deduplication is by `job_id = MD5(company_name + job_title)`. Re-fetching a page
  where nothing changed costs one HTTP request and zero LLM calls.
- New jobs from a company are picked up the same day they are posted, not up to
  7 days later.

Company jobs inserted by `run_company_checker.py` have `evaluated=FALSE`.
`run_evaluator.py` (which runs next in the chain) picks them up and scores them
immediately in the same daily run.

---

## Reporting — two separate sections per digest

Company watchlist jobs and pharmiweb jobs are **never mixed** in the same report
section. Each daily digest has two clearly separated blocks:

### Email (`reporter/formatter.py` — `build_email_html`)

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  TOP JOBS FROM PHARMIWEB.JOBS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[card 1]  [card 2]  ...  [card N]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  NEW JOBS FROM COMPANY WATCHLIST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[card 1]  [card 2]  ...
```

- If no new company jobs today → the second section is omitted entirely.
- Company job cards show the company name prominently (since there is no
  central job board to establish brand recognition).

### Telegram (`reporter/formatter.py` — `build_telegram_text`)

```
📋 Top pharmiweb jobs:
1️⃣  [title] · [employer] · score X
...

🏢 New from your company watchlist:
• [title] @ [company] — [url]
...
```

- Telegram company block is a compact bulleted list (no score badge — just
  title, company, and direct link), since these jobs haven't been pre-filtered
  by score in the same way.
- If no new company jobs → the watchlist block is omitted.

### Changes to `reporter/db.py`

Add a new fetch function alongside the existing `fetch_unsent_jobs()`:

```python
def fetch_unsent_company_jobs() -> list[dict]:
    """
    Return unsent company_direct jobs, ordered by score DESC.
    No limit — typically only a handful per day.
    """
    sql = """
        SELECT job_id, title, employer, location, url,
               score, score_reasoning, should_apply
        FROM jobs
        WHERE source = 'company_direct'
          AND evaluated = TRUE
          AND job_sent  = FALSE
          AND job_active = TRUE
        ORDER BY score DESC NULLS LAST
    """
    ...
```

### Changes to `run_reporter.py`

```python
# Fetch both sources separately
pharmiweb_jobs     = fetch_unsent_jobs(limit=top_email, min_score=0)
company_jobs       = fetch_unsent_company_jobs()

# Build email with two sections
html = build_email_html(pharmiweb_jobs, company_jobs, stats)

# Build Telegram with two blocks
text = build_telegram_text(pharmiweb_jobs, company_jobs, top_n=top_telegram)

# Mark both sent together after successful email delivery
mark_as_sent([j["job_id"] for j in pharmiweb_jobs + company_jobs])
```

---

## Fix required in `scraper/db.py` before implementation

`run_scraper.py` currently marks ALL jobs not seen on pharmiweb as inactive:
```python
gone_ids = db_ids - live_pharmiweb_ids   # ← this includes company_direct jobs!
mark_jobs_inactive(gone_ids)             # ← silently deactivates them every day
```

Before this module is built, `get_all_job_ids()` in the pharmiweb run must be scoped
to `source='pharmiweb'` only:
```python
def get_job_ids_by_source(source: str) -> set[str]:
    with get_cursor() as cur:
        cur.execute(
            "SELECT job_id FROM jobs WHERE source = %s OR (source IS NULL AND %s = 'pharmiweb')",
            (source, source),
        )
        return {row["job_id"] for row in cur.fetchall()}
```

---

## Google Sheet sync module

The Google Sheet is the source of truth for the company list. Rather than
manually keeping `input_data/companies.yaml` in sync, a dedicated sync module
reads the sheet and updates the YAML automatically.

### New file: `scripts/sync_companies_from_sheet.py`

```python
"""
Reads the company watchlist Google Sheet and updates input_data/companies.yaml.
Run manually whenever you add/edit companies in the sheet.

Detects:
  - New rows → appended to companies.yaml with source_type: html (default)
  - Removed rows → flagged in output but NOT deleted (manual review required)
  - Changed career URLs → updated in companies.yaml

Requires: GOOGLE_SHEET_ID in .env, gspread + google-auth pip packages,
          and a Google service account JSON key in input_data/google_credentials.json
          (git-ignored).
"""
```

### How it works

1. Reads the sheet via the **Google Sheets API** (no browser needed):
   - Library: `gspread` (lightweight, well-maintained)
   - Auth: Google service account key (JSON file, stored in `input_data/google_credentials.json`, git-ignored)
2. Parses columns: Company, City, Career URL, Accept Initiative Applications, Interesting
3. Compares against the current `input_data/companies.yaml`
4. Prints a diff (new / changed / removed) and writes the updated YAML
5. For new companies it auto-detects `source_type` from the career URL:
   - URL contains `personio.de` → `personio`
   - URL contains `workable.com` → `workable`
   - URL contains `recruitee.com` → `recruitee`
   - URL contains `myworkdayjobs.com` → `workday`
   - Anything else → `html`

### New `.env` variables

```ini
# Google Sheets API — required for sync_companies_from_sheet.py
GOOGLE_SHEET_ID=1Ty6fRQIxwxY9cV-s7Cuv9-L59BW2Pzdsx7Mxpt82m54
GOOGLE_CREDENTIALS_FILE=input_data/google_credentials.json
```

### New `.gitignore` entries

```
input_data/google_credentials.json   # service account key — never commit
```

### Workflow

```
You add a company to the Google Sheet
        ↓
python scripts/sync_companies_from_sheet.py
        ↓
companies.yaml updated automatically
        ↓
next run_company_checker.py picks it up
```

This is a **manual trigger** (not in cron) — you run it only when you've updated
the sheet. This avoids accidental overwrites from automated runs.

---

## Implementation order

1. **`scripts/migrate_db.py`** — add `source VARCHAR(32) DEFAULT 'pharmiweb'`
2. **`scraper/db.py`** — add `get_job_ids_by_source()`, add `source` to `insert_job()`
3. **`run_scraper.py`** — replace `get_all_job_ids()` with `get_job_ids_by_source('pharmiweb')`
4. **`scripts/sync_companies_from_sheet.py`** — Google Sheet → companies.yaml sync
5. **`input_data/companies.yaml`** — initial population via the sync script
6. **`scraper/company_scraper.py`** — ATS API fetchers + LLM HTML extractor
7. **`run_company_checker.py`** — orchestration script
8. **`reporter/db.py`** — add `fetch_unsent_company_jobs()`
9. **`reporter/formatter.py`** — add two-section layout to email and Telegram builders
10. **`run_reporter.py`** — fetch both sources, pass both to formatter, mark both sent
11. **Test** — run against 3 companies (1 Personio, 1 Workable, 1 HTML), check DB inserts and email layout
12. **Update cron** — add `run_company_checker.py` to daily chain

---

## Open questions before implementation

- **JavaScript-rendered pages:** Some sites (e.g. BioSolveIT) are WordPress/React apps.
  Plain `requests` returns a skeleton HTML. Options:
  a) `playwright` (adds headless Chrome dependency — heavy)
  b) `requests-html` with JS rendering (lighter)
  c) Skip JS-only sites and note them in `companies.yaml` as `source_type: skip`
  Initial recommendation: start with `requests` and mark JS-only sites as `skip` for now.
  Add `playwright` support later if needed.

- **LLM cost per run:** ~36 HTML pages × ~5000 chars each = ~180k tokens input.
  At gpt-5-mini rates (~$0.25/1M) ≈ $0.05 per weekly run. Negligible.

- **German job listings:** Several companies (Dolorgiet, AdhexPharma, Enzymaster) list
  jobs in German. The existing LLM evaluator handles German natively — no change needed.
