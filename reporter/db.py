"""
Database helpers for the reporter module.

Fetches evaluated, unsent jobs and marks them as sent after delivery.
"""

import logging
from datetime import datetime
from typing import Optional

from scraper.db import get_cursor

logger = logging.getLogger(__name__)


def fetch_unsent_jobs(
    limit: int = 10,
    min_score: int = 0,
    only_should_apply: bool = False,
) -> list[dict]:
    """
    Return evaluated pharmiweb jobs that have not yet been reported,
    ordered by score descending.
    """
    apply_clause = " AND should_apply = TRUE " if only_should_apply else ""
    sql = f"""
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
          {apply_clause}
        ORDER BY score DESC NULLS LAST
        LIMIT %s
    """
    with get_cursor() as cur:
        cur.execute(sql, (min_score, limit))
        return [dict(row) for row in cur.fetchall()]


def mark_pharmiweb_evaluated_non_apply_as_sent() -> int:
    """
    Clear unsent queue for pharmiweb jobs that did not get should_apply.
    """
    sql = """
        UPDATE jobs
        SET job_sent = TRUE, job_sent_at = %s
        WHERE (source = 'pharmiweb' OR source IS NULL)
          AND evaluated = TRUE
          AND should_apply = FALSE
          AND (job_sent = FALSE OR job_sent IS NULL)
    """
    with get_cursor() as cur:
        cur.execute(sql, (datetime.now(),))
        return cur.rowcount


def fetch_unsent_company_jobs(
    min_score: int = 0,
    only_should_apply: bool = False,
    limit: Optional[int] = None,
) -> list[dict]:
    """
    Return evaluated company_direct jobs that passed pre-screening, meet the
    minimum score, and have not yet been reported, ordered by score descending.
    No row limit — typically only a handful per day.
    """
    apply_clause = " AND should_apply = TRUE " if only_should_apply else ""
    lim = f" LIMIT {int(limit)} " if limit is not None and limit > 0 else ""
    sql = f"""
        SELECT
            job_id, title, employer, location,
            score, score_reasoning, should_apply, url,
            contract_type, hours
        FROM jobs
        WHERE source              = 'company_direct'
          AND job_active          = TRUE
          AND evaluated           = TRUE
          AND passed_prescreening = TRUE
          AND score               >= %s
          AND (job_sent = FALSE OR job_sent IS NULL)
          {apply_clause}
        ORDER BY score DESC NULLS LAST
        {lim}
    """
    with get_cursor() as cur:
        cur.execute(sql, (min_score,))
        return [dict(row) for row in cur.fetchall()]


def fetch_unsent_nrw_major_jobs(
    min_score: int = 0,
    only_should_apply: bool = False,
    limit: Optional[int] = None,
) -> list[dict]:
    apply_clause = " AND should_apply = TRUE " if only_should_apply else ""
    lim = f" LIMIT {int(limit)} " if limit is not None and limit > 0 else ""
    sql = f"""
        SELECT
            job_id, title, employer, location,
            score, score_reasoning, should_apply, url,
            contract_type, hours
        FROM jobs
        WHERE source              = 'company_nrw_major'
          AND job_active          = TRUE
          AND evaluated           = TRUE
          AND passed_prescreening = TRUE
          AND score               >= %s
          AND (job_sent = FALSE OR job_sent IS NULL)
          {apply_clause}
        ORDER BY score DESC NULLS LAST
        {lim}
    """
    with get_cursor() as cur:
        cur.execute(sql, (min_score,))
        return [dict(row) for row in cur.fetchall()]


def count_unsent_nrw_major_all() -> int:
    sql = """
        SELECT COUNT(*) AS n
        FROM jobs
        WHERE source              = 'company_nrw_major'
          AND job_active          = TRUE
          AND evaluated           = TRUE
          AND passed_prescreening = TRUE
          AND (job_sent = FALSE OR job_sent IS NULL)
    """
    with get_cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
        return int(row["n"]) if row else 0


def count_unsent_company_jobs() -> int:
    """
    Count all unsent company_direct jobs that passed pre-screening,
    regardless of score. Used to show a 'found but below threshold' note.
    """
    sql = """
        SELECT COUNT(*) AS n
        FROM jobs
        WHERE source              = 'company_direct'
          AND job_active          = TRUE
          AND evaluated           = TRUE
          AND passed_prescreening = TRUE
          AND (job_sent = FALSE OR job_sent IS NULL)
    """
    with get_cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
        return int(row["n"]) if row else 0


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
