from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from n8n_to_laa.catalog import download_catalog


class CatalogTests(unittest.TestCase):
    def test_downloads_and_normalizes_catalog_workflow(self) -> None:
        def fetcher(url: str) -> dict:
            if "/templates/search?" in url:
                return {
                    "totalWorkflows": 1,
                    "workflows": [{"id": 42, "name": "Example"}],
                }
            self.assertTrue(url.endswith("/templates/workflows/42"))
            return {
                "workflow": {
                    "id": 42,
                    "name": "Example",
                    "description": "Example workflow",
                    "createdAt": "2026-01-01T00:00:00Z",
                    "totalViews": 100,
                    "user": {"name": "Example Author", "username": "example"},
                    "categories": [{"name": "Engineering"}],
                    "workflow": {
                        "nodes": [],
                        "connections": {},
                        "settings": {},
                    },
                }
            }

        with tempfile.TemporaryDirectory() as temp:
            result = download_catalog(
                Path(temp),
                limit=1,
                delay_seconds=0,
                fetcher=fetcher,
            )
            self.assertEqual(result.downloaded, 1)
            workflow = json.loads((Path(temp) / "42.json").read_text())
            self.assertEqual(workflow["id"], "42")
            self.assertEqual(workflow["author"], "Example Author")
            self.assertEqual(workflow["tags"], ["Engineering"])
            self.assertEqual(
                workflow["sourceUrl"], "https://n8n.io/workflows/42/"
            )


if __name__ == "__main__":
    unittest.main()
