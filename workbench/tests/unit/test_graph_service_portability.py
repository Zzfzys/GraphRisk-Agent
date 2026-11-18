"""Graph checkpoint path portability without weakening the artifact-root guard."""

from pathlib import Path

import pytest

from graphrisk.graph.service import _legacy_windows_checkpoint_path


def test_legacy_windows_checkpoint_rebinds_only_within_configured_root(tmp_path):
    root = (tmp_path / "artifacts" / "stage-04" / "ieee-cis").resolve()
    relative = Path("artifacts/stage-04/ieee-cis")
    legacy = r"E:\GraphRisk-Agent\artifacts\stage-04\ieee-cis\models\graphsage-v1\model.pt"
    assert _legacy_windows_checkpoint_path(legacy, root, relative) == (
        root / "models" / "graphsage-v1" / "model.pt"
    ).resolve()


@pytest.mark.parametrize("raw", [
    r"E:\GraphRisk-Agent\other\models\model.pt",
    r"E:\GraphRisk-Agent\artifacts\stage-04\ieee-cis\..\..\outside.pt",
    r"models\graphsage-v1\model.pt",
])
def test_legacy_windows_checkpoint_rejects_wrong_root_or_escape(tmp_path, raw):
    root = (tmp_path / "artifacts" / "stage-04" / "ieee-cis").resolve()
    with pytest.raises(ValueError):
        _legacy_windows_checkpoint_path(raw, root, Path("artifacts/stage-04/ieee-cis"))
