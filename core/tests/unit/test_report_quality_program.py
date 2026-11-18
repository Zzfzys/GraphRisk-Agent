from copy import deepcopy
from datetime import date

import pytest

from test_report_pilot_execution import pilot, request, transport, good, review, write
from test_autonomous_tool_contracts import scope,index,registry
from test_autonomous_action_providers import profile
from graphrisk.agent import report_pilot_execution as channel
from graphrisk.agent import report_quality_program as program
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.autonomous_session import SessionStore
from graphrisk.agent.upgrade_contract import digest,file_hash
from graphrisk.agent.judgment_v3 import JudgmentProvider
from run_report_pilot import run_case


def new_batch(budget,store,parent,output,program_cfg,name):
    directory=output.parent/name
    directory.mkdir()
    cfg=deepcopy(parent)
    cfg.update(version=program.BATCH,campaign=name,program_id=program_cfg['program_id'],
               program_sha256=program_cfg['contract_sha256'],data_kind='real_development',kind='report')
    for case,row in cfg['cases'].items():row['session']=name+'-'+case
    cfg['contract_sha256']=digest({k:v for k,v in cfg.items() if k!='contract_sha256'})
    write(directory/'contract-private.json',cfg)
    store.campaign(name,cfg['contract_sha256'],cap_each=4,group_key='route5_supplement_v3')
    program.register_batch(budget,directory/'contract-private.json')
    return budget,store,cfg,directory


@pytest.fixture
def authorized(pilot):
    budget,store,old,output=pilot
    phase,packet,payload,key=request(pilot)
    attempt=budget.reserve(provider='deepseek',request_key=key,packet_sha=digest(payload),phase=phase,reserve_micro=2103296)
    budget.unknown(attempt,'provider_connection_unknown')
    channel.stop(budget,'batch','model_request_unknown_saved_do_not_retry')
    path=output/'program-private.json'
    with budget.transaction() as db:
        program.setup(db)
        unknowns=[channel.unknown_record(db,r[0]) for r in db.execute("SELECT attempt_id FROM model_attempts WHERE state='unknown' ORDER BY attempt_id")]
        baseline=program.old_rows(db,'quality-program')
    cfg={'version':program.VERSION,'program_id':'quality-program','parent_campaign':'batch','max_requests':30,
         'unknowns':unknowns,'baseline':baseline,'protected_files':{str(output/'contract-private.json'):file_hash(output/'contract-private.json')},
         'real_evidence_allowed':[r['evidence_sha'] for r in old['cases'].values()]}
    cfg['contract_sha256']=digest(cfg)
    write(path,cfg)
    program.register_program(budget,path,'User authorized finite autonomous improvement while retaining historical unknowns.')
    return pilot,cfg,path


def test_two_exact_unknowns_are_retained_while_real_generation_can_complete(authorized):
    parent,cfg,path=authorized
    budget,store,old,output=parent
    current=new_batch(budget,store,old,output,cfg,'dev1')
    posts=[]
    assert run_case(output,current[3],'C1',budget=budget,transport=transport([good()],posts))['attempts']==1
    assert review(current,'C1')['quality']=='Pass'
    with budget.transaction() as db:
        assert program.old_rows(db,'quality-program')==cfg['baseline']
        assert [channel.unknown_record(db,r['attempt_id']) for r in cfg['unknowns']]==cfg['unknowns']
        assert db.execute('SELECT closed_reason FROM report_pilot_grants').fetchone()==('model_request_unknown_saved_do_not_retry',)
    assert budget.snapshot()['unresolved_attempts']==2 and len(posts)==1


def test_new_completed_batch_may_follow_but_cannot_reopen_old_one(authorized):
    parent,cfg,path=authorized
    budget,store,old,output=parent
    current=new_batch(budget,store,old,output,cfg,'dev1')
    posts=[]
    mock=transport([good(),good()],posts)
    run_case(output,current[3],'C1',budget=budget,transport=mock)
    review(current,'C1')
    run_case(output,current[3],'C2',budget=budget,transport=mock)
    review(current,'C2')
    with budget.transaction() as db:
        with pytest.raises(ExecutionStop,match='batch_closed'):program.load_batch(db,'dev1')
    follow=new_batch(budget,store,old,output,cfg,'dev2')
    phase,packet,payload,key=request(follow)
    attempt=budget.reserve(provider='deepseek',request_key=key,packet_sha=digest(payload),phase=phase,reserve_micro=2103296)
    budget.settle(attempt,cost_micro=100,receipt_sha='f'*64,receipt_path='fixture',identity='["deepseek-flash","fixture-fp"]',usage={},result_code='ok')
    assert budget.snapshot()['total_attempts']==5


@pytest.mark.parametrize('change',['old_money','third_unknown','third_reserved','integrity','protected_file','allow_more_calls'])
def test_fresh_program_cannot_waive_or_delete_historical_records(authorized,change):
    parent,cfg,path=authorized
    budget,store,old,output=parent
    current=new_batch(budget,store,old,output,cfg,'dev1')
    phase,packet,payload,key=request(current)
    with budget.transaction() as db:
        if change=='old_money':db.execute('UPDATE execution_requests SET accounted=0 WHERE attempt_id=?',(cfg['unknowns'][0]['attempt_id'],))
        elif change in ('third_unknown','third_reserved'):
            db.execute('INSERT INTO model_attempts VALUES(?,?,?,?)',('third','deepseek','unknown' if change=='third_unknown' else 'reserved',1))
            db.execute('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES(?,?,?,?,?)',('third','third','pilot','third',1))
        elif change=='integrity':db.execute('UPDATE execution_requests SET integrity_stop=1')
    if change=='protected_file':
        (output/'contract-private.json').write_text('{}')
    elif change=='allow_more_calls':
        cfg['max_requests']=31
        cfg['contract_sha256']=digest({k:v for k,v in cfg.items() if k!='contract_sha256'})
        write(path,cfg)
    with pytest.raises(ExecutionStop):
        budget.reserve(provider='deepseek',request_key=key,packet_sha=digest(payload),phase=phase,reserve_micro=2103296)


def test_thirty_request_cap_is_shared_across_frozen_batches(authorized):
    parent,cfg,path=authorized
    budget,store,old,output=parent
    current=new_batch(budget,store,old,output,cfg,'dev1')
    phase,packet,payload,key=request(current)
    with budget.transaction() as db:
        for n in range(30):
            db.execute('INSERT INTO model_attempts VALUES(?,?,?,?)',(f'used-{n}','deepseek','settled',1))
            db.execute('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES(?,?,?,?,?)',(f'used-{n}',f'used-{n}',phase,digest(n),1))
    other=new_batch(budget,store,old,output,cfg,'dev2')
    phase,packet,payload,key=request(other)
    with pytest.raises(ExecutionStop,match='program_or_batch_request_cap'):
        budget.reserve(provider='deepseek',request_key=key,packet_sha=digest(payload),phase=phase,reserve_micro=2103296)


def test_unapproved_real_evidence_cannot_enter_quality_program(authorized):
    parent,cfg,path=authorized
    budget,store,old,output=parent
    changed=deepcopy(old)
    changed['cases']['C1']['evidence_sha']='0'*64
    with pytest.raises(ExecutionStop,match='summary_outside_authorized_scope'):
        new_batch(budget,store,changed,output,cfg,'wrong')


def test_operational_failure_closes_entire_program_but_quality_fail_only_closes_batch(authorized):
    parent,cfg,path=authorized
    budget,store,old,output=parent
    current=new_batch(budget,store,old,output,cfg,'dev1')
    channel.stop(budget,'dev1','two_consecutive_completed_case_failures')
    next_one=new_batch(budget,store,old,output,cfg,'dev2')
    channel.stop(budget,'dev2','model_request_unknown_saved_do_not_retry')
    with pytest.raises(ExecutionStop,match='program_closed'):
        new_batch(budget,store,old,output,cfg,'dev3')


def test_program_authorization_cannot_be_repeated_to_reset_allocation(authorized):
    parent,cfg,path=authorized
    budget,store,old,output=parent
    program.register_program(budget,path,'User authorized finite autonomous improvement while retaining historical unknowns.')
    with pytest.raises(ExecutionStop,match='not_resettable'):
        program.register_program(budget,path,'Another authorization without a new reviewed program.')


def test_known_mapper_fix_review_preserves_stop_and_count_and_does_not_waive_next_stop(authorized):
    parent,cfg,path=authorized
    budget,store,old,output=parent
    current=new_batch(budget,store,old,output,cfg,'dev1')
    phase,packet,payload,key=request(current)
    wrapper={'profile_sha256':digest(current[2]['profile']),'packets':[packet],'session':current[2]['cases']['C1']['session']}
    bad=good();bad['next_check']['text']=['history_stats','card_attempts']
    posts=[]
    client=JudgmentProvider('deepseek',profile=current[2]['profile'],budget=budget,campaign=wrapper,
        campaign_sha=digest(wrapper),phase=phase,output=current[3]/'receipts',runtime=output,
        transport=transport([bad],posts))
    assert client.generate('0')['code']=='invalid_model_action'
    channel.stop(budget,'dev1','report_pilot_execution_guard_failed')
    xml=output/'tests.xml'
    xml.write_text('<testsuites><testsuite tests="2" failures="0" errors="0" skipped="0"/></testsuites>')
    evidence=output/'mapping-fix-review.json'
    write(evidence,{'cause':'next_check_list_quality_mapping_type_error','reviewer':'assistant_development',
                    'test_evidence':{'path':str(xml),'sha256':file_hash(xml)}})
    before=budget.snapshot()
    program.register_mapping_fix_review(budget,'quality-program',evidence)
    assert budget.snapshot()==before and len(posts)==1
    with budget.transaction() as db:
        assert db.execute('SELECT closed_reason FROM report_quality_batches WHERE campaign_id="dev1"').fetchone()==('report_pilot_execution_guard_failed',)
        with pytest.raises(ExecutionStop,match='batch_closed'):program.load_batch(db,'dev1')
    follow=new_batch(budget,store,old,output,cfg,'dev2')
    channel.stop(budget,'dev2','report_pilot_execution_guard_failed')
    with pytest.raises(ExecutionStop,match='program_closed'):
        new_batch(budget,store,old,output,cfg,'dev3')


def extension(authorized, **changes):
    parent,original,path=authorized
    budget,store,old,output=parent
    with budget.transaction() as db:used=program.used_requests(db,original['program_id'])
    cfg={'version':'report-quality-additive-extension-v1','program_id':original['program_id'],
        'program_sha256':original['contract_sha256'],'original_max_requests':30,'additional_max_requests':20,
        'cumulative_max_requests':50,'authorized_at_requests':used,
        'scope':'two_previously_approved_real_summaries_and_public_synthetic_boundary_tasks',
        'supplement_group':'route5_supplement_v3','supplement_total_max_requests':100,'overall_max_cny':100,
        'no_reset_no_reopen_no_unknown_retry':True,**changes}
    cfg['contract_sha256']=digest(cfg)
    target=output/'extension.json';write(target,cfg)
    return target


def test_additive_extension_keeps_original_counters_stops_and_unknowns(authorized):
    parent,cfg,path=authorized;budget,store,old,output=parent
    target=extension(authorized);before=budget.snapshot();original_bytes=path.read_bytes()
    program.register_extension(budget,path,target,'Allow at most 20 additional requests.')
    program.register_extension(budget,path,target,'Allow at most 20 additional requests.')
    assert budget.snapshot()==before and path.read_bytes()==original_bytes
    current=new_batch(budget,store,old,output,cfg,'dev1')
    phase,packet,payload,key=request(current)
    with budget.transaction() as db:
        for n in range(49):
            db.execute('INSERT INTO model_attempts VALUES(?,?,?,?)',(f'used-{n}','deepseek','settled',1))
            db.execute('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES(?,?,?,?,?)',(f'used-{n}',f'used-{n}',phase,digest(n),1))
        assert program.effective_cap(db,cfg)==50
    other=new_batch(budget,store,old,output,cfg,'dev2')
    phase,packet,payload,key=request(other)
    attempt=budget.reserve(provider='deepseek',request_key=key,packet_sha=digest(payload),phase=phase,reserve_micro=2103296)
    budget.settle(attempt,cost_micro=100,receipt_sha='f'*64,receipt_path='fixture',identity='["deepseek-flash","fixture-fp"]',usage={},result_code='ok')
    other=new_batch(budget,store,old,output,cfg,'dev3');phase,packet,payload,key=request(other)
    with pytest.raises(ExecutionStop,match='program_or_batch_request_cap'):
        budget.reserve(provider='deepseek',request_key=key,packet_sha=digest(payload),phase=phase,reserve_micro=2103296)
    with budget.transaction() as db:
        assert [channel.unknown_record(db,r['attempt_id']) for r in cfg['unknowns']]==cfg['unknowns']
    with pytest.raises(ExecutionStop,match='not_resettable'):
        program.register_extension(budget,path,target,'Reset count or new approval.')


@pytest.mark.parametrize('changes',[{'cumulative_max_requests':51},{'program_id':'other'},
    {'overall_max_cny':101},{'no_reset_no_reopen_no_unknown_retry':False},{'authorized_at_requests':1}])
def test_extension_rejects_wrong_binding_or_stale_count(authorized,changes):
    parent,cfg,path=authorized;budget=parent[0];target=extension(authorized,**changes)
    with pytest.raises(ExecutionStop):program.register_extension(budget,path,target,'User approved 20 additional.')


def test_extension_cannot_waive_stop_or_mutated_addendum(authorized):
    parent,cfg,path=authorized;budget,store,old,output=parent;target=extension(authorized)
    program.register_extension(budget,path,target,'User approved 20 additional.')
    target.write_text('{}')
    with budget.transaction() as db:
        with pytest.raises(ExecutionStop,match='extension_file_changed'):program.effective_cap(db,cfg)
    current=new_batch(budget,store,old,output,cfg,'dev1')
    channel.stop(budget,'dev1','model_request_unknown_saved_do_not_retry')
    with pytest.raises(ExecutionStop,match='program_closed'):
        program.register_extension(budget,path,target,'User approved 20 additional.')
