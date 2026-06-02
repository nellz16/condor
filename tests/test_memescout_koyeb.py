import json
import logging
import urllib.request

from condor.memescout_ai.health import start_health_server, status_payload, stop_health_server
from condor.memescout_ai.koyeb import ensure_data_dir, log_memory_usage, startup_resource_checks
from condor.memescout_ai.store import MemeScoutStore


def test_healthz_returns_200(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMESCOUT_DB_PATH", str(tmp_path / "memescout.sqlite"))
    server = start_health_server(port=0)
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as resp:
            assert resp.status == 200
            assert resp.read() == b"OK\n"
    finally:
        stop_health_server()


def test_status_does_not_expose_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMESCOUT_DB_PATH", str(tmp_path / "memescout.sqlite"))
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-secret")
    store = MemeScoutStore(tmp_path / "memescout.sqlite")
    payload = status_payload(store)
    encoded = json.dumps(payload)
    assert payload["app"] == "condor-memescout"
    assert payload["paper_only"] is True
    assert "secret" not in encoded
    assert "api_key" not in encoded.lower()


def test_koyeb_free_mode_blocks_real_trading_integrations_in_main_source():
    source = open("main.py", encoding="utf-8").read()
    koyeb_branch = source[source.index("if koyeb_free_mode():") : source.index("# Import fresh versions after reload")]
    assert "handlers.trading" not in koyeb_branch
    assert "unified_trade" not in koyeb_branch
    assert "gateway" not in koyeb_branch.lower()
    assert "memescout" in koyeb_branch


def test_data_directory_is_created(tmp_path):
    db_path = tmp_path / "nested" / "memescout.sqlite"
    data_dir = ensure_data_dir(db_path)
    assert data_dir.exists()
    assert data_dir == db_path.parent


def test_local_sqlite_warning_appears_in_koyeb_free_mode(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("KOYEB_FREE_MODE", "true")
    monkeypatch.setenv("MEMESCOUT_DB_PATH", str(tmp_path / "memescout.sqlite"))
    caplog.set_level(logging.WARNING)
    startup_resource_checks()
    assert "Koyeb Free has no persistent volume" in caplog.text


def test_health_server_starts_without_telegram_network_calls(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMESCOUT_DB_PATH", str(tmp_path / "memescout.sqlite"))
    server = start_health_server(port=0)
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=2) as resp:
            payload = json.loads(resp.read().decode())
        assert payload["app"] == "condor-memescout"
        assert "telegram" not in payload
    finally:
        stop_health_server()


def test_memory_logging_failure_does_not_crash(monkeypatch):
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError("no psutil")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    log_memory_usage()
