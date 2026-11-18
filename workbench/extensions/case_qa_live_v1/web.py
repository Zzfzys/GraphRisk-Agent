"""Independent browser entry; no model calls until an explicit authorized question."""
import httpx
import streamlit as st
from extensions.case_qa_live_v1.workbench import QAClient,render_case_qa

st.set_page_config(page_title="GraphRisk · DeepSeek本案问答",page_icon=":material/chat:")
if "qa_client" not in st.session_state:
    st.session_state.qa_client=QAClient(httpx.Client(base_url="http://127.0.0.1:8003",timeout=150,trust_env=False))
render_case_qa(st.session_state.qa_client)
