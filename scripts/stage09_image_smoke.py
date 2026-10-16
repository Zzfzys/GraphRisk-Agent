"""Execute inside the newly built image, with --network none."""
from pathlib import Path
import sys
import graphrisk
from api.main import create_app

assert sys.version_info[:2] == (3, 11)
assert Path(graphrisk.__file__).resolve().is_relative_to(Path('/app/.venv'))
assert callable(create_app)
for name in ('.secrets', '.env', 'data/raw', 'data/interim', 'data/processed', 'artifacts', 'reports', 'tests'):
    assert not (Path('/app')/name).exists(), name
assert (Path('/app/data/policies/stage05/device-sharing-v2.md')).is_file()
print('image imports and private-data boundary passed; no investigation/model calls')
