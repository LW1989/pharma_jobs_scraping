"""
Daily reporting entry point.

Workflow:
  1. Load requirements.yaml for reporting config.
  2. Fetch top-N evaluated, unsent jobs from the DB (ordered by score).
  3. Build and send an HTML email with the top 10 jobs (full details + AI reasoning).
  4. Build and send a Telegram digest with the top 5 jobs (compact format).
  5. Mark all fetched jobs as sent so they are never recommended again.

Usage:
    python run_reporter.py

Run after run_evaluator.py in the daily cron chain:
    run_scraper.py → run_evaluator.py → run_reporter.py
"""

import logging
import sys
from datetime import date
from pathlib import Path

import yaml

from scraper import config
import reporter.db
import reporter.formatter
import reporter.email_sender
import reporter.telegram_sender

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(name)s – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("run_reporter")

ROOT = Path(__file__).parent
REQUIREMENTS_PATH = ROOT / "requirements.yaml"


def _load_requirements() -> dict:
    if not REQUIREMENTS_PATH.exists():
        logger.error("requirements.yaml not found.")
        sys.exit(1)
    with REQUIREMENTS_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> None:
    logger.info("=" * 60)
    logger.info("Reporter")
    logger.info("=" * 60)

    requirements = _load_requirements()
    cfg = requirements.get("reporting", {})

    top_email    = int(cfg.get("top_jobs_email", 10))
    top_telegram = int(cfg.get("top_jobs_telegram", 5))
    min_score    = int(cfg.get("min_score_to_report", 0))

    logger.info("Config:  email top-%d  |  telegram top-%d  |  min_score=%d",
                top_email, top_telegram, min_score)

    # Step 1 — fetch unsent jobs from both sources
    logger.info("Step 1 – Fetching unsent evaluated jobs …")
    jobs = reporter.db.fetch_unsent_jobs(limit=top_email, min_score=min_score)
    company_jobs = reporter.db.fetch_unsent_company_jobs()

    logger.info("  pharmiweb: %d job(s)  |  company watchlist: %d job(s)",
                len(jobs), len(company_jobs))

    if not jobs and not company_jobs:
        logger.info("No unsent evaluated jobs found. Nothing to report.")
        logger.info("=" * 60)
        return

    for j in jobs:
        flag = "APPLY" if j.get("should_apply") else "review"
        logger.info("  [pharmiweb]  %3d/100  [%s]  %s @ %s",
                    j.get("score", 0), flag,
                    (j.get("title") or "")[:50], j.get("employer") or "")
    for j in company_jobs:
        score_str = f"{int(j.get('score') or 0)}/100" if j.get("score") is not None else "unscored"
        logger.info("  [watchlist]  %s  %s @ %s",
                    score_str, (j.get("title") or "")[:50], j.get("employer") or "")

    # Step 2 — build and send email
    logger.info("-" * 60)
    logger.info("Step 2 – Sending email report to %s …", config.REPORT_TO or "(not configured)")
    stats = reporter.db.count_evaluated_today()
    html_body = reporter.formatter.build_email_html(
        jobs, stats=stats, company_jobs=company_jobs or None
    )
    total_count = len(jobs) + len(company_jobs)
    subject = (
        f"Pharma Job Digest — {total_count} match{'es' if total_count != 1 else ''}"
        f" — {date.today().strftime('%-d %b %Y')}"
    )
    email_sent = False
    try:
        reporter.email_sender.send(subject=subject, html_body=html_body)
        email_sent = True
    except Exception as exc:
        logger.error("Email send failed: %s", exc)
        logger.error("Jobs have NOT been marked as sent — they will be retried tomorrow.")
        sys.exit(1)

    # Step 3 — build and send Telegram
    logger.info("-" * 60)
    logger.info("Step 3 – Sending Telegram digest (top %d pharmiweb + watchlist) …", top_telegram)
    tg_text = reporter.formatter.build_telegram_text(
        jobs, top_n=top_telegram, company_jobs=company_jobs or None
    )
    try:
        reporter.telegram_sender.send(tg_text)
    except Exception as exc:
        logger.warning("Telegram send failed (email was delivered): %s", exc)
        # Do not abort — email already delivered, still mark as sent

    # Step 4 — mark both sources as sent
    logger.info("-" * 60)
    all_ids = [j["job_id"] for j in jobs + company_jobs]
    logger.info("Step 4 – Marking %d job(s) as sent …", len(all_ids))
    reporter.db.mark_as_sent(all_ids)

    logger.info("=" * 60)
    logger.info("Done. Reported %d pharmiweb + %d watchlist job(s). Email: %s | Telegram: attempted.",
                len(jobs), len(company_jobs), "OK" if email_sent else "FAILED")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
