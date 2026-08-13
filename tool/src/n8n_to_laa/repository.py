from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .converter import ConversionOptions, convert_workflow
from .sanitization import sanitize_secrets


def write_conversion(
    workflow: dict[str, Any],
    output_root: Path,
    source_path: Path | None = None,
    options: ConversionOptions | None = None,
) -> tuple[Path, dict[str, Any]]:
    result = convert_workflow(workflow, options)
    template_id = result.template["metadata"]["id"]
    target = output_root / "templates" / template_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "manifest.json").write_text(
        json.dumps(result.template, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (target / "conversion-report.json").write_text(
        json.dumps(result.report(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (target / "source.n8n.json").write_text(
        json.dumps(sanitize_secrets(workflow), indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    attribution = {
        "sourcePath": str(source_path) if source_path else None,
        "sourcePlatform": "n8n",
        "sourceWorkflowId": workflow.get("id"),
        "sourceWorkflowName": workflow.get("name"),
        "originalAuthor": workflow.get("author"),
        "license": workflow.get("license"),
    }
    (target / "attribution.json").write_text(
        json.dumps(attribution, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return target, result.report()


def update_manifest(output_root: Path) -> Path:
    templates_root = output_root / "templates"
    templates_root.mkdir(parents=True, exist_ok=True)
    template_ids = sorted(
        child.name
        for child in templates_root.iterdir()
        if child.is_dir() and (child / "manifest.json").exists()
    )
    manifest = templates_root / "manifest.json"
    manifest.write_text(
        json.dumps(template_ids, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest
