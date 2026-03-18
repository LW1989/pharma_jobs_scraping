"""
Database helpers for the evaluation module.

All write operations for evaluation columns live here.
Read operations (fetching jobs to evaluate) also live here
so run_evaluator.py stays thin.
"""

import logging
import os
from datetime import datetime

from scraper.db import get_cursor

logger = logging.getLogger(__name__)


def get_jobs_to_evaluate(cv_version: str) -> list[dict]:
    """
    Return jobs that need (re-)evaluation:
      - evaluated = FALSE, OR
      - cv_version differs from the current CV hash (CV was updated)
    Only active jobs are considered.
    """
    limit_raw = os.environ.get("EVALUATOR_MAX_JOBS", "").strip()
    limit_sql = ""
    params: list = [cv_version]
    if limit_raw.isdigit() and int(limit_raw) > 0:
        limit_sql = f" LIMIT {int(limit_raw)}"
    sql = f"""
        SELECT
            job_id, title, employer, location, salary,
            start_date, closing_date, discipline, hours,
            contract_type, experience_level, job_details, url,
            source
        FROM jobs
        WHERE job_active = TRUE
          AND (evaluated = FALSE OR cv_version IS DISTINCT FROM %s)
        ORDER BY job_id
        {limit_sql}
    """
    with get_cursor() as cur:
        cur.execute(sql, tuple(params))
        rows = [dict(row) for row in cur.fetchall()]
    if limit_raw.isdigit() and int(limit_raw) > 0:
        logger.info(
            "EVALUATOR_MAX_JOBS=%s — evaluating at most %d job(s) this run.",
            limit_raw,
            int(limit_raw),
        )
    return rows


def save_prescreening_fail(job_id: str, reason: str, cv_version: str) -> None:
    """Write pre-screening rejection results back to the jobs table."""
    sql = """
        UPDATE jobs SET
            evaluated            = TRUE,
            evaluated_at         = %s,
            cv_version           = %s,
            passed_prescreening  = FALSE,
            score                = 0,
            score_reasoning      = %s,
            should_apply         = FALSE
        WHERE job_id = %s
    """
    with get_cursor() as cur:
        cur.execute(sql, (datetime.now(), cv_version, reason, job_id))
    logger.debug("Saved pre-screening fail for job %s", job_id)


def save_evaluation(
    job_id: str,
    cv_version: str,
    score: int,
    score_reasoning: str,
    should_apply: bool,
) -> None:
    """Write LLM evaluation results back to the jobs table."""
    sql = """
        UPDATE jobs SET
            evaluated            = TRUE,
            evaluated_at         = %s,
            cv_version           = %s,
            passed_prescreening  = TRUE,
            score                = %s,
            score_reasoning      = %s,
            should_apply         = %s
        WHERE job_id = %s
    """
    with get_cursor() as cur:
        cur.execute(
            sql,
            (datetime.now(), cv_version, score, score_reasoning, should_apply, job_id),
        )
    logger.debug("Saved evaluation for job %s (score=%d)", job_id, score)


def insert_evaluation_run(
    model: str,
    cv_version: str,
    requirements_hash: str,
    jobs_total: int,
    jobs_prefiltered: int,
    jobs_evaluated: int,
    jobs_should_apply: int,
    tokens_input: int,
    tokens_output: int,
    tokens_total: int,
    estimated_cost_usd: float,
    run_success: bool = True,
    error_message: str | None = None,
) -> int:
    """Insert a run record into evaluation_runs and return the new run_id."""
    sql = """
        INSERT INTO evaluation_runs (
            model, cv_version, requirements_hash,
            jobs_total, jobs_prefiltered, jobs_evaluated, jobs_should_apply,
            tokens_input, tokens_output, tokens_total,
            estimated_cost_usd, run_success, error_message
        ) VALUES (
            %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s
        )
        RETURNING run_id
    """
    with get_cursor() as cur:
        cur.execute(
            sql,
            (
                model, cv_version, requirements_hash,
                jobs_total, jobs_prefiltered, jobs_evaluated, jobs_should_apply,
                tokens_input, tokens_output, tokens_total,
                estimated_cost_usd, run_success, error_message,
            ),
        )
        row = cur.fetchone()
        return row["run_id"]
