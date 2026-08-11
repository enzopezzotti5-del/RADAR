"""Regressões HTTP de alto valor, sem banco ou scheduler de produção."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import datetime as dt

import pytest


def _make_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, auth_enabled: bool):
    # Os módulos importam estes caminhos como constantes; isolar antes de criar
    # a app impede qualquer leitura/escrita no history.sqlite3 operacional.
    from radar_v2.app.repositories import storage
    from radar_v2.app.services import preflight_service, run_service

    data_dir = tmp_path / "web_app"
    monkeypatch.setenv("RADAR_V2_SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("RADAR_EMAIL_SYNC_ENABLED", "false")
    monkeypatch.setenv("RADAR_V2_SECRET_KEY", "test-secret")
    monkeypatch.setenv("RADAR_V2_AUTH_ENABLED", "true" if auth_enabled else "false")
    monkeypatch.setattr(storage, "APP_DATA_DIR", data_dir)
    monkeypatch.setattr(storage, "DB_PATH", data_dir / "history.sqlite3")
    monkeypatch.setattr(storage, "LEGACY_DB", tmp_path / "legacy.sqlite3")
    monkeypatch.setattr(run_service, "APP_DATA_DIR", data_dir)
    monkeypatch.setattr(run_service, "RUN_LOG_DIR", data_dir / "run_logs")
    monkeypatch.setattr(preflight_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(run_service, "RUN_METRICS_DIR", data_dir / "run_metrics")

    from radar_v2.app.api.server import REACT_DIST, create_app

    app = create_app()
    app.config.update(TESTING=True)
    app.extensions["test_react_asset"] = next((REACT_DIST / "assets").glob("index-*.js")).name
    return app.test_client()


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Default production posture: RADAR_V2_AUTH_ENABLED=false (login screen removed)."""
    return _make_client(monkeypatch, tmp_path, auth_enabled=False)


@pytest.fixture()
def client_auth_enabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Legacy/opt-in posture: RADAR_V2_AUTH_ENABLED=true restores session gating."""
    return _make_client(monkeypatch, tmp_path, auth_enabled=True)


def test_public_shell_and_assets(client):
    assert client.get("/health").status_code == 200
    assert client.get("/login").status_code in {302, 303}  # auth disabled: /login bounces home
    asset = client.application.extensions["test_react_asset"]
    response = client.get(f"/assets/{asset}")
    assert response.status_code == 200
    assert "javascript" in response.content_type


@pytest.mark.parametrize("path", ["/api/tasks", "/api/preflight", "/api/runs/history", "/api/schedules"])
def test_private_api_reachable_without_session_when_auth_disabled(client, path):
    """AUTH_ENABLED=false (default): API stays reachable with no session dependency."""
    assert client.get(path).status_code == 200


def test_public_session_reports_authenticated_when_auth_disabled(client):
    payload = client.get("/api/session").get_json()
    assert payload["authenticated"] is True
    assert payload["auth_enabled"] is False
    payload = client.get(
        "/api/calendar/summary?start=2026-08-01&end=2026-08-31"
    ).get_json()
    assert payload["has_metrics"] is False


def test_email_endpoints_reachable_and_empty_when_no_events_imported(client):
    summary = client.get("/api/email/summary").get_json()
    assert summary["ok"] is True
    assert summary["total_imported"] == 0
    history = client.get("/api/email/history").get_json()
    assert history["ok"] is True
    assert history["events"] == []
    status = client.get("/api/email/status").get_json()
    assert status["ok"] is True
    assert "scheduler" in status


@pytest.mark.parametrize("path", ["/api/tasks", "/api/preflight", "/api/runs/history", "/api/schedules"])
def test_private_api_requires_session_when_auth_enabled(client_auth_enabled, path):
    """Legacy behavior is preserved and testable via RADAR_V2_AUTH_ENABLED=true."""
    assert client_auth_enabled.get(path).status_code == 401


def test_public_session_and_login_flow_when_auth_enabled(client_auth_enabled):
    client = client_auth_enabled
    assert client.get("/login").status_code == 200
    payload = client.get("/api/session").get_json()
    assert payload["authenticated"] is False
    assert payload["auth_enabled"] is True
    login = client.post("/login", data={"username": "teste", "password": "teste"})
    assert login.status_code in {302, 303}
    assert client.get("/api/session").get_json()["authenticated"] is True


def _login(client):
    response = client.post("/login", data={"username": "teste", "password": "teste"})
    assert response.status_code in {302, 303}


def test_calendar_aggregates_metrics_and_keeps_zero_distinct_from_absence(client):
    from radar_v2.app.repositories.storage import upsert_invoice_metric

    common = dict(task_id="dl_enel_sp", metric_date="2026-08-04", metrics_complete=True,
                  source="test", started_at="2026-08-04 08:00:00",
                  finished_at="2026-08-04 08:01:00", updated_at="2026-08-04 08:01:00",
                  details_json="{}")
    upsert_invoice_metric(run_id=10, utility="ENEL", downloaded=0, processed=0, errors=0, **common)
    upsert_invoice_metric(run_id=11, utility="CELESC", downloaded=5, processed=4, errors=1, **common)
    _login(client)
    payload = client.get("/api/calendar/summary?start=2026-08-01&end=2026-08-31").get_json()
    day = next(item for item in payload["days"] if item["date"] == "2026-08-04")
    assert payload["has_metrics"] is True
    assert day["has_metrics"] is True
    assert (day["downloaded"], day["processed"], day["errors"]) == (5, 4, 1)
    assert day["utilities"] == ["CELESC", "ENEL"]
    # dates without data are absent from the sparse days list
    assert not any(item["date"] == "2026-08-05" for item in payload["days"])


def test_metric_upsert_is_idempotent(client):
    from radar_v2.app.repositories.storage import upsert_invoice_metric

    common = dict(run_id=42, task_id="dl_copel_bt", utility="COPEL", metric_date="2026-08-10",
                  metrics_complete=True, source="test", started_at="2026-08-10 08:00:00",
                  finished_at="2026-08-10 08:01:00", updated_at="2026-08-10 08:01:00", details_json="{}")
    upsert_invoice_metric(downloaded=2, processed=2, errors=0, **common)
    upsert_invoice_metric(downloaded=3, processed=2, errors=1, **common)
    _login(client)
    payload = client.get("/api/calendar/summary?start=2026-08-10&end=2026-08-10").get_json()
    assert payload["days"][0]["downloaded"] == 3
    assert payload["days"][0]["run_count"] == 1


def test_new_metric_path_reaches_sqlite_and_api(client):
    """RADAR_METRIC stdout path: items persist idempotently and appear in the calendar API."""
    from radar_v2.app.repositories.storage import (
        create_run, initialize_run_metrics, set_run_metrics_complete,
    )
    from radar_v2.app.services.run_service import RunService

    run_id = create_run(
        started_at=dt.datetime(2026, 8, 1, 12, 0).strftime("%Y-%m-%d %H:%M:%S"),
        task_id="dl_enel_sp", task_name="ENEL SP", category="ENEL", command="test",
    )
    initialize_run_metrics(run_id, utility="ENEL SP", task_id="dl_enel_sp")
    from core.metrics.radar_metrics import build_item_key
    import json as _json
    def _metric_line(i: int, outcome: str = "downloaded") -> str:
        item_key = build_item_key(
            utility="ENEL SP", account_id=f"UC{i:03d}",
            competence="2026-07", invoice_id=f"INV{i}",
        )
        return "RADAR_METRIC " + _json.dumps({
            "version": 1, "item_key": item_key, "outcome": outcome,
            "utility": "ENEL SP", "task_id": "dl_enel_sp", "competence": "2026-07",
        }, sort_keys=True, ensure_ascii=True)
    for i in range(5):
        RunService._record_metric_event(run_id, "dl_enel_sp", _metric_line(i))
    # idempotent: re-emitting the same item must not double the count
    RunService._record_metric_event(run_id, "dl_enel_sp", _metric_line(0))
    set_run_metrics_complete(run_id, complete=True)
    RunService._close_run_metrics(
        run_id, "dl_enel_sp", 0,
        dt.datetime(2026, 8, 1, 12, 0), dt.datetime(2026, 8, 1, 15, 0),
    )
    _login(client)
    payload = client.get("/api/calendar/summary?start=2026-08-01&end=2026-08-01").get_json()
    assert payload["days"][0]["downloaded"] == 5
    assert payload["days"][0]["by_utility"][0]["run_ids"] == [run_id]


def test_synced_metric_keeps_skipped_other_and_task_filter(client):
    from radar_v2.app.repositories.storage import create_run, initialize_run_metrics
    from radar_v2.app.services.run_service import RunService
    from core.metrics.radar_metrics import build_item_key
    import json as _json

    run_id = create_run(
        started_at="2026-08-03 10:00:00", task_id="dl_enel_ce",
        task_name="ENEL CE", category="Downloaders", command="test",
    )
    initialize_run_metrics(run_id, utility="ENEL CE", task_id="dl_enel_ce")

    for index, outcome in enumerate(("downloaded", "skipped_existing", "other")):
        item_key = build_item_key(
            utility="ENEL CE", account_id=f"UC{index}",
            competence="2026-08", invoice_id=f"INV{index}",
        )
        line = "RADAR_METRIC " + _json.dumps({
            "version": 1, "item_key": item_key, "outcome": outcome,
            "utility": "ENEL CE", "task_id": "dl_enel_ce", "competence": "2026-08",
        })
        RunService._record_metric_event(run_id, "dl_enel_ce", line)

    RunService._close_run_metrics(
        run_id, "dl_enel_ce", 0,
        dt.datetime(2026, 8, 3, 10, 0), dt.datetime(2026, 8, 3, 10, 1),
    )
    _login(client)
    payload = client.get(
        "/api/calendar/summary?start=2026-08-03&end=2026-08-03&task_id=dl_enel_ce"
    ).get_json()
    assert payload["totals"] == {
        "downloaded": 1, "skipped_existing": 1, "errors": 0,
        "other": 1, "processed": 3,
    }
    empty = client.get(
        "/api/calendar/summary?start=2026-08-03&end=2026-08-03&task_id=dl_cpfl_bt"
    ).get_json()
    assert empty["has_metrics"] is False
