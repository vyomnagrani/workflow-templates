from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(authorization|api[-_]?key|apikey|access[-_]?token|client[-_]?secret|"
    r"password|secret|token)$",
    re.IGNORECASE,
)
_SAFE_VALUE = re.compile(
    r"(^[=@]|{{|\$\(|replace|placeholder|your[_ -]|example|dummy|test|"
    r"^\*+$|^x+$|^<.*>$|^[A-Z0-9_ -]*(API_KEY|TOKEN|SECRET|AUTH)$)",
    re.IGNORECASE,
)


def _is_sensitive_name(value: Any) -> bool:
    return isinstance(value, str) and bool(_SENSITIVE_KEY.search(value))


def _sanitize_literal(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    if _SAFE_VALUE.search(value):
        return value
    return "REPLACE_WITH_SECRET"


def sanitize_secrets(value: Any) -> Any:
    result = deepcopy(value)

    def visit(item: Any) -> Any:
        if isinstance(item, list):
            return [visit(child) for child in item]
        if not isinstance(item, dict):
            return item

        sanitized = {key: visit(child) for key, child in item.items()}
        for key, child in list(sanitized.items()):
            if _is_sensitive_name(str(key)):
                sanitized[key] = _sanitize_literal(child)

        header_name = sanitized.get("name")
        if _is_sensitive_name(header_name) and "value" in sanitized:
            sanitized["value"] = _sanitize_literal(sanitized["value"])
        return sanitized

    return visit(result)
