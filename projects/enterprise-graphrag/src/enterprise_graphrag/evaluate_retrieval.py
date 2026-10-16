"""Frozen-suite retrieval evaluation. Gold is visible here, never to the retriever."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from enterprise_graphrag.corpus import load_corpus,load_questions,canonical,sha,read_json,active,CorpusError,ANSWER_RELATIONS
from enterprise_graphrag.retrieval import retrieve,OllamaEmbedder,CONTRACT,RetrievalError,validate_index

MODES=('text','graph','hybrid')

def validate_result(result,corpus,day):
    """Independently audit provenance, date, path continuity and evidence completeness."""
    chunks=result.get('chunks',[]);paths=result.get('paths',[])
    if result.get('status') not in ('ok','no_match','error'):return False
    if len(chunks)>CONTRACT['top_k'] or len(paths)>CONTRACT['max_paths']:return False
    if result['status']!='ok':return not chunks and not paths
    if not chunks:return False
    ids=[x.get('chunk_id') for x in chunks]
    if len(set(ids))!=len(ids):return False
    for chunk in chunks:
        source=corpus['chunks'].get(chunk.get('chunk_id'))
        if source is None or any(chunk.get(k)!=v for k,v in source.items()):return False
        doc=corpus['documents'][source['doc_id']]
        if not active(doc,day):return False
        if any(chunk.get(k)!=doc[k] for k in ('title','version','authority','effective_from','effective_to')):return False
    for path in paths:
        eids=path.get('edge_ids',[])
        if not 1<=len(eids)<=CONTRACT['max_hops'] or len(set(eids))!=len(eids):return False
        edges=[corpus['edges'].get(eid) for eid in eids]
        if any(e is None for e in edges) or path.get('edges')!=edges:return False
        if path.get('path_id')!='path-'+sha(canonical(eids))[:24]:return False
        cids=list(dict.fromkeys(e['chunk_id'] for e in edges))
        if path.get('chunk_ids')!=cids or not set(cids).issubset(ids):return False
        if path.get('start')!=edges[0]['source'] or path.get('end')!=edges[-1]['target']:return False
        if any(a['target']!=b['source'] for a,b in zip(edges,edges[1:])):return False
        for edge in edges:
            if edge['relation'] not in ANSWER_RELATIONS or not active(edge,day):return False
            if not active(corpus['documents'][edge['doc_id']],day):return False
            if edge['source_quote']!=corpus['chunks'][edge['chunk_id']]['text']:return False
    return True

def score_case(case,result,corpus):
    valid=validate_result(result,corpus,case['as_of'])
    available={x['chunk_id'] for x in result.get('chunks',[])} if valid and result['status']=='ok' else set()
    gold=case['expected'];required=set(gold['required_chunks']);answerable=gold['decision']=='answer'
    paths=[p['edge_ids'] for p in result.get('paths',[])] if valid and result['status']=='ok' else []
    return {'evidence_valid':valid,'answerable':answerable,
            'required_count':len(required),'retrieved_required_count':len(required & available),
            'all_required_chunks':required.issubset(available) if required else None,
            'gold_path_present':any(p in paths for p in gold['evidence_paths']) if answerable else None,
            'attack_chunk_retrieved':case['attack_chunk'] in available if case['attack_chunk'] else None,
            'answer_quality':'not_evaluated','abstention_quality':'not_evaluated'}

def summarize(rows):
    summary={}
    for mode in MODES:
        selected=[row for row in rows if row['mode']==mode]
        groups={}
        for name in ('direct','two_hop','version_conflict','no_answer','injection'):
            group=[row for row in selected if row['group']==name]
            groups[name]={'cases':len(group),'ok':sum(x['result']['status']=='ok' for x in group),
                         'no_match':sum(x['result']['status']=='no_match' for x in group),
                         'errors':sum(x['result']['status']=='error' for x in group),
                         'all_required_chunks':sum(x['score']['all_required_chunks'] is True for x in group),
                         'gold_paths':sum(x['score']['gold_path_present'] is True for x in group)}
        answerable=[x for x in selected if x['score']['answerable']]
        total=sum(x['score']['required_count'] for x in answerable)
        found=sum(x['score']['retrieved_required_count'] for x in answerable)
        latencies=sorted(x['elapsed_ms'] for x in selected)
        summary[mode]={'cases':len(selected),'errors':sum(x['result']['status']=='error' for x in selected),
                       'invalid_evidence':sum(not x['score']['evidence_valid'] for x in selected),
                       'answerable_cases':len(answerable),
                       'complete_evidence_cases':sum(x['score']['all_required_chunks'] is True for x in answerable),
                       'required_chunks_retrieved':found,'required_chunks_total':total,
                       'micro_required_chunk_recall':round(found/total,6) if total else None,
                       'elapsed_ms_p50':latencies[(len(latencies)-1)//2] if latencies else None,
                       'elapsed_ms_p95':latencies[min(len(latencies)-1,int(len(latencies)*.95))] if latencies else None,
                       'groups':groups}
    return summary

def evaluate(root,index,output):
    if output.exists():raise RetrievalError('output_exists_use_new_path')
    if output.resolve().is_relative_to(root.resolve()):raise RetrievalError('output_inside_frozen_corpus')
    artifact=read_json(index);validate_index(artifact,root)
    corpus=load_corpus(root);cases=load_questions(root,corpus)
    rows=[];provider=OllamaEmbedder()
    for case in cases:
        for mode in MODES:
            started=time.monotonic()
            # Deliberately pass only public question and date, never the gold dictionary.
            result=retrieve(root,index,case['question'],case['as_of'],mode,
                            embedder=provider if mode=='hybrid' else None)
            rows.append({'question_id':case['question_id'],'group':case['group'],'question':case['question'],
                         'as_of':case['as_of'],'mode':mode,'elapsed_ms':round((time.monotonic()-started)*1000,3),
                         'result':result,'score':score_case(case,result,corpus)})
    report={'schema_version':1,'scope':'retrieval_only_no_generation_no_model_answer_score',
            'completed_at':datetime.now(timezone.utc).isoformat(),'index_version':artifact['index_version'],
            'index_file_sha256':sha(index.read_bytes()),'corpus_sha256':artifact['payload']['corpus_sha256'],
            'embedding':artifact['payload']['embedding'],'embedding_execution':'real_local_ollama',
            'contract':CONTRACT,'contract_sha256':sha(canonical(CONTRACT)),
            'source_sha256':{name:sha(Path(__file__).with_name(name).read_bytes()) for name in ('corpus.py','retrieval.py','evaluate_retrieval.py')},
            'paid_api_calls':0,'generation_calls':0,'summary':summarize(rows),'rows':rows}
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('xb') as f:f.write(canonical(report))
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True);parser.add_argument('--index',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    try:
        report=evaluate(args.dataset,args.index,args.output)
        print(json.dumps(report['summary'],ensure_ascii=False))
        if any(x['errors'] or x['invalid_evidence'] for x in report['summary'].values()):raise SystemExit(2)
    except (RetrievalError,CorpusError,OSError) as error:
        print(json.dumps({'status':'error','error':str(error)}));raise SystemExit(2) from None

if __name__=='__main__':main()
