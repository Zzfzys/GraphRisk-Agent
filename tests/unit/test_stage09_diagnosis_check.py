"""Reject misleading saved-history claims in the read-only route4 checker."""
from copy import deepcopy
import pytest
from scripts.check_stage09_graphsage import validate_history

def evidence():
    history=[{"epoch":i,"validation_pr_auc":ap} for i,ap in enumerate([.2,.4,.3,.2],1)]
    meta={"best_epoch":2,"stopped_after_epoch":4,"validation_metrics":{"pr_auc":.4}}
    return history,meta,{"max_epochs":50,"patience":2}

@pytest.mark.parametrize("change",["sequence","best","score","patience"])
def test_checker_rejects_tampered_or_premature_training_evidence(change):
    history,meta,plan=evidence()
    if change=="sequence":
        history[-1]["epoch"]=8
    elif change=="best":
        meta["best_epoch"]=3
    elif change=="score":
        meta["validation_metrics"]["pr_auc"]=.8
    else:
        plan["patience"]=10
    with pytest.raises(ValueError):
        validate_history(history,meta,plan)

def test_checker_accepts_restored_best_weights_history():
    history,meta,plan=evidence()
    assert validate_history(history,meta,plan)["epoch"]==2

