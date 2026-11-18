"""Read-only logical recovery fingerprints; row payloads never leave this function."""
from __future__ import annotations

import hashlib
import json
from psycopg import sql
from psycopg.rows import tuple_row

TABLES = ("schema_migrations", "investigation_runs", "create_idempotency", "approval_events",
          "cost_budget", "cost_attempts", "checkpoint_migrations", "checkpoints", "checkpoint_blobs",
          "checkpoint_writes", "policy_vector_indexes", "policy_vector_chunks")


def logical_manifest(connection):
    """Hash canonical SQL JSON rows independently of row order, OIDs, owners or grants."""
    def rows(query):
        with connection.cursor(row_factory=tuple_row) as cursor:
            return cursor.execute(query).fetchall()
    found = [row[0] for row in rows(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
    if set(found) - set(TABLES):
        raise ValueError("unexpected table in isolated recovery database")
    required = set(TABLES) - {"policy_vector_indexes", "policy_vector_chunks"}
    if not required.issubset(found):
        raise ValueError("required recovery table is missing")
    tables = {}
    for table in found:
        query = sql.SQL("SELECT row_to_json(t)::text FROM public.{} AS t").format(sql.Identifier(table))
        # Keep PostgreSQL numeric text exact; parsing through float loses 1e-6
        # differences near the numeric(18,6) upper range.
        values = [row[0] for row in rows(query)]
        digest = hashlib.sha256()
        for row in sorted(values):
            data = row.encode("utf-8")
            digest.update(str(len(data)).encode("ascii") + b":" + data)
        tables[table] = {"rows": len(values), "sha256": digest.hexdigest()}
    extensions = [list(row) for row in rows("SELECT extname, extversion FROM pg_extension ORDER BY extname")]
    catalog = rows("SELECT table_name,column_name,ordinal_position,data_type,udt_name,is_nullable,column_default "
                   "FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position")
    constraints = rows("SELECT conrelid::regclass::text,conname,pg_get_constraintdef(oid) FROM pg_constraint "
                       "WHERE connamespace='public'::regnamespace ORDER BY 1,2")
    indexes = rows("SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY 1,2")
    schema_hash = hashlib.sha256(json.dumps([catalog, constraints, indexes], sort_keys=True).encode()).hexdigest()
    result = {"schema_version": "stage09-logical-recovery-v1", "tables": tables, "extensions": extensions,
              "schema_sha256": schema_hash}
    result["content_sha256"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


def require_restored_equal(source, restored):
    if source != restored:
        raise ValueError("restored database differs from frozen logical source manifest")
    return {"status": "passed", "tables": len(source["tables"]),
            "rows": sum(v["rows"] for v in source["tables"].values()),
            "logical_sha256": source["content_sha256"]}
