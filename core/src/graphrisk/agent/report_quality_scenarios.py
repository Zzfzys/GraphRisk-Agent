"""Public, label-free authored boundary evidence; never real fraud metrics.

The six cases are frozen together before validation. They are inputs to real
model calls, not model stubs or hand-written model answers.
"""
from datetime import date
from graphrisk.agent.autonomous_tools import InvestigationScope,ReadOnlyToolExecutor,SPEC_BY_NAME,ToolBoundaryError
from graphrisk.agent.policy_retrieval import PolicyEvidence

SCENARIOS=(
    {'id':'S1','name':'sparse_history','counts':[0,0,1],'amount':23.5,'mean':None,'std':None,'deviation':None,'primary':0.03,'graph':0.09,'entities':['email_domain'],'total':700,'graph_unavailable':False},
    {'id':'S2','name':'busy_history_without_threshold','counts':[9,19,28],'amount':82.0,'mean':79.0,'std':11.0,'deviation':3.0,'primary':0.23,'graph':0.61,'entities':['device','card'],'total':40,'graph_unavailable':False},
    {'id':'S3','name':'missing_history','history_unavailable':True,'counts':[0,0,0],'amount':49.0,'mean':None,'std':None,'deviation':None,'primary':0.58,'graph':0.21,'entities':['address'],'total':3,'graph_unavailable':False},
    {'id':'S4','name':'graph_unavailable','counts':[1,2,4],'amount':140.0,'mean':60.0,'std':5.0,'deviation':80.0,'primary':0.84,'graph':None,'entities':['card'],'total':3,'graph_unavailable':True},
    {'id':'S5','name':'unmeasured_policy_exception','counts':[1,3,6],'amount':27.0,'mean':27.0,'std':0.0,'deviation':0.0,'primary':0.12,'graph':0.39,'entities':['device'],'total':3,'graph_unavailable':False},
    {'id':'S6','name':'partial_relations','counts':[2,5,12],'amount':75.0,'mean':40.0,'std':10.0,'deviation':35.0,'primary':0.77,'graph':0.34,'entities':['email_domain','address'],'total':900,'graph_unavailable':False},
)


# Prospective second cohort: original six and their Fail records stay intact.
# Different values and combinations; no labels or expected model answers.
SCENARIOS_V2=(
 {'id':'S7','name':'below_history_with_conflicting_scores','counts':[2,4,11],'amount':34.0,'mean':80.0,'std':9.0,'deviation':-46.0,'primary':0.68,'graph':0.15,'entities':['card','email_domain'],'total':160,'graph_unavailable':False},
 {'id':'S8','name':'flat_baseline_shared_device','counts':[4,8,20],'amount':45.0,'mean':45.0,'std':0.0,'deviation':0.0,'primary':0.08,'graph':0.14,'entities':['device'],'total':8,'graph_unavailable':False},
 {'id':'S9','name':'graph_missing_strong_amount_shift','counts':[2,6,18],'amount':210.0,'mean':70.0,'std':8.0,'deviation':140.0,'primary':0.79,'graph':None,'entities':['email_domain','address'],'total':120,'graph_unavailable':True},
 {'id':'S10','name':'history_unavailable_graph_signal','history_unavailable':True,'counts':[0,0,0],'amount':66.0,'mean':None,'std':None,'deviation':None,'primary':0.4,'graph':0.63,'entities':['card','device'],'total':12,'graph_unavailable':False},
 {'id':'S11','name':'sparse_zero_match_many_relations','counts':[0,0,0],'amount':18.25,'mean':None,'std':None,'deviation':None,'primary':0.04,'graph':0.18,'entities':['address'],'total':520,'graph_unavailable':False},
 {'id':'S12','name':'busy_history_small_relative_shift','counts':[7,21,63],'amount':104.0,'mean':100.0,'std':18.0,'deviation':4.0,'primary':0.28,'graph':0.72,'entities':['card','device','email_domain','address'],'total':92,'graph_unavailable':False},
)


def build_scenario(case,index,registry):
    if case not in SCENARIOS+SCENARIOS_V2:raise ValueError('unknown_public_boundary_scenario')
    scope=InvestigationScope(case_alias=('%032x'%((SCENARIOS+SCENARIOS_V2).index(case)+101)),transaction_id='SYN-'+case['id'],
        dataset_source='synthetic',target_event_second=10000,policy_as_of=date(2026,10,19),transaction_revision='b'*64,
        feature_revision='c'*64,model_revision='d'*64,primary_model_version='xgb-public-fixture-v1',
        graph_model_version='graph-public-fixture-v1',policy_index_version=index.index_version)
    def backend(name):
        def call(args):
            if name=='retrieve_policy':
                chosen=[c for c in index.chunks if c.applies_on(scope.policy_as_of) and c.policy_id in {'device-sharing','trusted-device-exception'}][:args['top_k']]
                return {'status':'ok','index_version':index.index_version,'backend':'tfidf','hits':[{'chunk':PolicyEvidence.model_validate(c.model_dump(exclude={'embedding'})).model_dump(mode='json'),'score':0.7} for c in chosen]}
            target=scope.transaction_id
            base={'tool':name,'status':'ok','transaction_id':target,'dataset_source':'synthetic'}
            def ref(kind,record):return {'source_kind':kind,'record_id':record,'dataset_source':'synthetic'}
            shared=[{'entity_type':e,'entity_id':'PUBLIC-'+e} for e in case['entities']]
            if name=='get_transaction':
                data={'transaction_id':target,'transaction_amount':case['amount'],'product_code':'W','source_transaction_dt_seconds':10000,
                      'card_id':'PUBLIC-CARD','device_id':'PUBLIC-DEVICE','email_domain':'public.example','address_id':'PUBLIC-ADDRESS'}
                evidence=[ref('canonical_transaction',target)]
            elif name=='get_transaction_features':
                if case.get('history_unavailable'):
                    return {**base,'status':'unavailable','error':{'code':'capability_unavailable','message':'This investigation capability is not available yet.','retryable':False}}
                data={'transaction_id':target,**dict(zip(('tx_count_last_1h','tx_count_last_24h','tx_count_last_7d'),case['counts'])),
                      'amount_mean_last_24h':case['mean'],'amount_std_last_24h':case['std'],'amount_deviation_last_24h':case['deviation']}
                evidence=[ref('historical_features',target)]
            elif name=='get_risk_score':
                data={'transaction_id':target,'risk_score':case['primary'],'risk_level':'high' if case['primary']>=0.5 else 'low','model_name':'xgboost','model_version':scope.primary_model_version}
                evidence=[ref('model_artifact',scope.primary_model_version)]
            elif name=='get_related_entities':
                matches=[{'transaction_id':'SYN-N'+str(i),'seconds_before_target':200+i,'shared_entities':shared} for i in range(min(3,args['limit']))]
                data={'transaction_id':target,'matches':matches,'total_matches':case['total'],'truncated':case['total']>len(matches)}
                evidence=[ref('entity_link',n['transaction_id']) for n in matches]
            else:
                if case['graph_unavailable']:
                    return {**base,'status':'unavailable','error':{'code':'capability_unavailable','message':'This investigation capability is not available yet.','retryable':False}}
                neighbors=[{'transaction_id':'SYN-N'+str(i),'seconds_before_target':200+i,'shared_entities':shared,'graph_risk_score':0.2,'evidence_role':'scored'} for i in range(min(2,args['limit']))]
                # Preserve legacy unknown truncation instead of fabricating a new field.
                data={'transaction_id':target,'neighbors':neighbors,'graph_model_version':scope.graph_model_version,'target_graph_risk_score':case['graph'],'score_role':'experimental'}
                evidence=[ref('model_artifact',scope.graph_model_version),*[ref('entity_link',n['transaction_id']) for n in neighbors]]
            return {**base,'data':data,'evidence':evidence}
        return call
    executor=ReadOnlyToolExecutor(scope,{name:backend(name) for name in SPEC_BY_NAME},lambda:None)
    for name in SPEC_BY_NAME:
        try:executor.execute(name,{'focus':'device_sharing','top_k':6} if name=='retrieve_policy' else {})
        except ToolBoundaryError as error:
            if str(error)!='tool_dependency_missing':raise
    return scope,executor.observations
