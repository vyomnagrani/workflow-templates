from __future__ import annotations

import unittest

from n8n_to_laa.expressions import translate_value


class ExpressionTests(unittest.TestCase):
    def test_named_node_first_item_reference(self) -> None:
        diagnostics = []
        result = translate_value(
            "={{ $('Fetch Rows').first().json.customerId }}",
            None,
            diagnostics,
            "Current Node",
        )
        self.assertEqual(
            result,
            "@first(body('Fetch_Rows'))?['customerId']",
        )
        self.assertEqual(diagnostics, [])

    def test_input_last_item_reference(self) -> None:
        diagnostics = []
        result = translate_value(
            "={{ $input.last().json.status }}",
            "Previous_Action",
            diagnostics,
            "Current Node",
        )
        self.assertEqual(
            result,
            "@last(body('Previous_Action'))?['status']",
        )
        self.assertEqual(diagnostics, [])

    def test_mixed_text_interpolation(self) -> None:
        diagnostics = []
        result = translate_value(
            "=Customer {{ $json.id }} is {{ $json.status }}",
            None,
            diagnostics,
            "Current Node",
        )
        self.assertEqual(
            result,
            "Customer @{triggerBody()?['id']} is @{triggerBody()?['status']}",
        )
        self.assertEqual(diagnostics, [])


if __name__ == "__main__":
    unittest.main()
