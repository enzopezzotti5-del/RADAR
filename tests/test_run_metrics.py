"""Testes da infraestrutura RADAR_METRIC: emit → parse → storage → calendar API."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from radar_v2.app.repositories import storage
    monkeypatch.setattr(storage, "APP_DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "history.sqlite3")
    monkeypatch.setattr(storage, "LEGACY_DB", tmp_path / "legacy.sqlite3")


@pytest.fixture()
def calendar_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Flask test client with only the blueprint — no scheduler or extensions needed."""
    from flask import Flask
    from radar_v2.app.api.routes import bp
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app.test_client()


def _make_metric_line(suffix: str, outcome: str = "downloaded",
                      utility: str = "ENEL SP", task_id: str = "dl_enel_sp") -> str:
    from core.metrics.radar_metrics import build_item_key
    item_key = build_item_key(
        utility=utility, account_id=f"UC{suffix}",
        competence="2026-07", invoice_id=f"INV{suffix}",
    )
    return "RADAR_METRIC " + json.dumps({
        "version": 1,
        "item_key": item_key,
        "outcome": outcome,
        "utility": utility,
        "task_id": task_id,
        "competence": "2026-07",
    }, sort_keys=True, ensure_ascii=True)


def _make_run(*, task_id: str = "dl_enel_sp", utility: str = "ENEL SP") -> int:
    from radar_v2.app.repositories.storage import create_run, initialize_run_metrics
    import datetime as dt
    run_id = create_run(
        started_at=dt.datetime(2026, 7, 31, 10, 0).strftime("%Y-%m-%d %H:%M:%S"),
        task_id=task_id, task_name="ENEL SP", category="ENEL", command="test",
    )
    initialize_run_metrics(run_id, utility=utility, task_id=task_id)
    return run_id


def _record(run_id: int, suffix: str, outcome: str = "downloaded") -> None:
    from radar_v2.app.services.run_service import RunService
    RunService._record_metric_event(
        run_id, "dl_enel_sp", _make_metric_line(suffix, outcome),
    )


# ── emit / parse round-trip ───────────────────────────────────────────────────

def test_emit_outcome_produces_valid_json(capsys):
    import os
    from core.metrics.radar_metrics import emit_outcome
    os.environ["RADAR_RUN_ID"] = "1"
    os.environ["RADAR_TASK_ID"] = "dl_enel_sp"
    try:
        emit_outcome("downloaded", utility="ENEL SP", account_id="UC001",
                     competence="07/2026", invoice_id="INV001")
    finally:
        os.environ.pop("RADAR_RUN_ID", None)
        os.environ.pop("RADAR_TASK_ID", None)
    captured = capsys.readouterr().out.strip()
    assert captured.startswith("RADAR_METRIC ")
    payload = json.loads(captured[len("RADAR_METRIC "):])
    assert payload["outcome"] == "downloaded"
    assert payload["utility"] == "ENEL SP"
    assert payload["competence"] == "2026-07"
    assert payload["version"] == 1
    assert payload["item_key"].startswith("invoice:")


def test_emit_is_silent_without_env_vars(capsys):
    import os
    from core.metrics.radar_metrics import emit_outcome
    os.environ.pop("RADAR_RUN_ID", None)
    os.environ.pop("RADAR_TASK_ID", None)
    emit_outcome("downloaded", utility="ENEL SP", account_id="UC001",
                 competence="07/2026", invoice_id="INV001")
    assert capsys.readouterr().out == ""


def test_parse_metric_event_round_trip():
    from radar_v2.app.services.metric_events import parse_metric_event
    line = _make_metric_line("001")
    event = parse_metric_event(line)
    assert event is not None
    assert event.outcome == "downloaded"
    assert event.utility == "ENEL SP"
    assert event.task_id == "dl_enel_sp"
    assert event.competence == "2026-07"
    assert event.version == 1
    assert event.item_key.startswith("invoice:")


def test_parse_metric_event_returns_none_for_unrelated_lines():
    from radar_v2.app.services.metric_events import parse_metric_event
    assert parse_metric_event("INFO: something happened") is None
    assert parse_metric_event("RADAR_PROGRESS {}") is None
    # missing version / item_key → rejected
    assert parse_metric_event(
        'RADAR_METRIC {"outcome":"downloaded","utility":"X","task_id":"t","competence":"2026-07"}'
    ) is None


def test_competence_normalization():
    from core.metrics.radar_metrics import normalize_competence
    assert normalize_competence("07/2026") == "2026-07"
    assert normalize_competence("2026-07") == "2026-07"
    assert normalize_competence("bad") == ""


def test_build_item_key_is_deterministic():
    from core.metrics.radar_metrics import build_item_key
    k1 = build_item_key(utility="ENEL SP", account_id="00001234",
                         competence="2026-07", invoice_id="INV001")
    k2 = build_item_key(utility="ENEL SP", account_id="00001234",
                         competence="2026-07", invoice_id="INV001")
    assert k1 == k2
    assert k1.startswith("invoice:")
    k3 = build_item_key(utility="ENEL SP", account_id="00001235",
                         competence="2026-07", invoice_id="INV001")
    assert k1 != k3


# ── storage: items & counts ───────────────────────────────────────────────────

def test_record_metric_event_increments_count():
    run_id = _make_run()
    _record(run_id, "A")
    from radar_v2.app.repositories.storage import get_run_metric_counts
    counts = get_run_metric_counts(run_id)
    assert counts is not None
    assert counts["downloaded"] == 1


def test_item_is_idempotent():
    run_id = _make_run()
    _record(run_id, "A")
    _record(run_id, "A")  # same item_key
    from radar_v2.app.repositories.storage import get_run_metric_counts
    counts = get_run_metric_counts(run_id)
    assert counts["downloaded"] == 1


def test_different_items_each_counted():
    run_id = _make_run()
    _record(run_id, "A")
    _record(run_id, "B")
    _record(run_id, "C")
    from radar_v2.app.repositories.storage import get_run_metric_counts
    counts = get_run_metric_counts(run_id)
    assert counts["downloaded"] == 3


def test_multiple_outcomes_counted_separately():
    run_id = _make_run()
    _record(run_id, "A", "downloaded")
    _record(run_id, "B", "skipped_existing")
    _record(run_id, "C", "item_error")
    _record(run_id, "D", "other")
    from radar_v2.app.repositories.storage import get_run_metric_counts
    counts = get_run_metric_counts(run_id)
    assert counts["downloaded"] == 1
    assert counts["skipped_existing"] == 1
    assert counts["item_error"] == 1
    assert counts["other"] == 1


def test_same_item_key_updates_to_latest_outcome():
    """Re-emitting the same item_key with a different outcome updates to last outcome (last-write-wins).
    Total item count stays at 1; the category shifts."""
    run_id = _make_run()
    _record(run_id, "A", "downloaded")
    _record(run_id, "A", "item_error")  # same item_key → outcome updated
    from radar_v2.app.repositories.storage import get_run_metric_counts
    counts = get_run_metric_counts(run_id)
    assert counts["downloaded"] == 0
    assert counts["item_error"] == 1


def test_get_run_metric_counts_returns_none_for_unknown_run():
    from radar_v2.app.repositories.storage import ensure_db, get_run_metric_counts
    ensure_db()
    assert get_run_metric_counts(99999) is None


def test_initialize_run_metrics_is_idempotent():
    from radar_v2.app.repositories.storage import initialize_run_metrics, get_run_metric_counts
    run_id = _make_run()
    initialize_run_metrics(run_id, utility="ENEL SP", task_id="dl_enel_sp")  # second call
    _record(run_id, "A")
    assert get_run_metric_counts(run_id)["downloaded"] == 1


# ── schema: ensure_db creates tables ─────────────────────────────────────────

def test_ensure_db_creates_metric_tables(tmp_path: Path):
    import sqlite3
    from radar_v2.app.repositories.storage import ensure_db, DB_PATH
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "run_metrics" in tables
    assert "run_metric_items" in tables


# ── close_run_metrics syncs to invoice_run_metrics ────────────────────────────

def test_close_run_metrics_syncs_to_invoice():
    import datetime as dt
    from radar_v2.app.repositories.storage import (
        set_run_metrics_complete, calendar_invoice_metrics,
    )
    from radar_v2.app.services.run_service import RunService
    run_id = _make_run()
    _record(run_id, "A")
    _record(run_id, "B")
    set_run_metrics_complete(run_id, complete=True)
    label = RunService._close_run_metrics(
        run_id, "dl_enel_sp", 0,
        dt.datetime(2026, 7, 31, 10, 0), dt.datetime(2026, 7, 31, 11, 0),
    )
    assert label is not None
    assert "baixadas=2" in label
    rows = calendar_invoice_metrics("2026-07-31", "2026-07-31")
    assert len(rows) == 1
    assert rows[0]["downloaded"] == 2


# ── calendar_metric_summary ───────────────────────────────────────────────────

def test_calendar_metric_summary_returns_empty_for_no_data():
    from radar_v2.app.repositories.storage import calendar_metric_summary, ensure_db
    ensure_db()
    result = calendar_metric_summary("2026-07-01", "2026-07-31")
    assert result["has_metrics"] is False
    assert result["days"] == []
    assert result["totals"]["downloaded"] == 0


def test_calendar_metric_summary_from_run_metrics_only():
    from radar_v2.app.repositories.storage import calendar_metric_summary
    run_id = _make_run()
    _record(run_id, "A")
    _record(run_id, "B")
    result = calendar_metric_summary("2026-07-31", "2026-07-31")
    assert result["has_metrics"] is True
    assert len(result["days"]) == 1
    assert result["days"][0]["downloaded"] == 2
    assert result["totals"]["downloaded"] == 2


def test_calendar_metric_summary_deduplicates_synced_run():
    import datetime as dt
    from radar_v2.app.repositories.storage import (
        calendar_metric_summary, set_run_metrics_complete,
    )
    from radar_v2.app.services.run_service import RunService
    run_id = _make_run()
    _record(run_id, "A")
    set_run_metrics_complete(run_id, complete=True)
    RunService._close_run_metrics(
        run_id, "dl_enel_sp", 0,
        dt.datetime(2026, 7, 31, 10, 0), dt.datetime(2026, 7, 31, 11, 0),
    )
    # run is now in BOTH tables; must not be double-counted
    result = calendar_metric_summary("2026-07-31", "2026-07-31")
    assert result["totals"]["downloaded"] == 1


def test_calendar_metric_summary_utility_filter():
    from radar_v2.app.repositories.storage import calendar_metric_summary
    run_id = _make_run(utility="ENEL SP")
    _record(run_id, "A")
    assert calendar_metric_summary("2026-07-31", "2026-07-31", utility="ENEL SP")["has_metrics"] is True
    assert calendar_metric_summary("2026-07-31", "2026-07-31", utility="CELESC")["has_metrics"] is False


# ── calendar API endpoint ─────────────────────────────────────────────────────

def test_calendar_endpoint_returns_200_with_totals(calendar_client):
    run_id = _make_run()
    _record(run_id, "A")
    resp = calendar_client.get(
        "/api/calendar/summary?start=2026-07-31&end=2026-07-31&timezone=America/Sao_Paulo"
    )
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["has_metrics"] is True
    assert payload["totals"]["downloaded"] == 1


def test_calendar_endpoint_filters_utility(calendar_client):
    run_id = _make_run(utility="ENEL SP")
    _record(run_id, "A")
    resp_match = calendar_client.get(
        "/api/calendar/summary?start=2026-07-31&end=2026-07-31&utility=ENEL+SP"
    )
    resp_no = calendar_client.get(
        "/api/calendar/summary?start=2026-07-31&end=2026-07-31&utility=CELESC"
    )
    assert resp_match.get_json()["has_metrics"] is True
    assert resp_no.get_json()["has_metrics"] is False


def test_calendar_endpoint_empty_period_has_no_metrics(calendar_client):
    from radar_v2.app.repositories.storage import ensure_db
    ensure_db()
    resp = calendar_client.get(
        "/api/calendar/summary?start=2026-06-01&end=2026-06-30&timezone=America/Sao_Paulo"
    )
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["has_metrics"] is False
    assert payload["days"] == []


def test_calendar_endpoint_returns_400_for_bad_dates(calendar_client):
    from radar_v2.app.repositories.storage import ensure_db
    ensure_db()
    resp = calendar_client.get("/api/calendar/summary?start=bad&end=2026-07-31")
    assert resp.status_code == 400
