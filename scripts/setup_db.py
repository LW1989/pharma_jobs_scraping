"""
One-time database setup script.

Run this once against any target database (local Docker or Hetzner) to create
the jobs table:

    python scripts/setup_db.py

The database connection is read from the .env file in the project root.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper.db import create_schema, get_connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("Testing database connection …")
    try:
        conn = get_connection()
        conn.close()
    except Exception as exc:
        logger.error("Could not connect to the database: %s", exc)
        sys.exit(1)

    logger.info("Connection successful. Creating schema …")
    create_schema()
    logger.info("Done. The 'jobs' table is ready.")


if __name__ == "__main__":
    main()
