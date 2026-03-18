import logging
from contextlib import contextmanager
from datetime import date, datetime
from typing import Generator

import psycopg2
import psycopg2.extras

from scraper import config

logger = logging.getLogger(__name__)

# PostgreSQL text cannot contain NUL; scraped HTML/JSON sometimes includes \x00.
_JOB_TEXT_KEYS = (
    "job_id",
    "url",
    "title",
    "employer",
    "location",
    "salary",
    "start_date",
    "closing_date",
    "discipline",
    "hours",
    "contract_type",
    "experience_level",
    "job_details",
    "source",
)


def _strip_nul_from_job_strings(job: dict) -> None:
    for key in _JOB_TEXT_KEYS:
        val = job.get(key)
        if isinstance(val, str) and "\x00" in val:
            job[key] = val.replace("\x00", "")


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    -- Core identity
    job_id          VARCHAR(32)  PRIMARY KEY,
    url             TEXT         NOT NULL,
    title           TEXT,

    -- Job metadata
    employer        TEXT,
    location        TEXT,
    salary          TEXT,
    start_date      TEXT,
    closing_date    TEXT,
    discipline      TEXT,
    hours           TEXT,
    contract_type   TEXT,
    experience_level TEXT,
    job_details     TEXT,

    -- Tracking
    first_seen      DATE         NOT NULL,
    last_seen       DATE         NOT NULL,
    job_active      BOOLEAN      NOT NULL DEFAULT TRUE,

    -- Future: CV scoring (populated by a separate evaluation module)
    evaluated       BOOLEAN      NOT NULL DEFAULT FALSE,
    evaluated_at    TIMESTAMP,
    cv_version      VARCHAR(64),
    score           FLOAT,
    score_reasoning TEXT,
    should_apply    BOOLEAN      NOT NULL DEFAULT FALSE,
    applied         BOOLEAN      NOT NULL DEFAULT FALSE
);
"""


def get_connection() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        dbname=config.DB_NAME,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
    )


@contextmanager
def get_cursor() -> Generator[psycopg2.extensions.cursor, None, None]:
    conn = get_connection()
    try:
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                yield cur
    finally:
        conn.close()


def create_schema() -> None:
    with get_cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
    logger.info("Schema created / verified.")


def get_all_active_job_ids() -> set[str]:
    with get_cursor() as cur:
        cur.execute("SELECT job_id FROM jobs WHERE job_active = TRUE")
        return {row["job_id"] for row in cur.fetchall()}


def get_all_job_ids() -> set[str]:
    with get_cursor() as cur:
        cur.execute("SELECT job_id FROM jobs")
        return {row["job_id"] for row in cur.fetchall()}


def get_job_ids_by_source(source: str) -> set[str]:
    """Return all job IDs for a specific source.

    Treats NULL source rows as 'pharmiweb' for backward compatibility
    with jobs inserted before the source column was added.
    """
    with get_cursor() as cur:
        cur.execute(
            "SELECT job_id FROM jobs WHERE source = %s"
            " OR (source IS NULL AND %s = 'pharmiweb')",
            (source, source),
        )
        return {row["job_id"] for row in cur.fetchall()}


def insert_job(job: dict) -> None:
    today = date.today()
    sql = """
        INSERT INTO jobs (
            job_id, url, title, employer, location, salary,
            start_date, closing_date, discipline, hours,
            contract_type, experience_level, job_details,
            first_seen, last_seen, job_active, source
        ) VALUES (
            %(job_id)s, %(url)s, %(title)s, %(employer)s, %(location)s, %(salary)s,
            %(start_date)s, %(closing_date)s, %(discipline)s, %(hours)s,
            %(contract_type)s, %(experience_level)s, %(job_details)s,
            %(first_seen)s, %(last_seen)s, TRUE, %(source)s
        )
        ON CONFLICT (job_id) DO NOTHING
    """
    job.setdefault("first_seen", today)
    job.setdefault("last_seen", today)
    job.setdefault("source", "pharmiweb")
    _strip_nul_from_job_strings(job)
    with get_cursor() as cur:
        cur.execute(sql, job)
    logger.debug("Inserted job %s: %s", job["job_id"], job.get("title"))


def mark_jobs_active(job_ids: set[str]) -> None:
    if not job_ids:
        return
    today = date.today()
    sql = """
        UPDATE jobs
        SET job_active = TRUE, last_seen = %s
        WHERE job_id = ANY(%s)
    """
    with get_cursor() as cur:
        cur.execute(sql, (today, list(job_ids)))
    logger.debug("Marked %d jobs active.", len(job_ids))


def mark_jobs_inactive(job_ids: set[str]) -> None:
    if not job_ids:
        return
    sql = "UPDATE jobs SET job_active = FALSE WHERE job_id = ANY(%s)"
    with get_cursor() as cur:
        cur.execute(sql, (list(job_ids),))
    logger.info("Marked %d jobs inactive.", len(job_ids))
