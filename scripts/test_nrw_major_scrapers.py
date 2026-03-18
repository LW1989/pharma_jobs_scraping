#!/usr/bin/env python3
"""
Smoke-test NRW major employer scrapers (network + optional Playwright).

Does not write to the database. Run from project root:

  python scripts/test_nrw_major_scrapers.py

Pipeline context:
  run_scraper → run_company_checker → run_nrw_major_checker → run_evaluator → run_reporter
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scraper import config  # noqa: E402
from scraper.nrw_major_fetchers import (  # noqa: E402
    JOB_PATH_RE,
    fetch_jobs_for_employer,
    fetch_smartrecruiters,
    probe_jnj_careers_listing_link_count,
)

YAML_PATH = ROOT / "input_data" / "nrw_major_employers.yaml"
SESSION = requests.Session()
SESSION.headers.update(config.HEADERS)


def sf_listing_links(base_list: str, page_param: str) -> tuple[int, list[str]]:
    sep = "&" if "?" in base_list else "?"
    url = f"{base_list.rstrip('/')}{sep}{page_param}=1"
    r = SESSION.get(url, timeout=45)
    r.raise_for_status()
    parsed = urlparse(url if "://" in url else f"https://{url}")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    seen: set[str] = set()
    links: list[str] = []
    for a in BeautifulSoup(r.text, "lxml").find_all("a", href=True):
        href = a["href"]
        if "/job/" not in href:
            continue
        m = JOB_PATH_RE.search(href)
        if not m:
            continue
        full = urljoin(origin, m.group(0).split("?")[0])
        if full not in seen:
            seen.add(full)
            links.append(full)
    return len(links), links


def probe_workday(url: str) -> tuple[str, int]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "playwright not installed", -1
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=config.HEADERS["User-Agent"])
            page.set_default_timeout(60000)
            page.goto(url, wait_until="networkidle")
            page.wait_for_timeout(4000)
            try:
                for b in page.locator("button:has-text('Accept')").all()[:1]:
                    b.click(timeout=2000)
                    page.wait_for_timeout(1000)
            except Exception:
                pass
            html = page.content()
            browser.close()
        n = 0
        for tag in BeautifulSoup(html, "lxml").find_all("a", href=True):
            h = tag["href"]
            if "/job/" in h and (
                "myworkdayjobs.com" in h or h.startswith("/")
            ):
                n += 1
        return "ok", n
    except Exception as exc:
        return str(exc)[:120], -1


def probe_ucb(url: str) -> tuple[str, int]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "playwright not installed", -1
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=config.HEADERS["User-Agent"])
            page.goto(url, wait_until="networkidle", timeout=90000)
            page.wait_for_timeout(8000)
            html = page.content()
            browser.close()
        soup = BeautifulSoup(html, "lxml")
        n = sum(
            1
            for a in soup.find_all("a", href=True)
            if "/job" in a["href"].lower() and "ucb" in a["href"].lower()
        )
        return "ok", n
    except Exception as exc:
        return str(exc)[:120], -1


def main() -> None:
    with YAML_PATH.open(encoding="utf-8") as f:
        employers = yaml.safe_load(f).get("employers", [])

    print("NRW major scraper smoke test (no DB writes)\n")
    print(f"{'Employer':<22} {'Type':<14} {'Result':<50}")
    print("-" * 88)

    for row in employers:
        name = row.get("name", "?")
        st = row.get("source_type", "")
        try:
            if st == "smartrecruiters":
                jobs = fetch_smartrecruiters(row)
                msg = f"{len(jobs)} job(s) after eligibility filter"
                print(f"{name:<22} {st:<14} {msg}")

            elif st == "bayer_eightfold":
                jobs = fetch_jobs_for_employer(row)
                print(
                    f"{name:<22} bayer_eightfold {len(jobs)} jobs "
                    f"(API NW slice; compare to ~61 on careers page)"
                )

            elif st == "successfactors":
                base = row["listing_base_url"]
                param = row.get("page_param", "Page")
                n_links, links = sf_listing_links(base, param)
                smoke = {
                    **row,
                    "max_pages": 1,
                    "max_detail_attempts": 15,
                }
                jobs = fetch_jobs_for_employer(smoke)
                extra = f"{n_links} links on page 1"
                print(
                    f"{name:<22} {st:<14} "
                    f"listing OK ({extra}), {len(jobs)} eligible in first 15 detail tries"
                )

            elif st == "workday":
                url = row["workday_url"]
                status, n = probe_workday(url)
                if n >= 0:
                    print(f"{name:<22} {st:<14} {status}, ~{n} job anchors seen")
                else:
                    print(f"{name:<22} {st:<14} FAIL {status}")

            elif st == "jnj_careers":
                lu = row.get("listing_url") or row.get("jnj_listing_url", "")
                st_probe, n_links = probe_jnj_careers_listing_link_count(lu)
                if n_links < 0:
                    print(f"{name:<22} jnj_careers    FAIL {st_probe}")
                elif n_links < 3:
                    print(
                        f"{name:<22} jnj_careers    WARN only {n_links} links on page 1 ({st_probe})"
                    )
                else:
                    print(
                        f"{name:<22} jnj_careers    {st_probe}, {n_links} job links on page 1 "
                        "(detail fetch in full run)"
                    )

            elif st == "henkel_portal":
                try:
                    from playwright.sync_api import sync_playwright
                except ImportError:
                    print(f"{name:<22} henkel_portal  playwright not installed")
                    continue
                smoke = {**row, "max_jobs": 5, "henkel_load_more_clicks": 4}
                jobs = fetch_jobs_for_employer(smoke)
                print(
                    f"{name:<22} henkel_portal  {len(jobs)} eligible (smoke: 5 jobs max, 4 load-more)"
                )

            elif st == "ucb":
                url = row["careers_url"]
                status, n = probe_ucb(url)
                if n >= 0:
                    print(f"{name:<22} {st:<14} {status}, {n} ucb job links")
                else:
                    print(f"{name:<22} {st:<14} FAIL {status}")
            else:
                print(f"{name:<22} {st:<14} unknown source_type")

        except Exception as exc:
            print(f"{name:<22} {st:<14} ERROR {exc!s}"[:88])

    print("-" * 88)
    print("\nFull daily run uses run_nrw_major_checker.py (no detail cap).")


if __name__ == "__main__":
    main()
