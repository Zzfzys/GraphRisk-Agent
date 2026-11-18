"""Resume the frozen Route9 code through the explicitly approved web partition."""
from argparse import ArgumentParser
import importlib.util
from pathlib import Path

from graphrisk.agent.upgrade_contract import canonical
from web_trial_protocol import TrialProtocol


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--protocol-approval', type=Path)
    parser.add_argument('--outbound-approval', type=Path)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--close-execution', action='store_true')
    args = parser.parse_args()
    protocol = TrialProtocol(args.protocol, args.protocol_approval)
    if args.live or args.close_execution:
        protocol.authorize()
    protocol.install()
    source = Path(protocol.cfg['source_root']) / 'scripts/run_full_case_evaluation.py'
    spec = importlib.util.spec_from_file_location('frozen_route9_cli', source)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    print(canonical({'shared_ledger_before': protocol.status(), 'source_execution': 'unchanged_frozen_route9_code',
                     'runtime_extension': 'explicit_four_hook_web_trial_protocol'}))
    if args.close_execution:
        from graphrisk.agent.autonomous_budget import InvestigationBudget
        from graphrisk.agent.upgrade_contract import load_contract
        budget = InvestigationBudget(Path(protocol.cfg['ledger_path']),
                                     load_contract(Path(protocol.cfg['source_root']) / 'configs/agent_investigation_experiment.json'), mode='live')
        result = runner.formal.close_execution(budget, Path(protocol.cfg['route9_execution_path']))
    else:
        result = runner.run(Path(protocol.cfg['route9_execution_path']), provider='local', live=args.live,
                            approval=args.outbound_approval)
    print(canonical({'route9_result': result, 'shared_ledger_after': protocol.status()}))


if __name__ == '__main__':
    main()
