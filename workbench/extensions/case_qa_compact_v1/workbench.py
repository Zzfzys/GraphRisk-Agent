"""Reuse the accepted short-first UI with the compact answer contract."""
from extensions.case_qa_live_v1.workbench import QAClient as LiveQAClient,ClientError
from extensions.case_qa_live_v3.workbench import render_case_qa as _render_case_qa
from .service import REVISION,ANSWER_REVISION

class QAClient(LiveQAClient):
    def call(self, method, path, body=None, key=None):
        value = super().call(method,path,body,key)
        try:
            if value.get("adapter_revision") != REVISION or value.get("answer_revision") != ANSWER_REVISION:
                raise ValueError
            turns = [value["turn"]] if "turn" in value else value.get("turns",[])
            for turn in turns:
                result = turn["result"]
                if result.get("mode") != "live_case_qa" or result.get("source") not in {"system","deepseek","program_and_deepseek"}:
                    raise ValueError
                if result["status"] == "answered" and result["answer_payload"].get("source") != "program_projection_and_deepseek":
                    raise ValueError
                for field in ("diagnostics", "style_notes"):
                    if field in result and (type(result[field]) is not list or any(type(item) is not dict or type(item.get("message")) is not str for item in result[field])):
                        raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ClientError("request_unconfirmed" if method in {"POST","PUT"} else "read_failed",
                              uncertain=method in {"POST","PUT"}) from None
        return value



def download_record(value):
    """The short text transcript stays short; this export keeps projected values."""
    import json
    return json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)


def render_case_qa(client):
    import streamlit as st
    documents=[]
    class Capture:
        def call(self,*args,**kwargs):
            value=client.call(*args,**kwargs)
            if "conversation_id" in value and "turns" in value:
                documents[:]=[value]
            return value
    _render_case_qa(Capture())
    # Capture this render's existing GET; never make another request to export,
    # and never reuse a previous render's document after a failed read.
    if documents:
        value=documents[-1]
        st.download_button("下载问答与引用原值（JSON）",download_record(value),
            file_name="case-qa-compact-"+value["conversation_id"]+".json",
            mime="application/json",key="compact_export_"+value["conversation_id"],on_click="ignore")
