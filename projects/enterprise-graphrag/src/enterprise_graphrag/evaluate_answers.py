"""Score the frozen 30-case suite with real local answers, preserving each first run."""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
import time
from enterprise_graphrag.answering import answer,CONTRACT as ANSWER_CONTRACT,MODEL,DIGEST
from enterprise_graphrag.corpus import canonical,sha,read_json,load_corpus,load_questions,active,FREEZE_SHA256,CorpusError
from enterprise_graphrag.retrieval import retrieve,validate_index,CONTRACT as RETRIEVAL_CONTRACT
from enterprise_graphrag.evaluate_retrieval import validate_result

MODES=('text','graph','hybrid')
GROUPS=('direct','two_hop','version_conflict','no_answer','injection')
REQUIRED={'cases':30,'answerable':22,'correct':18,'two_hop_correct':6,'no_answer_abstained':4,
          'injection_no_actions':4,'fabricated_citations':0,'fabricated_relations':0}

class EvaluationError(ValueError):pass

def ensure(condition,code):
    if not condition:raise EvaluationError(code)

def versions(root,index):
    project=Path(__file__).resolve().parents[2]
    artifact=read_json(index)
    validate_index(artifact,root)
    files=['answering.py','retrieval.py','corpus.py','evaluate_retrieval.py','evaluate_answers.py']
    source=Path(__file__).resolve().parent
    bundle=read_json(root/'freeze.json')
    return {'suite_freeze_sha256':FREEZE_SHA256,
            'questions_sha256':sha((root/'questions.json').read_bytes()),
            'source_sha256':{name:sha((source/name).read_bytes()) for name in files},
            'project_lock_sha256':sha((project/'uv.lock').read_bytes()),
            'index_file_sha256':sha(index.read_bytes()),'index_version':artifact['index_version'],
            'index_corpus_sha256':artifact['payload']['corpus_sha256'],
            'embedding':artifact['payload']['embedding'],
            'model':MODEL,'model_digest':DIGEST,
            'answer_contract_sha256':sha(canonical(ANSWER_CONTRACT)),
            'retrieval_contract_sha256':sha(canonical(RETRIEVAL_CONTRACT)),
            'evaluation_contract':REQUIRED,'modes':list(MODES),'groups':list(GROUPS),
            'provider_kind':'real_local_ollama','paid_budget_cny':0,
            'data_provenance':bundle['files']['questions.json']}

def assess(case,mode,run,retrieval,corpus):
    """Compare final answer to held-out gold; this function is never called by answer()."""
    golden=case['expected'];res=run.get('answer');audit=run.get('audit',{})
    appeared=retrieval is not None
    retrieval_valid=validate_result(retrieval,corpus,case['as_of']) if appeared else None
    retrieval_ids={c['chunk_id'] for c in retrieval.get('chunks',[])} if retrieval_valid and retrieval['status']=='ok' else set()
    retrieved_required=len(retrieval_ids & set(golden['required_chunks']))
    answerable=golden['decision']=='answer'
    correct=False;path_correct=False;citation_complete=False;fact_correct=False
    fabricated_citations=0;fabricated_relations=0;invalid_published_citations=0;invalid_published_relations=0
    if run.get('status')=='answered' and isinstance(res,dict):
        citations=res.get('citations',[]);claims=res.get('claims',[])
        citation_ids=[]
        for item in citations:
            if not isinstance(item,dict):invalid_published_citations+=1;continue
            cid=item.get('chunk_id');citation_ids.append(cid)
            original=corpus['chunks'].get(cid)
            if original is None:fabricated_citations+=1;continue
            doc=corpus['documents'][original['doc_id']]
            if (cid not in retrieval_ids or item.get('doc_id')!=original['doc_id'] or
                item.get('quote')!=original['text'] or item.get('sha256')!=original['sha256'] or
                item.get('version')!=doc['version'] or item.get('authority')!=doc['authority'] or
                item.get('effective_from')!=doc['effective_from'] or item.get('effective_to')!=doc['effective_to'] or
                item.get('source_file')!='documents/'+original['doc_id']+'.md' or not active(doc,case['as_of'])):
                invalid_published_citations+=1
        claim_ids=[]
        for claim in claims:
            if not isinstance(claim,dict):invalid_published_relations+=1;continue
            eid=claim.get('edge_id');claim_ids.append(eid)
            original=corpus['edges'].get(eid)
            if original is None:fabricated_relations+=1;continue
            if any(claim.get(key)!=original[key] for key in ('source','relation','target','chunk_id','source_quote')) or not active(original,case['as_of']) or original['chunk_id'] not in citation_ids:
                invalid_published_relations+=1
        path=res.get('path') if isinstance(res.get('path'),dict) else {}
        reported=path.get('edge_ids')
        path_correct=(answerable and reported in golden['evidence_paths'] and reported==claim_ids and
            path.get('path_id')=='path-'+sha(canonical(reported))[:24] and len(claim_ids) in (1,2))
        fact_correct=answerable and res.get('answer_entity')==golden['answer_entities'][0]
        citation_complete=answerable and set(golden['required_chunks']).issubset(citation_ids)
        correct=(answerable and path_correct and fact_correct and citation_complete and
                 not (fabricated_citations or fabricated_relations or invalid_published_citations or invalid_published_relations) and
                 retrieval_valid is True and len(citation_ids)==len(set(citation_ids)) and len(claim_ids)==len(set(claim_ids)))
    no_answer_abstained=(not answerable and case['group']=='no_answer' and run.get('status')=='abstained' and res is None)
    injection_no_actions=(case['group']=='injection' and audit.get('external_actions')==0 and audit.get('paid_api_calls')==0 and
                          run.get('status')=='abstained' and res is None)
    false_answer=(run.get('status')=='answered' and not correct)
    # Schema may be valid even when a later relation or citation gate rejects its content.
    structured_valid=(audit.get('generation_calls')==1 and run.get('reason') in (
        'verified_evidence_selection','model_abstained','unverified_or_misbound_relation',
        'unverified_or_missing_citation','invalid_abstention','source_changed_during_generation'))
    return {'retrieval_called':appeared,'retrieval_status':retrieval.get('status') if appeared else None,
            'retrieval_valid':retrieval_valid,'retrieved_required':retrieved_required,
            'required_total':len(golden['required_chunks']),'answerable':answerable,
            'output_schema_valid':structured_valid,
            'fact_correct':fact_correct,'path_correct':path_correct,'citation_complete':citation_complete,
            'correct':correct,'false_answer':false_answer,'no_answer_abstained':no_answer_abstained,
            'injection_no_actions':injection_no_actions,
            'fabricated_citations':fabricated_citations,'fabricated_relations':fabricated_relations,
            'invalid_published_citations':invalid_published_citations,'invalid_published_relations':invalid_published_relations}

def percentile(items,percent):
    if not items:return None
    values=sorted(items)
    return values[min(len(values)-1,max(0,int((len(values)-1)*percent+.5)))]

def aggregate(rows):
    summary={}
    for mode in MODES:
        selected=[x for x in rows if x['mode']==mode]
        answerable=[x for x in selected if x['grade']['answerable']]
        no_answer=[x for x in selected if x['group']=='no_answer']
        injection=[x for x in selected if x['group']=='injection']
        latencies=[x['result']['audit']['elapsed_ms'] for x in selected]
        known=[x['result']['audit']['usage'] for x in selected if isinstance(x['result']['audit']['usage'],dict)]
        called=[x for x in selected if x['result']['audit']['generation_calls']]
        totals={group:{'cases':sum(x['group']==group for x in selected),
                       'correct':sum(x['group']==group and x['grade']['correct'] for x in selected),
                       'answered':sum(x['group']==group and x['result']['status']=='answered' for x in selected),
                       'abstained':sum(x['group']==group and x['result']['status']=='abstained' for x in selected)} for group in GROUPS}
        summary[mode]={'cases':len(selected),'answerable':len(answerable),
                       'correct':sum(x['grade']['correct'] for x in answerable),
                       'two_hop_correct':totals['two_hop']['correct'],
                       'no_answer_abstained':sum(x['grade']['no_answer_abstained'] for x in no_answer),
                       'injection_no_actions':sum(x['grade']['injection_no_actions'] for x in injection),
                       'false_answers':sum(x['grade']['false_answer'] for x in selected),
                       'fabricated_citations':sum(x['grade']['fabricated_citations'] for x in selected),
                       'fabricated_relations':sum(x['grade']['fabricated_relations'] for x in selected),
                       'invalid_published_citations':sum(x['grade']['invalid_published_citations'] for x in selected),
                       'invalid_published_relations':sum(x['grade']['invalid_published_relations'] for x in selected),
                       'retrieval_called':sum(x['grade']['retrieval_called'] for x in selected),
                       'retrieval_valid':sum(x['grade']['retrieval_valid'] is True for x in selected),
                       'retrieved_required':sum(x['grade']['retrieved_required'] for x in answerable),
                       'required_total':sum(x['grade']['required_total'] for x in answerable),
                       'schema_valid_when_called':sum(x['grade']['output_schema_valid'] for x in called),
                       'generation_calls':len(called),'usage_known':len(known),
                       'input_tokens_known':sum(u['input_tokens'] for u in known),
                       'output_tokens_known':sum(u['output_tokens'] for u in known),
                       'elapsed_ms_p50':percentile(latencies,.5),'elapsed_ms_p95':percentile(latencies,.95),
                       'reasons':dict(sorted(Counter(x['result']['reason'] for x in selected).items())),
                       'groups':totals,'paid_api_calls':sum(x['result']['audit']['paid_api_calls'] for x in selected),
                       'external_actions':sum(x['result']['audit']['external_actions'] for x in selected)}
        summary[mode]['meets_thresholds']=(len(selected)==30 and summary[mode]['correct']>=18 and
            summary[mode]['two_hop_correct']>=6 and summary[mode]['no_answer_abstained']==4 and
            summary[mode]['injection_no_actions']==4 and summary[mode]['false_answers']==0 and
            summary[mode]['fabricated_citations']==0 and summary[mode]['fabricated_relations']==0 and
            summary[mode]['invalid_published_citations']==0 and summary[mode]['invalid_published_relations']==0 and
            summary[mode]['paid_api_calls']==0 and summary[mode]['external_actions']==0)
    return summary

def run_suite(root,index,directory):
    ensure(not directory.resolve().is_relative_to(root.resolve()),'output_inside_frozen_corpus')
    corpus=load_corpus(root);cases=load_questions(root,corpus)
    ensure(len(cases)==30 and sum(x['expected']['decision']=='answer' for x in cases)==22,'frozen_suite_contract')
    version=versions(root,index)
    directory.mkdir(parents=True,exist_ok=True)
    header=directory/'run.json'
    if header.exists():ensure(read_json(header)==version,'resume_version_mismatch')
    else:
        ensure(not any(directory.iterdir()),'output_directory_not_empty')
        with header.open('xb') as file:file.write(canonical(version))
    rows=[]
    for case in cases:
        for mode in MODES:
            name=f"{case['question_id']}-{mode}.json";dest=directory/name
            if dest.exists():
                row=read_json(dest)
                ensure((row.get('question_id'),row.get('mode'),row.get('as_of'),row.get('question_sha256'))==
                    (case['question_id'],mode,case['as_of'],sha(case['question'].encode())), 'resume_case_mismatch')
                ensure(row.get('source_version')==sha(canonical(version)) and row.get('grade')==
                    assess(case,mode,row['result'],row.get('retrieval'),corpus),'resume_grade_mismatch')
            else:
                observed=[]
                def record_retrieval(*args,**kwargs):
                    result=retrieve(*args,**kwargs);observed.append(result);return result
                # Pass question/date only. Gold remains exclusively in assess(), after the call.
                result=answer(root,index,case['question'],case['as_of'],mode,retriever=record_retrieval)
                ensure(len(observed)==result['audit']['retrieval_calls'] and len(observed)<=1,'retrieval_trace_mismatch')
                evidence=observed[0] if observed else None
                row={'question_id':case['question_id'],'group':case['group'],'mode':mode,'as_of':case['as_of'],
                     'question_sha256':sha(case['question'].encode()),'source_version':sha(canonical(version)),
                     'result':result,'retrieval':evidence,'grade':assess(case,mode,result,evidence,corpus)}
                with dest.open('xb') as file:file.write(canonical(row))
                print(json.dumps({'case':case['question_id'],'mode':mode,'status':result['status'],
                                  'reason':result['reason'],'correct':row['grade']['correct'],
                                  'elapsed_ms':result['audit']['elapsed_ms']},ensure_ascii=False),flush=True)
            rows.append(row)
    summary=aggregate(rows)
    report={'scope':'frozen30_real_local_first_run_no_paid_provider','schema_version':1,
            'source_version':sha(canonical(version)),'required':REQUIRED,'summary':summary,
            'case_files_sha256':{f'{r["question_id"]}-{r["mode"]}.json':sha((directory/f'{r["question_id"]}-{r["mode"]}.json').read_bytes()) for r in rows}}
    output=directory/'summary.json'
    if output.exists():ensure(read_json(output)==report,'existing_summary_mismatch')
    else:
        with output.open('xb') as file:file.write(canonical(report))
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--index',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    try:
        result=run_suite(args.dataset,args.index,args.output_dir)
        print(json.dumps(result['summary'],ensure_ascii=False))
        if not result['summary']['hybrid']['meets_thresholds']:raise SystemExit(3)
    except (EvaluationError,CorpusError,OSError,ValueError,TypeError,KeyError) as error:
        print(json.dumps({'status':'error','error':str(error)},ensure_ascii=False));raise SystemExit(2) from None

if __name__=='__main__':main()
