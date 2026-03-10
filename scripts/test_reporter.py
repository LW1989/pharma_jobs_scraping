"""
Smoke test for the reporter module.

Does NOT send any email or Telegram message.

Tests:
  1. Module imports
  2. DB query: fetch up to 2 evaluated jobs (or use dummy data if DB is empty)
  3. Formatter: build HTML email and print Telegram text to stdout
  4. Write HTML to _test_email.html in the project root for browser inspection
  5. Verify Telegram message fits within the 4 096-char limit

Usage:
    python scripts/test_reporter.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── 1. Imports ────────────────────────────────────────────────────────────────
print("[1/5] Importing modules...", flush=True)
import reporter.db
import reporter.formatter
from scraper import config
print("      OK", flush=True)

# ── 2. Fetch jobs ─────────────────────────────────────────────────────────────
print("[2/5] Fetching up to 2 evaluated jobs from DB...", flush=True)
jobs = reporter.db.fetch_unsent_jobs(limit=2, min_score=0)

if jobs:
    print(f"      Found {len(jobs)} job(s):", flush=True)
    for j in jobs:
        print(f"        {int(j.get('score') or 0):3d}/100  {j.get('title', '')[:55]}", flush=True)
else:
    print("      No unsent jobs in DB yet — using dummy data for rendering test.", flush=True)
    jobs = [
        {
            "job_id": "DEMO1",
            "title": "Clinical Trial Manager",
            "employer": "ICON Strategic Solutions",
            "location": "UK, Homeworking",
            "contract_type": "Permanent",
            "hours": "Full Time",
            "closing_date": None,
            "score": 78,
            "score_reasoning": (
                "Strong transferable fit: SOP authoring, method/assay validation, "
                "multi-team project leadership and active GxP/ICH-GCP training align "
                "well with clinical operations. Main gaps are lack of direct site "
                "monitoring experience."
            ),
            "should_apply": True,
            "url": "https://www.pharmiweb.jobs/job/2138823/",
        },
        {
            "job_id": "DEMO2",
            "title": "Senior Clinical Research Associate",
            "employer": "Parexel",
            "location": "Germany, Homeworking",
            "contract_type": "Permanent",
            "hours": "Full Time",
            "closing_date": None,
            "score": 69,
            "score_reasoning": (
                "Good domain match with GxP training and SOP experience. "
                "Lacks hands-on EDC/CTMS and site monitoring history, "
                "but these are learnable on the job."
            ),
            "should_apply": True,
            "url": "https://www.pharmiweb.jobs/job/2137305/",
        },
    ]

# ── 3. Build Telegram text ────────────────────────────────────────────────────
print("\n[3/5] Building Telegram message...", flush=True)
tg_text = reporter.formatter.build_telegram_text(jobs, top_n=5)
print("-" * 60)
print(tg_text)
print("-" * 60)
print(f"      Length: {len(tg_text)} chars (limit: 4096)", flush=True)

# ── 4. Build HTML email + write to file ───────────────────────────────────────
print("\n[4/5] Building HTML email body...", flush=True)
stats = {"total_evaluated": 250, "total_apply": 4, "total_review": 6}
html_body = reporter.formatter.build_email_html(jobs, stats=stats)

output_path = ROOT / "_test_email.html"
output_path.write_text(html_body, encoding="utf-8")
print(f"      HTML written to: {output_path}", flush=True)
print(f"      Open it in a browser to inspect the email design.", flush=True)

# ── 5. Assertions ─────────────────────────────────────────────────────────────
print("\n[5/5] Validating output...", flush=True)
errors = []

if len(tg_text) > 4096:
    errors.append(f"Telegram message too long: {len(tg_text)} > 4096 chars")

if "<div class=\"card\">" not in html_body:
    errors.append("HTML email missing job cards")

if not html_body.startswith("<!DOCTYPE html>"):
    errors.append("HTML email missing DOCTYPE")

if errors:
    for e in errors:
        print(f"      [FAIL] {e}", flush=True)
    sys.exit(1)

print("      All checks passed.", flush=True)
print("\nSmoke test complete.")
print(f"Inspect the email at: {output_path}")
