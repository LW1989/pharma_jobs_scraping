"""
Full watchlist sweep — tests all companies in input_data/companies.yaml.
No DB writes. Prints a results table at the end.

Usage:
    python scripts/test_all_companies.py           # full run (all 44 companies)
    python scripts/test_all_companies.py --api-only # skip HTML+LLM, only test API companies
    python scripts/test_all_companies.py --name "Cube BioTech"  # test a single company by name

Cost estimate (HTML+LLM mode, ~41 HTML companies):
    ~41 × gpt-5-mini calls ≈ $0.40 total
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import logging
import time

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("test_all_companies")

from scraper.company_scraper import fetch_jobs

COMPANIES_PATH = Path(__file__).resolve().parent.parent / "input_data" / "companies.yaml"


def _load_companies() -> list[dict]:
    with COMPANIES_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)["companies"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-only", action="store_true",
                        help="Only run personio/workable/recruitee, skip html")
    parser.add_argument("--name", metavar="NAME",
                        help="Test only the company with this exact name")
    args = parser.parse_args()

    companies = _load_companies()

    if args.name:
        companies = [c for c in companies if c["name"] == args.name]
        if not companies:
            logger.error("No company named %r found in companies.yaml", args.name)
            sys.exit(1)

    if args.api_only:
        companies = [c for c in companies if c.get("source_type") != "html"]
        logger.info("--api-only: running %d non-HTML companies", len(companies))

    print()
    print("=" * 70)
    print(f"  Company Watchlist — full sweep ({len(companies)} companies, no DB writes)")
    print("=" * 70)

    results = []  # (name, source_type, job_count, status, sample_titles)

    for i, company in enumerate(companies, 1):
        name = company["name"]
        source_type = company.get("source_type", "html")

        logger.info("[%d/%d]  %-30s [%s]", i, len(companies), name, source_type)

        t0 = time.time()
        try:
            jobs = fetch_jobs(company)
            elapsed = time.time() - t0
            status = "ok"
            job_count = len(jobs)
            sample = [j["title"] for j in jobs[:3]]
            if job_count:
                for j in jobs:
                    logger.info("    + %s | %s", j["title"], j.get("location") or "—")
            else:
                logger.info("    (no open jobs found)")
        except Exception as exc:
            elapsed = time.time() - t0
            status = f"ERROR: {exc}"
            job_count = -1
            sample = []
            logger.warning("    FAILED: %s", exc)

        results.append((name, source_type, job_count, status, sample, elapsed))

    # ── Summary table ──────────────────────────────────────────────────────
    print()
    print("=" * 70)
    print("  RESULTS SUMMARY")
    print("=" * 70)
    print(f"  {'Company':<30} {'Type':<10} {'Jobs':>5}  {'Status'}")
    print("  " + "-" * 65)

    total_jobs = 0
    ok_count = 0
    empty_count = 0
    error_count = 0

    for name, source_type, job_count, status, sample, elapsed in results:
        if job_count >= 0:
            total_jobs += job_count
            if job_count > 0:
                ok_count += 1
                flag = f"{job_count} jobs"
            else:
                empty_count += 1
                flag = "0 jobs (none listed or JS-only)"
        else:
            error_count += 1
            flag = status[:50]

        jobs_display = str(job_count) if job_count >= 0 else "ERR"
        print(f"  {name:<30} {source_type:<10} {jobs_display:>5}  {flag}")

    print()
    print(f"  Total: {total_jobs} jobs found across {ok_count} companies")
    print(f"  Empty (no jobs): {empty_count}   Errors: {error_count}")
    print("=" * 70)

    if error_count:
        print()
        print("  ERRORS (need attention):")
        for name, source_type, job_count, status, _, _ in results:
            if job_count < 0:
                print(f"    {name}: {status}")

    sys.exit(0 if error_count == 0 else 1)


if __name__ == "__main__":
    main()
