# Job visualization — current state & suggestions

This document analyses **how jobs are surfaced today**, **limitations**, and **directions for improvement**, including a **password-protected frontend** for single-user use (no full account system).

---

## How visualization works today

### 1. HTML email digest (primary)

- **Code:** `reporter/formatter.py` → `build_email_html()`
- **Trigger:** `run_reporter.py` after evaluation, via Gmail SMTP.
- **Content:**
  - Header: date + short summary (apply count, review count, watchlist / NRW major counts).
  - **Section A — Pharmiweb.jobs:** one card per job (top *N* from `requirements.yaml` → `reporting.top_jobs_email`).
  - **Section B — Company watchlist** (if any): same card layout; optional note if jobs were found but below score threshold.
  - **Section C — NRW major employers** (if any): same cards or threshold note.
- **Per card:** score badge (colour: green ≥70, amber 55–69, grey &lt;55), 10-dot meter, APPLY vs REVIEW label, title, employer, location, contract/hours, closing date, **AI reasoning** (“Why it fits”), **View Job** button.
- **Styling:** Inline-friendly CSS, ~680px width, works in most mail clients.

### 2. Telegram digest (secondary)

- **Code:** `reporter/formatter.py` → `build_telegram_text()`
- **Content:** Top *N* pharmiweb jobs (`top_jobs_telegram`), compact HTML; then optional watchlist / NRW bullets; points user to email for full reasoning.
- **Constraint:** ~4 096 character limit drives brevity.

### 3. Configuration

- **`requirements.yaml` → `reporting`:** limits, `min_score_to_report`, whether to include only `should_apply` for pharmiweb/NRW, watchlist caps, etc.

### 4. What is *not* there

- **No web app** (no Streamlit, Flask, FastAPI UI in the repo).
- **No Google Sheets export** in code (the old roadmap in `01_project_overview.md` still mentions it; delivery is email + Telegram).
- **`applied` flag** exists on `jobs` in the DB schema but there is **no in-app UI** to toggle it — likely manual SQL or external tooling.
- **Ad hoc analysis:** `notebooks/explore_db.ipynb` only.

---

## Strengths of the current approach

| Aspect | Why it works |
|--------|----------------|
| **Zero hosting for “viewing”** | Email is the UI; no server to harden for a public jobs browser. |
| **Rich context in one place** | Score + reasoning + metadata + link in one card beats a bare link list. |
| **Mobile-friendly enough** | Telegram for on-the-go skim; email for depth. |
| **Aligned with pipeline** | Reporter only shows **unsent** jobs above filters; avoids re-spamming the same rows. |
| **Multi-source story** | One digest combines pharmiweb, watchlist, and NRW major sections. |

---

## Limitations & what could be better

### Discovery & volume

- **Fixed top-N:** High-scoring jobs beyond the email cap are easy to miss unless you query the DB or re-run reporter logic mentally.
- **“Sent” semantics:** Once jobs are marked sent, they drop out of the daily digest queue — your **archive is your mailbox**, not a searchable product UI.

### Interaction & workflow

- **No actions:** Cannot mark *applied*, *dismiss*, *snooze*, or *favourite* from the digest (DB could support more of this with a small API).
- **Reasoning is static:** No way to re-prompt or add notes per job in the UI.

### Information density

- **Full job text** (`job_details`) is **not** in the email (would blow up size); you always open the external site for the full description.
- **No charts:** e.g. score distribution, applications over time, prescreen vs LLM funnel — would need a dashboard or notebook refresh.

### Email-specific

- **Client variance** (dark mode, link tracking, image blocking) can slightly change perceived layout.
- **Historical compare** (“what was in last Tuesday’s digest?”) is only as good as your email search.

---

## Direction: password-protected web frontend (single user)

You **do not need** login/sign-up, OAuth, or multi-tenant design. A **single shared password** (or HTTP Basic Auth) in front of a small app is enough to stop casual crawlers and roommates from seeing your CV-aligned job list.

### Security notes (keep expectations honest)

- A **password gate** is **not** equal to bank-grade auth: use **HTTPS** (e.g. reverse proxy with Let’s Encrypt on Hetzner), a **strong password**, and ideally **VPN or IP allowlist** if the app is on the public internet.
- **Prefer not exposing PostgreSQL** directly: the app should use a **read-only DB user** for listing jobs, and a **separate** credential (or same app, POST endpoints) for mutating `applied` if you add that.
- **Session secret** and password hashes belong in `.env`, never in git.

### Implementation options (from light to heavier)

| Approach | Pros | Cons |
|----------|------|------|
| **Reverse proxy Basic Auth** (Caddy/NGINX) + **static HTML** generated daily | Minimal code; same card HTML as email saved to file | No live filters; regenerate on schedule |
| **Streamlit** + `st.text_input` password check + `st.stop()` | Very fast to prototype; good tables/filters | Session model is quirky; less ideal for a “polished” app |
| **FastAPI + Jinja2** (or **Flask**) | Full control, cookie session after password POST | More boilerplate |
| **SQLite sync / read replica** | If you don’t want the dashboard to touch prod DB | Extra sync job |

### Suggested features for a v1 dashboard

1. **Table view** of evaluated, active jobs: sort by score, date, employer; filter by `should_apply`, `passed_prescreening`, `source` (pharmiweb vs company vs NRW major).
2. **Search** on title / employer / location.
3. **Expand row** or modal for full `job_details` + `score_reasoning` (avoid loading huge text in initial table).
4. **External link** to original posting (existing `url`).
5. **Toggle `applied`** (and optionally `job_sent` visibility filter) — closes the loop with the schema you already have.
6. **Optional:** show jobs that are **evaluated but never emailed** (if you want to bypass the “unsent only” reporter lens sometimes).

### Suggested features for v2

- Simple **stats strip**: count by score band, by source, evaluated today / this week.
- **Export** CSV or “open printable digest” (reuse `build_email_html` server-side).
- **Dark mode** toggle (nice for evening browsing).

---

## Other improvements (without a full frontend)

| Idea | Effort | Benefit |
|------|--------|---------|
| **Attach or link a static “full list” HTML** in email (hosted on your server behind auth, or as attachment) | Low–medium | See beyond top-N without SQL |
| **Google Sheet sync** (original roadmap) | Medium | Familiar spreadsheet filters for non-coders |
| **Notebook template** | Low | Reusable score histograms and SQL snippets |
| **Telegram inline buttons** | Medium | Quick “open job” / “mark interesting” if you add a tiny webhook bot |

---

## Summary

- **Today:** visualization = **well-designed HTML email** + **Telegram teaser**, driven by `run_reporter.py` and `reporter/formatter.py`. No interactive job browser in the repo.
- **Gaps:** top-N cap, no in-digest actions, no browse/search over the full evaluated set, reasoning/description only partial in email.
- **For a single user:** a **small password-protected web UI** (Streamlit or FastAPI) over read-only queries — plus optional **mark applied** — is a high-value next step without building real “user accounts.”

When you implement a frontend, add a short section to `02_codebase_guide.md` and env keys to `.env.example` (e.g. `DASHBOARD_PASSWORD`, `DASHBOARD_SESSION_SECRET`).
