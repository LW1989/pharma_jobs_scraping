"""
Test script: intercept jobvector.de XHR/fetch requests with Playwright
to discover the internal API endpoint used for job listings + pagination.

Run: python scripts/test_jobvector_playwright.py
"""
import sys, json, re
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

BASE_URL = "https://www.jobvector.de/jobs/?filter=331&filter=301&filter=322"

captured_requests = []   # (url, method, post_data)
captured_responses = {}  # url -> response body (JSON only)


def on_request(request):
    url = request.url
    # Capture non-static, non-CDN requests
    if any(skip in url for skip in ["cdn", "static", "fonts", "analytics",
                                     "google", "facebook", "tracking", ".css",
                                     ".png", ".jpg", ".woff", "sprite", "logo",
                                     "envelop", "pop_up", "confirmation"]):
        return
    captured_requests.append((request.method, url, request.post_data))


def parse_cards_html(html):
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select("article[data-jobid]")
    jobs = []
    for card in cards:
        title = card.select_one("h2")
        employer = card.select_one("span.company-name-text")
        jobs.append({
            "job_id": card.get("data-jobid", ""),
            "title": title.get_text(strip=True) if title else "",
            "employer": employer.get_text(strip=True) if employer else "",
        })
    return jobs


with sync_playwright() as p:
    print("Launching headless Chromium...")
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    )
    page = context.new_page()

    # ── Intercept all network requests ──────────────────────────────────
    page.on("request", on_request)

    # Capture JSON responses
    def on_response(response):
        url = response.url
        ct = response.headers.get("content-type", "")
        if "json" in ct and "jobvector" in url:
            try:
                captured_responses[url] = response.json()
            except Exception:
                pass

    page.on("response", on_response)

    # ── Load page 1 ──────────────────────────────────────────────────────
    print(f"\nLoading: {BASE_URL}")
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
    # Wait for job cards to appear
    page.wait_for_selector("article[data-jobid]", timeout=20000)
    page.wait_for_timeout(2000)   # let any further XHR settle

    jobs_p1 = parse_cards_html(page.content())
    print(f"Page 1 HTML cards: {len(jobs_p1)}")
    for j in jobs_p1[:3]:
        print(f"  [{j['job_id']}] {j['title'][:60]}")

    # ── Print the POST bodies of /api/jobvector/ calls ───────────────────
    print(f"\nAll API requests captured:")
    api_calls = [(m, u, p) for m, u, p in captured_requests if "api" in u or "papi" in u]
    for method, url, post in api_calls:
        print(f"  {method:6} {url}")
        if post:
            try:
                parsed = json.loads(post)
                print(f"         POST body: {json.dumps(parsed, indent=6)[:400]}")
            except Exception:
                print(f"         POST body (raw): {str(post)[:200]}")

    print(f"\nJSON responses captured from jobvector domain: {len(captured_responses)}")
    for url, data in list(captured_responses.items())[:5]:
        print(f"  {url}")
        print(f"  → {str(data)[:200]}")

    # ── Dismiss cookie consent modal, then click page 2 ─────────────────
    captured_requests.clear()
    captured_responses.clear()

    print("\n\nDismissing cookie modal if present...")
    try:
        # Common cookie/consent modal dismiss selectors
        for sel in ["#ddt-M1 button", "button:has-text('Akzeptieren')",
                     "button:has-text('Alle akzeptieren')", "button:has-text('Accept')",
                     "button:has-text('Zustimmen')", "[id*='cookie'] button",
                     "[class*='consent'] button", "[class*='modal'] button"]:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                print(f"  Dismissing modal via: {sel}")
                btn.click()
                page.wait_for_timeout(1000)
                break
    except Exception as e:
        print(f"  Modal dismiss error (ok): {e}")

    print("Looking for page 2 button...")
    try:
        # Try various selectors for the next-page button
        next_btn = None
        for sel in ["button:has-text('2')", "a[href*='page=2']", "[aria-label='2']",
                     ".pagination a:nth-child(3)", "nav a:has-text('2')"]:
            el = page.query_selector(sel)
            if el:
                print(f"  Found with selector: {sel}  (visible={el.is_visible()})")
                next_btn = el
                break

        if next_btn:
            print("  Clicking page 2...")
            next_btn.click(force=True)   # force=True bypasses visibility checks
            page.wait_for_timeout(3000)

            jobs_p2 = parse_cards_html(page.content())
            print(f"Page 2 HTML cards: {len(jobs_p2)}")
            for j in jobs_p2[:3]:
                print(f"  [{j['job_id']}] {j['title'][:60]}")

            overlap = {j["job_id"] for j in jobs_p1} & {j["job_id"] for j in jobs_p2}
            print(f"Overlap p1 ∩ p2: {len(overlap)} (0 = pagination works!)")

            print(f"\nAPI requests on page 2 navigation:")
            api_calls_p2 = [(m, u, p) for m, u, p in captured_requests if "api" in u or "papi" in u]
            for method, url, post in api_calls_p2:
                print(f"  {method:6} {url}")
                if post:
                    try:
                        parsed = json.loads(post)
                        print(f"         POST body: {json.dumps(parsed)[:600]}")
                    except Exception:
                        print(f"         POST body (raw): {str(post)[:300]}")
            print(f"JSON responses on page 2:")
            for url, data in list(captured_responses.items())[:5]:
                print(f"  {url}")
                print(f"  → {str(data)[:300]}")
        else:
            print("  No page 2 link found — checking current page URL:")
            print(f"  {page.url}")
            # Print full pagination area HTML
            pag = page.query_selector(".pagination, nav[role='navigation'], ul.pages")
            if pag:
                print(f"  Pagination HTML: {pag.inner_html()[:500]}")
    except Exception as e:
        print(f"  Error: {e}")

    browser.close()

print("\nDone.")
