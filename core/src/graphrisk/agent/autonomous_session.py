"""Durable episode authorization/checkpoints extending, never resetting, route2."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time

from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash


def guard_session_reservation(db, phase, provider):
    """Called INSIDE route2's reservation transaction; legacy phases unchanged."""
    exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='autonomous_phases'").fetchone()
    if not exists:return
    row=db.execute("SELECT s.session_id,s.provider,s.max_calls,s.deadline,s.campaign_id,c.cap_each,c.group_key FROM autonomous_phases p JOIN autonomous_sessions s USING(session_id) JOIN autonomous_campaigns c USING(campaign_id) WHERE p.phase=?",(phase,)).fetchone()
    if row is None:
        linked=db.execute('SELECT 1 FROM autonomous_phases WHERE phase=?',(phase,)).fetchone()
        if linked is not None or phase.startswith('loop-'):raise ExecutionStop('session_metadata_incomplete_no_reset')
        return
    session,expected,cap,deadline,campaign,phase_cap,group_key=row
    if provider!=expected:raise ExecutionStop('session_provider_binding_changed')
    if time.time()>=deadline:raise ExecutionStop('session_deadline_reached')
    used=db.execute("SELECT count(*) FROM execution_requests r JOIN autonomous_phases p USING(phase) WHERE p.session_id=?",(session,)).fetchone()[0]
    if used>=cap:raise ExecutionStop('session_model_call_limit')
    used=db.execute("SELECT count(*) FROM execution_requests r JOIN model_attempts a USING(attempt_id) JOIN autonomous_phases p USING(phase) JOIN autonomous_sessions s USING(session_id) WHERE s.campaign_id=? AND a.provider=?",(campaign,provider)).fetchone()[0]
    if used>=phase_cap:raise ExecutionStop('campaign_model_call_limit')
    group_used=db.execute('SELECT count(*) FROM execution_requests r JOIN model_attempts a USING(attempt_id) JOIN autonomous_phases p USING(phase) JOIN autonomous_sessions s USING(session_id) JOIN autonomous_campaigns c USING(campaign_id) WHERE c.group_key=? AND a.provider=?',(group_key,provider)).fetchone()[0]
    if group_key=='route3' and group_used>=15:raise ExecutionStop('route3_group_model_call_limit')
    if group_key=='route4' and group_used>=10:raise ExecutionStop('route4_group_model_call_limit')
    if group_key=='route5' and group_used>=10:raise ExecutionStop('route5_group_model_call_limit')
    if group_key=='route5_supplement_v3':
        if provider!='deepseek':raise ExecutionStop('supplement_deepseek_only')
        if group_used>=100:raise ExecutionStop('supplement_100_request_cap')
        return  # Separately authorized additional allocation; global money/call guards already ran.
    if group_key=='route5_ieee_v12_20261025':
        if provider!='deepseek' or group_used>=50:raise ExecutionStop('ieee_v12_fifty_request_cap')
        return
    if group_key in ('route5_daily_report_v1','route5_daily_candidate_v1','route5_daily_quotes_v2'):
        if provider!='deepseek' or cap!=1 or phase_cap!=6 or group_used>=6:
            raise ExecutionStop('daily_report_six_first_draft_cap')
        from graphrisk.agent.rank_successor import bounds,load
        if bounds(load(db,campaign))[2]!=group_key:raise ExecutionStop('daily_report_successor_required')
        return
    if group_key=='route7_reference_planner_v1':
        if provider!='deepseek' or cap!=1 or phase_cap!=24 or group_used>=24:
            raise ExecutionStop('reference_planner_twenty_four_request_cap')
        from graphrisk.agent.rank_successor import bounds,load
        if bounds(load(db,campaign))!=(24,5_000_000,group_key):
            raise ExecutionStop('reference_planner_successor_required')
        return
    if group_key=='route7_reference_planner_cont_v1':
        if provider!='deepseek' or cap!=1 or phase_cap!=23 or group_used>=23:
            raise ExecutionStop('reference_planner_twenty_three_request_cap')
        from graphrisk.agent.rank_successor import bounds,load
        if bounds(load(db,campaign))!=(23,2_870_080,group_key):
            raise ExecutionStop('reference_planner_continuation_required')
        return
    if group_key=='route7_reference_answer_v1':
        if provider!='deepseek' or cap!=1 or phase_cap!=12 or group_used>=12:
            raise ExecutionStop('reference_answer_twelve_first_draft_cap')
        from graphrisk.agent.rank_successor import bounds,load
        if bounds(load(db,campaign))!=(12,5_000_000,group_key):
            raise ExecutionStop('reference_answer_successor_required')
        return
    if group_key=='route7_case_reference_pair_v1':
        if provider!='deepseek' or cap!=1 or phase_cap!=2 or group_used>=2:
            raise ExecutionStop('case_reference_two_first_draft_cap')
        from graphrisk.agent.rank_successor import bounds,load
        if bounds(load(db,campaign))!=(2,5_000_000,group_key):
            raise ExecutionStop('case_reference_successor_required')
        return
    if group_key=='route7_boundary_probe_v1':
        if provider!='deepseek' or cap!=1 or phase_cap!=6 or group_used>=6:
            raise ExecutionStop('reference_boundary_six_request_cap')
        from graphrisk.agent.rank_successor import bounds,load
        if bounds(load(db,campaign))!=(6,5_000_000,group_key):
            raise ExecutionStop('reference_boundary_successor_required')
        return
    if group_key=='route8_judge_controls_v1':
        if provider!='deepseek' or cap!=1 or phase_cap!=12 or group_used>=12:
            raise ExecutionStop('judge_control_twelve_request_cap')
        from graphrisk.agent.rank_successor import bounds,load
        if bounds(load(db,campaign))!=(12,5_000_000,group_key):
            raise ExecutionStop('judge_control_successor_required')
        return
    if group_key=='route8_daily_whole_judge_v1':
        if provider!='deepseek' or cap!=1 or phase_cap!=8 or group_used>=8:
            raise ExecutionStop('daily_judge_eight_request_cap')
        from graphrisk.agent.rank_successor import bounds,load
        if bounds(load(db,campaign))!=(8,5_000_000,group_key):
            raise ExecutionStop('daily_judge_successor_required')
        return
    if group_key=='route6_deepseek_rank_v1':
        if provider!='deepseek' or group_used>=6:raise ExecutionStop('route6_six_request_cap')
        return
    if group_key=='route6_deepseek_rank_200_v1':
        if provider!='deepseek' or group_used>=200:raise ExecutionStop('route6_two_hundred_request_cap')
        return
    if group_key=='route6_deepseek_rank_500_v1':
        if provider!='deepseek' or group_used>=500:raise ExecutionStop('route6_five_hundred_request_cap')
        return
    if group_key=='route6_deepseek_rich_500_v1':
        if provider!='deepseek' or group_used>=500:raise ExecutionStop('route6_rich_five_hundred_request_cap')
        return
    if group_key=='route6_deepseek_rich_cont_v1':
        if provider!='deepseek' or group_used>=500:raise ExecutionStop('route6_rich_cont_request_cap')
        return
    if group_key=='route9_full_cases_v1':
        if cap!=5 or phase_cap!=325 or group_used>=325:
            raise ExecutionStop('route9_formal_attempt_cap')
        return  # Exact scope and payload already checked inside budget.reserve.
    if group_key not in ('route3','route4','route5'):raise ExecutionStop('unregistered_session_group')
    # Route2 and every registered development episode share the proposed cap60.
    used=db.execute("SELECT count(*) FROM execution_requests r JOIN model_attempts a USING(attempt_id) LEFT JOIN autonomous_phases p USING(phase) LEFT JOIN autonomous_sessions sx ON p.session_id=sx.session_id LEFT JOIN autonomous_campaigns cx ON sx.campaign_id=cx.campaign_id WHERE a.provider=? AND (r.phase LIKE 'route2-%' OR p.session_id IS NOT NULL) AND COALESCE(cx.group_key,'') NOT IN ('route5_supplement_v3','route5_ieee_v12_20261025')",(provider,)).fetchone()[0]
    if used>=60:raise ExecutionStop('development_allocation_limit')


class SessionStore:
    def __init__(self,budget):
        self.budget=budget
        with budget.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS autonomous_campaigns(campaign_id TEXT PRIMARY KEY, manifest_sha TEXT NOT NULL, cap_each INTEGER NOT NULL)')
            if 'group_key' not in {r[1] for r in db.execute('PRAGMA table_info(autonomous_campaigns)')}:
                db.execute("ALTER TABLE autonomous_campaigns ADD COLUMN group_key TEXT NOT NULL DEFAULT 'route3'")
            db.execute('CREATE TABLE IF NOT EXISTS autonomous_sessions(session_id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL,manifest_sha TEXT NOT NULL,provider TEXT NOT NULL,max_calls INTEGER NOT NULL,deadline REAL NOT NULL,checkpoint_sha TEXT,checkpoint_path TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS autonomous_phases(phase TEXT PRIMARY KEY,session_id TEXT NOT NULL,slot INTEGER NOT NULL,UNIQUE(session_id,slot))')
            missing=db.execute("SELECT count(*) FROM execution_requests r LEFT JOIN autonomous_phases p USING(phase) WHERE r.phase LIKE 'loop-%' AND p.phase IS NULL").fetchone()[0]
            if missing:raise ExecutionStop('session_metadata_incomplete_no_reset')

    def used(self,session):
        with self.budget.transaction() as db:
            return db.execute('SELECT count(*) FROM execution_requests r JOIN autonomous_phases p USING(phase) WHERE p.session_id=?',(session,)).fetchone()[0]

    def campaign(self,identifier,manifest_sha,*,cap_each=15,group_key="route3"):
        if group_key=='route9_full_cases_v1':
            from graphrisk.agent.full_case_execution import campaign_guard
            with self.budget.transaction() as db:
                campaign_guard(db,identifier,manifest_sha,cap_each)
                old=db.execute('SELECT manifest_sha,cap_each,group_key FROM autonomous_campaigns WHERE campaign_id=?',(identifier,)).fetchone()
                if old is not None and old!=(manifest_sha,cap_each,group_key):raise ExecutionStop('campaign_registration_changed')
                if old is None:db.execute('INSERT INTO autonomous_campaigns VALUES(?,?,?,?)',(identifier,manifest_sha,cap_each,group_key))
            return
        if group_key not in ("route3","route4","route5","route5_supplement_v3","route5_ieee_v12_20261025","route5_daily_report_v1","route5_daily_candidate_v1","route5_daily_quotes_v2","route7_reference_planner_v1","route7_reference_planner_cont_v1","route7_reference_answer_v1","route7_case_reference_pair_v1","route7_boundary_probe_v1","route8_judge_controls_v1","route8_daily_whole_judge_v1","route6_deepseek_rank_v1","route6_deepseek_rank_200_v1","route6_deepseek_rank_500_v1","route6_deepseek_rich_500_v1","route6_deepseek_rich_cont_v1") or (group_key in ("route4","route5") and cap_each>10):raise ExecutionStop("invalid_campaign_group")
        if group_key in ('route5_daily_report_v1','route5_daily_candidate_v1','route5_daily_quotes_v2') and cap_each!=6:raise ExecutionStop('invalid_daily_report_campaign_cap')
        if group_key=='route7_reference_planner_v1' and cap_each!=24:raise ExecutionStop('invalid_reference_planner_campaign_cap')
        if group_key=='route7_reference_planner_cont_v1' and cap_each!=23:raise ExecutionStop('invalid_reference_planner_continuation_cap')
        if group_key=='route7_reference_answer_v1' and cap_each!=12:raise ExecutionStop('invalid_reference_answer_cap')
        if group_key=='route7_case_reference_pair_v1' and cap_each!=2:raise ExecutionStop('invalid_case_reference_cap')
        if group_key=='route7_boundary_probe_v1' and cap_each!=6:raise ExecutionStop('invalid_reference_boundary_cap')
        if group_key=='route8_daily_whole_judge_v1' and cap_each!=8:raise ExecutionStop('invalid_daily_judge_cap')
        if group_key=='route8_judge_controls_v1' and cap_each!=12:raise ExecutionStop('invalid_judge_control_cap')
        if type(cap_each) is not int or not 1<=cap_each<=(500 if group_key in ('route6_deepseek_rank_500_v1','route6_deepseek_rich_500_v1','route6_deepseek_rich_cont_v1') else 200 if group_key=='route6_deepseek_rank_200_v1' else 100 if group_key=='route5_supplement_v3' else 50 if group_key=='route5_ieee_v12_20261025' else 24 if group_key=='route7_reference_planner_v1' else 23 if group_key=='route7_reference_planner_cont_v1' else 15):raise ExecutionStop('invalid_campaign_cap')
        with self.budget.transaction() as db:
            old=db.execute('SELECT manifest_sha,cap_each,group_key FROM autonomous_campaigns WHERE campaign_id=?',(identifier,)).fetchone()
            if old is not None and old!=(manifest_sha,cap_each,group_key):raise ExecutionStop('campaign_registration_changed')
            if old is None:db.execute('INSERT INTO autonomous_campaigns(campaign_id,manifest_sha,cap_each,group_key) VALUES(?,?,?,?)',(identifier,manifest_sha,cap_each,group_key))

    def open(self,session,campaign,manifest,provider,*,calls=5,seconds=600):
        if provider not in ('deepseek','local') or type(calls) is not int or not 1<=calls<=5 or not 0<seconds<=600:raise ExecutionStop('invalid_session_limits')
        sha=digest(manifest)
        with self.budget.transaction() as db:
            if not db.execute('SELECT 1 FROM autonomous_campaigns WHERE campaign_id=?',(campaign,)).fetchone():raise ExecutionStop('campaign_not_registered')
            if db.execute('SELECT group_key FROM autonomous_campaigns WHERE campaign_id=?',(campaign,)).fetchone()==('route9_full_cases_v1',):
                from graphrisk.agent.full_case_execution import open_guard
                open_guard(db,session,campaign,manifest,provider,calls,seconds)
            old=db.execute('SELECT campaign_id,manifest_sha,provider,max_calls,deadline FROM autonomous_sessions WHERE session_id=?',(session,)).fetchone()
            if old is not None:
                if old[:4]!=(campaign,sha,provider,calls):raise ExecutionStop('session_registration_changed')
                return old[4]
            deadline=time.time()+seconds
            db.execute('INSERT INTO autonomous_sessions VALUES(?,?,?,?,?,?,NULL,NULL)',(session,campaign,sha,provider,calls,deadline))
            return deadline

    def register_packet(self,session,slot,campaign_sha,provider):
        phase='loop-'+session+'-'+str(slot)
        with self.budget.transaction() as db:
            row=db.execute('SELECT provider,max_calls FROM autonomous_sessions WHERE session_id=?',(session,)).fetchone()
            if row is None or row[0]!=provider or not 0<=slot<row[1]:raise ExecutionStop('unregistered_session_slot')
            wanted=(campaign_sha,int(provider=='deepseek'),int(provider=='local'))
            old=db.execute('SELECT manifest_sha,cloud_cap,local_cap FROM execution_phases WHERE phase=?',(phase,)).fetchone()
            if old is not None and old!=wanted:raise ExecutionStop('session_packet_changed')
            linked=db.execute('SELECT session_id,slot FROM autonomous_phases WHERE phase=?',(phase,)).fetchone()
            if linked is not None and linked!=(session,slot):raise ExecutionStop('session_phase_binding_changed')
            if old is None:db.execute('INSERT INTO execution_phases VALUES(?,?,?,?)',(phase,*wanted))
            if linked is None:db.execute('INSERT INTO autonomous_phases VALUES(?,?,?)',(phase,session,slot))
        return phase

    def checkpoint(self,session,directory,state):
        directory.mkdir(parents=True,exist_ok=True)
        text=canonical(state);sha=digest(state)
        path=(directory/(sha+'.json')).resolve()
        if not path.exists():
            temporary=path.with_suffix('.tmp');temporary.write_bytes(text.encode('utf-8'));os.replace(temporary,path)
        elif file_hash(path)!=sha:raise ExecutionStop('checkpoint_content_changed')
        with self.budget.transaction() as db:
            if not db.execute('SELECT 1 FROM autonomous_sessions WHERE session_id=?',(session,)).fetchone():raise ExecutionStop('session_not_registered')
            db.execute('UPDATE autonomous_sessions SET checkpoint_sha=?,checkpoint_path=? WHERE session_id=?',(sha,str(path),session))
        return sha

    def restore(self,session,directory):
        with self.budget.transaction() as db:
            row=db.execute('SELECT checkpoint_sha,checkpoint_path FROM autonomous_sessions WHERE session_id=?',(session,)).fetchone()
        if row is None:raise ExecutionStop('session_not_registered')
        if row[0] is None:return None
        path=Path(row[1]).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file() or file_hash(path)!=row[0]:raise ExecutionStop('checkpoint_missing_or_modified')
        return json.loads(path.read_text(encoding='utf-8'))
