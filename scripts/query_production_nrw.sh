#!/bin/bash
# Run ON THE HETZNER SERVER (or: ssh root@157.180.47.26 'bash -s' < scripts/query_production_nrw.sh)
set -e
cd /root/pharma_jobs_scraping 2>/dev/null || cd /opt/pharma_jobs_scraping
export $(grep -E '^DB_' .env | grep -v '^#' | xargs)

run_sql() {
  PGPASSWORD="$DB_PASSWORD" psql -h localhost -U "$DB_USER" -d "$DB_NAME" -x "$@"
}

echo "========== JOB COUNTS BY SOURCE =========="
PGPASSWORD="$DB_PASSWORD" psql -h localhost -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT source, COUNT(*) AS n,
          SUM(CASE WHEN job_active THEN 1 ELSE 0 END) AS active,
          SUM(CASE WHEN evaluated THEN 1 ELSE 0 END) AS evaluated,
          SUM(CASE WHEN should_apply THEN 1 ELSE 0 END) AS should_apply
   FROM jobs GROUP BY source ORDER BY n DESC;"

echo ""
echo "========== NRW MAJOR BY EMPLOYER =========="
PGPASSWORD="$DB_PASSWORD" psql -h localhost -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT employer, COUNT(*) AS total,
          SUM(CASE WHEN job_active THEN 1 ELSE 0 END) AS active,
          SUM(CASE WHEN evaluated THEN 1 ELSE 0 END) AS evaluated,
          SUM(CASE WHEN should_apply THEN 1 ELSE 0 END) AS should_apply,
          SUM(CASE WHEN passed_prescreening = FALSE THEN 1 ELSE 0 END) AS prescreen_fail
   FROM jobs WHERE source = 'company_nrw_major'
   GROUP BY employer ORDER BY employer;"

echo ""
echo "========== MILTENYI BIOTEC =========="
PGPASSWORD="$DB_PASSWORD" psql -h localhost -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT LEFT(title, 65) AS title, job_active, evaluated, passed_prescreening,
          score, should_apply, job_sent, last_seen::date
   FROM jobs WHERE source = 'company_nrw_major' AND employer = 'Miltenyi Biotec'
   ORDER BY score DESC NULLS LAST, last_seen DESC;"

echo ""
echo "========== QIAGEN =========="
PGPASSWORD="$DB_PASSWORD" psql -h localhost -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT LEFT(title, 65) AS title, job_active, evaluated, passed_prescreening,
          score, should_apply, job_sent, last_seen::date
   FROM jobs WHERE source = 'company_nrw_major' AND employer = 'QIAGEN'
   ORDER BY score DESC NULLS LAST, last_seen DESC;"

echo ""
echo "========== LAST 5 EVALUATION RUNS =========="
PGPASSWORD="$DB_PASSWORD" psql -h localhost -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT run_at::date, model, jobs_total, jobs_prefiltered, jobs_evaluated,
          jobs_should_apply, ROUND(estimated_cost_usd::numeric, 3) AS cost_usd
   FROM evaluation_runs ORDER BY run_at DESC LIMIT 5;"

echo ""
echo "========== LAST CRON LOG (tail) =========="
tail -30 logs/cron_$(date +%Y%m%d).log 2>/dev/null || tail -30 logs/cron_*.log 2>/dev/null | tail -30 || echo "(no cron log found)"
