"""
Database migration script — safe to re-run at any time.

Applies all schema changes needed for the evaluation module:
  1. Adds `passed_prescreening` column to the `jobs` table.
  2. Creates the `evaluation_runs` table.

Usage:
    python scripts/migrate_db.py
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper.db import get_cursor, get_connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

MIGRATIONS = [
    (
        "Add passed_prescreening column to jobs",
        """
        ALTER TABLE jobs
          ADD COLUMN IF NOT EXISTS passed_prescreening BOOLEAN DEFAULT NULL
        """,
    ),
    (
        "Create evaluation_runs table",
        """
        CREATE TABLE IF NOT EXISTS evaluation_runs (
            run_id              SERIAL       PRIMARY KEY,
            run_at              TIMESTAMP    NOT NULL DEFAULT NOW(),
            model               VARCHAR(64)  NOT NULL,
            cv_version          VARCHAR(64)  NOT NULL,
            requirements_hash   VARCHAR(64)  NOT NULL,
            jobs_total          INT          NOT NULL,
            jobs_prefiltered    INT          NOT NULL,
            jobs_evaluated      INT          NOT NULL,
            jobs_should_apply   INT          NOT NULL,
            tokens_input        INT,
            tokens_output       INT,
            tokens_total        INT,
            estimated_cost_usd  FLOAT,
            run_success         BOOLEAN      NOT NULL DEFAULT TRUE,
            error_message       TEXT
        )
        """,
    ),
]


def main() -> None:
    logger.info("Testing database connection …")
    try:
        conn = get_connection()
        conn.close()
    except Exception as exc:
        logger.error("Could not connect to the database: %s", exc)
        sys.exit(1)

    logger.info("Running %d migration(s) …", len(MIGRATIONS))
    with get_cursor() as cur:
        for description, sql in MIGRATIONS:
            logger.info("  → %s", description)
            cur.execute(sql)

    logger.info("All migrations applied successfully.")


if __name__ == "__main__":
    main()
