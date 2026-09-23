from __future__ import annotations

import argparse

from graphrisk.graph.build_artifacts import build_graph_artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Stage 04 temporal graph artifacts")
    parser.add_argument("--config", default="configs/graph_ieee_cis.yaml")
    arguments = parser.parse_args()
    summary = build_graph_artifacts(arguments.config)
    print(
        "graph artifacts complete: "
        f"indexed={summary.indexed_transactions}, "
        f"targets={summary.target_counts}, "
        f"content_sha256={summary.content_sha256}, "
        f"elapsed_seconds={summary.elapsed_seconds:.2f}, "
        f"manifest={summary.manifest_path}"
    )


if __name__ == "__main__":
    main()
