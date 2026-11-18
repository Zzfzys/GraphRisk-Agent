"""Serve closed daily risk, new arrivals and bound day-end evidence locally."""
from argparse import ArgumentParser
from datetime import date, datetime, timezone
from pathlib import Path

import uvicorn

from api.main import create_app
from graphrisk.agent.autonomous_tools import ArtifactGuard
from graphrisk.graph.coverage_service import CoverageGraphService, CoverageRiskTool, load_coverage
from graphrisk.investigation.repository import InvestigationRepository
from graphrisk.serving.champion_daily_batch import ChampionDailyBatch, ClosedHistoricalBatches
from graphrisk.serving.model_risk_band import MODELS, PROFILE_SHA


def build_service(runtime: Path, available_through_second: int):
    route = runtime/'reports/generated/agent-investigation-upgrade/route6'
    study = route/'20261102-champion-216-xgb-v1'
    freeze = route/'20261105-champion-feature-freeze-v1'
    model = ChampionDailyBatch(
        model=study/'fold-3/day/enhanced-model.json', protocol=study/'protocol.json',
        maps=freeze/'category-maps.json', audit=freeze/'audit.json',
        policy=route/'20261105-daily-bands-v1/policy.json',
        source=runtime/'artifacts/stage-09/route5b/20261009-features-v1/features.parquet',
        derived=route/'20261102-champion-association-features-v2/day-features.parquet')
    profile_path = runtime/'artifacts/stage-09/route5b/20261015-coverage-lowmem-v3/coverage-profile-calendar-v2.json'
    profile = load_coverage(runtime, profile_path, '5ecb2126eb282fc759bbbf261e576c6fe3b5f19d3c0185a9dee7d21a821232e4')
    transaction = runtime/'data/interim/ieee-cis/transactions.parquet'
    features = runtime/'data/interim/ieee-cis/features_all.parquet'
    graph = CoverageGraphService(runtime, profile, transaction, features)
    x3_tool = CoverageRiskTool(InvestigationRepository('ieee_cis', transaction, features), graph)
    bindings = {profile_path: PROFILE_SHA}
    for fold in (2, 3):
        bindings[runtime/f'artifacts/stage-09/route5b/20261010-confirmation-v1/fold-{fold}/x3/model.json'] = MODELS[f'x3-fold-{fold}-historical-v1'][0]
    return ClosedHistoricalBatches(model, available_through_second=available_through_second,
                                   x3_tool=x3_tool, x3_guard=ArtifactGuard(bindings))


def build_new_services(runtime: Path, *, db_path: Path, history_cutoff_second: int,
                       clock, policy_as_of: date, x3_conversations=None,
                       narrative_backend=None, reference_answer_service=None,
                       reference_query=None, reference_cache=None, case_reference_service=None):
    from graphrisk.serving.daily_batch_store import DailyBatchStore
    from graphrisk.serving.daily_risk_service import DailyRiskService, RoutedConversationService
    from graphrisk.serving.daily_conversation_backend import DailyConversationBackend
    from graphrisk.serving.conversations import ConversationService
    from graphrisk.serving.conversation_store import MemoryConversationStore
    from graphrisk.serving.daily_investigation_tools import DailyInvestigationTools
    from graphrisk.agent.policy_retrieval import load_policy_index, PolicyRetriever
    from graphrisk.agent.autonomous_policy import PolicyRegistry
    from graphrisk.agent.upgrade_contract import file_hash
    fallback = build_service(runtime, clock())
    store = DailyBatchStore(db_path, fallback.model,
        history=fallback.model.paths['source'], history_cutoff_second=history_cutoff_second,
        scratch=runtime/'artifacts/tmp/daily-inference')
    service = DailyRiskService(store, clock=clock, fallback=fallback)
    index_path = runtime/'reports/generated/agent-investigation-upgrade/route1/20261019-native-v2/lexical-policy-index.json'
    annotations = Path(__file__).resolve().parents[1]/'configs/agent_investigation_policy_requirements.json'
    registry = PolicyRegistry.from_file(load_policy_index(index_path), annotations)
    policy_guard = ArtifactGuard({index_path: file_hash(index_path), annotations: file_hash(annotations)})
    tools = DailyInvestigationTools(store, retriever=PolicyRetriever(index_path),
                                   registry=registry, source_guard=policy_guard)
    backend = DailyConversationBackend(store, policy_as_of=policy_as_of,
        source_guard=fallback.model._check_files, evidence_tools=tools,
        narrative_backend=narrative_backend, reference_answer_service=reference_answer_service,
        reference_query=reference_query, reference_cache=reference_cache,
        case_reference_service=case_reference_service)
    conversations = RoutedConversationService(
        ConversationService(MemoryConversationStore(), backend), x3_conversations)
    return service, conversations


def relative_clock(epoch_utc: str):
    """An explicitly configured time origin, never inferred from IEEE relative time."""
    epoch = datetime.fromisoformat(epoch_utc.replace('Z', '+00:00'))
    if epoch.tzinfo is None or epoch.utcoffset().total_seconds() != 0:
        raise ValueError('relative epoch must include an explicit UTC offset')
    return lambda: int((datetime.now(timezone.utc) - epoch).total_seconds())


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--mode', choices=('historical', 'new'), default='new')
    clock_group = parser.add_mutually_exclusive_group(required=True)
    clock_group.add_argument('--available-through-second', type=int,
                             help='Server-owned fixed relative clock for local replay')
    clock_group.add_argument('--relative-epoch-utc', help='Explicit UTC origin for the arrival clock')
    parser.add_argument('--history-cutoff-second', type=int, help='Last source event second in the bound history')
    parser.add_argument('--batch-db', type=Path)
    parser.add_argument('--policy-as-of', type=date.fromisoformat)
    parser.add_argument('--public-reference-cache', type=Path,
                        help='Explicit reviewed public-question answer cache; no new model request')
    parser.add_argument('--public-reference-manifest', type=Path,
                        help='Trusted local corpus for task-bound case reference preparation; no model call')
    parser.add_argument('--public-reference-version',
                        help='Required pinned corpus version for --public-reference-manifest')
    parser.add_argument('--x3-conversations', action='store_true',
                        help='Also register the original read-only x3 conversation backend')
    parser.add_argument('--port', type=int, default=8002)
    args = parser.parse_args()
    if (args.public_reference_manifest is None) != (args.public_reference_version is None):
        parser.error('public reference manifest and pinned version must be configured together')
    if args.public_reference_manifest is not None and args.public_reference_cache is not None:
        parser.error('case references and a fixed public-question cache are mutually exclusive')
    clock = (relative_clock(args.relative_epoch_utc) if args.relative_epoch_utc else
             lambda: args.available_through_second)
    if args.mode == 'historical':
        if args.public_reference_cache is not None or args.public_reference_manifest is not None:
            parser.error('public references require new sealed-batch conversation mode')
        app = create_app(daily_risk_service_factory=lambda: build_service(args.runtime, clock()))
    else:
        if args.history_cutoff_second is None or args.policy_as_of is None:
            parser.error('new mode requires --history-cutoff-second and --policy-as-of')
        original = None
        case_reference_service = None
        if args.public_reference_manifest is not None:
            from graphrisk.agent.public_references import PublicReferenceRetriever
            from graphrisk.agent.reference_answers import ReferenceAnswerService
            case_reference_service = ReferenceAnswerService(PublicReferenceRetriever(
                args.public_reference_manifest, args.public_reference_version))
        if args.x3_conversations:
            from serve_investigation_conversations import make_service
            original = make_service(args.runtime, live=False, narrative=True)
        risk, conversations = build_new_services(args.runtime,
            db_path=args.batch_db or args.runtime/'artifacts/daily-risk/arrivals.sqlite',
            history_cutoff_second=args.history_cutoff_second, clock=clock,
            policy_as_of=args.policy_as_of, x3_conversations=original,
            reference_cache=args.public_reference_cache, case_reference_service=case_reference_service)
        app = create_app(daily_risk_service_factory=lambda: risk,
                         conversation_service_factory=lambda: conversations)
    uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
