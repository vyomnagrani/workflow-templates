from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NodeMapping:
    converter: str
    trigger: bool = False
    confidence: str = "exact"
    connector: str | None = None


NODE_MAPPINGS: dict[str, NodeMapping] = {
    "n8n-nodes-base.stickyNote": NodeMapping("metadata"),
    "n8n-nodes-base.manualTrigger": NodeMapping("request_trigger", trigger=True),
    "n8n-nodes-base.webhook": NodeMapping("request_trigger", trigger=True),
    "n8n-nodes-base.scheduleTrigger": NodeMapping("recurrence_trigger", trigger=True),
    "n8n-nodes-base.cron": NodeMapping("recurrence_trigger", trigger=True),
    "n8n-nodes-base.httpRequest": NodeMapping("http"),
    "n8n-nodes-base.code": NodeMapping(
        "javascript_code", confidence="review"
    ),
    "n8n-nodes-base.googleSheets": NodeMapping(
        "connector", connector="google_sheets"
    ),
    "n8n-nodes-base.gmail": NodeMapping("connector", connector="gmail"),
    "n8n-nodes-base.telegram": NodeMapping(
        "connector", connector="telegram"
    ),
    "n8n-nodes-base.set": NodeMapping("compose"),
    "n8n-nodes-base.editFields": NodeMapping("compose"),
    "n8n-nodes-base.if": NodeMapping("if"),
    "n8n-nodes-base.switch": NodeMapping("switch"),
    "n8n-nodes-base.wait": NodeMapping("wait"),
    "n8n-nodes-base.noOp": NodeMapping("compose"),
    "n8n-nodes-base.respondToWebhook": NodeMapping("response"),
}


def get_mapping(node_type: str) -> NodeMapping | None:
    return NODE_MAPPINGS.get(node_type)


def supported_node_types() -> list[str]:
    return sorted(NODE_MAPPINGS)
