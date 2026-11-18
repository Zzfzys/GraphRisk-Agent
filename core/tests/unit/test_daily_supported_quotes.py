"""Bounded quotes and clause-local authorization checks, with counterexamples."""
from copy import deepcopy
import pytest

from test_judgment_v12_daily_review import daily_review, refs_for, offline_source_run
from test_daily_narrative import daily_scope
from test_autonomous_tool_contracts import scope,index,registry
from test_autonomous_action_providers import profile
from test_judgment_v3 import good
from graphrisk.agent import judgment_v3 as base, judgment_v12_daily_review as prior
from graphrisk.agent import judgment_v12_daily_quotes as candidate
from graphrisk.agent.autonomous_actions import ActionFailure
from graphrisk.agent.upgrade_contract import canonical,digest,file_hash


@pytest.fixture
def quotes(daily_review):
    _,bundle,original,profile=daily_review
    packet=candidate.prepare_packet(original,bundle,profile)
    return packet,bundle


def check(quotes,text,refs,field='rationale'):
    packet,bundle=quotes;value=good()
    value[field]={'text':text,'refs':refs} if field=='rationale' else [{'text':text,'refs':refs}]
    before=deepcopy(value)
    issues,report=candidate.verify(candidate.parse(value,packet,bundle),packet,bundle)
    assert value==before
    return issues,report


@pytest.mark.parametrize('text,refs',[
    ('目标交易金额为17.0，尚需核查授权。',['F1']),
    ('此前24小时匿名匹配历史交易数为2，历史基线仍需审查。',['F3']),
    ('已返回样本中共享设备字段的交易数为2，不证明同一人。',['F13']),
])
def test_only_supported_exact_quotes_pass_without_rewriting(quotes,text,refs):
    issues,report=check(quotes,text,refs)
    assert not issues
    assert report['draft']['rationale']['text']==text
    assert report['semantic_quality']=='requires_review' and not report['approval_eligible']
    assert report['fact_support']['witnesses']


@pytest.mark.parametrize('text,refs,code',[
    ('此前24小时匿名匹配历史交易数为3。',['F3'],'fact_claim_value_mismatch'),
    ('此前24小时匿名匹配历史交易数为2。',['F2'],'fact_claim_citation_required'),
    ('此前24小时历史均值为0。',['F5'],'fact_claim_unavailable'),
    ('已返回样本中共享设备字段的交易数为2。',['F1'],'fact_claim_citation_required'),
    ('目标交易金额为17美元，尚需核查。',['F1'],'fact_claim_currency_unverified'),
    ('目标交易金额为17万元，尚需核查。',['F1'],'fact_support_review_required'),
    ('目标交易金额为17.0，另外99笔待核。',['F1'],'unverified_numeric_prose_requires_review'),
    ('本笔欺诈概率为0.8。',['F8'],'fact_claim_probability_unsupported'),
    ('日终XGBoost主评分为0.8，是未校准信号，不是欺诈概率。',['F8'],'score_or_scope_number_program_owned'),
])
def test_wrong_missing_or_uninterpreted_claims_are_not_whitelisted(quotes,text,refs,code):
    issues,report=check(quotes,text,refs)
    assert code in {i['code'] for i in issues}
    assert report is None


def test_measured_quote_in_hypothesis_still_requires_review(quotes):
    issues,report=check(quotes,'此前24小时匿名匹配历史交易数为2。',['F3'],'hypotheses')
    assert 'measured_claim_in_hypothesis_requires_review' in {i['code'] for i in issues}
    assert report is None


def test_rationale_capacity_is_consistent_and_only_rationale_changes(quotes):
    packet,bundle=quotes
    value=good();value['rationale']={'text':'当前交易证据仍需审查。'*13,'refs':['F1','F2','F3','F4','F5','F6','F7','F8']}
    assert 120<len(value['rationale']['text'])<=160
    parsed=candidate.parse(value,packet,bundle)
    assert not candidate.issues_for(value,packet,bundle)
    issues,report=candidate.verify(parsed,packet,bundle)
    assert not issues and report['draft']==value
    assert candidate.verify_saved(report,packet,bundle)==report
    with pytest.raises(ValueError):prior.Draft.model_validate_json(canonical(value),strict=True)
    assert packet['view']['output_contract']['bounds']['rationale_refs']==[1,8]
    assert packet['view']['response_schema']['$defs']['Rationale']['properties']['text']['maxLength']==160
    value['basis']=[{'text':'当前交易证据仍需审查。'*13,'refs':['F1']}]
    with pytest.raises(ValueError):candidate.parse(value,packet,bundle)


@pytest.mark.parametrize('bad',['text','refs'])
def test_new_capacity_has_real_upper_bounds(quotes,bad):
    packet,bundle=quotes;value=good()
    value['rationale'][bad]='核查。'*54 if bad=='text' else ['F1']*9
    with pytest.raises(ValueError):candidate.parse(value,packet,bundle)


@pytest.mark.parametrize('impact',[
    '持卡人是否同意该笔支付尚未核验，无法据此判断授权状态；卡片尝试次数是另一问题，不能由该缺口推断。',
    '持卡人是否同意该笔支付尚未核验；卡片尝试次数是另一问题，不能由此缺口推断。',
    '授权状态不能解释尝试次数，二者是不同问题。',
])
def test_denial_and_catalog_explanation_are_not_attempt_claims(quotes,impact):
    packet,bundle=quotes;value=good();value['main_limit']['impact']=impact
    assert 'authorization_gap_cannot_explain_attempt_frequency' not in {i['code'] for i in candidate.issues_for(value,packet,bundle)}


@pytest.mark.parametrize('impact',[
    '由于授权尚未核验，无法判断卡片尝试次数。',
    '授权缺失导致卡片尝试次数未知。',
    '授权与卡片速度无关；但授权未核验导致无法判断尝试次数。',
])
def test_affirmative_misuse_remains_blocked_in_each_clause(quotes,impact):
    packet,bundle=quotes;value=good();value['main_limit']['impact']=impact
    assert 'authorization_gap_cannot_explain_attempt_frequency' in {i['code'] for i in candidate.issues_for(value,packet,bundle)}


@pytest.mark.parametrize('changed',['view','support','identity','saved'])
def test_quote_packet_and_rebuilt_report_bindings_remain_strict(quotes,changed):
    packet,bundle=deepcopy(quotes);value=good()
    if changed=='saved':
        _,report=candidate.verify(candidate.parse(value,packet,bundle),packet,bundle)
        report['draft']['rationale']['text']='新增未核实解释。'
        report.pop('report_sha256');report['report_sha256']=digest(report)
        with pytest.raises(ActionFailure):candidate.verify_saved(report,packet,bundle)
        return
    if changed=='view':packet['view']['writing_plan']='changed'
    if changed=='support':packet['fact_support_contract_sha256']='f'*64
    if changed=='identity':packet['scope_private']['case_alias']='f'*32
    with pytest.raises(ActionFailure):candidate.issues_for(value,packet,bundle)


def test_offline_cli_prepares_selected_generation_without_transport(offline_source_run,tmp_path):
    offline,source_run,before=offline_source_run
    result=offline.prepare(source_run,tmp_path/'quote-payloads',generation='supported-quotes-v2')
    assert result['candidate_generation']==candidate.GENERATION and len(result['cases'])==6
    assert result['new_first_drafts_generated']==result['paid_calls']==result['external_transfers']==0
    assert all(file_hash(p)==sha for p,sha in before.items())


def test_unknown_generation_fails_before_creating_output(offline_source_run,tmp_path):
    offline,source_run,_=offline_source_run;out=tmp_path/'bad-generation'
    with pytest.raises(ValueError,match='unknown offline candidate'):
        offline.prepare(source_run,out,generation='other')
    assert not out.exists()
