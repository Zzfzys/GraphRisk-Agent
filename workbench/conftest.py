"""Keep pytest temporary files separate for each local operating-system account."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    if config.option.basetemp is not None:
        return
    # On Windows, USERNAME/getpass.getuser() can be inherited from another
    # account by a sandbox. os.getlogin() identifies the actual process owner.
    account = os.getlogin() if os.name == "nt" else str(os.getuid())
    safe_account = re.sub(r"[^A-Za-z0-9._-]", "_", account).lower()
    config.option.basetemp = str(Path(config.rootpath) / "artifacts" / f"pytest-{safe_account}")
