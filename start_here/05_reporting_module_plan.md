# Reporting Module — Plan

## What it does

After the daily evaluation run, a new script `run_reporter.py` reads all evaluated,
not-yet-sent jobs from the database, ranks them by score, and delivers two outputs:

| Channel  | Jobs | Format |
|----------|------|--------|
| Email    | Top 10 | Full HTML report — job card per entry with AI reasoning and a direct link |
| Telegram | Top 5  | Compact plain-text digest + note that the full report was sent via email |

A new `job_sent` column (+ `job_sent_at` timestamp) prevents any job from being
recommended twice across daily runs.

---

## Daily pipeline order

```
run_scraper.py      ← marks scraped jobs job_active=TRUE
run_evaluator.py    ← auto-synthesizes cv if needed, scores unsent jobs
run_reporter.py     ← sends top-N, marks job_sent=TRUE  ← NEW
```

Cron example (runs at 07:00 every day):
```
0 7 * * * cd /path/to/pharma_jobs_scraping && .venv/bin/python run_scraper.py && .venv/bin/python run_evaluator.py && .venv/bin/python run_reporter.py
```

---

## Database changes

### New columns on `jobs`

```sql
ALTER TABLE jobs
  ADD COLUMN IF NOT EXISTS job_sent     BOOLEAN   DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS job_sent_at  TIMESTAMP DEFAULT NULL;
```

Added to `scripts/migrate_db.py` so it is safe to re-run.

### Job selection query

```sql
SELECT *
FROM   jobs
WHERE  job_active        = TRUE
  AND  evaluated         = TRUE
  AND  job_sent          = FALSE  -- never sent before
ORDER  BY score DESC NULLS LAST
LIMIT  10;                        -- configurable
```

After sending, set `job_sent = TRUE` and `job_sent_at = NOW()`.

---

## Configuration (`requirements.yaml`)

Add a new `reporting` section:

```yaml
reporting:
  top_jobs_email:        10      # number of jobs in the email
  top_jobs_telegram:     5       # number of jobs in the Telegram message
  min_score_to_report:   0       # 0 = always send something; set >0 to suppress low-quality runs
  only_should_apply:     false   # true = only send jobs where should_apply=TRUE
```

### New `.env` variables

```ini
# Email (Gmail SMTP with App Password)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your@gmail.com
SMTP_PASSWORD=your_16char_app_password   # https://myaccount.google.com/apppasswords
REPORT_TO=recipient@example.com          # comma-separated for multiple recipients

# Telegram
TELEGRAM_BOT_TOKEN=123456789:ABCdef...
TELEGRAM_CHAT_ID=-1001234567890          # group chat or personal chat ID
```

---

## Technology choices

### Email — `smtplib` (Python built-in, zero new dependencies)

Python's standard library `smtplib` + `email.mime` is sufficient for sending
HTML emails via Gmail SMTP with an App Password. No extra pip packages needed.

**Why not SendGrid / Resend?**
This is a personal daily tool sending at most one email per day to one recipient.
SendGrid/Resend add sign-up friction, API keys, and vendor lock-in that are not
justified for this use case. `smtplib` + Gmail is simpler and completely free.

If you later need higher-volume or transactional sending, switch to
[Resend](https://resend.com) (cleanest modern API, great Python SDK).

### Telegram — direct Bot API via `requests` (already a dependency)

We call the Telegram Bot API directly using `requests`, which is already in
`requirements.txt`. No new package needed (avoids the overhead of
`python-telegram-bot`'s async framework, which is overkill for a fire-and-forget
send).

```python
import requests

def send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, data={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=15)
```

**Telegram limits:** 4 096 UTF-8 characters per message. The top-5 compact
format uses ~100–150 chars per job, comfortably within this limit.

---

## File structure

```
pharma_jobs_scraping/
├── run_reporter.py          ← new entry point
└── reporter/
    ├── __init__.py
    ├── db.py                ← fetch unsent jobs; mark as sent
    ├── formatter.py         ← build HTML email body + Telegram text
    ├── email_sender.py      ← smtplib wrapper
    └── telegram_sender.py   ← requests wrapper for Bot API
```

---

## Email report design

**Subject:** `Pharma Job Digest — {N} new matches — {date}`

**HTML structure:**

```
┌─────────────────────────────────────────────┐
│  PHARMA JOB DIGEST  ·  10 Mar 2026          │
│  3 jobs to apply for · 7 worth reviewing    │
├─────────────────────────────────────────────┤
│  ┌─── JOB CARD ───────────────────────────┐ │
│  │  Score: 78/100  ●●●●●○○○○○  [APPLY]    │ │
│  │  Clinical Trial Manager                 │ │
│  │  ICON Plc  ·  UK, Homeworking           │ │
│  │  Permanent · Full Time                  │ │
│  │  Closing: 15 Apr 2026                   │ │
│  │                                         │ │
│  │  Why it fits:                           │ │
│  │  "Strong match on GxP/GCP training,     │ │
│  │   SOP authoring..."  (AI reasoning)     │ │
│  │                                         │ │
│  │                [View Job →]             │ │
│  └─────────────────────────────────────────┘ │
│  ... (repeat for each job) ...              │
├─────────────────────────────────────────────┤
│  Evaluated {N} jobs total · {date}          │
└─────────────────────────────────────────────┘
```

Score badge colours:
- ≥ 70: green (`#2e7d32`)
- 55–69: amber (`#e65100`)
- < 55: grey (`#757575`)

---

## Telegram message design

```
🔍 <b>Pharma Job Digest · 10 Mar 2026</b>

<b>Top 5 matches for today:</b>

1️⃣ <b>Clinical Trial Manager</b> · ICON Plc
   📍 UK, Homeworking · Permanent
   ⭐ Score: 78/100 · <b>APPLY</b>
   🔗 https://pharmiweb.jobs/job/2138823

2️⃣ <b>Senior CRA</b> · Parexel
   📍 Germany, Homeworking · Permanent
   ⭐ Score: 72/100 · <b>APPLY</b>
   🔗 https://pharmiweb.jobs/job/2137305

... (up to 5 jobs) ...

📧 Full report (10 jobs + AI reasoning) sent to your email.
```

---

## `run_reporter.py` — high-level logic

```python
def main():
    requirements = _load_requirements()
    reporting_cfg = requirements.get("reporting", {})

    top_email     = reporting_cfg.get("top_jobs_email", 10)
    top_telegram  = reporting_cfg.get("top_jobs_telegram", 5)
    min_score     = reporting_cfg.get("min_score_to_report", 0)
    only_apply    = reporting_cfg.get("only_should_apply", False)

    jobs = reporter.db.fetch_unsent_jobs(
        limit=top_email,
        min_score=min_score,
        only_should_apply=only_apply,
    )

    if not jobs:
        logger.info("No unsent evaluated jobs to report. Exiting.")
        return

    # Build and send email (top N)
    html_body = reporter.formatter.build_email_html(jobs, top_n=top_email)
    reporter.email_sender.send(
        subject=f"Pharma Job Digest — {len(jobs)} matches — {date.today():%d %b %Y}",
        html_body=html_body,
    )

    # Build and send Telegram (top 5)
    tg_text = reporter.formatter.build_telegram_text(jobs, top_n=top_telegram)
    reporter.telegram_sender.send(tg_text)

    # Mark as sent
    job_ids = [j["job_id"] for j in jobs]
    reporter.db.mark_as_sent(job_ids)

    logger.info("Reported %d jobs. Marked as sent.", len(jobs))
```

---

## Error handling

| Failure | Behaviour |
|---------|-----------|
| No evaluated jobs yet | Log and exit cleanly (no email/Telegram sent) |
| SMTP authentication error | Log error with hint to check `SMTP_PASSWORD`; do **not** mark jobs as sent |
| Telegram API error | Log warning; still mark jobs as sent (email already delivered) |
| Partial send (email OK, Telegram fails) | Log warning, mark as sent anyway to avoid duplicate email next day |

---

## Implementation todos (for coding agent)

1. **DB migration** — add `job_sent` + `job_sent_at` to `scripts/migrate_db.py` and run it
2. **`reporter/` package** — create `__init__.py`, `db.py`, `formatter.py`, `email_sender.py`, `telegram_sender.py`
3. **`run_reporter.py`** — entry point script
4. **`requirements.yaml`** — add `reporting:` section
5. **`.env.example`** — add `SMTP_*`, `REPORT_TO`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
6. **`scraper/config.py`** — load the new env vars
7. **`start_here/02_codebase_guide.md`** — update to document the reporter module and new daily flow
8. **Smoke test** — `scripts/test_reporter.py` that picks one real DB job (without actually sending) and prints the rendered HTML + Telegram text to stdout

---

## Open questions / decisions before implementing

1. **Gmail vs dedicated service** — are you happy using Gmail SMTP with an app password?
   If yes, no new pip packages are needed.

2. **Telegram setup** — do you already have a Telegram bot token + chat ID, or do we
   need to walk through the `@BotFather` setup?

3. **Score threshold for reporting** — should the reporter always send the top-N regardless
   of score, or only if score ≥ some threshold (e.g. ≥ 50)?

4. **`only_should_apply` default** — should the report include jobs where the LLM said
   "don't apply yet" but scored, say, 60? (Currently defaulting to `false` = include all.)
