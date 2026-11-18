import json
from copy import deepcopy
import httpx
import pytest
from test_autonomous_tool_contracts import scope,index,registry
from test_autonomous_action_providers import profile,cloud_body,ledger
from test_judgment_v3 import evidence,v3,good
from graphrisk.agent.judgment_v3 import JudgmentProvider,packet_for
from graphrisk.agent.report_pilot import reviewed_profile,RATES,MODEL,REVISION,PRICE_URL,REVISION_URL
from graphrisk.agent.upgrade_contract import canonical,digest


def config(profile):
    return {**deepcopy(profile),'report_generation_profile':'quality-v3-thinking','report_input_presentation':'plain-facts-v1',
            'max_prompt_utf8_bytes':16000,'max_output_tokens':4096,'report_thinking':True,'reasoning_effort':'high'}


@pytest.mark.parametrize('effort,limit',[('high',4096),('low',4096),('low',8192)])
def test_thinking_is_explicitly_frozen_and_discards_reasoning_text(tmp_path,evidence,scope,registry,profile,effort,limit):
    executor,_,bundle=evidence
    cfg=config(profile)
    if effort=='low':cfg.update(report_generation_profile='quality-v3-thinking-low',reasoning_effort='low')
    cfg['max_output_tokens']=limit
    packet=packet_for(scope,executor.observations,registry,cfg);packet['logical_key']='0'
    campaign={'profile_sha256':digest(cfg),'packets':[packet]}
    budget=ledger(tmp_path,campaign)
    posts=[]
    def handler(req):
        posts.append(json.loads(req.content))
        body=cloud_body(good())
        body['choices'][0]['message']['reasoning_content']='PRIVATE-REASONING-MUST-NOT-PERSIST'
        body['usage']['completion_tokens_details']={'reasoning_tokens':12}
        return httpx.Response(200,json=body)
    client=JudgmentProvider('deepseek',profile=cfg,budget=budget,campaign=campaign,campaign_sha=digest(campaign),
                           phase='pilot',output=tmp_path/'receipts',runtime=tmp_path,transport=httpx.MockTransport(handler))
    result=client.generate('0')
    assert result['code']=='ok' and not result['integrity_stop']
    assert result['reasoning_observed'] is True and result['reasoning_tokens']==12
    assert posts[0]['thinking']=={'type':'enabled'} and posts[0]['reasoning_effort']==effort
    assert 'temperature' not in posts[0] and posts[0]['max_tokens']==limit
    assert 'PRIVATE-REASONING' not in ''.join(p.read_text(encoding='utf-8') for p in (tmp_path/'receipts').glob('*.json'))


def test_profile_extension_is_report_only_and_retains_price_endpoint_and_caps(profile):
    cfg=config(profile)
    pricing={'verified_date':'2026-10-24','pricing_source':PRICE_URL,'revision_source':REVISION_URL,
             'model':MODEL,'documented_revision':REVISION,'peak_cny_per_million':RATES}
    result=reviewed_profile(cfg,pricing,'2026-10-24')
    assert result['max_output_tokens']==4096 and result['max_prompt_utf8_bytes']==16000
    assert profile['max_output_tokens']==768 and profile['max_prompt_utf8_bytes']==7000
