#!/usr/bin/env python3
"""
NRW major employer scrape funnel — links found → detail fetched → eligibility → stored.

No DB writes. Run on the server:

  .venv/bin/python scripts/diagnose_nrw_major.py
  .venv/bin/python scripts/diagnose_nrw_major.py --employer QIAGEN
  .venv/bin/python scripts/diagnose_nrw_major.py --employer "Johnson & Johnson" --listing-only

Shows why jobs drop off (wrong board URL, empty listing HTML, eligibility, etc.).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scraper import config  # noqa: E402
from scraper.nrw_eligibility import (  # noqa: E402
    job_eligible_nrw_major,
    job_text_eligible,
    text_suggests_us_only_remote,
)
from scraper.nrw_major_fetchers import (  # noqa: E402
    JOB_PATH_RE,
    fetch_jobs_for_employer,
    probe_jnj_careers_listing_link_count,
)

YAML_PATH = ROOT / "input_data" / "nrw_major_employers.yaml"


def _load_employers(name: str | None) -> list[dict]:
    with YAML_PATH.open(encoding="utf-8") as f:
        rows = yaml.safe_load(f).get("employers", [])
    if name:
        rows = [r for r in rows if r.get("name", "").lower() == name.lower()]
        if not rows:
            print(f"No employer matching {name!r}")
            sys.exit(1)
    return rows


def _eligibility_reason(location: str, text: str, *, scoped: bool) -> str:
    blob = f"{location or ''}\n{text or ''}"
    if text_suggests_us_only_remote(blob):
        return "US-only remote"
    if scoped:
        return "pass (listing NRW-scoped)"
    if job_text_eligible(location, text):
        return "pass (remote/hybrid/NRW rules)"
    if "remote" in blob.lower() and not any(
        x in blob.lower() for x in ("germany", "deutschland", "europe", "emea", "eu ", "dach")
    ):
        return "remote but no DE/EU region hint"
    return "no NRW / remote / hybrid match"


def _workday_listing_links(start_url: str, max_list: int) -> tuple[list[str], str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [], "playwright not installed"

    links: list[str] = []
    final_url = start_url
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=config.HEADERS["User-Agent"])
        page.set_default_timeout(60000)
        page.goto(start_url, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        try:
            for btn in page.locator("button:has-text('Accept')").all()[:1]:
                btn.click(timeout=3000)
                page.wait_for_timeout(1000)
        except Exception:
            pass
        for _ in range(3):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)
        html = page.content()
        final_url = page.url
        browser.close()

    for a in BeautifulSoup(html, "lxml").find_all("a", href=True):
        h = a["href"]
        if "/job/" not in h:
            continue
        if "myworkdayjobs.com" not in h and not h.startswith("/"):
            continue
        full = urljoin(start_url, h.split("?")[0])
        if full not in links:
            links.append(full)
    return links[:max_list], final_url


def diagnose_workday(row: dict, listing_only: bool, sample: int) -> None:
    name = row["name"]
    url = row["workday_url"]
    scoped = bool(row.get("listing_nrw_scoped"))
    max_list = int(row.get("workday_max_list_jobs", 60))

    print(f"\n{'='*72}")
    print(f"{name} [workday]  configured URL: {url}")
    links, final_url = _workday_listing_links(url, max_list)
    print(f"  listing resolved to: {final_url}")
    print(f"  job links on listing: {len(links)}")
    if not links:
        print("  → 0 eligible in production = listing found nothing (not eligibility filter).")
        if "wd3." in url and "wd502" not in url:
            print("  hint: QIAGEN moved to wd502 — update workday_url in nrw_major_employers.yaml")
        return
    for i, u in enumerate(links[:8], 1):
        print(f"    {i}. {u}")
    if len(links) > 8:
        print(f"    … +{len(links) - 8} more")

    if listing_only:
        return

    eligible = 0
    detail_fail = 0
    rejected: list[tuple[str, str]] = []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  playwright not installed — skip detail probe")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=config.HEADERS["User-Agent"])
        for job_url in links[:sample]:
            try:
                page.goto(job_url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2000)
                body = page.inner_text("body")[:12000]
                title = page.title() or ""
            except Exception as exc:
                detail_fail += 1
                rejected.append((job_url, f"detail fetch failed: {exc}"[:80]))
                continue
            if job_eligible_nrw_major("", body, listing_nrw_scoped=scoped):
                eligible += 1
            else:
                rejected.append((job_url, _eligibility_reason("", body, scoped=scoped)))
        browser.close()

    print(f"  detail sample ({min(sample, len(links))}): eligible={eligible}, fetch_fail={detail_fail}")
    for url_, reason in rejected[:6]:
        print(f"    ✗ {reason}")
        print(f"      {url_[:90]}")


def diagnose_jnj(row: dict, listing_only: bool) -> None:
    name = row["name"]
    listing_url = row.get("listing_url") or ""
    print(f"\n{'='*72}")
    print(f"{name} [jnj_careers]  listing: {listing_url}")
    status, n = probe_jnj_careers_listing_link_count(listing_url)
    print(f"  page-1 probe: {status}, {n} job links")
    if listing_only:
        max_pages = int(row.get("jnj_max_listing_pages", 25))
        est_min = max_pages * 12 / 60
        print(f"  full run paginates up to {max_pages} pages (~{est_min:.0f} min listing phase alone)")
        print("  then fetches up to max_jobs detail pages — can look 'stuck' for 20–40 min on 2GB VPS")
        return

    jobs = fetch_jobs_for_employer(row)
    print(f"  full fetch: {len(jobs)} eligible job(s)")


def diagnose_henkel(row: dict, sample: int) -> None:
    """Henkel logs link count in fetcher; run capped smoke + full eligibility funnel."""
    smoke = {**row, "max_jobs": sample, "henkel_max_load_rounds": 15}
    print(f"\n{'='*72}")
    print(f"{row['name']} [henkel_portal]  smoke max_jobs={sample}")
    jobs = fetch_jobs_for_employer(smoke)
    print(f"  eligible after smoke: {len(jobs)}")
    print("  (check log line 'Henkel portal: N job URL(s) found' for raw link count)")


def diagnose_successfactors(row: dict) -> None:
    from scraper import config as cfg
    import requests

    name = row["name"]
    base = row["listing_base_url"]
    param = row.get("page_param", "Page")
    sep = "&" if "?" in base else "?"
    url = f"{base.rstrip('/')}{sep}{param}=1"
    print(f"\n{'='*72}")
    print(f"{name} [successfactors]")
    print(f"  listing URL: {url}")
    try:
        r = requests.get(url, headers=cfg.HEADERS, timeout=45)
        r.raise_for_status()
        links = []
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        for a in BeautifulSoup(r.text, "lxml").find_all("a", href=True):
            m = JOB_PATH_RE.search(a["href"])
            if m:
                links.append(urljoin(origin, m.group(0).split("?")[0]))
        links = list(dict.fromkeys(links))
        print(f"  job links on page 1: {len(links)}")
        jobs = fetch_jobs_for_employer({**row, "max_pages": 1, "max_detail_attempts": 10})
        print(f"  eligible from first 10 detail tries: {len(jobs)}")
        if len(links) == 0:
            print("  → listing HTML has no /job/ links (site change or blocked)")
    except Exception as exc:
        print(f"  listing fetch FAILED: {exc}")


def diagnose_api(row: dict) -> None:
    name = row["name"]
    st = row["source_type"]
    print(f"\n{'='*72}")
    print(f"{name} [{st}]")
    jobs = fetch_jobs_for_employer(row)
    print(f"  eligible jobs returned: {len(jobs)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="NRW major employer scrape diagnostics")
    ap.add_argument("--employer", help="Only this employer (e.g. QIAGEN)")
    ap.add_argument("--listing-only", action="store_true", help="Skip detail fetches (fast)")
    ap.add_argument("--sample", type=int, default=5, help="Workday detail pages to probe")
    args = ap.parse_args()

    rows = _load_employers(args.employer)
    print("NRW major diagnostics (no DB writes)\n")

    for row in rows:
        st = row.get("source_type", "")
        if st == "workday":
            diagnose_workday(row, args.listing_only, args.sample)
        elif st == "jnj_careers":
            diagnose_jnj(row, args.listing_only)
        elif st == "henkel_portal":
            diagnose_henkel(row, min(args.sample, 10))
        elif st == "successfactors":
            diagnose_successfactors(row)
        else:
            diagnose_api(row)

    print(f"\n{'='*72}")
    print("DB check (run on server):")
    print("  see scripts/query_production_nrw.sh or GROUP BY employer query")


if __name__ == "__main__":
    main()
