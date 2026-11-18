"""Temporal label gate, equivalent roots, exact AP/ranking and native reload."""
from dataclasses import asdict
from types import SimpleNamespace
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest
import xgboost as xgb

from graphrisk.agent.stage09_preflight import _content_hash
from graphrisk.graph.schema import GraphFeatureEncoder, GraphTransaction, GraphNode
from graphrisk.graph.stage09_enhancement import EnhancementLedger, load_contract
from graphrisk.models.enhanced_contract import TabularSettings
from graphrisk.models.enhanced_data import MatrixIterator, root_encoder, root_values, targets
from graphrisk.models.enhanced_training import metrics, predict_xgb, quantile, select_candidate, train_xgb
from graphrisk.models.tabular import FEATURE_COLUMNS, NUMERIC_FEATURES

ROOT = Path(__file__).resolve().parents[2]


def test_label_projection_never_reads_invalid_future_outcomes(tmp_path):
    path = tmp_path / "source.parquet"
    with duckdb.connect() as db:
        db.execute("CREATE TABLE source(transaction_id VARCHAR, source_transaction_dt_seconds BIGINT, is_fraud VARCHAR)")
        db.execute("INSERT INTO source VALUES ('1',10,'0'),('2',20,'1'),('3',30,'DO_NOT_READ')")
        db.execute("COPY source TO ? (FORMAT PARQUET)", [str(path)])
        window = {"min_relative_seconds":10,"max_relative_seconds":20,"count":2,"ids_sha256":_content_hash({"ids":["1","2"]})}
        # Legitimate parquet outcomes are integers; use a cast view to ensure only permitted IDs are projected.
        db.execute("CREATE TABLE typed AS SELECT transaction_id,source_transaction_dt_seconds,CASE WHEN source_transaction_dt_seconds<=20 THEN CAST(is_fraud AS INTEGER) ELSE 999 END AS is_fraud FROM source")
        db.execute("COPY typed TO ? (FORMAT PARQUET)", [str(tmp_path / "typed.parquet")])
        assert targets(db, tmp_path / "typed.parquet", window).transaction_id.tolist() == ["1","2"]
        with pytest.raises(ValueError, match="chronological"):
            targets(db, tmp_path / "typed.parquet", {**window,"count":3})


def test_vectorized_roots_equal_original_graph_encoder():
    records=[]
    for i in range(10):
        row = {n: float(i+1) for n in NUMERIC_FEATURES}
        row.update(transaction_id=str(i), transaction_time=pd.Timestamp('2000-01-01',tz='UTC')+pd.Timedelta(seconds=i),
            card_id='c',device_id='d',email_domain='e',address_id='a',product_code=['W','C','R','H','S'][i%5])
        row['amount_mean_last_24h'] = None if i==0 else float(i)
        records.append(row)
    frame=pd.DataFrame(records)
    transactions=[GraphTransaction.from_feature_row(r) for r in records]
    original=GraphFeatureEncoder.fit(transactions,train_transaction_ids=frame.transaction_id)
    optimized=root_encoder(frame,{})
    assert asdict(original)==asdict(optimized)
    expected=np.asarray([original.transform_node(GraphNode('transaction', r.transaction_id,r)) for r in transactions],dtype=np.float32)
    np.testing.assert_array_equal(root_values(frame, optimized),expected)


def test_metric_ties_use_id_not_incoming_order():
    got=metrics(['b','a'],[0,1],[.5,.5])
    assert got['recall_top1']==1 and got['precision_top1']==1
    assert got['ap']==.5 and got['roc_auc']==.5
    with pytest.raises(ValueError,match='probability'):
        metrics(['a','b'],[0,1],[np.inf,.2])


def test_fixed_candidate_selection_requires_complete_set_and_does_not_use_roc():
    candidates={k:{'status':'completed','metrics':{'ap':.2,'roc_auc':.7},'fit_seconds':2.,'model_sha256':'a'*64} for k in ('x1','x2','x3')}
    candidates['x2']['fit_seconds']=1.
    candidates['x3']['metrics']['roc_auc']=.99
    assert select_candidate(candidates,['x1','x2','x3'])['candidate']=='x2'
    with pytest.raises(ValueError,match='complete'):
        select_candidate({'x1':candidates['x1']},['x1','x2','x3'])


def test_native_categories_quantile_early_stopping_and_json_reload(tmp_path):
    frozen=TabularSettings.model_validate_json((ROOT/'configs/stage09_tabular_baselines.json').read_text())
    values=frozen.model_dump()
    values.update(max_estimators=25,patience=3,max_bin=16,threads=1)
    spec=SimpleNamespace(**values)
    frame=pd.DataFrame({'numeric':np.arange(40,dtype=np.float32), 'category':pd.Categorical(['v:a','v:b']*20,categories=['__missing__','__unknown__','v:a','v:b'])})
    labels=np.array([0,1]*20,dtype=np.float32)
    def factory():
        for start in range(0,40,7):
            yield frame.iloc[start:start+7],labels[start:start+7]
    dtrain=quantile(factory,40,spec,lambda:None)
    inner=quantile(factory,40,spec,lambda:None,dtrain)
    task={'parameters':{'id':'x1','max_depth':2,'learning_rate':.1,'min_child_weight':1}}
    model, history=train_xgb(task,spec,dtrain,inner,lambda:None)
    assert history['saved_rounds']==history['best_round']<=history['rounds_observed']
    path=tmp_path/'weights.json'
    model.save_model(path)
    loaded=xgb.Booster()
    loaded.load_model(path)
    np.testing.assert_array_equal(predict_xgb(model,factory,lambda:None),predict_xgb(loaded,factory,lambda:None))
    calls=[]
    with pytest.raises(ValueError,match='row count'):
        quantile(factory,99,spec,lambda:calls.append(1))
    assert calls


def test_development_controls_are_fixed_first_fold_and_keep_quota(tmp_path):
    contract=load_contract(ROOT/'configs/stage09_model_enhancement.json')
    ledger=EnhancementLedger(tmp_path/'ledger.json',contract,'b'*64)
    ledger.initialize()
    reg={'input_sha256':'a'*64,'fold':'fold-1','appendix_sha256':'c'*64,'control_id':'root28'}
    with pytest.raises(ValueError,match='fold-1'):
        ledger.reserve('xgboost','control','future',{**reg,'fold':'fold-2'})
    with pytest.raises(ValueError,match='identity'):
        ledger.reserve('xgboost','control','unknown',{**reg,'control_id':'unlimited'})
    attempt=ledger.reserve('xgboost','control','root',reg)
    attempt.finish('completed')
    with pytest.raises(ValueError,match='already attempted'):
        ledger.reserve('xgboost','control','again',reg)
    with pytest.raises(ValueError,match='decision'):
        ledger.open_results('outer')
    assert ledger.read()['decision_sha256'] is None and len(ledger.read()['fits'])==1
