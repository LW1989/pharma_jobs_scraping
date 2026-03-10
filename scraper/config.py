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
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

# Pricing per 1M tokens (input / output) used for cost estimation in evaluation_runs
OPENAI_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o-mini":           {"input": 0.15,  "output": 0.60},
    "gpt-4o-mini-2024-07-18":{"input": 0.15,  "output": 0.60},
    "gpt-4o":                {"input": 2.50,  "output": 10.00},
    "gpt-4o-2024-08-06":     {"input": 2.50,  "output": 10.00},
}

SEARCH_BASE_URL = (
    "https://www.pharmiweb.jobs/searchjobs/"
    "?Keywords=&LocationId=3&RadialLocation=100&LocationId=20752010&CountryCode="
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
