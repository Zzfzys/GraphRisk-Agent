"""Prepare legacy14 and root28 with explicit chronological train/inner alignment."""
import duckdb
import numpy as np
import pandas as pd
import pytest

from graphrisk.agent.stage09_preflight import _content_hash
from graphrisk.models.enhanced_data import batches, prepare_legacy, targets
from graphrisk.models.tabular import FEATURE_COLUMNS, NUMERIC_FEATURES


def window(begin,end):
    ids=[str(i) for i in range(begin,end)]
    return {'min_relative_seconds':begin,'max_relative_seconds':end-1,'count':len(ids),'ids_sha256':_content_hash({'ids':ids})}


def test_compact_and_root_preparation_and_small_split_selection(tmp_path):
    root=tmp_path
    interim=root/'data/interim/ieee-cis'
    interim.mkdir(parents=True)
    artifact=root/'prepared'
    artifact.mkdir()
    frame=pd.DataFrame([{**{n:float(i) if n in NUMERIC_FEATURES else 'key-'+str(i) for n in FEATURE_COLUMNS},
        'transaction_id':str(i),'product_code':['W','C','R','H','S'][i%5]} for i in range(10)])
    frame['device_id']=['device-key-'+str(i) for i in range(10)]
    frame.loc[6,'device_id']='unknown-device-6'
    frame.to_parquet(interim/'features_all.parquet',index=False)
    canonical=pd.DataFrame({'transaction_id':[str(i) for i in range(10)],'source_transaction_dt_seconds':range(10),'is_fraud':[i%2 for i in range(10)]})
    canonical.to_parquet(interim/'transactions.parquet',index=False)
    with duckdb.connect() as db:
        info=prepare_legacy(db,root,artifact,window(0,6),window(6,10),lambda:None)
        assert info['root_dimensions']==28
        train=targets(db,interim/'transactions.parquet',window(0,6))
        inner=targets(db,interim/'transactions.parquet',window(6,10))
        tx,ty=next(batches(db,root,artifact,None,'root28',train,'train'))
        vx,vy=next(batches(db,root,artifact,None,'root28',inner,'inner'))
        assert tx.shape==(6,28) and vx.shape==(4,28)
        np.testing.assert_array_equal(ty,train.is_fraud)
        np.testing.assert_array_equal(vy,inner.is_fraud)
        old,_=next(batches(db,root,artifact,None,'legacy14',inner,'inner'))
        assert old['card_id'].astype(str).tolist()==['__unknown__']*4
        assert old['device_id'].astype(str).tolist()[0]=='__missing__'
        with pytest.raises(ValueError,match='split'):
            next(batches(db,root,artifact,None,'root28',train,'bad'))
        with pytest.raises(ValueError,match='order'):
            next(batches(db,root,artifact,None,'legacy14',inner.iloc[::-1].reset_index(drop=True),'inner'))
        with pytest.raises(ValueError,match='order'):
            next(batches(db,root,artifact,None,'root28',inner.iloc[::-1].reset_index(drop=True),'inner'))
