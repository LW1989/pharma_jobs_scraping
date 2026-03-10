"""
OpenAI LLM client for job-CV evaluation.

Applies five evidence-based prompting techniques:
  1. Role-first activation     — domain persona before task instructions
  2. Candidate context block   — active industry training + transferable skills
  3. Rubric with score anchors — explicit high/mid/low descriptions per dimension
  4. Guided per-dimension analysis — lightweight CoT without open-ended "think step by step"
  5. Few-shot examples         — four examples including an academic-to-industry trainee

Uses OpenAI Structured Outputs (strict=True) to guarantee JSON schema compliance.

Public API:
    evaluate(job, cv_text, model, threshold, tier_1_threshold, preferences) -> EvalResult
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
            "description": "True if score meets the application threshold for this role's tier",
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
You are a pharma hiring manager with experience evaluating candidates entering \
the industry for the first time from a research or academic background. You \
understand that PhD researchers with active GxP/GCP industry training bring \
genuine, credible qualifications for clinical operations and project management \
roles — not just "potential". You credit structured industry training programs, \
academic project management, SOP authorship, and method validation as real \
industry-equivalent competencies, and you score them accordingly rather than \
penalising the absence of a prior industry job title.

Your task is to evaluate how well a job posting matches a candidate's CV and \
return a structured JSON assessment. Be calibrated: reserve scores above 80 for \
genuine strong fits, and below 30 for clear mismatches.\
"""

CANDIDATE_CONTEXT = """\
## Candidate Context (read before scoring)

This candidate holds a PhD in Biochemistry (grade A) and a B.Sc. in Pharmacy, \
with 9+ years of international R&D experience. She is currently enrolled \
(since December 2025) in a full-time pharmaceutical industry training program \
(ATV GmbH, Cologne) covering GxP quality systems, ICH-GCP clinical trial \
processes, regulatory submissions in Germany and the EU, and medical project \
management. This is not a theoretical course — she is actively building \
structured industry knowledge alongside her academic background.

Her transferable competencies that map directly to industry roles:
- Project management: multi-team coordination, milestone tracking, timeline \
  management, MS Project
- Clinical documentation: SOP authoring, protocol writing, troubleshooting \
  guides, training materials
- Method development and validation (ICH-equivalent experience from research)
- Team supervision: 3 direct reports (technician, BSc student, MSc student)
- Tech transfer: 6-month cross-institutional project at Fraunhofer IME \
  (protocol standardisation, benchmarking, quality evaluation)
- Scientific expertise: biochemistry, molecular biology, neurodegenerative \
  diseases (Alzheimer's), drug discovery
- Languages: German B2+ (professional), English C1, Portuguese (native), \
  Spanish B2

When scoring, do NOT penalise her for lacking a prior industry job title. \
Her GCP training is actively underway — treat it as equivalent to a GCP \
certification in progress. A score of 65–80 is appropriate for roles in \
clinical trial management, R&D project management, or clinical operations \
where her combined academic skills and current training form a credible and \
competitive profile.\
"""

FEW_SHOT_EXAMPLES = """\
## Few-Shot Examples

### Example 1 — Strong match (industry-to-industry)
CV: 8 years CRA/CRA II at CROs, oncology trials, CTMS/Veeva certified, \
BSc Life Sciences, experience managing site relationships across EU.
Job: Senior CRA – Oncology, Permanent, UK/Homeworking, Experienced.

Analysis:
  A. Skills: CV directly covers CRA work, oncology, Veeva — score 90
  B. Seniority: 8 years matches Senior CRA — score 85
  C. Domain: Oncology-to-oncology exact match — score 95
  D. Progression: Senior title is a natural next step — score 80
Output: {"score": 88, "score_reasoning": "Direct oncology CRA experience with \
Veeva certification maps precisely to the role requirements. Seniority and domain \
are well aligned. Clear upward step from current position.", "should_apply": true}

### Example 2 — Moderate match
CV: 4 years in pharmacovigilance case processing, EU regulatory knowledge, \
BSc Pharmacy. No project management experience.
Job: Clinical Project Manager – Phase II, Permanent, Remote, Experienced.

Analysis:
  A. Skills: PV background lacks core CPM skills (budgets, timelines, PM tools) — score 35
  B. Seniority: Experience level matches but function is different — score 60
  C. Domain: Drug development adjacent but different function — score 55
  D. Progression: Functional shift, not a natural linear step — score 45
Output: {"score": 47, "score_reasoning": "Strong regulatory grounding but \
significant skills gap in project management and clinical operations. The role \
would require a substantial functional pivot rather than a direct progression.", \
"should_apply": false}

### Example 3 — Poor match
CV: 3 years medical sales representative, cardiovascular products, UK field.
Job: Biostatistician – Phase III oncology, Permanent, Germany, Experienced.

Analysis:
  A. Skills: No statistics, SAS, or clinical trial methodology in CV — score 5
  B. Seniority: Similar years but entirely different career track — score 20
  C. Domain: Cardiovascular sales vs. oncology biostats — score 10
  D. Progression: Unrelated trajectory — score 10
Output: {"score": 12, "score_reasoning": "CV background in medical sales has no \
overlap with the quantitative and clinical research skills required. Domain, \
methodology, and career trajectory are all misaligned.", "should_apply": false}

### Example 4 — Active industry trainee with research background
CV: PhD in Biochemistry (grade A), B.Sc. Pharmacy, 3-year postdoc as R&D \
Project Lead (neurodegeneration, Alzheimer's), SOP authoring, method \
validation, supervised 3 team members, MS Project, 14 publications. \
Currently in full-time pharma industry training (GxP, ICH-GCP, regulatory \
submissions, medical project management) since Dec 2025.
Job: Clinical Trial Manager (Associate) – Permanent, Homeworking, \
Experienced (non-manager).

Analysis:
  A. Skills: GCP training in progress + SOP writing + project coordination + \
     method validation all map directly; no site monitoring experience yet but \
     training covers clinical trial workflows — score 72
  B. Seniority: 9+ years total experience, supervised direct reports, managed \
     timelines across multiple concurrent projects — matches Experienced level \
     — score 75
  C. Domain: Pharma/biochemistry background with active clinical trial \
     coursework; CNS/neurodegeneration postdoc relevant for many CROs — score 70
  D. Progression: Associate CTM is the logical first industry step; clear \
     upward trajectory — score 80
Output: {"score": 74, "score_reasoning": "Combines strong academic project \
management with active GxP/GCP training, making this a credible application \
rather than a stretch. SOP authoring, method validation, and multi-team \
coordination transfer directly. GCP certification-in-progress addresses the \
main gap. A competitive application for an Associate CTM role.", \
"should_apply": true}\
"""


def _build_preferences_block(
    preferences: dict | None,
    tier_1_threshold: int,
    tier_2_threshold: int,
) -> str:
    if not preferences:
        return ""

    tier_1 = preferences.get("tier_1_definitely", [])
    tier_2 = preferences.get("tier_2_would_work", [])

    tier_1_text = "\n".join(f"  - {r}" for r in tier_1) or "  (none specified)"
    tier_2_text = "\n".join(f"  - {r}" for r in tier_2) or "  (none specified)"

    return f"""\
## Candidate Job Preferences

The candidate has ranked the following role types by preference:

TIER 1 — Definitely wants (set should_apply = true if score >= {tier_1_threshold}):
{tier_1_text}

TIER 2 — Would work in (set should_apply = true if score >= {tier_2_threshold}):
{tier_2_text}

TIER 3 — Would NOT do (never recommend applying):
  - Quality Assurance / Quality Operations
  - Regulatory Affairs (any function)
  - Medical Science Liaison (MSL)
  - Commercial / Field Sales roles
  (Note: Tier 3 jobs are filtered before reaching the LLM. If this job appears
  to be a Tier 3 role that slipped through, set should_apply = false.)

Step 1: Classify this job into TIER 1, TIER 2, or TIER 3 based on the role \
type above.
Step 2: Set should_apply = true only if:
  - Classified as TIER 1 AND score >= {tier_1_threshold}, OR
  - Classified as TIER 2 AND score >= {tier_2_threshold}
  - If the job does not clearly fit any tier, use TIER 2 as the default.\
"""


def _build_user_prompt(
    job: dict,
    cv_text: str,
    threshold: int,
    tier_1_threshold: int | None = None,
    preferences: dict | None = None,
) -> str:
    job_details = (job.get("job_details") or "")[:2000]

    # Build the preference tier block (empty string if no preferences)
    tier_2_threshold = threshold
    effective_tier_1 = tier_1_threshold if tier_1_threshold is not None else threshold
    preferences_block = _build_preferences_block(preferences, effective_tier_1, tier_2_threshold)

    return f"""\
## Candidate CV
{cv_text}

{CANDIDATE_CONTEXT}

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
     High (80–100): CV directly demonstrates OR has clear academic equivalents
                    for the required skills (e.g. SOP writing = GCP documentation;
                    method validation = QC/analytical validation; project lead = PM)
     Mid  (40–79):  Relevant transferable skills present; some upskilling required
     Low  (0–39):   Core required skills absent and no credible transferable path

  B. Seniority fit (20%)
     High: Role level matches candidate's years of experience and current title
     Mid:  One level above or below; stretch or slight step-down
     Low:  Significant mismatch (e.g. Director role for an early-career candidate)

  C. Domain / therapeutic area relevance (20%)
     High: Same therapeutic area or functional domain; OR pharma-adjacent
           academic background with active industry training in the target area
     Mid:  Adjacent domain (e.g. oncology → rare disease; academic R&D → CRO)
     Low:  Unrelated domain with no overlap

  D. Career progression value (20%)
     High: Clear step forward in responsibility, scope, or industry experience
     Mid:  Lateral move with some new exposure
     Low:  Step backwards or no development value

After your per-dimension analysis, compute the weighted score.

{preferences_block if preferences_block else f"Set should_apply = true if final score >= {threshold}."}

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

def evaluate(
    job: dict,
    cv_text: str,
    model: str,
    threshold: int,
    tier_1_threshold: int | None = None,
    preferences: dict | None = None,
) -> EvalResult:
    """
    Evaluate a single job against the CV using the LLM.

    Args:
        job:               DB row dict with at minimum job_id, title, employer, etc.
        cv_text:           Plain text CV content.
        model:             OpenAI model name (e.g. "gpt-5-mini").
        threshold:         Minimum score for should_apply = True (Tier 2 / fallback).
        tier_1_threshold:  Lower threshold for Tier 1 "definitely want" roles.
                           Defaults to threshold if not provided.
        preferences:       job_preferences dict from requirements.yaml, used to
                           inject tier lists into the prompt.

    Returns:
        EvalResult dataclass with score, reasoning, flag, and token counts.
    """
    user_prompt = _build_user_prompt(
        job, cv_text, threshold, tier_1_threshold, preferences
    )

    logger.debug("Calling %s for job %s …", model, job.get("job_id"))
    raw = _call_api(model, SYSTEM_PROMPT, user_prompt)

    parsed = json.loads(raw["content"])

    score = max(0, min(100, int(parsed["score"])))

    return EvalResult(
        score=score,
        score_reasoning=parsed["score_reasoning"],
        should_apply=bool(parsed["should_apply"]),
        tokens_input=raw["tokens_input"],
        tokens_output=raw["tokens_output"],
        tokens_total=raw["tokens_total"],
    )
