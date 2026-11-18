"""Historical coverage without loading legacy models or an all-transaction index."""
import gc
import math
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

from graphrisk.graph.coverage_data import DiskHistory, NewTransaction
from graphrisk.graph.service import GraphRiskPrediction, GraphTargetNotCoveredError
from graphrisk.graph.schema import ENTITY_FIELDS
from graphrisk.investigation.risk_tool import RiskModelTool
from graphrisk.models.enhanced_execution import binding, checked_json
from graphrisk.graph.coverage_resources import RUNTIME
from graphrisk.serving.predictor import RiskPrediction, TransactionNotFoundError


class CoverageArtifactError(RuntimeError):
    """A frozen dependency changed; never classify this as a bad user event."""


def local_path(root, relative):
    root=Path(root).resolve()
    path=(root/relative).resolve()
    if not path.is_relative_to(root) or not any(path.is_relative_to(root/p) for p in (
            'artifacts','data/interim','reports/generated')):
        raise ValueError('coverage file escapes registered data/artifact roots')
    return path


def load_coverage(root, path, expected):
    value=checked_json(path)
    if value['content_sha256']!=expected or value['schema_version']!='stage09-historical-coverage-v1':
        raise ValueError('coverage profile version/hash mismatch')
    if value['score_mode']!='xgboost_only' or value['gnn_role']!='experimental':
        raise ValueError('graph model has no promotion authority')
    for relative, wanted in value['files'].items():
        if binding(local_path(root,relative))!=wanted:
            raise ValueError('coverage bound file differs')
    decision=checked_json(local_path(root,value['decision_path']))
    if (decision['content_sha256']!=value['decision_sha256'] or decision['promoted']
            or not decision['primary_quality_eligible']):
        raise ValueError('coverage parent model decision differs')
    for split in value['splits'].values():
        if split['selection_cutoff']>=split['min_second']:
            raise ValueError('model selection overlaps declared held-out coverage')
    if set(value['splits'])!={'validation','test'} or sorted(s['rows'] for s in value['splits'].values())!=[88581,88581]:
        raise ValueError('complete historical split denominator required')
    if value.get('runtime_budget')!=RUNTIME or len(value['seeds'])!=3 or len(set(value['seeds']))!=3:
        raise ValueError('coverage runtime/seed contract mismatch')
    for name in (value['scores_path'],value['decision_path'],*value['tree_models'].values()):
        if name not in value['files']:raise ValueError('coverage file has no immutable binding')
    if 'legacy_calendar_timezone' in value:
        calendar_path=Path(path).parent/'calendar-contract.json'
        calendar=checked_json(calendar_path)
        if (calendar['timezone']!=value['legacy_calendar_timezone'] or calendar['rows']!=590540
                or calendar['hour_mismatches'] or calendar['weekday_mismatches']
                or calendar['parent_coverage_sha256']!=value['parent_coverage_sha256']
                or calendar_path.resolve().relative_to(Path(root).resolve()).as_posix() not in value['files']):
            raise ValueError('legacy calendar source/coverage proof changed')
    return value


class CoverageGraphService:
    def __init__(self,root,profile,transaction_parquet,feature_parquet):
        self.root,self.profile=Path(root),profile
        self.dataset_source='ieee_cis'
        self.model_version=profile['gnn_model_version']
        self.transaction_parquet,self.feature_parquet=Path(transaction_parquet),Path(feature_parquet)
        if profile.get('schema_version')=='stage09-historical-coverage-v1':
            for path in (self.transaction_parquet,self.feature_parquet):
                if path.resolve().relative_to(self.root.resolve()).as_posix() not in profile['files']:
                    raise ValueError('configured coverage data is not bound by the profile')
        self.startup_error=None
        self._inference_lock=threading.Lock()
        self.history=DiskHistory(self.transaction_parquet,self.feature_parquet,
                                 self.root/'artifacts/tmp/stage09-coverage',
                                 calendar_timezone=profile.get('legacy_calendar_timezone','UTC'))
        self.scores=local_path(self.root,profile['scores_path'])
        paths={local_path(self.root,n) for n in profile['files']}
        paths.update((self.scores,self.transaction_parquet,self.feature_parquet))
        self._stats={p:(p.stat().st_size,p.stat().st_mtime_ns) for p in paths}

    def _intact(self):
        try:
            if any((p.stat().st_size,p.stat().st_mtime_ns)!=v for p,v in self._stats.items()):
                raise CoverageArtifactError('live coverage data changed; reload and revalidate artifacts')
        except OSError as error:
            raise CoverageArtifactError('coverage dependency disappeared') from error

    def lookup(self,key):
        self._intact()
        with self.history.connect() as db:
            frame=db.execute('SELECT * FROM read_parquet(?) WHERE transaction_id=?',[str(self.scores),key]).fetchdf()
        if not len(frame): raise GraphTargetNotCoveredError(key)
        if len(frame)!=1: raise ValueError('duplicate coverage registry ID')
        return frame.iloc[0].to_dict()

    def coverage(self,key):
        try:
            row=self.lookup(key)
        except GraphTargetNotCoveredError:
            return {'status':'out_of_coverage','transaction_id':key,'scope':'historical',
                    'reason':'not_registered_held_out_target','risk_score':None}
        except (OSError,ValueError,CoverageArtifactError):
            return {'status':'artifact_unavailable','transaction_id':key,'risk_score':None}
        return {'status':'covered','transaction_id':key,'split':row['split'],
            'graph_index_present':True,'score_eligible':True,'score_scope':'held_out',
            'selection_cutoff':int(row['selection_cutoff']),'event_second':int(row['event_second']),
            'primary_model_version':row['primary_model_version'],
            'graph_model_version':row['graph_model_version'],'graph_role':'experimental',
            'probability_calibrated':False}

    def predict(self,key):
        row=self.lookup(key)
        return GraphRiskPrediction(key,float(row['graph_score']),row['graph_model_version'],
            None,None,None,None,score_role='experimental')

    def find_neighbors(self,key,*,limit):
        if isinstance(limit,bool) or not 1<=limit<=50: raise ValueError('limit must be 1..50')
        target=self.lookup(key)
        row=self.history.graph_row(key)
        roots={kind:row[field] for field,kind in ENTITY_FIELDS}
        entity_fields={kind:field for field,kind in ENTITY_FIELDS}
        matches=[]
        for candidate in self.history.selected_history(row):
            entities=tuple((kind,value) for kind,value in roots.items()
                           if not value.lower().startswith('unknown-') and
                           value==candidate[entity_fields[kind]])
            if not entities: continue
            score=None
            reason='outside_independent_score_scope'
            try:
                other=self.lookup(candidate['transaction_id'])
                if other['graph_model_version']==target['graph_model_version']:
                    score=float(other['graph_score']);reason=None
                else: reason='different_model_scope'
            except GraphTargetNotCoveredError: pass
            matches.append(SimpleNamespace(transaction_id=candidate['transaction_id'],graph_risk_score=score,
                seconds_before_target=math.ceil((row['transaction_time']-candidate['transaction_time']).total_seconds()),
                shared_entities=entities,evidence_role='relation_only' if score is None else 'scored',
                score_unavailable_reason=reason))
        # All neighbors use the same observable time/ID order. Null scores never become zeros.
        matches.sort(key=lambda n:(n.seconds_before_target,n.transaction_id))
        return matches[:limit]

    def prospective(self,raw,guard=None):
        if not self._inference_lock.acquire(blocking=False):
            raise BlockingIOError('one prospective inference at a time')
        try:
            return self._prospective(raw,guard)
        finally:
            self._inference_lock.release()

    def _prospective(self,raw,guard=None):
        """Single-event CPU inference, one weight at a time; no history mutation."""
        import numpy as np
        import pandas as pd
        import torch
        from torch_geometric.data import Data
        from graphrisk.features.wide_preprocessing import WidePreprocessor
        from graphrisk.graph.schema import GraphFeatureEncoder
        from graphrisk.graph.stage09_expansion_data import encode_graph
        from graphrisk.graph.stage09_conditional_data import neural_core,normalized
        from graphrisk.graph.model import GraphSAGERiskModel
        if guard is None:
            import psutil
            started=time.perf_counter()
            def guard():
                if (psutil.virtual_memory().available<RUNTIME['minimum_available_ram_gib']*2**30
                        or psutil.Process().memory_info().rss>RUNTIME['maximum_process_rss_mib']*2**20):
                    raise MemoryError('prospective resource floor reached')
                if time.perf_counter()-started>180:raise TimeoutError('prospective deadline reached')
        event=NewTransaction.model_validate(raw)
        if 'legacy_calendar_timezone' not in self.profile:
            raise ValueError('new events require an explicitly verified frozen calendar contract')
        self._intact();guard()
        candidates=[v for v in self.profile['splits'].values() if v['selection_cutoff']<event.source_transaction_dt_seconds]
        if not candidates: raise ValueError('new event predates every frozen model selection cutoff')
        spec=max(candidates,key=lambda v:v['selection_cutoff'])
        folder=local_path(self.root,spec['trained_directory'])
        for name in self.profile['model_inputs'][spec['fold']]:
            if binding(local_path(self.root,name))!=self.profile['files'][name]:
                raise CoverageArtifactError('prospective model/input changed')
        core=WidePreprocessor.load(folder/'fold-1-core.json')
        tree=WidePreprocessor.load(folder/'fold-1-source_as_received.json')
        columns=list(dict.fromkeys(core.state['input_columns']+tree.state['input_columns']))
        row,features=self.history.new_features(event,columns=columns,raw_names=self.profile['raw_fields'])
        graph=self.history.graph(row)
        encoder=checked_json(folder/'root-encoder.json')['encoder']
        encoder=GraphFeatureEncoder(**{k:tuple(v) for k,v in encoder.items()})
        x=encode_graph(graph,encoder)
        values=core.transform(pd.DataFrame([features],columns=core.state['input_columns']))
        normalizer=checked_json(folder/'core-normalizer.json')
        extra=normalized(neural_core(values,core.state),normalizer)
        xx=np.zeros((len(x),x.shape[1]+extra.shape[1]),dtype=np.float32)
        xx[:,:x.shape[1]]=x;xx[0,x.shape[1]:]=extra[0]
        edges=np.asarray(sorted([*graph.edges,*[(b,a) for a,b in graph.edges]]),dtype=np.int64).reshape(-1,2)
        data=Data(x=torch.from_numpy(xx),edge_index=torch.from_numpy(edges.T.copy()),
                  root_index=torch.tensor([0]),batch=torch.zeros(len(x),dtype=torch.long))
        torch.set_num_threads(1)
        scores=[]
        for seed in self.profile['seeds']:
            guard()
            model=GraphSAGERiskModel(xx.shape[1],64,.4)
            model.load_state_dict(torch.load(folder/('gnn-'+str(seed))/'model.pt',map_location='cpu',weights_only=True))
            model.eval()
            with torch.no_grad(): scores.append(float(torch.sigmoid(model(data)).item()))
            del model;gc.collect()
        guard()
        # A disposable CPU worker prevents native tree buffers from accumulating
        # in the API and avoids the large JSON parser alongside Torch.
        scratch=self.root/'artifacts/tmp/stage09-coverage'/uuid4().hex
        scratch.mkdir(parents=True)
        output=scratch/'tree.npy'
        job={'model':str(local_path(self.root,self.profile['tree_models'][spec['fold']])),
            'source_sha256':binding(folder/'x3/model.json')['sha256'],
            'preprocessor':str(folder/'fold-1-source_as_received.json'),
            'features':[{k:features[k] for k in tree.state['input_columns']}], 'output':str(output)}
        (scratch/'job.json').write_text(json.dumps(job,allow_nan=False),encoding='utf-8')
        try:
            subprocess.run([sys.executable,'-m','graphrisk.graph.coverage_tree_worker',str(scratch/'job.json')],
                check=True,timeout=120,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),capture_output=True)
            score=float(np.load(output,allow_pickle=False)[0])
            tree_resources=json.loads(output.with_suffix('.resources.json').read_text(encoding='utf-8'))
        finally:
            # Only files created for this request, never original data or models.
            for file in scratch.iterdir():
                if file.is_file():file.unlink()
            if not any(scratch.iterdir()):scratch.rmdir()
        guard()
        if not all(math.isfinite(v) and 0<=v<=1 for v in [score,*scores]):
            raise ValueError('nonfinite prospective prediction')
        return {'transaction_id':event.transaction_id,'scope':'prospective_replay','fold':spec['fold'],
            'risk_score':score,'graph_risk_score':float(np.mean(scores)),'graph_seed_scores':scores,
            'graph_role':'experimental','fusion_score':None,'probability_calibrated':False,
            'selected_history_ids':list(graph.selected_history_ids),'label_read':False,'history_mutated':False,
            'tree_worker_peak_rss_mib':max(s['peak_rss_mib'] for s in tree_resources['samples'])}


class CoveragePredictor:
    def __init__(self,service): self.service=service
    def predict(self,key):
        try: row=self.service.lookup(key)
        except GraphTargetNotCoveredError as error: raise TransactionNotFoundError(key) from error
        score=float(row['primary_score']);thresholds=self.service.profile['risk_thresholds']
        level='low' if score<thresholds['low'] else 'medium' if score<thresholds['high'] else 'high'
        return RiskPrediction(key,score,level,'xgboost',row['primary_model_version'],'ieee_cis')


class CoverageRiskTool(RiskModelTool):
    def __init__(self,repository,service):
        if (repository.dataset_source!='ieee_cis' or
                repository.transaction_parquet.resolve()!=service.transaction_parquet.resolve() or
                repository.feature_parquet.resolve()!=service.feature_parquet.resolve()):
            raise ValueError('coverage primary tool source mismatch')
        self.repository=repository
        self._startup_error=None
        self._predictor=CoveragePredictor(service)

    def get_risk_score(self,raw_request):
        from graphrisk.investigation.contracts import GetRiskScoreRequest,GetRiskScoreResult,validate_tool_request
        request,failure=validate_tool_request(GetRiskScoreRequest,GetRiskScoreResult,raw_request)
        if failure is not None:return failure
        if request.dataset_source!=self.repository.dataset_source:
            return self._failure('data_source_mismatch',request.transaction_id,request.dataset_source)
        coverage=self._predictor.service.coverage(request.transaction_id)
        if coverage['status']=='out_of_coverage':
            # Preserve a true missing transaction error; existing training rows
            # are present but intentionally have no independent risk score.
            from graphrisk.investigation.transaction_tools import get_transaction
            transaction=get_transaction(request.model_dump(),self.repository)
            if transaction.error:return self._failure(transaction.error.code,request.transaction_id,request.dataset_source)
            return self._failure('prediction_out_of_coverage',request.transaction_id,request.dataset_source)
        if coverage['status']=='artifact_unavailable':
            return self._failure('model_artifact_unavailable',request.transaction_id,request.dataset_source)
        return super().get_risk_score(raw_request)
