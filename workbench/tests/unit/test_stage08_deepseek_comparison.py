from copy import deepcopy
import json
import httpx
import pytest
from graphrisk.agent.deepseek_comparison import MODEL, BASE, load_environment, account_preflight, credentials, deepseek_attempt
from graphrisk.agent.comparison_providers import ComparisonFailure
from graphrisk.agent.provider_comparison import load_suite, messages
from graphrisk.agent.provider_budget import EvaluationBudget
KEY='fake-deepseek-private-not-real'

@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY',KEY)
    monkeypatch.setenv('DEEPSEEK_BASE_URL',BASE)
    monkeypatch.delenv('GRAPHRISK_STAGE08_LIVE',raising=False)


def rig(tmp_path,env,mode=None):
    case=load_suite()[0]; calls=[]; budget=EvaluationBudget(tmp_path/'ledger')
    def handler(request):
        calls.append(request)
        payload=json.loads(request.content); limit=payload['max_tokens']
        if mode=='timeout': raise httpx.ReadTimeout(KEY)
        if mode=='redirect': return httpx.Response(302,headers={'location':'https://attacker.invalid'},text=KEY)
        if mode=='auth': return httpx.Response(401,text=KEY)
        prompt=400 if mode!='counter' or limit==1 else 401
        body={'model':MODEL,'system_fingerprint':'fp_fixture','usage':{'prompt_tokens':prompt,'completion_tokens':limit if limit==1 else 50,'prompt_cache_hit_tokens':0,'prompt_cache_miss_tokens':prompt,'total_tokens':prompt+(limit if limit==1 else 50)},'choices':[{'finish_reason':'length' if limit==1 else 'stop','message':{'role':'assistant','content':'{' if limit==1 else json.dumps(case['expected'])}}]}
        if mode=='usage': body['usage']['prompt_tokens']=True
        if mode=='overlimit': body['usage'].update(prompt_tokens=5000,prompt_cache_miss_tokens=5000,total_tokens=5000+body['usage']['completion_tokens'])
        if mode=='revision': body['model']='other'
        if mode=='fingerprint' and limit==512: body['system_fingerprint']='fp_changed'
        if mode=='secret': body['choices'][0]['message']['content']=KEY
        if mode=='tool': body['choices'][0]['message']['tool_calls']=[{'name':'freeze'}]
        if mode=='thinking': body['usage']['completion_tokens_details']={'reasoning_tokens':1}
        if mode=='bad_answer' and limit==512: body['choices'][0]['message']['content']='{}'
        return httpx.Response(200,json=body)
    return case,budget,calls,httpx.MockTransport(handler)


def test_actual_usage_calibration_same_messages_and_upper_cost(tmp_path,env):
    case,budget,calls,t=rig(tmp_path,env)
    row=deepseek_attempt(case,budget,transport=t)
    assert row['error'] is None and row['score']['passed']
    assert len(calls)==2
    for r in calls:
        p=json.loads(r.content)
        assert p['messages']==messages(case) and p['thinking']=={'type':'disabled'}
        assert p['model']==MODEL and 'tools' not in p
    assert row['calibration']['prompt_tokens']==row['prompt_tokens']==400
    assert budget.snapshot()=={'accounted_micro_cny':2008,'pending_attempts':0}
    assert KEY not in json.dumps(row)

@pytest.mark.parametrize('mode,error,pending',[
    ('timeout','transport_timeout',1),('redirect','redirect_rejected',1),('auth','authentication_failed',1),
    ('usage','invalid_usage',1),('overlimit','token_limit_or_counter_mismatch',0),
    ('counter','token_limit_or_counter_mismatch',0),('revision','model_revision_mismatch',0),
    ('fingerprint','backend_fingerprint_changed',0),('secret','invalid_or_sensitive_output',0),
    ('tool','unexpected_reasoning_or_tools',0),('thinking','unexpected_reasoning_or_tools',0),
])
def test_failure_is_retained_and_never_reports_success(tmp_path,env,mode,error,pending):
    case,budget,calls,t=rig(tmp_path,env,mode)
    row=deepseek_attempt(case,budget,transport=t)
    assert row['error']==error and not row['score']['passed']
    assert budget.snapshot()['pending_attempts']==pending
    assert KEY not in json.dumps(row)
    if mode in ('timeout','overlimit','counter'):
        n=len(calls); again=deepseek_attempt(case,EvaluationBudget(budget.path),transport=t)
        assert again['error']=='budget_unreconciled' and len(calls)==n


def test_bad_answer_is_paid_but_not_fixed(tmp_path,env):
    case,budget,calls,t=rig(tmp_path,env,'bad_answer')
    row=deepseek_attempt(case,budget,transport=t)
    assert row['error'] is None and not row['score']['passed'] and len(calls)==2
    assert budget.snapshot()['accounted_micro_cny']>0


def test_input_and_live_guards_precede_requests(tmp_path,env):
    case,budget,calls,t=rig(tmp_path,env)
    changed=deepcopy(case); changed['evidence'][0]['note']='private row'
    with pytest.raises(ValueError,match='unreviewed'): deepseek_attempt(changed,budget,transport=t)
    with pytest.raises(ComparisonFailure,match='live_opt_in'): deepseek_attempt(case,budget,live=True)
    assert not calls and budget.snapshot()['accounted_micro_cny']==0


def test_environment_no_override_and_endpoint_allowlist(tmp_path,env,monkeypatch):
    p=tmp_path/'private'; p.write_text('DEEPSEEK_API_KEY=second-fake-key\nDEEPSEEK_BASE_URL=https://evil.invalid\n')
    load_environment(p); assert credentials()==KEY
    monkeypatch.setenv('DEEPSEEK_BASE_URL','https://api.deepseek.com.evil.invalid')
    with pytest.raises(ComparisonFailure,match='official_endpoint'): credentials()
    p.write_text('UNKNOWN=hidden-key')
    with pytest.raises(ComparisonFailure,match='invalid_private'): load_environment(p)


def test_account_probe_is_read_only_and_sanitized(env):
    calls=[]
    def handler(r):
        calls.append(r)
        return httpx.Response(200,json={'data':[{'id':MODEL}]} if r.url.path=='/models' else {'is_available':True,'balance_infos':[{'currency':'CNY','total_balance':'1.00'}]})
    assert account_preflight(transport=httpx.MockTransport(handler))['balance_available']
    assert all(r.method=='GET' for r in calls)
