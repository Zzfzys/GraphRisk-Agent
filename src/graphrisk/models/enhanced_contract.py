"""Fixed fold-1 tabular development; final experiment decision stays open."""
from typing import Literal

from graphrisk.agent.stage09_preflight import ContractModel
from graphrisk.graph.stage09_enhancement import EnhancementContract


class MLPSettings(ContractModel):
    hidden_channels: Literal[64]
    dropout: Literal[0.2]
    learning_rate: Literal[0.001]
    batch_size: Literal[1024]
    max_epochs: Literal[50]
    patience: Literal[10]


class TabularSettings(ContractModel):
    schema_version: Literal["stage09-5b-tabular-v1"]
    fold: Literal["fold-1"]
    seed: Literal[20260921]
    device: Literal["cpu"]
    threads: Literal[4]
    batch_rows: Literal[4096]
    matrix: Literal["quantile_iterator"]
    max_bin: Literal[256]
    objective: Literal["binary:logistic"]
    scale_pos_weight: Literal[1.0]
    subsample: Literal[0.8]
    colsample_bytree: Literal[0.8]
    reg_lambda: Literal[1.0]
    max_cat_to_onehot: Literal[4]
    max_cat_threshold: Literal[64]
    max_cached_hist_node: Literal[128]
    max_estimators: Literal[2000]
    patience: Literal[50]
    early_stop_metric: Literal["sklearn_average_precision"]
    selection: Literal["inner_ap_then_fit_seconds_then_registered_order"]
    controls: list[Literal["legacy14", "core", "root28"]]
    search_profile: Literal["source_as_received"]
    mlp: MLPSettings
    maximum_prepare_seconds: Literal[3600]
    prediction_tolerance: Literal[0.000001]
    outer_and_test: Literal["not_read_or_scored"]
    calibration: Literal["not_read_or_used_for_selection"]

    def tasks(self, contract: EnhancementContract):
        if self.controls != ["legacy14", "core", "root28"]:
            raise ValueError("fixed control order required")
        fixed = contract.xgboost_candidates[0].model_dump()
        return [{"id": "control-"+profile, "family": "xgboost", "lane": "control", "profile": profile, "parameters": fixed} for profile in self.controls] + [
            {"id": "control-mlp-root28", "family": "mlp", "lane": "control", "profile": "root28", "parameters": self.mlp.model_dump()}] + [
            {"id": c.id, "family": "xgboost", "lane": "search", "profile": self.search_profile, "parameters": c.model_dump()} for c in contract.xgboost_candidates]
