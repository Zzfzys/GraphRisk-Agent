"""Finite parameter enforcement, genuine training and best-weight restoration."""
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from torch_geometric.data import Batch,Data

from graphrisk.demo.stage09_evaluation import read_json
from graphrisk.graph.stage09_enhancement import load_contract
from graphrisk.graph.stage09_expansion_execution import settings_for,tasks_for,idle_start,retry_binding
from graphrisk.graph.stage09_expansion_training import positive_weight,probe_positions,train,choose
from graphrisk.graph.model import GraphSAGERiskModel
from graphrisk.graph.stage09_diagnosis import scores_for
from graphrisk.models.enhanced_training import metrics


SOURCE = Path(__file__).resolve().parents[2]


def test_nine_real_tasks_registered_scales_then_six_candidates_and_g6_weight():
    contract = load_contract(SOURCE / "configs/stage09_model_enhancement.json")
    settings = settings_for(SOURCE,contract)
    tasks = tasks_for(contract,settings)
    assert len(tasks) == 9
    assert [t["scale_target"] for t in tasks[:3]] == [50000,150000,"full_allowed_train"]
    assert [t["candidate_id"] for t in tasks[3:]] == ["g1","g2","g3","g4","g5","g6"]
    assert tasks[2]["parameters"] == tasks[3]["parameters"] and tasks[2]["seed"] != tasks[3]["seed"]
    y = np.asarray([0,0,0,0,1])
    assert positive_weight(y,tasks[-1]["parameters"]["positive_weight"]) == 2.
    assert positive_weight(y,tasks[3]["parameters"]["positive_weight"]) == 4.
    for bad in ([0,0],[0,2,1],[0,np.nan,1]):
        with pytest.raises(ValueError):
            positive_weight(bad,"train_ratio")
    with pytest.raises(ValueError,match="unregistered"):
        positive_weight(y,"ignore")


def test_selection_requires_all_six_and_uses_cost_then_order():
    tasks = [{"id":"g"+str(i),"lane":"search"} for i in range(1,7)]
    results = {t["id"]:{"metrics":{"ap":.3},"fit_seconds":2.} for t in tasks}
    assert choose(results,tasks) == "g1"
    results["g4"]["fit_seconds"] = 1.
    assert choose(results,tasks) == "g4"
    results["g2"]["metrics"]["ap"] = .4
    assert choose(results,tasks) == "g2"
    del results["g3"]
    with pytest.raises(ValueError,match="all six"):
        choose(results,tasks)
    np.testing.assert_array_equal(probe_positions(100,5),[0,24,49,74,99])


class Tiny:
    dimension = 28
    def __init__(self,n,prefix):
        self.ids = tuple(prefix+str(i) for i in range(n))
    def batch(self,positions):
        graphs = []
        for i in positions:
            x = torch.zeros(3,28)
            x[0,0] = float(i%2)
            x[2,0] = float(i%2)
            x[0,-5] = x[2,-5] = 1
            x[1,-4] = 1
            graphs.append(Data(x=x,edge_index=torch.tensor([[0,1,2,1],[1,0,1,2]]),root_index=torch.tensor([0])))
        return Batch.from_data_list(graphs)


def test_real_training_saves_best_reloads_all_roots_and_records_both_curves(tmp_path):
    dataset,inner = Tiny(12,"t"),Tiny(6,"v")
    y,yi = np.arange(12)%2,np.arange(6)%2
    task = {"id":"fixture","seed":7,"parameters":{"learning_rate":.02,"hidden_channels":8,"dropout":0.,"positive_weight":"sqrt_train_ratio"}}
    settings = {"device":"cpu","cpu_threads":1,"batch_size":4,"max_epochs":5,"patience":2,"train_probe_count":12}
    result = train(dataset,inner,y,yi,task,settings,tmp_path)
    history = read_json(tmp_path / "training-history.json")
    assert 1 <= result["best_epoch"] <= len(history) <= 5
    assert result["metrics"]["ap"] == max(r["inner_ap"] for r in history)
    assert all(np.isfinite(r["train_probe_ap"]) and np.isfinite(r["inner_roc_auc"]) for r in history)
    assert result["reload_rows"] == 6 and result["reload_max_abs_delta"] == 0
    assert result["positive_weight"] == 1.
    loaded = GraphSAGERiskModel(28,8,0.)
    loaded.load_state_dict(torch.load(tmp_path / "model.pt",weights_only=True))
    predicted = scores_for(loaded,inner,4,torch.device("cpu"))
    np.testing.assert_array_equal(predicted,np.load(tmp_path / "inner-scores.npy"))
    assert metrics(inner.ids,yi,predicted) == result["metrics"]
    for k,v in torch.load(tmp_path / "best-progress.pt",weights_only=True).items():
        torch.testing.assert_close(loaded.state_dict()[k],v)


def test_deadline_before_training_does_not_create_checkpoint(tmp_path):
    def fail():
        raise TimeoutError("budget exhausted")
    settings = {"device":"cpu","cpu_threads":1,"batch_size":4,"max_epochs":5,"patience":2,"train_probe_count":12}
    task = {"id":"fixture","seed":7,"parameters":{"learning_rate":.02,"hidden_channels":8,"dropout":0.,"positive_weight":"train_ratio"}}
    with pytest.raises(TimeoutError,match="budget"):
        train(Tiny(12,"t"),Tiny(6,"v"),np.arange(12)%2,np.arange(6)%2,task,settings,tmp_path,fail)
    assert not (tmp_path / "model.pt").exists()


@pytest.mark.parametrize("failure",["ram","gpu","always_busy"])
def test_gpu_cooldown_is_bounded_and_never_retries_other_resource_failure(failure):
    budget = load_contract(SOURCE / "configs/stage09_model_enhancement.json").resources
    samples,waits = [],[]
    def sample(gpu):
        assert gpu
        value = {"available_ram_gib":5.,"free_gpu_mib":5000,"gpu_utilization_percent":30 if len(samples)<2 else 0,
                 "disk_free_gib":{"C":10.,"D":10.,"E":20.}}
        samples.append(value)
        return value
    assert idle_start(SimpleNamespace(sample=sample),budget,waits.append)["gpu_utilization_percent"] == 0
    assert len(samples) == 3 and waits == [2,2]
    calls = []
    def bad(gpu):
        calls.append(gpu)
        return {"available_ram_gib":2.9 if failure == "ram" else 5.,"free_gpu_mib":4000 if failure == "gpu" else 5000,
                "gpu_utilization_percent":99 if failure == "always_busy" else 0,"disk_free_gib":{"C":10.,"D":10.,"E":20.}}
    with pytest.raises(ValueError,match="no new attempt reserved"):
        idle_start(SimpleNamespace(sample=bad),budget,lambda seconds:None)
    assert len(calls) == (5 if failure == "always_busy" else 1)


def test_recovery_only_bound_failed_task_not_abandoned_or_other_candidates():
    repairs = [{"allowed_failed_task":"scale-150000"},{"allowed_failed_task":"g2"}]
    assert retry_binding({"id":"g1"},None,repairs) is None
    assert retry_binding({"id":"g2"},{"state":"failed"},repairs) == repairs[1]
    assert retry_binding({"id":"scale-150000"},{"state":"failed"},repairs) == repairs[0]
    for task,state in (("g3","failed"),("g2","abandoned"),("g2","completed"),("g2","reserved")):
        with pytest.raises(ValueError,match="audit"):
            retry_binding({"id":task},{"state":state},repairs)
