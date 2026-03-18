"""
NRW major employers checker — jobs that are remote (DE/EU) or hybrid in NRW.

  python run_nrw_major_checker.py

Cron: run_scraper → run_company_checker → run_nrw_major_checker → run_evaluator → run_reporter
"""

import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from scraper import config
from scraper.db import get_cursor, insert_job, mark_jobs_active
from scraper.nrw_major_fetchers import fetch_jobs_for_employer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(name)s – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("run_nrw_major_checker")

ROOT = Path(__file__).parent
YAML_PATH = ROOT / "input_data" / "nrw_major_employers.yaml"
INACTIVE_AFTER_DAYS = 30


def _load_employers() -> list[dict]:
    if not YAML_PATH.exists():
        logger.error("Missing %s", YAML_PATH)
        sys.exit(1)
    with YAML_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    rows = data.get("employers", [])
    logger.info("Loaded %d NRW major employer(s)", len(rows))
    return rows


def _db_ids_nrw_major() -> set[str]:
    with get_cursor() as cur:
        cur.execute("SELECT job_id FROM jobs WHERE source = 'company_nrw_major'")
        return {row["job_id"] for row in cur.fetchall()}


def _deactivate_stale() -> int:
    cutoff = date.today() - timedelta(days=INACTIVE_AFTER_DAYS)
    with get_cursor() as cur:
        cur.execute(
            """
            UPDATE jobs SET job_active = FALSE
             WHERE source = 'company_nrw_major'
               AND job_active = TRUE
               AND last_seen < %s
            """,
            (cutoff,),
        )
        return cur.rowcount


def main() -> None:
    start = datetime.now()
    logger.info("=" * 60)
    logger.info("NRW major employer checker started")
    logger.info("=" * 60)

    employers = _load_employers()
    db_ids = _db_ids_nrw_major()
    total_new = 0
    total_seen = 0
    failed = 0

    for row in employers:
        name = row.get("name", "?")
        logger.info("Checking: %s [%s]", name, row.get("source_type"))
        try:
            raw = fetch_jobs_for_employer(row)
        except Exception as exc:
            logger.warning("  FAILED %s: %s", name, exc)
            failed += 1
            continue

        seen: set[str] = set()
        for job in raw:
            jid = job["job_id"]
            seen.add(jid)
            if jid not in db_ids:
                insert_job(job)
                logger.info("  + NEW  %s — %s", jid, job.get("title", "")[:60])
                total_new += 1
                db_ids.add(jid)
        if seen:
            mark_jobs_active(seen)
            total_seen += len(seen)
        logger.info("  %s: %d eligible job(s) this run", name, len(seen))
        time.sleep(config.REQUEST_DELAY_SECONDS)

    deactivated = _deactivate_stale()
    elapsed = (datetime.now() - start).total_seconds()
    logger.info("-" * 60)
    logger.info(
        "Done in %.1fs | +%d new | %d seen | -%d stale | %d employer fetch failures",
        elapsed,
        total_new,
        total_seen,
        deactivated,
        failed,
    )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
