from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConnectorSpec:
    key: str
    connection_name: str
    api_id: str

    @property
    def template_connection_name(self) -> str:
        return f"{self.connection_name}_#workflowname#"


CONNECTORS = {
    "google_sheets": ConnectorSpec(
        key="google_sheets",
        connection_name="googlesheet",
        api_id="/managedApis/googlesheet",
    ),
    "gmail": ConnectorSpec(
        key="gmail",
        connection_name="gmail",
        api_id="/managedApis/gmail",
    ),
    "telegram": ConnectorSpec(
        key="telegram",
        connection_name="telegrambotip",
        api_id="/managedApis/telegrambotip",
    ),
}


def get_connector(key: str | None) -> ConnectorSpec | None:
    return CONNECTORS.get(key or "")
