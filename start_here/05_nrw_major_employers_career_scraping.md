# NRW major employers — career sites & scraping notes

Research date: **March 2026**. Context: this project’s company watchlist already supports **Personio** (XML), **Workable** (JSON API), **Recruitee** (JSON API), and treats **Workday** + generic HTML via **LLM extraction** (no public job JSON). See `04_company_watchlist_plan.md` and `scraper/company_scraper.py`.

**“UC”** below is interpreted as **UCB** (pharma, Monheim am Rhein / Mettmann, NRW). If you meant another employer (e.g. Universität zu Köln), say so and this section can be adjusted.

### Implemented in this repo

| Piece | Role |
|-------|------|
| `run_nrw_major_checker.py` | Daily fetch for `input_data/nrw_major_employers.yaml` |
| `scraper/nrw_major_fetchers.py` | SmartRecruiters, Bayer Eightfold API, SuccessFactors, Workday, UCB, Henkel, J&J careers, rexx portal, Dolorgiet static, Adhex HubSpot |
| `scripts/nrw_major_health_check.py` | Weekly listing probes + email (19 employers) |
| `scraper/nrw_eligibility.py` + `input_data/nrw_eligibility.yaml` | Remote (DE/EU) or hybrid-in-NRW gate; optional `eligibility_profile` for Benelux (`nrw_benelux_remote`), Syneos/Fortrea CRA (`syneos_clinical_eu`) |
| `source = company_nrw_major` | DB rows; third section in email/Telegram reporter (scores + APPLY/review) |
| Title filter | **Internship / Praktikum / Praktikant** (and whole-word **intern**) are **not stored** — skipped in `run_nrw_major_checker` |

LANXESS **career-jobboard.lanxess.com** is not in the default YAML (main site only). Add a second employer row if you need EMEA board jobs.

### Added July 2026 (7 employers)

| Employer | `source_type` | Notes |
|----------|---------------|-------|
| **Evonik** | `workday` | Germany facet on `evonik.wd3`; NRW filter on detail (Essen, Marl, Herne, Wesseling) |
| **Octapharma** | `successfactors` | `careers.octapharma.com` + `locationsearch=Langenfeld` |
| **Janssen-Cilag** | `jnj_careers` | `search=Janssen` + DE + NRW; `jnj_detail_keywords` on detail |
| **Apontis Pharma** | `rexx_portal` | rexx systems at `apontis-pharma.de/karriere/`; health warns if 0 jobs |
| **Dolorgiet** | `dolorgiet_static` | Static h2 + PDF links on `dolorgiet.de/karriere` |
| **Klosterfrau** | `successfactors` | `klosterfrau-jobs.com/klosterfraucologne` + Köln search |
| **AdhexPharma** | `adhex_hubspot` | Sitemap discovery; Langenfeld pages only |

Apontis, Dolorgiet, and AdhexPharma were removed from `companies.yaml` watchlist to avoid duplicate scraping.

### Added July 2026 — Medtronic

| Employer | `source_type` | Notes |
|----------|---------------|-------|
| **Medtronic** | `workday` | `medtronic.wd1` Germany + Belgium `locationCountry` facets + Amsterdam city facet; `eligibility_profile: nrw_benelux_remote` accepts NRW + Benelux on-site/hybrid and DE/EU remote; rejects other DE on-site (e.g. Deggendorf) |

Use `eligibility_profile: nrw_benelux_remote` as the pattern for future majors that should include Benelux offices alongside NRW.

### Added July 2026 — Syneos Health (clinical)

| Employer | `source_type` | Notes |
|----------|---------------|-------|
| **Syneos Health** | `syneos_clinical` | `clinical-corporate-careers` Phenom board; hub listings Munich/Amsterdam/Brussels + `?q=CRA`; `eligibility_profile: syneos_clinical_eu` accepts DEU-/NLD-/BEL-Remote CRA/CTM and EU roles when language requirements are DE/EN/ES/PT only |

Location on detail pages uses codes like `DEU-Remote` (home-based Germany) even when the listing facet is Munich.

### Added July 2026 — Fortrea (clinical)

| Employer | `source_type` | Notes |
|----------|---------------|-------|
| **Fortrea** | `fortrea_clinical` | [careers.fortrea.com](https://careers.fortrea.com/us/en/) Phenom front-end over Workday CXS; HTTP listing via `searchText` (Munich, Germany, CRA); `eligibility_profile: syneos_clinical_eu` for DE/Benelux/EU CRA/CTM with DE/EN/ES/PT language gate |

Detail location uses city + country (e.g. `Munich, Germany`) rather than Syneos-style `DEU-Remote` codes. No Playwright required.

---

## Summary table

| Employer | Primary career / jobs URL | Stack (approx.) | Uses implemented APIs? | Practical scraping approach |
|----------|---------------------------|-----------------|------------------------|----------------------------|
| **Henkel** | [henkel.de/karriere/jobs-und-bewerbung](https://www.henkel.de/karriere/jobs-und-bewerbung) | Embedded **SAP** job portal (JS, “Mehr Jobs laden”) | **No** | **Playwright** on the DE job page; links often point to SuccessFactors/SAP. Implemented as `henkel_portal` in `nrw_major_employers.yaml`. |
| **QIAGEN** | [qiagen.com/careers](https://www.qiagen.com/us/careers) → **Workday** | **Workday** (`qiagen.wd3.myworkdayjobs.com`) | **No** (Workday = HTML path in repo) | Same as Covestro/Alvotech: headless browser (Playwright) or LLM on rendered listing + detail; no stable public JSON. |
| **Bayer** | [Eightfold NRW](https://bayer.eightfold.ai/careers?query=&location=NW%2C%20Germany&hl=de) (+ talent.bayer.com job pages) | **Eightfold PCS** (JSON API) | **No** | **`bayer_eightfold`**: `GET bayer.eightfold.ai/api/apply/v2/jobs` with `location=NW, Germany` — aligns with ~61 NRW roles (not jobs.bayer.com SF). |
| **LANXESS** | [career.lanxess.com](https://www.career.lanxess.com), [career-jobboard.lanxess.com](https://career-jobboard.lanxess.com) | **Phenom** portal (`lanxess_portal` in YAML) | **No** | Legacy SuccessFactors `/search/` URLs redirect; scraper uses `/us/en/search-results` + location search. |
| **Johnson & Johnson** | [careers.jnj.com](https://www.careers.jnj.com) | **careers.jnj.com** filtered search (e.g. Germany + NRW); detail pages are on-site | **No** | Scraper: Playwright on paginated `listing_url` + detail text + NRW eligibility. |
| **Grünenthal** | [careers.grunenthal.com](https://careers.grunenthal.com) | SAP **SuccessFactors** RMK | **No** | Same as Bayer: [view all jobs](https://careers.grunenthal.com/go/All-jobs/4703901) returns server-rendered links to `/job/.../{id}/`. |
| **Miltenyi Biotec** | [jobs.smartrecruiters.com/MiltenyiBiotec1](https://jobs.smartrecruiters.com/MiltenyiBiotec1) | **SmartRecruiters** | **Not yet in repo — but JSON API works without auth** | **Recommended:** `GET https://api.smartrecruiters.com/v1/companies/MiltenyiBiotec1/postings` (paginate with `offset`/`limit`). Map `location.country`, `city` to NRW filter. |
| **UCB** (“UC”) | [careers.ucb.com](https://careers.ucb.com) | **Phenom** (front) + **SuccessFactors** (ATS) | **No** | Heavy **SPA**: plain `requests` often insufficient. Use **Playwright** (wait for job cards), then parse DOM or pipe visible text to existing **LLM job extractor**. Filter Monheim / Mettmann / Germany. |
| **Covestro** | [career.covestro.com](https://career.covestro.com) | **Workday** (`covestro.wd3.myworkdayjobs.com`) | **No** | Same playbook as QIAGEN: Playwright on Workday job search + NRW/DE filters. |

---

## Per company (detail)

### Henkel
- **Düsseldorf** HQ; large adhesive & consumer brands employer in NRW.
- Jobs run on **SAP SuccessFactors** career experience (aligned with `jobs.henkel.com`).
- **Implemented APIs:** none match (not Personio / Workable / Recruitee).
- **Suggestion:** Mirror the **Bayer** approach if the listing HTML is server-rendered; if the job list loads only via XHR, use **Playwright** once, then cache link URLs.

### QIAGEN
- Hilden (NRW) is a key site; global HQ Netherlands.
- Public board: **Workday** subdomain (`qiagen.wd3.myworkdayjobs.com/QIAGEN`).
- **Implemented APIs:** Workday is explicitly **not** a public JSON API in this project.
- **Suggestion:** **Playwright**: open Workday search, set location/keyword filters, collect job posting URLs, scrape detail (or LLM on extracted text). Workday DOM is stable enough for selectors with occasional maintenance.

### Bayer
- **Leverkusen**, **Wuppertal**, etc. appear on the **Eightfold** board with filter **NW, Germany** (~61 roles).
- **jobs.bayer.com** is a **different** SuccessFactors listing; NRW counts won’t match Eightfold.
- **Scraper:** Eightfold JSON API + `talent.bayer.com/careers/job/{id}` URLs; change `eightfold_location` in YAML for other regions.

### LANXESS
- **Leverkusen**; chemistry group. Two entry points: **career.lanxess.com** (DE/IN/CN/US focus) and **career-jobboard.lanxess.com** (EMEA + others).
- **Suggestion:** Two scraper configs or one runner that hits both base URLs and merges deduplicated jobs. Same HTML family as Bayer.

### Johnson & Johnson
- Germany: Aachen, Neuss, Norderstedt, etc.
- Primary path: **careers.jnj.com** job search with query params (e.g. `country=Germany&state=North+Rhine-Westphalia`); paginate with `?page=N`.
- **Scraper:** `source_type: jnj_careers` + `listing_url` (filter query + `#results`); optional Taleo/Workday apply links from detail pages.

### Grünenthal
- **Aachen**, Stolberg (NRW).
- **careers.grunenthal.com** — same `/go/...` and `/job/.../{id}/` pattern as Bayer.
- **Suggestion:** HTML list + detail fetch; filter Germany / Aachen / Stolberg as needed.

### Miltenyi Biotec
- **Bergisch Gladbach**, Köln, Bielefeld, etc.
- **SmartRecruiters** company slug: **`MiltenyiBiotec1`**.
- **Public API verified (no API key):**
  - `GET https://api.smartrecruiters.com/v1/companies/MiltenyiBiotec1/postings?limit=100&offset=0`
  - Response includes `name`, `location.city`, `location.country`, refs, and links to apply.
- **Suggestion:** Add a **`smartrecruiters`** `source_type` in `company_scraper.py` (same idea as Recruitee). This is the **only** employer in this list with a **simple, unauthenticated JSON** feed comparable to Personio/Workable/Recruitee.

### UCB (“UC”)
- **Monheim am Rhein**, Mettmann (NRW).
- **Phenom**-driven careers site on top of **SuccessFactors**.
- **Suggestion:** **Playwright** + location filter + either DOM parsing or LLM extraction of listing text. No documented public job JSON for end users.

### Covestro
- **Leverkusen**; materials/chemistry.
- **Workday:** `covestro.wd3.myworkdayjobs.com`.
- **Suggestion:** Identical technical approach to QIAGEN and J&J Workday boards.

---

## Implementation priority (for this repo)

1. **Miltenyi** — Add **SmartRecruiters** fetcher (high ROI, stable JSON, no LLM cost for listing).
2. **Bayer, LANXESS, Grünenthal** — Shared **SuccessFactors RMK** HTML parser (list + detail); location filter mandatory.
3. **Henkel** — Validate whether listing is static HTML; then same SF parser or Playwright.
4. **QIAGEN, Covestro** — **Workday** Playwright; use **locale + location facets** in YAML where they match your manual filters.
5. **J&J** — **careers.jnj.com** paginated search (not Workday listing).
6. **UCB** — Playwright + DOM / eligibility on global search-results.

---

## References (indicative)

- Project ATS shortcuts: `start_here/04_company_watchlist_plan.md` (Workable / Personio / Recruitee / Workday).
- SmartRecruiters Posting API: [developers.smartrecruiters.com](https://developers.smartrecruiters.com) (list postings by company identifier).
- UCB + Phenom + SuccessFactors: Phenom case studies / blog (careers stack).


## Manual job counts (your browser check)

Use these URLs as the **reference inventory** for “how many roles the site shows” under each filter.

| Employer | Reference URL (your check) | Your count |
|----------|---------------------------|------------|
| Henkel | [NRW filter on DE portal](https://www.henkel.de/karriere/jobs-und-bewerbung#selectFilterByParameter=Locations_279384=Europe&Europe_877522=Germany&Germany_279422=North%20Rhine%20Westphalia&startIndex=0&loadCount=10&) | **91** |
| QIAGEN | [de-DE QIAGEN on wd502](https://qiagen.wd502.myworkdayjobs.com/de-DE/QIAGEN) + `workday_path_include` Hilden / Remote_Deutschland | **~19** |
| Bayer | [Eightfold careers NRW](https://bayer.eightfold.ai/careers?location=NRW%2C%20Germany&pid=562949975996941&domain=bayer.com&sort_by=relevance&hl=de&triggerGoButton=false&triggerGoButton=true) | **61** |
| LANXESS | [search-results + Leverkusen](https://career.lanxess.com/us/en/search-results) | **10** |
| J&J | [Germany + NRW on careers.jnj.com](https://www.careers.jnj.com/en/jobs/?search=&country=Germany&state=North+Rhine-Westphalia&pagesize=20#results) | **42** |
| Grünenthal | [NRW, Germany search](https://careers.grunenthal.com/search/?createNewAlert=false&p_kws=&locationsearch=NRW%2C+Germany&optionsFacetsDD_dept=&q=) | **15** |
| Miltenyi | [Corporate site Germany filter](https://www.miltenyibiotec.com/UN-en/about-us/careers/open-positions.html?filterJob=facet.filter.Country%3DGermany&list=328a8a4b-a635-4cef-b76f-02a6f1b966a6.en_GB) | **43** |
| UCB | [Global search-results](https://careers.ucb.com/global/en/search-results) (Monheim-focused) | **13** |
| Covestro | [de-DE + location facet](https://covestro.wd3.myworkdayjobs.com/de-DE/cov_external?locations=ff1397ebecb2016c13f9a0fe5c31b98d) | **12** |

---

## Eligibility policy (aligned with NRW job-site counts)

1. **`listing_nrw_scoped: true`**: listing URL already restricts to NRW / your office filter → **keep every job** from that listing except **US-only remote** (LANXESS, Grünenthal, Covestro, QIAGEN, J&J).
2. **Unscoped** (Bayer SF, Henkel portal): **remote EU / hybrid+NRW / on-site** if any **NRW location keyword** appears in listing+detail.
3. **Miltenyi**: SmartRecruiters — **Germany + NRW city** (incl. on-site Köln / Bergisch Gladbach).
4. **UCB**: detail must mention **Monheim** or **Mettmann**; search-results page is scrolled to load more job links.

## Verification vs this repo (March 2026)

| Employer | Your count | Expectation |
|----------|------------|-------------|
| LANXESS / Grünenthal / Covestro / QIAGEN / J&J | table above | Stored ≈ listing (scoped), within `max_jobs` / Workday link cap. |
| Miltenyi | ~32 NRW | API **32** DE+NRW cities; **31** Köln+BG only. |
| UCB | 13 Monheim | Keyword filter; scroll helps discover links. |
| Henkel | 91 NRW | Full portal + NRW text on page — should approach **~91** if copy names sites. |
| Bayer | 61 | **`bayer_eightfold`** fetcher uses the same API as the [NW, Germany careers page](https://bayer.eightfold.ai/careers?query=&location=NW%2C%20Germany&hl=de). |
### Added Aug 2026 — Viatris + generic Workday CXS

| Employer | `source_type` | Notes |
|----------|---------------|-------|
| **Viatris** | `workday_cxs` | `viatris.wd5` External board via the CXS JSON API; German `Country` facet + `Troisdorf` search; NRW narrowed by the default `nrw` eligibility profile |

`workday_cxs` generalises the JSON path the Fortrea fetcher already used
(`/wday/cxs/{tenant}/{site}/jobs`) into a tenant-parameterised fetcher, so a
Workday board can be read without Playwright. YAML keys: `workday_cxs_host`
(or `workday_url`, or `workday_cxs_tenant` + `workday_cxs_wd`),
`workday_cxs_tenant`, `workday_cxs_site`, `workday_cxs_locale`,
`workday_cxs_facets` (passed through as `appliedFacets`), `workday_cxs_queries`,
`workday_cxs_max_list_jobs`, `workday_cxs_page_size`, `max_jobs`.
Listing rows are prefiltered with `listing_row_worth_detail_fetch` before the
detail GET when `listing_nrw_scoped` is false.

It is a candidate to replace the Playwright `workday` path for QIAGEN, Covestro,
Evonik and Medtronic — none of them switched over yet.

**Lonza Cologne is not enabled.** The board at `lonza.com/careers/job-search`
could not be identified without network access, so `nrw_major_employers.yaml`
carries a commented entry with both candidate configurations (Workday CXS and
SuccessFactors) and what to verify. Uncomment one and add a `MIN_EXPECTED` row
in `scripts/nrw_major_health_check.py`.
