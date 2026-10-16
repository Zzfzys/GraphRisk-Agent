"""CPU-only, resumable coverage construction; never fit or project outcome labels."""
import gc
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

import numpy as np
import pandas as pd
import psutil
import torch
import xgboost as xgb

from graphrisk.demo.stage09_evaluation import write_json,read_json,exclusive
from graphrisk.features.wide_execution import FeatureMonitor,peak_working_set_mib
from graphrisk.graph.model import GraphSAGERiskModel
from graphrisk.graph.stage09_confirmation_execution import context,check as confirmation_check
from graphrisk.graph.stage09_confirmation_data import prepare_prediction_data
from graphrisk.graph.stage09_conditional_data import ConditionalGraphs
from graphrisk.graph.stage09_diagnosis import scores_for
from graphrisk.graph.stage09_packed import verify_cache
from graphrisk.models.enhanced_execution import binding,checked_json,sealed,open_db
from graphrisk.models.enhanced_data import batches
from graphrisk.models.enhanced_training import predict_xgb
from graphrisk.graph.coverage_service import local_path,load_coverage
from graphrisk.graph.coverage_resources import CoverageMonitor,RUNTIME


def model_for_time(windows,first_second):
    choices=[w for w in windows if max(w[k]['max_relative_seconds'] for k in ('train','early_stop','calibration'))<first_second]
    if not choices: raise ValueError('no model finished selection before target period')
    return max(choices,key=lambda w:w['calibration']['max_relative_seconds'])


def verify_prepared(folder):
    value=checked_json(folder/'prepared.json')
    for name,wanted in value['files'].items():
        if binding(folder/name)!=wanted:raise ValueError('coverage prepared file drift')
    cache=verify_cache(folder/'cache')
    if cache['content_sha256']!=value['cache_sha256']:raise ValueError('coverage cache drift')
    return cache


def execute(source,root,parent,name,reuse_prepared=None):
    source,root,parent=map(lambda p:Path(p).resolve(),(source,root,parent))
    if root.drive.upper()!='E:' or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,60}',name):
        raise ValueError('safe E-drive coverage run required')
    plan,contract,ledger,trained=context(root,parent,False)
    confirmation_check(root,parent)
    decision=checked_json(parent/'model-decision.json')
    if decision['promoted'] or not decision['primary_quality_eligible']:
        raise ValueError('this profile requires qualified XGB and experimental graph')
    frozen=checked_json(Path(plan['registration_directory'])/'freeze.json')
    windows={key:frozen['data']['splits'][key] for key in ('validation','test')}
    selected={key:model_for_time(plan['windows'],window['min_relative_seconds']) for key,window in windows.items()}
    artifact=root/'artifacts/stage-09/route5b'/name
    report=root/'reports/generated/stage09/route5b'/name
    artifact.mkdir(parents=True,exist_ok=True);report.mkdir(parents=True,exist_ok=True)
    with exclusive(report/'execution.lock'):
        if (report/'closure.json').exists():return check(root,report)
        before=binding(Path(plan['ledger_path']))
        recipe={'schema_version':'stage09-coverage-recipe-v1','parent_decision':decision['content_sha256'],
            'selected_folds':{k:v['name'] for k,v in selected.items()},'windows':windows,
            'device':'cpu','runtime_budget':RUNTIME,'tree_serialization':'native_ubjson_isolated_worker',
            'binary_directory':'artifacts/stage-09/route5b/20261015-coverage-binary-v1',
            'seed_aggregation':'fixed_three_seed_score_mean_experimental_only','seeds':plan['seeds'],
            'new_fits':0,'new_scoring_label_batches':0,'new_generation_calls':0,'paid_calls':0,
            'ledger_before':before,'source_files':{str(p.relative_to(source)):binding(p)
              for base in ('src','api','scripts','configs') for p in (source/base).rglob('*')
              if p.is_file() and p.suffix in ('.py','.json','.yaml','.ps1') and '__pycache__' not in p.parts}}
        if reuse_prepared is not None:
            reuse_prepared=Path(reuse_prepared).resolve()
            if not reuse_prepared.is_relative_to(root/'artifacts/stage-09/route5b'):
                raise ValueError('reused prepared data must stay under registered E artifacts')
            verify_prepared(reuse_prepared)
            previous=checked_json(reuse_prepared/'prepared.json')
            if (previous['window']!=windows['validation'] or previous['trained_preprocessor']!=binding(trained/selected['validation']['name']/'fold-1-core.json')):
                raise ValueError('reused coverage data belongs to a different time/model scope')
            recipe['reuse_prepared']={'path':str(reuse_prepared),'binding':binding(reuse_prepared/'prepared.json')}
        if (report/'recipe.json').exists():
            if checked_json(report/'recipe.json')!=sealed(recipe):raise ValueError('coverage source/recipe changed; preserve run and register a new version')
        else:write_json(report/'recipe.json',sealed(recipe))
        snapshot=artifact/'source-snapshot'
        for relative,wanted in recipe['source_files'].items():
            dest=snapshot/relative
            if not dest.exists():
                dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/relative,dest)
            if binding(dest)!=wanted:raise ValueError('coverage source snapshot changed')
        torch.set_num_threads(1)
        db=open_db(root,Path(plan['side_path']))
        db.execute("SET memory_limit='128MB'");db.execute('SET threads=1')
        db.execute('SET temp_directory=?',[str(artifact/'duckdb-spill')])
        monitor=CoverageMonitor(report/'resources.json')
        monitor.require()
        thread=threading.Thread(target=monitor.loop,daemon=True);thread.start()
        started=time.perf_counter()
        phase='prepare'
        try:
            folder=artifact/'validation'
            if not (folder/'prepared.json').exists():
                if folder.exists():raise ValueError('partial preparation preserved; use a new run directory')
                if reuse_prepared is not None:
                    shutil.copytree(reuse_prepared,folder)
                else:
                    folder.mkdir()
                    value=prepare_prediction_data(db,root,Path(plan['side_path']),folder,
                        trained/selected['validation']['name'],windows['validation'],monitor.require)
                    write_json(folder/'prepared.json',sealed(value))
            verify_prepared(folder)
            phase='predict'
            outputs=[]
            for split in ('validation','test'):
                destination=artifact/(split+'-scores.parquet')
                result_file=report/(split+'-scores.json')
                if result_file.exists():
                    saved=checked_json(result_file)
                    if binding(destination)!=saved['file']:raise ValueError('coverage score checkpoint changed')
                    outputs.append(pd.read_parquet(destination));continue
                window=windows[split];fitdir=trained/selected[split]['name']
                if split=='validation':
                    prepared=artifact/'validation'
                    frame=pd.read_parquet(prepared/'targets-test.parquet')
                    cache=verify_prepared(prepared)
                    binary=root/recipe['binary_directory']/(selected[split]['name']+'-x3.ubj')
                    job=report/'validation-tree-job.json'
                    write_json(job,{'model':str(binary),'source_sha256':binding(fitdir/'x3/model.json')['sha256'],
                        'preprocessor':str(fitdir/'fold-1-source_as_received.json'), 'side':plan['side_path'],
                        'targets':str(prepared/'targets-test.parquet'),'output':str(artifact/'validation-tree.npy')})
                    monitor.require()
                    subprocess.run([sys.executable,'-m','graphrisk.graph.coverage_tree_worker',str(job)],
                        check=True,timeout=330,creationflags=subprocess.CREATE_NO_WINDOW)
                    base=np.load(artifact/'validation-tree.npy',allow_pickle=False)
                    graphs=[]
                    for seed in plan['seeds']:
                        monitor.require()
                        reader=ConditionalGraphs(prepared/'cache',cache,'test','full',core=prepared/'core-test.npy')
                        model=GraphSAGERiskModel(reader.dimension,64,.4)
                        try:
                            model.load_state_dict(torch.load(fitdir/('gnn-'+str(seed))/'model.pt',weights_only=True,map_location='cpu'))
                            graphs.append(scores_for(model,reader,64,torch.device('cpu'),monitor.require))
                        finally:reader.close();del model;gc.collect()
                        print('coverage graph seed '+str(seed)+' complete',flush=True)
                else:
                    prepared=trained/'historical-test'
                    cache=verify_cache(prepared/'cache')
                    frame=pd.read_parquet(prepared/'targets-test.parquet')
                    base=np.load(prepared/'x3-scores.npy',allow_pickle=False)
                    graphs=[np.load(prepared/('gnn-'+str(seed)+'-scores.npy'),allow_pickle=False) for seed in plan['seeds']]
                    if tuple(frame.transaction_id)!=tuple(cache['target_ids']['test']):raise ValueError('test full graph index mismatch')
                if len(frame)!=window['count'] or len(set(frame.transaction_id))!=window['count']:
                    raise ValueError('complete fixed coverage denominator mismatch')
                values=np.column_stack([base,*graphs])
                if not np.isfinite(values).all() or ((values<0)|(values>1)).any():raise ValueError('invalid coverage probabilities')
                cutoff=selected[split]['calibration']['max_relative_seconds']
                if frame.source_transaction_dt_seconds.min()<=cutoff:raise ValueError('model selection reached covered targets')
                result=pd.DataFrame({'transaction_id':frame.transaction_id,'event_second':frame.source_transaction_dt_seconds,
                    'split':split,'selection_cutoff':cutoff,'primary_score':base,'graph_score':np.mean(graphs,axis=0),
                    'primary_model_version':'x3-'+selected[split]['name']+'-historical-v1',
                    'graph_model_version':'c4-'+selected[split]['name']+'-three-seed-mean-v1'})
                for seed,score in zip(plan['seeds'],graphs,strict=True):result['graph_seed_'+str(seed)]=score
                result.to_parquet(destination,index=False)
                write_json(result_file,sealed({'rows':len(result),'file':binding(destination),'graph_cache':cache['content_sha256'],
                    'selection_cutoff':cutoff,'source':'fresh_cpu_prediction' if split=='validation' else 'sealed_parent_predictions'}))
                outputs.append(result);print('coverage '+split+' complete: '+str(len(result)),flush=True)
                del values,graphs,base;gc.collect()
            phase='registry'
            result=pd.concat(outputs,ignore_index=True)
            if len(result)!=177162 or result.transaction_id.duplicated().any():raise ValueError('177162 unique held-out targets required')
            scorefile=artifact/'coverage-scores.parquet';result.to_parquet(scorefile,index=False)
            files={}
            def bind(path):
                relative=Path(path).resolve().relative_to(root).as_posix()
                files[relative]=binding(Path(path));return relative
            model_inputs={}
            tree_models={}
            for window in selected.values():
                fold=window['name'];fitdir=trained/fold
                model_inputs[fold]=[bind(fitdir/n) for n in ('fold-1-core.json','fold-1-source_as_received.json','core-normalizer.json','root-encoder.json','x3/model.json',
                    *['gnn-'+str(s)+'/model.pt' for s in plan['seeds']])]
                tree_models[fold]=bind(root/recipe['binary_directory']/(fold+'-x3.ubj'))
                model_inputs[fold].append(tree_models[fold])
            for path in (artifact/'validation/cache').rglob('*'):
                if path.is_file():bind(path)
            for path in (trained/'historical-test/cache').rglob('*'):
                if path.is_file():bind(path)
            bind(artifact/'validation/core-test.npy');bind(trained/'historical-test/core-test.npy')
            scorepath=bind(scorefile)
            canonical=root/'data/interim/ieee-cis/transactions.parquet';features=root/'data/interim/ieee-cis/features_all.parquet'
            bind(canonical);bind(features)
            parent_decision=bind(parent/'model-decision.json');bind(parent/'closure.json')
            catalog=read_json(trained/'fold-2/field-catalog.json')
            raw=[v['name'] for v in catalog if v['source'] in ('transaction','identity')]
            thresholds=read_json(root/'artifacts/stage-02/ieee-cis/champion/metadata.json')['risk_thresholds']
            profile=sealed({'schema_version':'stage09-historical-coverage-v1','score_mode':'xgboost_only','gnn_role':'experimental',
                'decision_path':parent_decision,'decision_sha256':decision['content_sha256'],'files':files,
                'scores_path':scorepath,'comparison_run':'stage05-route8','primary_model_version':'x3-historical-coverage-v1',
                'gnn_model_version':'c4-historical-coverage-v1','seeds':plan['seeds'],'raw_fields':raw,
                'model_inputs':model_inputs,'tree_models':tree_models,'runtime_budget':RUNTIME,
                'risk_thresholds':thresholds,'splits':{k:{'fold':v['name'],
                    'min_second':windows[k]['min_relative_seconds'],'selection_cutoff':v['calibration']['max_relative_seconds'],
                    'rows':windows[k]['count'],'trained_directory':(trained/v['name']).relative_to(root).as_posix()} for k,v in selected.items()}})
            profilepath=artifact/'coverage-profile.json';write_json(profilepath,profile)
            load_coverage(root,profilepath,profile['content_sha256'])
            if binding(Path(plan['ledger_path']))!=before:raise ValueError('coverage must not mutate fit/label ledger')
            monitor.stop.set();thread.join(timeout=5);monitor.require()
            write_json(report/'resources.json',{'budget':RUNTIME,'samples':monitor.samples,'status':'passed'})
            closure=sealed({'status':'built_not_accepted','parent_decision_sha256':decision['content_sha256'],
                'profile_path':profilepath.relative_to(root).as_posix(),'profile_sha256':profile['content_sha256'],
                'rows':177162,'split_rows':{k:int((result.split==k).sum()) for k in windows},
                'excluded_rows':0,'missing_index_rows':0,'invalid_score_rows':0,'runtime_seconds':time.perf_counter()-started,
                'peak_working_set_mib':peak_working_set_mib(),'resources':binding(report/'resources.json'),
                'ledger_before':before,'ledger_after':binding(Path(plan['ledger_path'])),
                'new_fits':0,'new_generation_calls':0,'paid_calls':0,'new_scoring_label_batches':0,
                'acceptance':'pending independent prospective/API/UI/rollback checks'})
            write_json(report/'closure.json',closure)
            return closure
        except Exception as error:
            write_json(report/('failure-'+str(time.time_ns())+'.json'),{'phase':phase,'error':type(error).__name__,
                'seconds':time.perf_counter()-started,'new_fits':0,'paid_calls':0})
            raise
        finally:
            monitor.stop.set();thread.join(timeout=5);db.close()
            if not (report/'resources.json').exists():
                write_json(report/'resources.json',{'budget':RUNTIME,'samples':monitor.samples,
                    'status':'failed' if monitor.violation.is_set() else 'interrupted'})


def check(root,report):
    value=checked_json(Path(report)/'closure.json')
    profile=load_coverage(root,local_path(root,value['profile_path']),value['profile_sha256'])
    if value['rows']!=177162 or value['split_rows']!={'validation':88581,'test':88581}:
        raise ValueError('coverage denominator changed')
    if binding(Path(report)/'resources.json')!=value['resources']:
        raise ValueError('coverage resource evidence changed')
    recipe=checked_json(Path(report)/'recipe.json')
    snapshot=local_path(root,value['profile_path']).parent/'source-snapshot'
    for relative,wanted in recipe['source_files'].items():
        target=(snapshot/relative).resolve()
        if not target.is_relative_to(snapshot.resolve()) or binding(target)!=wanted:
            raise ValueError('coverage archived source changed')
    return value
