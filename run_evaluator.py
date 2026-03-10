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
REQUIREMENTS_PATH = ROOT / "requirements.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _auto_synthesize_if_needed(requirements: dict) -> None:
    """
    If cv_matching.txt is missing or out of date, automatically run CV synthesis.
    This ensures run_evaluator.py is self-contained — no manual pre-step needed.
    """
    import scripts.synthesize_cv as synthesize_cv

    synthesis_cfg = requirements.get("cv_synthesis", {})
    source_dir = ROOT / synthesis_cfg.get("source_directory", "input_data/cv")
    output_file = ROOT / synthesis_cfg.get("output_file", "cv_matching.txt")

    # Only auto-synthesize if there are source CV files available
    if not source_dir.exists() or not list(source_dir.glob("*.txt")):
        return  # No source files — skip, _load_cv will handle the fallback

    if synthesize_cv._is_up_to_date(synthesize_cv._combined_hash(
        synthesize_cv._load_source_cvs(source_dir)
    )):
        if output_file.exists():
            logger.info("CV synthesis:  up to date, skipping regeneration")
            return

    logger.info("CV synthesis:  cv_matching.txt missing or outdated — running synthesis …")
    synthesize_cv.main()
    logger.info("CV synthesis:  complete")


def _load_cv(requirements: dict) -> str:
    """
    Load CV text from the file specified in requirements.yaml (scoring.cv_file).
    Auto-triggers synthesis if cv_matching.txt is missing and source files exist.
    Falls back to cv_matching.txt, then cv.txt if not configured or not found.
    """
    _auto_synthesize_if_needed(requirements)

    scoring = requirements.get("scoring", {})
    configured = scoring.get("cv_file", "")

    candidates = []
    if configured:
        candidates.append(ROOT / configured)
    candidates += [ROOT / "cv_matching.txt", ROOT / "cv.txt"]

    for path in candidates:
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text and not text.startswith("CV files have been moved"):
                logger.info("CV file:       %s", path.name)
                return text
            if path.name != "cv.txt":
                logger.warning("CV file '%s' exists but appears empty or is a stub.", path.name)

    logger.error(
        "No usable CV file found. Add CV .txt files to input_data/cv/ "
        "or run synthesize_cv.py manually."
    )
    sys.exit(1)


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
    requirements = _load_requirements()
    cv_text = _load_cv(requirements)

    cv_version = _md5(cv_text)
    requirements_hash = _md5(str(requirements))

    filters = requirements.get("filters", {})
    # Merge job_preferences into filters so the prescreener can access them
    filters["job_preferences"] = requirements.get("job_preferences", {})

    scoring = requirements.get("scoring", {})
    threshold = int(scoring.get("should_apply_min_score", 65))
    tier_1_threshold = int(scoring.get("tier_1_min_score", 55))
    preferences = requirements.get("job_preferences", {})
    model = scoring.get("model") or config.OPENAI_MODEL

    logger.info("CV version:    %s", cv_version)
    logger.info("Requirements:  %s", requirements_hash)
    logger.info("Model:         %s", model)
    logger.info("Apply threshold: score >= %d (tier-1: >= %d)", threshold, tier_1_threshold)

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
                result = evaluate(
                    job, cv_text, model, threshold,
                    tier_1_threshold=tier_1_threshold,
                    preferences=preferences,
                )
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
