from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .analyzer import analyze_workflow
from .connectors import ConnectorSpec, get_connector
from .expressions import translate_value
from .mappings import get_mapping
from .models import ConversionMode, ConversionResult, Diagnostic
from .naming import action_name, template_id, unique_name
from .sanitization import sanitize_secrets

WDL_SCHEMA = (
    "https://schema.management.azure.com/providers/Microsoft.Logic/"
    "schemas/2016-06-01/workflowdefinition.json#"
)


@dataclass(slots=True)
class ConversionOptions:
    mode: ConversionMode = "best-effort"
    author: str = "Converted from n8n"
    source: str = "community"
    category: str = "automation"
    include_source_metadata: bool = True


def _incoming_edges(
    workflow: dict[str, Any],
) -> dict[str, list[tuple[str, int, str, int]]]:
    incoming: dict[str, list[tuple[str, int, str, int]]] = {}
    for source, output_types in workflow.get("connections", {}).items():
        if not isinstance(output_types, dict):
            continue
        for connection_type, output_groups in output_types.items():
            if not isinstance(output_groups, list):
                continue
            for output_index, destinations in enumerate(output_groups):
                if not isinstance(destinations, list):
                    continue
                for destination in destinations:
                    if not isinstance(destination, dict) or "node" not in destination:
                        continue
                    incoming.setdefault(str(destination["node"]), []).append(
                        (
                            str(source),
                            output_index,
                            str(connection_type),
                            int(destination.get("index", 0)),
                        )
                    )
    return incoming


def _request_trigger(node: dict[str, Any]) -> dict[str, Any]:
    parameters = node.get("parameters") or {}
    result: dict[str, Any] = {"type": "Request", "kind": "Http"}
    inputs: dict[str, Any] = {}
    method = parameters.get("httpMethod") or parameters.get("method")
    if method:
        inputs["method"] = str(method).upper()
    if isinstance(parameters.get("options"), dict):
        schema = parameters["options"].get("rawBody")
        if isinstance(schema, dict):
            inputs["schema"] = schema
    if inputs:
        result["inputs"] = inputs
    return result


def _recurrence_trigger(node: dict[str, Any]) -> dict[str, Any]:
    parameters = node.get("parameters") or {}
    rule = parameters.get("rule") or {}
    intervals = rule.get("interval") if isinstance(rule, dict) else None
    interval = intervals[0] if isinstance(intervals, list) and intervals else {}
    field = str(interval.get("field", "minutes"))
    field_map = {
        "seconds": "Second",
        "minutes": "Minute",
        "hours": "Hour",
        "days": "Day",
        "weeks": "Week",
        "months": "Month",
    }
    frequency = field_map.get(field, "Minute")
    value = interval.get(
        f"{field[:-1]}Interval" if field.endswith("s") else "interval", 1
    )
    return {
        "type": "Recurrence",
        "recurrence": {"frequency": frequency, "interval": int(value or 1)},
    }


def _header_values(parameters: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    source = parameters.get("headerParameters") or parameters.get("headers")
    if isinstance(source, dict):
        source = source.get("parameters") or source.get("values") or source
    if isinstance(source, list):
        for item in source:
            if isinstance(item, dict) and item.get("name"):
                result[str(item["name"])] = item.get("value", "")
    elif isinstance(source, dict):
        result.update(source)
    return result


def _http_action(
    node: dict[str, Any],
    previous_action: str | None,
    diagnostics: list[Diagnostic],
) -> dict[str, Any]:
    parameters = node.get("parameters") or {}
    name = str(node.get("name", "HTTP"))
    inputs: dict[str, Any] = {
        "method": str(parameters.get("method", "GET")).upper(),
        "uri": translate_value(
            parameters.get("url", ""),
            previous_action,
            diagnostics,
            name,
        ),
    }
    headers = _header_values(parameters)
    if headers:
        inputs["headers"] = translate_value(
            headers, previous_action, diagnostics, name
        )

    body = parameters.get("jsonBody")
    if body is None:
        body = parameters.get("body")
    if body is None and isinstance(parameters.get("bodyParameters"), dict):
        values = parameters["bodyParameters"].get("parameters", [])
        if isinstance(values, list):
            body = {
                str(item.get("name")): item.get("value")
                for item in values
                if isinstance(item, dict) and item.get("name")
            }
    if body is not None:
        inputs["body"] = translate_value(body, previous_action, diagnostics, name)

    options = parameters.get("options")
    if isinstance(options, dict) and options.get("timeout"):
        diagnostics.append(
            Diagnostic(
                "HTTP_TIMEOUT_REVIEW",
                "n8n HTTP timeout has no direct generic WDL input mapping and requires review.",
                "warning",
                node_name=name,
                node_type=str(node.get("type", "")),
            )
        )
    return {"type": "Http", "inputs": inputs}


def _compose_action(
    node: dict[str, Any],
    previous_action: str | None,
    diagnostics: list[Diagnostic],
) -> dict[str, Any]:
    parameters = node.get("parameters") or {}
    assignments = parameters.get("assignments")
    output: Any = {}
    if isinstance(assignments, dict):
        assignments = assignments.get("assignments", [])
    if isinstance(assignments, list):
        output = {
            str(item.get("name")): item.get("value")
            for item in assignments
            if isinstance(item, dict) and item.get("name")
        }
    elif isinstance(parameters.get("values"), dict):
        output = parameters["values"]
    elif parameters:
        output = parameters
    return {
        "type": "Compose",
        "inputs": translate_value(
            output,
            previous_action,
            diagnostics,
            str(node.get("name", "Compose")),
        ),
    }


def _response_action(
    node: dict[str, Any],
    previous_action: str | None,
    diagnostics: list[Diagnostic],
) -> dict[str, Any]:
    parameters = node.get("parameters") or {}
    return {
        "type": "Response",
        "kind": "Http",
        "inputs": {
            "statusCode": int(parameters.get("responseCode", 200)),
            "body": translate_value(
                parameters.get("responseBody", ""),
                previous_action,
                diagnostics,
                str(node.get("name", "Response")),
            ),
        },
    }


def _wait_action(node: dict[str, Any]) -> dict[str, Any]:
    parameters = node.get("parameters") or {}
    unit = str(parameters.get("unit", "seconds")).lower()
    units = {
        "seconds": "Second",
        "minutes": "Minute",
        "hours": "Hour",
        "days": "Day",
        "weeks": "Week",
        "months": "Month",
    }
    return {
        "type": "Wait",
        "inputs": {
            "interval": {
                "count": parameters.get("amount", 1),
                "unit": units.get(unit, "Second"),
            }
        },
    }


def _condition_expression(
    node: dict[str, Any],
    previous_action: str | None,
    diagnostics: list[Diagnostic],
    mode: ConversionMode,
) -> dict[str, Any]:
    parameters = node.get("parameters") or {}
    conditions = parameters.get("conditions") or {}
    entries = conditions.get("conditions", []) if isinstance(conditions, dict) else []
    combinator = str(conditions.get("combinator", "and")).lower()
    expressions: list[dict[str, Any]] = []
    operators = {
        "equals": "equals",
        "notEquals": "not",
        "contains": "contains",
        "notContains": "not",
        "startsWith": "startsWith",
        "endsWith": "endsWith",
        "gt": "greater",
        "gte": "greaterOrEquals",
        "lt": "less",
        "lte": "lessOrEquals",
    }
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        operation = str((entry.get("operator") or {}).get("operation", "equals"))
        left = translate_value(
            entry.get("leftValue"),
            previous_action,
            diagnostics,
            str(node.get("name", "IF")),
        )
        right = translate_value(
            entry.get("rightValue"),
            previous_action,
            diagnostics,
            str(node.get("name", "IF")),
        )
        wdl_operator = operators.get(operation)
        if wdl_operator == "not":
            inner_operator = "equals" if operation == "notEquals" else "contains"
            expressions.append({"not": {inner_operator: [left, right]}})
        elif wdl_operator:
            expressions.append({wdl_operator: [left, right]})
        elif operation in {"exists", "notExists", "empty", "notEmpty"}:
            empty = {"empty": [left]}
            expressions.append(
                {"not": empty} if operation in {"exists", "notEmpty"} else empty
            )
        elif operation in {"true", "false"}:
            comparison = {"equals": [left, operation == "true"]}
            expressions.append(comparison)
        else:
            diagnostics.append(
                Diagnostic(
                    "IF_OPERATOR_UNSUPPORTED",
                    f"IF operator '{operation}' requires manual review.",
                    "error" if mode == "strict" else "warning",
                    node_name=str(node.get("name", "IF")),
                    node_type=str(node.get("type", "")),
                )
            )
    if not expressions:
        diagnostics.append(
            Diagnostic(
                "IF_CONDITION_MISSING",
                "IF node has no condition that can be translated.",
                "error" if mode == "strict" else "warning",
                node_name=str(node.get("name", "IF")),
                node_type=str(node.get("type", "")),
            )
        )
        return {"equals": [1, 1]}
    if len(expressions) == 1:
        return expressions[0]
    return {combinator if combinator in {"and", "or"} else "and": expressions}


def _placeholder_action(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "Compose",
        "inputs": {
            "conversionStatus": "unsupported",
            "n8nNodeName": node.get("name"),
            "n8nNodeType": node.get("type"),
            "message": "Replace this placeholder with an equivalent LAA action.",
        },
        "description": "Generated placeholder for an unsupported n8n node.",
        "metadata": {"x-n8n-original-parameters": node.get("parameters", {})},
    }


def _main_outgoing(
    workflow: dict[str, Any],
) -> dict[str, dict[int, list[str]]]:
    outgoing: dict[str, dict[int, list[str]]] = {}
    for source, output_types in workflow.get("connections", {}).items():
        groups = output_types.get("main", []) if isinstance(output_types, dict) else []
        if not isinstance(groups, list):
            continue
        for output_index, destinations in enumerate(groups):
            if not isinstance(destinations, list):
                continue
            for destination in destinations:
                if isinstance(destination, dict) and destination.get("node"):
                    outgoing.setdefault(str(source), {}).setdefault(
                        output_index, []
                    ).append(str(destination["node"]))
    return outgoing


def _distances(
    starts: list[str],
    outgoing: dict[str, dict[int, list[str]]],
) -> dict[str, int]:
    distances: dict[str, int] = {}
    queue = [(item, 0) for item in starts]
    while queue:
        current, distance = queue.pop(0)
        if current in distances and distances[current] <= distance:
            continue
        distances[current] = distance
        for destinations in outgoing.get(current, {}).values():
            queue.extend((item, distance + 1) for item in destinations)
    return distances


def _nodes_before(
    starts: list[str],
    stop: str | None,
    outgoing: dict[str, dict[int, list[str]]],
) -> set[str]:
    result: set[str] = set()
    pending = list(starts)
    while pending:
        current = pending.pop()
        if current == stop or current in result:
            continue
        result.add(current)
        for destinations in outgoing.get(current, {}).values():
            pending.extend(destinations)
    return result


def _if_plans(
    workflow: dict[str, Any],
    nodes_by_name: dict[str, dict[str, Any]],
    incoming: dict[str, list[tuple[str, int, str, int]]],
    diagnostics: list[Diagnostic],
    mode: ConversionMode,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    outgoing = _main_outgoing(workflow)
    plans: dict[str, dict[str, Any]] = {}
    consumed_by: dict[str, str] = {}
    severity = "error" if mode == "strict" else "warning"
    for if_name, node in nodes_by_name.items():
        if str(node.get("type", "")) != "n8n-nodes-base.if":
            continue
        groups = outgoing.get(if_name, {})
        true_starts = groups.get(0, [])
        false_starts = groups.get(1, [])
        if len(true_starts) > 1 or len(false_starts) > 1 or any(
            index > 1 for index in groups
        ):
            diagnostics.append(
                Diagnostic(
                    "IF_BRANCH_SHAPE_UNSUPPORTED",
                    "IF conversion currently requires at most one destination per true/false output.",
                    severity,
                    node_name=if_name,
                    node_type=str(node.get("type", "")),
                )
            )
            continue
        true_distances = _distances(true_starts, outgoing)
        false_distances = _distances(false_starts, outgoing)
        common = set(true_distances) & set(false_distances)
        convergence = (
            min(
                common,
                key=lambda name: (
                    true_distances[name] + false_distances[name],
                    max(true_distances[name], false_distances[name]),
                ),
            )
            if common
            else None
        )
        true_nodes = _nodes_before(true_starts, convergence, outgoing)
        false_nodes = _nodes_before(false_starts, convergence, outgoing)
        branch_nodes = true_nodes | false_nodes
        invalid = bool(true_nodes & false_nodes)
        for branch_name in branch_nodes:
            for source, _, connection_type, input_index in incoming.get(
                branch_name, []
            ):
                if (
                    source not in branch_nodes
                    and source != if_name
                    and connection_type == "main"
                    and input_index == 0
                ):
                    invalid = True
        if invalid or any(name in consumed_by for name in branch_nodes):
            diagnostics.append(
                Diagnostic(
                    "IF_REGION_UNSUPPORTED",
                    "IF branches overlap or have external inputs, so nesting them would change behavior.",
                    severity,
                    node_name=if_name,
                    node_type=str(node.get("type", "")),
                )
            )
            continue
        plans[if_name] = {
            "true": true_nodes,
            "false": false_nodes,
            "convergence": convergence,
        }
        for branch_name in branch_nodes:
            consumed_by[branch_name] = if_name
    return plans, consumed_by


def _switch_plans(
    workflow: dict[str, Any],
    nodes_by_name: dict[str, dict[str, Any]],
    incoming: dict[str, list[tuple[str, int, str, int]]],
    existing_consumed: dict[str, str],
    diagnostics: list[Diagnostic],
    mode: ConversionMode,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    outgoing = _main_outgoing(workflow)
    plans: dict[str, dict[str, Any]] = {}
    consumed_by: dict[str, str] = {}
    severity = "error" if mode == "strict" else "warning"
    for switch_name, node in nodes_by_name.items():
        if str(node.get("type", "")) != "n8n-nodes-base.switch":
            continue
        if switch_name in existing_consumed:
            diagnostics.append(
                Diagnostic(
                    "SWITCH_NESTED_UNSUPPORTED",
                    "Switch nodes nested inside another compiled branch region require manual review.",
                    severity,
                    node_name=switch_name,
                    node_type=str(node.get("type", "")),
                )
            )
            continue
        parameters = node.get("parameters") or {}
        rules = (parameters.get("rules") or {}).get("values", [])
        if not isinstance(rules, list) or not rules:
            diagnostics.append(
                Diagnostic(
                    "SWITCH_RULES_MISSING",
                    "Switch node has no explicit rule definitions to translate.",
                    severity,
                    node_name=switch_name,
                    node_type=str(node.get("type", "")),
                )
            )
            continue
        groups = outgoing.get(switch_name, {})
        if any(len(destinations) > 1 for destinations in groups.values()):
            diagnostics.append(
                Diagnostic(
                    "SWITCH_BRANCH_SHAPE_UNSUPPORTED",
                    "Switch conversion currently requires at most one destination per output.",
                    severity,
                    node_name=switch_name,
                    node_type=str(node.get("type", "")),
                )
            )
            continue
        output_indexes = sorted(groups)
        if output_indexes and output_indexes[-1] > len(rules):
            diagnostics.append(
                Diagnostic(
                    "SWITCH_OUTPUT_UNSUPPORTED",
                    "Switch has more output groups than rules plus one fallback output.",
                    severity,
                    node_name=switch_name,
                    node_type=str(node.get("type", "")),
                )
            )
            continue
        starts_by_output = [
            groups.get(index, []) for index in range(len(rules) + 1)
        ]
        active_starts = [starts for starts in starts_by_output if starts]
        distance_maps = [_distances(starts, outgoing) for starts in active_starts]
        common = (
            set.intersection(*(set(distances) for distances in distance_maps))
            if len(distance_maps) > 1
            else set()
        )
        convergence = (
            min(
                common,
                key=lambda name: (
                    sum(distances[name] for distances in distance_maps),
                    max(distances[name] for distances in distance_maps),
                ),
            )
            if common
            else None
        )
        branch_sets = [
            _nodes_before(starts, convergence, outgoing)
            for starts in starts_by_output
        ]
        branch_nodes = set().union(*branch_sets)
        invalid = sum(len(items) for items in branch_sets) != len(branch_nodes)
        for branch_name in branch_nodes:
            for source, _, connection_type, input_index in incoming.get(
                branch_name, []
            ):
                if (
                    source not in branch_nodes
                    and source != switch_name
                    and connection_type == "main"
                    and input_index == 0
                ):
                    invalid = True
        if (
            invalid
            or any(name in existing_consumed for name in branch_nodes)
            or any(name in consumed_by for name in branch_nodes)
        ):
            diagnostics.append(
                Diagnostic(
                    "SWITCH_REGION_UNSUPPORTED",
                    "Switch branches overlap or have external inputs, so nesting them would change behavior.",
                    severity,
                    node_name=switch_name,
                    node_type=str(node.get("type", "")),
                )
            )
            continue
        plans[switch_name] = {
            "rules": rules,
            "branches": branch_sets[: len(rules)],
            "fallback": branch_sets[len(rules)],
            "convergence": convergence,
        }
        for branch_name in branch_nodes:
            consumed_by[branch_name] = switch_name
    return plans, consumed_by


def _resource_locator_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def _encoded_path_value(value: Any) -> str:
    if isinstance(value, str) and value.startswith("@"):
        expression = value[1:]
    else:
        escaped = str(value or "").replace("'", "''")
        expression = f"'{escaped}'"
    return f"@{{encodeURIComponent(encodeURIComponent({expression}))}}"


def _google_sheets_action(
    node: dict[str, Any],
    previous_action: str | None,
    diagnostics: list[Diagnostic],
    connector: ConnectorSpec,
) -> dict[str, Any] | None:
    parameters = node.get("parameters") or {}
    operation = str(parameters.get("operation", "append"))
    operation_map = {
        "append": ("post", "PostItem"),
        "read": ("get", "GetItems"),
    }
    operation_details = operation_map.get(operation)
    if not operation_details:
        diagnostics.append(
            Diagnostic(
                "CONNECTOR_OPERATION_UNSUPPORTED",
                f"Google Sheets operation '{operation}' is not safely equivalent to a single LAA connector action.",
                "warning",
                node_name=str(node.get("name", "Google Sheets")),
                node_type=str(node.get("type", "")),
            )
        )
        return None

    document_id = translate_value(
        _resource_locator_value(parameters.get("documentId")),
        previous_action,
        diagnostics,
        str(node.get("name", "Google Sheets")),
    )
    sheet_name = translate_value(
        _resource_locator_value(parameters.get("sheetName")),
        previous_action,
        diagnostics,
        str(node.get("name", "Google Sheets")),
    )
    if document_id in (None, "", "=") or sheet_name in (None, "", "="):
        diagnostics.append(
            Diagnostic(
                "CONNECTOR_RESOURCE_REQUIRED",
                "Google Sheets requires a spreadsheet and worksheet value before import.",
                "warning",
                node_name=str(node.get("name", "Google Sheets")),
                node_type=str(node.get("type", "")),
            )
        )
        if document_id in (None, "", "="):
            document_id = "REPLACE_WITH_SPREADSHEET_ID"
        if sheet_name in (None, "", "="):
            sheet_name = "REPLACE_WITH_WORKSHEET"
    method, swagger_operation = operation_details
    inputs: dict[str, Any] = {
        "host": {
            "connection": {
                "referenceName": connector.template_connection_name,
            }
        },
        "method": method,
        "path": (
            f"/datasets/{_encoded_path_value(document_id)}"
            f"/tables/{_encoded_path_value(sheet_name)}/items"
        ),
    }
    if operation == "append":
        columns = parameters.get("columns") or {}
        body = columns.get("value", {}) if isinstance(columns, dict) else {}
        inputs["body"] = translate_value(
            body,
            previous_action,
            diagnostics,
            str(node.get("name", "Google Sheets")),
        )
    elif not parameters.get("returnAll", True):
        inputs["queries"] = {"$top": int(parameters.get("limit", 256))}
    return {
        "type": "ApiConnection",
        "inputs": inputs,
        "metadata": {
            "flowSystemMetadata": {
                "swaggerOperationId": swagger_operation,
            }
        },
    }


def _gmail_action(
    node: dict[str, Any],
    previous_action: str | None,
    diagnostics: list[Diagnostic],
    connector: ConnectorSpec,
) -> dict[str, Any] | None:
    parameters = node.get("parameters") or {}
    resource = str(parameters.get("resource", "message"))
    operation = str(parameters.get("operation", "send"))
    if resource != "message" or operation not in {"send", "sendEmail"}:
        diagnostics.append(
            Diagnostic(
                "CONNECTOR_OPERATION_UNSUPPORTED",
                f"Gmail {resource}/{operation} is not mapped to a deterministic LAA action.",
                "warning",
                node_name=str(node.get("name", "Gmail")),
                node_type=str(node.get("type", "")),
            )
        )
        return None
    options = parameters.get("options") or {}
    if not isinstance(options, dict):
        options = {}
    if options.get("attachmentsUi"):
        diagnostics.append(
            Diagnostic(
                "CONNECTOR_ATTACHMENTS_UNSUPPORTED",
                "Gmail attachment conversion requires binary-content mapping and remains a placeholder.",
                "warning",
                node_name=str(node.get("name", "Gmail")),
                node_type=str(node.get("type", "")),
            )
        )
        return None
    recipient = parameters.get("sendTo")
    if operation == "sendEmail":
        recipient = parameters.get("email")
    body: dict[str, Any] = {
        "To": translate_value(
            recipient or "REPLACE_WITH_RECIPIENT",
            previous_action,
            diagnostics,
            str(node.get("name", "Gmail")),
        ),
        "Subject": translate_value(
            parameters.get("subject", ""),
            previous_action,
            diagnostics,
            str(node.get("name", "Gmail")),
        ),
        "Body": translate_value(
            parameters.get("message", ""),
            previous_action,
            diagnostics,
            str(node.get("name", "Gmail")),
        ),
    }
    if options.get("ccList"):
        body["Cc"] = translate_value(
            options["ccList"],
            previous_action,
            diagnostics,
            str(node.get("name", "Gmail")),
        )
    if options.get("bccList"):
        body["Bcc"] = translate_value(
            options["bccList"],
            previous_action,
            diagnostics,
            str(node.get("name", "Gmail")),
        )
    return {
        "type": "ApiConnection",
        "inputs": {
            "host": {
                "connection": {
                    "referenceName": connector.template_connection_name,
                }
            },
            "method": "post",
            "path": "/v2/Mail",
            "body": body,
        },
        "metadata": {
            "flowSystemMetadata": {
                "swaggerOperationId": "SendEmailV2",
            }
        },
    }


def _telegram_action(
    node: dict[str, Any],
    previous_action: str | None,
    diagnostics: list[Diagnostic],
    connector: ConnectorSpec,
) -> dict[str, Any] | None:
    parameters = node.get("parameters") or {}
    resource = str(parameters.get("resource", "message"))
    operation = str(parameters.get("operation", "send"))
    if (
        resource != "message"
        or operation != "send"
        or not parameters.get("chatId")
        or not parameters.get("text")
    ):
        diagnostics.append(
            Diagnostic(
                "CONNECTOR_OPERATION_UNSUPPORTED",
                f"Telegram {resource}/{operation} requires text and chatId for deterministic conversion.",
                "warning",
                node_name=str(node.get("name", "Telegram")),
                node_type=str(node.get("type", "")),
            )
        )
        return None
    additional_fields = parameters.get("additionalFields") or {}
    body = {
        "chat_id": translate_value(
            parameters["chatId"],
            previous_action,
            diagnostics,
            str(node.get("name", "Telegram")),
        ),
        "text": translate_value(
            parameters["text"],
            previous_action,
            diagnostics,
            str(node.get("name", "Telegram")),
        ),
    }
    if isinstance(additional_fields, dict) and additional_fields.get("parse_mode"):
        body["parse_mode"] = additional_fields["parse_mode"]
    diagnostics.append(
        Diagnostic(
            "TELEGRAM_TOKEN_REQUIRED",
            "Replace the Telegram bot-token placeholder with a secure deployment value before import.",
            "warning",
            node_name=str(node.get("name", "Telegram")),
            node_type=str(node.get("type", "")),
        )
    )
    return {
        "type": "ApiConnection",
        "inputs": {
            "host": {
                "connection": {
                    "referenceName": connector.template_connection_name,
                }
            },
            "method": "post",
            "path": (
                "/bot@{encodeURIComponent('REPLACE_WITH_TELEGRAM_BOT_TOKEN')}"
                "/sendMessage"
            ),
            "body": body,
        },
        "metadata": {
            "flowSystemMetadata": {
                "swaggerOperationId": "SendMessage",
            }
        },
    }


def _javascript_code_action(
    node: dict[str, Any],
    previous_action: str | None,
    diagnostics: list[Diagnostic],
) -> dict[str, Any] | None:
    parameters = node.get("parameters") or {}
    language = str(parameters.get("language", "javaScript"))
    code = parameters.get("jsCode")
    if language != "javaScript" or not isinstance(code, str) or not code.strip():
        diagnostics.append(
            Diagnostic(
                "CODE_LANGUAGE_UNSUPPORTED",
                "Only non-empty JavaScript Code nodes can use the LAA inline-code action.",
                "warning",
                node_name=str(node.get("name", "Code")),
                node_type=str(node.get("type", "")),
            )
        )
        return None
    unsupported_features = {
        "binary data": r"\bbinary\b",
        "n8n helpers": r"\bhelpers\b",
        "module loading": r"\brequire\s*\(",
        "network fetch": r"\bfetch\s*\(",
        "top-level await": r"\bawait\b",
    }
    found = [
        label
        for label, pattern in unsupported_features.items()
        if re.search(pattern, code)
    ]
    if found:
        diagnostics.append(
            Diagnostic(
                "CODE_RUNTIME_UNSUPPORTED",
                "Inline JavaScript uses unsupported n8n runtime features: "
                + ", ".join(found),
                "warning",
                node_name=str(node.get("name", "Code")),
                node_type=str(node.get("type", "")),
            )
        )
        return None

    code = re.sub(
        r"""\$\(['"]([^'"]+)['"]\)""",
        lambda match: f"__node('{action_name(match.group(1))}')",
        code,
    )
    if previous_action:
        input_expression = (
            f"workflowContext.actions['{previous_action}'].outputs.body"
        )
    else:
        input_expression = "workflowContext.trigger.outputs.body"
    prelude = f"""
const __inputRaw = {input_expression};
const __inputValues = Array.isArray(__inputRaw) ? __inputRaw : [__inputRaw];
const items = __inputValues.map(value =>
  value && typeof value === 'object' && 'json' in value ? value : {{ json: value }}
);
const __unwrap = value => {{
  if (Array.isArray(value)) {{
    return value.map(item =>
      item && typeof item === 'object' && 'json' in item ? item.json : item
    );
  }}
  return value && typeof value === 'object' && 'json' in value ? value.json : value;
}};
const __node = name => {{
  const output = workflowContext.actions[name]?.outputs;
  const raw = output && typeof output === 'object' && 'body' in output
    ? output.body
    : output;
  const values = Array.isArray(raw) ? raw : [raw];
  const wrapped = values.map(value =>
    value && typeof value === 'object' && 'json' in value ? value : {{ json: value }}
  );
  return {{
    item: wrapped[0],
    first: () => wrapped[0],
    last: () => wrapped[wrapped.length - 1],
    all: () => wrapped
  }};
}};
"""
    mode = str(parameters.get("mode", "runOnceForAllItems"))
    if mode == "runOnceForEachItem":
        wrapped_code = f"""{prelude}
const __results = items.map(item => {{
  const $json = item.json;
  const $input = {{
    item,
    first: () => item,
    last: () => item,
    all: () => items
  }};
  return (() => {{
{code}
  }})();
}});
return {{ body: __unwrap(__results) }};
"""
    else:
        wrapped_code = f"""{prelude}
const $json = items[0]?.json ?? {{}};
const $input = {{
  item: items[0],
  first: () => items[0],
  last: () => items[items.length - 1],
  all: () => items
}};
const __result = (() => {{
{code}
}})();
return {{ body: __unwrap(__result) }};
"""
    diagnostics.append(
        Diagnostic(
            "CODE_RUNTIME_REVIEW",
            "JavaScript was wrapped with an n8n item-model compatibility adapter; review runtime assumptions before publishing.",
            "warning",
            node_name=str(node.get("name", "Code")),
            node_type=str(node.get("type", "")),
        )
    )
    return {
        "type": "JavaScriptCode",
        "inputs": {"code": wrapped_code.strip()},
    }


def _convert_action(
    node: dict[str, Any],
    previous_action: str | None,
    diagnostics: list[Diagnostic],
) -> tuple[
    dict[str, Any],
    bool,
    str | None,
    ConnectorSpec | None,
]:
    mapping = get_mapping(str(node.get("type", "")))
    if mapping and mapping.converter == "http":
        return (
            _http_action(node, previous_action, diagnostics),
            True,
            "connectionProviders/Http",
            None,
        )
    if mapping and mapping.converter == "compose":
        return _compose_action(node, previous_action, diagnostics), True, None, None
    if mapping and mapping.converter == "response":
        return _response_action(node, previous_action, diagnostics), True, None, None
    if mapping and mapping.converter == "wait":
        return _wait_action(node), True, "connectionProviders/schedule", None
    if mapping and mapping.converter == "javascript_code":
        action = _javascript_code_action(node, previous_action, diagnostics)
        if action:
            return action, True, "connectionProviders/inlineCode", None
    if mapping and mapping.converter == "connector":
        connector = get_connector(mapping.connector)
        if connector and connector.key == "google_sheets":
            action = _google_sheets_action(
                node, previous_action, diagnostics, connector
            )
            if action:
                return action, True, connector.api_id, connector
        if connector and connector.key == "gmail":
            action = _gmail_action(node, previous_action, diagnostics, connector)
            if action:
                return action, True, connector.api_id, connector
        if connector and connector.key == "telegram":
            action = _telegram_action(
                node, previous_action, diagnostics, connector
            )
            if action:
                return action, True, connector.api_id, connector
    return _placeholder_action(node), False, None, None


def _unsupported_node_diagnostic(
    node: dict[str, Any],
    mode: ConversionMode,
) -> Diagnostic:
    node_type = str(node.get("type", ""))
    mapping = get_mapping(node_type)
    code = "NODE_CONFIGURATION_UNSUPPORTED" if mapping else "NODE_UNSUPPORTED"
    message = (
        f"The configuration of {node_type} is not safely supported."
        if mapping
        else f"No deterministic mapping exists for {node_type}."
    )
    if node_type in {
        "n8n-nodes-base.splitInBatches",
        "n8n-nodes-base.loopOverItems",
    }:
        code = "LOOP_SEMANTICS_UNSUPPORTED"
        message = (
            "This n8n loop requires a bounded LAA Foreach/Until region; "
            "batch size, reset, and loop-back behavior must be preserved explicitly."
        )
    elif node_type == "n8n-nodes-base.merge":
        code = "MERGE_SEMANTICS_UNSUPPORTED"
        message = (
            "This Merge node requires mode-specific data and synchronization semantics "
            "that cannot be represented by runAfter alone."
        )
    return Diagnostic(
        code,
        message,
        "error" if mode == "strict" else "warning",
        node_name=str(node.get("name", "Node")),
        node_type=node_type,
    )


def _pin_body(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    items = []
    for item in value:
        if isinstance(item, dict) and "json" in item:
            items.append(item["json"])
        else:
            items.append(item)
    return items[0] if len(items) == 1 else items


def _quality_grade(
    diagnostics: list[Diagnostic], converted: int, total: int
) -> str:
    if any(item.severity == "error" for item in diagnostics):
        return "D"
    if converted < total:
        return "C"
    if diagnostics:
        return "B"
    return "A"


def convert_workflow(
    workflow: dict[str, Any],
    options: ConversionOptions | None = None,
) -> ConversionResult:
    options = options or ConversionOptions()
    analysis = analyze_workflow(workflow)
    diagnostics = list(analysis.diagnostics)
    used_names: set[str] = set()
    normalized_nodes = [
        (str(node.get("name") or f"Node_{index + 1}"), node)
        for index, node in enumerate(workflow.get("nodes", []))
        if isinstance(node, dict)
    ]
    name_map = {
        source_name: unique_name(source_name, used_names)
        for source_name, _ in normalized_nodes
    }
    incoming = _incoming_edges(workflow)
    nodes_by_name = {
        source_name: node for source_name, node in normalized_nodes
    }
    triggers: dict[str, Any] = {}
    actions: dict[str, Any] = {}
    trigger_node_names: set[str] = set()
    converted_nodes = 0
    featured_connectors: set[str] = set()
    connections: dict[str, Any] = {}

    for source_name, node in normalized_nodes:
        target_name = name_map[source_name]
        mapping = get_mapping(str(node.get("type", "")))
        if not mapping or not mapping.trigger:
            continue
        if mapping.converter == "request_trigger":
            triggers[target_name] = _request_trigger(node)
            featured_connectors.add("connectionProviders/request")
        else:
            triggers[target_name] = _recurrence_trigger(node)
            featured_connectors.add("connectionProviders/schedule")
        trigger_node_names.add(source_name)
        converted_nodes += 1

    if_plans, consumed_by = _if_plans(
        workflow,
        nodes_by_name,
        incoming,
        diagnostics,
        options.mode,
    )
    switch_plans, switch_consumed = _switch_plans(
        workflow,
        nodes_by_name,
        incoming,
        consumed_by,
        diagnostics,
        options.mode,
    )
    consumed_by.update(switch_consumed)

    if not triggers:
        diagnostics.append(
            Diagnostic(
                "TRIGGER_MISSING",
                "No supported n8n trigger was found.",
                "error" if options.mode == "strict" else "warning",
            )
        )
        triggers["Manual_Trigger"] = {"type": "Request", "kind": "Http"}
        featured_connectors.add("connectionProviders/request")

    def compile_branch(
        branch_names: set[str],
        if_name: str,
    ) -> tuple[dict[str, Any], int]:
        branch_actions: dict[str, Any] = {}
        branch_converted = 0
        for branch_name, branch_node in nodes_by_name.items():
            if branch_name not in branch_names:
                continue
            branch_edges = incoming.get(branch_name, [])
            previous_sources = [
                name_map[source]
                for source, _, connection_type, input_index in branch_edges
                if source in branch_names
                and connection_type == "main"
                and input_index == 0
            ]
            previous_action = previous_sources[0] if previous_sources else None
            action, supported, featured_connector, connection_spec = _convert_action(
                branch_node, previous_action, diagnostics
            )
            if not supported:
                diagnostics.append(
                    _unsupported_node_diagnostic(branch_node, options.mode)
                )
            else:
                branch_converted += 1
            if featured_connector:
                featured_connectors.add(featured_connector)
            if connection_spec:
                connections[connection_spec.template_connection_name] = {
                    "connectorType": "shared",
                    "apiId": connection_spec.api_id,
                }
            run_after: dict[str, list[str]] = {}
            for source, output_index, connection_type, input_index in branch_edges:
                if source == if_name:
                    continue
                if source in branch_names and connection_type == "main":
                    run_after[name_map[source]] = ["Succeeded"]
                elif (
                    connection_type != "main"
                    or output_index
                    or input_index
                ):
                    diagnostics.append(
                        Diagnostic(
                            "CONNECTION_SEMANTICS_REVIEW",
                            "Typed or indexed connection inside an IF branch requires manual review.",
                            "error" if options.mode == "strict" else "warning",
                            node_name=branch_name,
                        )
                    )
            action["runAfter"] = run_after
            branch_actions[name_map[branch_name]] = action
        return branch_actions, branch_converted

    for source_name, node in normalized_nodes:
        if source_name in trigger_node_names:
            continue
        mapping = get_mapping(str(node.get("type", "")))
        if mapping and mapping.converter == "metadata":
            converted_nodes += 1
            continue
        if source_name in consumed_by:
            continue
        target_name = name_map[source_name]
        edges = incoming.get(source_name, [])
        previous_sources = [
            name_map[consumed_by.get(source, source)]
            for source, _, _, _ in edges
            if source in name_map
            and source not in trigger_node_names
            and consumed_by.get(source, source) != source_name
        ]
        previous_action = previous_sources[0] if previous_sources else None

        if source_name in if_plans:
            plan = if_plans[source_name]
            true_actions, true_count = compile_branch(plan["true"], source_name)
            false_actions, false_count = compile_branch(plan["false"], source_name)
            action = {
                "type": "If",
                "expression": _condition_expression(
                    node, previous_action, diagnostics, options.mode
                ),
                "actions": true_actions,
                "else": {"actions": false_actions},
            }
            converted_nodes += 1 + true_count + false_count
        elif source_name in switch_plans:
            plan = switch_plans[source_name]
            compiled_branches: list[dict[str, Any]] = []
            branch_count = 0
            for branch_names in plan["branches"]:
                branch_actions, converted_count = compile_branch(
                    branch_names, source_name
                )
                compiled_branches.append(branch_actions)
                branch_count += converted_count
            fallback_actions, fallback_count = compile_branch(
                plan["fallback"], source_name
            )
            next_action: dict[str, Any] | None = None
            for index in range(len(plan["rules"]) - 1, -1, -1):
                rule = plan["rules"][index]
                condition_node = {
                    **node,
                    "parameters": {
                        "conditions": rule.get("conditions", {})
                        if isinstance(rule, dict)
                        else {}
                    },
                }
                else_actions = (
                    {f"Rule_{index + 2}": next_action}
                    if next_action is not None
                    else fallback_actions
                )
                next_action = {
                    "type": "If",
                    "expression": _condition_expression(
                        condition_node,
                        previous_action,
                        diagnostics,
                        options.mode,
                    ),
                    "actions": compiled_branches[index],
                    "else": {"actions": else_actions},
                    "runAfter": {},
                }
            action = next_action or _placeholder_action(node)
            converted_nodes += 1 + branch_count + fallback_count
        else:
            action, supported, featured_connector, connection_spec = _convert_action(
                node, previous_action, diagnostics
            )
            if featured_connector:
                featured_connectors.add(featured_connector)
            if connection_spec:
                connections[connection_spec.template_connection_name] = {
                    "connectorType": "shared",
                    "apiId": connection_spec.api_id,
                }
            if supported:
                converted_nodes += 1
            else:
                diagnostics.append(_unsupported_node_diagnostic(node, options.mode))

        run_after: dict[str, list[str]] = {}
        for source, output_index, connection_type, input_index in edges:
            effective_source = consumed_by.get(source, source)
            if effective_source == source_name:
                continue
            source_is_if_branch = source in consumed_by
            if (
                connection_type != "main"
                or input_index
                or (output_index and not source_is_if_branch)
            ):
                diagnostics.append(
                    Diagnostic(
                        "CONNECTION_SEMANTICS_REVIEW",
                        "Typed, indexed, or uncompiled branched connection requires manual review.",
                        "error" if options.mode == "strict" else "warning",
                        node_name=source_name,
                    )
                )
            if effective_source not in trigger_node_names and effective_source in name_map:
                run_after[name_map[effective_source]] = ["Succeeded"]
        action["runAfter"] = run_after
        actions[target_name] = action

    workflow_name = str(workflow.get("name") or "Converted workflow")
    workflow_id = template_id(workflow_name)
    tags = workflow.get("tags") or ["n8n", "converted"]
    normalized_tags = [
        str(item.get("name") if isinstance(item, dict) else item)
        for item in tags
    ]
    definition: dict[str, Any] = {
        "$schema": WDL_SCHEMA,
        "contentVersion": "1.0.0.0",
        "triggers": triggers,
        "actions": actions,
        "outputs": {},
    }
    pin_data = workflow.get("pinData") or {}
    trigger_name = next(iter(triggers))
    trigger_source_name = next(iter(trigger_node_names), None)
    trigger_body = (
        _pin_body(pin_data.get(trigger_source_name))
        if trigger_source_name and isinstance(pin_data, dict)
        else {}
    )
    mocks: dict[str, Any] = {}
    if isinstance(pin_data, dict):
        for node_name, value in pin_data.items():
            if node_name in trigger_node_names or node_name not in name_map:
                continue
            mocks[name_map[node_name]] = {
                "status": "Succeeded",
                "outputs": {
                    "statusCode": "200",
                    "body": _pin_body(value),
                },
            }

    description = workflow.get("description") or (
        f"Converted from the n8n workflow '{workflow_name}'."
    )
    metadata: dict[str, Any] = {
        "id": workflow_id,
        "name": workflow_name,
        "description": str(description),
        "category": [options.category],
        "tags": normalized_tags,
        "author": options.author,
        "source": options.source,
        "featuredConnectors": sorted(featured_connectors)
        or ["connectionProviders/request"],
    }
    if options.include_source_metadata:
        metadata["tags"] = list(dict.fromkeys([*normalized_tags, "n8n-converted"]))

    template = sanitize_secrets({
        "kind": "AutoTemplate",
        "apiVersion": "v1",
        "metadata": metadata,
        "workflow": {"definition": definition},
        "trigger": {
            "name": trigger_name,
            "outputs": {
                "method": "POST",
                "headers": {"Content-Type": "application/json"},
                "queries": {},
                "body": trigger_body or {},
            },
        },
        "mocks": mocks,
        "connections": connections,
    })
    return ConversionResult(
        template=template,
        diagnostics=diagnostics,
        source_workflow_name=workflow_name,
        quality_grade=_quality_grade(
            diagnostics, converted_nodes, analysis.node_count
        ),
        converted_nodes=converted_nodes,
        total_nodes=analysis.node_count,
    )
