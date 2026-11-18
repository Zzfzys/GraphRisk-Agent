"""Daily XGBoost evidence must not initialize the unused GNN runtime."""
import os
import subprocess
import sys


def test_daily_only_imports_do_not_load_torch():
    result=subprocess.run([sys.executable,'-c',
        'import sys; from graphrisk.serving.daily_batch_store import DailyBatchStore; '
        'from graphrisk.serving.daily_investigation_tools import DailyInvestigationTools; '
        'assert "torch" not in sys.modules; assert "torch_geometric" not in sys.modules'],
        env=os.environ.copy(),capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stderr
