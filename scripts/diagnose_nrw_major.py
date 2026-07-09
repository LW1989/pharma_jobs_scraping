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
    _sf_listing_page_url,
    _ucb_apply_listing_filters,
    _ucb_collect_job_links,
    _workday_gather_listing_links,
    fetch_jobs_for_employer,
    probe_adhex_hubspot_job_count,
    probe_dolorgiet_job_count,
    probe_henkel_portal_link_count,
    probe_jnj_careers_listing_link_count,
    probe_lanxess_portal_link_count,
    probe_rexx_portal_link_count,
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


def _workday_listing_links(
    company: dict, max_list: int, max_pages: int, employer: str
) -> tuple[list[str], str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [], "playwright not installed"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=config.HEADERS["User-Agent"])
        page.set_default_timeout(60000)
        links = _workday_gather_listing_links(page, company, employer)
        final_url = page.url
        browser.close()
    return links, final_url


def _workday_link_breakdown(links: list[str]) -> dict[str, int]:
    from collections import Counter

    buckets: Counter[str] = Counter()
    for u in links:
        if "/job/" not in u:
            buckets["other"] += 1
            continue
        city = u.split("/job/")[-1].split("/")[0]
        buckets[city] += 1
    return dict(buckets)


def diagnose_workday(row: dict, listing_only: bool, sample: int) -> None:
    name = row["name"]
    url = row["workday_url"]
    scoped = bool(row.get("listing_nrw_scoped"))
    path_include = row.get("workday_path_include") or []

    print(f"\n{'='*72}")
    print(f"{name} [workday]  configured URL: {url}")
    if path_include:
        print(f"  path filter: {path_include}")
    links, final_url = _workday_listing_links(row, 0, 0, name)
    print(f"  listing resolved to: {final_url}")
    print(f"  job links after merge/path filter: {len(links)}")
    if path_include and links:
        breakdown = _workday_link_breakdown(links)
        hilden = sum(v for k, v in breakdown.items() if "hilden" in k.lower())
        remote_de = sum(
            v for k, v in breakdown.items() if "remote" in k.lower() and "deutsch" in k.lower()
        )
        print(f"  breakdown: Hilden={hilden}, Remote_DE={remote_de}, buckets={breakdown}")
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


def diagnose_henkel(row: dict, listing_only: bool, sample: int) -> None:
    name = row["name"]
    url = row.get("careers_url", "https://www.henkel.de/karriere/jobs-und-bewerbung")
    print(f"\n{'='*72}")
    print(f"{name} [henkel_portal]  URL: {url}")
    print("  listing: Babiel JSON API (queryresults/asJson, startIndex pagination)")

    status, n_links = probe_henkel_portal_link_count(row)
    if n_links < 0:
        print(f"  listing probe FAILED: {status}")
        return
    print(f"  job URLs from Babiel API: {n_links}")
    if n_links == 0:
        print("  → scraper found no job links (API change or collection id moved)")
        return
    if n_links < 50:
        print("  ⚠ low link count — check henkel_ajax_url / collection id in yaml")

    if listing_only:
        return

    smoke = {**row, "max_jobs": sample}
    jobs = fetch_jobs_for_employer(smoke)
    print(f"  eligible from first {sample} detail fetches: {len(jobs)}")
    for j in jobs[:5]:
        print(f"    ✓ {(j.get('title') or '')[:70]}")


def diagnose_successfactors(row: dict) -> None:
    from scraper import config as cfg
    import requests

    name = row["name"]
    base = row["listing_base_url"]
    param = row.get("page_param", "Page")
    page_size = int(row.get("sf_page_size", 10))
    url = _sf_listing_page_url(base.rstrip("/"), 1, param, page_size)
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


def diagnose_ucb(row: dict, listing_only: bool) -> None:
    name = row["name"]
    url = row.get("careers_url", "")
    country = row.get("ucb_country_filter", "Germany")
    city = row.get("ucb_city_filter", "Monheim")
    print(f"\n{'='*72}")
    print(f"{name} [ucb]  URL: {url}")
    print(f"  facet filters: Country={country!r}, City={city!r}")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  playwright not installed")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=config.HEADERS["User-Agent"])
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(4000)
        _ucb_apply_listing_filters(page, row)
        links = _ucb_collect_job_links(
            page, max_pages=int(row.get("ucb_max_listing_pages", 10))
        )
        browser.close()

    print(f"  job links after facet filters: {len(links)}")
    if not links:
        print("  → no links (facet UI change?)")
        return

    if listing_only:
        return

    jobs = fetch_jobs_for_employer(row)
    print(f"  eligible jobs returned: {len(jobs)}")
    for j in jobs[:5]:
        print(f"    ✓ {(j.get('title') or '')[:70]}")


def diagnose_lanxess(row: dict, listing_only: bool) -> None:
    name = row["name"]
    listing_url = row.get(
        "listing_url", "https://career.lanxess.com/us/en/search-results"
    )
    loc = row.get("location_search", "Leverkusen")
    print(f"\n{'='*72}")
    print(f"{name} [lanxess_portal]  URL: {listing_url}")
    print(f"  location search: {loc!r}")

    status, n = probe_lanxess_portal_link_count(row)
    if n < 0:
        print(f"  listing probe FAILED: {status}")
        return
    print(f"  job links on portal: {n}")
    if n == 0:
        print("  → Phenom portal returned no job links (site change or search filter)")
        return
    if n < 5:
        print("  ⚠ low link count — check listing_url / location_search")

    if listing_only:
        return

    jobs = fetch_jobs_for_employer(row)
    print(f"  eligible jobs returned: {len(jobs)}")
    for j in jobs[:5]:
        print(f"    ✓ {(j.get('title') or '')[:70]}")


def diagnose_rexx(row: dict, listing_only: bool) -> None:
    name = row["name"]
    listing = row.get("listing_url", "")
    print(f"\n{'='*72}")
    print(f"{name} [rexx_portal]  listing: {listing}")
    status, n = probe_rexx_portal_link_count(row)
    print(f"  job links on listing: {status}, {n}")
    if n == 0:
        print("  → 0 links (may have no open roles, or JS-rendered list)")
    if listing_only:
        return
    jobs = fetch_jobs_for_employer(row)
    print(f"  eligible jobs returned: {len(jobs)}")


def diagnose_dolorgiet(row: dict, listing_only: bool) -> None:
    name = row["name"]
    url = row.get("careers_url", "")
    print(f"\n{'='*72}")
    print(f"{name} [dolorgiet_static]  URL: {url}")
    status, n = probe_dolorgiet_job_count(row)
    print(f"  job headings on page: {status}, {n}")
    if listing_only:
        return
    jobs = fetch_jobs_for_employer(row)
    print(f"  jobs returned: {len(jobs)}")
    for j in jobs:
        print(f"    ✓ {(j.get('title') or '')[:70]}")


def diagnose_adhex(row: dict, listing_only: bool) -> None:
    name = row["name"]
    sitemap = row.get("sitemap_url", "")
    kw = row.get("adhex_location_keywords") or ["Langenfeld"]
    print(f"\n{'='*72}")
    print(f"{name} [adhex_hubspot]  sitemap: {sitemap}")
    print(f"  location filter: {kw}")
    status, n = probe_adhex_hubspot_job_count(row)
    print(f"  Langenfeld-eligible pages: {status}, {n}")
    if listing_only:
        return
    jobs = fetch_jobs_for_employer(row)
    print(f"  eligible jobs returned: {len(jobs)}")


def diagnose_api_fallback(row: dict) -> None:
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
            diagnose_henkel(row, args.listing_only, min(args.sample, 15))
        elif st == "successfactors":
            diagnose_successfactors(row)
        elif st == "lanxess_portal":
            diagnose_lanxess(row, args.listing_only)
        elif st == "ucb":
            diagnose_ucb(row, args.listing_only)
        elif st == "rexx_portal":
            diagnose_rexx(row, args.listing_only)
        elif st == "dolorgiet_static":
            diagnose_dolorgiet(row, args.listing_only)
        elif st == "adhex_hubspot":
            diagnose_adhex(row, args.listing_only)
        else:
            diagnose_api_fallback(row)

    print(f"\n{'='*72}")
    print("DB check (run on server):")
    print("  see scripts/query_production_nrw.sh or GROUP BY employer query")


if __name__ == "__main__":
    main()
