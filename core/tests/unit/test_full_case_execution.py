"""Real reservation/checkpoint flow against a synthetic 1469-attempt history."""
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest

from test_autonomous_tool_contracts import index,registry,scope
from test_autonomous_action_providers import profile
from test_daily_narrative import daily_scope
from test_daily_autonomous_loop import make_daily
from graphrisk.agent import full_case_execution as formal
from graphrisk.agent import judgment_v12_daily_quotes as quotes
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.autonomous_loop import BoundedInvestigation
from graphrisk.agent.upgrade_contract import canonical,digest,file_hash

ROOT=Path(__file__).resolve().parents[2]


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(canonical(value),encoding='utf-8')
    return str(path)


def prepare(tmp_path,daily_scope,index,registry,profile,*,provider='deepseek',timeout=False,approved=True):
    _,budget,posts,_,options=make_daily(tmp_path,daily_scope,index,registry,profile,
        provider=provider,timeout=timeout,full_path=True)
    options['profile']['deepseek']['pricing_verified_date']=date.today().isoformat()
    with budget.transaction() as db:
        for number in range(1469):
            name='history-'+str(number);p='deepseek' if number<1446 else 'local'
            state='unknown' if number<3 else 'settled'
            db.execute('INSERT INTO model_attempts VALUES(?,?,?,?)',(name,p,state,int(p=='deepseek')))
            db.execute('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted,integrity_stop) '
                'VALUES(?,?,?,?,?,?)',(name,name,'history','1'*64,int(p=='deepseek'),int(number==3)))
        db.execute('INSERT INTO provider_identity VALUES(?,?)',('deepseek',canonical(['deepseek-flash','old-fixture-fp'])))
        history=digest(formal.history_rows(db))
    folder=tmp_path/'formal'
    first=[{'case_alias':daily_scope.case_alias if i==0 else f'{i:032x}',
        'transaction_id':daily_scope.transaction_id if i==0 else 'fixture-'+str(i),
        'group':'natural' if i<30 else 'diagnostic','event_second':daily_scope.target_event_second} for i in range(60)]
    registration={'cases':first,'repeat_ids':[row['transaction_id'] for row in first[:5]],
        'human_read_ids':[row['transaction_id'] for row in first[:5]]}
    scope_path=write(folder/'scope.json',daily_scope.model_dump(mode='json'))
    episodes=[]
    for p in ('deepseek','local'):
        for i,original in enumerate(first+first[:5]):
            name='formal-'+p+'-'+str(i)
            episodes.append({'session_id':name,'provider':p,'repeat':i>=60,
                'group':original['group'],'case_alias':original['case_alias'],
                'preblocked':i not in (0,60),'scope_sha256':daily_scope.binding_sha256 if i in (0,60) else None,
                'scope_path':scope_path if i in (0,60) else None,'directory':str(folder/'episodes'/name)})
    count=write(folder/'count.json',{'count_scope_approved':True,'outbound_authorized':False,
        'new_attempt_caps':{'deepseek':325,'local':325,'total':650},'absolute_attempt_caps':formal.CAPS})
    profile_path=write(folder/'profile.json',options['profile'])
    registration_path=write(folder/'registration.json',registration)
    index_path=write(folder/'index.json',index.model_dump(mode='json'))
    source=folder/'source';(source/'src').mkdir(parents=True);(source/'scripts').mkdir()
    cfg={'version':formal.VERSION,'fixture':True,'campaign':'formal-fixture','limits':formal.LIMITS,
        'report_contract':quotes.CONTRACT,'before_counts':formal.BEFORE,'absolute_caps':formal.CAPS,
        'new_per_provider':325,'new_total':650,'money_cap_micro_cny':100_000_000,'pause_at_micro_cny':80_000_000,
        'paid_judge_calls':0,'fixed_evidence_ablation_calls':0,'source_root':str(source),'source_inventory':{},
        'episodes':episodes,'human_read_aliases':[row['case_alias'] for row in first[:5]],
        'case_registration_path':registration_path,'count_approval_path':count,'profile_path':profile_path,
        'policy_index_path':index_path,'policy_requirements_path':str(ROOT/'configs/agent_investigation_policy_requirements.json'),
        'history_sha256':history,'identities':{'deepseek':canonical(['deepseek-flash','fixture-fp']),
            'local':canonical(['qwen3:4b',profile['local']['digest'],profile['local']['num_ctx'],False])}}
    cfg['files']={path:file_hash(Path(path)) for path in (scope_path,count,profile_path,registration_path,index_path,cfg['policy_requirements_path'])}
    config=folder/'execution.json';write(config,cfg)
    note=folder/'approval.json';write(note,{'outbound_authorized':approved,'execution_sha256':file_hash(config),
        'max_new_requests':650,'providers':['deepseek','local'],'question_item_id':'fixture-only-not-real-approval',
        'user_answer':'fixture-only'})
    return budget,posts,options,cfg,config,note


def start(budget,options,cfg,config,note,provider):
    formal.register(budget,config,note)
    store=options['store'];sha=file_hash(config)
    store.campaign(cfg['campaign'],sha,cap_each=325,group_key=formal.GROUP)
    episode=next(row for row in cfg['episodes'] if row['provider']==provider and not row['repeat'])
    deadline=store.open(episode['session_id'],cfg['campaign'],formal.session_manifest(sha,episode),provider)
    return BoundedInvestigation(**{**options,'session':episode['session_id'],'directory':episode['directory'],'deadline':deadline})


@pytest.mark.parametrize('provider',['deepseek','local'])
def test_both_providers_use_only_the_new_bound_grant_and_keep_old_failures(tmp_path,daily_scope,index,registry,profile,provider):
    budget,posts,options,cfg,config,note=prepare(tmp_path,daily_scope,index,registry,profile,provider=provider)
    engine=start(budget,options,cfg,config,note,provider);result=engine.run()
    assert result['status']=='verified_report' and result['model_calls']==5 and len(posts)==5
    assert budget.snapshot()['total_attempts']==1474
    assert budget.snapshot()['unresolved_attempts']==3 and budget.snapshot()['integrity_stops']==1
    assert engine.run()==result and len(posts)==5
    with budget.transaction() as db:
        assert digest(formal.history_rows(db)[:1469])==cfg['history_sha256']


def test_count_approval_alone_cannot_register_or_reserve(tmp_path,daily_scope,index,registry,profile):
    budget,posts,options,cfg,config,note=prepare(tmp_path,daily_scope,index,registry,profile,approved=False)
    with pytest.raises(ExecutionStop,match='specific_outbound_approval_required'):formal.register(budget,config,note)
    with pytest.raises(ExecutionStop,match='specific_outbound_approval_required'):
        options['store'].campaign(cfg['campaign'],file_hash(config),cap_each=325,group_key=formal.GROUP)
    assert not posts and budget.snapshot()['total_attempts']==1469


@pytest.mark.parametrize('change',['extra_case','different_pair','more_calls','more_money','paid_judge','different_reading'])
def test_registration_rejects_scope_growth(tmp_path,daily_scope,index,registry,profile,change):
    budget,posts,_,cfg,config,note=prepare(tmp_path,daily_scope,index,registry,profile)
    changed=deepcopy(cfg)
    if change=='extra_case':changed['episodes'].append(changed['episodes'][0])
    elif change=='different_pair':changed['episodes'][-1]['case_alias']='f'*32
    elif change=='more_calls':changed['new_total']=651
    elif change=='more_money':changed['money_cap_micro_cny']=101_000_000
    elif change=='paid_judge':changed['paid_judge_calls']=1
    else:changed['human_read_aliases']=list(reversed(changed['human_read_aliases']))
    with pytest.raises(ExecutionStop):formal.check_contract(changed,mode='fixture')
    assert not posts and budget.snapshot()['total_attempts']==1469


def test_unregistered_and_preblocked_sessions_cannot_be_opened(tmp_path,daily_scope,index,registry,profile):
    budget,posts,options,cfg,config,note=prepare(tmp_path,daily_scope,index,registry,profile)
    formal.register(budget,config,note);store=options['store']
    store.campaign(cfg['campaign'],file_hash(config),cap_each=325,group_key=formal.GROUP)
    for name in ('not-registered','formal-deepseek-1'):
        with pytest.raises(ExecutionStop,match='episode_not_executable'):
            store.open(name,cfg['campaign'],{},'deepseek')
    assert not posts and budget.snapshot()['total_attempts']==1469


def test_changed_material_blocks_before_post(tmp_path,daily_scope,index,registry,profile):
    budget,posts,options,cfg,config,note=prepare(tmp_path,daily_scope,index,registry,profile)
    engine=start(budget,options,cfg,config,note,'deepseek')
    Path(cfg['profile_path']).write_text('{}',encoding='utf-8')
    result=engine.run()
    assert not posts and result['report'] is None and budget.snapshot()['total_attempts']==1469


def test_new_unknown_is_not_hidden_among_the_old_three(tmp_path,daily_scope,index,registry,profile):
    budget,posts,options,cfg,config,note=prepare(tmp_path,daily_scope,index,registry,profile,timeout=True)
    engine=start(budget,options,cfg,config,note,'deepseek');result=engine.run()
    assert len(posts)==1 and result['report'] is None and budget.snapshot()['unresolved_attempts']==4
    assert engine.run()==result and len(posts)==1
    with budget.transaction() as db:
        with pytest.raises(ExecutionStop,match='prior_unresolved_or_integrity_stop'):formal.check_history(db,cfg)


def test_fixture_authorization_cannot_be_live(tmp_path,daily_scope,index,registry,profile):
    _,_,_,cfg,_,_=prepare(tmp_path,daily_scope,index,registry,profile)
    with pytest.raises(ExecutionStop,match='fixture_cannot_be_live'):formal.check_contract(cfg,mode='live')


@pytest.mark.parametrize('change',['destination','context','memory'])
def test_provider_profile_cannot_change_destination_or_lower_guards(tmp_path,daily_scope,index,registry,profile,change):
    _,_,_,cfg,_,_=prepare(tmp_path,daily_scope,index,registry,profile)
    path=Path(cfg['profile_path']);value=formal.read(path)
    if change=='destination':value['deepseek']['endpoint']='https://example.invalid/chat/completions'
    elif change=='context':value['local']['num_ctx']=16384
    else:value['local']['running_ram_min_gib']=1
    write(path,value);cfg['files'][str(path)]=file_hash(path)
    with pytest.raises(ValueError):formal.check_contract(cfg,mode='fixture')
