from __future__ import annotations

from typing import Any

from .models import Diagnostic

WDL_SCHEMA = (
    "https://schema.management.azure.com/providers/Microsoft.Logic/"
    "schemas/2016-06-01/workflowdefinition.json#"
)


def validate_template(template: dict[str, Any]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []

    required = ("kind", "apiVersion", "metadata", "workflow", "trigger")
    for field in required:
        if field not in template:
            diagnostics.append(
                Diagnostic(
                    "TEMPLATE_REQUIRED_FIELD",
                    f"Missing required template field: {field}",
                    "error",
                    json_path=f"$.{field}",
                )
            )

    if template.get("kind") != "AutoTemplate":
        diagnostics.append(
            Diagnostic("TEMPLATE_KIND", "kind must be AutoTemplate.", "error")
        )
    if template.get("apiVersion") != "v1":
        diagnostics.append(
            Diagnostic("TEMPLATE_VERSION", "apiVersion must be v1.", "error")
        )

    metadata = template.get("metadata")
    if not isinstance(metadata, dict):
        diagnostics.append(
            Diagnostic("TEMPLATE_METADATA", "metadata must be an object.", "error")
        )
    else:
        for field in (
            "id",
            "name",
            "description",
            "category",
            "author",
            "source",
            "featuredConnectors",
        ):
            if not metadata.get(field):
                diagnostics.append(
                    Diagnostic(
                        "TEMPLATE_METADATA_FIELD",
                        f"metadata.{field} is required.",
                        "error",
                        json_path=f"$.metadata.{field}",
                    )
                )

    workflow = template.get("workflow")
    definition = workflow.get("definition") if isinstance(workflow, dict) else None
    if not isinstance(definition, dict):
        diagnostics.append(
            Diagnostic(
                "TEMPLATE_DEFINITION",
                "workflow.definition must be an object.",
                "error",
            )
        )
    else:
        if definition.get("$schema") != WDL_SCHEMA:
            diagnostics.append(
                Diagnostic(
                    "TEMPLATE_WDL_SCHEMA",
                    "workflow.definition.$schema is invalid.",
                    "error",
                )
            )
        if not isinstance(definition.get("triggers"), dict) or not definition.get(
            "triggers"
        ):
            diagnostics.append(
                Diagnostic(
                    "TEMPLATE_TRIGGER_MISSING",
                    "At least one trigger is required.",
                    "error",
                )
            )
        if not isinstance(definition.get("actions", {}), dict):
            diagnostics.append(
                Diagnostic(
                    "TEMPLATE_ACTIONS",
                    "workflow.definition.actions must be an object.",
                    "error",
                )
            )

    trigger = template.get("trigger")
    if not isinstance(trigger, dict) or not trigger.get("name"):
        diagnostics.append(
            Diagnostic(
                "TEMPLATE_TRIGGER_FIXTURE",
                "trigger.name and trigger.outputs are required.",
                "error",
            )
        )
    elif isinstance(definition, dict) and trigger["name"] not in definition.get(
        "triggers", {}
    ):
        diagnostics.append(
            Diagnostic(
                "TEMPLATE_TRIGGER_REFERENCE",
                "trigger.name must reference a workflow trigger.",
                "error",
            )
        )

    connections = template.get("connections", {})
    if isinstance(connections, dict):
        for name, value in connections.items():
            if not name.endswith("_#workflowname#"):
                diagnostics.append(
                    Diagnostic(
                        "TEMPLATE_CONNECTION_NAME",
                        f"Connection {name} must end with _#workflowname#.",
                        "error",
                    )
                )
            if not isinstance(value, dict) or value.get(
                "connectorType"
            ) not in {"shared", "inapp", "agent"}:
                diagnostics.append(
                    Diagnostic(
                        "TEMPLATE_CONNECTION_TYPE",
                        f"Connection {name} has an invalid connectorType.",
                        "error",
                    )
                )
    return diagnostics

