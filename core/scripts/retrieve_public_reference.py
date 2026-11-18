"""Read-only CLI for the opt-in public-reference tool. No network or model calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from graphrisk.agent.public_references import (
    PublicReferenceRetriever, ReferenceContext, ReferenceQueryPlan,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--expected-version")
    parser.add_argument("--query", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--jurisdiction")
    parser.add_argument("--payment-rail", choices=("eft", "credit_card", "other"))
    parser.add_argument("--facts-json", help="Server-verified boolean facts, never inferred from anonymous field names")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--version-label")
    parser.add_argument("--planner", choices=("literal", "rules", "llm"), default="rules")
    parser.add_argument("--query-plan", help="Previously obtained JSON queries only; does not call an LLM")
    args = parser.parse_args()
    try:
        context = ReferenceContext(as_of=args.as_of, jurisdiction=args.jurisdiction, payment_rail=args.payment_rail,
                                   facts=json.loads(Path(args.facts_json).read_bytes()) if args.facts_json else {})
        plan = ReferenceQueryPlan.model_validate_json(Path(args.query_plan).read_bytes()) if args.query_plan else None
        result = PublicReferenceRetriever(args.manifest, args.expected_version).retrieve(
            args.query, context, args.top_k, planner=args.planner, supplied_plan=plan, version_label=args.version_label)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(result.model_dump_json(indent=2))
    if result.status == "error":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
