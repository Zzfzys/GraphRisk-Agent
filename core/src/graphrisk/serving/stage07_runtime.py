"""Container entry points for the isolated Stage 07 Compose topology.

Secrets are mounted files; neither DSN nor password is printed or stored in image metadata.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import quote

from graphrisk.agent.config import load_stage05_config
from graphrisk.agent.pgvector_retrieval import import_policy_vectors, migrate_policy_vectors
from graphrisk.agent.policy_retrieval import load_policy_index
from graphrisk.serving.postgres_roles import grant_stage07_app
from graphrisk.serving.postgres_store import PostgresStore


CONFIG = Path("configs/stage07_compose.yaml")
ADMIN_SECRET = Path("/run/secrets/stage07_db_admin_password")
APP_SECRET = Path("/run/secrets/stage07_db_app_password")


def _dsn(role: str, secret_path: Path) -> str:
    password = secret_path.read_text(encoding="utf-8").strip()
    if not password:
        raise ValueError("Stage 07 database secret must not be empty")
    return f"postgresql://{role}:{quote(password, safe='')}@db:5432/graphrisk"


def bootstrap() -> None:
    admin_dsn = _dsn("graphrisk", ADMIN_SECRET)
    app_dsn = _dsn("graphrisk_app", APP_SECRET)
    config = load_stage05_config(CONFIG)
    if config.retrieval.backend != "pgvector" or config.generation.provider != "host_ollama":
        raise ValueError("Stage 07 container config must pin pgvector and host Ollama")
    index_path = config.resolve_path(config.paths.policy_artifact_dir) / "policy-index-local_embedding.json"
    index = load_policy_index(index_path)
    if (index.model_id != config.retrieval.embedding_model_id
            or index.model_revision != config.retrieval.embedding_model_revision):
        raise ValueError("Pinned policy index does not match Stage 07 container config")
    store = PostgresStore(admin_dsn)
    store.migrate()
    migrate_policy_vectors(admin_dsn)
    count = import_policy_vectors(admin_dsn, index)
    grant_stage07_app(admin_dsn, app_dsn)
    if not PostgresStore(app_dsn).check():
        raise RuntimeError("Limited Stage 07 application role cannot read the migrated schema")
    print(f"Stage 07 schema and policy vectors ready: chunks={count}, index={index.index_version}")


def serve_api() -> None:
    app_dsn = _dsn("graphrisk_app", APP_SECRET)
    if not PostgresStore(app_dsn).check():
        raise RuntimeError("Stage 07 database is not ready")
    os.environ["GRAPHRISK_STAGE07_DSN"] = app_dsn
    os.environ["GRAPHRISK_STAGE07_STORAGE"] = "postgres"
    os.environ["GRAPHRISK_STAGE06_CONFIG"] = str(CONFIG)
    os.execvp("uvicorn", ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"])


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"bootstrap", "api"}:
        raise SystemExit("usage: python -m graphrisk.serving.stage07_runtime [bootstrap|api]")
    bootstrap() if sys.argv[1] == "bootstrap" else serve_api()


if __name__ == "__main__":
    main()
