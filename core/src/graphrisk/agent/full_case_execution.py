"""A separately approved, bounded two-provider formal case allocation.

Preparation never registers a grant. Each reservation checks the exact durable
packet rebuilt from the case checkpoint; a count approval alone cannot send it.
"""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path

from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash

VERSION='route9-full-case-execution-v1'
GROUP='route9_full_cases_v1'
BEFORE={'deepseek':1446,'local':23}
CAPS={'deepseek':1771,'local':348,'total':2119}
LIMITS={'max_model_calls':5,'max_tool_calls':8,'max_nodes':12,'max_repairs':1,'seconds':600,'no_progress_limit':2}


def require(condition,code):
    if not condition:raise ExecutionStop('route9_'+code)


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def history_rows(db):
    return db.execute('SELECT a.attempt_id,a.provider,a.state,a.reserved_micro_cny,r.request_key,r.phase,'
        'r.packet_sha,r.accounted,r.receipt_sha,r.receipt_path,r.result_code,r.identity,r.usage_json,r.integrity_stop '
        'FROM model_attempts a JOIN execution_requests r USING(attempt_id) ORDER BY a.rowid').fetchall()


def check_files(cfg):
    for text,expected in cfg['files'].items():
        path=Path(text)
        require(path.is_file() and file_hash(path)==expected,'bound_file_changed')
    source=Path(cfg['source_root'])
    actual={str(path.relative_to(source)).replace('\\','/'):file_hash(path)
        for root in ('src','api','scripts') for path in (source/root).rglob('*.py')}
    require(actual==cfg['source_inventory'],'source_inventory_changed')


def check_contract(cfg,*,mode):
    from graphrisk.agent import judgment_v12_daily_quotes as quotes
    from graphrisk.agent.autonomous_actions import load_profile
    profile=load_profile(Path(cfg['profile_path']))
    require(profile['local']['idle_ram_min_gib']==4 and profile['local']['running_ram_min_gib']==3,'local_memory_guards')
    require(cfg['identities']['local']==canonical(['qwen3:4b',profile['local']['digest'],8192,False]),'local_identity')
    require(cfg['version']==VERSION and cfg['limits']==LIMITS and cfg['report_contract']==quotes.CONTRACT,'contract')
    require(all(type(value) is int for value in cfg['limits'].values()),'typed_limits')
    require(cfg['before_counts']==BEFORE and cfg['absolute_caps']==CAPS
        and cfg['new_per_provider']==325 and cfg['new_total']==650,'count_scope')
    require(cfg['money_cap_micro_cny']==100_000_000 and cfg['pause_at_micro_cny']==80_000_000,'money_scope')
    require(cfg['paid_judge_calls']==0 and cfg['fixed_evidence_ablation_calls']==0,'extra_model_purpose')
    require(mode in ('fixture','live') and not (mode=='live' and cfg.get('fixture')),'fixture_cannot_be_live')
    episodes=cfg['episodes']
    require(len(episodes)==130 and len({row['session_id'] for row in episodes})==130,'episode_registration')
    for provider in ('deepseek','local'):
        rows=[row for row in episodes if row['provider']==provider]
        first=[row for row in rows if row['repeat'] is False]
        repeats=[row for row in rows if row['repeat'] is True]
        require(len(first)==60 and len(repeats)==5 and len({row['case_alias'] for row in first})==60,'case_counts')
        require({row['case_alias'] for row in repeats}<={row['case_alias'] for row in first},'repeat_scope')
        require(sum(row['group']=='natural' for row in first)==30 and sum(row['group']=='diagnostic' for row in first)==30,'groups')
    left=[(r['case_alias'],r['repeat'],r['group']) for r in episodes if r['provider']=='deepseek']
    right=[(r['case_alias'],r['repeat'],r['group']) for r in episodes if r['provider']=='local']
    require(left==right,'paired_cases')
    paired=[[ (r['scope_sha256'],r['preblocked'],r['scope_path']) for r in episodes if r['provider']==provider]
            for provider in ('deepseek','local')]
    require(paired[0]==paired[1],'paired_sources')
    require(len(cfg['human_read_aliases'])==5 and len(set(cfg['human_read_aliases']))==5
        and set(cfg['human_read_aliases'])<={r[0] for r in left},'human_read_scope')
    registration=read(cfg['case_registration_path'])
    originals=[row for row in registration['cases'] if row['group'] in ('natural','diagnostic')]
    aliases={row['transaction_id']:row['case_alias'] for row in originals}
    require(len(originals)==60 and [(row['case_alias'],row['group']) for row in originals]
        ==[(row[0],row[2]) for row in left[:60]],'original_cases_changed')
    require([aliases[item] for item in registration['repeat_ids']]==[row[0] for row in left[60:]]
        and [aliases[item] for item in registration['human_read_ids']]==cfg['human_read_aliases'],'original_read_or_repeat_changed')
    if mode=='live':
        require(Path(cfg['source_root']).resolve()==Path(__file__).resolve().parents[3]
            and file_hash(Path(cfg['case_registration_path']))=='8f8c267e82d72849a0416fdd20634bd389fd2f5e8a67d585816fe69e20c1f1ad','original_case_binding')
        for original,episode in zip(originals,[r for r in episodes if r['provider']=='deepseek' and not r['repeat']],strict=True):
            blocked=original['event_second']//86400*86400<=10785898
            require(episode['preblocked'] is blocked,'original_time_eligibility')
            if not blocked:
                scope=read(episode['scope_path']);context=scope['scoring_context']
                require(scope['case_alias']==original['case_alias'] and scope['transaction_id']==original['transaction_id']
                    and scope['target_event_second']==original['event_second'] and scope['dataset_source']=='ieee_cis'
                    and context['model_sha256']=='39df475311dc34ecd83de66372cc1bc7aae4505517f6d878a4e1c4b11d47f2ba'
                    and context['policy_sha256']=='b0373c18c1fbe443f6ee5c9fd545d7353fc13e918724ecdf60bc1ce257f60f92','original_case_scope')
                require(episode['scope_path'] in cfg['files'] and episode['envelope_path'] in cfg['files'],'case_file_binding')
    approval=read(cfg['count_approval_path'])
    require(approval.get('count_scope_approved') is True and approval.get('outbound_authorized') is False
        and approval['new_attempt_caps']=={'deepseek':325,'local':325,'total':650}
        and approval['absolute_attempt_caps']==CAPS,'count_approval')
    if mode=='live':
        require(approval['question_item_id']=='["request_user_input_async","call_2fb8234064304004a538a77974f9b221",0]','count_approval_identity')
    predecessor=cfg.get('predecessor')
    if predecessor:
        previous=read(predecessor['contract_path']);aborted=read(predecessor['abort_manifest_path'])
        require(file_hash(Path(predecessor['contract_path']))==predecessor['contract_sha256']
            and file_hash(Path(predecessor['abort_manifest_path']))==predecessor['abort_manifest_sha256']
            and cfg['campaign']==previous['campaign']+'-executor-fix'
            and predecessor['campaign']==previous['campaign'] and not previous.get('predecessor')
            and previous['before_counts']==BEFORE and previous['absolute_caps']==CAPS,'predecessor_scope')
        fields=('provider','repeat','group','case_alias','preblocked','scope_sha256','scope_path','envelope_path')
        require([[row.get(key) for key in fields] for row in cfg['episodes']]
            ==[[row.get(key) for key in fields] for row in previous['episodes']]
            and cfg['human_read_aliases']==previous['human_read_aliases']
            and cfg['identities']==previous['identities']
            and file_hash(Path(cfg['profile_path']))==file_hash(Path(previous['profile_path'])),'predecessor_materials_changed')
        require(aborted['execution_sha256']==predecessor['contract_sha256'] and aborted['technical_abort'] is True
            and aborted['reason']=='restore_requires_empty_executor' and aborted['whole_report_drafts']==0
            and aborted['unknown']==0 and aborted['new_attempts']==predecessor['used_requests'],'predecessor_abort')
        if mode=='live':
            require(predecessor['contract_sha256']=='69044d6b3e24291801284d2f5c19823cda710e167a793a0f283a49aee7534c01'
                and predecessor['used_requests']==14 and {key for key in cfg['source_inventory']
                    if cfg['source_inventory'][key]!=previous['source_inventory'].get(key)}==
                {'scripts/run_full_case_evaluation.py','src/graphrisk/agent/full_case_execution.py'},'executor_fix_only')
    check_files(cfg)


def check_history(db,cfg):
    rows=history_rows(db);before=sum(BEFORE.values())
    require(len(rows)>=before and digest(rows[:before])==cfg['history_sha256'],'historical_ledger_changed')
    require({provider:sum(row[1]==provider for row in rows[:before]) for provider in BEFORE}==BEFORE,'starting_provider_counts')
    newer=rows[before:]
    predecessor=cfg.get('predecessor');prior=[]
    for row in newer:
        bound=db.execute('SELECT c.group_key,s.campaign_id FROM autonomous_phases p '
            'JOIN autonomous_sessions s USING(session_id) JOIN autonomous_campaigns c USING(campaign_id) '
            'WHERE p.phase=?',(row[5],)).fetchone()
        allowed={cfg['campaign']}
        if predecessor:allowed.add(predecessor['campaign'])
        require(bound is not None and bound[0]==GROUP and bound[1] in allowed,'unrelated_new_attempt')
        if predecessor and bound[1]==predecessor['campaign']:prior.append(row)
        require(row[2]=='settled' and row[-1]==0,'prior_unresolved_or_integrity_stop')
    if predecessor:
        aborted=read(predecessor['abort_manifest_path'])
        require(len(prior)==predecessor['used_requests'] and digest(prior)==aborted['attempts_sha256'],'predecessor_attempts_changed')
    counts={provider:sum(row[1]==provider for row in rows) for provider in BEFORE}
    require(all(counts[p]==BEFORE[p]+sum(row[1]==p for row in newer) for p in BEFORE),'provider_history')
    return newer


def register(budget,path,outbound_approval_path):
    """Only called after concrete material approval, never by a dry run."""
    cfg=read(path);check_contract(cfg,mode=budget.mode)
    note=read(outbound_approval_path)
    require(note.get('outbound_authorized') is True and note.get('execution_sha256')==file_hash(path)
        and note.get('max_new_requests')==650 and note.get('providers')==['deepseek','local']
        and isinstance(note.get('question_item_id'),str) and bool(note.get('user_answer')),'specific_outbound_approval_required')
    with budget.transaction() as db:
        table=db.execute('SELECT 1 FROM sqlite_master WHERE name=\'formal_case_grants\'').fetchone()
        grants=db.execute('SELECT campaign_id,contract_sha,closed FROM formal_case_grants').fetchall() if table else []
        predecessor=cfg.get('predecessor')
        if predecessor:
            require(grants==[(predecessor['campaign'],predecessor['contract_sha256'],1)],'only_closed_technical_predecessor_allowed')
            recorded=db.execute('SELECT manifest_path,manifest_sha FROM formal_case_aborts WHERE campaign_id=?',
                (predecessor['campaign'],)).fetchone()
            require(recorded==(predecessor['abort_manifest_path'],predecessor['abort_manifest_sha256']),'predecessor_abort_binding')
        else:require(not grants,'grant_already_registered')
        require(len(history_rows(db))==sum(BEFORE.values())+(predecessor['used_requests'] if predecessor else 0),'starting_count')
        check_history(db,cfg)
        if budget.mode=='live':
            from graphrisk.agent.daily_judge_execution import verify_saved_result
            parent=read(cfg['parent_contract_path'])
            require(cfg['identities']['deepseek']==parent['expected_identity'],'parent_model_identity')
            old=db.execute('SELECT contract_sha,closed FROM rank_successors WHERE campaign_id=?',(parent['campaign'],)).fetchone()
            require(old==('c4460058fd879713fcf6e711dad3159257df0146daf52e6cf2f04ee262c25825',1),'parent_closed_binding')
            require(all(verify_saved_result(db,parent,parent['campaign'],i) is not None for i in range(8)),'parent_saved_results')
        db.execute('CREATE TABLE IF NOT EXISTS formal_case_grants(campaign_id TEXT PRIMARY KEY,contract_path TEXT NOT NULL,'
            'contract_sha TEXT NOT NULL,approval_path TEXT NOT NULL,approval_sha TEXT NOT NULL,closed INTEGER NOT NULL DEFAULT 0)')
        db.execute('INSERT INTO formal_case_grants VALUES(?,?,?,?,?,0)',
            (cfg['campaign'],str(Path(path).resolve()),file_hash(path),str(Path(outbound_approval_path).resolve()),file_hash(outbound_approval_path)))


def load(db,campaign):
    exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='formal_case_grants'").fetchone()
    require(exists,'specific_outbound_approval_required')
    row=db.execute('SELECT contract_path,contract_sha,approval_path,approval_sha,closed FROM formal_case_grants WHERE campaign_id=?',(campaign,)).fetchone()
    require(row is not None and row[4]==0,'grant_missing_or_closed')
    require(file_hash(Path(row[0]))==row[1] and file_hash(Path(row[2]))==row[3],'grant_changed')
    cfg=read(row[0]);note=read(row[2])
    require(cfg['campaign']==campaign and note.get('outbound_authorized') is True
        and note.get('execution_sha256')==row[1],'approval_scope_changed')
    mode=db.execute('SELECT mode FROM execution_settings WHERE id=1').fetchone()[0]
    check_contract(cfg,mode=mode)
    return cfg,row[1]


def campaign_guard(db,campaign,manifest_sha,cap_each):
    cfg,sha=load(db,campaign)
    require(manifest_sha==sha and cap_each==325,'campaign_binding')


def session_manifest(cfg_sha,episode):
    return {'execution_sha256':cfg_sha,'session_id':episode['session_id'],
        'case_alias':episode['case_alias'],'provider':episode['provider'],
        'repeat':episode['repeat'],'scope_sha256':episode['scope_sha256']}


def episode_for(cfg,session):
    rows=[row for row in cfg['episodes'] if row['session_id']==session]
    require(len(rows)==1 and rows[0]['preblocked'] is False,'episode_not_executable')
    return rows[0]


def open_guard(db,session,campaign,manifest,provider,calls,seconds):
    cfg,sha=load(db,campaign);episode=episode_for(cfg,session)
    require(episode['provider']==provider and manifest==session_manifest(sha,episode)
        and calls==5 and seconds==600,'session_scope')


def guard(db,*,phase,provider,request_key,packet_sha,reserve_micro):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='autonomous_phases'").fetchone():return None
    row=db.execute('SELECT s.session_id,s.provider,s.max_calls,s.manifest_sha,s.checkpoint_path,s.checkpoint_sha,'
        's.campaign_id,p.slot,c.group_key,c.cap_each FROM autonomous_phases p JOIN autonomous_sessions s USING(session_id) '
        'JOIN autonomous_campaigns c USING(campaign_id) WHERE p.phase=?',(phase,)).fetchone()
    if row is None or row[8]!=GROUP:return None
    cfg,sha=load(db,row[6]);episode=episode_for(cfg,row[0]);newer=check_history(db,cfg)
    require(provider==row[1]==episode['provider'] and row[2]==5 and row[9]==325
        and row[3]==digest(session_manifest(sha,episode)),'session_binding')
    require(len(newer)<650 and sum(item[1]==provider for item in newer)<325,'formal_attempt_cap')
    require(file_hash(Path(row[4]))==row[5],'checkpoint_changed')
    state=read(row[4]);slot=row[7];pending=state.get('pending') or {}
    require(state['next_slot']==slot and pending.get('slot')==slot and pending.get('phase')==phase
        and state['scope_sha256']==episode['scope_sha256']
        and state.get('report_contract')==cfg['report_contract'],'checkpoint_binding')
    from graphrisk.agent.autonomous_tools import DailyInvestigationScope
    from graphrisk.agent.autonomous_loop import packet_for, restore_observations
    from graphrisk.agent.autonomous_policy import PolicyRegistry
    from graphrisk.agent.policy_retrieval import load_policy_index
    from graphrisk.agent.report_generation_profile import quality_report_profile
    from graphrisk.agent.autonomous_providers import ActionProvider, reservation_micro
    from graphrisk.agent import judgment_v12_daily_quotes as quotes
    scope=DailyInvestigationScope.model_validate_json(canonical(read(episode['scope_path'])),strict=True)
    require(scope.binding_sha256==episode['scope_sha256'],'scope_changed')
    kind=pending.get('kind');require(kind in ('action','report'),'request_purpose')
    profile=read(cfg['profile_path'])
    if kind=='report':profile=quality_report_profile(profile)
    if provider=='deepseek':
        require(profile['deepseek']['pricing_verified_date']==date.today().isoformat(),'pricing_refresh_required')
    registry=PolicyRegistry.from_file(load_policy_index(Path(cfg['policy_index_path'])),Path(cfg['policy_requirements_path']))
    packet=packet_for(scope,restore_observations(scope,state['observations']),registry,profile,kind=kind,
        remaining=5-slot,remaining_tools=8-state['tool_requests'],feedback=state['feedback'],
        task='investigate',report_contract=cfg['report_contract'])
    packet['logical_key']=str(slot)
    wrapper={'version':'loop-packet-v1','profile_sha256':digest(profile),'packets':[packet],
        'session_id':row[0],'scope_sha256':scope.binding_sha256,'kind':kind,'slot':slot}
    path=Path(episode['directory'])/'packets'/f'{slot}.json'
    require(read(path)==wrapper and file_hash(path)==pending.get('sha256')==digest(wrapper),'exact_checkpoint_packet')
    formatter=object.__new__(quotes.Provider if kind=='report' else ActionProvider)
    formatter.provider,formatter.profile=provider,profile
    require(digest(formatter.payload(packet))==packet_sha
        and request_key==digest([digest(wrapper),provider,str(slot)]),'exact_payload')
    require(reserve_micro==(reservation_micro(profile) if provider=='deepseek' else 0),'reservation_bound')
    return cfg


def identity_for_attempt(db,attempt):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='formal_case_grants'").fetchone():return None
    row=db.execute('SELECT s.campaign_id,a.provider FROM execution_requests r JOIN model_attempts a USING(attempt_id) '
        'JOIN autonomous_phases p USING(phase) JOIN autonomous_sessions s USING(session_id) '
        'JOIN formal_case_grants g USING(campaign_id) WHERE r.attempt_id=?',(attempt,)).fetchone()
    if row is None:return None
    cfg,_=load(db,row[0])
    return cfg['identities'][row[1]]


def episode_result(episode,state=None):
    value={'session_id':episode['session_id'],'case_alias':episode['case_alias'],
        'provider':episode['provider'],'repeat':episode['repeat'],
        'tool_execution_mode':'frozen_real_snapshot_query_replay'}
    if episode['preblocked']:
        return {**value,'status':'blocked_before_investigation',
            'stop_reason':'current_daily_policy_time_ineligible','model_calls':0,
            'first_drafts':[],'report':None,'first_report_quality':'NotDelivered'}
    return {**value,'state':state,'status':state['status'],'model_calls':state['model_calls'],
        'first_report_quality':'PendingSemanticReading' if state['first_drafts'] else 'NotDelivered'}


def audit_episode(db,cfg,episode):
    """Authenticate saved output against the durable state and raw receipts."""
    from graphrisk.agent import judgment_v12_daily_quotes as quotes
    from graphrisk.agent.autonomous_loop import build_evidence,latest_observations,restore_observations
    from graphrisk.agent.autonomous_tools import DailyInvestigationScope
    from graphrisk.agent.autonomous_policy import PolicyRegistry
    from graphrisk.agent.policy_retrieval import load_policy_index
    from graphrisk.agent.report_generation_profile import quality_report_profile
    from graphrisk.agent.autonomous_providers import ActionProvider
    directory=Path(episode['directory']);path=directory/'result-private.json'
    require(path.is_file(),'episode_result_missing')
    result=read(path)
    row=db.execute('SELECT campaign_id,provider,manifest_sha,checkpoint_path,checkpoint_sha '
        'FROM autonomous_sessions WHERE session_id=?',(episode['session_id'],)).fetchone()
    if episode['preblocked']:
        require(row is None and result==episode_result(episode),'preblocked_result_changed')
        return {'session_id':episode['session_id'],'result_sha256':file_hash(path),'model_calls':0}
    require(row is not None and row[:2]==(cfg['campaign'],episode['provider'])
        and row[3] is not None and Path(row[3]).resolve().is_relative_to((directory/'checkpoints').resolve())
        and file_hash(Path(row[3]))==row[4],'saved_checkpoint_binding')
    state=read(row[3])
    grant=db.execute('SELECT contract_sha FROM formal_case_grants WHERE campaign_id=?',(cfg['campaign'],)).fetchone()
    require(grant is not None and row[2]==digest(session_manifest(grant[0],episode)),'saved_session_manifest')
    require(result==episode_result(episode,state) and state['route']=='end'
        and state['scope_sha256']==episode['scope_sha256']
        and state['report_contract']==cfg['report_contract'],'saved_terminal_result')
    calls=db.execute('SELECT a.attempt_id,p.slot,a.state,r.packet_sha,r.receipt_path,r.receipt_sha,'
        'r.identity,r.result_code,r.usage_json,r.accounted,r.integrity_stop,r.request_key '
        'FROM model_attempts a JOIN execution_requests r USING(attempt_id) '
        'JOIN autonomous_phases p USING(phase) WHERE p.session_id=? ORDER BY p.slot',
        (episode['session_id'],)).fetchall()
    traces=[event for event in state['trace'] if event['event']=='model']
    require(len(calls)==state['model_calls']==len(traces)<=5
        and state['repairs']<=1 and state['tool_requests']<=8 and state['visits']<=12,'saved_call_counts')
    proof={str(path):file_hash(path),row[3]:row[4]}
    for call,trace in zip(calls,traces,strict=True):
        attempt,slot,settled,payload_sha,receipt_path,receipt_sha,identity,code,usage,cost,hard,key=call
        packet_path=directory/'packets'/f'{slot}.json';wrapper=read(packet_path);packet=wrapper['packets'][0]
        profile=read(cfg['profile_path'])
        if wrapper['kind']=='report':profile=quality_report_profile(profile)
        formatter=object.__new__(quotes.Provider if wrapper['kind']=='report' else ActionProvider)
        formatter.provider,formatter.profile=episode['provider'],profile
        require(settled=='settled' and hard==0 and identity==cfg['identities'][episode['provider']]
            and wrapper['session_id']==episode['session_id'] and wrapper['scope_sha256']==episode['scope_sha256']
            and wrapper['profile_sha256']==digest(profile) and wrapper['slot']==slot
            and digest(formatter.payload(packet))==payload_sha
            and digest(wrapper)==trace['packet_sha256']
            and key==digest([digest(wrapper),episode['provider'],str(slot)]),'saved_request_binding')
        receipt_path=Path(receipt_path)
        require(receipt_path.resolve().is_relative_to((directory/'receipts').resolve())
            and file_hash(receipt_path)==receipt_sha,'saved_receipt_changed')
        receipt=read(receipt_path)
        require(receipt['attempt_id']==attempt and receipt['profile_sha256']==digest(profile)
            and receipt['payload_sha256']==payload_sha,'saved_receipt_binding')
        analyzed=formatter._analyze(receipt,packet)
        require((analyzed['identity'],analyzed['code'],canonical(analyzed['usage']),analyzed['estimated_peak_cost_micro_cny'])
            ==(identity,code,usage,cost) and not analyzed['integrity_stop']
            and (trace['attempt_id'],trace['slot'],trace['kind'],trace['code'],trace['action'],trace['action_error'])
            ==(attempt,slot,wrapper['kind'],code,analyzed['action'],analyzed['action_error']),'saved_analysis_changed')
        proof[str(packet_path)]=file_hash(packet_path);proof[str(receipt_path)]=receipt_sha
    report_traces=[event for event in traces if event['kind']=='report']
    require(len(report_traces)==len(state['first_drafts']) and all(
        (draft['attempt_id'],draft['packet_slot'],draft['draft'],draft['format_code'])
        ==(trace['attempt_id'],trace['slot'],trace['action'],trace['code'])
        for draft,trace in zip(state['first_drafts'],report_traces,strict=True)),'saved_drafts_changed')
    if state['report'] is not None:
        scope=DailyInvestigationScope.model_validate_json(canonical(read(episode['scope_path'])),strict=True)
        registry=PolicyRegistry.from_file(load_policy_index(Path(cfg['policy_index_path'])),Path(cfg['policy_requirements_path']))
        bundle=build_evidence(scope,latest_observations(restore_observations(scope,state['observations'])),registry)
        quotes.verify_saved(state['report'],state['report_packet'],bundle)
    return {'session_id':episode['session_id'],'result_sha256':file_hash(path),
        'model_calls':len(calls),'bound_files':proof}


def close_execution(budget,path):
    """Close only after all 130 original outcomes, including failures, are saved."""
    cfg=read(path);check_contract(cfg,mode=budget.mode)
    with budget.transaction() as db:
        row=db.execute('SELECT contract_sha,closed FROM formal_case_grants WHERE campaign_id=?',(cfg['campaign'],)).fetchone()
        require(row is not None and row[0]==file_hash(path),'close_grant_binding')
        newer=check_history(db,cfg)
        proofs=[audit_episode(db,cfg,episode) for episode in cfg['episodes']]
        prior=cfg.get('predecessor',{}).get('used_requests',0)
        require(sum(item['model_calls'] for item in proofs)+prior==len(newer),'close_attempt_total')
        manifest={'version':VERSION,'execution_sha256':file_hash(path),'episodes':proofs,
            'executed_episodes_including_preblocks':130,'first_case_denominator_per_provider':60,
            'repeats_per_provider':5,'new_model_calls':len(newer),'technical_predecessor_model_calls':prior,
            'semantic_quality_acceptance':'Pending','human_reading':'Pending','labels_read':False}
        destination=Path(cfg['output'])/'EXECUTION-CLOSED-PRIVATE.json'
        body=canonical(manifest).encode('utf-8')
        if destination.exists():require(destination.read_bytes()==body,'closed_manifest_changed')
        else:
            destination.parent.mkdir(parents=True,exist_ok=True)
            temporary=destination.with_suffix('.tmp');temporary.write_bytes(body);temporary.replace(destination)
        db.execute('UPDATE formal_case_grants SET closed=1 WHERE campaign_id=?',(cfg['campaign'],))
    return manifest
