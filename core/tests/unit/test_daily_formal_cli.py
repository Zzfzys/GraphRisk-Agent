"""CLI wiring only: no server, real artifact loading or paid provider."""
import sys
from pathlib import Path

import pytest

import serve_champion_daily as cli
from graphrisk.agent import public_references, reference_answers


def invoke(monkeypatch, tmp_path, *extra):
    monkeypatch.setattr(sys, 'argv', ['serve_champion_daily', '--runtime', str(tmp_path),
        '--available-through-second', '172802', '--history-cutoff-second', '0',
        '--policy-as-of', '2026-11-13', *extra])
    return cli.main()


def test_case_reference_cli_uses_explicit_manifest_and_pin_without_a_provider(monkeypatch, tmp_path):
    calls = []
    manifest = tmp_path/'manifest.json'
    monkeypatch.setattr(public_references, 'PublicReferenceRetriever',
        lambda path, version: calls.append((path, version)) or 'fixture-retriever')
    monkeypatch.setattr(reference_answers, 'ReferenceAnswerService',
        lambda retriever: ('fixture-service', retriever))
    def services(runtime, **kwargs):
        assert runtime == tmp_path
        assert kwargs['case_reference_service'] == ('fixture-service', 'fixture-retriever')
        assert kwargs['reference_cache'] is None
        assert 'narrative_backend' not in kwargs  # The CLI grants no paid authorization.
        return 'fixture-risk', 'fixture-conversations'
    monkeypatch.setattr(cli, 'build_new_services', services)
    monkeypatch.setattr(cli, 'create_app', lambda **kwargs: 'fixture-app')
    monkeypatch.setattr(cli.uvicorn, 'run', lambda *args, **kwargs: None)
    invoke(monkeypatch, tmp_path, '--public-reference-manifest', str(manifest),
           '--public-reference-version', 'pinned-fixture-version')
    assert calls == [(manifest, 'pinned-fixture-version')]


@pytest.mark.parametrize('extra', [
    ['--public-reference-manifest', 'manifest.json'],
    ['--public-reference-version', 'pin'],
    ['--public-reference-manifest', 'manifest.json', '--public-reference-version', 'pin',
     '--public-reference-cache', 'cache.json'],
    ['--mode', 'historical', '--public-reference-manifest', 'manifest.json',
     '--public-reference-version', 'pin'],
])
def test_invalid_case_reference_configuration_stops_before_build_or_server(monkeypatch, tmp_path, extra):
    monkeypatch.setattr(cli, 'build_new_services', lambda *a, **k: pytest.fail('invalid config'))
    monkeypatch.setattr(cli, 'build_service', lambda *a, **k: pytest.fail('invalid config'))
    monkeypatch.setattr(cli.uvicorn, 'run', lambda *a, **k: pytest.fail('invalid config'))
    with pytest.raises(SystemExit) as stopped:
        invoke(monkeypatch, tmp_path, *extra)
    assert stopped.value.code == 2
