# Project Overview

## What this project does

This is a **daily job scraper** that collects all European pharma/life-science jobs from [pharmiweb.jobs](https://www.pharmiweb.jobs) and stores them in a PostgreSQL database. It is designed to grow into a personal job-application pipeline.

## Roadmap (in order of implementation)

### Module 1 — Scraper (DONE)
Runs daily via cron. Scrapes all jobs from pharmiweb.jobs (currently ~966 jobs across 49 pages) and maintains the database:
- New jobs are fetched in full and inserted.
- Still-listed jobs get `last_seen` refreshed and `job_active = true`.
- Jobs that have disappeared get `job_active = false`.

### Module 2 — CV Evaluator (NEXT — not yet built)
Takes a CV stored in a plain `.txt` file, scores every unevaluated job in the database against it using an LLM, and writes back:
- `score` (0–100 fit score)
- `score_reasoning` (LLM explanation)
- `should_apply` (boolean recommendation)
- `evaluated`, `evaluated_at`, `cv_version`

### Module 3 — Distribution (NEXT — not yet built)
After evaluation:
- Copies the full jobs table to a Google Sheet.
- Sends a Telegram message with the Top 10 highest-scoring jobs.

## Environments

| Environment | Database host | Port | How to run |
|---|---|---|---|
| Local dev | `localhost` (Docker) | `5433` | `python run_scraper.py` |
| Production | Hetzner server IP | `5432` | cron job at 06:00 daily |

The only difference between environments is the `.env` file. The code is identical.

## Key conventions

- All DB access goes through `scraper/db.py`. Never write raw SQL outside that file.
- All HTTP fetching goes through `scraper/scraper.py`. Never use `requests` directly in other modules.
- Settings (DB credentials, URLs, timeouts) live in `scraper/config.py`, loaded from `.env`.
- `.env` is git-ignored. Use `.env.example` (production) or `.env.local.example` (Docker) as templates.
