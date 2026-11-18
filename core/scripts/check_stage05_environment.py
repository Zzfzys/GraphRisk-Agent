"""Run the Stage 05 route 0 local framework and model smoke."""

from __future__ import annotations

import argparse
import json

from graphrisk.agent.config import load_stage05_config
from graphrisk.agent.preflight import run_preflight


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify Stage 05 LangGraph and local generation")
    parser.add_argument("--config", default="configs/stage05.yaml")
    arguments = parser.parse_args()
    result = run_preflight(load_stage05_config(arguments.config))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
