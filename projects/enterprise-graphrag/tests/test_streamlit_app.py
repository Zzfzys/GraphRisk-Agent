"""UI smoke tests use no model calls or generated case scores."""
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

try:
    from streamlit.testing.v1 import AppTest
except ImportError:
    AppTest = None

APP = Path(__file__).resolve().parents[1] / "streamlit_app.py"


@unittest.skipUnless(AppTest is not None, "install the optional demo extra")
class WorkbenchTests(unittest.TestCase):
    def test_unknown_service_refuses_without_model_call(self):
        with tempfile.TemporaryDirectory() as folder:
            index = Path(folder) / "placeholder-index.json"
            index.write_text("{}", encoding="utf-8")
            with patch.dict(os.environ, {"GRAPHRAG_DEMO_INDEX": str(index)}):
                page = AppTest.from_file(str(APP), default_timeout=15).run()
                self.assertFalse(page.exception)
                page.selectbox(key="example").select("无答案：未知服务").run()
                page.button[0].click().run()
                self.assertFalse(page.exception)
                self.assertTrue(page.warning)
                self.assertEqual(page.session_state["graphrag_result"]["reason"], "unknown_entity")
                self.assertEqual(page.session_state["graphrag_result"]["audit"]["generation_calls"], 0)

    def test_verified_answer_displays_path_and_source(self):
        verified = {
            "status": "answered", "reason": "verified_evidence_selection", "mode": "graph",
            "as_of": "2026-09-30",
            "answer": {
                "text": "截至 2026-09-30，根据原创模拟资料：网关由平台组负责。",
                "path": {"path_id": "path-example", "edge_ids": ["e-example"]},
                "claims": [{"edge_id": "e-example", "source": "svc-gateway", "relation": "OWNED_BY",
                            "target": "team-platform", "chunk_id": "service-gateway-v1#owner"}],
                "citations": [{"chunk_id": "service-gateway-v1#owner", "version": 1,
                               "authority": "approved", "quote": "网关责任团队是平台组。",
                               "source_file": "documents/service-gateway-v1.md", "sha256": "sample",
                               "effective_from": "2026-01-01", "effective_to": None}],
            },
            "audit": {"model": "fixture", "retrieval_calls": 1, "generation_calls": 1,
                      "external_actions": 0, "elapsed_ms": 2, "steps": []},
        }
        with tempfile.TemporaryDirectory() as folder:
            index = Path(folder) / "placeholder-index.json"
            index.write_text("{}", encoding="utf-8")
            with patch.dict(os.environ, {"GRAPHRAG_DEMO_INDEX": str(index)}), \
                 patch("enterprise_graphrag.answering.answer", return_value=verified):
                page = AppTest.from_file(str(APP), default_timeout=15).run()
                page.button[0].click().run()
                self.assertFalse(page.exception)
                self.assertTrue(page.success)
                self.assertIn("原创模拟资料", page.success[0].value)
                self.assertTrue(page.table)
                self.assertTrue(any("service-gateway-v1#owner" in item.label for item in page.expander))
    def test_missing_index_stops_before_model(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / "not-built.json"
            with patch.dict(os.environ, {"GRAPHRAG_DEMO_INDEX": str(missing)}):
                page = AppTest.from_file(str(APP), default_timeout=15).run()
                self.assertFalse(page.exception)
                page.button[0].click().run()
                self.assertFalse(page.exception)
                self.assertTrue(page.error)
                self.assertNotIn("graphrag_result", page.session_state)


if __name__ == "__main__":
    unittest.main()