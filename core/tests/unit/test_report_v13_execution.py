from copy import deepcopy
from datetime import date
import pytest
from test_report_ieee_validation import stopped,authorized,pilot,scope,index,registry,profile
from test_report_pilot_execution import write
from graphrisk.agent import report_v13_execution as execution,report_ieee_validation as ieee
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.upgrade_contract import digest,file_hash


@pytest.fixture
def trial(stopped):
    auth,allocation,allocation_path=stopped;parent,original,program_path=auth
    budget,store,old,output=parent;ieee.register(budget,program_path,allocation_path,'Original +50 authorization.')
    packet={'logical_key':'0','view':{'reports':[]}};packetpath=output/'packet.json';write(packetpath,packet)
    cfg={'version':'v13-existing-budget-scope-v1','trial_id':'v13-test','program_id':original['program_id'],
        'program_path':str(program_path),'program_file_sha256':file_hash(program_path),
        'existing_grant_sha256':allocation['contract_sha256'],'group':ieee.GROUP,'group_used_at_authorization':0,
        'max_new_requests':14,'minimum_retained_requests':21,'overall_max_cny':100,'pause_cny':80,
        'protected_files':{str(packetpath):file_hash(packetpath)},'packet_hashes':{'calibration':[digest(packet)],'report':[]}}
    cfg['contract_sha256']=digest(cfg);path=output/'v13-trial.json';write(path,cfg)
    execution.register_trial(budget,path,'Try v13 using existing remaining allocation.')
    directory=output/'v13-test';directory.mkdir();payload={'payload':'fixed'}
    item={'session':'v13-test-C1','session_manifest':{'packet':digest(packet)},'packet_path':str(packetpath),
        'packet_sha256':digest(packet),'payload_sha256':digest(payload)}
    batch={'version':'report-v13-batch-v1','trial_id':'v13-test','trial_sha256':cfg['contract_sha256'],
        'program_id':original['program_id'],'source_root':old['source_root'],'source':old['source'],
        'files':{str(packetpath):file_hash(packetpath)},'profile':old['profile'],'cases':{'C1':item},
        'campaign':'v13-test','kind':'calibration','pricing_date':date.today().isoformat()}
    batch['contract_sha256']=digest(batch);write(directory/'contract-private.json',batch)
    store.campaign('v13-test',batch['contract_sha256'],cap_each=3,group_key=ieee.GROUP)
    execution.register_batch(budget,directory/'contract-private.json')
    store.open(item['session'],'v13-test',item['session_manifest'],'deepseek',calls=1,seconds=600)
    wrapper={'profile_sha256':digest(batch['profile']),'packets':[packet],'session':item['session']}
    phase=store.register_packet(item['session'],0,digest(wrapper),'deepseek')
    return budget,store,cfg,path,batch,phase,digest([digest(wrapper),'deepseek','0']),digest(payload),directory


def test_scope_addendum_keeps_fifty_grant_and_counters_unchanged(trial):
    budget,_,cfg,path,*_=trial;before=budget.snapshot()
    execution.register_trial(budget,path,'Try v13 using existing remaining allocation.')
    assert budget.snapshot()==before
    with pytest.raises(ExecutionStop,match='not_resettable'):execution.register_trial(budget,path,'Reset allowance.')


def test_exact_new_payload_required_and_request_never_repeats(trial):
    budget,_,_,_,_,phase,key,payload,_=trial;before=budget.snapshot()
    with pytest.raises(ExecutionStop,match='exact_payload'):
        budget.reserve(provider='deepseek',request_key=key,packet_sha='f'*64,phase=phase,reserve_micro=2103296)
    assert budget.snapshot()==before
    attempt=budget.reserve(provider='deepseek',request_key=key,packet_sha=payload,phase=phase,reserve_micro=2103296)
    budget.unknown(attempt,'provider_connection_unknown')
    with pytest.raises(ExecutionStop,match='new_unresolved'):
        budget.reserve(provider='deepseek',request_key='different',packet_sha=payload,phase=phase,reserve_micro=2103296)


def test_new_scope_cannot_expand_budget_or_reset_used_counter(trial):
    budget,_,cfg,path,*_=trial;cfg=deepcopy(cfg);cfg['max_new_requests']=15
    cfg['contract_sha256']=digest({k:v for k,v in cfg.items() if k!='contract_sha256'});write(path,cfg)
    with budget.transaction() as db:
        with pytest.raises(ExecutionStop,match='trial_binding_changed'):execution.grant(db,'v13-test')


def test_source_change_stops_before_reservation(trial):
    budget,_,_,_,batch,phase,key,payload,_=trial
    from pathlib import Path
    target=Path(batch['source_root'])/'src'/'changed.py';target.parent.mkdir(exist_ok=True);target.write_text('changed=True')
    before=budget.snapshot()
    with pytest.raises(ExecutionStop,match='source_changed'):
        budget.reserve(provider='deepseek',request_key=key,packet_sha=payload,phase=phase,reserve_micro=2103296)
    assert budget.snapshot()==before
