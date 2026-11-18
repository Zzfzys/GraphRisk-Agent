"""Offline quality packets on the original fixture-only budget executor."""
from pathlib import Path

from extensions.case_qa_v1.budget import CaseQABudget, require
from graphrisk.agent.upgrade_contract import file_hash
from .service import QualityService


class QualityBudget(CaseQABudget):
    def _validate_config(self, path=None, contract=None):
        super()._validate_config(path, contract)
        for source in (Path(__file__).resolve(), Path(__file__).with_name("service.py").resolve(),
                       Path(__file__).with_name("contract.py").resolve()):
            require(source.is_file() and self.config["files"].get(str(source)) == file_hash(source),
                    "qa_quality_source_binding_missing")

    def reserve(self, value, request, packet):
        require(packet == QualityService._packet(self, value, request)[0], "qa_history_scope_changed")
        return super().reserve(value, request, packet)
