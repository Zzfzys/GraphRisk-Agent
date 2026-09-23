from __future__ import annotations

import argparse
from pathlib import Path

from graphrisk.config import load_config
from graphrisk.investigation.risk_tool import RiskModelTool
from graphrisk.investigation.workflow import InvestigationWorkflow
from graphrisk.training.config import load_training_config


def _write_json(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the deterministic Stage 03 transaction investigation"
    )
    parser.add_argument("transaction_id")
    parser.add_argument("--data-config", default="configs/ieee_cis.yaml")
    parser.add_argument(
        "--training-config", default="configs/train_baseline_ieee_cis.yaml"
    )
    parser.add_argument("--relation-limit", type=int, default=20)
    parser.add_argument("--report-output", type=Path)
    parser.add_argument("--audit-output", type=Path)
    arguments = parser.parse_args()

    data_config = load_config(arguments.data_config)
    training_config = load_training_config(arguments.training_config)
    tool = RiskModelTool.from_configs(data_config, training_config)
    run = InvestigationWorkflow(tool).run(
        {
            "transaction_id": arguments.transaction_id,
            "dataset_source": data_config.data.source,
            "relation_limit": arguments.relation_limit,
        }
    )
    report_json = run.report.model_dump_json(indent=2)
    audit_json = run.audit.model_dump_json(indent=2)
    if arguments.report_output is not None:
        _write_json(arguments.report_output, report_json)
    if arguments.audit_output is not None:
        _write_json(arguments.audit_output, audit_json)
    if arguments.report_output is None and arguments.audit_output is None:
        print(run.model_dump_json(indent=2))
    else:
        print(
            f"status={run.report.status}, "
            f"report={arguments.report_output or 'stdout-disabled'}, "
            f"audit={arguments.audit_output or 'stdout-disabled'}"
        )


if __name__ == "__main__":
    main()
