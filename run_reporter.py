"""
Daily reporting entry point.

After run_evaluator.py in the daily cron chain.
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

    top_email = int(cfg.get("top_jobs_email", 10))
    top_telegram = int(cfg.get("top_jobs_telegram", 5))
    min_score = int(cfg.get("min_score_to_report", 0))

    pharmi_apply_only = bool(cfg.get("pharmiweb_report_only_should_apply", True))
    mark_pharmi_rest = bool(cfg.get("mark_unreported_pharmiweb_evaluated_as_sent", True))

    company_apply_only = bool(cfg.get("company_watchlist_report_only_should_apply", False))
    company_max = cfg.get("company_watchlist_max_email")
    company_limit = int(company_max) if company_max is not None else None

    nrw_apply_only = bool(cfg.get("nrw_major_report_only_should_apply", True))
    nrw_max = int(cfg.get("nrw_major_max_email", 10))

    logger.info(
        "Config: email top-%d | telegram top-%d | min_score=%d | "
        "pharmiweb should_apply_only=%s",
        top_email,
        top_telegram,
        min_score,
        pharmi_apply_only,
    )

    logger.info("Step 1 – Fetching unsent jobs …")
    jobs = reporter.db.fetch_unsent_jobs(
        limit=top_email,
        min_score=min_score,
        only_should_apply=pharmi_apply_only,
    )
    company_jobs = reporter.db.fetch_unsent_company_jobs(
        min_score=min_score,
        only_should_apply=company_apply_only,
        limit=company_limit,
    )
    company_jobs_found = reporter.db.count_unsent_company_jobs()

    nrw_jobs = reporter.db.fetch_unsent_nrw_major_jobs(
        min_score=min_score,
        only_should_apply=nrw_apply_only,
        limit=nrw_max,
    )
    nrw_total_unsent = reporter.db.count_unsent_nrw_major_all()

    logger.info(
        "  pharmiweb: %d | watchlist shown: %d (%d unsent prescreened) | "
        "NRW major: %d (%d unsent prescreened)",
        len(jobs),
        len(company_jobs),
        company_jobs_found,
        len(nrw_jobs),
        nrw_total_unsent,
    )

    has_content = jobs or company_jobs or nrw_jobs
    has_notes = (company_jobs_found > len(company_jobs)) or (
        nrw_total_unsent > len(nrw_jobs) and nrw_total_unsent > 0
    )

    if not has_content and not has_notes:
        logger.info("No unsent evaluated jobs to report. Exiting.")
        logger.info("=" * 60)
        return

    for j in jobs:
        logger.info(
            "  [pharmiweb]  %3d/100  APPLY  %s",
            int(j.get("score") or 0),
            (j.get("title") or "")[:50],
        )
    for j in company_jobs:
        logger.info(
            "  [watchlist]  %s  %s",
            int(j.get("score") or 0),
            (j.get("title") or "")[:45],
        )
    for j in nrw_jobs:
        logger.info(
            "  [nrw_major]  %s  %s",
            int(j.get("score") or 0),
            (j.get("title") or "")[:45],
        )

    logger.info("-" * 60)
    logger.info("Step 2 – Email …")
    stats = reporter.db.count_evaluated_today()
    html_body = reporter.formatter.build_email_html(
        jobs,
        stats=stats,
        company_jobs=company_jobs or None,
        company_jobs_found=company_jobs_found,
        min_score=min_score,
        nrw_major_jobs=nrw_jobs or None,
        nrw_major_found=nrw_total_unsent if not nrw_jobs else 0,
    )
    n_show = len(jobs) + len(company_jobs) + len(nrw_jobs)
    subject = (
        f"Pharma Job Digest — {n_show} match{'es' if n_show != 1 else ''}"
        f" — {date.today().strftime('%-d %b %Y')}"
    )
    if config.REPORTER_DRY_RUN:
        logger.info(
            "REPORTER_DRY_RUN=1 — skipping email, Telegram, and mark_as_sent "
            "(set REPORTER_DRY_RUN=0 for real sends)."
        )
        logger.info("=" * 60)
        logger.info("Dry run done. Would have reported %d job(s).", n_show)
        logger.info("=" * 60)
        return

    email_sent = False
    try:
        reporter.email_sender.send(subject=subject, html_body=html_body)
        email_sent = True
    except Exception as exc:
        logger.error("Email send failed: %s", exc)
        sys.exit(1)

    logger.info("-" * 60)
    logger.info("Step 3 – Telegram …")
    tg_text = reporter.formatter.build_telegram_text(
        jobs,
        top_n=top_telegram,
        company_jobs=company_jobs or None,
        company_jobs_found=company_jobs_found,
        min_score=min_score,
        nrw_major_jobs=nrw_jobs or None,
        nrw_major_found=nrw_total_unsent if not nrw_jobs else 0,
    )
    try:
        reporter.telegram_sender.send(tg_text)
    except Exception as exc:
        logger.warning("Telegram failed: %s", exc)

    logger.info("-" * 60)
    all_ids = [j["job_id"] for j in jobs + company_jobs + nrw_jobs]
    reporter.db.mark_as_sent(all_ids)
    if mark_pharmi_rest and email_sent:
        n = reporter.db.mark_pharmiweb_evaluated_non_apply_as_sent()
        if n:
            logger.info("Marked %d other pharmiweb job(s) as sent (non-apply).", n)

    logger.info("=" * 60)
    logger.info("Done. Reported %d job(s).", len(all_ids))
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
