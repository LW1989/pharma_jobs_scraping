"""
Rule-based pre-screener.

Checks structured job columns against requirements.yaml filters.
No HTTP requests, no LLM calls — pure in-memory logic.

Public API:
    prescreen(job, filters) -> tuple[bool, str]
        Returns (passed, reason_string).
"""


def prescreen(job: dict, filters: dict) -> tuple[bool, str]:
    """
    Apply all configured filters to a job dict (as returned from the DB).

    Returns:
        (True,  "Passed pre-screening")          if all filters pass
        (False, "Pre-screened out: <reason>")    if any filter fails
    """
    # --- Contract type ---
    allowed_contract_types = filters.get("contract_types", [])
    if allowed_contract_types:
        value = (job.get("contract_type") or "").strip()
        if value not in allowed_contract_types:
            return False, (
                f"Pre-screened out: contract type '{value}' "
                f"not in {allowed_contract_types}"
            )

    # --- Hours ---
    allowed_hours = filters.get("hours", [])
    if allowed_hours:
        value = (job.get("hours") or "").strip()
        if value not in allowed_hours:
            return False, (
                f"Pre-screened out: hours '{value}' not in {allowed_hours}"
            )

    # --- Location keywords (OR logic — any match passes) ---
    location_keywords = filters.get("location_keywords", [])
    if location_keywords:
        location = (job.get("location") or "").lower()
        if not any(kw.lower() in location for kw in location_keywords):
            return False, (
                f"Pre-screened out: location '{job.get('location')}' "
                f"does not contain any of {location_keywords}"
            )

    # --- Experience level ---
    allowed_experience = filters.get("experience_levels", [])
    if allowed_experience:
        value = (job.get("experience_level") or "").strip()
        if value not in allowed_experience:
            return False, (
                f"Pre-screened out: experience level '{value}' "
                f"not in {allowed_experience}"
            )

    # --- Excluded title keywords ---
    exclude_keywords = filters.get("exclude_title_keywords", [])
    if exclude_keywords:
        title = (job.get("title") or "").lower()
        for kw in exclude_keywords:
            if kw.lower() in title:
                return False, (
                    f"Pre-screened out: title contains excluded keyword '{kw}'"
                )

    return True, "Passed pre-screening"
