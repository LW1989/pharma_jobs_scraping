"""
Career-page discovery helper — one-off, read-only.

Some companies on the watchlist were researched with only a homepage URL. This
script visits each homepage, scores every outgoing link for "looks like a career
page", and prints companies.yaml-ready rows for the best candidate. Nothing is
written: paste the good rows into input_data/companies.yaml yourself and mark the
UNRESOLVED ones `source_type: skip`.

Usage:
    python scripts/discover_career_urls.py                  # the built-in backlog
    python scripts/discover_career_urls.py --name Refoxy    # one backlog company
    python scripts/discover_career_urls.py --url https://example.de --company Foo --city Köln
    python scripts/discover_career_urls.py --check-yaml     # re-check companies.yaml rows
                                                            # whose career_url is a bare homepage

The fetch goes through company_scraper._get_html_career_response(), which warms a
session on the site root first — several of these hosts 403 a cold deep link.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import argparse
import logging
import re
import time
from urllib.parse import urljoin, urlparse, urlunparse

import yaml
from bs4 import BeautifulSoup

from scraper import config
from scraper.company_scraper import _get_html_career_response

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("discover_career_urls")

COMPANIES_PATH = ROOT / "input_data" / "companies.yaml"

# Companies from the Aug 2026 CSV that came with a website but no career URL.
# (name, city, website)
BACKLOG: list[tuple[str, str, str]] = [
    ("Qualistery",                    "Neuss",        "https://qualistery.com"),
    ("MEDPERION",                     "Köln",         "https://www.medperion.de/en"),
    ("Cannaflos",                     "Köln",         "https://cannaflos.de"),
    ("Axiogenesis",                   "Köln",         "https://www.axiogenesis.com"),
    ("The Healthonauts",              "Leverkusen",   "https://www.thehealthonauts.com"),
    ("Refoxy Pharma",                 "Köln",         "https://www.refoxy.com"),
    ("VitrofluidiX",                  "Köln",         "https://vitrofluidix.com"),
    ("IBSA Germany",                  "Düsseldorf",   "https://www.ibsagermany.de"),
    ("PB Pharma",                     "Meerbusch",    "https://www.pbpharma.de"),
    ("ABclonal Technology (Europe)",  "Düsseldorf",   "https://abclonal.com"),
    ("AIRA Pharm",                    "Düsseldorf",   "https://airapharm.de"),
    ("SynBiotic Distribution",        "Köln",         "https://www.synbiotic.com/de/home"),
    ("ROOBS",                         "Köln",         "https://roobs.de/de/startseite/"),
    ("!mmunetrue",                    "",             "https://www.immunetrue.eu"),
    ("Precimmo",                      "Köln",         "https://precimmo.com"),
    ("Orthogen",                      "Düsseldorf",   "https://www.orthogen.org"),
    ("ToRa Pharma",                   "Troisdorf",    "https://tora-pharma.de"),
    ("NMVS Connect",                  "",             "https://www.nmvs-connect.com"),
]

# Path/anchor keywords scored highest → lowest. A career page usually says
# "Karriere" or "Career"; a jobs listing is nearly as good; "Team"/"Über uns"
# only counts when nothing better exists.
KEYWORD_SCORES: list[tuple[str, int]] = [
    (r"karriere",                     10),
    (r"careers?\b",                   10),
    (r"stellenangebot|stellenanzeige|stellenausschreibung", 9),
    (r"jobb[oö]rse|job-?boerse",       9),
    (r"\bjobs?\b",                     8),
    (r"\bstellen\b",                   8),
    (r"vacanc|vacature",               8),
    (r"offene-?stellen",               9),
    (r"work-?with-?us|mit-?uns-?arbeiten|arbeiten-?bei", 7),
    (r"join-?us|join-?our",            6),
    (r"bewerb",                        5),
    (r"\bteam\b",                      2),
]

# Links that superficially match but are never a career listing.
NEGATIVE = re.compile(
    r"(job-?alert|jobmail|linkedin\.com|xing\.com|facebook\.com|instagram\.com|"
    r"twitter\.com|x\.com/|youtube\.com|\.pdf$|mailto:|tel:)",
    re.IGNORECASE,
)


def _clean_url(url: str) -> str:
    """Drop fragments and ad-tracking query params."""
    p = urlparse(url)
    if p.query:
        keep = [
            kv for kv in p.query.split("&")
            if not re.match(
                r"^(gclid|gad_source|gad_campaignid|gbraid|wbraid|srsltid|fbclid|"
                r"utm_[a-z]+|tw_[a-z]+|_su_rec.*)=",
                kv, re.IGNORECASE,
            )
        ]
        p = p._replace(query="&".join(keep))
    return urlunparse(p._replace(fragment=""))


def _score_link(href: str, text: str, base_netloc: str) -> int:
    if NEGATIVE.search(href):
        return 0
    haystack_path = urlparse(href).path.lower() + " " + (urlparse(href).query or "").lower()
    haystack_text = (text or "").strip().lower()

    score = 0
    for pattern, points in KEYWORD_SCORES:
        in_path = bool(re.search(pattern, haystack_path, re.IGNORECASE))
        in_text = bool(re.search(pattern, haystack_text, re.IGNORECASE))
        if in_path or in_text:
            # A keyword in the URL path is far stronger evidence than link text,
            # which is often a generic nav label.
            score = max(score, points + (3 if in_path else 0))

    if not score:
        return 0
    if urlparse(href).netloc and urlparse(href).netloc != base_netloc:
        score -= 4          # off-site (an ATS subdomain still wins on keyword strength)
    return score


def discover(name: str, website: str) -> list[tuple[int, str, str]]:
    """Return [(score, url, link_text)] best first, for one company homepage."""
    resp = _get_html_career_response(website)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    base_netloc = urlparse(resp.url).netloc

    seen: dict[str, tuple[int, str]] = {}
    for a in soup.find_all("a", href=True):
        href = _clean_url(urljoin(resp.url, a["href"].strip()))
        if not href.startswith("http"):
            continue
        text = a.get_text(" ", strip=True)[:80]
        score = _score_link(href, text, base_netloc)
        if score <= 0:
            continue
        # Keep the highest-scoring occurrence of each URL.
        if href not in seen or score > seen[href][0]:
            seen[href] = (score, text)

    ranked = sorted(
        ((score, url, text) for url, (score, text) in seen.items()),
        key=lambda t: (-t[0], len(t[1]) or 99, t[1]),
    )
    return ranked


def _yaml_row(name: str, city: str, url: str) -> str:
    return (
        f"  - name: {name}\n"
        f"    city: {city}\n"
        f"    country: Germany\n"
        f"    career_url: {url}\n"
        f"    source_type: html\n"
    )


def _yaml_skip_row(name: str, city: str, website: str) -> str:
    return (
        f"  # No career page found on {website}\n"
        f"  - name: {name}\n"
        f"    city: {city}\n"
        f"    country: Germany\n"
        f"    career_url: {website}\n"
        f"    source_type: skip\n"
    )


def _homepage_rows_from_yaml() -> list[tuple[str, str, str]]:
    """companies.yaml rows whose career_url looks like a bare homepage."""
    with COMPANIES_PATH.open(encoding="utf-8") as f:
        entries = yaml.safe_load(f)["companies"]
    out = []
    for c in entries:
        url = c.get("career_url", "")
        if url and urlparse(url).path.strip("/") == "":
            out.append((c["name"], c.get("city", ""), url))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", help="Only this backlog company (substring match)")
    parser.add_argument("--url", help="Ad-hoc website to inspect")
    parser.add_argument("--company", default="Unknown", help="Name to use with --url")
    parser.add_argument("--city", default="", help="City to use with --url")
    parser.add_argument("--check-yaml", action="store_true",
                        help="Inspect companies.yaml rows whose career_url is a bare homepage")
    parser.add_argument("--top", type=int, default=5, help="Candidates to print per company")
    args = parser.parse_args()

    if args.url:
        targets = [(args.company, args.city, args.url)]
    elif args.check_yaml:
        targets = _homepage_rows_from_yaml()
    else:
        targets = BACKLOG
        if args.name:
            needle = args.name.lower()
            targets = [t for t in targets if needle in t[0].lower()]
            if not targets:
                logger.error("No backlog company matching %r", args.name)
                sys.exit(1)

    print()
    print("=" * 72)
    print(f"  Career-page discovery — {len(targets)} company/companies (no writes)")
    print("=" * 72)

    resolved: list[tuple[str, str, str]] = []
    unresolved: list[tuple[str, str, str, str]] = []

    for i, (name, city, website) in enumerate(targets, 1):
        logger.info("[%d/%d]  %-32s %s", i, len(targets), name, website)
        try:
            ranked = discover(name, website)
        except Exception as exc:
            logger.warning("    FAILED: %s", exc)
            unresolved.append((name, city, website, str(exc)[:80]))
            time.sleep(config.REQUEST_DELAY_SECONDS)
            continue

        if not ranked:
            logger.info("    no career-like link found")
            unresolved.append((name, city, website, "no career-like link"))
        else:
            for score, url, text in ranked[: args.top]:
                logger.info("    %2d  %-58s  %s", score, url[:58], text[:30])
            resolved.append((name, city, ranked[0][1]))
        time.sleep(config.REQUEST_DELAY_SECONDS)

    print()
    print("=" * 72)
    print("  companies.yaml rows — review each URL before pasting")
    print("=" * 72)
    print()
    for name, city, url in resolved:
        print(_yaml_row(name, city, url))

    if unresolved:
        print("=" * 72)
        print("  UNRESOLVED — add as skip, or research by hand")
        print("=" * 72)
        print()
        for name, city, website, reason in unresolved:
            print(f"  # {reason}")
            print(_yaml_skip_row(name, city, website))

    print(f"  {len(resolved)} resolved, {len(unresolved)} unresolved")


if __name__ == "__main__":
    main()
