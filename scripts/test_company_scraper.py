"""
Local test for scraper/company_scraper.py — no DB writes, no cron needed.

Tests:
  1. Personio API   — Prosion (Köln)
  2. Workable API   — Allucent (Köln)
  3. Recruitee API  — Cellex (Cologne)
  4. HTML + LLM     — BioEcho (Köln)  (1 LLM call, ~$0.01)

Usage:
    python scripts/test_company_scraper.py

Pass --skip-llm to skip the HTML+LLM test if you want zero OpenAI cost.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import logging
import json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("test_company_scraper")

from scraper.company_scraper import fetch_jobs

TEST_COMPANIES = [
    {
        "name": "Prosion",
        "city": "Köln",
        "country": "Germany",
        "career_url": "https://prosion-gmbh.jobs.personio.de",
        "source_type": "personio",
        "slug": "prosion-gmbh",
    },
    {
        "name": "Allucent",
        "city": "Köln",
        "country": "Germany",
        "career_url": "https://apply.workable.com/allucent/",
        "source_type": "workable",
        "slug": "allucent",
    },
    {
        "name": "Cellex",
        "city": "Cologne",
        "country": "Germany",
        "career_url": "https://cellexgmbh.recruitee.com",
        "source_type": "recruitee",
        "slug": "cellexgmbh",
    },
]

HTML_LLM_COMPANY = {
    "name": "Cube BioTech",
    "city": "Monheim",
    "country": "Germany",
    "career_url": "https://cube-biotech.com/information/job-opportunities/",
    "source_type": "html",
}


def _print_jobs(company_name: str, jobs: list[dict]) -> None:
    if not jobs:
        logger.info("  → 0 jobs (either none listed or page blocked)")
        return
    for j in jobs:
        logger.info(
            "  → [%s] %s | %s | %s",
            j["job_id"],
            j["title"],
            j.get("location") or "—",
            j["url"],
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-llm", action="store_true",
                        help="Skip the HTML+LLM test (no OpenAI cost)")
    args = parser.parse_args()

    print()
    print("=" * 65)
    print("  Company Scraper — local test (no DB writes)")
    print("=" * 65)

    all_ok = True
    total_jobs = 0

    for company in TEST_COMPANIES:
        print()
        logger.info("Testing %s  [%s]", company["name"], company["source_type"])
        try:
            jobs = fetch_jobs(company)
            _print_jobs(company["name"], jobs)
            total_jobs += len(jobs)
        except Exception as exc:
            logger.error("  FAILED: %s", exc)
            all_ok = False

    if not args.skip_llm:
        print()
        logger.info("Testing %s  [html+llm] — 1 OpenAI call", HTML_LLM_COMPANY["name"])
        try:
            jobs = fetch_jobs(HTML_LLM_COMPANY)
            _print_jobs(HTML_LLM_COMPANY["name"], jobs)
            total_jobs += len(jobs)
        except Exception as exc:
            logger.error("  FAILED: %s", exc)
            all_ok = False
    else:
        logger.info("Skipping HTML+LLM test (--skip-llm)")

    print()
    print("=" * 65)
    status = "ALL PASSED" if all_ok else "SOME FAILURES — see above"
    logger.info("Result: %s  |  %d total jobs found across tested companies", status, total_jobs)
    print("=" * 65)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
