"""
Sync input_data/companies.yaml from the Google Sheet company watchlist.

Run this whenever you add or edit companies in the sheet. deploy/run_pipeline.sh
also runs it nightly (non-fatally) before run_company_checker.py.

Usage:
    python scripts/sync_companies_from_sheet.py

Prerequisites:
  1. GOOGLE_SHEET_ID in .env (the spreadsheet ID from the URL)
  2. GOOGLE_CREDENTIALS_FILE in .env pointing to a service account JSON key
     (default: input_data/google_credentials.json — git-ignored)
  3. The service account must have "Viewer" access to the sheet.
     Share the sheet with the service account email in the JSON key.
  4. pip install gspread google-auth

What it does:
  - Reads Company, City, Career URL columns from the sheet.
  - Skips rows with no career URL.
  - Auto-detects source_type from the URL pattern.
  - Diffs against the existing companies.yaml.
  - Prints added / changed companies and writes the updated YAML.
  - Removed companies are flagged but NOT deleted (manual review required).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
import re
from urllib.parse import urlparse

import yaml

from scraper.urls import canonical, is_less_specific, strip_tracking_params

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
COMPANIES_PATH = ROOT / "input_data" / "companies.yaml"

# Column indices (0-based) in the Google Sheet
# A=Company, B=City, C=Profile, D=Jobs, E=Accept Initiative Applications, F=Open positions, G=Interesting
COL_COMPANY = 0
COL_CITY    = 1
COL_PROFILE = 2
COL_JOBS    = 3   # career URL (may be a URL, "Yes", "No", or empty)
# Columns E and G are intentionally NOT used for filtering — we include every
# company that has a scrapable career URL regardless of whether they accept
# unsolicited applications or how "interesting" they are.


def _detect_source_type(url: str) -> tuple[str, str | None]:
    """Return (source_type, slug_or_None) inferred from the career URL."""
    if not url:
        return "skip", None
    u = url.lower()
    if "personio.de" in u:
        # https://{slug}.jobs.personio.de  or  https://{slug}.personio.de
        m = re.search(r"https?://([^.]+)\.(?:jobs\.)?personio\.de", url, re.I)
        return "personio", (m.group(1) if m else None)
    if "workable.com" in u:
        # https://apply.workable.com/{slug}/
        m = re.search(r"workable\.com/([^/?#]+)", url, re.I)
        return "workable", (m.group(1) if m else None)
    if "recruitee.com" in u:
        # https://{slug}.recruitee.com
        m = re.search(r"https?://([^.]+)\.recruitee\.com", url, re.I)
        return "recruitee", (m.group(1) if m else None)
    if "join.com" in u:
        # https://join.com/companies/{slug}
        m = re.search(r"join\.com/companies/([^/?#]+)", url, re.I)
        return "join", (m.group(1) if m else None)
    if "myworkdayjobs.com" in u:
        return "html", None   # Workday has no public JSON API
    return "html", None


# Legal forms and decorations the sheet spells inconsistently. "Cannamedical
# Pharma" and "Cannamedical", or "Singleron Biotechnologies HR" and "Singleron
# Biotechnologies", are the same company; matching on the literal name re-adds
# them as duplicates that are then scraped twice under two job_id sets.
# Legal forms and a couple of decorations the sheet spells inconsistently
# ("… GmbH & Co. KG", "… HR"). Deliberately NOT geographic words: both the
# sheet and the YAML carry "Germany"/"Europe" identically, so stripping them
# only risks collapsing a real name to a generic token.
_NAME_NOISE_RE = re.compile(
    r"\b(gmbh|mbh|ag|kg|se|ohg|ug|e\.?\s?v|co(mpany)?|group|holding|hr)\b"
    r"|[®™&.,()]",
    re.IGNORECASE,
)


def _normalise_name(name: str) -> str:
    """Comparison key for company identity across sheet/YAML spellings."""
    cleaned = _NAME_NOISE_RE.sub(" ", name or "")
    return " ".join(cleaned.split()).strip().lower()


def _alias_map(existing: dict[str, dict]) -> dict[str, str]:
    """
    Normalised alias -> canonical companies.yaml name.

    Some sheet spellings differ by more than a legal form ("Medios Solutions
    Bonn", "Cannamedical Biotech"), and two sheet rows were deliberately
    consolidated into one entry. Those are recorded as an `aliases:` list on
    the entry rather than guessed.
    """
    out: dict[str, str] = {}
    for name, entry in existing.items():
        for alias in entry.get("aliases") or []:
            out[_normalise_name(alias)] = name
    return out


def _blocked_names() -> dict[str, str]:
    """
    Normalised name -> why it must never be added to the watchlist.

    companies_excluded.yaml records the companies deliberately left out —
    industry associations whose postings belong to member firms, recruiters,
    and rows already covered elsewhere. nrw_major_employers.yaml holds the
    large employers routed to the other system. Without this, the nightly sync
    re-adds every one of them from the sheet and quietly reverses the decision.
    """
    blocked: dict[str, str] = {}

    def _add(name: str, reason: str) -> None:
        norm = _normalise_name(name)
        if not norm:
            return
        blocked.setdefault(norm, reason)

    excluded_path = ROOT / "input_data" / "companies_excluded.yaml"
    if excluded_path.exists():
        with excluded_path.open(encoding="utf-8") as f:
            for row in (yaml.safe_load(f) or {}).get("excluded", []):
                reason = row.get("reason") or row.get("covered_by") or "excluded"
                _add(row["name"], f"companies_excluded.yaml: {str(reason).strip()[:80]}")

    nrw_path = ROOT / "input_data" / "nrw_major_employers.yaml"
    if nrw_path.exists():
        with nrw_path.open(encoding="utf-8") as f:
            for row in (yaml.safe_load(f) or {}).get("employers", []):
                _add(row["name"],
                     f"already scraped as an NRW major ({row.get('source_type')})")

    return blocked


def _load_existing() -> dict[str, dict]:
    """Return existing companies keyed by name."""
    if not COMPANIES_PATH.exists():
        return {}
    with COMPANIES_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {c["name"]: c for c in data.get("companies", [])}


def _fetch_sheet_rows() -> list[dict]:
    """Fetch rows from the Google Sheet and return normalised company dicts."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        logger.error(
            "gspread and google-auth are required. "
            "Run: pip install gspread google-auth"
        )
        sys.exit(1)

    from scraper import config

    creds_path = Path(config.GOOGLE_CREDENTIALS_FILE)
    if not creds_path.exists():
        logger.error(
            "Google credentials file not found: %s\n"
            "Set GOOGLE_CREDENTIALS_FILE in .env or place the service account JSON at %s",
            creds_path, creds_path,
        )
        sys.exit(1)

    if not config.GOOGLE_SHEET_ID:
        logger.error("GOOGLE_SHEET_ID is not set in .env")
        sys.exit(1)

    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = Credentials.from_service_account_file(str(creds_path), scopes=scopes)
    gc = gspread.authorize(creds)

    sheet = gc.open_by_key(config.GOOGLE_SHEET_ID).sheet1
    rows = sheet.get_all_values()

    companies = []
    for row in rows:
        # Pad row to at least 4 columns
        while len(row) < 4:
            row.append("")

        name     = row[COL_COMPANY].strip()
        jobs_col = row[COL_JOBS].strip()
        profile  = row[COL_PROFILE].strip() if len(row) > COL_PROFILE else ""

        # Skip header row, empty rows, and the job-hub aggregate row
        if not name or name.lower() in ("company", "") or name.startswith("http"):
            continue

        # Resolve career URL:
        #   - Jobs column has a URL         → use it directly
        #   - Jobs column is "Yes"          → career page = Profile column URL
        #   - Jobs column is "No" or empty  → no scrapable page, skip entirely
        if jobs_col.startswith("http"):
            career_url = jobs_col
        elif jobs_col.lower() == "yes" and profile.startswith("http"):
            career_url = profile
        else:
            # "No", empty, or unparseable — nothing to scrape
            continue

        # City: use column B if it looks like a city name, not a URL
        city = row[COL_CITY].strip() if len(row) > COL_CITY else ""
        if city.startswith("http"):
            city = ""   # malformed row (URL ended up in city column)

        source_type, slug = _detect_source_type(career_url)
        entry: dict = {
            "name":        name,
            "city":        city,
            "country":     "Germany",
            "career_url":  career_url,
            "source_type": source_type,
        }
        if slug:
            entry["slug"] = slug
        companies.append(entry)

    logger.info("Read %d company rows from Google Sheet", len(companies))
    return companies


def _sheet_id() -> str:
    from scraper import config
    return config.GOOGLE_SHEET_ID


def main() -> None:
    logger.info("Loading existing companies.yaml …")
    existing = _load_existing()
    logger.info("  %d companies currently in YAML", len(existing))

    logger.info("Fetching rows from Google Sheet …")
    sheet_companies = _fetch_sheet_rows()

    # Normalise before diffing: the sheet's URLs carry per-click ad parameters,
    # which would otherwise make an unchanged page look changed every night and
    # land in the stored job URL.
    for entry in sheet_companies:
        entry["career_url"] = strip_tracking_params(entry.get("career_url", ""))

    sheet_by_name = {c["name"]: c for c in sheet_companies}

    blocked = _blocked_names()
    by_norm = {_normalise_name(n): n for n in existing}
    by_norm.update(_alias_map(existing))

    added       = []
    added_norms = set()   # norms of companies being added THIS run
    changed     = []
    missing     = []
    refused     = []
    aliased     = []
    kept        = []

    # Detect new and changed entries
    for name, entry in sheet_by_name.items():
        norm = _normalise_name(name)

        if not norm:
            # A name that is only legal forms/punctuation — cannot be matched
            # or de-duplicated reliably; treat it as a distinct new row.
            added.append(entry)
            continue

        if norm in blocked:
            refused.append((name, blocked[norm]))
            continue

        # by_norm maps only to names that exist in companies.yaml, so target
        # is always a real existing entry — never a sheet-only spelling, which
        # would KeyError at existing[target] below.
        target = name if name in existing else by_norm.get(norm)
        if target is None:
            if norm in added_norms:
                # A second spelling of a company already being added this run
                # (e.g. "Acme GmbH" after "Acme"). Fold it in, don't re-add.
                continue
            added.append(entry)
            added_norms.add(norm)
            continue

        if target != name:
            aliased.append((name, target))
        old_entry = existing[target]
        old_url = old_entry.get("career_url") or ""
        new_url = entry.get("career_url") or ""
        if canonical(old_url) == canonical(new_url):
            continue
        if is_less_specific(new_url, old_url):
            # The sheet often holds a homepage or a section index where someone
            # has since found the actual listing page; do not walk that back.
            kept.append((target, old_url, new_url))
            continue
        changed.append((old_entry, entry))

    # Detect removed entries
    for name in existing:
        if name not in sheet_by_name:
            missing.append(name)

    # Print diff
    if added:
        logger.info("\nNEW companies (%d):", len(added))
        for c in added:
            logger.info("  + %s (%s) — %s [%s]",
                        c["name"], c.get("city"), c["career_url"], c["source_type"])

    if changed:
        logger.info("\nCHANGED career URLs (%d):", len(changed))
        for old, new in changed:
            logger.info("  ~ %s: %s → %s", old["name"], old["career_url"], new["career_url"])

    if refused:
        logger.info("\nREFUSED (%d) — deliberately not in the watchlist:", len(refused))
        for name, why in refused:
            logger.info("  x %s — %s", name, why)

    if aliased:
        logger.info("\nMATCHED to an existing entry under another spelling (%d):",
                    len(aliased))
        for sheet_name, yaml_name in aliased:
            logger.info("  = %s → %s", sheet_name, yaml_name)

    if kept:
        logger.info("\nKEPT the researched career URL over the sheet homepage (%d):",
                    len(kept))
        for name, old_url, new_url in kept:
            logger.info("  = %s: kept %s (sheet has %s)", name, old_url, new_url)

    if missing:
        logger.info("\nNOT IN SHEET any more (%d) — NOT removed (manual review required):",
                    len(missing))
        for name in missing:
            logger.info("  ? %s", name)

    if not added and not changed:
        logger.info("\nNo changes detected. companies.yaml is up to date.")
        return

    # Merge: update existing + append new (preserve order and any manual fields like notes)
    merged: dict[str, dict] = dict(existing)

    for old, new in changed:
        merged[old["name"]].update({
            "career_url":  new["career_url"],
            "source_type": new["source_type"],
        })
        if old.get("source_type") == "skip" and new["source_type"] != "skip":
            # A skip row carries a notes: explaining that no career page was
            # found. Someone putting a Jobs URL in the sheet is re-enabling it
            # deliberately, so honour that — but drop the note, which is now
            # false and would otherwise outlive the reason it was written.
            merged[old["name"]].pop("notes", None)
            logger.info("  ^ %s re-enabled from skip → %s (sheet now has a Jobs URL)",
                        old["name"], new["source_type"])
        if new.get("slug"):
            merged[old["name"]]["slug"] = new["slug"]
        elif "slug" in merged[old["name"]] and not new.get("slug"):
            del merged[old["name"]]["slug"]

    for entry in added:
        merged[entry["name"]] = entry

    # Write back preserving the file header comment
    header = (
        "# Company watchlist — NRW pharma/biotech companies with their own career pages.\n"
        "#\n"
        "# NOTE: this file was last written by sync_companies_from_sheet.py, which\n"
        "# rewrites it from the sheet and therefore drops any hand-written comments.\n"
        "#\n"
        f"# Populated from: https://docs.google.com/spreadsheets/d/{_sheet_id()}\n"
        "# Keep up to date by running: python scripts/sync_companies_from_sheet.py\n\n"
    )

    output = header + yaml.dump(
        {"companies": list(merged.values())},
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )

    COMPANIES_PATH.write_text(output, encoding="utf-8")
    logger.info(
        "\nWrote %d companies to %s (+%d new, %d updated).",
        len(merged), COMPANIES_PATH, len(added), len(changed),
    )


if __name__ == "__main__":
    main()
