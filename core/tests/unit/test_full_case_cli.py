from pathlib import Path

import pytest

import run_full_case_evaluation as cli


def test_dry_run_never_constructs_a_mutating_budget_or_provider(monkeypatch):
    monkeypatch.setattr(cli,'check',lambda path:({}, {'status':'validated_not_sent','new_model_calls':0}))
    def forbidden(*args,**kwargs):raise AssertionError('dry run touched live execution')
    monkeypatch.setattr(cli,'InvestigationBudget',forbidden)
    monkeypatch.setattr(cli,'ActionProvider',forbidden)
    assert cli.run(Path('fixture'),provider='deepseek')=={'status':'validated_not_sent','new_model_calls':0}


def test_live_requires_both_environment_and_a_specific_approval_path(monkeypatch):
    monkeypatch.setattr(cli,'check',lambda path:({}, {}))
    monkeypatch.delenv('GRAPHRISK_UPGRADE_LIVE',raising=False)
    with pytest.raises(ValueError,match='concrete outbound approval'):
        cli.run(Path('fixture'),provider='deepseek',live=True,approval=Path('fixture'))
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE','1')
    with pytest.raises(ValueError,match='concrete outbound approval'):
        cli.run(Path('fixture'),provider='deepseek',live=True)


def test_results_cannot_be_overwritten_with_a_new_attempt(tmp_path):
    path=tmp_path/'result.json'
    cli.write(path,{'model_calls':1,'report':None})
    before=path.read_bytes()
    cli.write(path,{'model_calls':1,'report':None})
    with pytest.raises(ValueError,match='retain and review'):
        cli.write(path,{'model_calls':2,'report':{}})
    assert path.read_bytes()==before
