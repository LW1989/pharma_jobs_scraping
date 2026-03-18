# Pharma Jobs Scraper

Daily scraper for European pharma/life-science jobs from [pharmiweb.jobs](https://www.pharmiweb.jobs), stored in PostgreSQL.

## What it does

- Scrapes all jobs listed for Europe on pharmiweb.jobs (handles pagination automatically)
- Extracts: title, employer, location, salary, start date, closing date, discipline, hours, contract type, experience level, and full job description
- On every daily run:
  - **New jobs** → fetched in detail and inserted into the DB
  - **Still-listed jobs** → `last_seen` updated, `job_active = true`
  - **Removed jobs** → `job_active = false`
- Schema includes future-ready columns for CV scoring (evaluated, score, score_reasoning, should_apply, applied, …)

## Project structure

```
pharma_jobs_scraping/
├── scraper/
│   ├── config.py        # reads .env settings
│   ├── db.py            # PostgreSQL helpers
│   └── scraper.py       # HTTP fetching & HTML parsing
├── run_scraper.py        # daily entry point (called by cron)
├── setup_db.py           # one-time table creation
├── requirements.txt
├── .env.example          # template for production (Hetzner)
└── .env.local.example    # template for local Docker DB
```

---

## Local development setup

### 1. Clone and install

```bash
git clone <repo-url>
cd pharma_jobs_scraping
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Start a local PostgreSQL database via Docker

```bash
docker run --name pharma-jobs-dev \
  -e POSTGRES_DB=pharma_jobs \
  -e POSTGRES_USER=pharma \
  -e POSTGRES_PASSWORD=pharma \
  -p 5432:5432 \
  -d postgres:16
```

### 3. Configure the environment

```bash
cp .env.local.example .env
```

The default values in `.env.local.example` match the Docker container above — no edits needed for local dev.

### 4. Create the database schema

```bash
python setup_db.py
```

Expected output:

```
INFO: Testing database connection …
INFO: Connection successful. Creating schema …
INFO: Schema created / verified.
INFO: Done. The 'jobs' table is ready.
```

### 5. Run the scraper

```bash
python run_scraper.py
```

The first run will be slow (it fetches every job detail page, ~1 request/second). Subsequent runs only fetch new jobs.

### Full pipeline (local smoke test)

From repo root, after `.env` is set and Playwright browsers are installed (`playwright install chromium`):

```bash
# Dry-run reporter (no email/Telegram); limit evaluator for speed
REPORTER_DRY_RUN=1 EVALUATOR_MAX_JOBS=20 ./scripts/run_full_pipeline_local.sh
```

Steps: `run_scraper` → `run_company_checker` → `run_nrw_major_checker` → `run_evaluator` → `run_reporter`.

- **`REPORTER_DRY_RUN=1`** — builds the digest but skips SMTP/Telegram and marking jobs sent.
- **`EVALUATOR_MAX_JOBS=N`** — optional; evaluates at most *N* pending jobs (omit for full backlog).
- For a **real** digest send, unset `REPORTER_DRY_RUN` and ensure SMTP/Telegram in `.env`.

---

## Hetzner production deployment

### 1. Provision a PostgreSQL database on your Hetzner server

```bash
# On the server
sudo apt install postgresql
sudo -u postgres createuser pharma
sudo -u postgres createdb pharma_jobs -O pharma
sudo -u postgres psql -c "ALTER USER pharma PASSWORD '<strong-password>';"
```

### 2. Deploy the project

```bash
# On the server
git clone <repo-url> /opt/pharma_jobs_scraping
cd /opt/pharma_jobs_scraping
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure the environment

```bash
cp .env.example .env
# Edit .env and fill in your Hetzner DB credentials
nano .env
```

### 4. Create the schema

```bash
python setup_db.py
```

### 5. Set up the daily cron job

```bash
crontab -e
```

Add this line to run every day at 06:00:

```
0 6 * * * cd /opt/pharma_jobs_scraping && /opt/pharma_jobs_scraping/.venv/bin/python run_scraper.py >> /opt/pharma_jobs_scraping/logs/scraper.log 2>&1
```

Create the log directory first:

```bash
mkdir -p /opt/pharma_jobs_scraping/logs
```

### 6. Verify the first cron run

```bash
tail -f /opt/pharma_jobs_scraping/logs/scraper.log
```

---

## Database schema

| Column            | Type         | Description                                      |
|-------------------|--------------|--------------------------------------------------|
| `job_id`          | VARCHAR PK   | Numeric ID extracted from the job URL            |
| `url`             | TEXT         | Full job page URL                                |
| `title`           | TEXT         | Job title                                        |
| `employer`        | TEXT         |                                                  |
| `location`        | TEXT         |                                                  |
| `salary`          | TEXT         |                                                  |
| `start_date`      | TEXT         |                                                  |
| `closing_date`    | TEXT         |                                                  |
| `discipline`      | TEXT         |                                                  |
| `hours`           | TEXT         | Full Time / Part Time                            |
| `contract_type`   | TEXT         | Permanent / Contract                             |
| `experience_level`| TEXT         |                                                  |
| `job_details`     | TEXT         | Full job description body                        |
| `first_seen`      | DATE         | Date first scraped                               |
| `last_seen`       | DATE         | Date last confirmed active                       |
| `job_active`      | BOOLEAN      | False when job disappears from listing           |
| `evaluated`       | BOOLEAN      | CV scoring done?                                 |
| `evaluated_at`    | TIMESTAMP    | When was the CV score calculated                 |
| `cv_version`      | VARCHAR(64)  | Hash of CV used; reset evaluated when CV changes |
| `score`           | FLOAT        | 0–100 fit score vs CV                            |
| `score_reasoning` | TEXT         | LLM explanation of the score                     |
| `should_apply`    | BOOLEAN      | Recommended to apply (can be manually overridden)|
| `applied`         | BOOLEAN      | Did you actually apply?                          |

---

## Configuration reference (`.env`)

| Variable      | Description                  | Example           |
|---------------|------------------------------|-------------------|
| `DB_HOST`     | PostgreSQL host              | `123.45.67.89`    |
| `DB_PORT`     | PostgreSQL port              | `5432`            |
| `DB_NAME`     | Database name                | `pharma_jobs`     |
| `DB_USER`     | Database user                | `pharma`          |
| `DB_PASSWORD` | Database password            | `secret`          |
