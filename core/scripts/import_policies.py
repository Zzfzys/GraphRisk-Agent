"""Validate the public demo policies and write a reproducible local manifest."""

from __future__ import annotations

import argparse
import hashlib

from graphrisk.agent.config import load_stage05_config
from graphrisk.agent.policy_corpus import load_policy_corpus, write_policy_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Stage 05 simulated policy sources")
    parser.add_argument("--config", default="configs/stage05.yaml")
    arguments = parser.parse_args()
    config = load_stage05_config(arguments.config)
    corpus = load_policy_corpus(config.resolve_path(config.paths.policy_source_dir))
    if len(corpus.documents) < 8:
        raise ValueError("Stage 05 route 1 requires at least eight policy documents")
    target = config.resolve_path(config.paths.policy_artifact_dir) / "corpus-manifest.json"
    write_policy_manifest(corpus, target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    print(f"documents={len(corpus.documents)} manifest_sha256={digest} output={target}")


if __name__ == "__main__":
    main()
