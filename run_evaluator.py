"""
CV evaluation entry point.

Workflow:
  1. Load cv.txt and requirements.yaml; compute hashes.
  2. Fetch all active jobs that are unevaluated or used a different CV version.
  3. For each job:
       a. Pre-screen against requirements filters (no LLM call).
          Failures → score=0, evaluated=true, passed_prescreening=false.
       b. Pass → call LLM, write score/reasoning/should_apply to DB.
  4. Insert a row into evaluation_runs with token counts and estimated cost.
  5. Log a human-readable summary.

Usage:
    python run_evaluator.py

Prerequisites:
  - cv.txt must exist in the project root (copy cv.txt.example as a template)
  - OPENAI_API_KEY must be set in .env
  - run migrate_db.py once before the first run
"""

import hashlib
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

from scraper import config
from evaluator.db import (
    get_jobs_to_evaluate,
    insert_evaluation_run,
    save_evaluation,
    save_prescreening_fail,
)
from evaluator.llm_client import evaluate
from evaluator.prescreener import prescreen

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(name)s – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("run_evaluator")

ROOT = Path(__file__).parent
CV_PATH = ROOT / "cv.txt"
REQUIREMENTS_PATH = ROOT / "requirements.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _load_cv() -> str:
    if not CV_PATH.exists():
        logger.error(
            "cv.txt not found. Copy cv.txt.example to cv.txt and fill in your CV."
        )
        sys.exit(1)
    text = CV_PATH.read_text(encoding="utf-8").strip()
    if not text:
        logger.error("cv.txt is empty.")
        sys.exit(1)
    return text


def _load_requirements() -> dict:
    if not REQUIREMENTS_PATH.exists():
        logger.error("requirements.yaml not found in project root.")
        sys.exit(1)
    with REQUIREMENTS_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _estimate_cost(model: str, tokens_input: int, tokens_output: int) -> float:
    pricing = config.OPENAI_PRICING.get(model, {"input": 0.15, "output": 0.60})
    return (tokens_input * pricing["input"] + tokens_output * pricing["output"]) / 1_000_000


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    start = datetime.now()
    logger.info("=" * 60)
    logger.info("Evaluation run started at %s", start.isoformat())
    logger.info("=" * 60)

    # ------------------------------------------------------------------
    # Step 1: Load inputs
    # ------------------------------------------------------------------
    cv_text = _load_cv()
    requirements = _load_requirements()

    cv_version = _md5(cv_text)
    requirements_hash = _md5(str(requirements))

    filters = requirements.get("filters", {})
    scoring = requirements.get("scoring", {})
    threshold = int(scoring.get("should_apply_min_score", 70))
    model = scoring.get("model") or config.OPENAI_MODEL

    logger.info("CV version:    %s", cv_version)
    logger.info("Requirements:  %s", requirements_hash)
    logger.info("Model:         %s", model)
    logger.info("Apply threshold: score >= %d", threshold)

    # ------------------------------------------------------------------
    # Step 2: Fetch jobs to evaluate
    # ------------------------------------------------------------------
    logger.info("Step 2 – Fetching jobs to evaluate …")
    jobs = get_jobs_to_evaluate(cv_version)
    jobs_total = len(jobs)
    logger.info("Jobs pending evaluation: %d", jobs_total)

    if jobs_total == 0:
        logger.info("Nothing to do — all jobs already evaluated with current CV.")
        insert_evaluation_run(
            model=model,
            cv_version=cv_version,
            requirements_hash=requirements_hash,
            jobs_total=0,
            jobs_prefiltered=0,
            jobs_evaluated=0,
            jobs_should_apply=0,
            tokens_input=0,
            tokens_output=0,
            tokens_total=0,
            estimated_cost_usd=0.0,
        )
        return

    # ------------------------------------------------------------------
    # Step 3: Pre-screen + LLM evaluate
    # ------------------------------------------------------------------
    jobs_prefiltered = 0
    jobs_evaluated = 0
    jobs_should_apply = 0
    total_tokens_input = 0
    total_tokens_output = 0
    run_success = True
    error_message: str | None = None

    logger.info("Step 3 – Evaluating %d jobs (pre-screen first, then LLM) …", jobs_total)

    PROGRESS_EVERY = 50  # print a running summary every N jobs

    for i, job in enumerate(jobs, start=1):
        job_id = job["job_id"]

        # --- Pre-screening (no LLM) ---
        passed, reason = prescreen(job, filters)
        if not passed:
            # Keep pre-screen fails on a single concise line to avoid terminal flood
            short_reason = reason.replace("Pre-screened out: ", "")
            logger.info("  [%d/%d] SKIP  %s – %s | %s",
                        i, jobs_total, job_id, job.get("title", "")[:50], short_reason)
            save_prescreening_fail(job_id, reason, cv_version)
            jobs_prefiltered += 1

        else:
            # --- LLM evaluation ---
            logger.info("  [%d/%d] LLM   %s – %s",
                        i, jobs_total, job_id, job.get("title", "")[:60])
            logger.info("         ↳ calling %s …", model)
            try:
                result = evaluate(job, cv_text, model, threshold)
                save_evaluation(
                    job_id=job_id,
                    cv_version=cv_version,
                    score=result.score,
                    score_reasoning=result.score_reasoning,
                    should_apply=result.should_apply,
                )
                total_tokens_input += result.tokens_input
                total_tokens_output += result.tokens_output
                jobs_evaluated += 1
                running_cost = _estimate_cost(model, total_tokens_input, total_tokens_output)
                if result.should_apply:
                    jobs_should_apply += 1
                    logger.info("         ↳ score=%d  ✓ APPLY  | cost so far $%.4f | %s",
                                result.score, running_cost, result.score_reasoning[:80])
                else:
                    logger.info("         ↳ score=%d  – skip   | cost so far $%.4f | %s",
                                result.score, running_cost, result.score_reasoning[:80])
            except Exception as exc:
                logger.warning("         ↳ LLM call FAILED for job %s: %s", job_id, exc)
                run_success = False
                error_message = str(exc)

        # --- Periodic progress summary ---
        if i % PROGRESS_EVERY == 0:
            running_cost = _estimate_cost(model, total_tokens_input, total_tokens_output)
            pct = i / jobs_total * 100
            logger.info(
                "  ── progress %d/%d (%.0f%%) │ skipped=%d │ llm=%d │ apply=%d │ cost=$%.4f ──",
                i, jobs_total, pct, jobs_prefiltered, jobs_evaluated, jobs_should_apply, running_cost,
            )

    # ------------------------------------------------------------------
    # Step 4: Write run record
    # ------------------------------------------------------------------
    total_tokens = total_tokens_input + total_tokens_output
    estimated_cost = _estimate_cost(model, total_tokens_input, total_tokens_output)

    run_id = insert_evaluation_run(
        model=model,
        cv_version=cv_version,
        requirements_hash=requirements_hash,
        jobs_total=jobs_total,
        jobs_prefiltered=jobs_prefiltered,
        jobs_evaluated=jobs_evaluated,
        jobs_should_apply=jobs_should_apply,
        tokens_input=total_tokens_input,
        tokens_output=total_tokens_output,
        tokens_total=total_tokens,
        estimated_cost_usd=estimated_cost,
        run_success=run_success,
        error_message=error_message,
    )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    elapsed = (datetime.now() - start).total_seconds()
    logger.info("-" * 60)
    logger.info("Run #%d complete in %.1f seconds.", run_id, elapsed)
    logger.info("  Total jobs:        %d", jobs_total)
    logger.info("  Pre-screened out:  %d", jobs_prefiltered)
    logger.info("  LLM evaluated:     %d", jobs_evaluated)
    logger.info("  Should apply:      %d", jobs_should_apply)
    logger.info("  Tokens used:       %d (in=%d, out=%d)", total_tokens, total_tokens_input, total_tokens_output)
    logger.info("  Estimated cost:    $%.4f", estimated_cost)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
