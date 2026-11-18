"""Independent live quality page; startup itself creates no model request."""
import httpx
import streamlit as st

from extensions.case_qa_live_v3.workbench import QAClient, render_case_qa

st.set_page_config(page_title="GraphRisk · DeepSeek本案问答",page_icon=":material/chat:")
if not isinstance(st.session_state.get("qa_client"), QAClient):
    st.session_state.qa_client = QAClient(httpx.Client(base_url="http://127.0.0.1:8003",timeout=150,trust_env=False))
render_case_qa(st.session_state.qa_client)
