from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .analyzer import analyze_workflow, inventory_workflows
from .catalog import download_catalog
from .converter import ConversionOptions, convert_workflow
from .mappings import supported_node_types
from .repository import update_manifest, write_conversion
from .validator import validate_template


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read JSON from {path}: {exc}") from exc


def _write_json(value: Any, path: Path | None) -> None:
    content = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    else:
        sys.stdout.write(content)


def _workflow_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(
        path
        for path in root.rglob("*.json")
        if path.name not in {
            "manifest.json",
            "conversion-report.json",
            "attribution.json",
        }
        and not path.name.endswith(".laa.json")
    )


def _options(args: argparse.Namespace) -> ConversionOptions:
    return ConversionOptions(
        mode=args.mode,
        author=args.author,
        source=args.source,
        category=args.category,
    )


def command_analyze(args: argparse.Namespace) -> int:
    workflow = _load_json(Path(args.input))
    analysis = analyze_workflow(workflow)
    _write_json(analysis.to_dict(), Path(args.output) if args.output else None)
    return 1 if any(item.severity == "error" for item in analysis.diagnostics) else 0


def command_convert(args: argparse.Namespace) -> int:
    workflow = _load_json(Path(args.input))
    result = convert_workflow(workflow, _options(args))
    output = Path(args.output) if args.output else None
    _write_json(result.template, output)
    report_path = (
        Path(args.report)
        if args.report
        else output.with_suffix(".report.json")
        if output
        else None
    )
    if report_path:
        _write_json(result.report(), report_path)
    if args.summary:
        print(
            f"{result.quality_grade}: converted {result.converted_nodes}/"
            f"{result.total_nodes} nodes; {len(result.diagnostics)} diagnostics",
            file=sys.stderr,
        )
    return 0 if result.success else 2


def command_batch(args: argparse.Namespace) -> int:
    source_root = Path(args.input)
    output_root = Path(args.output)
    files = _workflow_files(source_root)
    reports: list[dict[str, Any]] = []
    failures = 0
    for path in files:
        try:
            workflow = _load_json(path)
            if not isinstance(workflow, dict) or not isinstance(
                workflow.get("nodes"), list
            ):
                continue
            target, report = write_conversion(
                workflow, output_root, path, _options(args)
            )
            reports.append({"source": str(path), "target": str(target), **report})
            if not report["success"]:
                failures += 1
        except ValueError as exc:
            failures += 1
            reports.append({"source": str(path), "success": False, "error": str(exc)})
    update_manifest(output_root)
    _write_json(
        {
            "input": str(source_root),
            "output": str(output_root),
            "filesExamined": len(files),
            "templatesWritten": len(reports),
            "failures": failures,
            "results": reports,
        },
        output_root / "batch-report.json",
    )
    print(
        f"Wrote {len(reports)} template folders to {output_root}; "
        f"{failures} conversion failures.",
        file=sys.stderr,
    )
    return 2 if failures else 0


def command_inventory(args: argparse.Namespace) -> int:
    workflows: list[tuple[str, dict[str, Any]]] = []
    for path in _workflow_files(Path(args.input)):
        value = _load_json(path)
        if isinstance(value, dict) and isinstance(value.get("nodes"), list):
            workflows.append((str(path), value))
    result = inventory_workflows(workflows)
    _write_json(result, Path(args.output) if args.output else None)
    return 0


def command_validate(args: argparse.Namespace) -> int:
    template = _load_json(Path(args.input))
    diagnostics = validate_template(template)
    result = {
        "valid": not any(item.severity == "error" for item in diagnostics),
        "diagnostics": [item.to_dict() for item in diagnostics],
    }
    _write_json(result, Path(args.output) if args.output else None)
    return 0 if result["valid"] else 2


def command_supported(_: argparse.Namespace) -> int:
    _write_json({"supportedNodeTypes": supported_node_types()}, None)
    return 0


def command_download_catalog(args: argparse.Namespace) -> int:
    result = download_catalog(
        Path(args.output),
        limit=args.limit,
        rows=args.rows,
        search=args.search,
        category=args.category,
        delay_seconds=args.delay,
        overwrite=args.overwrite,
    )
    print(
        f"Downloaded {result.downloaded}, skipped {result.skipped}, "
        f"failed {result.failed}.",
        file=sys.stderr,
    )
    return 2 if result.failed else 0


def _conversion_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--mode",
        choices=("strict", "best-effort"),
        default="best-effort",
    )
    parser.add_argument("--author", default="Converted from n8n")
    parser.add_argument("--source", default="community")
    parser.add_argument("--category", default="automation")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="n8n-to-laa",
        description="Convert n8n workflows to Azure Logic Apps Automation templates.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Analyze one n8n workflow.")
    analyze.add_argument("input")
    analyze.add_argument("--output")
    analyze.set_defaults(handler=command_analyze)

    convert = subparsers.add_parser("convert", help="Convert one n8n workflow.")
    convert.add_argument("input")
    convert.add_argument("--output", "-o")
    convert.add_argument("--report")
    convert.add_argument("--summary", action="store_true")
    _conversion_arguments(convert)
    convert.set_defaults(handler=command_convert)

    batch = subparsers.add_parser(
        "batch", help="Convert all workflow JSON files under a directory."
    )
    batch.add_argument("input")
    batch.add_argument("--output", "-o", required=True)
    _conversion_arguments(batch)
    batch.set_defaults(handler=command_batch)

    inventory = subparsers.add_parser(
        "inventory", help="Inventory node usage across a template corpus."
    )
    inventory.add_argument("input")
    inventory.add_argument("--output", "-o")
    inventory.set_defaults(handler=command_inventory)

    validate = subparsers.add_parser(
        "validate", help="Validate an AutoTemplate v1 envelope."
    )
    validate.add_argument("input")
    validate.add_argument("--output", "-o")
    validate.set_defaults(handler=command_validate)

    supported = subparsers.add_parser(
        "supported", help="List currently supported n8n node types."
    )
    supported.set_defaults(handler=command_supported)

    catalog = subparsers.add_parser(
        "download-catalog",
        help="Download normalized workflows from the public n8n template catalog.",
    )
    catalog.add_argument("--output", "-o", required=True)
    catalog.add_argument("--limit", type=int)
    catalog.add_argument("--rows", type=int, default=100)
    catalog.add_argument("--search")
    catalog.add_argument("--category")
    catalog.add_argument("--delay", type=float, default=0.05)
    catalog.add_argument("--overwrite", action="store_true")
    catalog.set_defaults(handler=command_download_catalog)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        raise SystemExit(args.handler(args))
    except ValueError as exc:
        parser.error(str(exc))
