from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path

from graphrisk.config import load_config
from graphrisk.data.ingestion import IngestionSummary, ingest_canonical_transactions
from graphrisk.data.splitting import SplitSummary, write_temporal_splits
from graphrisk.data.synthetic import GenerationSummary, generate_from_config
from graphrisk.features.transaction_features import (
    FeatureBuildSummary,
    build_historical_features,
)
from graphrisk.logging_config import configure_logging
from graphrisk.reporting.data_quality import write_feature_report

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineSummary:
    generation: GenerationSummary | None
    ingestion: IngestionSummary
    features: FeatureBuildSummary
    splits: SplitSummary
    report_path: Path


def run_pipeline(config_path: str | Path) -> PipelineSummary:
    config = load_config(config_path)
    configure_logging(config.logging.level)

    raw_path = config.resolve_path(config.paths.raw_transactions)
    generation: GenerationSummary | None = None
    if config.data.source == "synthetic" and (
        config.data.regenerate_synthetic or not raw_path.is_file()
    ):
        generation = generate_from_config(config)
    elif not raw_path.is_file():
        raise FileNotFoundError(f"configured transaction data does not exist: {raw_path}")

    ingestion = ingest_canonical_transactions(
        raw_path,
        config.resolve_path(config.paths.interim_transactions),
    )
    features = build_historical_features(
        ingestion.output_path,
        config.resolve_path(config.paths.interim_features),
    )
    splits = write_temporal_splits(
        features.output_path,
        config.resolve_path(config.paths.processed_dir),
        config.split,
    )
    report_path = write_feature_report(
        features.output_path,
        splits,
        config.resolve_path(config.paths.feature_report),
        source_name=config.data.source,
    )

    LOGGER.info(
        "feature pipeline completed",
        extra={
            "row_count": features.row_count,
            "train_count": splits.train_count,
            "validation_count": splits.validation_count,
            "test_count": splits.test_count,
            "report_path": str(report_path),
        },
    )
    return PipelineSummary(
        generation=generation,
        ingestion=ingestion,
        features=features,
        splits=splits,
        report_path=report_path,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build point-in-time transaction features")
    parser.add_argument("--config", default="configs/dev.yaml")
    arguments = parser.parse_args()

    summary = run_pipeline(arguments.config)
    print(
        "feature pipeline complete: "
        f"train={summary.splits.train_count}, "
        f"validation={summary.splits.validation_count}, "
        f"test={summary.splits.test_count}, "
        f"report={summary.report_path}"
    )


if __name__ == "__main__":
    main()
