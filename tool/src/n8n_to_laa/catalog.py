from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

DEFAULT_BASE_URL = "https://api.n8n.io"


@dataclass(slots=True)
class CatalogDownloadResult:
    downloaded: int
    skipped: int
    failed: int
    files: list[str]
    errors: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "downloaded": self.downloaded,
            "skipped": self.skipped,
            "failed": self.failed,
            "files": self.files,
            "errors": self.errors,
        }


def _get_json(url: str, timeout: float = 30) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "n8n-to-laa/0.1 (+template-conversion-research)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to fetch {url}: {exc}") from exc


def _author(template: dict[str, Any]) -> str | None:
    user = template.get("user")
    if not isinstance(user, dict):
        return None
    return str(user.get("name") or user.get("username") or "") or None


def _category_names(template: dict[str, Any]) -> list[str]:
    categories = template.get("categories") or []
    return [
        str(item.get("name") if isinstance(item, dict) else item)
        for item in categories
        if item
    ]


def _normalize_template(payload: dict[str, Any], template_id: int) -> dict[str, Any]:
    template = payload.get("workflow", payload)
    if not isinstance(template, dict):
        raise ValueError(f"Template {template_id} returned an invalid object.")
    workflow = template.get("workflow")
    if not isinstance(workflow, dict):
        raise ValueError(f"Template {template_id} has no workflow definition.")
    result = dict(workflow)
    result["id"] = str(template.get("id", template_id))
    result["name"] = template.get("name") or result.get("name") or f"Template {template_id}"
    result["description"] = template.get("description") or result.get("description")
    result["author"] = _author(template)
    result["sourceUrl"] = f"https://n8n.io/workflows/{template_id}/"
    result["sourceMetadata"] = {
        "templateId": template.get("id", template_id),
        "createdAt": template.get("createdAt"),
        "totalViews": template.get("totalViews", template.get("views")),
        "recentViews": template.get("recentViews"),
        "status": template.get("status"),
        "reviewStatus": template.get("reviewStatus"),
        "categories": _category_names(template),
        "author": template.get("user"),
        "workflowInfo": template.get("workflowInfo"),
    }
    if _category_names(template):
        result["tags"] = _category_names(template)
    return result


def download_catalog(
    destination: Path,
    *,
    limit: int | None = None,
    rows: int = 100,
    search: str | None = None,
    category: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    delay_seconds: float = 0.05,
    overwrite: bool = False,
    fetcher: Callable[[str], dict[str, Any]] = _get_json,
) -> CatalogDownloadResult:
    destination.mkdir(parents=True, exist_ok=True)
    page = 1
    downloaded = skipped = failed = 0
    files: list[str] = []
    errors: list[dict[str, Any]] = []
    examined = 0

    while limit is None or examined < limit:
        query: dict[str, Any] = {"page": page, "rows": rows}
        if search:
            query["search"] = search
        if category:
            query["category"] = category
        search_url = (
            f"{base_url.rstrip('/')}/templates/search?"
            f"{urllib.parse.urlencode(query)}"
        )
        search_payload = fetcher(search_url)
        workflows = search_payload.get("workflows", [])
        if not isinstance(workflows, list) or not workflows:
            break

        for item in workflows:
            if limit is not None and examined >= limit:
                break
            examined += 1
            if not isinstance(item, dict) or "id" not in item:
                failed += 1
                errors.append({"page": page, "error": "Search item has no ID."})
                continue
            template_id = int(item["id"])
            target = destination / f"{template_id}.json"
            if target.exists() and not overwrite:
                skipped += 1
                files.append(str(target))
                continue
            try:
                payload = fetcher(
                    f"{base_url.rstrip('/')}/templates/workflows/{template_id}"
                )
                workflow = _normalize_template(payload, template_id)
                target.write_text(
                    json.dumps(workflow, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                downloaded += 1
                files.append(str(target))
            except (ValueError, OSError) as exc:
                failed += 1
                errors.append({"templateId": template_id, "error": str(exc)})
            if delay_seconds:
                time.sleep(delay_seconds)

        total = search_payload.get("totalWorkflows")
        if isinstance(total, int) and page * rows >= total:
            break
        page += 1

    result = CatalogDownloadResult(downloaded, skipped, failed, files, errors)
    (destination / "download-report.json").write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return result

