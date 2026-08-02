"""Regressões HTTP de alto valor, sem banco ou scheduler de produção."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Os módulos importam estes caminhos como constantes; isolar antes de criar
    # a app impede qualquer leitura/escrita no history.sqlite3 operacional.
    from radar_v2.app.repositories import storage
    from radar_v2.app.services import preflight_service, run_service

    data_dir = tmp_path / "web_app"
    monkeypatch.setenv("RADAR_V2_SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("RADAR_V2_SECRET_KEY", "test-secret")
    monkeypatch.setattr(storage, "APP_DATA_DIR", data_dir)
    monkeypatch.setattr(storage, "DB_PATH", data_dir / "history.sqlite3")
    monkeypatch.setattr(storage, "LEGACY_DB", tmp_path / "legacy.sqlite3")
    monkeypatch.setattr(run_service, "APP_DATA_DIR", data_dir)
    monkeypatch.setattr(run_service, "RUN_LOG_DIR", data_dir / "run_logs")
    monkeypatch.setattr(preflight_service, "PROJECT_ROOT", tmp_path)

    from radar_v2.app.api.server import REACT_DIST, create_app

    app = create_app()
    app.config.update(TESTING=True)
    app.extensions["test_react_asset"] = next((REACT_DIST / "assets").glob("index-*.js")).name
    return app.test_client()


def test_public_shell_and_assets(client):
    assert client.get("/health").status_code == 200
    assert client.get("/login").status_code == 200
    asset = client.application.extensions["test_react_asset"]
    response = client.get(f"/assets/{asset}")
    assert response.status_code == 200
    assert "javascript" in response.content_type


@pytest.mark.parametrize("path", ["/api/tasks", "/api/preflight", "/api/runs/history", "/api/schedules"])
def test_private_api_requires_session(client, path):
    assert client.get(path).status_code == 401


def test_public_session_and_calendar_without_metrics(client):
    assert client.get("/api/session").get_json()["authenticated"] is False
    login = client.post("/login", data={"username": "teste", "password": "teste"})
    assert login.status_code in {302, 303}
    payload = client.get(
        "/api/calendar/summary?start=2026-08-01&end=2026-08-31"
    ).get_json()
    assert payload["has_metrics"] is False
