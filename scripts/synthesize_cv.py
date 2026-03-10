"""
CV Synthesis — one-time script to combine multiple specialised CV versions into a
single comprehensive cv_matching.txt optimised for automated job matching.

The output is NOT intended for direct application — it is a union of all source CVs
with explicit academic-to-industry competency mappings and pharma terminology.

Usage:
    python scripts/synthesize_cv.py

Prerequisites:
    - Place one or more CV .txt files in input_data/cv/
    - OPENAI_API_KEY must be set in .env
    - requirements.yaml must exist with a cv_synthesis section

Behaviour:
    - Computes an md5 hash of all source CV files combined.
    - If the hash matches .cv_synthesis_hash, skips the API call (output is up to date).
    - Otherwise calls gpt-5 once, writes cv_matching.txt, and saves the new hash.
"""

import hashlib
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
from openai import OpenAI

from scraper import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(name)s – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("synthesize_cv")

ROOT = Path(__file__).parent.parent
REQUIREMENTS_PATH = ROOT / "requirements.yaml"
HASH_FILE = ROOT / ".cv_synthesis_hash"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_requirements() -> dict:
    if not REQUIREMENTS_PATH.exists():
        logger.error("requirements.yaml not found.")
        sys.exit(1)
    with REQUIREMENTS_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_source_cvs(source_dir: Path) -> list[tuple[str, str]]:
    """Return sorted list of (filename, content) tuples from source_dir."""
    if not source_dir.exists():
        logger.error("CV source directory '%s' does not exist.", source_dir)
        logger.error("Create it and place your CV .txt files there.")
        sys.exit(1)

    cv_files = sorted(source_dir.glob("*.txt"))
    if not cv_files:
        logger.error("No .txt files found in '%s'.", source_dir)
        sys.exit(1)

    result = []
    for path in cv_files:
        text = path.read_text(encoding="utf-8").strip()
        if text:
            result.append((path.name, text))
            logger.info("  Loaded: %s (%d chars)", path.name, len(text))
        else:
            logger.warning("  Skipping empty file: %s", path.name)

    return result


def _combined_hash(cv_list: list[tuple[str, str]]) -> str:
    combined = "".join(f"{name}:{text}" for name, text in cv_list)
    return hashlib.md5(combined.encode()).hexdigest()


def _is_up_to_date(current_hash: str) -> bool:
    if not HASH_FILE.exists():
        return False
    return HASH_FILE.read_text(encoding="utf-8").strip() == current_hash


def _build_prompt(
    cv_list: list[tuple[str, str]],
    tier_1: list[str],
    tier_2: list[str],
) -> str:
    cv_sections = "\n\n".join(
        f"## CV File: {name}\n{text}" for name, text in cv_list
    )
    tier_1_text = "\n".join(f"  - {r}" for r in tier_1) or "  (none specified)"
    tier_2_text = "\n".join(f"  - {r}" for r in tier_2) or "  (none specified)"

    return f"""\
{cv_sections}

## Target Role Types (from job preferences)

TIER 1 — Definitely wants:
{tier_1_text}

TIER 2 — Would work in:
{tier_2_text}

## Instructions

Produce a single unified candidate profile optimised ONLY for automated \
job-matching (this output will never be sent directly to an employer). \
Apply the following rules strictly:

1. UNION, not intersection — include every skill, tool, credential, \
   and experience from all source CVs. Do not drop or summarise anything.
2. Resolve duplicates: where multiple CVs describe the same experience, \
   use the most detailed and comprehensive version.
3. Translate academic language into pharma industry equivalents explicitly \
   throughout, e.g. "SOP-style protocols" becomes \
   "GxP-compliant documentation (SOP authoring)".
4. For the ATV GmbH training program, list every topic covered as a \
   separate bullet point (GxP, ICH-GCP, regulatory submissions, medical \
   project management, commercial lifecycle, etc.).
5. Add an "ACADEMIC-TO-INDUSTRY COMPETENCY MAP" section at the very end. \
   For each key academic competency, write: [Academic term] → [Industry equivalent]. \
   Cover at least 10 mappings.
6. Use pharma/CRO industry-standard terminology throughout where applicable: \
   ICH-GCP, GxP, GMP, CTMS, CRO, CPM, CTM, aCTM, CMC, CAPA, SOP, IMP, \
   protocol deviation, site monitoring, TMF, eTMF, SUSAR, SAE, ICF.
7. Make all language skills explicit with proficiency levels.
8. List all software tools from all CVs in one combined tools section.
9. Output plain text only — no markdown hash (#) headers. \
   Use ALL-CAPS section titles followed by a blank line, \
   matching the format of the source CVs.
10. Do not add any preamble, explanation, or commentary — output the \
    unified CV text only.\
"""


def _estimate_cost(model: str, tokens_input: int, tokens_output: int) -> float:
    pricing = config.OPENAI_PRICING.get(model, {"input": 1.25, "output": 10.00})
    return (tokens_input * pricing["input"] + tokens_output * pricing["output"]) / 1_000_000


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    logger.info("=" * 60)
    logger.info("CV Synthesis")
    logger.info("=" * 60)

    requirements = _load_requirements()
    synthesis_cfg = requirements.get("cv_synthesis", {})

    source_directory = ROOT / synthesis_cfg.get("source_directory", "input_data/cv")
    output_file = ROOT / synthesis_cfg.get("output_file", "cv_matching.txt")
    model = synthesis_cfg.get("model", "gpt-5")

    logger.info("Source directory: %s", source_directory)
    logger.info("Output file:      %s", output_file)
    logger.info("Model:            %s", model)

    # Load source CVs
    logger.info("Loading CV files …")
    cv_list = _load_source_cvs(source_directory)
    logger.info("Loaded %d CV file(s).", len(cv_list))

    # Check if synthesis is needed
    current_hash = _combined_hash(cv_list)
    if _is_up_to_date(current_hash):
        logger.info("cv_matching.txt is up to date — sources unchanged. Nothing to do.")
        logger.info("Delete .cv_synthesis_hash to force regeneration.")
        return

    # Build preferences for goal-directed synthesis
    preferences = requirements.get("job_preferences", {})
    tier_1 = preferences.get("tier_1_definitely", [])
    tier_2 = preferences.get("tier_2_would_work", [])

    # Build prompt and call API
    user_prompt = _build_prompt(cv_list, tier_1, tier_2)
    system_prompt = (
        "You are an expert pharma career consultant who specialises in helping "
        "researchers transition into the pharmaceutical industry. Your task is to "
        "produce a comprehensive, unified candidate profile for use in automated "
        "job matching. Be thorough and precise — nothing from the source CVs should "
        "be lost, and all academic competencies should be translated into recognised "
        "pharma industry equivalents."
    )

    logger.info("Calling %s for synthesis (this may take 20-40 seconds) …", model)

    if not config.OPENAI_API_KEY:
        logger.error("OPENAI_API_KEY is not set. Add it to your .env file.")
        sys.exit(1)

    client = OpenAI(api_key=config.OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
    )

    synthesized_text = response.choices[0].message.content.strip()
    usage = response.usage

    # Write output
    output_file.write_text(synthesized_text, encoding="utf-8")
    logger.info("Written: %s (%d chars)", output_file, len(synthesized_text))

    # Save hash
    HASH_FILE.write_text(current_hash, encoding="utf-8")

    # Cost estimate
    cost = _estimate_cost(model, usage.prompt_tokens, usage.completion_tokens)
    logger.info("-" * 60)
    logger.info("Tokens:  %d input + %d output = %d total",
                usage.prompt_tokens, usage.completion_tokens, usage.total_tokens)
    logger.info("Cost:    ~$%.4f", cost)
    logger.info("=" * 60)
    logger.info("Done. Use cv_matching.txt as the cv_file in requirements.yaml.")


if __name__ == "__main__":
    main()
