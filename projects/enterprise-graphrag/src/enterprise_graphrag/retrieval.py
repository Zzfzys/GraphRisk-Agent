"""Deterministic, date-gated retrieval over the frozen simulated IT corpus."""
from __future__ import annotations
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
from enterprise_graphrag.corpus import (load_corpus, canonical, sha, read_json, active, iso,
                                       CorpusError, FREEZE_SHA256, ANSWER_RELATIONS)

MODEL='qwen3-embedding:0.6b'
DIGEST='ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d'
DIMENSIONS=1024
CONTRACT={'version':'retrieval-v1','top_k':6,'max_hops':2,'max_seeds':3,'max_paths':3,
          'max_expansions':128,'max_candidates':64,'bm25_k1':1.2,'bm25_b':0.75,
          'rrf_k':60,'dense_min_similarity':0.45,'vector_round_decimals':8,
          'query_timeout_seconds':60,'entity_linking':'exact_name_or_id','gold_in_index':False}

class RetrievalError(ValueError):pass

def check(ok,code):
    if not ok:raise RetrievalError(code)

def normalize(vector):
    check(isinstance(vector,list) and len(vector)==DIMENSIONS,'embedding_dimension_mismatch')
    check(all(type(v) in (float,int) and math.isfinite(v) for v in vector),'embedding_nonfinite')
    norm=math.sqrt(sum(v*v for v in vector))
    check(math.isfinite(norm) and norm>0,'embedding_zero_or_invalid_norm')
    return [round(v/norm,CONTRACT['vector_round_decimals']) for v in vector]

def terms(text):
    result=[]
    for word in re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]+',text.lower()):
        if re.fullmatch(r'[\u4e00-\u9fff]+',word):
            result.extend(word[i:i+2] for i in range(len(word)-1))
            if len(word)==1:result.append(word)
        else:result.append(word)
    return result

class OllamaEmbedder:
    model=MODEL;digest=DIGEST;dimensions=DIMENSIONS
    def __init__(self):
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    def _request(self,path,payload=None,deadline=None):
        remaining=(deadline-time.monotonic()) if deadline is not None else 60
        check(remaining>0,'embedding_deadline_exceeded')
        req=urllib.request.Request('http://127.0.0.1:11434'+path,
            data=canonical(payload) if payload is not None else None,
            headers={'Content-Type':'application/json'})
        try:
            with self.opener.open(req,timeout=min(remaining,60)) as response:
                raw=response.read(8_000_001)
            check(len(raw)<=8_000_000,'embedding_response_too_large')
            check(deadline is None or time.monotonic()<=deadline,'embedding_deadline_exceeded')
            return json.loads(raw)
        except (urllib.error.URLError,OSError,ValueError) as error:
            if isinstance(error,RetrievalError):raise
            raise RetrievalError('embedding_unavailable_or_invalid_response') from None
    def verify(self,deadline):
        body=self._request('/api/tags',deadline=deadline)
        check(isinstance(body,dict) and isinstance(body.get('models'),list),'invalid_embedding_inventory')
        check(all(isinstance(m,dict) for m in body['models']),'invalid_embedding_inventory')
        check(any(m.get('name')==MODEL and m.get('digest')==DIGEST for m in body['models']),'embedding_digest_mismatch')
    def embed(self,texts,deadline=None):
        check(isinstance(texts,list) and 0<len(texts)<=8 and all(isinstance(t,str) and 0<len(t)<=4000 for t in texts),'invalid_embedding_input')
        deadline=deadline or time.monotonic()+60
        self.verify(deadline)
        body=self._request('/api/embed',{'model':MODEL,'input':texts,'truncate':False,'keep_alive':'5m','options':{'num_ctx':4096}},deadline)
        check(isinstance(body,dict),'invalid_embedding_response')
        check(body.get('model')==MODEL,'embedding_model_mismatch')
        vectors=body.get('embeddings')
        check(isinstance(vectors,list) and len(vectors)==len(texts),'embedding_count_mismatch')
        result=[normalize(v) for v in vectors]
        self.verify(deadline)
        return result

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise RetrievalError('embedding_redirect_rejected')

def build_index(root:Path,embedder):
    corpus=load_corpus(root)
    check((embedder.model,embedder.digest,embedder.dimensions)==(MODEL,DIGEST,DIMENSIONS),'embedding_config_mismatch')
    ids=sorted(corpus['chunks'])
    texts=[corpus['documents'][corpus['chunks'][cid]['doc_id']]['title']+'\n'+corpus['chunks'][cid]['text'] for cid in ids]
    vectors=[]
    for start in range(0,len(ids),8):
        batch=texts[start:start+8];response=embedder.embed(batch)
        check(isinstance(response,list) and len(response)==len(batch),'embedding_count_mismatch')
        vectors.extend(response)
    check(len(vectors)==len(ids),'embedding_count_mismatch')
    vectors=[normalize(v) for v in vectors]
    payload={'schema_version':1,'corpus_sha256':FREEZE_SHA256,'contract':CONTRACT,
             'embedding':{'model':MODEL,'digest':DIGEST,'dimensions':DIMENSIONS,'normalized':True},
             'corpus':corpus,'chunk_ids':ids,'lexical':[dict(sorted(Counter(terms(t)).items())) for t in texts],
             'vectors':vectors}
    artifact={'index_version':sha(canonical(payload)),'payload':payload}
    validate_index(artifact,root)
    return artifact

def validate_index(artifact,root):
    check(isinstance(artifact,dict) and set(artifact)=={'index_version','payload'},'invalid_index_schema')
    p=artifact['payload']
    check(artifact['index_version']==sha(canonical(p)),'index_checksum_mismatch')
    check(isinstance(p,dict) and set(p)=={'schema_version','corpus_sha256','contract','embedding','corpus','chunk_ids','lexical','vectors'},'invalid_index_payload')
    check(p['schema_version']==1 and p['corpus_sha256']==FREEZE_SHA256 and p['contract']==CONTRACT,'index_contract_mismatch')
    check(p['embedding']=={'model':MODEL,'digest':DIGEST,'dimensions':DIMENSIONS,'normalized':True},'index_embedding_mismatch')
    corpus=load_corpus(root)
    check(p['corpus']==corpus,'index_source_mismatch')
    ids=sorted(corpus['chunks'])
    check(p['chunk_ids']==ids,'index_chunk_inventory_mismatch')
    texts=[corpus['documents'][corpus['chunks'][cid]['doc_id']]['title']+'\n'+corpus['chunks'][cid]['text'] for cid in ids]
    check(p['lexical']==[dict(sorted(Counter(terms(t)).items())) for t in texts],'index_lexical_mismatch')
    check(isinstance(p['vectors'],list) and len(p['vectors'])==len(ids),'index_vector_count_mismatch')
    for vector in p['vectors']:
        normalize(vector)
        check(abs(sum(v*v for v in vector)-1)<1e-5,'index_vector_not_normalized')
    return p

def write_index(artifact,output,root):
    check(not output.exists(),'output_exists_use_new_path')
    check(not output.resolve().is_relative_to(root.resolve()),'output_inside_frozen_corpus')
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('xb') as f:f.write(canonical(artifact))

def bm25(query,ids,counts):
    q=Counter(terms(query));n=len(ids)
    if not n:return []
    lengths={cid:sum(counts[cid].values()) for cid in ids};avg=sum(lengths.values())/n
    result=[]
    for cid in ids:
        score=0.0
        for token in q:
            freq=counts[cid].get(token,0)
            if not freq:continue
            df=sum(token in counts[x] for x in ids)
            idf=math.log(1+(n-df+0.5)/(df+0.5))
            score+=idf*freq*2.2/(freq+1.2*(0.25+0.75*lengths[cid]/(avg or 1)))
        if score>0:result.append((cid,score))
    return sorted(result,key=lambda v:(-v[1],v[0]))[:CONTRACT['max_candidates']]

def intent(query):
    if '直接依赖' in query:return ['DEPENDS_ON','OWNED_BY'] if '团队' in query else ['DEPENDS_ON']
    if '手册' in query and ('维护' in query or '团队' in query):return ['HAS_RUNBOOK','MAINTAINED_BY']
    if '团队' in query or '归' in query:return ['OWNED_BY']
    return None

def graph_paths(query,day,corpus):
    mentions=[n for n in corpus['nodes'].values() if n['name'] in query or n['node_id'].lower() in query.lower()]
    mentions=sorted(mentions,key=lambda n:(n['type']=='team',-len(n['name']),n['node_id']))[:CONTRACT['max_seeds']]
    allowed=intent(query)
    edges=[e for e in corpus['edges'].values() if e['relation'] in ANSWER_RELATIONS and active(e,day)
           and active(corpus['documents'][e['doc_id']],day)]
    edges.sort(key=lambda e:e['edge_id'])
    paths=[];expanded=0
    for seed in mentions:
        queue=[(seed['node_id'],[],{seed['node_id']})]
        while queue:
            node,path,seen=queue.pop(0)
            if len(path)>=CONTRACT['max_hops']:continue
            for edge in edges:
                if edge['source']!=node or edge['target'] in seen:continue
                expanded+=1
                check(expanded<=CONTRACT['max_expansions'],'graph_expansion_limit')
                if allowed is not None and (len(path)>=len(allowed) or edge['relation']!=allowed[len(path)]):continue
                next_path=path+[edge['edge_id']]
                if allowed is None or len(next_path)==len(allowed):
                    paths.append({'path_id':'path-'+sha(canonical(next_path))[:24],'edge_ids':next_path,
                                  'chunk_ids':list(dict.fromkeys(corpus['edges'][eid]['chunk_id'] for eid in next_path)),
                                  'start':seed['node_id'],'end':edge['target']})
                queue.append((edge['target'],next_path,seen|{edge['target']}))
    paths=sorted({p['path_id']:p for p in paths}.values(),key=lambda p:(len(p['edge_ids']),p['path_id']))
    return paths[:CONTRACT['max_paths']],{'seed_ids':[n['node_id'] for n in mentions],'expansions':expanded,'intent_relations':allowed}

def retrieve(root:Path,index:Path,query,day,mode,*,embedder=None):
    started=time.monotonic()
    result={'status':'error','error':None,'mode':mode,'as_of':day if isinstance(day,str) else None,
            'chunks':[],'paths':[],'index_version':None,'corpus_sha256':FREEZE_SHA256}
    try:
        check(mode in ('text','graph','hybrid'),'invalid_retrieval_mode')
        check(isinstance(query,str) and 0<len(query.strip())<=1000,'invalid_query')
        iso(day)
        check(index.stat().st_size<=12_000_000,'index_too_large')
        artifact=read_json(index);p=validate_index(artifact,root);corpus=p['corpus']
        result['index_version']=artifact['index_version']
        ids=[cid for cid in p['chunk_ids'] if active(corpus['documents'][corpus['chunks'][cid]['doc_id']],day)]
        if not ids:
            result.update(status='no_match',error=None);return result
        counts=dict(zip(p['chunk_ids'],p['lexical']))
        lexical=bm25(query,ids,counts) if mode!='graph' else []
        paths,graph_meta=graph_paths(query,day,corpus) if mode!='text' else ([],{})
        result['graph']=graph_meta
        dense=[]
        if mode=='hybrid':
            check(embedder is not None,'embedding_provider_required')
            check((embedder.model,embedder.digest,embedder.dimensions)==(MODEL,DIGEST,DIMENSIONS),'embedding_config_mismatch')
            vectors=embedder.embed([query],deadline=started+CONTRACT['query_timeout_seconds'])
            check(len(vectors)==1,'embedding_count_mismatch')
            vector=normalize(vectors[0]);lookup=dict(zip(p['chunk_ids'],p['vectors']))
            dense=sorted([(cid,sum(a*b for a,b in zip(vector,lookup[cid]))) for cid in ids],key=lambda x:(-x[1],x[0]))
            dense=[x for x in dense if x[1]>=CONTRACT['dense_min_similarity']][:CONTRACT['max_candidates']]
        graph_ids=list(dict.fromkeys(cid for path in paths for cid in path['chunk_ids']))
        scores={}
        lists=[[(cid,1) for cid in graph_ids]] if mode=='graph' else [lexical] if mode=='text' else [lexical,dense,[(cid,1) for cid in graph_ids]]
        for ranking in lists:
            for rank,(cid,_) in enumerate(ranking,1):scores[cid]=scores.get(cid,0)+1/(CONTRACT['rrf_k']+rank)
        selected=[];included_paths=[]
        # Keep complete graph paths together; never expose a path with a missing evidence chunk.
        for path in paths:
            additions=[cid for cid in path['chunk_ids'] if cid not in selected]
            if len(selected)+len(additions)<=CONTRACT['top_k']:
                selected+=additions;included_paths.append(path)
        for cid in sorted(scores,key=lambda cid:(-scores[cid],cid)):
            if len(selected)>=CONTRACT['top_k']:break
            if cid not in selected:selected.append(cid)
        for cid in selected:
            chunk=corpus['chunks'][cid];doc=corpus['documents'][chunk['doc_id']]
            result['chunks'].append({**chunk,'title':doc['title'],'version':doc['version'],'authority':doc['authority'],
                'effective_from':doc['effective_from'],'effective_to':doc['effective_to'],'source_file':'documents/'+doc['doc_id']+'.md',
                'score':round(scores.get(cid,0),10),'score_type':'rrf_rank_not_probability'})
        result['paths']=[{**path,'edges':[corpus['edges'][eid] for eid in path['edge_ids']]} for path in included_paths]
        result['candidate_counts']={'date_valid':len(ids),'text':len(lexical),'dense':len(dense),'paths':len(paths)}
        check(time.monotonic()-started<=CONTRACT['query_timeout_seconds'],'retrieval_deadline_exceeded')
        result.update(status='ok' if selected else 'no_match',error=None)
    except (RetrievalError,CorpusError) as error:
        result.update(status='error',error=str(error),chunks=[],paths=[])
    except OSError:
        result.update(status='error',error='index_or_corpus_unavailable',chunks=[],paths=[])
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError):
        result.update(status='error',error='invalid_index_or_provider_response',chunks=[],paths=[])
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['build','query'])
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--index',type=Path,required=True)
    parser.add_argument('--query');parser.add_argument('--as-of',default='2026-09-30')
    parser.add_argument('--mode',choices=['text','graph','hybrid'],default='hybrid')
    args=parser.parse_args()
    try:
        if args.action=='build':
            artifact=build_index(args.dataset,OllamaEmbedder());write_index(artifact,args.index,args.dataset)
            print(json.dumps({'status':'built','index_version':artifact['index_version'],'chunks':len(artifact['payload']['chunk_ids'])}))
        else:
            result=retrieve(args.dataset,args.index,args.query,args.as_of,args.mode,
                            embedder=OllamaEmbedder() if args.mode=='hybrid' else None)
            print(json.dumps(result,ensure_ascii=False))
            if result['status']=='error':raise SystemExit(2)
    except (RetrievalError,CorpusError,OSError) as error:
        print(json.dumps({'status':'error','error':str(error)},ensure_ascii=False));raise SystemExit(2) from None

if __name__=='__main__':main()
