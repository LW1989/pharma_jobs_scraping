#!/bin/bash
# Daily pharma jobs pipeline — called by cron at 04:00
# Logs to logs/cron_YYYYMMDD.log

set -e

PROJECT="/root/pharma_jobs_scraping"
PYTHON="$PROJECT/.venv/bin/python"
LOG_DIR="$PROJECT/logs"
DATE=$(date +%Y%m%d)

mkdir -p "$LOG_DIR"
cd "$PROJECT"

echo "[$(date)] Starting daily pipeline" >> "$LOG_DIR/cron_$DATE.log"

# Pull latest code changes
git pull origin master >> "$LOG_DIR/cron_$DATE.log" 2>&1 \
  || echo "[$(date)] WARNING: git pull failed, continuing with current code" >> "$LOG_DIR/cron_$DATE.log"

# Install any new dependencies quietly
.venv/bin/pip install -r requirements.txt -q >> "$LOG_DIR/cron_$DATE.log" 2>&1

# Run the pipeline
$PYTHON run_scraper.py   >> "$LOG_DIR/cron_$DATE.log" 2>&1
$PYTHON run_evaluator.py >> "$LOG_DIR/cron_$DATE.log" 2>&1
$PYTHON run_reporter.py  >> "$LOG_DIR/cron_$DATE.log" 2>&1

echo "[$(date)] Pipeline complete" >> "$LOG_DIR/cron_$DATE.log"
