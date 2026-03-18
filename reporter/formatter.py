"""
Builds the HTML email body and the plain-text Telegram message.

Email design (from start_here/05_reporting_module_plan.md):
  - Header: date + summary line
  - One card per job: score badge + dot-meter, title/employer/location,
    contract/hours, closing date, AI reasoning, "View Job →" button
  - Footer: total evaluated + run date
  - Score colours: green ≥70 / amber 55–69 / grey <55

Telegram design (from start_here/05_reporting_module_plan.md):
  - HTML parse mode, numbered emoji entries
  - Each entry: bold title · employer, location · contract, score + APPLY flag, URL
  - Footer: note that full report was sent by email
"""

from __future__ import annotations

from datetime import date as _date
from html import escape

# Number emoji used for Telegram entries
_NUMBERS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


# ---------------------------------------------------------------------------
# Score helpers
# ---------------------------------------------------------------------------

def _score_colour(score: int) -> str:
    if score >= 70:
        return "#2e7d32"   # green
    if score >= 55:
        return "#e65100"   # amber
    return "#757575"       # grey


def _dot_meter(score: int) -> str:
    """Return a 10-dot progress bar, e.g. ●●●●●●●○○○ for score 72."""
    filled = round(score / 10)
    return "●" * filled + "○" * (10 - filled)


def _apply_label(job: dict) -> str:
    return "APPLY" if job.get("should_apply") else "REVIEW"


def _format_date(val) -> str:
    if val is None:
        return "—"
    if hasattr(val, "strftime"):
        return val.strftime("%-d %b %Y")
    return str(val)


# ---------------------------------------------------------------------------
# HTML email
# ---------------------------------------------------------------------------

_EMAIL_CSS = """
body { margin:0; padding:0; background:#f4f4f4; font-family:Arial,sans-serif; }
.wrapper { max-width:680px; margin:0 auto; background:#ffffff; }
.header { background:#1a237e; color:#ffffff; padding:28px 32px 20px; }
.header h1 { margin:0 0 6px; font-size:22px; font-weight:700; letter-spacing:0.5px; }
.header p  { margin:0; font-size:14px; opacity:0.85; }
.card { border:1px solid #e0e0e0; border-radius:8px; margin:16px 24px; padding:20px 24px; }
.score-row { display:flex; align-items:center; gap:12px; margin-bottom:14px; }
.badge { display:inline-block; padding:4px 12px; border-radius:20px;
         color:#fff; font-size:13px; font-weight:700; white-space:nowrap; }
.dots { font-size:14px; color:#9e9e9e; letter-spacing:1px; }
.apply-label { font-size:12px; font-weight:700; padding:3px 8px; border-radius:4px;
               background:#e8f5e9; color:#2e7d32; }
.apply-label.review { background:#fff3e0; color:#e65100; }
.job-title { font-size:17px; font-weight:700; color:#212121; margin:0 0 4px; }
.meta { font-size:13px; color:#616161; margin:2px 0; }
.reasoning { margin:14px 0; padding:12px 16px; background:#f9f9f9;
             border-left:3px solid #e0e0e0; font-size:13px;
             color:#424242; line-height:1.55; }
.reasoning strong { display:block; margin-bottom:4px; color:#212121; font-size:12px;
                    text-transform:uppercase; letter-spacing:0.5px; }
.btn { display:inline-block; margin-top:14px; padding:9px 20px;
       background:#1a237e; color:#ffffff !important; text-decoration:none;
       border-radius:5px; font-size:13px; font-weight:600; }
.footer { padding:20px 32px; font-size:12px; color:#9e9e9e; border-top:1px solid #eeeeee; }
"""


def _job_card_html(job: dict) -> str:
    score = int(job.get("score") or 0)
    colour = _score_colour(score)
    dots = _dot_meter(score)
    label = _apply_label(job)
    label_class = "apply-label" if label == "APPLY" else "apply-label review"
    reasoning = escape(job.get("score_reasoning") or "No reasoning available.")
    title = escape(job.get("title") or "—")
    employer = escape(job.get("employer") or "—")
    location = escape(job.get("location") or "—")
    contract = escape(job.get("contract_type") or "—")
    hours = escape(job.get("hours") or "—")
    closing = _format_date(job.get("closing_date"))
    url = job.get("url") or "#"

    return f"""
<div class="card">
  <div class="score-row">
    <span class="badge" style="background:{colour};">{score}/100</span>
    <span class="dots">{dots}</span>
    <span class="{label_class}">{label}</span>
  </div>
  <p class="job-title">{title}</p>
  <p class="meta"><strong>{employer}</strong> &nbsp;·&nbsp; {location}</p>
  <p class="meta">{contract} &nbsp;·&nbsp; {hours} &nbsp;·&nbsp; Closes: {closing}</p>
  <div class="reasoning">
    <strong>Why it fits:</strong>
    {reasoning}
  </div>
  <a class="btn" href="{url}" target="_blank">View Job &rarr;</a>
</div>"""


_SECTION_HEADER_CSS = """
.section-header { margin: 24px 24px 0; padding: 10px 16px;
                  background: #e8eaf6; border-left: 4px solid #1a237e;
                  font-size: 13px; font-weight: 700; color: #1a237e;
                  text-transform: uppercase; letter-spacing: 0.6px; }
"""


def _section_header_html(text: str) -> str:
    return f'<div class="section-header">{escape(text)}</div>'


def build_email_html(
    jobs: list[dict],
    stats: dict | None = None,
    company_jobs: list[dict] | None = None,
    company_jobs_found: int = 0,
    min_score: int = 0,
    nrw_major_jobs: list[dict] | None = None,
    nrw_major_found: int = 0,
) -> str:
    """Build the full HTML email body.

    Args:
        jobs:               Pharmiweb jobs (scored, top-N).
        stats:              Summary counts for the footer.
        company_jobs:       Company watchlist jobs above the score threshold.
        company_jobs_found: Total company jobs that passed prescreening
                            (including those below the threshold). Used to
                            show a 'found but not shown' note.
        min_score:          The minimum score threshold (for the note text).
    """
    today = _date.today().strftime("%-d %b %Y")
    total = (stats or {}).get("total_evaluated", len(jobs))
    apply_count = sum(1 for j in jobs if j.get("should_apply"))
    review_count = len(jobs) - apply_count

    summary = f"{apply_count} job{'s' if apply_count != 1 else ''} to apply for"
    if review_count:
        summary += f" &nbsp;·&nbsp; {review_count} worth reviewing"
    if company_jobs:
        summary += f" &nbsp;·&nbsp; {len(company_jobs)} watchlist"
    elif company_jobs_found:
        summary += f" &nbsp;·&nbsp; {company_jobs_found} watchlist (below threshold)"
    if nrw_major_jobs:
        summary += f" &nbsp;·&nbsp; {len(nrw_major_jobs)} NRW major"
    elif nrw_major_found:
        summary += f" &nbsp;·&nbsp; {nrw_major_found} NRW major (below threshold)"

    # Section 1 — pharmiweb jobs
    pharmiweb_section = ""
    if jobs:
        pharmiweb_section = (
            _section_header_html("Pharmiweb.jobs — apply recommended")
            + "".join(_job_card_html(j) for j in jobs)
        )

    # Section 2 — company watchlist jobs
    company_section = ""
    if company_jobs:
        company_section = (
            _section_header_html("New from Your Company Watchlist")
            + "".join(_job_card_html(j) for j in company_jobs)
        )
    elif company_jobs_found:
        threshold_note = (
            f'<div style="margin:16px 24px;padding:14px 18px;background:#f9f9f9;'
            f'border:1px solid #e0e0e0;border-radius:8px;font-size:13px;color:#616161;">'
            f'<strong style="color:#212121;">Company Watchlist</strong><br>'
            f'{company_jobs_found} new job{"s" if company_jobs_found != 1 else ""} '
            f'found in your company watchlist, but '
            f'{"none" if company_jobs_found > 0 else "it"} scored {min_score}+/100. '
            f'They will be included when the score threshold is met.'
            f'</div>'
        )
        company_section = threshold_note

    nrw_section = ""
    if nrw_major_jobs:
        nrw_section = (
            _section_header_html("NRW major employers (remote / hybrid NRW)")
            + "".join(_job_card_html(j) for j in nrw_major_jobs)
        )
    elif nrw_major_found:
        nrw_section = (
            f'<div style="margin:16px 24px;padding:14px 18px;background:#f9f9f9;'
            f'border:1px solid #e0e0e0;border-radius:8px;font-size:13px;color:#616161;">'
            f"<strong>NRW major employers</strong><br>"
            f"{nrw_major_found} job(s) found but none met the report filter "
            f"(score ≥{min_score} or should_apply)."
            f"</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{_EMAIL_CSS}{_SECTION_HEADER_CSS}</style>
</head>
<body>
<div class="wrapper">
  <div class="header">
    <h1>Pharma Job Digest &nbsp;&middot;&nbsp; {today}</h1>
    <p>{summary}</p>
  </div>
  {pharmiweb_section}
  {company_section}
  {nrw_section}
  <div class="footer">
    Evaluated {total} active jobs &nbsp;&middot;&nbsp; {today}
    &nbsp;&middot;&nbsp; PharmaJobScraper
  </div>
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Telegram message
# ---------------------------------------------------------------------------

def build_telegram_text(
    jobs: list[dict],
    top_n: int = 5,
    company_jobs: list[dict] | None = None,
    company_jobs_found: int = 0,
    min_score: int = 0,
    nrw_major_jobs: list[dict] | None = None,
    nrw_major_found: int = 0,
) -> str:
    """Build the compact Telegram HTML message.

    Args:
        jobs:               Pharmiweb jobs.
        top_n:              How many pharmiweb jobs to include.
        company_jobs:       Company watchlist jobs above the score threshold.
        company_jobs_found: Total company jobs that passed prescreening.
        min_score:          The minimum score threshold (for the note text).
    """
    today = _date.today().strftime("%-d %b %Y")
    subset = jobs[:top_n]

    lines = [
        f"🔍 <b>Pharma Job Digest · {today}</b>\n",
        f"<b>Top {len(subset)} matches for today:</b>\n",
    ]

    for i, job in enumerate(subset):
        num = _NUMBERS[i] if i < len(_NUMBERS) else f"{i+1}."
        title = escape(job.get("title") or "—")
        employer = escape(job.get("employer") or "—")
        location = escape(job.get("location") or "—")
        contract = escape(job.get("contract_type") or "—")
        score = int(job.get("score") or 0)
        label = "<b>APPLY</b>" if job.get("should_apply") else "review"
        url = job.get("url") or ""

        lines.append(
            f"{num} <b>{title}</b> · {employer}\n"
            f"   📍 {location} · {contract}\n"
            f"   ⭐ Score: {score}/100 · {label}\n"
            f"   🔗 {url}\n"
        )

    lines.append(
        f"\n📧 Full report ({len(jobs)} jobs + AI reasoning) sent to your email."
    )

    # Section 2 — company watchlist (compact bullets)
    if company_jobs:
        lines.append(f"\n\n🏢 <b>Company watchlist — {len(company_jobs)} new today:</b>")
        for job in company_jobs:
            title = escape(job.get("title") or "—")
            employer = escape(job.get("employer") or "—")
            url = job.get("url") or ""
            score = job.get("score")
            score_str = f" · ⭐{int(score)}" if score is not None else ""
            lines.append(f"• <b>{title}</b> @ {employer}{score_str}\n  🔗 {url}")
    elif company_jobs_found:
        lines.append(
            f"\n\n🏢 <b>Company watchlist:</b> {company_jobs_found} new job"
            f"{'s' if company_jobs_found != 1 else ''} found but none scored "
            f"{min_score}+/100."
        )

    if nrw_major_jobs:
        lines.append(
            f"\n\n🏭 <b>NRW major employers — {len(nrw_major_jobs)}:</b>"
        )
        for job in nrw_major_jobs[:8]:
            title = escape(job.get("title") or "—")
            employer = escape(job.get("employer") or "—")
            url = job.get("url") or ""
            score = int(job.get("score") or 0)
            label = "<b>APPLY</b>" if job.get("should_apply") else "review"
            lines.append(
                f"• <b>{title}</b> @ {employer}\n"
                f"   ⭐ Score: {score}/100 · {label}\n"
                f"   🔗 {url}"
            )
    elif nrw_major_found:
        lines.append(
            f"\n\n🏭 <b>NRW major:</b> {nrw_major_found} job(s) below report filter."
        )

    return "\n".join(lines)
