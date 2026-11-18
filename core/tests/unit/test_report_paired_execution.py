from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import httpx
import pytest
from test_report_ieee_validation import stopped,authorized,pilot,scope,index,registry,profile
from test_report_pilot_execution import write
from test_autonomous_tool_contracts import executor_for,run_core
from test_autonomous_action_providers import cloud_body
from graphrisk.agent import report_paired_execution as execution,report_ieee_validation as ieee
from graphrisk.agent import judgment_v3 as v12,judgment_v13 as v13
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.autonomous_loop import observation_rows
from graphrisk.agent.upgrade_contract import digest,file_hash,canonical


@pytest.fixture
def paired(stopped,scope,index,registry):
    auth,allocation,allocation_path=stopped;parent,original,program_path=auth
    budget,store,old,output=parent
    ieee.register(budget,program_path,allocation_path,'Existing +50 grant, unchanged.')
    directory=output/'paired';directory.mkdir()
    cfg={'version':execution.VERSION,'campaign':'paired-six','program_id':original['program_id'],
        'program_path':str(program_path),'program_file_sha256':file_hash(program_path),
        'existing_grant_sha256':allocation['contract_sha256'],'group_used_at_authorization':0,
        'max_new_requests':12,'new_allocation':0,'repairs':0,'automatic_retries':0,'judge_calls':0,
        'overall_max_cny':100,'pause_cny':80,'pricing_date':date.today().isoformat(),
        'source_root':old['source_root'],'source':old['source'],'profile':old['profile'],
        'cases':{},'order':[],'files':{}}
    exclusions=directory/'exclusions.json';write(exclusions,{'ids':['OLD-TRANSACTION']})
    cfg['exclusions_path']=str(exclusions);cfg['files'][str(exclusions)]=file_hash(exclusions)
    cfg['profile']={**cfg['profile'],'max_prompt_utf8_bytes':20000}
    for n in range(1,7):
        current=scope.model_copy(update={'case_alias':f'{n:032x}','transaction_id':f'PRIVATE-NEW-{n}'})
        executor,_=executor_for(current,index);run_core(executor)
        bundle=build_evidence(current,executor.observations,registry)
        raw=directory/f'N{n}-raw.json';write(raw,{'scope':current.model_dump(mode='json'),
            'observations':observation_rows(executor.observations),'evidence':bundle.model_dump(mode='json')})
        cfg['files'][str(raw)]=file_hash(raw)
        for version,module,cls in (('v12',v12,v12.JudgmentProvider),('v13',v13,v13.Provider)):
            packet=module.packet_for(current,executor.observations,registry,cfg['profile']);packet['logical_key']='0'
            path=directory/f'N{n}-{version}-packet.json';write(path,packet)
            formatter=object.__new__(cls);formatter.provider,formatter.profile='deepseek',cfg['profile']
            key=f'N{n}-{version}';cfg['order'].append(key);cfg['files'][str(path)]=file_hash(path)
            cfg['cases'][key]={'alias':f'N{n}','version':version,'split':'validation' if n<=3 else 'test',
                'raw_path':str(raw),'packet_path':str(path),'packet_sha256':digest(packet),
                'evidence_sha256':digest(bundle.model_dump(mode='json')),'payload_sha256':digest(formatter.payload(packet)),
                'session':f'paired-{key}','session_manifest':{'case':key,'packet':digest(packet)}}
    cfg['contract_sha256']=digest(cfg);path=directory/'contract.json';write(path,cfg)
    store.campaign(cfg['campaign'],cfg['contract_sha256'],cap_each=12,group_key=ieee.GROUP)
    execution.register(budget,path,'Start the prepared six paired first drafts, at most twelve requests.')
    return budget,store,cfg,path,directory


def request(paired,key=None):
    budget,store,cfg,path,directory=paired;key=key or cfg['order'][0];item=cfg['cases'][key]
    deadline=store.open(item['session'],cfg['campaign'],item['session_manifest'],'deepseek',calls=1,seconds=600)
    packet=json.loads(Path(item['packet_path']).read_text(encoding='utf-8'))
    wrapper={'profile_sha256':digest(cfg['profile']),'packets':[packet],'session':item['session']}
    phase=store.register_packet(item['session'],0,digest(wrapper),'deepseek')
    return phase,wrapper,digest([digest(wrapper),'deepseek','0']),item['payload_sha256']


def reserve(paired,key=None,**changes):
    phase,wrapper,keyhash,payload=request(paired,key)
    args={'provider':'deepseek','request_key':keyhash,'packet_sha':payload,'phase':phase,'reserve_micro':2103296}
    args.update(changes)
    return paired[0].reserve(**args)


def test_register_preserves_allocation_and_cannot_reset_approval(paired):
    budget,_,cfg,path,_=paired;before=budget.snapshot()
    execution.register(budget,path,'Start the prepared six paired first drafts, at most twelve requests.')
    assert before==budget.snapshot()
    with pytest.raises(ExecutionStop,match='not_resettable'):execution.register(budget,path,'Different approval')
    with pytest.raises(ExecutionStop,match='authorization_required'):execution.register(budget,path,'')


@pytest.mark.parametrize('change',[
    {'max_new_requests':13},{'new_allocation':1},{'repairs':1},{'automatic_retries':1},
    {'judge_calls':1},{'overall_max_cny':101},{'pause_cny':81}])
def test_scope_cannot_expand_requests_money_or_enable_repair(paired,change):
    cfg=deepcopy(paired[2]);cfg.update(change);cfg.pop('contract_sha256');cfg['contract_sha256']=digest(cfg)
    with pytest.raises(ExecutionStop,match='bounds_invalid'):execution.validate_files(cfg)


def test_wrong_payload_order_or_provider_stops_before_reservation(paired):
    before=paired[0].snapshot()
    with pytest.raises(ExecutionStop,match='exact_payload'):reserve(paired,packet_sha='f'*64)
    with pytest.raises(ExecutionStop,match='order_required'):reserve(paired,key='N2-v12')
    with pytest.raises(ExecutionStop,match='order_required'):reserve(paired,provider='local',reserve_micro=0)
    assert paired[0].snapshot()==before


def test_new_unknown_never_retries_or_uses_another_case(paired):
    budget=paired[0];attempt=reserve(paired);budget.unknown(attempt,'provider_connection_unknown')
    with pytest.raises(ExecutionStop,match='new_unresolved'):reserve(paired)
    with pytest.raises(ExecutionStop,match='new_unresolved'):reserve(paired,key='N1-v13')


def test_closed_batch_and_source_tampering_stop(paired):
    budget,_,cfg,_,_=paired
    execution.close(budget,cfg['campaign'],'completed_no_reopen')
    with pytest.raises(ExecutionStop,match='batch_closed'):reserve(paired)


def test_source_tampering_stops_before_reservation(paired):
    root=Path(paired[2]['source_root']);(root/'src').mkdir(exist_ok=True)
    (root/'src/changed.py').write_text('changed=True')
    before=paired[0].snapshot()
    with pytest.raises(ExecutionStop,match='source_changed'):reserve(paired)
    assert paired[0].snapshot()==before


def test_twelve_exact_native_requests_settle_and_saved_replay_is_free(paired):
    budget,_,cfg,_,directory=paired;posts=[];before=budget.snapshot()['total_attempts']
    def handler(req):
        posts.append(json.loads(req.content))
        # Deliberately schema-invalid: receipts still settle and remain in denominator.
        return httpx.Response(200,json=cloud_body({'invalid':'model draft'}))
    for key in cfg['order']:
        phase,wrapper,_,_=request(paired,key);item=cfg['cases'][key]
        cls=v12.JudgmentProvider if item['version']=='v12' else v13.Provider
        provider=cls('deepseek',profile=cfg['profile'],budget=budget,campaign=wrapper,campaign_sha=digest(wrapper),
            phase=phase,output=directory/'receipts',runtime=directory,transport=httpx.MockTransport(handler))
        result=provider.generate('0');assert result['code']=='invalid_model_action'
        path=directory/(key+'-result.json');write(path,{'case_key':key,'result':result})
        execution.record_result(budget,cfg['campaign'],key,path)
        assert provider.generate('0')['replayed_without_model_request'] is True
    assert len(posts)==12 and budget.snapshot()['total_attempts']==before+12
    with pytest.raises(ExecutionStop,match='request_cap'):reserve(paired)


def test_missing_previous_result_stops_next_case(paired):
    budget=paired[0];attempt=reserve(paired)
    budget.settle(attempt,cost_micro=1,receipt_sha='f'*64,receipt_path='fixture',identity='["deepseek-flash","fixture-fp"]',usage={},result_code='ok')
    with pytest.raises(ExecutionStop,match='previous_result_required'):reserve(paired,key='N1-v13')


@pytest.fixture
def continuation(paired):
    budget,store,old,path,directory=paired
    attempt=reserve(paired);receipt=directory/'settled-receipt.json';write(receipt,{'fixture_receipt':attempt})
    budget.settle(attempt,cost_micro=1,receipt_sha=file_hash(receipt),receipt_path=str(receipt),
        identity='["deepseek-flash","fixture-fp"]',usage={},result_code='ok')
    result=directory/'N1-v12-inherited.json';write(result,{'case_key':'N1-v12','result':{'attempt_id':attempt}})
    execution.record_result(budget,old['campaign'],'N1-v12',result)
    execution.close(budget,old['campaign'],'paired_console_summary_type_error_after_saved_result')
    cfg=deepcopy(old);cfg.update(version=execution.CONTINUATION,campaign='paired-continuation',
        group_used_at_authorization=1,max_new_requests=11,prior_campaign=old['campaign'],
        inherited_results={'N1-v12':{'path':str(result),'file_sha':file_hash(result)}})
    cfg['files'][str(result)]=file_hash(result)
    for item in cfg['cases'].values():item['session']='continuation-'+item['session']
    cfg.pop('contract_sha256');cfg['contract_sha256']=digest(cfg)
    target=directory/'continuation.json';write(target,cfg)
    store.campaign(cfg['campaign'],cfg['contract_sha256'],cap_each=12,group_key=ieee.GROUP)
    execution.register(budget,target,'Use remaining eleven of the same twelve; never resend saved first draft.')
    return budget,store,cfg,target,directory


def test_continuation_inherits_settled_first_draft_and_never_resends_it(continuation):
    before=continuation[0].snapshot()['total_attempts']
    with pytest.raises(ExecutionStop,match='order_required'):reserve(continuation,key='N1-v12')
    reserve(continuation,key='N1-v13')
    assert continuation[0].snapshot()['total_attempts']==before+1


def test_continuation_cannot_restore_twelve_new_calls_or_skip_prefix(continuation):
    cfg=deepcopy(continuation[2]);cfg['max_new_requests']=12
    cfg.pop('contract_sha256');cfg['contract_sha256']=digest(cfg)
    with pytest.raises(ExecutionStop,match='bounds_invalid'):execution.validate_files(cfg)
    cfg=deepcopy(continuation[2]);cfg['inherited_results']={'N2-v12':next(iter(cfg['inherited_results'].values()))}
    cfg.pop('contract_sha256');cfg['contract_sha256']=digest(cfg)
    with pytest.raises(ExecutionStop,match='inherited_prefix_invalid'):execution.validate_files(cfg)


def test_guidance_pair_has_same_native_v12_contract_and_exact_frozen_payload(paired,registry):
    from graphrisk.agent import judgment_v12_guidance as guidance
    from graphrisk.agent.autonomous_tools import InvestigationScope
    from graphrisk.agent.autonomous_loop import restore_observations
    budget,store,old,_,directory=paired;cfg=deepcopy(old)
    cfg.update(version=execution.GUIDANCE,campaign='paired-guidance',cases={},order=[])
    cfg['profile']={**cfg['profile'],'report_input_presentation':'plain-facts-v2-program-scope'}
    for n in range(1,7):
        rawpath=old['cases'][f'N{n}-v12']['raw_path'];raw=json.loads(Path(rawpath).read_text(encoding='utf-8'))
        current=InvestigationScope.model_validate_json(canonical(raw['scope']),strict=True)
        observations=restore_observations(current,raw['observations'])
        for version,module in (('v12',v12),('v12_guidance',guidance)):
            packet=module.packet_for(current,observations,registry,cfg['profile']);packet['logical_key']='0'
            path=directory/f'guidance-N{n}-{version}.json';write(path,packet);cfg['files'][str(path)]=file_hash(path)
            formatter=object.__new__(v12.JudgmentProvider);formatter.provider,formatter.profile='deepseek',cfg['profile']
            key=f'N{n}-{version}';item=deepcopy(old['cases'][f'N{n}-v12'])
            item.update(version=version,packet_path=str(path),packet_sha256=digest(packet),payload_sha256=digest(formatter.payload(packet)),
                session='guidance-'+key,session_manifest={'case':key,'packet':digest(packet)})
            cfg['cases'][key]=item;cfg['order'].append(key)
    cfg.pop('contract_sha256');cfg['contract_sha256']=digest(cfg);path=directory/'guidance-contract.json';write(path,cfg)
    store.campaign(cfg['campaign'],cfg['contract_sha256'],cap_each=12,group_key=ieee.GROUP)
    execution.register(budget,path,'Exactly these six baseline/guidance pairs, max twelve first drafts.')
    current=(budget,store,cfg,path,directory)
    with pytest.raises(ExecutionStop,match='exact_payload'):reserve(current,packet_sha='f'*64)
    reserve(current)
