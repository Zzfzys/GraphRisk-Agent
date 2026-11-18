from copy import deepcopy
import pytest
from test_report_quality_program import authorized,new_batch,extension
from test_report_pilot_execution import pilot,request,write
from test_autonomous_tool_contracts import scope,index,registry
from test_autonomous_action_providers import profile
from graphrisk.agent import report_ieee_validation as ieee,report_quality_program as program
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.upgrade_contract import digest,file_hash


@pytest.fixture
def stopped(authorized):
    parent,original,path=authorized;budget,store,old,output=parent
    program.register_extension(budget,path,extension(authorized),'Approved prior 20.')
    current=new_batch(budget,store,old,output,original,'previous')
    phase,_,_,_=request(current)
    with budget.transaction() as db:
        for n in range(50):
            db.execute('INSERT INTO model_attempts VALUES(?,?,?,?)',(f'used-{n}','deepseek','settled',1))
            db.execute('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES(?,?,?,?,?)',
                (f'used-{n}',f'used-{n}',phase,digest(n),1))
    cfg={'version':'report-ieee-v12-additive-grant-v1','program_id':original['program_id'],
        'program_sha256':original['contract_sha256'],'prior_cap':50,'authorized_at_requests':50,
        'additional_requests':50,'cumulative_cap':100,'group':ieee.GROUP,'overall_max_cny':100,'pause_cny':80,
        'no_reset_no_reopen_no_unknown_retry':True,'scope':'frozen_ieee_v12_first_drafts_and_bound_judges',
        'real_evidence_allowed':list(dict.fromkeys([c['evidence_sha'] for c in old['cases'].values()])) ,
        'generation_files':{str(path):file_hash(path)},'protected_files':{str(path):file_hash(path)},
        'report_profile_sha256':digest(old['profile'])}
    while len(cfg['real_evidence_allowed'])<6:cfg['real_evidence_allowed'].append(digest(len(cfg['real_evidence_allowed'])))
    cfg['contract_sha256']=digest(cfg);target=output/'new-grant.json';write(target,cfg)
    return authorized,cfg,target


def real_batch(stopped,name):
    authorized,grant,target=stopped;parent,original,path=authorized;budget,store,old,output=parent
    directory=output.parent/name;directory.mkdir();cfg=deepcopy(old)
    cfg.update(version=program.BATCH,campaign=name,program_id=original['program_id'],program_sha256=original['contract_sha256'],
        kind='report',data_kind='real_validation',repair_allowed=False,ieee_v12_grant_sha256=grant['contract_sha256'])
    for case in cfg['cases'].values():case['session']=name+'-'+case['session']
    cfg['contract_sha256']=digest({k:v for k,v in cfg.items() if k!='contract_sha256'})
    write(directory/'contract-private.json',cfg);store.campaign(name,cfg['contract_sha256'],cap_each=4,group_key=ieee.GROUP)
    program.register_batch(budget,directory/'contract-private.json')
    return budget,store,cfg,directory


def test_add_fifty_does_not_reset_old_money_unknowns_or_cap(stopped):
    auth,cfg,target=stopped;parent,original,path=auth;budget=parent[0];before=budget.snapshot();old=path.read_bytes()
    ieee.register(budget,path,target,'Test real IEEE v12 then add 50 calls.')
    ieee.register(budget,path,target,'Test real IEEE v12 then add 50 calls.')
    assert budget.snapshot()==before and old==path.read_bytes()
    with budget.transaction() as db:assert program.effective_cap(db,original)==100
    with pytest.raises(ExecutionStop,match='not_resettable'):ieee.register(budget,path,target,'Different approval.')


@pytest.mark.parametrize('field,value',[('additional_requests',51),('cumulative_cap',101),('overall_max_cny',101),('pause_cny',81)])
def test_additive_grant_rejects_changed_bounds(stopped,field,value):
    auth,cfg,target=stopped;cfg[field]=value;cfg['contract_sha256']=digest({k:v for k,v in cfg.items() if k!='contract_sha256'});write(target,cfg)
    with pytest.raises(ExecutionStop,match='binding_invalid'):ieee.register(auth[0][0],auth[2],target,'Approved 50.')


def test_new_group_enforces_fifty_at_reservation_and_rejects_fifty_first(stopped):
    auth,grant,target=stopped;budget=auth[0][0];ieee.register(budget,auth[2],target,'Approved 50.')
    current=real_batch(stopped,'real-one');phase,packet,payload,key=request(current)
    with budget.transaction() as db:
        for n in range(49):
            db.execute('INSERT INTO model_attempts VALUES(?,?,?,?)',(f'new-{n}','deepseek','settled',1))
            db.execute('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES(?,?,?,?,?)',
                (f'new-{n}',f'new-{n}',phase,digest(n),1))
    current=real_batch(stopped,'real-two');phase,packet,payload,key=request(current)
    attempt=budget.reserve(provider='deepseek',request_key=key,packet_sha=digest(payload),phase=phase,reserve_micro=2103296)
    budget.settle(attempt,cost_micro=100,receipt_sha='f'*64,receipt_path='fixture',identity='["deepseek-flash","fixture-fp"]',usage={},result_code='ok')
    current=real_batch(stopped,'real-three');phase,packet,payload,key=request(current)
    with pytest.raises(ExecutionStop,match='fifty_request_cap'):
        budget.reserve(provider='deepseek',request_key=key,packet_sha=digest(payload),phase=phase,reserve_micro=2103296)


def test_real_validation_cannot_repair_change_profile_or_send_unlisted_evidence(stopped):
    auth,grant,target=stopped;budget=auth[0][0];ieee.register(budget,auth[2],target,'Approved 50.')
    current=real_batch(stopped,'real-one');cfg=current[2]
    with budget.transaction() as db:
        original=auth[1]
        for change in ({'repair_allowed':True},{'profile':{}},{'cases':{'C1':{**cfg['cases']['C1'],'evidence_sha':'x'}}}):
            with pytest.raises(ExecutionStop):ieee.validate_batch(db,original,{**cfg,**change})


def test_new_grant_cannot_waive_new_unknown_or_allow_old_group(stopped):
    auth,grant,target=stopped;budget=auth[0][0];ieee.register(budget,auth[2],target,'Approved 50.')
    with budget.transaction() as db:
        with pytest.raises(ExecutionStop,match='legacy_group_remains_closed'):
            ieee.guard_allocation(db,{'program_id':auth[1]['program_id']})
        db.execute('INSERT INTO model_attempts VALUES(?,?,?,?)',('new-unknown','deepseek','unknown',1))
        db.execute('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES(?,?,?,?,?)',('new-unknown','new-unknown','x','x',1))
    with pytest.raises(ExecutionStop,match='new_unresolved_stop'):ieee.register(budget,auth[2],target,'Approved 50.')


def test_real_validation_first_failure_is_saved_once_without_repair(stopped):
    from test_report_pilot_execution import transport,good
    from run_report_pilot import run_case
    auth,grant,target=stopped;budget=auth[0][0];ieee.register(budget,auth[2],target,'Approved 50.')
    current=real_batch(stopped,'real-one');posts=[];bad=good();bad['risk_level']='unsupported'
    result=run_case(current[3],current[3],'C1',budget=budget,transport=transport([bad],posts))
    assert result['attempts']==1 and result['guard_report_formed'] is False and len(posts)==1
    assert run_case(current[3],current[3],'C1',budget=budget,transport=transport([],posts))['status']=='saved_case_no_new_request'
