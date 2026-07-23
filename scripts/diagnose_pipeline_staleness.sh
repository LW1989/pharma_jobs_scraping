#!/bin/bash
# Pipeline staleness diagnostics — run ON THE HETZNER SERVER:
#   bash scripts/diagnose_pipeline_staleness.sh
# Or remotely:
#   ssh root@<host> 'bash -s' < scripts/diagnose_pipeline_staleness.sh
set -e
cd /root/pharma_jobs_scraping 2>/dev/null || cd "$(dirname "$0")/.."
export $(grep -E '^DB_' .env | grep -v '^#' | xargs)

PSQL="psql -h localhost -U $DB_USER -d $DB_NAME"
export PGPASSWORD="$DB_PASSWORD"

echo "========== 1. PIPELINE FRESHNESS (last_seen by source) =========="
$PSQL -c "
SELECT source,
       COUNT(*) AS total,
       SUM(CASE WHEN job_active THEN 1 ELSE 0 END) AS active,
       MAX(last_seen) AS newest_last_seen,
       MIN(last_seen) FILTER (WHERE job_active) AS oldest_active_last_seen,
       SUM(CASE WHEN job_active AND last_seen < CURRENT_DATE - 5 THEN 1 ELSE 0 END) AS active_stale_5d,
       SUM(CASE WHEN first_seen >= CURRENT_DATE - 5 THEN 1 ELSE 0 END) AS first_seen_last_5d
FROM jobs
GROUP BY source
ORDER BY source NULLS FIRST;
"

echo ""
echo "========== 2. NEW JOBS PER DAY (last 14 days, all sources) =========="
$PSQL -c "
SELECT first_seen::date AS day, source, COUNT(*) AS new_jobs
FROM jobs
WHERE first_seen >= CURRENT_DATE - 14
GROUP BY 1, 2
ORDER BY 1 DESC, 2;
"

echo ""
echo "========== 3. last_seen REFRESH PER DAY (re-seen jobs, last 14 days) =========="
$PSQL -c "
-- Approximation: jobs whose last_seen equals that day and first_seen is older
SELECT last_seen::date AS day, source, COUNT(*) AS jobs_seen_that_day
FROM jobs
WHERE last_seen >= CURRENT_DATE - 14
  AND first_seen < last_seen
GROUP BY 1, 2
ORDER BY 1 DESC, 2;
"

echo ""
echo "========== 4. EVALUATION RUNS (did evaluator run daily?) =========="
$PSQL -c "
SELECT run_at::date AS day,
       COUNT(*) AS runs,
       SUM(jobs_total) AS pending,
       SUM(jobs_evaluated) AS llm_evaluated,
       SUM(jobs_should_apply) AS should_apply,
       BOOL_AND(run_success) AS all_ok,
       MAX(estimated_cost_usd)::numeric(8,3) AS max_cost_usd
FROM evaluation_runs
WHERE run_at >= CURRENT_DATE - 14
GROUP BY 1
ORDER BY 1 DESC;
"

echo ""
echo "========== 5. REPORTING QUEUE (why no digest?) =========="
$PSQL -c "
SELECT source,
       COUNT(*) FILTER (WHERE job_active) AS active,
       COUNT(*) FILTER (WHERE job_active AND evaluated) AS evaluated,
       COUNT(*) FILTER (WHERE job_active AND evaluated AND passed_prescreening) AS prescreen_pass,
       COUNT(*) FILTER (WHERE job_active AND evaluated AND passed_prescreening AND score >= 50) AS score_50_plus,
       COUNT(*) FILTER (WHERE job_active AND evaluated AND passed_prescreening
                        AND score >= 50 AND COALESCE(job_sent, FALSE) = FALSE) AS ready_to_report,
       COUNT(*) FILTER (WHERE job_active AND evaluated AND should_apply
                        AND COALESCE(job_sent, FALSE) = FALSE) AS unsent_should_apply
FROM jobs
GROUP BY source
ORDER BY source NULLS FIRST;
"

echo ""
echo "========== 6. JOBS EVALUATED BUT NEVER SENT (score >= 50) =========="
$PSQL -c "
SELECT source, employer, LEFT(title, 55) AS title, score, should_apply,
       job_sent, last_seen::date, first_seen::date
FROM jobs
WHERE job_active
  AND evaluated
  AND passed_prescreening
  AND score >= 50
  AND COALESCE(job_sent, FALSE) = FALSE
ORDER BY score DESC NULLS LAST, last_seen DESC
LIMIT 25;
"

echo ""
echo "========== 7. UNEVALUATED ACTIVE JOBS (evaluator backlog) =========="
$PSQL -c "
SELECT source, COUNT(*) AS unevaluated
FROM jobs
WHERE job_active AND (evaluated = FALSE OR evaluated IS NULL)
GROUP BY source
ORDER BY unevaluated DESC;
"

echo ""
echo "========== 8. NRW MAJOR — last_seen BY EMPLOYER =========="
$PSQL -c "
SELECT employer,
       COUNT(*) FILTER (WHERE job_active) AS active,
       MAX(last_seen) AS newest,
       MIN(last_seen) FILTER (WHERE job_active) AS oldest_active,
       SUM(CASE WHEN first_seen >= CURRENT_DATE - 5 THEN 1 ELSE 0 END) AS new_5d
FROM jobs
WHERE source = 'company_nrw_major'
GROUP BY employer
ORDER BY oldest_active NULLS FIRST, employer;
"

echo ""
echo "========== 9. PHARMIWEB MASS-DEACTIVATION CHECK =========="
$PSQL -c "
SELECT job_active, COUNT(*), MAX(last_seen) AS newest, MIN(last_seen) AS oldest
FROM jobs
WHERE source = 'pharmiweb' OR source IS NULL
GROUP BY job_active;
"

echo ""
echo "========== 10. RECENTLY SENT JOBS (reporter working?) =========="
$PSQL -c "
SELECT job_sent_at::date AS sent_day, source, COUNT(*) AS n
FROM jobs
WHERE job_sent_at >= CURRENT_DATE - 14
GROUP BY 1, 2
ORDER BY 1 DESC, 2;
"

echo ""
echo "========== 11. CRON LOG TAILS (last 5 days) =========="
for d in $(seq 0 4); do
  f="logs/cron_$(date -v-${d}d +%Y%m%d 2>/dev/null || date -d "$d days ago" +%Y%m%d).log"
  if [ -f "$f" ]; then
    echo "--- $f ---"
    grep -E 'Pipeline complete|FAILED|Fatal|Error|sys.exit|playwright|No unsent|Done\. Reported|Nothing to do|Found [0-9]+ live' "$f" | tail -20 || true
  fi
done

echo ""
echo "========== 12. ENV FLAGS (reporter / evaluator) =========="
grep -E '^(REPORTER_DRY_RUN|EVALUATOR_MAX_JOBS|OPENAI_API_KEY)=' .env 2>/dev/null \
  | sed 's/OPENAI_API_KEY=.*/OPENAI_API_KEY=***set***/' || echo "(no .env flags found)"

echo ""
echo "========== 13. ACTIVE JOB COUNT BY SOURCE (the email footer uses this pool) =========="
$PSQL -c "
SELECT COUNT(*) AS evaluated_active_pool
FROM jobs WHERE evaluated AND job_active;
"

echo ""
echo "========== 14. CHURN: jobs gone from listings but STILL ACTIVE (30-day grace) =========="
$PSQL -c "
SELECT source, employer, COUNT(*) AS stale_active,
       MIN(last_seen) AS oldest_last_seen
FROM jobs
WHERE job_active AND last_seen < CURRENT_DATE - 3
GROUP BY source, employer
HAVING COUNT(*) > 0
ORDER BY stale_active DESC
LIMIT 25;
"

echo ""
echo "========== 15. CHURN: duplicate titles (URL change => new job_id, old row lingers) =========="
$PSQL -c "
SELECT employer, LEFT(title, 48) AS title, COUNT(*) AS rows,
       SUM(CASE WHEN job_active THEN 1 ELSE 0 END) AS active
FROM jobs
WHERE source IN ('company_nrw_major', 'company_direct')
GROUP BY employer, LEFT(title, 48)
HAVING COUNT(*) > 1
ORDER BY rows DESC
LIMIT 20;
"

echo ""
echo "========== 16. LIVE FETCH vs DB (run on server; no DB writes) =========="
echo "  .venv/bin/python scripts/diagnose_pipeline_churn.py"
echo "  Shows per-employer: live fetch count, NEW vs DB, ACTIVE-not-in-live (removals hidden up to 30d)"

echo ""
echo "========== 17. PHARMIWEB STATUS =========="
$PSQL -c "
SELECT job_active, COUNT(*), MAX(last_seen), MIN(first_seen)
FROM jobs WHERE source = 'pharmiweb' OR source IS NULL
GROUP BY job_active;
"
echo "  (PharmiWeb.com shut down — run_scraper.py returns 0 jobs; site shows 'Thank you' closure page)"
