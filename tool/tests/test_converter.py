from __future__ import annotations

import json
import unittest
from pathlib import Path

from n8n_to_laa.analyzer import analyze_workflow
from n8n_to_laa.converter import ConversionOptions, convert_workflow
from n8n_to_laa.validator import validate_template

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ConverterTests(unittest.TestCase):
    def test_analyze_reports_full_supported_coverage(self) -> None:
        analysis = analyze_workflow(load_fixture("simple-http.json"))
        self.assertEqual(analysis.node_count, 3)
        self.assertEqual(analysis.supported_node_count, 3)
        self.assertEqual(analysis.coverage, 1)

    def test_convert_simple_http_workflow(self) -> None:
        result = convert_workflow(load_fixture("simple-http.json"))
        definition = result.template["workflow"]["definition"]
        self.assertEqual(result.quality_grade, "A")
        self.assertIn("When_clicking_Execute_Workflow", definition["triggers"])
        self.assertEqual(definition["actions"]["Get_Customer"]["type"], "Http")
        self.assertEqual(
            definition["actions"]["Select_Fields"]["runAfter"],
            {"Get_Customer": ["Succeeded"]},
        )
        self.assertEqual(
            definition["actions"]["Select_Fields"]["inputs"]["customerId"],
            "@body('Get_Customer')?['id']",
        )

    def test_generated_template_passes_envelope_validation(self) -> None:
        result = convert_workflow(load_fixture("simple-http.json"))
        self.assertEqual(validate_template(result.template), [])

    def test_best_effort_emits_placeholder(self) -> None:
        result = convert_workflow(load_fixture("unsupported.json"))
        action = result.template["workflow"]["definition"]["actions"][
            "Community_Node"
        ]
        self.assertEqual(action["type"], "Compose")
        self.assertEqual(result.quality_grade, "C")
        self.assertTrue(result.success)

    def test_strict_mode_fails_on_unsupported_node(self) -> None:
        result = convert_workflow(
            load_fixture("unsupported.json"),
            ConversionOptions(mode="strict"),
        )
        self.assertFalse(result.success)
        self.assertEqual(result.quality_grade, "D")

    def test_convert_if_branches_to_nested_actions(self) -> None:
        result = convert_workflow(load_fixture("if-branches.json"))
        actions = result.template["workflow"]["definition"]["actions"]
        condition = actions["Is_Active"]
        self.assertEqual(condition["type"], "If")
        self.assertEqual(
            condition["expression"],
            {"equals": ["@triggerBody()?['status']", "active"]},
        )
        self.assertIn("Active_Route", condition["actions"])
        self.assertIn("Inactive_Route", condition["else"]["actions"])
        self.assertNotIn("Active_Route", actions)
        self.assertEqual(actions["Continue"]["runAfter"], {"Is_Active": ["Succeeded"]})
        self.assertEqual(result.quality_grade, "A")

    def test_sticky_note_is_non_executable_metadata(self) -> None:
        workflow = load_fixture("simple-http.json")
        workflow["nodes"].append(
            {
                "name": "Documentation",
                "type": "n8n-nodes-base.stickyNote",
                "typeVersion": 1,
                "parameters": {"content": "Explain the workflow."},
            }
        )
        result = convert_workflow(workflow)
        actions = result.template["workflow"]["definition"]["actions"]
        self.assertNotIn("Documentation", actions)
        self.assertEqual(result.converted_nodes, result.total_nodes)
        self.assertEqual(result.quality_grade, "A")

    def test_wait_node_maps_to_native_wait_action(self) -> None:
        workflow = load_fixture("simple-http.json")
        workflow["nodes"].append(
            {
                "name": "Pause",
                "type": "n8n-nodes-base.wait",
                "typeVersion": 1.1,
                "parameters": {"amount": 10, "unit": "minutes"},
            }
        )
        workflow["connections"]["Select Fields"] = {
            "main": [[{"node": "Pause", "type": "main", "index": 0}]]
        }
        result = convert_workflow(workflow)
        pause = result.template["workflow"]["definition"]["actions"]["Pause"]
        self.assertEqual(
            pause["inputs"]["interval"], {"count": 10, "unit": "Minute"}
        )
        self.assertEqual(pause["runAfter"], {"Select_Fields": ["Succeeded"]})

    def test_convert_switch_to_nested_condition_chain(self) -> None:
        result = convert_workflow(load_fixture("switch-branches.json"))
        actions = result.template["workflow"]["definition"]["actions"]
        switch = actions["Route_Priority"]
        self.assertEqual(switch["type"], "If")
        self.assertEqual(
            switch["expression"],
            {"equals": ["@triggerBody()?['priority']", "high"]},
        )
        self.assertIn("High_Priority", switch["actions"])
        second_rule = switch["else"]["actions"]["Rule_2"]
        self.assertEqual(
            second_rule["expression"],
            {"equals": ["@triggerBody()?['priority']", "low"]},
        )
        self.assertIn("Low_Priority", second_rule["actions"])
        self.assertNotIn("High_Priority", actions)
        self.assertEqual(
            actions["Continue"]["runAfter"],
            {"Route_Priority": ["Succeeded"]},
        )
        self.assertEqual(result.quality_grade, "A")

    def test_convert_google_sheets_append_and_connection(self) -> None:
        workflow = {
            "name": "Append customer",
            "nodes": [
                {
                    "name": "Start",
                    "type": "n8n-nodes-base.manualTrigger",
                    "typeVersion": 1,
                    "parameters": {},
                },
                {
                    "name": "Append Row",
                    "type": "n8n-nodes-base.googleSheets",
                    "typeVersion": 4,
                    "parameters": {
                        "operation": "append",
                        "documentId": {"mode": "id", "value": "spreadsheet-123"},
                        "sheetName": {"mode": "name", "value": "Customers"},
                        "columns": {
                            "value": {
                                "CustomerId": "={{ $json.id }}",
                                "Status": "active",
                            }
                        },
                    },
                },
            ],
            "connections": {
                "Start": {
                    "main": [
                        [
                            {
                                "node": "Append Row",
                                "type": "main",
                                "index": 0,
                            }
                        ]
                    ]
                }
            },
        }
        result = convert_workflow(workflow)
        action = result.template["workflow"]["definition"]["actions"]["Append_Row"]
        self.assertEqual(action["type"], "ApiConnection")
        self.assertEqual(action["inputs"]["method"], "post")
        self.assertEqual(
            action["inputs"]["body"]["CustomerId"],
            "@triggerBody()?['id']",
        )
        self.assertIn("spreadsheet-123", action["inputs"]["path"])
        self.assertEqual(
            result.template["connections"]["googlesheet_#workflowname#"],
            {
                "connectorType": "shared",
                "apiId": "/managedApis/googlesheet",
            },
        )
        self.assertIn(
            "/managedApis/googlesheet",
            result.template["metadata"]["featuredConnectors"],
        )
        self.assertEqual(result.quality_grade, "A")

    def test_google_sheets_update_remains_visible_placeholder(self) -> None:
        workflow = {
            "name": "Update customer",
            "nodes": [
                {
                    "name": "Start",
                    "type": "n8n-nodes-base.manualTrigger",
                    "typeVersion": 1,
                    "parameters": {},
                },
                {
                    "name": "Update Row",
                    "type": "n8n-nodes-base.googleSheets",
                    "typeVersion": 4,
                    "parameters": {"operation": "update"},
                },
            ],
            "connections": {},
        }
        result = convert_workflow(workflow)
        action = result.template["workflow"]["definition"]["actions"]["Update_Row"]
        self.assertEqual(action["type"], "Compose")
        self.assertEqual(result.quality_grade, "C")
        self.assertIn(
            "CONNECTOR_OPERATION_UNSUPPORTED",
            {item.code for item in result.diagnostics},
        )

    def test_loop_placeholder_has_semantic_diagnostic(self) -> None:
        workflow = {
            "name": "Loop workflow",
            "nodes": [
                {
                    "name": "Start",
                    "type": "n8n-nodes-base.manualTrigger",
                    "typeVersion": 1,
                    "parameters": {},
                },
                {
                    "name": "Loop Items",
                    "type": "n8n-nodes-base.splitInBatches",
                    "typeVersion": 3,
                    "parameters": {"batchSize": 10},
                },
            ],
            "connections": {},
        }
        result = convert_workflow(workflow)
        self.assertIn(
            "LOOP_SEMANTICS_UNSUPPORTED",
            {item.code for item in result.diagnostics},
        )
        self.assertEqual(result.quality_grade, "C")

    def test_convert_gmail_send(self) -> None:
        workflow = {
            "name": "Send Gmail",
            "nodes": [
                {
                    "name": "Start",
                    "type": "n8n-nodes-base.manualTrigger",
                    "typeVersion": 1,
                    "parameters": {},
                },
                {
                    "name": "Send Email",
                    "type": "n8n-nodes-base.gmail",
                    "typeVersion": 2,
                    "parameters": {
                        "operation": "send",
                        "sendTo": "user@example.com",
                        "subject": "=Hello {{ $json.name }}",
                        "message": "=Customer {{ $json.id }} is ready.",
                        "options": {},
                    },
                },
            ],
            "connections": {},
        }
        result = convert_workflow(workflow)
        action = result.template["workflow"]["definition"]["actions"]["Send_Email"]
        self.assertEqual(action["inputs"]["path"], "/v2/Mail")
        self.assertEqual(
            action["inputs"]["body"]["Subject"],
            "Hello @{triggerBody()?['name']}",
        )
        self.assertEqual(
            result.template["connections"]["gmail_#workflowname#"]["apiId"],
            "/managedApis/gmail",
        )

    def test_convert_telegram_text_send(self) -> None:
        workflow = {
            "name": "Send Telegram",
            "nodes": [
                {
                    "name": "Start",
                    "type": "n8n-nodes-base.manualTrigger",
                    "typeVersion": 1,
                    "parameters": {},
                },
                {
                    "name": "Notify",
                    "type": "n8n-nodes-base.telegram",
                    "typeVersion": 1,
                    "parameters": {
                        "chatId": "={{ $json.chatId }}",
                        "text": "=Status: {{ $json.status }}",
                        "additionalFields": {"parse_mode": "HTML"},
                    },
                },
            ],
            "connections": {},
        }
        result = convert_workflow(workflow)
        action = result.template["workflow"]["definition"]["actions"]["Notify"]
        self.assertEqual(action["metadata"]["flowSystemMetadata"]["swaggerOperationId"], "SendMessage")
        self.assertEqual(
            action["inputs"]["body"]["chat_id"],
            "@triggerBody()?['chatId']",
        )
        self.assertIn("REPLACE_WITH_TELEGRAM_BOT_TOKEN", action["inputs"]["path"])
        self.assertEqual(
            result.template["connections"]["telegrambotip_#workflowname#"]["apiId"],
            "/managedApis/telegrambotip",
        )
        self.assertEqual(result.quality_grade, "B")

    def test_convert_javascript_code_with_item_adapter(self) -> None:
        workflow = {
            "name": "Transform records",
            "nodes": [
                {
                    "name": "Start",
                    "type": "n8n-nodes-base.manualTrigger",
                    "typeVersion": 1,
                    "parameters": {},
                },
                {
                    "name": "Transform",
                    "type": "n8n-nodes-base.code",
                    "typeVersion": 2,
                    "parameters": {
                        "mode": "runOnceForAllItems",
                        "jsCode": (
                            "return items.map(item => ({ "
                            "json: { id: item.json.id, active: true } }));"
                        ),
                    },
                },
            ],
            "connections": {},
        }
        result = convert_workflow(workflow)
        action = result.template["workflow"]["definition"]["actions"]["Transform"]
        self.assertEqual(action["type"], "JavaScriptCode")
        self.assertIn("const items =", action["inputs"]["code"])
        self.assertIn("return { body: __unwrap(__result) };", action["inputs"]["code"])
        self.assertIn(
            "connectionProviders/inlineCode",
            result.template["metadata"]["featuredConnectors"],
        )
        self.assertEqual(result.quality_grade, "B")

    def test_binary_javascript_remains_placeholder(self) -> None:
        workflow = {
            "name": "Binary transform",
            "nodes": [
                {
                    "name": "Start",
                    "type": "n8n-nodes-base.manualTrigger",
                    "typeVersion": 1,
                    "parameters": {},
                },
                {
                    "name": "Transform",
                    "type": "n8n-nodes-base.code",
                    "typeVersion": 2,
                    "parameters": {
                        "jsCode": "return items.map(item => item.binary);",
                    },
                },
            ],
            "connections": {},
        }
        result = convert_workflow(workflow)
        action = result.template["workflow"]["definition"]["actions"]["Transform"]
        self.assertEqual(action["type"], "Compose")
        self.assertIn(
            "CODE_RUNTIME_UNSUPPORTED",
            {item.code for item in result.diagnostics},
        )
        self.assertEqual(result.quality_grade, "C")


if __name__ == "__main__":
    unittest.main()
