from pathlib import Path

import pytest

from test_full_case_execution import prepare,start,write,index,registry,profile,daily_scope,scope
from graphrisk.agent import full_case_execution as formal
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.upgrade_contract import file_hash


def test_saved_case_authenticates_raw_receipts_and_rejects_changed_result(tmp_path,daily_scope,index,registry,profile):
    budget,posts,options,cfg,config,note=prepare(tmp_path,daily_scope,index,registry,profile)
    original=options['provider_factory'];directory=Path(cfg['episodes'][0]['directory'])/'receipts'
    class Factory:
        provider='deepseek'
        def __call__(self,*args):
            value=original(*args);value.output=directory;directory.mkdir(parents=True,exist_ok=True)
            return value
    options['provider_factory']=Factory()
    state=start(budget,options,cfg,config,note,'deepseek').run()
    episode=cfg['episodes'][0];path=Path(episode['directory'])/'result-private.json'
    write(path,formal.episode_result(episode,state))
    with budget.transaction() as db:
        proof=formal.audit_episode(db,cfg,episode)
    assert proof['model_calls']==5 and len(posts)==5
    changed=formal.read(path);changed['status']='invented_success';write(path,changed)
    with budget.transaction() as db:
        with pytest.raises(ExecutionStop,match='saved_terminal_result'):formal.audit_episode(db,cfg,episode)
    write(path,formal.episode_result(episode,state))
    receipt=next(Path(episode['directory']).joinpath('receipts').glob('*.json'))
    receipt.write_text('{}',encoding='utf-8')
    with budget.transaction() as db:
        with pytest.raises(ExecutionStop,match='saved_receipt_changed'):formal.audit_episode(db,cfg,episode)
    assert len(posts)==5


def test_close_preserves_all_original_failures_and_prevents_new_execution(tmp_path,daily_scope,index,registry,profile):
    budget,posts,options,cfg,config,note=prepare(tmp_path,daily_scope,index,registry,profile)
    cfg['output']=str(tmp_path/'closed')
    for episode in cfg['episodes']:
        episode.update(preblocked=True,scope_sha256=None,scope_path=None)
    write(config,cfg)
    approval=formal.read(note);approval['execution_sha256']=file_hash(config);write(note,approval)
    formal.register(budget,config,note)
    options['store'].campaign(cfg['campaign'],file_hash(config),cap_each=325,group_key=formal.GROUP)
    with pytest.raises(ExecutionStop,match='episode_result_missing'):formal.close_execution(budget,config)
    with budget.transaction() as db:
        assert db.execute('SELECT closed FROM formal_case_grants').fetchone()==(0,)
    for episode in cfg['episodes']:
        write(Path(episode['directory'])/'result-private.json',formal.episode_result(episode))
    manifest=formal.close_execution(budget,config)
    assert len(manifest['episodes'])==130 and manifest['first_case_denominator_per_provider']==60
    assert manifest['semantic_quality_acceptance']=='Pending' and manifest['human_reading']=='Pending'
    assert manifest['new_model_calls']==0 and not posts
    assert formal.close_execution(budget,config)==manifest
    with budget.transaction() as db:
        with pytest.raises(ExecutionStop,match='grant_missing_or_closed'):formal.load(db,cfg['campaign'])


def test_closed_cli_does_not_preflight_or_construct_a_provider(monkeypatch):
    import run_full_case_evaluation as cli
    monkeypatch.setattr(cli,'check',lambda path:({}, {'execution_closed':True,'new_model_calls':0}))
    def forbidden(*args,**kwargs):raise AssertionError('closed campaign touched live execution')
    monkeypatch.setattr(cli,'ActionProvider',forbidden)
    monkeypatch.setattr(cli,'InvestigationBudget',forbidden)
    assert cli.run(Path('fixture'),provider='deepseek',live=True)['status']=='closed_no_resend'


@pytest.mark.parametrize('provider,fake,expected',[('deepseek',False,'http://127.0.0.1:7890'),('local',False,None),('deepseek',True,None)])
def test_approved_proxy_is_cloud_only_and_never_overrides_fixture_transport(monkeypatch,provider,fake,expected):
    from types import SimpleNamespace
    import run_full_case_evaluation as cli
    calls=[]
    monkeypatch.setattr(cli,'post_one',lambda *args,**kwargs:calls.append((args,kwargs)))
    transport=object() if fake else None
    instance=SimpleNamespace(provider=provider,fake=fake,transport=transport,
        profile={'approved_loopback_proxy':'http://127.0.0.1:7890',provider:{'endpoint':'fixture-endpoint'}})
    cli.official_post_payload(instance,{'fixture':True},headers={},timeout=3)
    assert calls[0][1]['proxy']==expected and calls[0][1]['transport'] is transport
