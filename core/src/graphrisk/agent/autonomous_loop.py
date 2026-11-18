"""Bounded LangGraph decision/dispatch/report/verification with durable receipts."""
from __future__ import annotations

import json
from pathlib import Path
import time
from typing import TypedDict

from langgraph.graph import START,END,StateGraph

from graphrisk.agent.autonomous_actions import SYSTEM_PROMPT,prepare_packet,ActionFailure,active_action_schema
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.autonomous_tools import SPEC_BY_NAME,ToolBoundaryError,ToolObservation,bind_call
from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.autonomous_report import REPORT_PROMPT,EvidenceDraft,verify_summary
from graphrisk.agent.upgrade_contract import canonical,digest,file_hash


class GraphState(TypedDict):
    route: str


SUMMARY_CONTRACT = 'evidence-summary-only-v1'


def latest_observations(observations):
    # Superseded query-specific evidence remains in the audit, not silently
    # pooled into a second population or an ever-growing model context.
    latest={o.call.name:o for o in observations if o.call.name!='retrieve_policy'}
    return tuple(latest[n] for n in SPEC_BY_NAME if n in latest)+tuple(o for o in observations if o.call.name=='retrieve_policy')


def packet_for(scope,observations,registry,profile,*,kind,remaining,remaining_tools,feedback,task="investigate",
               report_contract=SUMMARY_CONTRACT):
    current=latest_observations(observations)
    if kind=='report' and report_contract!=SUMMARY_CONTRACT:
        from graphrisk.agent import judgment_v12_daily_quotes as quotes
        from graphrisk.agent.autonomous_tools import DailyInvestigationScope
        if report_contract!=quotes.CONTRACT or not isinstance(scope,DailyInvestigationScope):
            raise ActionFailure('daily_loop_report_contract_required')
        packet=quotes.packet_for(scope,current,registry,profile)
        packet['view']['workflow']={'remaining_model_calls':remaining,'remaining_tools':remaining_tools,
            'feedback':list(feedback),'allowed_actions':[], 'terminal_budget_window':True}
        packet.update(kind=kind,report_contract=report_contract,view_sha256=digest(packet['view']),
            messages_sha256=digest([quotes.PROMPT,canonical(packet['view'])]))
        packet['prompt_utf8_bytes']=len((quotes.PROMPT+canonical(packet['view'])).encode('utf-8'))
        if packet['prompt_utf8_bytes']>profile['max_prompt_utf8_bytes']:raise ActionFailure('loop_context_limit')
        return packet
    packet=prepare_packet(scope,current,registry,task=task,profile=profile)
    view=packet['view'];names=list(SPEC_BY_NAME)
    view['previous_requests']=[[names.index(o.call.name),json.loads(o.call.arguments_json)] for o in observations]
    allowed=['call_tool','finish','stop_insufficient']
    terminal=remaining<=2 or remaining_tools==0
    if terminal:
        allowed=['finish','stop_insufficient'] if all(v!='not_requested' for v in view['tool_status'].values()) else ['stop_insufficient']
        view['tools']=[]  # A closed tool permission window, not a hidden retry.
    view['workflow']={'remaining_model_calls':remaining,'remaining_tools':remaining_tools,'feedback':list(feedback),'allowed_actions':allowed,
        'terminal_budget_window':terminal,
        'only_F_or_P_are_evidence_refs':True,'unmeasured_policy_conditions_cannot_be_measured_by_retrieval':True}
    system=SYSTEM_PROMPT
    packet['action_schema_sha256']=digest(active_action_schema(packet))
    if kind=='report':
        system=REPORT_PROMPT
        view.pop('tools');view.pop('previous_requests')
        packet['action_schema_sha256']=digest(EvidenceDraft.model_json_schema())
    packet.update(kind=kind,view_sha256=digest(view),messages_sha256=digest([system,canonical(view)]))
    packet['prompt_utf8_bytes']=len((system+canonical(view)).encode())
    if packet['prompt_utf8_bytes']>profile['max_prompt_utf8_bytes']:raise ActionFailure('loop_context_limit')
    return packet


def observation_rows(observations):
    return [{'name':o.call.name,'arguments':json.loads(o.call.arguments_json),'result_json':o.result_json} for o in observations]


def restore_observations(scope,rows):
    return tuple(ToolObservation(bind_call(scope,r['name'],r['arguments']),r['result_json']) for r in rows)


def progress_signature(bundle):
    return digest({'facts':[(f.metric,f.subject,f.unit,f.window_seconds,f.value,f.availability) for f in bundle.facts],
                   'policy_refs':sorted(p.policy_ref for p in bundle.policies),
                   'outcomes':sorted({(o.tool,o.status) for o in bundle.tool_outcomes})})


class BoundedInvestigation:
    def __init__(self,*,scope,registry,profile,limits,store,session,directory,deadline,
                 tool_batch,provider_factory,source_guard,seed_observations=(),controlled_probe=False,
                 task="investigate",conversation_observations=(),parent_evidence_version=None,
                 report_contract=SUMMARY_CONTRACT):
        if task not in ("investigate","explain_evidence","policy_conditions","continue_checks"):
            raise ExecutionStop("unsupported_conversation_task")
        self.task=task
        self.report_contract=report_contract
        self.report_profile=profile
        if report_contract!=SUMMARY_CONTRACT:
            from graphrisk.agent import judgment_v12_daily_quotes as quotes
            from graphrisk.agent.autonomous_tools import DailyInvestigationScope
            from graphrisk.agent.report_generation_profile import quality_report_profile
            if report_contract!=quotes.CONTRACT or not isinstance(scope,DailyInvestigationScope):
                raise ExecutionStop('daily_loop_report_contract_required')
            if controlled_probe or seed_observations:
                raise ExecutionStop('daily_loop_requires_actual_investigation')
            self.report_profile=quality_report_profile(profile)
        self.scope,self.registry,self.profile,self.limits=scope,registry,profile,limits
        self.store,self.session,self.directory,self.deadline=store,session,Path(directory),deadline
        self.tool_batch,self.provider_factory,self.guard=tool_batch,provider_factory,source_guard
        self.controlled_probe=controlled_probe
        self.checkpoints=self.directory/'checkpoints'
        self.state=store.restore(session,self.checkpoints) or {
            'version':'bounded-investigation-v1.1','scope_sha256':scope.binding_sha256,'route':'bootstrap',
            'next_slot':0,'visits':0,'tool_requests':0,'repairs':0,'no_progress':0,'observations':[],
            'feedback':[],'trace':[],'first_drafts':[],'pending':None,'proposed':None,
            'report':None,'status':'running','stop_reason':None,'model_calls':0,
            'report_contract':report_contract,'report_profile_sha256':digest(self.report_profile)}
        if self.state['scope_sha256']!=scope.binding_sha256:raise ExecutionStop('checkpoint_scope_changed')
        if (self.state.get('report_contract',SUMMARY_CONTRACT)!=report_contract or
                self.state.get('report_profile_sha256',digest(profile))!=digest(self.report_profile)):
            raise ExecutionStop('checkpoint_report_contract_changed')
        if seed_observations and not self.state['observations'] and self.state['visits']==0:
            if not controlled_probe:raise ExecutionStop('unapproved_seeded_episode')
            build_evidence(scope,latest_observations(seed_observations),registry)
            self.state.update(observations=observation_rows(seed_observations),tool_requests=len(seed_observations),
                              route='report',stop_reason='controlled_fixed_evidence_probe')
        if conversation_observations and seed_observations:
            raise ExecutionStop('conflicting_seed_modes')
        if conversation_observations and not self.state['observations'] and self.state['visits']==0:
            bundle=build_evidence(scope,latest_observations(conversation_observations),registry)
            if bundle.evidence_version!=parent_evidence_version:
                raise ExecutionStop('conversation_parent_evidence_changed')
            self.state.update(observations=observation_rows(conversation_observations),route='decide',
                task=task,parent_evidence_version=parent_evidence_version)
        if self.state.get('task',task)!=task:
            raise ExecutionStop('conversation_task_changed')
        self.save()

    def save(self):self.store.checkpoint(self.session,self.checkpoints,self.state)

    def observations(self):return restore_observations(self.scope,self.state['observations'])

    def bundle(self):return build_evidence(self.scope,latest_observations(self.observations()),self.registry)

    def stop(self,reason,*,status='stopped'):
        self.state.update(route='end',status=status,stop_reason=reason)

    def model(self,kind):
        slot=self.state['next_slot']
        if slot>=self.limits['max_model_calls']:raise ExecutionStop('session_model_call_limit')
        pending=self.state['pending']
        if pending:
            if pending['kind']!=kind or pending['slot']!=slot:raise ExecutionStop('pending_request_kind_changed')
            path=self.directory/'packets'/f'{slot}.json'
            if file_hash(path)!=pending['sha256']:raise ExecutionStop('pending_packet_changed')
            campaign=json.loads(path.read_text(encoding='utf-8'))
        else:
            profile=self.report_profile if kind=='report' else self.profile
            packet=packet_for(self.scope,self.observations(),self.registry,profile,kind=kind,
                remaining=self.limits['max_model_calls']-slot,remaining_tools=self.limits['max_tool_calls']-self.state['tool_requests'],
                feedback=self.state['feedback'],task=self.task,report_contract=self.report_contract)
            packet['logical_key']=str(slot)
            campaign={'version':'loop-packet-v1','profile_sha256':digest(profile),'packets':[packet],
                'session_id':self.session,'scope_sha256':self.scope.binding_sha256,'kind':kind,'slot':slot}
            path=self.directory/'packets'/f'{slot}.json';path.parent.mkdir(parents=True,exist_ok=True)
            body=canonical(campaign).encode()
            if path.exists() and path.read_bytes()!=body:raise ExecutionStop('packet_file_already_exists_with_other_content')
            path.write_bytes(body)
            phase=self.store.register_packet(self.session,slot,digest(campaign),self.provider_factory.provider)
            self.state['pending']={'slot':slot,'kind':kind,'sha256':digest(campaign),'phase':phase}
            self.save()  # Exact packet survives a crash BEFORE/AFTER the POST.
        self.guard()
        packet=campaign['packets'][0]
        result=self.provider_factory(campaign,self.state['pending']['phase'],kind,self.deadline).generate(str(slot))
        self.state['next_slot']=slot+1
        self.state['model_calls']=self.store.used(self.session)
        self.state['pending']=None
        self.state['trace'].append({'event':'model','kind':kind,'slot':slot,'attempt_id':result['attempt_id'],
            'code':result['code'],'packet_sha256':digest(campaign),'evidence_version':self.bundle().evidence_version,
            'server_allowed_actions':packet['view']['workflow']['allowed_actions'],'terminal_budget_window':packet['view']['workflow']['terminal_budget_window'],
            'replayed':result['replayed_without_model_request'],'action':result['action'],'action_error':result['action_error']})
        self.save()
        return result,packet

    def execute(self,calls):
        before=self.bundle();observations=self.observations();known={o.call.request_sha256 for o in observations}
        unique={bind_call(self.scope,c['tool'],c['arguments']).request_sha256 for c in calls}-known
        if self.state['tool_requests']+len(unique)>self.limits['max_tool_calls']:raise ExecutionStop('session_tool_call_limit')
        rows,events=self.tool_batch(observation_rows(observations),calls)
        new=restore_observations(self.scope,rows)
        after=build_evidence(self.scope,latest_observations(new),self.registry)
        if not known<={o.call.request_sha256 for o in new}:raise ToolBoundaryError('tool_worker_dropped_observations')
        self.state['tool_requests']+=len({o.call.request_sha256 for o in new}-known)
        self.state['observations']=rows
        self.state['trace'].append({'event':'tools','calls':calls,'outcomes':events,'evidence_version':after.evidence_version})
        if progress_signature(before)==progress_signature(after):self.state['no_progress']+=1
        else:self.state['no_progress']=0
        self.state['feedback']=['no_information_gain'] if self.state['no_progress'] else []

    def bootstrap(self):
        self.execute([{'tool':'get_transaction','arguments':{}}])
        if next(o for o in self.bundle().tool_outcomes if o.tool=='get_transaction').status!='ok':
            self.stop('transaction_unavailable');return
        self.state['route']='decide'

    def decide(self):
        # A final report is a separate paid request; do not spend its last slot
        # on another action and quietly call a sixth time.
        if self.state['next_slot']>=self.limits['max_model_calls']-1:
            self.stop('model_limit_before_model_finish');return
        result,_=self.model('action')
        if result['code'] in ('local_running_resource_limit','backend_identity_or_context_violation'):
            self.stop('provider_resource_or_revision_stop');return
        if result['code']!='ok':
            if self.state['repairs']>=self.limits['max_repairs']:
                self.stop('action_repair_exhausted',status='rejected');return
            self.state['repairs']+=1
            self.state['feedback']=[(result['action_error'] or {}).get('code','invalid_action_json_or_schema')]
            self.state['route']='decide';return
        action=result['action']
        if action['action']=='call_tool':
            self.state['proposed']=[{'tool':c['tool'],'arguments':{k:v for k,v in c['arguments'].items() if v is not None}} for c in action['calls']]
            self.state['route']='dispatch'
        else:
            self.state['stop_reason']='model_finished' if action['action']=='finish' else 'model_insufficient'
            self.state['route']='report'

    def dispatch(self):
        self.execute(self.state['proposed']);self.state['proposed']=None
        if self.state['no_progress']>=self.limits['no_progress_limit']:self.stop('no_progress')
        else:self.state['route']='decide'

    def report(self):
        result,packet=self.model('report')
        self.state['first_drafts'].append({'attempt_id':result['attempt_id'],'draft':result['action'],
            'format_code':result['code'],'packet_slot':self.state['next_slot']-1})
        if result['code'] in ('local_running_resource_limit','backend_identity_or_context_violation'):
            self.stop('provider_resource_or_revision_stop');return
        self.state['draft']=result['action'];self.state['draft_format_code']=result['code']
        if self.controlled_probe and len(self.state['first_drafts'])==1 and result['action'] is not None:
            self.state['draft']={**result['action'],'score_meaning':'fraud_probability'}
            self.state['trace'].append({'event':'controlled_draft_fault_not_model_error',
                'original_sha256':digest(result['action']),'validation_input':self.state['draft']})
        self.state['report_packet']=packet;self.state['route']='verify'

    def verify(self):
        from graphrisk.agent.autonomous_report import EvidenceDraft
        errors=['report_schema_invalid'];report=None
        daily=self.report_contract!=SUMMARY_CONTRACT
        if daily:
            from graphrisk.agent import judgment_v12_daily_quotes as quotes
            issues=[{'code':'report_schema_invalid','path':'$','category':'schema'}]
            if self.state['draft_format_code']=='ok':
                packet=self.state['report_packet'];bundle=self.bundle()
                draft=quotes.parse(self.state['draft'],packet,bundle)
                issues,report=quotes.verify(draft,packet,bundle)
            errors=sorted({issue['code'] for issue in issues})
            self.state.setdefault('report_checks',[]).append({'packet_slot':self.state['next_slot']-1,
                'issues':issues,'native_guard_pass':not issues,'semantic_quality':'requires_review'})
        elif self.state['draft_format_code']=='ok':
            draft=EvidenceDraft.model_validate_json(canonical(self.state['draft']))
            errors,report=verify_summary(draft,self.state['report_packet'],self.bundle())
        self.state['trace'].append({'event':'verify_actual_draft','errors':errors,'report_sha256':report['report_sha256'] if report else None})
        if errors:
            if self.state['repairs']<self.limits['max_repairs'] and self.state['next_slot']<self.limits['max_model_calls']:
                self.state['repairs']+=1;self.state['feedback']=errors;self.state['route']='report'
            else:self.stop('daily_report_guard_rejected' if daily else 'summary_semantic_rejected',status='rejected')
        else:
            self.state['report']=report
            if daily:
                self.state.update(route='end',status='partial_report' if report['evidence_completeness']=='partial' or self.state['stop_reason']=='model_insufficient' else 'verified_report')
            else:
                self.state.update(route='end',status='partial_summary' if report['partial_tools'] or report['finding']=='evidence_insufficient' or self.state['stop_reason']=='model_insufficient' else 'verified_summary')

    def node(self,name):
        def run(_):
            if self.state['visits']>=self.limits['max_nodes']:
                self.stop('node_limit');self.save();return {'route':'end'}
            self.state['visits']+=1
            try:
                self.guard()
                if time.time()>=self.deadline:raise ExecutionStop('session_deadline_reached')
                getattr(self,name)()
                self.guard()
            except ToolBoundaryError:
                self.state['report']=None;self.stop('hard_source_or_tool_contract_failure',status='rejected')
            except (ExecutionStop,ActionFailure) as error:
                # Fixed codes only, never raw exception/arguments. Unknown cost
                # remains in the shared ledger and no repair call follows.
                code=str(error)
                allowed={'session_model_call_limit','session_tool_call_limit','session_deadline_reached',
                    'local_resource_headroom_insufficient','model_request_unknown_saved_do_not_retry','route4_group_model_call_limit',
                    'loop_context_limit','backend_revision_or_reservation_changed','pricing_refresh_required_before_new_live_requests',
                    'unresolved_attempt_blocks_execution','pause_for_budget_reconciliation','development_allocation_limit',
                    'campaign_model_call_limit','integrity_review_required'}
                allowed.add('route3_group_model_call_limit')
                self.state['report']=None;self.stop(code if code in allowed else 'execution_stopped_review_required')
            self.state['model_calls']=self.store.used(self.session)
            self.save();return {'route':self.state['route']}
        return run

    def run(self):
        if self.state['route']=='end':return self.state
        graph=StateGraph(GraphState)
        names=('bootstrap','decide','dispatch','report','verify')
        destinations={name:name for name in names}|{'end':END}
        for name in names:
            graph.add_node(name,self.node(name))
            graph.add_conditional_edges(name,lambda state:state['route'],destinations)
        graph.add_conditional_edges(START,lambda _:self.state['route'],destinations)
        graph.compile().invoke({'route':self.state['route']},{'recursion_limit':50})
        return self.state
