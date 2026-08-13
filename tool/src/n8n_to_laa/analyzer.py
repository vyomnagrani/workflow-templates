from __future__ import annotations

from collections import Counter
from typing import Any

from .mappings import get_mapping
from .models import Diagnostic, NodeAnalysis, WorkflowAnalysis


def analyze_workflow(workflow: dict[str, Any]) -> WorkflowAnalysis:
    nodes = workflow.get("nodes")
    connections = workflow.get("connections")
    diagnostics: list[Diagnostic] = []

    if not isinstance(nodes, list):
        diagnostics.append(
            Diagnostic(
                "N8N_INVALID_NODES",
                "The workflow must contain a nodes array.",
                "error",
                json_path="$.nodes",
            )
        )
        nodes = []
    if not isinstance(connections, dict):
        diagnostics.append(
            Diagnostic(
                "N8N_INVALID_CONNECTIONS",
                "The workflow must contain a connections object.",
                "error",
                json_path="$.connections",
            )
        )
        connections = {}

    node_analysis: list[NodeAnalysis] = []
    unsupported: Counter[str] = Counter()
    trigger_count = 0

    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            diagnostics.append(
                Diagnostic(
                    "N8N_INVALID_NODE",
                    f"Node at index {index} is not an object.",
                    "error",
                    json_path=f"$.nodes[{index}]",
                )
            )
            continue
        node_type = str(node.get("type", ""))
        name = str(node.get("name", f"Node {index + 1}"))
        mapping = get_mapping(node_type)
        if mapping and mapping.trigger:
            trigger_count += 1
        if not mapping:
            unsupported[node_type or "<missing>"] += 1
        node_analysis.append(
            NodeAnalysis(
                name=name,
                node_type=node_type,
                type_version=node.get("typeVersion"),
                supported=mapping is not None,
                converter=mapping.converter if mapping else None,
            )
        )

    connection_count = 0
    for outputs in connections.values():
        if not isinstance(outputs, dict):
            continue
        for output_groups in outputs.values():
            if not isinstance(output_groups, list):
                continue
            for group in output_groups:
                if isinstance(group, list):
                    connection_count += len(group)

    return WorkflowAnalysis(
        workflow_name=str(workflow.get("name") or "Unnamed workflow"),
        node_count=len(nodes),
        connection_count=connection_count,
        trigger_count=trigger_count,
        supported_node_count=sum(item.supported for item in node_analysis),
        unsupported_node_types=sorted(unsupported),
        nodes=node_analysis,
        diagnostics=diagnostics,
    )


def inventory_workflows(
    workflows: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    node_types: Counter[str] = Counter()
    node_versions: Counter[str] = Counter()
    credential_types: Counter[str] = Counter()
    unsupported_types: Counter[str] = Counter()
    connection_types: Counter[str] = Counter()
    branch_output_indexes: Counter[str] = Counter()
    expression_tokens: Counter[str] = Counter()
    graph_patterns: Counter[str] = Counter()
    node_type_workflows: Counter[str] = Counter()
    single_blocker_opportunities: Counter[str] = Counter()
    workflow_summaries: list[dict[str, Any]] = []
    supported_node_count = 0
    executable_node_count = 0
    supported_executable_node_count = 0
    fully_supported_workflows = 0

    for source, workflow in workflows:
        analysis = analyze_workflow(workflow)
        supported_node_count += analysis.supported_node_count
        has_ai_connection = False
        raw_nodes = workflow.get("nodes", [])
        for node, raw_node in zip(analysis.nodes, raw_nodes):
            node_types[node.node_type] += 1
            node_versions[f"{node.node_type}@{node.type_version}"] += 1
            mapping = get_mapping(node.node_type)
            if not mapping or mapping.converter != "metadata":
                executable_node_count += 1
                if node.supported:
                    supported_executable_node_count += 1
            if not node.supported:
                unsupported_types[node.node_type] += 1
            credentials = raw_node.get("credentials", {}) if isinstance(raw_node, dict) else {}
            if isinstance(credentials, dict):
                credential_types.update(str(key) for key in credentials)
            parameters = raw_node.get("parameters", {}) if isinstance(raw_node, dict) else {}
            serialized = str(parameters)
            for token in ("$json", "$(", "$node", "$input", "$items", "$env", "$vars"):
                if token in serialized:
                    expression_tokens[token] += 1

        for output_types in workflow.get("connections", {}).values():
            if not isinstance(output_types, dict):
                continue
            for connection_type, output_groups in output_types.items():
                connection_types[str(connection_type)] += 1
                has_ai_connection = has_ai_connection or str(connection_type).startswith("ai_")
                if not isinstance(output_groups, list):
                    continue
                for output_index, destinations in enumerate(output_groups):
                    if destinations:
                        branch_output_indexes[str(output_index)] += len(destinations)

        node_type_set = {node.node_type for node in analysis.nodes}
        node_type_workflows.update(node_type_set)
        unsupported_set = set(analysis.unsupported_node_types)
        if not unsupported_set:
            fully_supported_workflows += 1
        elif len(unsupported_set) == 1:
            single_blocker_opportunities.update(unsupported_set)
        if "n8n-nodes-base.if" in node_type_set:
            graph_patterns["if"] += 1
        if "n8n-nodes-base.switch" in node_type_set:
            graph_patterns["switch"] += 1
        if node_type_set.intersection(
            {"n8n-nodes-base.splitInBatches", "n8n-nodes-base.loopOverItems"}
        ):
            graph_patterns["loop"] += 1
        if has_ai_connection:
            graph_patterns["ai_cluster"] += 1
        workflow_summaries.append(
            {
                "source": source,
                "name": analysis.workflow_name,
                "nodes": analysis.node_count,
                "coverage": round(analysis.coverage, 4),
                "unsupportedNodeTypes": analysis.unsupported_node_types,
            }
        )

    return {
        "workflowCount": len(workflows),
        "nodeCount": sum(node_types.values()),
        "mappingCoverage": {
            "supportedNodes": supported_node_count,
            "coverage": round(
                supported_node_count / sum(node_types.values()), 4
            )
            if node_types
            else 0,
            "executableNodes": executable_node_count,
            "supportedExecutableNodes": supported_executable_node_count,
            "executableCoverage": round(
                supported_executable_node_count / executable_node_count, 4
            )
            if executable_node_count
            else 0,
            "fullySupportedWorkflows": fully_supported_workflows,
        },
        "nodeTypes": dict(node_types.most_common()),
        "nodeTypeWorkflowCounts": dict(node_type_workflows.most_common()),
        "singleBlockerOpportunities": dict(
            single_blocker_opportunities.most_common()
        ),
        "nodeVersions": dict(node_versions.most_common()),
        "credentialTypes": dict(credential_types.most_common()),
        "connectionTypes": dict(connection_types.most_common()),
        "branchOutputIndexes": dict(branch_output_indexes.most_common()),
        "expressionTokens": dict(expression_tokens.most_common()),
        "graphPatterns": dict(graph_patterns.most_common()),
        "unsupportedNodeTypes": dict(unsupported_types.most_common()),
        "workflows": workflow_summaries,
    }
