"""
OpenAI LLM client for job-CV evaluation.

Applies four evidence-based prompting techniques:
  1. Role-first activation    — domain persona before task instructions
  2. Rubric with score anchors — explicit high/mid/low descriptions per dimension
  3. Guided per-dimension analysis — lightweight CoT without open-ended "think step by step"
  4. Few-shot examples        — three edge-case examples (strong / moderate / poor match)

Uses OpenAI Structured Outputs (strict=True) to guarantee JSON schema compliance.

Public API:
    evaluate(job, cv_text, model, threshold) -> EvalResult
"""

import json
import logging
from dataclasses import dataclass

from openai import OpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from scraper import config

logger = logging.getLogger(__name__)

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not config.OPENAI_API_KEY:
            raise ValueError(
                "OPENAI_API_KEY is not set. Add it to your .env file."
            )
        _client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _client


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {
            "type": "integer",
            "description": "Overall fit score 0-100",
        },
        "score_reasoning": {
            "type": "string",
            "description": "2-3 sentences explaining the score",
        },
        "should_apply": {
            "type": "boolean",
            "description": "True if score meets the application threshold",
        },
    },
    "required": ["score", "score_reasoning", "should_apply"],
    "additionalProperties": False,
}


@dataclass
class EvalResult:
    score: int
    score_reasoning: str
    should_apply: bool
    tokens_input: int
    tokens_output: int
    tokens_total: int


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a senior pharma and life sciences recruiter with 15 years of \
experience placing candidates across clinical operations, regulatory \
affairs, pharmacovigilance, and drug development.

Your task is to evaluate how well a job posting matches a candidate's \
CV and return a structured JSON assessment. Be calibrated: reserve \
scores above 80 for genuine strong fits, and below 30 for clear \
mismatches. Do not inflate scores for vague thematic relevance.\
"""

FEW_SHOT_EXAMPLES = """\
## Few-Shot Examples

### Example 1 — Strong match
CV: 8 years CRA/CRA II at CROs, oncology trials, CTMS/Veeva certified, \
BSc Life Sciences, experience managing site relationships across EU.
Job: Senior CRA – Oncology, Permanent, UK/Homeworking, Experienced.

Analysis:
  A. Skills: CV directly covers CRA work, oncology, Veeva — score 90
  B. Seniority: 8 years matches Senior CRA — score 85
  C. Domain: Oncology-to-oncology exact match — score 95
  D. Progression: Senior title is a natural next step — score 80
Output: {"score": 88, "score_reasoning": "Direct oncology CRA experience with Veeva certification maps precisely to the role requirements. Seniority and domain are well aligned. Clear upward step from current position.", "should_apply": true}

### Example 2 — Moderate match
CV: 4 years in pharmacovigilance case processing, EU regulatory knowledge, \
BSc Pharmacy. No project management experience.
Job: Clinical Project Manager – Phase II, Permanent, Remote, Experienced.

Analysis:
  A. Skills: PV background lacks core CPM skills (budgets, timelines, PM tools) — score 35
  B. Seniority: Experience level matches but function is different — score 60
  C. Domain: Drug development adjacent but different function — score 55
  D. Progression: Functional shift, not a natural linear step — score 45
Output: {"score": 47, "score_reasoning": "Strong regulatory grounding but significant skills gap in project management and clinical operations. The role would require a substantial functional pivot rather than a direct progression.", "should_apply": false}

### Example 3 — Poor match
CV: 3 years medical sales representative, cardiovascular products, UK field.
Job: Biostatistician – Phase III oncology, Permanent, Germany, Experienced.

Analysis:
  A. Skills: No statistics, SAS, or clinical trial methodology in CV — score 5
  B. Seniority: Similar years but entirely different career track — score 20
  C. Domain: Cardiovascular sales vs. oncology biostats — score 10
  D. Progression: Unrelated trajectory — score 10
Output: {"score": 12, "score_reasoning": "CV background in medical sales has no overlap with the quantitative and clinical research skills required. Domain, methodology, and career trajectory are all misaligned.", "should_apply": false}\
"""


def _build_user_prompt(job: dict, cv_text: str, threshold: int) -> str:
    job_details = (job.get("job_details") or "")[:2000]

    return f"""\
## Candidate CV
{cv_text}

## Job Posting
Title:            {job.get('title', 'N/A')}
Employer:         {job.get('employer', 'N/A')}
Location:         {job.get('location', 'N/A')}
Contract Type:    {job.get('contract_type', 'N/A')}
Experience Level: {job.get('experience_level', 'N/A')}
Discipline:       {job.get('discipline', 'N/A')}
Closing Date:     {job.get('closing_date', 'N/A')}

Job Description:
{job_details}

## Scoring Instructions

Evaluate the match across four dimensions. For each, write 1 sentence \
of analysis, then assign a sub-score (0–100). The final score is the \
weighted average.

Dimensions and weights:
  A. Skills & experience match (40%)
     High (80–100): CV directly demonstrates the required skills/tools/methods
     Mid  (40–79):  Transferable skills present but gaps exist
     Low  (0–39):   Core required skills absent from CV

  B. Seniority fit (20%)
     High: Role level matches candidate's years of experience and current title
     Mid:  One level above or below; stretch or slight step-down
     Low:  Significant mismatch (e.g. Director role for a junior candidate)

  C. Domain / therapeutic area relevance (20%)
     High: Same therapeutic area or functional domain as CV experience
     Mid:  Adjacent domain (e.g. oncology → rare disease)
     Low:  Unrelated domain with no overlap

  D. Career progression value (20%)
     High: Clear step forward in responsibility, scope, or prestige
     Mid:  Lateral move with some new exposure
     Low:  Step backwards or no development value

After your per-dimension analysis, compute the weighted score and set \
should_apply = true if final score >= {threshold}.

{FEW_SHOT_EXAMPLES}

## Now evaluate the job posting above. Return only valid JSON.\
"""


# ---------------------------------------------------------------------------
# API call with retry
# ---------------------------------------------------------------------------

@retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)
def _call_api(model: str, system_prompt: str, user_prompt: str) -> dict:
    client = _get_client()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "job_evaluation",
                "strict": True,
                "schema": RESPONSE_SCHEMA,
            },
        },
    )
    content = response.choices[0].message.content
    usage = response.usage
    return {
        "content": content,
        "tokens_input":  usage.prompt_tokens,
        "tokens_output": usage.completion_tokens,
        "tokens_total":  usage.total_tokens,
    }


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def evaluate(job: dict, cv_text: str, model: str, threshold: int) -> EvalResult:
    """
    Evaluate a single job against the CV using the LLM.

    Args:
        job:       DB row dict with at minimum job_id, title, employer, etc.
        cv_text:   Plain text CV content.
        model:     OpenAI model name (e.g. "gpt-4o-mini").
        threshold: Minimum score for should_apply = True.

    Returns:
        EvalResult dataclass with score, reasoning, flag, and token counts.
    """
    user_prompt = _build_user_prompt(job, cv_text, threshold)

    logger.debug("Calling %s for job %s …", model, job.get("job_id"))
    raw = _call_api(model, SYSTEM_PROMPT, user_prompt)

    parsed = json.loads(raw["content"])

    # Clamp score to valid range in case model ignores the schema bounds
    score = max(0, min(100, int(parsed["score"])))

    return EvalResult(
        score=score,
        score_reasoning=parsed["score_reasoning"],
        should_apply=bool(parsed["should_apply"]),
        tokens_input=raw["tokens_input"],
        tokens_output=raw["tokens_output"],
        tokens_total=raw["tokens_total"],
    )
