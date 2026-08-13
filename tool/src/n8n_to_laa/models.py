from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Severity = Literal["info", "warning", "error"]
ConversionMode = Literal["strict", "best-effort"]


@dataclass(slots=True)
class Diagnostic:
    code: str
    message: str
    severity: Severity = "warning"
    node_name: str | None = None
    node_type: str | None = None
    json_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class NodeAnalysis:
    name: str
    node_type: str
    type_version: float | int | None
    supported: bool
    converter: str | None


@dataclass(slots=True)
class WorkflowAnalysis:
    workflow_name: str
    node_count: int
    connection_count: int
    trigger_count: int
    supported_node_count: int
    unsupported_node_types: list[str] = field(default_factory=list)
    nodes: list[NodeAnalysis] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        if self.node_count == 0:
            return 0.0
        return self.supported_node_count / self.node_count

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["coverage"] = round(self.coverage, 4)
        return result


@dataclass(slots=True)
class ConversionResult:
    template: dict[str, Any]
    diagnostics: list[Diagnostic]
    source_workflow_name: str
    quality_grade: str
    converted_nodes: int
    total_nodes: int

    @property
    def success(self) -> bool:
        return not any(item.severity == "error" for item in self.diagnostics)

    def report(self) -> dict[str, Any]:
        return {
            "sourceWorkflowName": self.source_workflow_name,
            "success": self.success,
            "qualityGrade": self.quality_grade,
            "convertedNodes": self.converted_nodes,
            "totalNodes": self.total_nodes,
            "coverage": round(self.converted_nodes / self.total_nodes, 4)
            if self.total_nodes
            else 0,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }

