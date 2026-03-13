"""
Company watchlist checker — daily entry point.

Workflow:
  1. Load input_data/companies.yaml.
  2. Fetch the set of current company_direct job IDs from the DB.
  3. For each company, fetch open job listings from its career page.
  4. Insert any new jobs (evaluated=FALSE — run_evaluator.py scores them next).
  5. Mark re-seen jobs as active (refresh last_seen).
  6. Age-based deactivation: mark individual job listings that haven't been
     seen for 30+ days as inactive. Companies themselves are never deactivated.
  7. Log a summary.

Usage:
    python run_company_checker.py

Run as part of the daily cron chain, after run_scraper.py and before run_evaluator.py:
    run_scraper.py → run_company_checker.py → run_evaluator.py → run_reporter.py
"""

import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import yaml

from scraper import config
from scraper.db import get_cursor, insert_job, mark_jobs_active, mark_jobs_inactive
from scraper.company_scraper import fetch_jobs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(name)s – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("run_company_checker")

ROOT = Path(__file__).parent
COMPANIES_PATH = ROOT / "input_data" / "companies.yaml"
INACTIVE_AFTER_DAYS = 30


def _load_companies() -> list[dict]:
    if not COMPANIES_PATH.exists():
        logger.error("input_data/companies.yaml not found. Run sync_companies_from_sheet.py first.")
        sys.exit(1)
    with COMPANIES_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    companies = data.get("companies", [])
    logger.info("Loaded %d companies from companies.yaml", len(companies))
    return companies


def _get_all_company_job_ids() -> set[str]:
    """Return all job IDs currently in the DB with source='company_direct'."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT job_id FROM jobs WHERE source = 'company_direct'"
        )
        return {row["job_id"] for row in cur.fetchall()}


def _deactivate_stale_company_jobs() -> int:
    """
    Mark company_direct job listings as inactive if last_seen is older than
    INACTIVE_AFTER_DAYS days. Only individual job rows are affected — companies
    in companies.yaml are never removed.
    """
    cutoff = date.today() - timedelta(days=INACTIVE_AFTER_DAYS)
    with get_cursor() as cur:
        cur.execute(
            """
            UPDATE jobs
               SET job_active = FALSE
             WHERE source = 'company_direct'
               AND job_active = TRUE
               AND last_seen < %s
            """,
            (cutoff,),
        )
        return cur.rowcount


def main() -> None:
    from datetime import datetime
    start = datetime.now()
    logger.info("=" * 60)
    logger.info("Company checker run started at %s", start.isoformat())
    logger.info("=" * 60)

    companies = _load_companies()

    # Step 2 — current company job IDs in DB
    db_job_ids = _get_all_company_job_ids()
    logger.info("Company jobs currently in DB: %d", len(db_job_ids))

    total_new = 0
    total_seen = 0
    total_failed = 0

    # Step 3+4+5 — per-company fetch, insert new, mark seen
    for company in companies:
        name = company.get("name", "?")
        source_type = company.get("source_type", "html")

        if source_type == "skip":
            logger.debug("Skipping %s (source_type=skip)", name)
            continue

        logger.info("Checking: %s [%s]", name, source_type)
        try:
            raw_jobs = fetch_jobs(company)
        except Exception as exc:
            logger.warning("  FAILED to fetch %s: %s", name, exc)
            total_failed += 1
            continue

        seen_ids: set[str] = set()
        for job in raw_jobs:
            job_id = job["job_id"]
            seen_ids.add(job_id)
            if job_id not in db_job_ids:
                insert_job(job)
                logger.info("  + NEW  %s — %s", job_id, job.get("title", ""))
                total_new += 1
                db_job_ids.add(job_id)  # prevent re-insert within same run

        if seen_ids:
            mark_jobs_active(seen_ids)
            total_seen += len(seen_ids)

        time.sleep(config.REQUEST_DELAY_SECONDS)

    # Step 6 — age-based deactivation of stale job listings
    deactivated = _deactivate_stale_company_jobs()
    if deactivated:
        logger.info("Deactivated %d stale company job listing(s) (not seen in %d days)",
                    deactivated, INACTIVE_AFTER_DAYS)

    # Step 7 — summary
    elapsed = (datetime.now() - start).total_seconds()
    logger.info("-" * 60)
    logger.info("Run complete in %.1f seconds.", elapsed)
    logger.info(
        "Summary: %d companies checked  |  +%d new jobs  |  "
        "%d re-seen  |  -%d deactivated  |  %d failed",
        len([c for c in companies if c.get("source_type") != "skip"]),
        total_new,
        total_seen,
        deactivated,
        total_failed,
    )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
