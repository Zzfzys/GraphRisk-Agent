from copy import deepcopy
from pathlib import Path

import pytest

import run_full_case_evaluation as cli
from test_frozen_daily_tools import envelope
from test_full_case_execution import prepare,start,write,index,registry,profile,daily_scope,scope
from test_daily_autonomous_loop import make_daily
from graphrisk.agent.autonomous_loop import BoundedInvestigation
from graphrisk.agent import full_case_execution as formal
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.policy_retrieval import PolicyRetriever,write_policy_index
from graphrisk.agent.upgrade_contract import canonical,digest,file_hash


def test_actual_cli_batch_restores_multiple_dispatches_and_cached_results(tmp_path,daily_scope,index,registry):
    retriever=PolicyRetriever(write_policy_index(index,tmp_path/'index.json'))
    batch=cli.tool_batch_for(daily_scope,envelope(daily_scope,index,registry),registry,retriever,lambda:None)
    rows,_=batch([],[{'tool':'get_transaction','arguments':{}}])
    rows,events=batch(rows,[{'tool':'get_transaction_features','arguments':{}},{'tool':'get_risk_score','arguments':{}}])
    assert len(rows)==3 and all(not event['cached'] for event in events)
    again,events=batch(rows,[{'tool':'get_risk_score','arguments':{}}])
    assert again==rows and events==[{'tool':'get_risk_score','status':'ok','cached':True}]


def test_actual_cli_batch_runs_the_full_bounded_agent_to_current_report(tmp_path,daily_scope,index,registry,profile):
    _,budget,posts,_,options=make_daily(tmp_path,daily_scope,index,registry,profile,full_path=True)
    retriever=PolicyRetriever(write_policy_index(index,tmp_path/'index.json'))
    options['tool_batch']=cli.tool_batch_for(daily_scope,envelope(daily_scope,index,registry),registry,retriever,lambda:None)
    engine=BoundedInvestigation(**options);state=engine.run()
    assert state['status']=='verified_report' and state['model_calls']==5 and len(posts)==5
    assert state['tool_requests']==6 and len(state['first_drafts'])==1
    assert engine.run()==state and len(posts)==5


def corrected_registration(tmp_path,daily_scope,index,registry,profile):
    budget,posts,options,old,old_path,note=prepare(tmp_path,daily_scope,index,registry,profile)
    engine=start(budget,options,old,old_path,note,'deepseek')
    engine.node('bootstrap')({});engine.node('decide')({})
    assert len(posts)==1 and engine.state['model_calls']==1 and not engine.state['first_drafts']
    with budget.transaction() as db:
        newer=formal.check_history(db,old)
        aborted={'execution_sha256':file_hash(old_path),'technical_abort':True,
            'reason':'restore_requires_empty_executor','whole_report_drafts':0,'unknown':0,
            'new_attempts':1,'attempts_sha256':digest(newer)}
        abort_path=write(tmp_path/'abort.json',aborted)
        db.execute('CREATE TABLE formal_case_aborts(campaign_id TEXT PRIMARY KEY,manifest_path TEXT,manifest_sha TEXT)')
        db.execute('INSERT INTO formal_case_aborts VALUES(?,?,?)',(old['campaign'],abort_path,file_hash(Path(abort_path))))
        db.execute('UPDATE formal_case_grants SET closed=1 WHERE campaign_id=?',(old['campaign'],))
    cfg=deepcopy(old);cfg['campaign']=old['campaign']+'-executor-fix'
    for episode in cfg['episodes']:
        episode['session_id']+='-executor-fix';episode['directory']+='-executor-fix'
    cfg['predecessor']={'campaign':old['campaign'],'contract_path':str(old_path),
        'contract_sha256':file_hash(old_path),'abort_manifest_path':abort_path,
        'abort_manifest_sha256':file_hash(Path(abort_path)),'used_requests':1}
    cfg['files'].update({str(old_path):file_hash(old_path),abort_path:file_hash(Path(abort_path))})
    path=tmp_path/'corrected.json';write(path,cfg)
    new_note=formal.read(note);new_note['execution_sha256']=file_hash(path)
    new_note['question_item_id']='fixture-corrected-protocol-approval'
    approved=tmp_path/'corrected-approval.json';write(approved,new_note)
    return budget,posts,options,cfg,path,approved


def test_corrected_registration_retains_old_spend_and_cannot_reset_the_allocation(tmp_path,daily_scope,index,registry,profile):
    budget,posts,options,cfg,path,approved=corrected_registration(tmp_path,daily_scope,index,registry,profile)
    formal.register(budget,path,approved)
    with budget.transaction() as db:
        assert len(formal.check_history(db,cfg))==1
        assert db.execute('SELECT closed FROM formal_case_grants WHERE campaign_id=?',('formal-fixture',)).fetchone()==(1,)
        assert db.execute('SELECT count(*) FROM formal_case_grants').fetchone()==(2,)
    assert budget.snapshot()['total_attempts']==1470 and len(posts)==1
    options['store'].campaign(cfg['campaign'],file_hash(path),cap_each=325,group_key=formal.GROUP)
    with budget.transaction() as db:
        formal.campaign_guard(db,cfg['campaign'],file_hash(path),325)
    episode=cfg['episodes'][0]
    deadline=options['store'].open(episode['session_id'],cfg['campaign'],formal.session_manifest(file_hash(path),episode),'deepseek')
    retriever=PolicyRetriever(write_policy_index(index,tmp_path/'corrected-index.json'))
    batch=cli.tool_batch_for(daily_scope,envelope(daily_scope,index,registry),registry,retriever,lambda:None)
    engine=BoundedInvestigation(**{**options,'session':episode['session_id'],
        'directory':episode['directory'],'deadline':deadline,'tool_batch':batch})
    state=engine.run()
    assert state['status']=='verified_report' and state['model_calls']==5 and len(posts)==6
    assert budget.snapshot()['total_attempts']==1475
    with budget.transaction() as db:assert len(formal.check_history(db,cfg))==6


@pytest.mark.parametrize('change',['old_spend','material','unapproved'])
def test_corrected_protocol_rejects_erased_spend_changed_material_or_missing_approval(tmp_path,daily_scope,index,registry,profile,change):
    budget,posts,options,cfg,path,approved=corrected_registration(tmp_path,daily_scope,index,registry,profile)
    if change=='old_spend':cfg['predecessor']['used_requests']=0
    elif change=='material':
        for episode in cfg['episodes']:
            if not episode['preblocked']:episode['scope_sha256']='f'*64
    else:
        note=formal.read(approved);note['outbound_authorized']=False;write(approved,note)
    if change!='unapproved':
        write(path,cfg);note=formal.read(approved);note['execution_sha256']=file_hash(path);write(approved,note)
    with pytest.raises(ExecutionStop):formal.register(budget,path,approved)
    assert budget.snapshot()['total_attempts']==1470 and len(posts)==1
