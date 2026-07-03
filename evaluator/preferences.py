"""
Helpers for formatting job_preferences from requirements.yaml.

Tier entries may be plain strings (legacy) or dicts with name + title_keywords.
"""


def format_tier_entry(entry: str | dict) -> str:
    """Format one tier-1 or tier-2 entry for injection into LLM prompts."""
    if isinstance(entry, str):
        return f"  - {entry}"

    name = entry.get("name", "Unnamed role")
    keywords = entry.get("title_keywords", [])
    if keywords:
        kw_text = ", ".join(keywords)
        return f"  - {name}\n    Title hints: {kw_text}"
    return f"  - {name}"


def format_tier_list(entries: list) -> str:
    """Format a tier_1_definitely or tier_2_would_work list for prompts."""
    if not entries:
        return "  (none specified)"
    return "\n".join(format_tier_entry(e) for e in entries)


def format_tier_3_summary(entries: list) -> str:
    """Format tier_3_exclude names for the LLM preferences block."""
    if not entries:
        return "  (none specified)"
    lines = []
    for entry in entries:
        if isinstance(entry, str):
            lines.append(f"  - {entry}")
        else:
            lines.append(f"  - {entry.get('name', 'Unnamed role')}")
    return "\n".join(lines)
