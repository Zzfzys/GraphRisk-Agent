"""The default review command cannot register or send a model request."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

import run_daily_report_review as cli


def test_default_check_leaves_live_ledger_bytes_unchanged(tmp_path, monkeypatch):
    path=tmp_path/'reports/generated/agent-investigation-upgrade/live-budget.sqlite3'
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE model_attempts(provider TEXT)')
        db.execute("INSERT INTO model_attempts VALUES ('deepseek')")
    before=path.read_bytes()
    cfg={'version':'route5-daily-report-successor-v1','runtime':str(tmp_path),
         'payloads':['a']*6,'pricing_required':False}
    monkeypatch.setattr(cli.successor,'read_contract',lambda path:cfg)
    monkeypatch.setattr(cli.successor,'check_history',lambda db,cfg:None)
    monkeypatch.setattr(cli.successor,'check_allocation',lambda db,cfg,used:None)
    monkeypatch.setattr(cli,'InvestigationBudget',lambda *args,**kwargs:pytest.fail('must stay read-only'))
    result=cli.run(Path('local-config.json'))
    assert result['status']=='validated_not_sent' and result['paid_calls']==0
    assert path.read_bytes()==before


def test_live_requires_specific_outbound_approval_before_opening_budget(monkeypatch):
    monkeypatch.setattr(cli,'check',lambda path:({'pricing_required':False},(1364,1387)))
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE','1')
    monkeypatch.setattr(cli,'InvestigationBudget',lambda *args,**kwargs:pytest.fail('no authorization'))
    with pytest.raises(ValueError,match='specific outbound approval'):
        cli.run(Path('local-config.json'),live=True)


def test_registered_closed_batch_is_read_without_registering_again(tmp_path,monkeypatch):
    from graphrisk.agent.upgrade_contract import file_hash
    config=tmp_path/'config.json'
    config.write_text('{}')
    path=tmp_path/'ledger.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE rank_successors(campaign_id,contract_path,contract_sha,authorization,closed)')
        db.execute('INSERT INTO rank_successors VALUES(?,?,?,?,1)',
            ('same-campaign',str(config),file_hash(config),'Specific six-case authorization'))
    class Budget:
        @contextmanager
        def transaction(self):
            with sqlite3.connect(path) as db:yield db
    monkeypatch.setattr(cli.successor,'register',lambda *a:pytest.fail('must not register again'))
    actual=cli.register_once(Budget(),config,{'campaign':'same-campaign'},'Specific six-case authorization')
    assert actual[3]==1
    config.write_text('{"changed":true}')
    with pytest.raises(ValueError,match='binding changed'):
        cli.register_once(Budget(),config,{'campaign':'same-campaign'},'Specific six-case authorization')
