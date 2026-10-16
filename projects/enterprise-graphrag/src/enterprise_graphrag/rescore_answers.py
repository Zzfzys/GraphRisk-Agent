"""Rescore immutable first-run answers after tightening the independent scorer.

No retrieval, embedding, generation, network call or new case observation is made.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from enterprise_graphrag.corpus import canonical,sha,read_json,load_corpus,load_questions,CorpusError
from enterprise_graphrag.evaluate_answers import (MODES,EvaluationError,ensure,versions,assess,aggregate)

def rescore(root,index,run_directory,output):
    ensure(not output.exists(),'output_exists_use_new_path')
    ensure(not output.resolve().is_relative_to(root.resolve()),'output_inside_frozen_corpus')
    baseline=read_json(run_directory/'run.json')
    first=read_json(run_directory/'summary.json')
    ensure(first.get('source_version')==sha(canonical(baseline)),'first_run_manifest_mismatch')
    current=versions(root,index)
    for key in baseline:
        if key=='source_sha256':
            for name,digest in baseline[key].items():
                if name!='evaluate_answers.py':ensure(current[key][name]==digest,'runtime_source_changed')
        else:ensure(current[key]==baseline[key],'source_or_index_changed')
    corpus=load_corpus(root);cases=load_questions(root,corpus)
    rows=[]
    for case in cases:
        for mode in MODES:
            name=f"{case['question_id']}-{mode}.json";path=run_directory/name
            ensure(sha(path.read_bytes())==first['case_files_sha256'].get(name),'case_file_changed')
            row=read_json(path)
            ensure((row.get('question_id'),row.get('group'),row.get('mode'),row.get('as_of'),row.get('question_sha256'))==
                (case['question_id'],case['group'],mode,case['as_of'],sha(case['question'].encode())),
                'case_identity_mismatch')
            ensure(row.get('source_version')==first['source_version'],'case_version_mismatch')
            rows.append({'question_id':case['question_id'],'group':case['group'],'mode':mode,
                         'as_of':case['as_of'],'result':row['result'],'retrieval':row['retrieval'],
                         'grade':assess(case,mode,row['result'],row['retrieval'],corpus)})
    ensure(len(rows)==90,'incomplete_run')
    summary=aggregate(rows)
    report={'schema_version':2,'scope':'rescore_existing_first_run_no_new_model_calls',
            'first_run_manifest_sha256':sha((run_directory/'run.json').read_bytes()),
            'first_run_summary_sha256':sha((run_directory/'summary.json').read_bytes()),
            'first_run_case_hashes':first['case_files_sha256'],
            'scorer_sha256':sha((Path(__file__).with_name('evaluate_answers.py')).read_bytes()),
            'rescore_code_sha256':sha(Path(__file__).read_bytes()),
            'answer_contract_sha256':baseline['answer_contract_sha256'],
            'runtime_source_sha256':{k:v for k,v in baseline['source_sha256'].items() if k!='evaluate_answers.py'},
            'source_version':first['source_version'],'summary':summary,'rows':rows,'paid_api_calls':0,
            'new_generation_calls':0}
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('xb') as file:file.write(canonical(report))
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--index',type=Path,required=True)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:
        report=rescore(args.dataset,args.index,args.run_dir,args.output)
        print(json.dumps(report['summary'],ensure_ascii=False))
        if not report['summary']['hybrid']['meets_thresholds']:raise SystemExit(3)
    except (EvaluationError,CorpusError,OSError,ValueError,TypeError,KeyError) as error:
        print(json.dumps({'status':'error','error':str(error)}));raise SystemExit(2) from None

if __name__=='__main__':main()
