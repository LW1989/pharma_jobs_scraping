#!/usr/bin/env bash
# Full daily chain locally. From repo root:
#
#   chmod +x scripts/run_full_pipeline_local.sh
#   ./scripts/run_full_pipeline_local.sh
#
# Env:
#   REPORTER_DRY_RUN=1 (default below) — no email/Telegram/mark_sent; use 0 for real digest.
#   EVALUATOR_MAX_JOBS=N — cap LLM evaluations (omit for full backlog).
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/.venv/bin/python"
export REPORTER_DRY_RUN="${REPORTER_DRY_RUN:-1}"

run() {
  echo ""
  echo "================================================================================"
  echo " $1"
  echo "================================================================================"
  "$PY" "$1.py"
}

run run_scraper
run run_company_checker
run run_nrw_major_checker
run run_evaluator
run run_reporter

echo ""
echo "Pipeline finished. REPORTER_DRY_RUN=$REPORTER_DRY_RUN"
