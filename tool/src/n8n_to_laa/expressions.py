from __future__ import annotations

import re
from typing import Any

from .models import Diagnostic
from .naming import action_name

_NODE_REFERENCE = re.compile(
    r"""\$\(['"](?P<node>[^'"]+)['"]\)\.(?:(?P<selector>item|first\(\)|last\(\))\.)?json(?P<path>(?:\.[A-Za-z_][\w]*|\[['"][^'"]+['"]\])*)"""
)
_JSON_REFERENCE = re.compile(
    r"""\$json(?P<path>(?:\.[A-Za-z_][\w]*|\[['"][^'"]+['"]\])*)"""
)
_INPUT_REFERENCE = re.compile(
    r"""\$input\.(?:(?P<selector>item|first\(\)|last\(\))\.)?json(?P<path>(?:\.[A-Za-z_][\w]*|\[['"][^'"]+['"]\])*)"""
)
_INTERPOLATION = re.compile(r"\{\{\s*(?P<expression>.*?)\s*\}\}")


def _wdl_path(path: str) -> str:
    if not path:
        return ""
    parts: list[str] = []
    for dot_name, bracket_name in re.findall(
        r"\.([A-Za-z_][\w]*)|\[['\"]([^'\"]+)['\"]\]", path
    ):
        parts.append(dot_name or bracket_name)
    return "".join(f"?['{part}']" for part in parts)


def _select_body(source: str, selector: str | None) -> str:
    if selector == "first()":
        return f"first({source})"
    if selector == "last()":
        return f"last({source})"
    return source


def translate_value(
    value: Any,
    previous_action: str | None,
    diagnostics: list[Diagnostic],
    node_name: str,
) -> Any:
    if isinstance(value, dict):
        return {
            key: translate_value(item, previous_action, diagnostics, node_name)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            translate_value(item, previous_action, diagnostics, node_name)
            for item in value
        ]
    if not isinstance(value, str):
        return value

    expression = value
    if expression.startswith("={{") and expression.endswith("}}"):
        expression = expression[3:-2].strip()
    elif expression.startswith("={{") and expression.endswith(" }"):
        expression = expression[3:-2].strip()
    elif not expression.startswith("="):
        return value
    elif expression.startswith("="):
        expression = expression[1:].strip()

    if "{{" in expression and "}}" in expression:
        unresolved = False

        def replace_interpolation(match: re.Match[str]) -> str:
            nonlocal unresolved
            translated = translate_value(
                f"={match.group('expression')}",
                previous_action,
                diagnostics,
                node_name,
            )
            if isinstance(translated, str) and translated.startswith("@"):
                return f"@{{{translated[1:]}}}"
            unresolved = True
            return match.group(0)

        rendered = _INTERPOLATION.sub(replace_interpolation, expression)
        if not unresolved:
            return rendered

    node_match = _NODE_REFERENCE.fullmatch(expression)
    if node_match:
        source = _select_body(
            f"body('{action_name(node_match.group('node'))}')",
            node_match.group("selector"),
        )
        return (
            f"@{source}"
            f"{_wdl_path(node_match.group('path'))}"
        )

    input_match = _INPUT_REFERENCE.fullmatch(expression)
    if input_match:
        source = (
            f"body('{previous_action}')"
            if previous_action
            else "triggerBody()"
        )
        source = _select_body(source, input_match.group("selector"))
        return f"@{source}{_wdl_path(input_match.group('path'))}"

    json_match = _JSON_REFERENCE.fullmatch(expression)
    if json_match:
        source = (
            f"body('{previous_action}')"
            if previous_action
            else "triggerBody()"
        )
        return f"@{source}{_wdl_path(json_match.group('path'))}"

    replacements = expression
    replacements = _NODE_REFERENCE.sub(
        lambda match: (
            f"{_select_body(f"body('{action_name(match.group('node'))}')", match.group('selector'))}"
            f"{_wdl_path(match.group('path'))}"
        ),
        replacements,
    )
    replacements = _INPUT_REFERENCE.sub(
        lambda match: (
            f"{_select_body(f'body({previous_action!r})' if previous_action else 'triggerBody()', match.group('selector'))}"
            f"{_wdl_path(match.group('path'))}"
        ),
        replacements,
    )
    replacements = _JSON_REFERENCE.sub(
        lambda match: (
            f"{f'body({previous_action!r})' if previous_action else 'triggerBody()'}"
            f"{_wdl_path(match.group('path'))}"
        ),
        replacements,
    )

    if "$" in replacements or replacements == expression:
        diagnostics.append(
            Diagnostic(
                "EXPRESSION_PARTIAL",
                f"Expression could not be translated safely: {value}",
                "warning",
                node_name=node_name,
            )
        )
        return value

    return f"@{replacements}"
