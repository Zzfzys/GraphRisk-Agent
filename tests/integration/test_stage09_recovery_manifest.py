"""Read-only recovery fingerprints and rolled-back mutations in the CI-owned DB."""
import os
import re
import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
import pytest
from graphrisk.monitoring.recovery import logical_manifest, require_restored_equal

DSN = os.getenv("GRAPHRISK_STAGE07_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="isolated PostgreSQL DSN required")


def _connect(**kwargs):
    parts = conninfo_to_dict(DSN)
    if (os.getenv("GRAPHRISK_STAGE07_EVAL_ISOLATED") != "1"
        or parts.get("host") != "127.0.0.1"
        or not re.fullmatch(r"graphrisk_(?:ci|r7)_[a-f0-9]+(?:_source|_restore)?", parts.get("dbname", ""))):
        raise ValueError("only the harness-owned isolated database is accepted")
    return psycopg.connect(DSN, **kwargs)


def test_logical_hash_preserves_exact_numeric_values_and_rollback():
    with _connect() as conn:
        initial = logical_manifest(conn)
        conn.execute("INSERT INTO cost_budget(singleton,spent_cny,stop_cny,budget_cny) "
                     "VALUES(true,999999999999.999998,24,30) ON CONFLICT(singleton) "
                     "DO UPDATE SET spent_cny=excluded.spent_cny")
        first = logical_manifest(conn)
        conn.execute("UPDATE cost_budget SET spent_cny=999999999999.999999")
        second = logical_manifest(conn)
        assert first["tables"]["cost_budget"]["sha256"] != second["tables"]["cost_budget"]["sha256"]
        with pytest.raises(ValueError, match="differs"):
            require_restored_equal(first, second)
        conn.rollback()
        assert logical_manifest(conn) == initial


def test_empty_table_schema_and_row_factory_are_covered():
    with _connect(row_factory=dict_row) as conn:
        initial = logical_manifest(conn)
        conn.execute("ALTER TABLE checkpoint_writes ADD COLUMN route7_probe_column text")
        changed = logical_manifest(conn)
        assert initial["schema_sha256"] != changed["schema_sha256"]
        conn.rollback()
        assert require_restored_equal(initial, logical_manifest(conn))["status"] == "passed"
