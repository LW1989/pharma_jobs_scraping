# jobvector.de scraper — implementation handoff

Research + working proof-of-concept done July 2026. This doc is the spec for turning
it into a real module. PoC lives in `scratch_jobvector/poc_jobvector.py`.

## The two obstacles

1. **Cloudflare "managed challenge"** on the whole domain (even `/robots.txt`). Plain
   `requests`, `curl_cffi`, and `cloudscraper` all get `403`. Headless browsers get
   stuck on "Nur einen Moment…" forever.
2. **Vue/Nuxt SPA** with numbered pagination — **176 pages × ~20 jobs ≈ 3,511 total**.
   Scrolling does nothing (not infinite scroll). Only the first page is easy to get.

## What works (tested)

| Attempt | Result |
|---|---|
| `requests` / `curl_cffi` / `cloudscraper` | ❌ 403 |
| Playwright headless / real Chrome headed | ❌ detected, stuck on challenge |
| **`undetected-chromedriver`** (pin `version_main` to installed Chrome) | ✅ solves challenge |
| **Reuse `cf_clearance` cookie in `curl_cffi`** (`impersonate="chrome124"`) | ✅ fast paging, no browser per page |

## Recommended architecture (hybrid)

```
1. undetected-chromedriver → open /jobs/, solve Cloudflare ONCE,
   harvest cf_clearance cookie + User-Agent.
2. curl_cffi Session (impersonate=chrome124) + that cookie + same UA
   → GET /jobs/{N}/ for N=1..176, parse job links.
3. Same session → GET /job/{slug-hash}/ detail pages, parse title/meta.
```

Only pay the browser cost once; everything else is fast HTTP. Matches how the repo
already uses a browser for Workday/UCB (see `start_here/05_...`).

## Key facts for the implementer

- **Pagination URL**: `https://www.jobvector.de/jobs/{N}/` (page 1 = `/jobs/` or `/jobs/1/`).
  `?page=` and `?p=` are **ignored** — must use the path form.
- **Job URL / stable ID**: `https://www.jobvector.de/job/{slug}-{16hexchars}/`.
  Regex: `/job/([a-z0-9\-]+-[0-9a-f]{16})/`. The 16-hex hash is a stable dedup key —
  use it as `job_id` (same idea as `MD5(company+title)` elsewhere).
- **Total count** on page 1: `Es wurden 3511 passende Jobs … gefunden` →
  regex `([\d.]+)\s*(?:passende\s*)?Jobs`.
- **Detail parsing**: title from `<h1>`; employer + location from `og:title`
  (`"<title> | Job in <City>"`) and `meta[name=description]`.
- **Page overlap**: expect ~2–4 repeated jobs/page — jobvector re-ranks/promotes
  listings in real time. Dedup by the hash ID; don't assume 176×20 unique.
- **Chrome version**: `undetected_chromedriver.Chrome(..., version_main=<installed Chrome major>)`.
  On the dev Mac that was 149.

## Deploying on the Hetzner server (157.180.47.26) — NOT yet verified

The solve auto-passed on a residential IP. Datacenter IPs are treated more harshly by
Cloudflare, so validate on the server before trusting it. Steps:

- Install: `apt install chromium xvfb` + `pip install undetected-chromedriver curl_cffi`.
- **Run Chrome headed inside `xvfb-run`** (pure `--headless` fails the challenge).
- The solve MUST run on the server — `cf_clearance` is bound to the solving IP+UA,
  so you can't solve locally and copy the cookie over.
- If the datacenter IP is blocked/loops: try forcing IPv4 (repo already has
  `COMPANY_SCRAPER_FORCE_IPV4`), then as a last resort route only the solve through a
  residential proxy and `curl_cffi` the pages directly.
- **First task on the server: run a tiny probe** — solve `/jobs/`, confirm
  `cf_clearance` appears and `/jobs/2/` returns 200 with job links. If that passes,
  the rest is straightforward.

## Integration into the existing pipeline

Follow the `company_scraper.py` + `run_nrw_major_checker.py` pattern:

1. New file `scraper/jobvector_fetcher.py` exposing `fetch_jobs() -> list[dict]`
   (dicts shaped for `db.insert_job()`; missing fields → `None`).
2. New entry point `run_jobvector_checker.py` (add to the daily cron chain).
3. Insert rows with `source='jobvector'` (add value; `source` column already planned
   in the watchlist migration). Reuse the existing evaluator + reporter unchanged.
4. Consider scoping to pharma/life-science via jobvector's discipline/location URL
   facets instead of harvesting all 3,500 (cuts LLM cost).

## Dependencies to add

`undetected-chromedriver`, `curl_cffi`, `selenium` (pulled in by UC). Chrome/Chromium
must be present on the host.
