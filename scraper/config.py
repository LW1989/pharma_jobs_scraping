import os
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.environ["DB_HOST"]
DB_PORT = int(os.environ.get("DB_PORT", 5432))
DB_NAME = os.environ["DB_NAME"]
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]

# OpenAI — required only when running run_evaluator.py
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")

# Email — required only when running run_reporter.py
SMTP_HOST     = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER     = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
REPORT_TO     = os.environ.get("REPORT_TO", "")   # comma-separated recipients

# Telegram — required only when running run_reporter.py
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

# Local pipeline test: skip email/Telegram/mark_sent (run_reporter.py)
REPORTER_DRY_RUN = os.environ.get("REPORTER_DRY_RUN", "").lower() in (
    "1",
    "true",
    "yes",
)

# Google Sheets API — required only for scripts/sync_companies_from_sheet.py
GOOGLE_SHEET_ID         = os.environ.get("GOOGLE_SHEET_ID", "")
GOOGLE_CREDENTIALS_FILE = os.environ.get(
    "GOOGLE_CREDENTIALS_FILE", "input_data/google_credentials.json"
)

# Pricing per 1M tokens (input / output) used for cost estimation in evaluation_runs.
# Source: https://openai.com/api/pricing/ — verified March 2026
OPENAI_PRICING: dict[str, dict[str, float]] = {
    # GPT-5 family (current generation, recommended)
    "gpt-5-nano":            {"input": 0.05,  "output": 0.40},   # cheapest
    "gpt-5-mini":            {"input": 0.25,  "output": 2.00},   # recommended default
    "gpt-5":                 {"input": 1.25,  "output": 10.00},
    # GPT-4.1 family
    "gpt-4.1-nano":          {"input": 0.10,  "output": 0.40},
    "gpt-4.1-mini":          {"input": 0.40,  "output": 1.60},
    "gpt-4.1":               {"input": 2.00,  "output": 8.00},
    # Reasoning models
    "o4-mini":               {"input": 1.10,  "output": 4.40},
    "o3":                    {"input": 2.00,  "output": 8.00},
    # Legacy (still work, but superseded)
    "gpt-4o-mini":           {"input": 0.15,  "output": 0.60},
    "gpt-4o":                {"input": 2.50,  "output": 10.00},
}

# Pharmiweb: Germany (127), Netherlands (148), Belgium (115). Luxembourg rarely
# has a dedicated facet; LU roles often appear under BE/NL. Override via env
# PHARMIWEB_LOCATION_IDS=127,148,115
def _pharmiweb_location_ids() -> list[int]:
    raw = os.environ.get("PHARMIWEB_LOCATION_IDS", "127,148,115")
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out or [127, 148, 115]


PHARMIWEB_LOCATION_IDS = _pharmiweb_location_ids()

# First ID used for backward-compat logging only
SEARCH_BASE_URL = (
    "https://www.pharmiweb.jobs/searchjobs/"
    f"?Keywords=&LocationId={PHARMIWEB_LOCATION_IDS[0]}&RadialLocation=100&CountryCode="
)
JOB_BASE_URL = "https://www.pharmiweb.jobs/job/"

REQUEST_DELAY_SECONDS = 0.5
REQUEST_TIMEOUT_SECONDS = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}
