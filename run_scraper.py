"""
Daily scraper entry point – called by cron.

Workflow:
  1. Collect all current job IDs from every search results page.
  2. Compare against the database:
     - New jobs       → scrape detail page and INSERT.
     - Known jobs     → UPDATE last_seen and set job_active = TRUE.
     - Missing jobs   → set job_active = FALSE.
  3. Log a summary.
"""

import logging
import sys
import time
from datetime import datetime

from scraper import config
from scraper.db import (
    get_job_ids_by_source,
    insert_job,
    mark_jobs_active,
    mark_jobs_inactive,
)
from scraper.scraper import scrape_all_job_links, scrape_job_detail

# ---------------------------------------------------------------------------
# Logging – write to stdout so cron can redirect to a log file
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(name)s – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("run_scraper")


def main() -> None:
    start = datetime.now()
    logger.info("=" * 60)
    logger.info("Scrape run started at %s", start.isoformat())
    logger.info("=" * 60)

    # ------------------------------------------------------------------
    # Step 1: Collect all active job IDs from the search listing pages
    # ------------------------------------------------------------------
    logger.info("Step 1 – Collecting job links from search pages …")
    try:
        live_jobs: dict[str, str] = scrape_all_job_links()
    except Exception as exc:
        logger.error("Fatal: could not scrape listing pages: %s", exc)
        sys.exit(1)

    live_ids = set(live_jobs.keys())
    logger.info("Found %d live jobs on the site.", len(live_ids))

    # ------------------------------------------------------------------
    # Step 2: Compare with the database
    # ------------------------------------------------------------------
    logger.info("Step 2 – Comparing with database …")
    db_ids = get_job_ids_by_source("pharmiweb")

    new_ids = live_ids - db_ids
    known_ids = live_ids & db_ids
    gone_ids = db_ids - live_ids

    logger.info(
        "New: %d  |  Still active: %d  |  No longer listed: %d",
        len(new_ids),
        len(known_ids),
        len(gone_ids),
    )

    # ------------------------------------------------------------------
    # Step 3: Insert new jobs (requires fetching each detail page)
    # ------------------------------------------------------------------
    if new_ids:
        logger.info("Step 3 – Scraping %d new job detail pages …", len(new_ids))
        inserted = 0
        failed = 0
        for i, job_id in enumerate(sorted(new_ids), start=1):
            url = live_jobs[job_id]
            logger.info("  [%d/%d] job %s", i, len(new_ids), job_id)
            try:
                job_data = scrape_job_detail(job_id, url)
                insert_job(job_data)
                inserted += 1
            except Exception as exc:
                logger.warning("  Failed to insert job %s: %s", job_id, exc)
                failed += 1
            time.sleep(config.REQUEST_DELAY_SECONDS)
        logger.info("Inserted %d new jobs (%d failed).", inserted, failed)
    else:
        logger.info("Step 3 – No new jobs to insert.")

    # ------------------------------------------------------------------
    # Step 4: Mark known jobs as still active
    # ------------------------------------------------------------------
    if known_ids:
        logger.info("Step 4 – Updating last_seen for %d known jobs …", len(known_ids))
        mark_jobs_active(known_ids)
    else:
        logger.info("Step 4 – No previously known jobs to update.")

    # ------------------------------------------------------------------
    # Step 5: Mark disappeared jobs as inactive
    # ------------------------------------------------------------------
    if gone_ids:
        logger.info("Step 5 – Marking %d jobs as inactive …", len(gone_ids))
        mark_jobs_inactive(gone_ids)
    else:
        logger.info("Step 5 – No jobs to mark inactive.")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    elapsed = (datetime.now() - start).total_seconds()
    logger.info("-" * 60)
    logger.info("Run complete in %.1f seconds.", elapsed)
    logger.info(
        "Summary: +%d new  |  ~%d updated  |  -%d deactivated",
        len(new_ids),
        len(known_ids),
        len(gone_ids),
    )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
