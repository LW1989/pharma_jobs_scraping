# Deployment Guide — Pharma Jobs Scraper

## Overview

The pipeline runs daily on the existing Hetzner Cloud server via a single cron job
that chains all three scripts:

```
04:00 AM  git pull → run_scraper.py → run_evaluator.py → run_reporter.py
```

All deduplication is already handled by the code:
- **Scraper** — `INSERT … ON CONFLICT DO NOTHING`; existing jobs only get
  `job_active` and `last_seen` updated, never duplicated.
- **Evaluator** — only processes rows where `evaluated = FALSE` or
  `cv_version` changed (CV was updated). Jobs are never re-scored unless the CV changes.
- **Reporter** — only fetches rows where `job_sent = FALSE`. Once sent, a
  job never appears in a digest again.

---

## One-time Server Setup

### 1. SSH into the Hetzner server

```bash
ssh root@<hetzner_ip>
```

### 2. Install system dependencies (if not already present)

```bash
apt update && apt upgrade -y
apt install -y python3.11 python3.11-venv python3-pip git curl libpq-dev
```

### 3. Clone the repository

```bash
cd /opt   # or wherever you keep projects on this server
git clone https://github.com/LW1989/pharma_jobs_scraping.git
cd pharma_jobs_scraping
```

### 4. Create Python virtual environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 5. Set up the database

The PostgreSQL instance is already running on Hetzner from the previous project.
Just create a new database and user for this project:

```bash
sudo -u postgres psql << EOF
CREATE DATABASE pharma_jobs;
CREATE USER pharma WITH ENCRYPTED PASSWORD 'CHOOSE_SECURE_PASSWORD';
GRANT ALL PRIVILEGES ON DATABASE pharma_jobs TO pharma;
\c pharma_jobs
GRANT ALL ON SCHEMA public TO pharma;
EOF
```

Then create the schema and run all migrations:

```bash
source .venv/bin/activate
python scripts/setup_db.py     # creates the jobs table
python scripts/migrate_db.py   # adds eval + reporter columns
```

### 6. Configure the environment file

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Fill in the production values:

```ini
# Database — Hetzner production (port 5432, not 5433 like local Docker)
DB_HOST=<hetzner_ip>
DB_PORT=5432
DB_NAME=pharma_jobs
DB_USER=pharma
DB_PASSWORD=CHOOSE_SECURE_PASSWORD

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5-mini

# Email
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=progressivedatalabs@gmail.com
SMTP_PASSWORD=yvfmwjlusbyrpacy
REPORT_TO=lutz.wallhorn@gmail.com

# Telegram
TELEGRAM_BOT_TOKEN=8210311101:AAFVOb8KmtOIdAIL2E6eX8bo3YvwEltMUzU
TELEGRAM_CHAT_ID=25338446
```

### 7. Add the CV files and synthesize

The CV files are git-ignored and must be copied manually to the server:

```bash
# From your local machine:
scp input_data/cv/*.txt root@<hetzner_ip>:/opt/pharma_jobs_scraping/input_data/cv/

# Then on the server run synthesis once:
source .venv/bin/activate
python scripts/synthesize_cv.py
```

`cv_matching.txt` will be generated and cached — synthesis only re-runs when
the source CV files change.

### 8. First-time population run

Run the full pipeline once manually to populate the database:

```bash
source .venv/bin/activate

# Step 1 — scrape all current jobs (~15 min, ~1000 jobs)
python run_scraper.py

# Step 2 — evaluate all jobs against CV (~2 hours, ~200 LLM calls after pre-screening)
python run_evaluator.py

# Step 3 — send first digest report
python run_reporter.py
```

After this, the cron job takes over and only processes the **delta** each day
(new jobs added overnight, old jobs marked inactive). Daily runs will be much faster.

---

## Cron Job Setup

### Create the pipeline script

Create `deploy/run_pipeline.sh` (this is the script cron calls):

```bash
#!/bin/bash
# Daily pharma jobs pipeline — called by cron at 04:00

set -e

PROJECT="/opt/pharma_jobs_scraping"
PYTHON="$PROJECT/.venv/bin/python"
LOG_DIR="$PROJECT/logs"
DATE=$(date +%Y%m%d)

mkdir -p "$LOG_DIR"

cd "$PROJECT"

echo "[$(date)] Starting daily pipeline" >> "$LOG_DIR/cron_$DATE.log"

# Pull latest code changes
git pull origin feature/evaluation-module >> "$LOG_DIR/cron_$DATE.log" 2>&1 \
  || echo "[$(date)] WARNING: git pull failed, continuing" >> "$LOG_DIR/cron_$DATE.log"

# Install any new dependencies
.venv/bin/pip install -r requirements.txt -q >> "$LOG_DIR/cron_$DATE.log" 2>&1

# Run the pipeline
$PYTHON run_scraper.py   >> "$LOG_DIR/cron_$DATE.log" 2>&1
$PYTHON run_evaluator.py >> "$LOG_DIR/cron_$DATE.log" 2>&1
$PYTHON run_reporter.py  >> "$LOG_DIR/cron_$DATE.log" 2>&1

echo "[$(date)] Pipeline complete" >> "$LOG_DIR/cron_$DATE.log"
```

```bash
chmod +x deploy/run_pipeline.sh
```

### Install the cron job

```bash
crontab -e
```

Add this line:

```
# Pharma Jobs — daily pipeline at 04:00
0 4 * * * /opt/pharma_jobs_scraping/deploy/run_pipeline.sh
```

### Verify it is installed

```bash
crontab -l | grep pharma
```

---

## Log Management

Logs are written per day to `logs/cron_YYYYMMDD.log`.

Add a cleanup job to crontab to keep only the last 30 days:

```
# Clean old pharma logs (daily at 05:00)
0 5 * * * find /opt/pharma_jobs_scraping/logs -name "cron_*.log" -mtime +30 -delete
```

To watch a live run:

```bash
tail -f /opt/pharma_jobs_scraping/logs/cron_$(date +%Y%m%d).log
```

---

## Useful Monitoring Queries

```sql
-- Jobs added today
SELECT COUNT(*) FROM jobs WHERE first_seen = CURRENT_DATE;

-- Evaluation cost this week
SELECT run_at::date, jobs_evaluated, estimated_cost_usd
FROM evaluation_runs ORDER BY run_at DESC LIMIT 7;

-- Top apply recommendations not yet sent
SELECT title, employer, score FROM jobs
WHERE should_apply = TRUE AND job_sent = FALSE AND job_active = TRUE
ORDER BY score DESC LIMIT 10;

-- Jobs sent to date
SELECT COUNT(*), MIN(job_sent_at), MAX(job_sent_at) FROM jobs WHERE job_sent = TRUE;
```

---

## Updating the CV

When new CV versions are added:

```bash
# From your local machine, copy to server:
scp input_data/cv/*.txt root@<hetzner_ip>:/opt/pharma_jobs_scraping/input_data/cv/

# On the server, delete the hash file to force re-synthesis:
rm /opt/pharma_jobs_scraping/.cv_synthesis_hash

# run_evaluator.py will pick this up automatically on the next run
# and re-score all jobs against the new CV.
```

---

## Branch note

Currently working on `feature/evaluation-module`. Before final deployment,
merge this branch into `main` and update the `git pull` command in
`deploy/run_pipeline.sh` to pull from `main`.

```bash
git checkout main
git merge feature/evaluation-module
git push origin main
```

Then update `run_pipeline.sh`:
```bash
git pull origin main
```
