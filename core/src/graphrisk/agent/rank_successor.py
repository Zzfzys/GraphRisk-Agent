"""Scoped, non-resetting successor to a reviewed DeepSeek identity stop."""
import json
from datetime import date
from pathlib import Path
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash

GROUP='route6_deepseek_rank_v1'
GROUP_200='route6_deepseek_rank_200_v1'
GROUP_500='route6_deepseek_rank_500_v1'
GROUP_RICH_500='route6_deepseek_rich_500_v1'
GROUP_RICH_CONT='route6_deepseek_rich_cont_v1'
DAILY_VERSION='route5-daily-report-successor-v1'
GROUP_DAILY_REPORT='route5_daily_report_v1'
DAILY_CANDIDATE_VERSION='route5-daily-report-candidate-successor-v1'
GROUP_DAILY_CANDIDATE='route5_daily_candidate_v1'
DAILY_QUOTES_VERSION='route5-daily-quotes-successor-v2'
GROUP_DAILY_QUOTES='route5_daily_quotes_v2'
PLANNER_VERSION='route7-query-planner-successor-v1'
GROUP_REFERENCE_PLANNER='route7_reference_planner_v1'
PLANNER_CONTINUATION_VERSION='route7-query-planner-continuation-v1'
GROUP_REFERENCE_PLANNER_CONT='route7_reference_planner_cont_v1'
PLANNER_VERSIONS=(PLANNER_VERSION,PLANNER_CONTINUATION_VERSION)
ANSWER_VERSION='reference-answer-support-v1'
GROUP_REFERENCE_ANSWER='route7_reference_answer_v1'
CASE_REFERENCE_VERSION='daily-reference-case-comparison-v1'
GROUP_CASE_REFERENCE='route7_case_reference_pair_v1'
BOUNDARY_VERSION='reference-boundary-probe-v1'
GROUP_BOUNDARY='route7_boundary_probe_v1'
JUDGE_CONTROL_VERSION='route8-judge-controls-v1'
GROUP_JUDGE_CONTROL='route8_judge_controls_v1'
DAILY_JUDGE_VERSION='route8-daily-whole-judge-v1'
GROUP_DAILY_JUDGE='route8_daily_whole_judge_v1'
REFERENCE_VERSIONS=(*PLANNER_VERSIONS,ANSWER_VERSION,CASE_REFERENCE_VERSION,BOUNDARY_VERSION,JUDGE_CONTROL_VERSION,DAILY_JUDGE_VERSION)
DAILY_VERSIONS=(DAILY_VERSION,DAILY_CANDIDATE_VERSION,DAILY_QUOTES_VERSION)
DAILY_CASES=['H1','H2','M1','M2','L1','L2']

def bounds(cfg):
    version=cfg.get('version')
    if version=='route6-rank-successor-v1':return 6,15_000_000,GROUP
    if version=='route6-rank-successor-v2-200':return 200,5_000_000,GROUP_200
    if version=='route6-rank-successor-v3-500':return 500,5_000_000,GROUP_500
    if version=='route6-rich-successor-v4-500':return 500,5_000_000,GROUP_RICH_500
    if version==DAILY_VERSION:return 6,5_000_000,GROUP_DAILY_REPORT
    if version==DAILY_CANDIDATE_VERSION:return 6,5_000_000,GROUP_DAILY_CANDIDATE
    if version==DAILY_QUOTES_VERSION:return 6,5_000_000,GROUP_DAILY_QUOTES
    if version==PLANNER_VERSION:return 24,5_000_000,GROUP_REFERENCE_PLANNER
    if version==PLANNER_CONTINUATION_VERSION:return 23,2_870_080,GROUP_REFERENCE_PLANNER_CONT
    if version==ANSWER_VERSION:return 12,5_000_000,GROUP_REFERENCE_ANSWER
    if version==CASE_REFERENCE_VERSION:return 2,5_000_000,GROUP_CASE_REFERENCE
    if version==BOUNDARY_VERSION:return 6,5_000_000,GROUP_BOUNDARY
    if version==DAILY_JUDGE_VERSION:return 8,5_000_000,GROUP_DAILY_JUDGE
    if version==JUDGE_CONTROL_VERSION:return 12,5_000_000,GROUP_JUDGE_CONTROL
    if version=='route6-rich-continuation-v5':
        n=cfg.get('max_requests');cost=cfg.get('cap_micro_cny')
        if type(n) is not int or not 1<=n<500 or type(cost) is not int or not 0<cost<5_000_000:raise ExecutionStop('rank_successor_continuation_bounds')
        return n,cost,GROUP_RICH_CONT
    raise ExecutionStop('rank_successor_version')

def require(ok, reason):
    if not ok: raise ExecutionStop('rank_successor_'+reason)

def rows(db, where):
    cur=db.execute('SELECT a.attempt_id,a.provider,a.state,a.reserved_micro_cny,r.request_key,r.packet_sha,r.accounted,r.receipt_sha,r.receipt_path,r.result_code,r.identity,r.usage_json,r.integrity_stop FROM model_attempts a JOIN execution_requests r USING(attempt_id) WHERE '+where+' ORDER BY a.attempt_id')
    return [dict(zip([x[0] for x in cur.description],row)) for row in cur.fetchall()]

def read_contract(path):
    cfg=json.loads(Path(path).read_text(encoding='utf-8'))
    maximum,cost,_=bounds(cfg)
    require(cfg['max_requests']==maximum and cfg['cap_micro_cny']==cost and cfg['retries']==0,'bounds')
    require(cfg['endpoint']=='https://api.deepseek.com/chat/completions' and cfg['model']=='deepseek-flash','endpoint')
    require(len(cfg['payloads'])==maximum and len(set(cfg['payloads']))==maximum,'payloads')
    if cfg['version'] in ('route6-rank-successor-v3-500','route6-rich-successor-v4-500','route6-rich-continuation-v5',*REFERENCE_VERSIONS):
        limit=1500 if cfg['version'] in ('route6-rich-successor-v4-500','route6-rich-continuation-v5',*REFERENCE_VERSIONS) else 1000
        require(type(cfg.get('deepseek_before')) is int and type(cfg.get('attempts_before')) is int and 0<=cfg['deepseek_before']<=cfg['attempts_before'] and cfg['attempts_before']+maximum<=limit,'global_allocation')
    for p,sha in cfg['files'].items(): require(Path(p).is_file() and file_hash(Path(p))==sha,'file_changed')
    if cfg['version'] in DAILY_VERSIONS:check_daily_report_contract(cfg)
    if cfg['version'] in PLANNER_VERSIONS:
        from graphrisk.agent.reference_planner_execution import check_contract
        check_contract(cfg)
    if cfg['version']==ANSWER_VERSION:
        from graphrisk.agent.reference_answer_execution import check_contract
        check_contract(cfg)
    if cfg['version']==CASE_REFERENCE_VERSION:
        from graphrisk.agent.daily_reference_comparison_execution import check_contract
        check_contract(cfg)
    if cfg['version']==BOUNDARY_VERSION:
        from graphrisk.agent.reference_boundary_execution import check_contract
        check_contract(cfg)
    if cfg['version']==JUDGE_CONTROL_VERSION:
        from graphrisk.agent.judge_control_execution import check_contract
        check_contract(cfg)
    if cfg['version']==DAILY_JUDGE_VERSION:
        from graphrisk.agent.daily_judge_execution import check_contract
        check_contract(cfg)
    return cfg


def check_daily_report_contract(cfg):
    """Only the six reviewed day-end v12 payloads receive this scoped successor."""
    from graphrisk.agent.autonomous_evidence import EvidenceBundle
    from graphrisk.agent.autonomous_tools import DailyInvestigationScope
    from graphrisk.agent.judgment_v3 import CONTRACT,DAY_END_VARIANT,RULES,JudgmentProvider
    from graphrisk.agent.report_generation_profile import quality_report_profile
    candidate=cfg['version']==DAILY_CANDIDATE_VERSION
    if candidate:
        from graphrisk.agent.judgment_v12_daily_review import (
            CONTRACT,GENERATION as DAY_END_VARIANT,RULES,Provider as JudgmentProvider,_check_packet)
    quotes=cfg['version']==DAILY_QUOTES_VERSION
    if quotes:
        from graphrisk.agent.judgment_v12_daily_quotes import (
            CONTRACT,GENERATION as DAY_END_VARIANT,RULES,Provider as JudgmentProvider,_check_packet)
    require(cfg.get('case_keys')==DAILY_CASES and cfg.get('report_contract')==CONTRACT
            and cfg.get('generation_variant')==DAY_END_VARIANT,'daily_scope')
    require(all(type(cfg.get(key)) is int and cfg[key]==0 for key in
                ('retries','automatic_retries','repairs','judge_calls'))
            and cfg.get('pause_cny')==80 and cfg.get('overall_max_cny')==100,'daily_bounds')
    require(type(cfg.get('deepseek_before')) is int and cfg['deepseek_before']==(1376 if quotes else 1370 if candidate else 1364)
            and type(cfg.get('attempts_before')) is int and cfg['attempts_before']==(1399 if quotes else 1393 if candidate else 1387)
            and cfg['attempts_before']+cfg['max_requests']<=1500,'global_allocation')
    require(type(cfg.get('pricing_required')) is bool,'daily_pricing_state')
    def bound_file(path,sha=None):
        path=Path(path)
        require(path.is_file() and cfg['files'].get(str(path))==file_hash(path)
                and (sha is None or file_hash(path)==sha),'daily_file_binding')
        return json.loads(path.read_text(encoding='utf-8'))
    manifest=bound_file(cfg['review_manifest_path'],cfg['review_manifest_sha256'])
    require(manifest.get('version')=='six-day-end-v12-review-prepared-v1'
            and manifest.get('max_requested_first_drafts')==6
            and all(manifest.get(key)==0 for key in ('automatic_retries','repairs','paid_judges'))
            and manifest.get('labels_read') is False and manifest.get('historical_replay') is True,
            'daily_review_manifest')
    root=Path(manifest['source'])
    require(Path(cfg['source_root']).resolve()==root.resolve(),'daily_source_root')
    actual={p.relative_to(root).as_posix():file_hash(p)
            for folder in ('src','api','scripts') for p in (root/folder).rglob('*.py')}
    require(actual==manifest['source_python_sha256'] and bool(actual),'daily_source_inventory')
    for relative,sha in actual.items():
        require(cfg['files'].get(str(root/relative))==sha,'daily_source_binding')
    budget_config=root/'configs/agent_investigation_experiment.json'
    require(budget_config.is_file() and cfg['files'].get(str(budget_config))==file_hash(budget_config),
            'daily_budget_contract')
    profile=bound_file(cfg['profile_path'])
    require(digest(profile)==manifest['profile_sha256'] and profile==quality_report_profile(profile)
            and profile['deepseek']['endpoint']==cfg['endpoint'] and profile['deepseek']['model']==cfg['model'],
            'daily_profile')
    cases=manifest['selected_cases']
    require([row['review_case'] for row in cases]==DAILY_CASES
            and [row['risk_level'] for row in cases]==['high','high','medium','medium','low','low']
            and len({row['scope_sha256'] for row in cases})==6
            and cfg['payloads']==[row['canonical_payload_sha256'] for row in cases],'daily_payload_whitelist')
    formatter=object.__new__(JudgmentProvider);formatter.provider,formatter.profile='deepseek',profile
    for row in cases:
        payload_path=Path(row['payload_path']);name=row['review_case']
        payload=bound_file(payload_path,row['payload_file_sha256'])
        packet=bound_file(payload_path.parent/(name+'-packet-private.json'))
        raw=bound_file(payload_path.parent/(name+'-source-private.json'))
        scope=DailyInvestigationScope.model_validate_json(canonical(raw['scope']),strict=True)
        bundle=EvidenceBundle.model_validate_json(canonical(raw['evidence']),strict=True)
        require(scope.binding_sha256==row['scope_sha256']==packet['source_scope_sha256']
                and packet['scope_private']==raw['scope'] and scope.case_alias==bundle.case_alias
                and scope.scoring_context==bundle.scoring_context
                and scope.scoring_context.risk_level==row['risk_level']
                and digest(bundle.model_dump(mode='json'))==packet['source_evidence_sha256'],'daily_evidence_binding')
        require(digest(packet)==row['packet_sha256'] and packet.get('logical_key')=='0'
                and packet.get('generation_variant')==DAY_END_VARIANT
                and packet['view'].get('narrative_contract')==CONTRACT
                and packet.get('judgment_rules_version')==RULES
                and not packet['view'].get('repair') and row.get('source_contract_issues')==[],
                'daily_packet_binding')
        if candidate or quotes:_check_packet(packet,bundle)
        require(payload==formatter.payload(packet) and digest(payload)==row['canonical_payload_sha256'],
                'daily_payload_binding')

def check_history(db,cfg):
    require(rows(db,"a.state IN ('reserved','unknown')")==cfg['historical_unknowns'],'unknowns_changed')
    require(rows(db,'r.integrity_stop=1')==cfg['historical_stops'],'stops_changed')
    unknowns=3 if cfg.get('version') in (PLANNER_CONTINUATION_VERSION,ANSWER_VERSION,CASE_REFERENCE_VERSION,BOUNDARY_VERSION,JUDGE_CONTROL_VERSION,DAILY_JUDGE_VERSION) else 2
    require(len(cfg['historical_unknowns'])==unknowns and len(cfg['historical_stops'])==1,'history_shape')
    stop=cfg['historical_stops'][0]
    require(stop['state']=='settled' and stop['provider']=='deepseek' and stop['identity']==cfg['expected_identity'],'stop_identity')
    require(stop['receipt_path'] and Path(stop['receipt_path']).is_file() and file_hash(Path(stop['receipt_path']))==stop['receipt_sha'],'old_receipt')
    body=json.loads(Path(stop['receipt_path']).read_text(encoding='utf-8'))['body']
    require(canonical([body.get('model'),body.get('system_fingerprint')])==cfg['expected_identity'],'receipt_identity')
    old=db.execute("SELECT identity FROM provider_identity WHERE provider='deepseek'").fetchone()
    require(old==(cfg['old_identity'],) and cfg['old_identity']!=cfg['expected_identity'],'old_identity')

def register(budget,path,authorization):
    require(isinstance(authorization,str) and bool(authorization.strip()),'authorization')
    cfg=read_contract(path); sha=file_hash(Path(path)); campaign=cfg['campaign']; maximum,_,group=bounds(cfg)
    with budget.transaction() as db:
        budget._check_registration(db);budget._check_request_links(db);check_history(db,cfg)
        check_allocation(db,cfg,0);check_parent(db,cfg)
        if cfg['version']==ANSWER_VERSION:
            from graphrisk.agent.reference_answer_execution import check_authorization
            check_authorization(db,cfg,authorization)
        if cfg['version']==CASE_REFERENCE_VERSION:
            from graphrisk.agent.daily_reference_comparison_execution import check_authorization
            check_authorization(db,cfg,authorization)
        if cfg['version']==BOUNDARY_VERSION:
            from graphrisk.agent.reference_boundary_execution import check_authorization
            check_authorization(db,cfg,authorization)
        if cfg['version']==JUDGE_CONTROL_VERSION:
            from graphrisk.agent.judge_control_execution import check_authorization
            check_authorization(db,cfg,authorization)
        if cfg['version']==DAILY_JUDGE_VERSION:
            from graphrisk.agent.daily_judge_execution import check_authorization
            check_authorization(db,cfg,authorization)
        require(db.execute('SELECT manifest_sha,cap_each,group_key FROM autonomous_campaigns WHERE campaign_id=?',(campaign,)).fetchone()==(sha,maximum,group),'campaign')
        db.execute('CREATE TABLE IF NOT EXISTS rank_successors(campaign_id TEXT PRIMARY KEY, contract_path TEXT NOT NULL, contract_sha TEXT NOT NULL, authorization TEXT NOT NULL, closed INTEGER NOT NULL DEFAULT 0)')
        db.execute('CREATE TABLE IF NOT EXISTS rank_successor_results(campaign_id TEXT NOT NULL, position INTEGER NOT NULL, attempt_id TEXT NOT NULL, result_path TEXT NOT NULL, result_sha TEXT NOT NULL, PRIMARY KEY(campaign_id,position))')
        require(not db.execute('SELECT 1 FROM rank_successors WHERE campaign_id=?',(campaign,)).fetchone(),'already_registered')
        require(not db.execute('SELECT 1 FROM execution_requests r JOIN autonomous_phases p USING(phase) JOIN autonomous_sessions s USING(session_id) WHERE s.campaign_id=?',(campaign,)).fetchone(),'campaign_used')
        db.execute('INSERT INTO rank_successors VALUES(?,?,?,?,0)',(campaign,str(Path(path).resolve()),sha,authorization))

def load(db,campaign):
    row=db.execute('SELECT contract_path,contract_sha,authorization,closed FROM rank_successors WHERE campaign_id=?',(campaign,)).fetchone()
    require(row is not None and row[2].strip() and row[3]==0,'not_open')
    require(Path(row[0]).is_file() and file_hash(Path(row[0]))==row[1],'contract_changed')
    cfg=read_contract(row[0]); require(cfg['campaign']==campaign,'contract_campaign')
    maximum,_,group=bounds(cfg)
    require(db.execute('SELECT manifest_sha,cap_each,group_key FROM autonomous_campaigns WHERE campaign_id=?',(campaign,)).fetchone()==(row[1],maximum,group),'campaign_changed')
    return cfg


def verify_daily_saved_result(db,cfg,campaign,position):
    """Recheck both saved result and paid receipt, including for a closed batch."""
    require(cfg['version'] in DAILY_VERSIONS and cfg['campaign']==campaign
            and type(position) is int and 0<=position<6,'daily_saved_result_scope')
    result=db.execute('SELECT attempt_id,result_path,result_sha FROM rank_successor_results '
        'WHERE campaign_id=? AND position=?',(campaign,position)).fetchone()
    if result is None:return None
    attempt=db.execute('SELECT a.provider,a.state,r.packet_sha,r.integrity_stop,r.identity,r.receipt_sha,r.receipt_path '
        'FROM model_attempts a JOIN execution_requests r USING(attempt_id) '
        'JOIN autonomous_phases p USING(phase) JOIN autonomous_sessions s USING(session_id) '
        'WHERE a.attempt_id=? AND s.campaign_id=? AND s.session_id=?',
        (result[0],campaign,campaign+'-C'+str(position+1))).fetchone()
    require(attempt is not None and attempt[:5]==('deepseek','settled',cfg['payloads'][position],0,cfg['expected_identity']),
            'daily_saved_attempt')
    require(attempt[6] and Path(attempt[6]).is_file() and file_hash(Path(attempt[6]))==attempt[5],
            'daily_saved_receipt')
    require(Path(result[1]).is_file() and file_hash(Path(result[1]))==result[2],'prior_result')
    return result[1],result[2]

def guard(db,*,phase,provider,request_key,packet_sha,reserve_micro):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='rank_successors'").fetchone(): return False
    sess=db.execute('SELECT s.campaign_id,s.session_id,s.manifest_sha,s.provider,s.max_calls,p.slot FROM autonomous_phases p JOIN autonomous_sessions s USING(session_id) WHERE p.phase=?',(phase,)).fetchone()
    if not sess or not db.execute('SELECT 1 FROM rank_successors WHERE campaign_id=?',(sess[0],)).fetchone(): return False
    campaign=sess[0]; cfg=load(db,campaign);check_history(db,cfg)
    if cfg['version'] in (*DAILY_VERSIONS,*REFERENCE_VERSIONS):
        require(cfg['pricing_required'] is False,'daily_pricing_required')
        profile=json.loads(Path(cfg['profile_path']).read_text(encoding='utf-8'))
        require(profile['deepseek']['pricing_verified_date']==date.today().isoformat(),
                'daily_pricing_refresh_required')
    used=db.execute('SELECT r.attempt_id,r.packet_sha,a.state,r.accounted FROM execution_requests r JOIN model_attempts a USING(attempt_id) JOIN autonomous_phases p USING(phase) JOIN autonomous_sessions s USING(session_id) WHERE s.campaign_id=? ORDER BY r.rowid',(campaign,)).fetchall()
    n=len(used);require(n<cfg['max_requests'],'request_cap')
    check_allocation(db,cfg,n);check_parent(db,cfg)
    require(provider=='deepseek' and sess[1]==campaign+'-C'+str(n+1) and sess[3:] == ('deepseek',1,0),'session')
    require(sess[2]==digest({'campaign':campaign,'position':n,'payload_sha':cfg['payloads'][n]}),'session_manifest')
    wrapper=db.execute('SELECT manifest_sha FROM execution_phases WHERE phase=?',(phase,)).fetchone()
    require(wrapper is not None and packet_sha==cfg['payloads'][n] and request_key==digest([wrapper[0],'deepseek','0']),'exact_payload')
    require(all(x[2]=='settled' for x in used),'prior_not_settled')
    for i,(attempt,payload,_,_) in enumerate(used):
        result=db.execute('SELECT attempt_id,result_path,result_sha FROM rank_successor_results WHERE campaign_id=? AND position=?',(campaign,i)).fetchone()
        if cfg['version'] in DAILY_VERSIONS:
            require(verify_daily_saved_result(db,cfg,campaign,i) is not None,'prior_result')
        if cfg['version'] in PLANNER_VERSIONS:
            from graphrisk.agent.reference_planner_execution import verify_saved_result
            require(verify_saved_result(db,cfg,campaign,i) is not None,'prior_result')
        if cfg['version']==ANSWER_VERSION:
            from graphrisk.agent.reference_answer_execution import verify_saved_result
            require(verify_saved_result(db,cfg,campaign,i) is not None,'prior_result')
        if cfg['version']==CASE_REFERENCE_VERSION:
            from graphrisk.agent.daily_reference_comparison_execution import verify_saved_result
            require(verify_saved_result(db,cfg,campaign,i) is not None,'prior_result')
        if cfg['version']==BOUNDARY_VERSION:
            from graphrisk.agent.reference_boundary_execution import verify_saved_result
            require(verify_saved_result(db,cfg,campaign,i) is not None,'prior_result')
        if cfg['version']==JUDGE_CONTROL_VERSION:
            from graphrisk.agent.judge_control_execution import verify_saved_result
            require(verify_saved_result(db,cfg,campaign,i) is not None,'prior_result')
        if cfg['version']==DAILY_JUDGE_VERSION:
            from graphrisk.agent.daily_judge_execution import verify_saved_result
            require(verify_saved_result(db,cfg,campaign,i) is not None,'prior_result')
        require(result is not None and result[0]==attempt and payload==cfg['payloads'][i] and Path(result[1]).is_file() and file_hash(Path(result[1]))==result[2],'prior_result')
    require(sum(x[3] for x in used)+reserve_micro<=cfg['cap_micro_cny'],'pilot_cost_cap')
    return True

def check_parent(db,cfg):
    if cfg['version']==DAILY_JUDGE_VERSION:
        from graphrisk.agent.daily_judge_execution import check_parent as check_daily_judge_parent
        check_daily_judge_parent(db,cfg)
        return
    if cfg['version']==JUDGE_CONTROL_VERSION:
        from graphrisk.agent.judge_control_execution import check_parent as check_judge_parent
        check_judge_parent(db,cfg)
        return
    if cfg['version']==BOUNDARY_VERSION:
        from graphrisk.agent.reference_boundary_execution import check_parent as check_boundary_parent
        check_boundary_parent(db,cfg)
        return
    if cfg['version']==CASE_REFERENCE_VERSION:
        from graphrisk.agent.daily_reference_comparison_execution import check_parent as check_case_parent
        check_case_parent(db,cfg)
        return
    if cfg['version']==ANSWER_VERSION:
        from graphrisk.agent.reference_answer_execution import check_parent as check_answer_parent
        check_answer_parent(db,cfg)
        return
    if cfg['version'] in PLANNER_VERSIONS:
        from graphrisk.agent.reference_planner_execution import check_parent as check_planner_parent
        check_planner_parent(db,cfg)
        return
    if cfg['version'] in (DAILY_CANDIDATE_VERSION,DAILY_QUOTES_VERSION):
        parent=cfg.get('parent_campaign')
        row=db.execute('SELECT contract_path,contract_sha,closed FROM rank_successors WHERE campaign_id=?',
                       (parent,)).fetchone()
        require(row is not None and row[2]==1 and row[1]==cfg.get('parent_contract_sha')
                and file_hash(Path(row[0]))==row[1],'daily_parent_binding')
        prior=json.loads(Path(row[0]).read_text(encoding='utf-8'))
        expected_parent=DAILY_CANDIDATE_VERSION if cfg['version']==DAILY_QUOTES_VERSION else DAILY_VERSION
        require(prior['version']==expected_parent and prior['campaign']==parent
                and prior['expected_identity']==cfg['expected_identity']
                and cfg['deepseek_before']==prior['deepseek_before']+6
                and cfg['attempts_before']==prior['attempts_before']+6,'daily_parent_scope')
        check_parent(db,prior)
        count=db.execute('SELECT count(*) FROM execution_requests JOIN autonomous_phases USING(phase) '
                         'JOIN autonomous_sessions USING(session_id) WHERE campaign_id=?',(parent,)).fetchone()[0]
        require(count==6,'daily_parent_count')
        old_manifest=Path(prior['review_manifest_path'])
        require(file_hash(old_manifest)==prior['review_manifest_sha256'],'daily_parent_manifest')
        old=json.loads(old_manifest.read_text(encoding='utf-8'))['selected_cases']
        new=json.loads(Path(cfg['review_manifest_path']).read_text(encoding='utf-8'))['selected_cases']
        for i,key in enumerate(DAILY_CASES):
            require(verify_daily_saved_result(db,prior,parent,i) is not None,'daily_parent_result')
            a=Path(old[i]['payload_path']).parent/(key+'-source-private.json')
            b=Path(new[i]['payload_path']).parent/(key+'-source-private.json')
            require(file_hash(a)==prior['files'].get(str(a))==file_hash(b),'daily_parent_evidence')
        return
    if cfg['version']!='route6-rich-continuation-v5':return
    parent=cfg.get('parent_campaign')
    row=db.execute('SELECT contract_path,contract_sha,closed FROM rank_successors WHERE campaign_id=?',(parent,)).fetchone()
    require(row is not None and row[2]==1 and row[1]==cfg.get('parent_contract_sha') and file_hash(Path(row[0]))==row[1],'parent_binding')
    prior=json.loads(Path(row[0]).read_text(encoding='utf-8'))
    require(prior['version']=='route6-rich-successor-v4-500' and prior['max_requests']==500 and prior['cap_micro_cny']==5_000_000 and prior['expected_identity']==cfg['expected_identity'],'parent_scope')
    attempts=db.execute('SELECT a.attempt_id,a.state,r.packet_sha,r.accounted,r.receipt_sha,r.receipt_path,r.identity,r.integrity_stop FROM model_attempts a JOIN execution_requests r USING(attempt_id) JOIN autonomous_phases p USING(phase) JOIN autonomous_sessions s USING(session_id) WHERE s.campaign_id=? ORDER BY r.rowid',(parent,)).fetchall()
    n=cfg.get('parent_completed');require(type(n) is int and 0<n<500 and len(attempts)==n and cfg['max_requests']==500-n,'parent_count')
    require(cfg['cap_micro_cny']==5_000_000-sum(x[3] for x in attempts) and cfg['parent_cost_micro']==sum(x[3] for x in attempts),'parent_cost')
    require(cfg['deepseek_before']==prior['deepseek_before']+n and cfg['attempts_before']==prior['attempts_before']+n,'parent_allocation')
    for i,(attempt,state,payload,cost,receipt_sha,receipt_path,identity,stop) in enumerate(attempts):
        require(state=='settled' and payload==prior['payloads'][i] and identity==cfg['expected_identity'] and stop==0 and receipt_path and Path(receipt_path).is_file() and file_hash(Path(receipt_path))==receipt_sha,'parent_receipt')
        result=db.execute('SELECT attempt_id,result_path,result_sha FROM rank_successor_results WHERE campaign_id=? AND position=?',(parent,i)).fetchone()
        require(result is not None and result[0]==attempt and Path(result[1]).is_file() and file_hash(Path(result[1]))==result[2],'parent_result')

def check_allocation(db,cfg,used_in_batch):
    if cfg['version'] not in ('route6-rank-successor-v3-500','route6-rich-successor-v4-500','route6-rich-continuation-v5',*DAILY_VERSIONS,*REFERENCE_VERSIONS):return
    cloud=db.execute("SELECT count(*) FROM model_attempts WHERE provider='deepseek'").fetchone()[0]
    total=db.execute('SELECT count(*) FROM model_attempts').fetchone()[0]
    require(cloud==cfg['deepseek_before']+used_in_batch and total==cfg['attempts_before']+used_in_batch,'global_allocation_changed')

def global_cloud_cap(db,phase):
    row=db.execute('SELECT s.campaign_id FROM autonomous_phases p JOIN autonomous_sessions s USING(session_id) WHERE p.phase=?',(phase,)).fetchone()
    require(row is not None,'global_allocation_phase')
    cfg=load(db,row[0])
    return cfg['deepseek_before']+cfg['max_requests'] if cfg['version'] in ('route6-rank-successor-v3-500','route6-rich-successor-v4-500','route6-rich-continuation-v5',*DAILY_VERSIONS,*REFERENCE_VERSIONS) else 600

def global_total_cap(db,phase):
    row=db.execute('SELECT s.campaign_id FROM autonomous_phases p JOIN autonomous_sessions s USING(session_id) WHERE p.phase=?',(phase,)).fetchone()
    require(row is not None,'global_allocation_phase')
    cfg=load(db,row[0])
    return cfg['attempts_before']+cfg['max_requests'] if cfg['version'] in ('route6-rich-successor-v4-500','route6-rich-continuation-v5',*DAILY_VERSIONS,*REFERENCE_VERSIONS) else 1000

def identity_for_attempt(db,attempt):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='rank_successors'").fetchone(): return None
    row=db.execute('SELECT s.campaign_id FROM execution_requests r JOIN autonomous_phases p USING(phase) JOIN autonomous_sessions s USING(session_id) JOIN rank_successors g USING(campaign_id) WHERE r.attempt_id=?',(attempt,)).fetchone()
    if not row: return None
    return load(db,row[0])['expected_identity']

def record(budget,campaign,position,attempt,path):
    with budget.transaction() as db:
        cfg=load(db,campaign)
        row=db.execute('SELECT a.state,r.packet_sha,r.integrity_stop,r.receipt_sha,r.receipt_path FROM model_attempts a JOIN execution_requests r USING(attempt_id) JOIN autonomous_phases p USING(phase) JOIN autonomous_sessions s USING(session_id) WHERE a.attempt_id=? AND s.campaign_id=? AND s.session_id=?',(attempt,campaign,campaign+'-C'+str(position+1))).fetchone()
        require(row is not None and row[0]=='settled' and row[1]==cfg['payloads'][position] and row[2]==0,'result_attempt')
        require(row[4] and file_hash(Path(row[4]))==row[3],'result_receipt')
        db.execute('INSERT INTO rank_successor_results VALUES(?,?,?,?,?)',(campaign,position,attempt,str(Path(path).resolve()),file_hash(Path(path))))

def close(budget,campaign):
    with budget.transaction() as db:
        require(db.execute('UPDATE rank_successors SET closed=1 WHERE campaign_id=? AND closed=0',(campaign,)).rowcount==1,'close')


