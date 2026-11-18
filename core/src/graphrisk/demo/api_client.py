"""Small HTTP-only client for the Stage 06 local workbench."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import quote

import httpx


class DemoApiError(RuntimeError):
    """The local API cannot be reached or returned an invalid response."""


@dataclass(frozen=True)
class ApiReply:
    status_code: int
    body: dict[str, Any]

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


class Stage06ApiClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000") -> None:
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str, *, json: Mapping[str, object] | None = None,
                 timeout: float = 15.0) -> ApiReply:
        try:
            with httpx.Client(base_url=self.base_url, timeout=timeout) as client:
                response = client.request(method, path, json=json)
            body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise DemoApiError("无法连接调查 API，或接口返回格式无效。请检查本地服务。") from error
        if not isinstance(body, dict):
            raise DemoApiError("调查 API 返回了非对象结果。")
        return ApiReply(status_code=response.status_code, body=body)

    def ready(self) -> ApiReply:
        return self._request("GET", "/api/v1/ready", timeout=5.0)

    def coverage(self, transaction_id: str) -> ApiReply:
        return self._request('GET', '/api/v1/coverage/'+quote(transaction_id,safe=''))

    def prospective(self, event: Mapping[str,object]) -> ApiReply:
        return self._request('POST','/api/v1/predictions/prospective',json=event,timeout=180.)

    def create_investigation(self, transaction_id: str, policy_as_of: str,
                             relation_limit: int) -> ApiReply:
        return self._request("POST", "/api/v1/investigations", json={
            "transaction_id": transaction_id, "dataset_source": "ieee_cis",
            "policy_as_of": policy_as_of, "relation_limit": relation_limit,
        }, timeout=180.0)

    def get_investigation(self, run_id: str) -> ApiReply:
        return self._request("GET", f"/api/v1/investigations/{run_id}")

    def get_approval(self, run_id: str) -> ApiReply:
        return self._request("GET", f"/api/v1/investigations/{run_id}/approval")

    def decide(self, run_id: str, *, session_id: str, transaction_id: str,
               report_sha256: str, decision: str, reviewer_id: str,
               note: str | None) -> ApiReply:
        return self._request("POST", f"/api/v1/investigations/{run_id}/approval", json={
            "run_id": run_id, "session_id": session_id,
            "transaction_id": transaction_id, "report_sha256": report_sha256,
            "decision": decision, "reviewer_id": reviewer_id, "note": note,
        })
