"""Explicit proxy correction for the separately approved five-case continuation.

Imports and server startup never generate a request.  The old failed H1 remains
unknown in the shared ledger; the recovery protocol decides what may run next.
"""
from __future__ import annotations

from argparse import ArgumentParser
import importlib.util
import os
from pathlib import Path
import time

from graphrisk.agent import autonomous_http
from graphrisk.agent import judgment_v12_daily_quotes as quotes
from graphrisk.agent.autonomous_actions import ActionFailure
from graphrisk.agent.autonomous_budget import ExecutionStop, InvestigationBudget
from graphrisk.agent.upgrade_contract import canonical, digest, load_contract
import serve_live_trial as original


PROXY = 'http://127.0.0.1:7890'
ENDPOINT = 'https://api.deepseek.com/chat/completions'


def checked_proxy(provider, profile):
    if (provider != 'deepseek' or profile.get('deepseek', {}).get('endpoint') != ENDPOINT
            or profile.get('approved_loopback_proxy') != PROXY):
        raise ActionFailure('web_trial_approved_proxy_required')
    return PROXY


class ApprovedProxyProvider(quotes.Provider):
    def __init__(self, provider, **kwargs):
        # Validate before reserve(), so a bad route cannot create another
        # unknown request or consume its conservative fee reservation.
        checked_proxy(provider, kwargs['profile'])
        super().__init__(provider, **kwargs)

    def post_payload(self, payload, *, headers, timeout):
        proxy = checked_proxy(self.provider, self.profile)
        return autonomous_http.post_one(self.profile['deepseek']['endpoint'], payload,
            headers=headers, timeout=timeout, transport=self.transport, proxy=proxy)


class RecoveryLiveDelegate(original.LiveDelegate):
    def make_provider(self, campaign, phase, deadline):
        checked_proxy(self.provider, self.report_profile)
        self.protocol.register_packet(self.store.budget, phase, campaign, self.report_profile)
        self.protocol.guard_campaign(campaign, phase, self.report_profile)
        return ApprovedProxyProvider('deepseek', profile=self.report_profile,
            budget=self.store.budget, campaign=campaign, campaign_sha=digest(campaign),
            phase=phase, output=Path(self.protocol.config_path).parent / 'receipts',
            runtime=Path(self.protocol.cfg['runtime']), live=True,
            transport=self.provider_transport, remaining=lambda: deadline - time.time())


def recovery_public_status(protocol):
    try:
        value = protocol.status()
        approved = bool(value.get('authorized', value.get('approved', False)))
        used = value.get('web_calls', value.get('used_calls', 0))
        if type(used) is not int:
            raise ValueError('invalid_status_count')
        return dict(approved=approved, used_calls=used, max_calls=6,
            state='ready' if approved and used < 6 and not value.get('stopped') else 'stopped',
            detail='H1原失败和费用保留；恢复批准后仅接续其余五案，H1不重发。')
    except (ExecutionStop, OSError, ValueError):
        return dict(approved=False, used_calls=0, max_calls=6, state='stopped',
                    error_code='web_trial_recovery_status_unavailable')


def make_recovery_app(protocol, *, provider_transport=None):
    # Explicit dependency injection in this new process, rather than editing
    # any frozen original module.  Only the existing factory's delegate varies.
    original.LiveDelegate = RecoveryLiveDelegate
    original.public_status = recovery_public_status
    return original.make_app(protocol.config_path, protocol.approval_path,
                             protocol=protocol, provider_transport=provider_transport)


def route9(protocol, *, live=False, close=False, outbound_approval=None):
    if live or close:
        protocol.authorize()
    if live and os.getenv('GRAPHRISK_UPGRADE_LIVE') != '1':
        raise ExecutionStop('upgrade_live_opt_in_required')
    protocol.install()
    source = Path(protocol.cfg['source_root']) / 'scripts/run_full_case_evaluation.py'
    spec = importlib.util.spec_from_file_location('frozen_route9_recovery_cli', source)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    if close:
        budget = InvestigationBudget(Path(protocol.cfg['ledger_path']),
            load_contract(Path(protocol.cfg['source_root']) / 'configs/agent_investigation_experiment.json'),
            mode='live')
        return runner.formal.close_execution(budget, Path(protocol.cfg['route9_execution_path']))
    return runner.run(Path(protocol.cfg['route9_execution_path']), provider='local',
                      live=live, approval=outbound_approval)


def main():
    parser = ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--serve', action='store_true')
    mode.add_argument('--route9-check', action='store_true')
    mode.add_argument('--route9-live', action='store_true')
    mode.add_argument('--route9-close', action='store_true')
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--approval', type=Path, required=True)
    parser.add_argument('--recovery-config', type=Path, required=True)
    parser.add_argument('--recovery-approval', type=Path)
    parser.add_argument('--route9-outbound-approval', type=Path)
    parser.add_argument('--port', type=int, default=8002)
    args = parser.parse_args()
    from web_trial_recovery import RecoveryTrialProtocol
    protocol = RecoveryTrialProtocol(args.config, args.approval,
        recovery_config_path=args.recovery_config,
        recovery_approval_path=args.recovery_approval)
    if args.serve:
        if os.getenv('GRAPHRISK_UPGRADE_LIVE') != '1':
            raise ExecutionStop('upgrade_live_opt_in_required')
        import uvicorn
        uvicorn.run(make_recovery_app(protocol), host='127.0.0.1',
                    port=args.port, access_log=False)
    else:
        print(canonical({'shared_ledger_before': protocol.status(),
            'route9_result': route9(protocol, live=args.route9_live, close=args.route9_close,
                                    outbound_approval=args.route9_outbound_approval),
            'shared_ledger_after': protocol.status()}))


if __name__ == '__main__':
    main()
