from copy import deepcopy
from pathlib import Path
import json,httpx,pytest
from test_report_paired_execution import paired,request
from test_report_ieee_validation import stopped,authorized,pilot,scope,index,registry,profile
from test_report_pilot_execution import write
from test_autonomous_action_providers import cloud_body
from graphrisk.agent import judgment_v13_1 as module,report_paired_execution as execution,report_ieee_validation as ieee
from graphrisk.agent.autonomous_tools import InvestigationScope
from graphrisk.agent.autonomous_loop import restore_observations
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.upgrade_contract import canonical,digest,file_hash

@pytest.fixture
def single(paired,registry):
    budget,store,old,_,directory=paired;cfg=deepcopy(old)
    cfg.update(version=execution.REVIEW,campaign='one-human-review',review_only=True,new_quality_target_claim=False,
        max_new_requests=1,cases={},order=['N1-v13_1'])
    item=deepcopy(old['cases']['N1-v12']);raw=json.loads(Path(item['raw_path']).read_text(encoding='utf-8'))
    current=InvestigationScope.model_validate_json(canonical(raw['scope']),strict=True)
    packet=module.packet_for(current,restore_observations(current,raw['observations']),registry,cfg['profile'])
    path=directory/'single-packet.json';write(path,packet)
    formatter=object.__new__(module.Provider);formatter.provider,formatter.profile='deepseek',cfg['profile']
    item.update(version='v13_1',packet_path=str(path),packet_sha256=digest(packet),payload_sha256=digest(formatter.payload(packet)),
        session='one-review-N1',session_manifest={'case':'N1','packet':digest(packet)})
    cfg['cases']['N1-v13_1']=item;cfg['files'][str(path)]=file_hash(path)
    cfg.pop('contract_sha256');cfg['contract_sha256']=digest(cfg);contract=directory/'single-contract.json';write(contract,cfg)
    store.campaign(cfg['campaign'],cfg['contract_sha256'],cap_each=1,group_key=ieee.GROUP)
    execution.register(budget,contract,'Exactly one human-review first draft, no retries or repairs.')
    return budget,store,cfg,contract,directory

@pytest.mark.parametrize('change',[{'max_new_requests':2},{'max_new_requests':True},{'repairs':1},
    {'automatic_retries':1},{'judge_calls':1},{'review_only':False},{'new_quality_target_claim':True}])
def test_one_review_scope_cannot_expand(single,change):
    cfg=deepcopy(single[2]);cfg.update(change);cfg.pop('contract_sha256');cfg['contract_sha256']=digest(cfg)
    with pytest.raises(ExecutionStop):execution.validate_files(cfg)

def test_single_exact_receipt_and_no_second_paid_request(single):
    budget,_,cfg,_,directory=single;posts=[];before=budget.snapshot()['total_attempts']
    phase,wrapper,key,payload=request(single)
    def handler(req):posts.append(json.loads(req.content));return httpx.Response(200,json=cloud_body({'invalid':'keep first draft'}))
    client=module.Provider('deepseek',profile=cfg['profile'],budget=budget,campaign=wrapper,campaign_sha=digest(wrapper),
        phase=phase,output=directory/'single-receipts',runtime=directory,transport=httpx.MockTransport(handler))
    result=client.generate('0')
    assert result['code']=='invalid_model_action' and len(posts)==1
    assert budget.snapshot()['total_attempts']==before+1
    with pytest.raises(ExecutionStop,match='request_cap'):
        budget.reserve(provider='deepseek',request_key='f'*64,packet_sha=payload,phase=phase,reserve_micro=2103296)
    execution.close(budget,cfg['campaign'],'one_first_draft_complete_no_reopen')
    with pytest.raises(ExecutionStop,match='batch_closed'):
        with budget.transaction() as db:execution.load(db,cfg['campaign'])
    assert len(posts)==1

def test_single_rejects_wrong_payload_before_reservation(single):
    budget=single[0];before=budget.snapshot();phase,_,key,_=request(single)
    with pytest.raises(ExecutionStop,match='exact_payload'):
        budget.reserve(provider='deepseek',request_key=key,packet_sha='e'*64,phase=phase,reserve_micro=2103296)
    assert budget.snapshot()==before
