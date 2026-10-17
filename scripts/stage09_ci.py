"""Stage09 regression layers. No real-data training, Ollama or paid API calls.

Run through the locked Python 3.11 uv environment. Output directories are unique;
all logs/temporary files go to the selected output root, never the source tree.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import site
import shutil
import subprocess
import sys
import time
import tomllib
from urllib.parse import quote
from uuid import uuid4
import xml.etree.ElementTree as ET
import zipfile

SOURCE = Path(__file__).resolve().parents[1]
DATABASE_FILES = (
    "tests/integration/test_stage07_postgres.py",
    "tests/integration/test_stage07_pgvector.py",
    "tests/integration/test_stage09_quality_postgres.py",
)
DB_IMAGE = "pgvector/pgvector:0.8.6-pg17-bookworm"


def source_inventory(source):
    files = {}
    for folder in ("src", "api", "scripts", "tests", "configs", "data/policies", "data/evaluation",
                   ".github", "ci", "projects/enterprise-graphrag"):
        for path in (source/folder).rglob("*"):
            if (path.is_file() and path.suffix in {".py", ".toml", ".lock", ".yaml", ".yml", ".json", ".md"}
                    and not any(p in {".venv", ".uv-cache", "__pycache__", "generated"} for p in path.parts)):
                files[path.relative_to(source).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ("pyproject.toml", "uv.lock", "Dockerfile", ".dockerignore", ".gitattributes"):
        files[name] = hashlib.sha256((source/name).read_bytes()).hexdigest()
    return dict(sorted(files.items()))


def child_env(source, output):
    env = os.environ.copy()
    for key in list(env):
        if ("DSN" in key.upper() or key.upper().endswith("API_KEY")
                or key.startswith(("GRAPHRISK_", "GRAPHRAG_"))):
            env.pop(key, None)
    temp = output / "tmp"
    temp.mkdir(parents=True, exist_ok=True)
    env.update(PYTHONPATH=os.pathsep.join(str(source/p) for p in ("src", "", "scripts")),
               PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
               PYTEST_ADDOPTS="", CUDA_VISIBLE_DEVICES="-1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1",
               OPENBLAS_NUM_THREADS="1", TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp),
               HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", DO_NOT_TRACK="1",
               STREAMLIT_BROWSER_GATHER_USAGE_STATS="false")
    return env


def absolute_interpreter(path):
    """Anchor a CLI path before changing cwd, preserving the virtualenv symlink."""
    return Path(os.path.abspath(path))


def junit_counts(path):
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    failed = sum(c.find("failure") is not None for c in cases)
    errors = sum(c.find("error") is not None for c in cases)
    skipped = sum(c.find("skipped") is not None for c in cases)
    return dict(total=len(cases), passed=len(cases)-failed-errors-skipped,
                failed=failed, errors=errors, skipped=skipped,
                skip_reasons=[c.find("skipped").get("message", "") for c in cases if c.find("skipped") is not None])


def require_tests(counts, *, minimum=1, allow_skips=False):
    if counts["passed"] < minimum or counts["failed"] or counts["errors"] or (counts["skipped"] and not allow_skips):
        raise ValueError("test layer failed or required cases were not executed")


def check_wheel(path):
    with zipfile.ZipFile(path) as wheel:
        members = wheel.namelist()
    expected = {"graphrisk/__init__.py", "api/main.py"} if path.name.startswith("graphrisk_agent-") else {"enterprise_graphrag/__init__.py"}
    if not expected.issubset(members):
        raise ValueError("wheel is missing expected importable packages")
    for name in members:
        parts = PurePosixPath(name).parts
        if (not parts or ".." in parts or name.startswith("/") or
                parts[0] not in {"graphrisk", "api", "enterprise_graphrag"}
                and not parts[0].endswith(".dist-info")):
            raise ValueError("unexpected or private wheel member")
        if not parts[0].endswith(".dist-info") and not name.endswith((".py", "/")):
            raise ValueError("non-source payload in wheel")
    return dict(file=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(), members=len(members))


def run(args, *, cwd, env, log, timeout=600):
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run([str(a) for a in args], cwd=cwd, env=env, stdout=stream,
                                stderr=subprocess.STDOUT, timeout=timeout)
    if result.returncode:
        raise RuntimeError("command failed; inspect local log " + log.name)


def pytest_layer(source, output, env, paths, *, python=None, port=0, ignore=()):
    args = [python or sys.executable, "-m", "pytest", "-q", "-p", "stage09_ci_pytest",
            "--rootdir", source, "--basetemp", output/"pytest-tmp", "--junitxml", output/"junit.xml"]
    if str(python or sys.executable) == sys.executable:
        args += ["-p", "anyio.pytest_plugin"]
    if port:
        args += ["--ci-db-port", str(port)]
    args += [str(source/p) for p in paths]
    args += ["--ignore=" + str(source/p) for p in ignore]
    run(args, cwd=source, env=env, log=output/"pytest.txt")
    return junit_counts(output/"junit.xml")


def database_layer(source, output, env):
    """Create only our own short-lived container/database; never accept a user's DSN."""
    import psycopg
    from psycopg import sql
    from graphrisk.serving.postgres_store import PostgresStore
    from graphrisk.agent.pgvector_retrieval import migrate_policy_vectors
    from graphrisk.serving.postgres_roles import grant_stage07_app
    name = "graphrisk-ci-" + uuid4().hex[:12]
    password = secrets.token_urlsafe(32)
    private = output/".secrets"
    private.mkdir()
    secret = private/"postgres-password"
    secret.write_text(password, encoding="utf-8")
    secret.chmod(0o600)
    command = ["docker", "run", "-d", "--name", name, "--memory", "384m",
               "--mount", f"type=bind,source={secret.resolve()},target=/run/secrets/password,readonly",
               "-e", "POSTGRES_PASSWORD_FILE=/run/secrets/password", "-e", "POSTGRES_USER=ci_admin",
               "-p", "127.0.0.1::5432", DB_IMAGE]
    created = False
    try:
        run(command, cwd=output, env=env, log=output/"docker-start.txt", timeout=180)
        created = True
        port_text = subprocess.check_output(["docker", "port", name, "5432"], text=True, env=env)
        port = int(port_text.strip().rsplit(":", 1)[1])
        admin_base = f"postgresql://ci_admin:{quote(password)}@127.0.0.1:{port}/"
        deadline = time.monotonic()+90
        while True:
            try:
                with psycopg.connect(admin_base+"postgres", connect_timeout=2, autocommit=True) as conn:
                    dbname = "graphrisk_ci_"+uuid4().hex[:12]
                    conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
                break
            except psycopg.OperationalError:
                if time.monotonic() > deadline:
                    raise RuntimeError("isolated database did not become ready") from None
                time.sleep(1)
        admin = admin_base+dbname
        app = f"postgresql://ci_app:{quote(secrets.token_urlsafe(32))}@127.0.0.1:{port}/{dbname}"
        PostgresStore(admin).migrate()
        migrate_policy_vectors(admin)
        grant_stage07_app(admin, app)
        with psycopg.connect(app) as conn:
            superuser = conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user").fetchone()[0]
            if superuser:
                raise ValueError("application role must not be superuser")
        db_env = env | {"GRAPHRISK_STAGE07_DSN": app, "GRAPHRISK_STAGE07_MIGRATION_DSN": admin,
                        "GRAPHRISK_STAGE07_EVAL_ISOLATED": "1"}
        # Legacy tests deliberately call migrate() in their fixture, so they use
        # the isolated migration account. The 15 probes below use the limited role.
        counts = pytest_layer(source, output, db_env | {"GRAPHRISK_STAGE07_DSN": admin}, DATABASE_FILES, port=port)
        require_tests(counts, minimum=24)
        run([sys.executable, source/"scripts/stage09_ci_probes.py", "--source", source,
             "--output", output/"probes"], cwd=source, env=db_env, log=output/"probes.txt", timeout=180)
        probes = json.loads((output/"probes/summary.json").read_text(encoding="utf-8"))
        if probes["passed"] != 15 or probes["not_run"] or probes["failed"]:
            raise ValueError("15 CI mechanism probes must actually pass")
        return dict(tests=counts, probes=probes, limited_app_role=True, isolated_database=True,
                    embedding_provider="deterministic_ci_fixture", real_model_calls=0,
                    legacy_tests_account="isolated_migration", probes_account="limited_application")
    finally:
        if created:
            # Exact randomly named container created in this call; no old stack/volume is touched.
            run(["docker", "rm", "-f", "-v", name], cwd=output, env=env, log=output/"docker-cleanup.txt", timeout=60)
        secret.unlink(missing_ok=True)


def git_frozen_roundtrip(source, output, env):
    repository = output/"git-roundtrip"
    repository.mkdir()
    shutil.copyfile(source/".gitattributes", repository/".gitattributes")
    files = []
    for folder in ("data/evaluation", "data/policies", "tests/fixtures", "projects/enterprise-graphrag/data"):
        for path in (source/folder).rglob("*"):
            if path.is_file():
                relative = path.relative_to(source)
                dest = repository/relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, dest)
                files.append(relative)
    run(["git", "init", "--quiet", repository], cwd=output, env=env, log=output/"git-init.txt")
    run(["git", "-c", "core.autocrlf=true", "add", "--", "."], cwd=repository, env=env, log=output/"git-add.txt")
    exported = output/"git-export"
    exported.mkdir()
    run(["git", "-c", "core.autocrlf=false", "checkout-index", "--all", "--prefix="+exported.as_posix()+"/"],
        cwd=repository, env=env, log=output/"git-export.txt")
    for relative in files:
        if (source/relative).read_bytes() != (exported/relative).read_bytes():
            raise ValueError("Git checkout changed frozen contract bytes")
    return len(files)


def static_layer(source):
    count = 0
    for folder in ("src", "api", "scripts", "tests", "projects/enterprise-graphrag/src"):
        for path in (source/folder).rglob("*.py"):
            if any(p in {".venv", "__pycache__"} for p in path.parts):
                continue
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            count += 1
    for path in ("pyproject.toml", "projects/enterprise-graphrag/pyproject.toml",
                 "projects/enterprise-graphrag/demo/pyproject.toml"):
        value = tomllib.loads((source/path).read_text(encoding="utf-8"))
        if value["project"]["requires-python"] != ">=3.11,<3.12":
            raise ValueError("Python 3.11 project contract changed")
    return dict(parsed_python_files=count, lint="not_configured_no_new_tool")


def package_layer(source, output, env):
    wheels = []
    for n, folder in enumerate((source, source/"projects/enterprise-graphrag")):
        dest = output/str(n)
        run(["uv", "build", "--offline", "--wheel", "--out-dir", dest, folder],
            cwd=output, env=env, log=output/f"build-{n}.txt", timeout=180)
        wheel = next(dest.glob("*.whl"))
        wheels.append(check_wheel(wheel))
        # Install to a fresh target and import away from the checkout, using runtime's locked dependencies.
        target = output/f"installed-{n}"
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(target)
        module = "graphrisk" if n == 0 else "enterprise_graphrag"
        code = ("import importlib,pathlib; m=importlib.import_module('"+module+"'); "
                "assert pathlib.Path(m.__file__).resolve().is_relative_to(pathlib.Path("+repr(str(target))+"))")
        run([sys.executable, "-c", code], cwd=output, env=env | {"PYTHONPATH": str(target)},
            log=output/f"installed-import-{n}.txt")
    return dict(wheels=wheels, source_imports_not_used=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("layer", choices=("static", "cpu", "database", "graphrag-core", "graphrag-demo", "package"))
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--python", type=Path, help="independent GraphRAG core or demo interpreter")
    args = parser.parse_args()
    if args.python:
        args.python = absolute_interpreter(args.python)
    source = args.source.resolve()
    output = args.output_root.resolve()/args.layer
    output.mkdir(parents=True, exist_ok=False)
    env = child_env(source, output)
    inventory = source_inventory(source)
    (output/"source-inputs.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    summary = dict(layer=args.layer, status="failed", real_model_calls=0, paid_calls=0,
                   real_dataset_fits=0, execution_context="github_actions" if os.getenv("GITHUB_ACTIONS") == "true" else "local")
    summary["source_sha256"] = hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()
    try:
        if sys.version_info[:2] != (3, 11):
            raise ValueError("Python 3.11 is required")
        if args.layer == "static":
            detail = static_layer(source)
            detail["frozen_git_roundtrip_files"] = git_frozen_roundtrip(source, output, env)
        elif args.layer == "cpu":
            counts = pytest_layer(source, output, env, ("tests",), ignore=DATABASE_FILES)
            require_tests(counts, minimum=100, allow_skips=True)
            # Only documented platform limitations may skip; missing DB was explicitly deselected.
            allowed = ("OS does not permit symlink creation", "E-drive production guard covered on Windows execution root")
            if any(not any(s in reason for s in allowed) for reason in counts["skip_reasons"]):
                raise ValueError("unexpected CPU skip; inspect junit.xml")
            detail = dict(tests=counts, database_files_deselected=list(DATABASE_FILES), gpu_visible=False)
        elif args.layer == "database":
            detail = database_layer(source, output, env)
        elif args.layer.startswith("graphrag-"):
            project = source/"projects/enterprise-graphrag"
            targets = [project/"tests"] if args.layer.endswith("core") else [project/"tests/test_streamlit_app.py"]
            ignore = [project/"tests/test_streamlit_app.py"] if args.layer.endswith("core") else []
            independent_env = env | {"PYTHONPATH": os.pathsep.join([str(project/"src"), str(source/"scripts"), *site.getsitepackages()])}
            if not args.python:
                raise ValueError("--python must select the independent core/demo interpreter")
            code = ("import importlib.util,pathlib,enterprise_graphrag; "
                    "assert importlib.util.find_spec('torch') is None; "
                    "assert importlib.util.find_spec('graphrisk') is None; "
                    "assert pathlib.Path(enterprise_graphrag.__file__).resolve().is_relative_to(pathlib.Path("
                    + repr(str(project/"src")) + ")); "
                    + ("assert importlib.util.find_spec('streamlit') is None" if args.layer.endswith("core")
                       else "import streamlit"))
            run([args.python, "-c", code], cwd=output, env=independent_env, log=output/"independent-runtime.txt")
            counts = pytest_layer(source, output, independent_env, targets, python=str(args.python) if args.python else None,
                                  ignore=ignore)
            require_tests(counts)
            detail = dict(tests=counts, independent_project=True)
        else:
            detail = package_layer(source, output, env)
        if source_inventory(source) != inventory:
            raise ValueError("source changed during this layer; preserve output and rerun in a new directory")
        summary.update(status="passed", detail=detail)
    except Exception as exc:
        summary["error_type"] = type(exc).__name__
        raise
    finally:
        (output/"public-summary.json").write_text(json.dumps(summary, ensure_ascii=True, indent=2)+"\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
