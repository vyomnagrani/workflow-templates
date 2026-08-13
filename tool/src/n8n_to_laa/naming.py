from __future__ import annotations

import re


def action_name(value: str) -> str:
    """Return a WDL-safe, readable operation name."""
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = "Action"
    if cleaned[0].isdigit():
        cleaned = f"Action_{cleaned}"
    return cleaned


def template_id(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return cleaned or "converted-workflow"


def unique_name(preferred: str, used: set[str]) -> str:
    candidate = action_name(preferred)
    if candidate not in used:
        used.add(candidate)
        return candidate
    index = 2
    while f"{candidate}_{index}" in used:
        index += 1
    result = f"{candidate}_{index}"
    used.add(result)
    return result
