#!/usr/bin/env python3
"""
Live fetch vs DB churn diagnostic (no writes).

  .venv/bin/python scripts/diagnose_pipeline_churn.py
  .venv/bin/python scripts/diagnose_pipeline_churn.py --nrw-only
  .venv/bin/python scripts/diagnose_pipeline_churn.py --company-only

Compares what fetchers return RIGHT NOW against the production DB (if .env
is configured) or fetch-only mode without DB.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scraper import config  # noqa: E402
from scraper.company_scraper import fetch_jobs  # noqa: E402

# This tool exists to compare a real fetch against the DB, so the watchlist's
# page-hash cache (which answers from the DB) would defeat the point.
config.COMPANY_PAGE_HASH_CACHE = False
from scraper.nrw_major_fetchers import fetch_jobs_for_employer  # noqa: E402

NRW_YAML = ROOT / "input_data" / "nrw_major_employers.yaml"
COMPANIES_YAML = ROOT / "input_data" / "companies.yaml"


def _load_yaml(path: Path, key: str) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return list(yaml.safe_load(f).get(key) or [])


def _db_snapshot(source: str) -> dict[str, dict]:
    try:
        from scraper.db import get_cursor
    except Exception:
        return {}

    try:
        sql = """
            SELECT job_id, employer, title, job_active, first_seen, last_seen,
                   evaluated, score, job_sent
            FROM jobs
            WHERE source = %s
        """
        out: dict[str, dict] = {}
        with get_cursor() as cur:
            cur.execute(sql, (source,))
            for row in cur.fetchall():
                out[row["job_id"]] = dict(row)
        return out
    except Exception as exc:
        print(f"(DB snapshot for {source} unavailable: {exc})")
        return {}


def _print_header(title: str) -> None:
    print(f"\n{'=' * 72}")
    print(title)
    print("=" * 72)


def _compare_employer(
    employer: str,
    live: list[dict],
    db_by_id: dict[str, dict],
    *,
    source: str,
) -> None:
    live_ids = {j["job_id"] for j in live}
    db_emp = {jid: row for jid, row in db_by_id.items() if row.get("employer") == employer}
    db_active_ids = {jid for jid, row in db_emp.items() if row.get("job_active")}

    new_vs_db = live_ids - set(db_emp.keys())
    missing_from_live = db_active_ids - live_ids
    still_active_both = live_ids & db_active_ids

    print(f"\n{employer}")
    print(f"  live fetch now:     {len(live)}")
    print(f"  DB active (emp):    {len(db_active_ids)}")
    print(f"  DB total (emp):     {len(db_emp)}")
    print(f"  NEW vs DB:          {len(new_vs_db)}  (would insert)")
    print(f"  ACTIVE not in live: {len(missing_from_live)}  (still active until 30d stale)")

    if new_vs_db:
        for jid in list(new_vs_db)[:3]:
            t = next((j.get("title", "") for j in live if j["job_id"] == jid), "")
            print(f"    + would insert: {t[:65]}")
        if len(new_vs_db) > 3:
            print(f"    … +{len(new_vs_db) - 3} more")

    if missing_from_live:
        for jid in list(missing_from_live)[:3]:
            row = db_emp[jid]
            print(
                f"    - gone from listing (still active): {row.get('title', '')[:55]} "
                f"| last_seen={row.get('last_seen')}"
            )
        if len(missing_from_live) > 3:
            print(f"    … +{len(missing_from_live) - 3} more still active in DB")

    if live and not new_vs_db and not missing_from_live:
        print(f"  → identical set to DB active ({len(still_active_both)} jobs)")


def diagnose_nrw(db_by_id: dict[str, dict]) -> None:
    _print_header("NRW MAJOR EMPLOYERS — live fetch vs DB")
    employers = _load_yaml(NRW_YAML, "employers")
    totals = {"live": 0, "new": 0, "missing": 0, "failed": 0}

    for row in employers:
        name = row["name"]
        try:
            live = fetch_jobs_for_employer(row)
        except Exception as exc:
            print(f"\n{name}: FETCH FAILED — {exc}")
            totals["failed"] += 1
            continue
        totals["live"] += len(live)
        live_ids = {j["job_id"] for j in live}
        db_emp = {jid: r for jid, r in db_by_id.items() if r.get("employer") == name}
        db_active = {jid for jid, r in db_emp.items() if r.get("job_active")}
        totals["new"] += len(live_ids - set(db_emp.keys()))
        totals["missing"] += len(db_active - live_ids)
        _compare_employer(name, live, db_by_id, source="company_nrw_major")

    print(f"\nNRW totals: live={totals['live']} would_insert={totals['new']} "
          f"active_not_in_live={totals['missing']} fetch_failures={totals['failed']}")


def diagnose_companies(db_by_id: dict[str, dict]) -> None:
    _print_header("COMPANY WATCHLIST — live fetch vs DB")
    companies = _load_yaml(COMPANIES_YAML, "companies")
    totals = {"live": 0, "new": 0, "missing": 0, "failed": 0}

    for row in companies:
        if row.get("source_type") == "skip":
            continue
        name = row.get("name", "?")
        try:
            live = fetch_jobs(row)
        except Exception as exc:
            print(f"\n{name}: FETCH FAILED — {exc}")
            totals["failed"] += 1
            continue
        totals["live"] += len(live)
        live_ids = {j["job_id"] for j in live}
        db_emp = {jid: r for jid, r in db_by_id.items() if r.get("employer") == name}
        db_active = {jid for jid, r in db_emp.items() if r.get("job_active")}
        totals["new"] += len(live_ids - set(db_emp.keys()))
        totals["missing"] += len(db_active - live_ids)
        _compare_employer(name, live, db_by_id, source="company_direct")

    print(f"\nWatchlist totals: live={totals['live']} would_insert={totals['new']} "
          f"active_not_in_live={totals['missing']} fetch_failures={totals['failed']}")


def db_churn_stats() -> bool:
    try:
        from scraper.db import get_cursor
    except Exception as exc:
        print(f"\n(DB churn stats skipped: {exc})")
        return False

    _print_header("DB CHURN STATS (last 14 days)")

    queries = [
        (
            "New inserts by day",
            """
            SELECT first_seen::date AS day, source, COUNT(*) AS n
            FROM jobs
            WHERE first_seen >= CURRENT_DATE - 14
            GROUP BY 1, 2 ORDER BY 1 DESC, 2
            """,
        ),
        (
            "Active count by source (today)",
            """
            SELECT source, COUNT(*) AS active
            FROM jobs WHERE job_active GROUP BY source ORDER BY source
            """,
        ),
        (
            "NRW: active jobs NOT refreshed in 3+ days (listing may have removed them)",
            """
            SELECT employer, COUNT(*) AS stale_active,
                   MIN(last_seen) AS oldest_last_seen
            FROM jobs
            WHERE source = 'company_nrw_major' AND job_active
              AND last_seen < CURRENT_DATE - 3
            GROUP BY employer
            HAVING COUNT(*) > 0
            ORDER BY stale_active DESC
            """,
        ),
        (
            "NRW: duplicate titles same employer (URL change = new job_id)",
            """
            SELECT employer, LEFT(title, 50) AS title, COUNT(*) AS n,
                   SUM(CASE WHEN job_active THEN 1 ELSE 0 END) AS active_n
            FROM jobs
            WHERE source = 'company_nrw_major'
            GROUP BY employer, LEFT(title, 50)
            HAVING COUNT(*) > 1
            ORDER BY n DESC
            LIMIT 15
            """,
        ),
        (
            "Jobs that became inactive (last_seen in last 14d but job_active=false)",
            """
            SELECT source, COUNT(*) AS n
            FROM jobs
            WHERE NOT job_active
              AND last_seen >= CURRENT_DATE - 14
            GROUP BY source
            """,
        ),
        (
            "Email footer pool (active evaluated jobs in DB)",
            """
            SELECT COUNT(*) AS total_evaluated_active
            FROM jobs WHERE evaluated AND job_active
            """,
        ),
        (
            "Unsent reportable jobs (score>=50, prescreen pass)",
            """
            SELECT source, COUNT(*) AS ready
            FROM jobs
            WHERE job_active AND evaluated AND passed_prescreening
              AND score >= 50 AND COALESCE(job_sent, FALSE) = FALSE
            GROUP BY source
            """,
        ),
    ]

    try:
        with get_cursor() as cur:
            for title, sql in queries:
                print(f"\n--- {title} ---")
                cur.execute(sql)
                rows = cur.fetchall()
                if not rows:
                    print("  (no rows)")
                    continue
                cols = rows[0].keys()
                print("  " + " | ".join(cols))
                for row in rows[:25]:
                    print("  " + " | ".join(str(row[c]) for c in cols))
                if len(rows) > 25:
                    print(f"  … +{len(rows) - 25} more")
        return True
    except Exception as exc:
        print(f"\n(DB churn stats failed: {exc})")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Live fetch vs DB churn diagnostic")
    parser.add_argument("--nrw-only", action="store_true")
    parser.add_argument("--company-only", action="store_true")
    parser.add_argument("--db-only", action="store_true", help="Skip live fetches")
    args = parser.parse_args()

    print("Pipeline churn diagnostic")
    print(f"Date: {date.today().isoformat()}")

    db_churn_stats()

    if args.db_only:
        return

    nrw_db = _db_snapshot("company_nrw_major")
    co_db = _db_snapshot("company_direct")
    if nrw_db:
        print(f"\n(DB: {len(nrw_db)} company_nrw_major rows loaded)")
    if co_db:
        print(f"(DB: {len(co_db)} company_direct rows loaded)")

    run_nrw = not args.company_only
    run_co = not args.nrw_only

    if run_nrw:
        diagnose_nrw(nrw_db)
    if run_co:
        diagnose_companies(co_db)


if __name__ == "__main__":
    main()
