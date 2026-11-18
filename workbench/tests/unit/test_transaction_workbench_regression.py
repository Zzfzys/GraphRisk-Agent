"""API state transitions plus one UI submission smoke; fixture transport only."""
from pathlib import Path

from fastapi.testclient import TestClient
import httpx

from test_transaction_workbench import create, make, question


def test_answer_refresh_and_case_switch_do_not_resend_or_mix_records(tmp_path):
    h = make(tmp_path)
    with TestClient(h.app) as client:
        first = create(client)
        body = question(first)
        path = "/api/v1/case-qa/conversations/" + first["conversation_id"] + "/turns"
        response = client.post(path, json=body)
        assert response.status_code == 200, response.text
        assert response.json()["turn"]["result"]["status"] == "answered"
        assert len(h.calls) == 1
        before_refresh = h.path.read_bytes()

        # The UI's refresh and transaction-selection actions are read-only GETs.
        for transaction in ("1000000", "1000001", "1000000"):
            response = client.get("/api/v1/workbench/transactions/" + transaction)
            assert response.status_code == 200, response.text
            view = response.json()
            assert view["transaction_id"] == transaction
            assert view["report_text"] == "本笔报告 " + transaction
            if transaction == "1000000":
                conversation = view["conversation"]
                assert conversation["conversation_id"] == first["conversation_id"]
                assert len(conversation["turns"]) == 1
                assert conversation["turns"][0]["message_id"] == body["message_id"]
                assert conversation["turns"][0]["result"]["status"] == "answered"
            else:
                assert view["conversation"] is None
            assert view["usage"]["used_requests"] == 6
        assert h.path.read_bytes() == before_refresh
        assert len(h.calls) == 1


def test_ui_submission_smoke(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import streamlit as st

    h = make(tmp_path)
    events = []
    st.cache_data.clear()
    st.cache_resource.clear()
    try:
        with TestClient(h.app) as client:
            original_request = client.request

            def tracked_request(method, url, *args, **kwargs):
                response = original_request(method, url, *args, **kwargs)
                events.append((method, str(url), response.status_code))
                return response

            monkeypatch.setattr(client, "request", tracked_request)
            monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: client)
            script = Path(__file__).resolve().parents[2] / "extensions/transaction_workbench/web.py"
            at = AppTest.from_file(str(script)).run(timeout=30)
            assert not at.exception
            assert not at.radio and len(at.selectbox) == 1
            assert [s.value for s in at.subheader] == ["风险与证据", "调查报告", "问问 DeepSeek"]
            assert any("本笔报告 1000000" in text.value for text in at.text)
            assert any("5 / 100" in caption.value and "¥5.00" in caption.value for caption in at.caption)
            assert not at.chat_input[0].disabled
            assert not h.calls

            at.chat_input[0].set_value("这些线索有哪些正常解释？").run(timeout=30)
            diagnostic = {"http": events, "errors": [e.value for e in at.error],
                          "warnings": [w.value for w in at.warning]}
            assert not at.exception, diagnostic
            posts = [event for event in events if event[0] == "POST" and event[1].endswith("/turns")]
            assert len(posts) == 1 and posts[0][2] == 200, diagnostic
            assert len(h.calls) == 1, diagnostic
            assert not at.chat_input[0].disabled
            assert any("DeepSeek回答" in caption.value for caption in at.caption)
    finally:
        # AppTest and the next test must not inherit cached clients or responses.
        st.cache_data.clear()
        st.cache_resource.clear()
