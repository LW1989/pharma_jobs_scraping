#!/usr/bin/env python3
"""
NRW major employer scraper health check — listing probes only, HTML email report.

Sends ONLY to NRW_HEALTH_CHECK_TO (default: lutzwallhorn@googlemail.com),
not REPORT_TO. Uses existing SMTP_USER / SMTP_PASSWORD from .env.

  .venv/bin/python scripts/nrw_major_health_check.py
  .venv/bin/python scripts/nrw_major_health_check.py --dry-run

Cron example (weekly Monday 06:30, after Playwright jobs have run once):
  30 6 * * 1 cd /root/pharma_jobs_scraping && .venv/bin/python scripts/nrw_major_health_check.py >> logs/nrw_health.log 2>&1
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scraper import config  # noqa: E402
from scraper.nrw_major_fetchers import (  # noqa: E402
    JOB_PATH_RE,
    _sf_listing_page_url,
    _ucb_apply_listing_filters,
    _ucb_collect_job_links,
    _workday_gather_listing_links,
    fetch_bayer_eightfold,
    fetch_smartrecruiters,
    probe_adhex_hubspot_job_count,
    probe_dolorgiet_job_count,
    probe_henkel_portal_link_count,
    probe_jnj_careers_listing_link_count,
    probe_lanxess_portal_link_count,
    probe_rexx_portal_link_count,
    probe_syneos_clinical_link_count,
)
import reporter.email_sender  # noqa: E402

logger = logging.getLogger("nrw_major_health_check")

YAML_PATH = ROOT / "input_data" / "nrw_major_employers.yaml"
DEFAULT_RECIPIENT = "lutzwallhorn@googlemail.com"

# Minimum listing link / job counts before we flag WARN (0 = FAIL).
MIN_EXPECTED: dict[str, int] = {
    "Miltenyi Biotec": 10,
    "Bayer": 10,
    "LANXESS": 5,
    "Grünenthal": 5,
    "Henkel": 50,
    "QIAGEN": 10,
    "Covestro": 2,
    "Johnson & Johnson": 10,
    "UCB": 5,
    "Evonik": 10,
    "Octapharma": 2,
    "Janssen-Cilag": 3,
    "Apontis Pharma": 1,
    "Dolorgiet": 1,
    "Klosterfrau": 3,
    "AdhexPharma": 1,
    "Medtronic": 5,
    "Syneos Health": 8,
}

# Employers that may legitimately have zero open roles (warn, not fail).
WARN_IF_ZERO: set[str] = {"Apontis Pharma"}


@dataclass
class CheckResult:
    name: str
    source_type: str
    status: str  # ok | warn | fail
    count: int
    detail: str


def _status_for(name: str, count: int, error: str | None = None) -> str:
    if error or count < 0:
        return "fail"
    if count == 0:
        if name in WARN_IF_ZERO:
            return "warn"
        return "fail"
    if count < MIN_EXPECTED.get(name, 1):
        return "warn"
    return "ok"


def _sf_listing_count(row: dict) -> tuple[int, str]:
    import requests
    from bs4 import BeautifulSoup

    base = row["listing_base_url"]
    param = row.get("page_param", "Page")
    page_size = int(row.get("sf_page_size", 10))
    url = _sf_listing_page_url(base.rstrip("/"), 1, param, page_size)
    r = requests.get(url, headers=config.HEADERS, timeout=45)
    r.raise_for_status()
    parsed = urlparse(url if "://" in url else f"https://{url}")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    seen: set[str] = set()
    for a in BeautifulSoup(r.text, "lxml").find_all("a", href=True):
        m = JOB_PATH_RE.search(a["href"])
        if m:
            seen.add(urljoin(origin, m.group(0).split("?")[0]))
    return len(seen), "listing page 1"


def _playwright_unavailable(exc: BaseException) -> str | None:
    msg = str(exc)
    if "playwright not installed" in msg.lower():
        return "playwright not installed"
    if "Executable doesn't exist" in msg or "playwright install" in msg.lower():
        return "playwright chromium missing (run: playwright install chromium)"
    return None


def _probe_workday(row: dict) -> tuple[int, str]:
    from playwright.sync_api import sync_playwright

    employer = row["name"]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=config.HEADERS["User-Agent"])
        page.set_default_timeout(90000)
        links = _workday_gather_listing_links(page, row, employer)
        browser.close()
    extra = ""
    if row.get("workday_path_include"):
        extra = f"path filter {row['workday_path_include']}"
    return len(links), extra or "workday listing"


def _probe_ucb(row: dict) -> tuple[int, str]:
    from playwright.sync_api import sync_playwright

    url = row["careers_url"]
    max_pages = int(row.get("ucb_max_listing_pages", 5))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=config.HEADERS["User-Agent"])
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(4000)
        _ucb_apply_listing_filters(page, row)
        links = _ucb_collect_job_links(page, max_pages=max_pages)
        browser.close()
    filt = f"Country={row.get('ucb_country_filter')}, City={row.get('ucb_city_filter')}"
    return len(links), filt


def check_employer(row: dict) -> CheckResult:
    name = row["name"]
    st = row.get("source_type", "")
    try:
        if st == "smartrecruiters":
            n = len(fetch_smartrecruiters(row))
            detail = "SmartRecruiters API + eligibility"
            err = None
        elif st == "bayer_eightfold":
            n = len(fetch_bayer_eightfold(row))
            detail = "Eightfold API (NW, Germany)"
            err = None
        elif st == "successfactors":
            n, detail = _sf_listing_count(row)
            err = None
        elif st == "lanxess_portal":
            status, n = probe_lanxess_portal_link_count(row)
            detail = f"location_search={row.get('location_search', 'Leverkusen')!r}"
            err = _playwright_unavailable(Exception(status)) if n < 0 and "launch" in status else (
                None if n >= 0 else status
            )
        elif st == "henkel_portal":
            status, n = probe_henkel_portal_link_count(row)
            detail = "Babiel JSON API"
            err = None if n >= 0 else status
        elif st == "workday":
            n, detail = _probe_workday(row)
            err = None
        elif st == "jnj_careers":
            listing_url = row.get("listing_url") or ""
            status, n = probe_jnj_careers_listing_link_count(listing_url)
            detail = "page 1 listing"
            err = _playwright_unavailable(Exception(status)) if n < 0 and "launch" in status else (
                None if n >= 0 else status
            )
        elif st == "ucb":
            n, detail = _probe_ucb(row)
            err = None
        elif st == "rexx_portal":
            status, n = probe_rexx_portal_link_count(row)
            detail = f"rexx listing ({row.get('rexx_location_filter', '')})"
            err = _playwright_unavailable(Exception(status)) if n < 0 and "launch" in status else (
                None if n >= 0 else status
            )
        elif st == "dolorgiet_static":
            status, n = probe_dolorgiet_job_count(row)
            detail = "static h2 postings"
            err = None if n >= 0 else status
        elif st == "adhex_hubspot":
            status, n = probe_adhex_hubspot_job_count(row)
            kw = row.get("adhex_location_keywords") or ["Langenfeld"]
            detail = f"sitemap Langenfeld filter {kw}"
            err = None if n >= 0 else status
        elif st == "syneos_clinical":
            status, n = probe_syneos_clinical_link_count(row)
            detail = "clinical-corporate-careers listings"
            err = _playwright_unavailable(Exception(status)) if n < 0 and "launch" in status else (
                None if n >= 0 else status
            )
        else:
            return CheckResult(name, st, "fail", -1, f"unknown source_type {st!r}")

        status = _status_for(name, n, err)
        if err:
            detail = f"{err}; {detail}"
        return CheckResult(name, st, status, n, detail)

    except Exception as exc:
        logger.exception("%s health check failed", name)
        hint = _playwright_unavailable(exc)
        return CheckResult(name, st, "fail", -1, hint or str(exc)[:200])


def run_checks() -> list[CheckResult]:
    with YAML_PATH.open(encoding="utf-8") as f:
        employers = yaml.safe_load(f).get("employers", [])
    return [check_employer(row) for row in employers]


def _build_html(results: list[CheckResult], when: str) -> str:
    ok = sum(1 for r in results if r.status == "ok")
    warn = sum(1 for r in results if r.status == "warn")
    fail = sum(1 for r in results if r.status == "fail")
    overall = "OK" if fail == 0 and warn == 0 else ("DEGRADED" if fail == 0 else "FAILING")

    colors = {"ok": "#1a7f37", "warn": "#9a6700", "fail": "#cf222e"}
    rows_html = []
    for r in results:
        min_e = MIN_EXPECTED.get(r.name, "—")
        count_s = str(r.count) if r.count >= 0 else "error"
        rows_html.append(
            f"<tr>"
            f"<td>{r.name}</td>"
            f'<td>{r.source_type}</td>'
            f'<td style="color:{colors[r.status]};font-weight:bold">{r.status.upper()}</td>'
            f"<td>{count_s}</td>"
            f"<td>{min_e}</td>"
            f"<td>{r.detail}</td>"
            f"</tr>"
        )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body {{ font-family: system-ui, sans-serif; margin: 24px; color: #1f2328; }}
  h1 {{ font-size: 1.25rem; }}
  .summary {{ margin: 16px 0; padding: 12px; background: #f6f8fa; border-radius: 6px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
  th, td {{ border: 1px solid #d0d7de; padding: 8px 10px; text-align: left; }}
  th {{ background: #f6f8fa; }}
</style></head><body>
<h1>NRW major employers — scraper health</h1>
<p class="summary"><strong>Overall: {overall}</strong> &nbsp;·&nbsp;
  {ok} OK, {warn} warn, {fail} fail &nbsp;·&nbsp; {when} UTC</p>
<p>Listing-level probes only (no DB). Count = job links or eligible API rows found.</p>
<table>
  <tr><th>Employer</th><th>Type</th><th>Status</th><th>Count</th><th>Min</th><th>Detail</th></tr>
  {''.join(rows_html)}
</table>
<p style="margin-top:24px;font-size:12px;color:#656d76">
  Run: <code>python scripts/nrw_major_health_check.py</code> &nbsp;·&nbsp;
  Recipient fixed to NRW_HEALTH_CHECK_TO (not daily REPORT_TO).
</p>
</body></html>"""


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="NRW major employer scraper health check")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print summary to stdout; do not send email",
    )
    ap.add_argument(
        "--to",
        default=os.environ.get("NRW_HEALTH_CHECK_TO", DEFAULT_RECIPIENT),
        help=f"Email recipient (default: {DEFAULT_RECIPIENT})",
    )
    args = ap.parse_args()

    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    logger.info("Running NRW major health checks …")
    results = run_checks()

    for r in results:
        logger.info(
            "%-22s %-16s %4s  count=%s  %s",
            r.name,
            r.source_type,
            r.status.upper(),
            r.count,
            r.detail[:60],
        )

    fail_n = sum(1 for r in results if r.status == "fail")
    warn_n = sum(1 for r in results if r.status == "warn")
    overall = "FAIL" if fail_n else ("WARN" if warn_n else "OK")
    subject = f"NRW major scrapers health: {overall} ({len(results) - fail_n - warn_n}/{len(results)} OK)"

    html = _build_html(results, when)

    if args.dry_run:
        print(f"\n{subject}\n")
        print(f"Would email: {args.to}")
        for r in results:
            print(f"  {r.status:4}  {r.name:<22}  {r.count:>4}  {r.detail}")
        return 1 if fail_n else 0

    reporter.email_sender.send_to([args.to], subject, html)
    logger.info("Health report sent to %s", args.to)
    return 1 if fail_n else 0


if __name__ == "__main__":
    raise SystemExit(main())
