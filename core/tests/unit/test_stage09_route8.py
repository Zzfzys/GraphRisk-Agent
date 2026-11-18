"""Final acceptance must retain negative results, input boundaries and real-call denominators."""
from copy import deepcopy
from datetime import UTC,datetime,timedelta
from pathlib import Path
from types import SimpleNamespace
import pytest
from graphrisk.demo.stage09_final import require_final_states,historical_binding,ReplayRunner
from graphrisk.demo.stage09_operations import file_binding
from graphrisk.serving.investigation_service import ServiceError


def accepted():
    return dict(engineering='done',overall_stage='In Progress',personal_learning='Pending',
        human_usefulness_review='Pending',gnn_increment='failed_no_promotion',
        agent_quality='historical_bounded_judgments_verified_current_profile_not_freshly_measured',
        new_real_fits=0,new_real_generation=0,paid_calls=0,
        criteria={k:True for k in ['historical_regrade','current_api_ui','layered_regression',
            'operations_recovery','frozen_models_and_ledgers','reproduction_guide']})


@pytest.mark.parametrize('change',[{'engineering':'done','criteria':{}},{'personal_learning':'done'},
    {'human_usefulness_review':'passed'},{'gnn_increment':'passed'},{'new_real_generation':1},
    {'paid_calls':1},{'overall_stage':'Done'},{'agent_quality':'fully_validated'}])
def test_final_gate_rejects_hidden_work_or_inflated_claims(change):
    with pytest.raises(ValueError):require_final_states(accepted()|change)


def test_final_gate_keeps_four_distinct_states():
    value=accepted();assert require_final_states(value)==value
    assert value['overall_stage']=='In Progress' and value['personal_learning']=='Pending'


def test_historical_resolution_requires_exact_bytes_and_rejects_escape(tmp_path):
    archive=tmp_path/'archive';archive.mkdir();(archive/'old.py').write_text('old',encoding='utf-8')
    (tmp_path/'old.py').write_text('new',encoding='utf-8')
    item={'path':'old.py',**file_binding(archive/'old.py')}
    assert historical_binding(tmp_path,item,[archive])==archive/'old.py'
    with pytest.raises(ValueError):historical_binding(tmp_path,item,[])
    with pytest.raises(ValueError):historical_binding(tmp_path,{'path':'../escape',**file_binding(archive/'old.py')},[archive])


def test_replay_runner_does_not_accept_another_request_or_mutate_original():
    from graphrisk.agent.contracts import Stage05Request
    request=Stage05Request(transaction_id='T3',dataset_source='synthetic',policy_as_of='2026-07-01',relation_limit=3)
    class Result:
        def model_copy(self,deep):return {'copied':True}
    original=SimpleNamespace(request=request,result=Result())
    runner=ReplayRunner([original]);assert runner.run(request.model_dump())=={'copied':True}
    for changed in [{'transaction_id':'T4'},{'policy_as_of':'2026-06-30'},{'relation_limit':4}]:
        with pytest.raises(ServiceError):runner.run(request.model_dump()|changed)
    assert runner.calls==1


def test_ci_source_fingerprint_includes_workbench_and_root_fixture_configuration(tmp_path):
    from stage09_ci import source_inventory
    for name in ['pyproject.toml','uv.lock','Dockerfile','.dockerignore','.gitattributes','streamlit_app.py','conftest.py']:
        (tmp_path/name).write_text('original',encoding='utf-8')
    before=source_inventory(tmp_path)
    for name in ['streamlit_app.py','conftest.py']:
        (tmp_path/name).write_text('changed',encoding='utf-8')
        assert source_inventory(tmp_path)[name]!=before[name]