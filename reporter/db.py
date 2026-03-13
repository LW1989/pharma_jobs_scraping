"""
Database helpers for the reporter module.

Fetches evaluated, unsent jobs and marks them as sent after delivery.
"""

import logging
from datetime import datetime

from scraper.db import get_cursor

logger = logging.getLogger(__name__)


def fetch_unsent_jobs(limit: int = 10, min_score: int = 0) -> list[dict]:
    """
    Return evaluated pharmiweb jobs that have not yet been reported,
    ordered by score descending.

    Args:
        limit:      Maximum number of jobs to return.
        min_score:  Only include jobs with score >= this value (0 = include all).
    """
    sql = """
        SELECT
            job_id, title, employer, location, salary,
            start_date, closing_date, discipline, hours,
            contract_type, experience_level,
            score, score_reasoning, should_apply, url
        FROM jobs
        WHERE job_active        = TRUE
          AND evaluated         = TRUE
          AND (job_sent = FALSE OR job_sent IS NULL)
          AND score             >= %s
          AND (source = 'pharmiweb' OR source IS NULL)
        ORDER BY score DESC NULLS LAST
        LIMIT %s
    """
    with get_cursor() as cur:
        cur.execute(sql, (min_score, limit))
        return [dict(row) for row in cur.fetchall()]


def fetch_unsent_company_jobs() -> list[dict]:
    """
    Return evaluated company_direct jobs that have not yet been reported,
    ordered by score descending. No row limit — typically only a handful per day.
    """
    sql = """
        SELECT
            job_id, title, employer, location,
            score, score_reasoning, should_apply, url,
            contract_type, hours
        FROM jobs
        WHERE source            = 'company_direct'
          AND job_active        = TRUE
          AND evaluated         = TRUE
          AND (job_sent = FALSE OR job_sent IS NULL)
        ORDER BY score DESC NULLS LAST
    """
    with get_cursor() as cur:
        cur.execute(sql)
        return [dict(row) for row in cur.fetchall()]


def mark_as_sent(job_ids: list[str]) -> None:
    """Set job_sent=TRUE and job_sent_at=NOW() for all given job IDs."""
    if not job_ids:
        return
    sql = """
        UPDATE jobs
        SET job_sent    = TRUE,
            job_sent_at = %s
        WHERE job_id = ANY(%s)
    """
    with get_cursor() as cur:
        cur.execute(sql, (datetime.now(), job_ids))
    logger.info("Marked %d job(s) as sent.", len(job_ids))


def count_evaluated_today() -> dict:
    """Return summary counts useful for the report header."""
    sql = """
        SELECT
            COUNT(*)                                         AS total_evaluated,
            COUNT(*) FILTER (WHERE should_apply = TRUE)     AS total_apply,
            COUNT(*) FILTER (WHERE should_apply = FALSE
                               AND passed_prescreening = TRUE) AS total_review
        FROM jobs
        WHERE evaluated = TRUE
          AND job_active = TRUE
    """
    with get_cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
        return dict(row) if row else {"total_evaluated": 0, "total_apply": 0, "total_review": 0}
