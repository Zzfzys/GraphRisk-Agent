"""Start the one-worker Stage 06 API and Streamlit workbench on Windows."""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from graphrisk.agent.config import Stage05Config, load_stage05_config


ROOT = Path(__file__).resolve().parents[1]
API_URL = "http://127.0.0.1:8000"
UI_URL = "http://127.0.0.1:8501"


def _port_available(port: int) -> bool:
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _check_ollama(config: Stage05Config) -> None:
    generation = config.generation
    if generation.provider != "local_http" or not generation.endpoint:
        raise ValueError("local launcher requires a loopback Ollama endpoint")
    tags_url = generation.endpoint.removesuffix("/generate") + "/tags"
    try:
        with httpx.Client(timeout=5.0) as client:
            models = client.get(tags_url).raise_for_status().json().get("models", [])
    except (httpx.HTTPError, ValueError, AttributeError, TypeError) as error:
        raise RuntimeError("Ollama is unavailable at the configured loopback endpoint") from error
    if not isinstance(models, list) or not any(
        isinstance(item, dict) and item.get("name") == generation.model_id
        and item.get("digest") == generation.model_revision for item in models
    ):
        raise RuntimeError("Ollama model name or pinned digest does not match the config")


def _wait_ready(process: subprocess.Popen, url: str, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"service exited before becoming ready: {url}")
        try:
            with httpx.Client(timeout=3.0) as client:
                response = client.get(url)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise TimeoutError(f"service did not become ready: {url}")


def _stop(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Stage 06 local workbench")
    parser.add_argument("--config", default="configs/stage05.yaml")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 11):
        parser.error("Stage 06 requires Python 3.11")
    os.chdir(ROOT)
    config = load_stage05_config(args.config)
    _check_ollama(config)
    for port in (8000, 8501):
        if not _port_available(port):
            raise RuntimeError(f"127.0.0.1:{port} is already in use; stop the prior demo first")

    logs = ROOT / "reports" / "generated" / "stage06-local"
    logs.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["GRAPHRISK_STAGE06_CONFIG"] = str(Path(args.config).resolve())
    env["GRAPHRISK_STAGE06_API_URL"] = API_URL
    env["GRAPHRISK_MODEL_DIR"] = str(ROOT / "artifacts" / "stage-02" / "ieee-cis" / "champion")
    env["GRAPHRISK_FEATURE_DATA"] = str(ROOT / "data" / "interim" / "ieee-cis" / "features_all.parquet")
    env["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    api = ui = None
    try:
        with (logs / "api.log").open("wb") as api_log, (logs / "ui.log").open("wb") as ui_log:
            api = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1",
                 "--port", "8000", "--workers", "1"], cwd=ROOT, env=env,
                stdout=api_log, stderr=subprocess.STDOUT, creationflags=flags,
            )
            _wait_ready(api, f"{API_URL}/api/v1/ready", timeout=120)
            ui = subprocess.Popen(
                [sys.executable, "-m", "streamlit", "run", "streamlit_app.py",
                 "--server.address", "127.0.0.1", "--server.port", "8501",
                 "--server.headless", "true", "--browser.gatherUsageStats", "false"],
                cwd=ROOT, env=env, stdout=ui_log, stderr=subprocess.STDOUT,
                creationflags=flags,
            )
            _wait_ready(ui, f"{UI_URL}/_stcore/health", timeout=60)
            print(f"Stage 06 ready: {UI_URL} (API: {API_URL}). Press Ctrl+C to stop.", flush=True)
            while api.poll() is None and ui.poll() is None:
                time.sleep(1)
            raise RuntimeError("API or workbench exited; inspect reports/generated/stage06-local/*.log")
    except KeyboardInterrupt:
        print("Stopping Stage 06 local services.", flush=True)
        return 0
    finally:
        _stop(ui)
        _stop(api)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, TimeoutError, ValueError) as error:
        print(f"Stage 06 startup failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
