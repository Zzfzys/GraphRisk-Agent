"""One reviewed action request; persistent reservation precedes every model POST."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_CEILING
import json
import os
from pathlib import Path
import re
import threading
import time

import httpx
import psutil

from graphrisk.agent.autonomous_actions import ActionFailure, ModelAction, SYSTEM_PROMPT, check_action, active_action_schema
from graphrisk.agent.autonomous_budget import ExecutionStop, InvestigationBudget
from graphrisk.agent.autonomous_http import post_one
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash


def credentials(runtime: Path) -> str:
    values={}
    path=runtime/".env.stage08-deepseek"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                name,value=line.split("=",1)
                if name.strip() in {"DEEPSEEK_API_KEY","DEEPSEEK_BASE_URL"}:values[name.strip()]=value.strip().strip("\"'")
    key=os.getenv("DEEPSEEK_API_KEY") or values.get("DEEPSEEK_API_KEY","")
    base=os.getenv("DEEPSEEK_BASE_URL") or values.get("DEEPSEEK_BASE_URL","https://api.deepseek.com")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,256}",key) or base.rstrip("/") not in {"https://api.deepseek.com","https://api.deepseek.com/v1"}:
        raise ActionFailure("official_credentials_unconfigured")
    return key  # Never import the legacy LIVE flag.


def cloud_usage(body, profile):
    usage=body.get("usage",{})
    names=("prompt_tokens","completion_tokens","total_tokens","prompt_cache_hit_tokens","prompt_cache_miss_tokens")
    if any(type(usage.get(k)) is not int or usage[k]<0 for k in names):raise ActionFailure("provider_usage_unknown")
    if usage['prompt_tokens']!=usage['prompt_cache_hit_tokens']+usage['prompt_cache_miss_tokens'] or usage['total_tokens']!=usage['prompt_tokens']+usage['completion_tokens']:
        raise ActionFailure("provider_usage_unknown")
    if usage['prompt_tokens']>profile['deepseek']['context_limit'] or usage['completion_tokens']>profile['max_output_tokens']:
        raise ActionFailure("provider_usage_outside_reservation_unknown")
    rates=profile['deepseek']['peak_cny_per_million']
    cost=int((Decimal(rates['cache_hit'])*usage['prompt_cache_hit_tokens']+Decimal(rates['cache_miss'])*usage['prompt_cache_miss_tokens']+Decimal(rates['output'])*usage['completion_tokens']).to_integral_value(rounding=ROUND_CEILING))
    return {k:usage[k] for k in names},cost


def reservation_micro(profile):
    # UTF8 byte guard is NOT an exact vendor tokenizer. Reserve the official full
    # context at cache-miss peak price, plus maximum output, then settle usage.
    rates=profile['deepseek']['peak_cny_per_million']
    return int((Decimal(rates['cache_miss'])*profile['deepseek']['context_limit']+Decimal(rates['output'])*profile['max_output_tokens']).to_integral_value(rounding=ROUND_CEILING))


class LocalSampler:
    def __init__(self, transport=None):
        self.transport=transport;self.samples=[];self.stop=threading.Event()
    def __enter__(self):
        def work():
            with httpx.Client(transport=self.transport,timeout=2,trust_env=False) as client:
                while not self.stop.is_set():
                    sample={'ram_available_bytes':psutil.virtual_memory().available}
                    try:sample['models']=client.get('http://127.0.0.1:11434/api/ps').raise_for_status().json()['models']
                    except Exception:sample['models']=None
                    self.samples.append(sample);self.stop.wait(.5)
        self.thread=threading.Thread(target=work,daemon=True);self.thread.start();return self
    def __exit__(self,*_):self.stop.set();self.thread.join(timeout=4)


class ActionProvider:
    def __init__(self, provider: str, *, profile: dict, budget: InvestigationBudget,
                 campaign: dict, campaign_sha: str, phase: str, output: Path, runtime: Path,
                 live: bool=False, transport=None, replay_only: bool=False, remaining=None):
        if provider not in {'deepseek','local'}:raise ActionFailure('unknown_provider')
        if digest(campaign)!=campaign_sha or campaign.get('profile_sha256')!=digest(profile):raise ActionFailure('campaign_or_profile_not_approved')
        fake=type(transport) is httpx.MockTransport
        if (budget.mode=='fixture')!=fake or (transport is not None and not fake):raise ActionFailure('transport_ledger_mode_mismatch')
        if not fake and not replay_only and (not live or os.getenv('GRAPHRISK_UPGRADE_LIVE')!='1'):raise ActionFailure('upgrade_live_opt_in_required')
        self.provider,self.profile,self.budget,self.campaign=provider,profile,budget,campaign
        self.campaign_sha,self.phase,self.output,self.runtime=campaign_sha,phase,output,runtime
        self.transport,self.fake,self.replay_only=transport,fake,replay_only
        self.remaining=remaining
        self.output.mkdir(parents=True,exist_ok=True)
        self.packets={p['logical_key']:p for p in campaign['packets']}
        # The runner loads the approved hash from the independently registered
        # phase. A caller-supplied reviewed boolean is never a capability.
        with budget.transaction() as db:
            row=db.execute('SELECT manifest_sha FROM execution_phases WHERE phase=?',(phase,)).fetchone()
            if row!=(campaign_sha,):raise ActionFailure('campaign_not_registered_in_shared_ledger')

    def system_prompt(self,packet):return SYSTEM_PROMPT

    def output_schema(self,packet):return active_action_schema(packet)

    def check_output(self,content,packet):return check_action(content,packet)

    def payload(self, packet):
        messages=[{'role':'system','content':self.system_prompt(packet)},{'role':'user','content':canonical(packet['view'])}]
        if self.provider=='deepseek':
            return {'model':self.profile['deepseek']['model'],'messages':messages,'temperature':0,
                    'max_tokens':self.profile['max_output_tokens'],'thinking':{'type':'disabled'},'stream':False,
                    'response_format':{'type':'json_object'}}
        return {'model':self.profile['local']['model'],'system':self.system_prompt(packet),'prompt':messages[1]['content'],
                'format':self.output_schema(packet),'stream':False,'think':False,
                'keep_alive':self.profile['local']['keep_alive'],
                'options':{'temperature':0,'num_ctx':self.profile['local']['num_ctx'],'num_predict':self.profile['max_output_tokens']}}

    def preflight(self):
        if self.provider=='deepseek':
            if not self.fake and self.profile['deepseek']['pricing_verified_date']!=date.today().isoformat():
                raise ActionFailure('pricing_refresh_required_before_new_live_requests')
            return {'key':'fixture-key-not-real' if self.fake else credentials(self.runtime)}
        with httpx.Client(transport=self.transport,timeout=10,trust_env=False,follow_redirects=False) as client:
            tags=client.get('http://127.0.0.1:11434/api/tags').raise_for_status().json()['models']
            expected=self.profile['local']
            if not any(m.get('name')==expected['model'] and m.get('digest')==expected['digest'] for m in tags):raise ActionFailure('local_digest_changed')
            models=client.get('http://127.0.0.1:11434/api/ps').raise_for_status().json()['models']
        if any(m.get('name')!=expected['model'] for m in models):raise ActionFailure('other_loaded_model_do_not_evict')
        if any(m.get('digest')!=expected['digest'] or m.get('context_length')!=expected['num_ctx'] for m in models):raise ActionFailure('local_loaded_revision_or_context_changed')
        minimum=expected['idle_ram_min_gib'] if not models else expected['running_ram_min_gib']
        if not self.fake and psutil.virtual_memory().available<minimum*2**30:raise ActionFailure('local_resource_headroom_insufficient')
        return {'residency_before':'warm' if models else 'cold'}

    def _analyze(self, receipt, packet):
        body=receipt['body'];hard=False
        if self.provider=='deepseek':
            usage,cost=cloud_usage(body,self.profile)
            model=body.get('model');fingerprint=body.get('system_fingerprint')
            if model not in {self.profile['deepseek']['model'],self.profile['deepseek']['documented_revision']} or not isinstance(fingerprint,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',fingerprint):hard=True
            identity=canonical([model,fingerprint])
            choices=body.get('choices',[])
            message=choices[0].get('message',{}) if isinstance(choices,list) and len(choices)==1 else {}
            content=message.get('content','');finish=choices[0].get('finish_reason') if len(choices)==1 else None
            expected_thinking=(self.profile.get('report_generation_profile') in ('quality-v3-thinking','quality-v3-thinking-low') and self.profile.get('report_thinking') is True)
            hard=hard or receipt.get('unexpected_reasoning',False) or (not expected_thinking and body.get('usage',{}).get('completion_tokens_details',{}).get('reasoning_tokens',0)!=0)
            if usage['total_tokens']>self.profile.get('cloud_total_token_budget',self.profile['local']['num_ctx']):hard=True
            output_bad=message.get('role')!='assistant' or bool(message.get('tool_calls'))
            timing={}
        else:
            names=('prompt_eval_count','eval_count')
            if any(type(body.get(k)) is not int or body[k]<0 for k in names):raise ActionFailure('provider_usage_unknown')
            usage={'prompt_tokens':body['prompt_eval_count'],'completion_tokens':body['eval_count'],'total_tokens':body['prompt_eval_count']+body['eval_count']}
            cost=0;model=body.get('model');fingerprint=None
            identity=canonical([model,self.profile['local']['digest'],self.profile['local']['num_ctx'],False])
            content=body.get('response','');finish=body.get('done_reason')
            hard=model!=self.profile['local']['model'] or usage['total_tokens']>self.profile['local']['num_ctx'] or usage['completion_tokens']>self.profile['max_output_tokens'] or receipt.get('unexpected_reasoning',False)
            output_bad=body.get('done') is not True
            timing={k:body.get(k) for k in ('total_duration','load_duration','prompt_eval_duration','eval_duration')}
        code='backend_identity_or_context_violation' if hard else 'output_truncated_or_invalid' if finish!='stop' or output_bad else 'ok'
        action=None;action_error=None
        if code=='ok':
            try:action=self.check_output(content,packet).model_dump(mode='json')
            except ActionFailure as error:
                code='invalid_model_action';action_error={'code':error.issue_code}
        if receipt.get('resource_contract_pass') is False:code='local_running_resource_limit'
        return {'provider':self.provider,'model':model,'fingerprint':fingerprint,'identity':identity,'usage':usage,
                'reasoning_observed':receipt.get('reasoning_observed',False),
                'reasoning_tokens':body.get('usage',{}).get('completion_tokens_details',{}).get('reasoning_tokens') if self.provider=='deepseek' else None,
                'estimated_peak_cost_micro_cny':cost,'bill_reconciled':False,'code':code,'action':action,'action_error':action_error,
                'schema_and_binding_valid':code=='ok','elapsed_ms':receipt['elapsed_ms'],'timing_ns':timing,
                'residency_before':receipt.get('residency_before'),'resource_samples':receipt.get('resource_samples',[]),
                'integrity_stop':hard,'message_sha256':packet['messages_sha256']}

    def post_payload(self, payload, *, headers, timeout):
        return post_one(self.profile[self.provider]['endpoint'], payload, headers=headers,
                        timeout=timeout, transport=self.transport)

    def generate(self, logical_key: str):
        if logical_key not in self.packets:raise ActionFailure('unregistered_packet_key')
        packet=self.packets[logical_key]
        if digest(self.campaign)!=self.campaign_sha or digest(self.profile)!=self.campaign['profile_sha256']:raise ActionFailure('campaign_or_profile_changed_after_review')
        if packet['view_sha256']!=digest(packet['view']) or packet['messages_sha256']!=digest([self.system_prompt(packet),canonical(packet['view'])]):raise ActionFailure('packet_changed_after_review')
        request_key=digest([self.campaign_sha,self.provider,logical_key])
        payload=self.payload(packet);payload_sha=digest(payload)
        if self.provider=='deepseek' and payload.get('response_format',{}).get('type')=='json_object' and not any('json' in m.get('content','').lower() for m in payload.get('messages',[])):
            raise ActionFailure('json_mode_requires_explicit_json_instruction')
        prior=self.budget.lookup(request_key)
        if prior:
            if prior['packet_sha']!=payload_sha:raise ActionFailure('saved_request_payload_changed')
            if prior['integrity_stop']:raise ExecutionStop('integrity_review_required')
            if not prior['receipt_sha']:raise ExecutionStop('saved_request_unknown_do_not_resend')
            path=Path(prior['receipt_path']).resolve()
            if not path.is_relative_to(self.output.resolve()) or file_hash(path)!=prior['receipt_sha']:raise ExecutionStop('saved_response_missing_or_changed')
            receipt=json.loads(path.read_text(encoding='utf-8'))
            if receipt['attempt_id']!=prior['attempt_id'] or receipt['payload_sha256']!=payload_sha or receipt['profile_sha256']!=digest(self.profile):
                raise ExecutionStop('saved_response_request_binding_changed')
            analyzed=self._analyze(receipt,packet)
            if prior['state']!='settled':
                self.budget.settle(prior['attempt_id'],cost_micro=analyzed['estimated_peak_cost_micro_cny'],receipt_sha=prior['receipt_sha'],receipt_path=str(path),identity=analyzed['identity'],usage=analyzed['usage'],result_code=analyzed['code'],integrity_stop=analyzed['integrity_stop'])
            return {**analyzed,'attempt_id':prior['attempt_id'],'replayed_without_model_request':True}
        if self.replay_only:raise ExecutionStop('replay_result_missing_no_model_request_allowed')
        preflight=self.preflight()
        key=preflight.get('key','')
        if key and key in canonical(payload):raise ActionFailure('credential_in_reviewed_input')
        timeout=self.profile['timeout_seconds']
        if self.remaining is not None:
            timeout=min(timeout,self.remaining())
            if timeout<=0:raise ExecutionStop('session_deadline_reached')
        attempt=self.budget.reserve(provider=self.provider,request_key=request_key,packet_sha=payload_sha,phase=self.phase,
                                    reserve_micro=reservation_micro(self.profile) if self.provider=='deepseek' else 0)
        started=time.perf_counter();sampler=None
        try:
            if self.provider=='local' and not self.fake:
                sampler=LocalSampler();sampler.__enter__()
            body=self.post_payload(payload,headers={'Authorization':'Bearer '+key} if key else {},timeout=timeout)
            samples=sampler.samples if sampler else []
            if sampler:sampler.__exit__();sampler=None
            if self.provider=='local' and not self.fake:
                with httpx.Client(timeout=5,trust_env=False) as client:
                    models=client.get('http://127.0.0.1:11434/api/ps').raise_for_status().json()['models']
                samples.append({'ram_available_bytes':psutil.virtual_memory().available,'models':models})
            if key and key in canonical(body):raise ActionFailure('credential_echo_response_rejected')
            unexpected=bool(body.pop('thinking',None))
            for choice in body.get('choices',[]):
                unexpected=bool(choice.get('message',{}).pop('reasoning_content',None)) or unexpected
            reasoning_observed=unexpected
            if self.provider=='deepseek' and self.profile.get('report_generation_profile') in ('quality-v3-thinking','quality-v3-thinking-low') and self.profile.get('report_thinking') is True:
                unexpected=False  # Reasoning was discarded above; only final JSON and usage persist.
            resource_ok=all(s['ram_available_bytes']>=self.profile['local']['running_ram_min_gib']*2**30 for s in samples)
            if self.provider=='local' and not self.fake:
                seen=[m for s in samples for m in (s.get('models') or []) if m.get('name')==self.profile['local']['model']]
                if not seen or any(m.get('digest')!=self.profile['local']['digest'] or m.get('context_length')!=self.profile['local']['num_ctx'] for m in seen):
                    unexpected=True
            receipt={'attempt_id':attempt,'payload_sha256':payload_sha,'profile_sha256':digest(self.profile),
                     'reasoning_observed':reasoning_observed,
                     'body':body,'unexpected_reasoning':unexpected,'elapsed_ms':round((time.perf_counter()-started)*1000,3),
                     'resource_samples':samples,'resource_contract_pass':resource_ok,**{k:v for k,v in preflight.items() if k!='key'}}
            path=(self.output/(attempt+'.json')).resolve();temp=path.with_suffix('.tmp')
            temp.write_text(canonical(receipt)+'\n',encoding='utf-8');os.replace(temp,path)
            sha=file_hash(path);self.budget.attach_receipt(attempt,sha,str(path))
            analyzed=self._analyze(receipt,packet)
        except Exception as error:
            if sampler:sampler.__exit__()
            # Preserve safe HTTP diagnostics for reconciliation; never the body,
            # auth headers, URL query, or an arbitrary exception string.
            status=getattr(error,'http_status',None)
            if type(status) is int:
                diagnostics=self.output/'failures';diagnostics.mkdir(exist_ok=True)
                (diagnostics/(attempt+'.json')).write_text(canonical({'attempt_id':attempt,'payload_sha256':payload_sha,
                    'http_status':status,'request_id':getattr(error,'request_id',None),'reason':str(error)}),encoding='utf-8')
            self.budget.unknown(attempt,str(error) if isinstance(error,(ActionFailure,ValueError)) and re.fullmatch(r'[a-z_]+',str(error)) else 'model_response_unknown')
            raise ExecutionStop('model_request_unknown_saved_do_not_retry') from None
        self.budget.settle(attempt,cost_micro=analyzed['estimated_peak_cost_micro_cny'],receipt_sha=sha,
                           receipt_path=str(path),identity=analyzed['identity'],usage=analyzed['usage'],
                           result_code=analyzed['code'],integrity_stop=analyzed['integrity_stop'])
        return {**analyzed,'attempt_id':attempt,'replayed_without_model_request':False}
