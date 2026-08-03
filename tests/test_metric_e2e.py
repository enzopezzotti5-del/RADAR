"""
E2E tests — RADAR_METRIC homologation scenarios (sections 9-11).

All tests run against an isolated in-memory-equivalent SQLite (tmp_path),
never touching any production file.

Scenarios:
  9. Golden path: 3 downloaded + 2 skipped_existing, idempotency
 10. Cancellation: 2 downloaded emitted, exit_code=1 → metrics_complete=False
 11. Global failure: run exits non-zero, no events → metrics_complete=False
     Anti-double-counting: pipeline task (pl_light_bt) not in METRIC_TASKS
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

import pytest


# ── shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from radar_v2.app.repositories import storage
    monkeypatch.setattr(storage, "APP_DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "history.sqlite3")
    monkeypatch.setattr(storage, "LEGACY_DB", tmp_path / "legacy.sqlite3")


@pytest.fixture()
def calendar_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from flask import Flask
    from radar_v2.app.api.routes import bp
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app.test_client()


def _make_run(task_id: str = "dl_enel_sp", utility: str = "ENEL SP") -> int:
    from radar_v2.app.repositories.storage import create_run, initialize_run_metrics
    run_id = create_run(
        started_at=dt.datetime(2026, 7, 31, 10, 0).strftime("%Y-%m-%d %H:%M:%S"),
        task_id=task_id, task_name=utility, category="ENEL", command="test",
    )
    initialize_run_metrics(run_id, utility=utility, task_id=task_id)
    return run_id


def _make_line(suffix: str, outcome: str, utility: str = "ENEL SP",
               task_id: str = "dl_enel_sp") -> str:
    from core.metrics.radar_metrics import build_item_key
    item_key = build_item_key(
        utility=utility, account_id=f"UC{suffix}",
        competence="2026-07", invoice_id=f"INV{suffix}",
    )
    return "RADAR_METRIC " + json.dumps({
        "version": 1, "item_key": item_key, "outcome": outcome,
        "utility": utility, "task_id": task_id, "competence": "2026-07",
    }, sort_keys=True, ensure_ascii=True)


def _record(run_id: int, suffix: str, outcome: str,
            utility: str = "ENEL SP", task_id: str = "dl_enel_sp") -> None:
    from radar_v2.app.services.run_service import RunService
    RunService._record_metric_event(
        run_id, task_id, _make_line(suffix, outcome, utility, task_id),
    )


def _read_metrics_complete(run_id: int) -> bool:
    from radar_v2.app.repositories.storage import DB_PATH
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT metrics_complete FROM run_metrics WHERE run_id=?", (run_id,)
        ).fetchone()
    return bool(row and row[0])


# ── Section 9 — Golden path: 3 downloaded + 2 skipped_existing ───────────────

class TestGoldenPath:
    def test_5_items_stored_correctly(self):
        run_id = _make_run()
        _record(run_id, "A", "downloaded")
        _record(run_id, "B", "downloaded")
        _record(run_id, "C", "downloaded")
        _record(run_id, "D", "skipped_existing")
        _record(run_id, "E", "skipped_existing")

        from radar_v2.app.repositories.storage import get_run_metric_counts
        counts = get_run_metric_counts(run_id)
        assert counts is not None
        assert counts["downloaded"] == 3
        assert counts["skipped_existing"] == 2
        assert counts["item_error"] == 0

    def test_5_unique_items_in_run_metric_items(self):
        run_id = _make_run()
        for s, o in [("A", "downloaded"), ("B", "downloaded"), ("C", "downloaded"),
                     ("D", "skipped_existing"), ("E", "skipped_existing")]:
            _record(run_id, s, o)

        from radar_v2.app.repositories.storage import DB_PATH
        with sqlite3.connect(DB_PATH) as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM run_metric_items WHERE run_id=?", (run_id,)
            ).fetchone()[0]
        assert n == 5

    def test_calendar_metric_summary_downloaded_3_skipped_2(self):
        run_id = _make_run()
        for s, o in [("A", "downloaded"), ("B", "downloaded"), ("C", "downloaded"),
                     ("D", "skipped_existing"), ("E", "skipped_existing")]:
            _record(run_id, s, o)

        from radar_v2.app.repositories.storage import calendar_metric_summary
        result = calendar_metric_summary("2026-07-31", "2026-07-31")
        assert result["has_metrics"] is True
        assert len(result["days"]) == 1
        day = result["days"][0]
        assert day["downloaded"] == 3
        assert day["skipped_existing"] == 2

    def test_api_has_metrics_true(self, calendar_client):
        run_id = _make_run()
        _record(run_id, "A", "downloaded")
        _record(run_id, "B", "downloaded")
        _record(run_id, "C", "downloaded")
        _record(run_id, "D", "skipped_existing")
        _record(run_id, "E", "skipped_existing")

        resp = calendar_client.get(
            "/api/calendar/summary?start=2026-07-31&end=2026-07-31"
        )
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload["has_metrics"] is True
        assert payload["totals"]["downloaded"] == 3
        assert payload["totals"]["skipped_existing"] == 2

    def test_idempotency_reemit_same_events(self):
        """Re-emitting the same 5 events must not inflate counts."""
        run_id = _make_run()
        events = [("A", "downloaded"), ("B", "downloaded"), ("C", "downloaded"),
                  ("D", "skipped_existing"), ("E", "skipped_existing")]
        for s, o in events:
            _record(run_id, s, o)
        # re-emit each event a second time
        for s, o in events:
            _record(run_id, s, o)

        from radar_v2.app.repositories.storage import get_run_metric_counts
        counts = get_run_metric_counts(run_id)
        assert counts["downloaded"] == 3
        assert counts["skipped_existing"] == 2

    def test_idempotency_item_count_stays_at_5(self):
        """Row count in run_metric_items must remain 5 after re-emission."""
        run_id = _make_run()
        events = [("A", "downloaded"), ("B", "downloaded"), ("C", "downloaded"),
                  ("D", "skipped_existing"), ("E", "skipped_existing")]
        for s, o in events:
            _record(run_id, s, o)
        for s, o in events:
            _record(run_id, s, o)

        from radar_v2.app.repositories.storage import DB_PATH
        with sqlite3.connect(DB_PATH) as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM run_metric_items WHERE run_id=?", (run_id,)
            ).fetchone()[0]
        assert n == 5


# ── Section 10 — Cancellation ─────────────────────────────────────────────────

class TestCancellation:
    def test_downloaded_count_preserved_after_cancel(self):
        """Events emitted before kill must survive; metrics_complete must be False."""
        run_id = _make_run()
        _record(run_id, "A", "downloaded")
        _record(run_id, "B", "downloaded")

        # Simulate cancellation: process killed → exit_code != 0
        from radar_v2.app.services.run_service import RunService
        RunService._close_run_metrics(
            run_id, "dl_enel_sp", 1,                         # exit_code=1 (killed)
            dt.datetime(2026, 7, 31, 10, 0),
            dt.datetime(2026, 7, 31, 10, 5),
        )

        from radar_v2.app.repositories.storage import get_run_metric_counts
        counts = get_run_metric_counts(run_id)
        assert counts["downloaded"] == 2

    def test_metrics_complete_false_after_cancel(self):
        run_id = _make_run()
        _record(run_id, "A", "downloaded")
        _record(run_id, "B", "downloaded")

        from radar_v2.app.services.run_service import RunService
        RunService._close_run_metrics(
            run_id, "dl_enel_sp", 1,
            dt.datetime(2026, 7, 31, 10, 0),
            dt.datetime(2026, 7, 31, 10, 5),
        )

        assert _read_metrics_complete(run_id) is False

    def test_invoice_run_metrics_metrics_complete_false_after_cancel(self):
        run_id = _make_run()
        _record(run_id, "A", "downloaded")
        _record(run_id, "B", "downloaded")

        from radar_v2.app.services.run_service import RunService
        RunService._close_run_metrics(
            run_id, "dl_enel_sp", 1,
            dt.datetime(2026, 7, 31, 10, 0),
            dt.datetime(2026, 7, 31, 10, 5),
        )

        from radar_v2.app.repositories.storage import calendar_invoice_metrics
        rows = calendar_invoice_metrics("2026-07-31", "2026-07-31")
        assert len(rows) == 1
        assert rows[0]["metrics_complete"] == 0

    def test_metrics_complete_true_only_on_exit_0(self):
        """Baseline: same scenario but exit_code=0 must produce metrics_complete=True."""
        run_id = _make_run()
        _record(run_id, "A", "downloaded")

        from radar_v2.app.services.run_service import RunService
        RunService._close_run_metrics(
            run_id, "dl_enel_sp", 0,
            dt.datetime(2026, 7, 31, 10, 0),
            dt.datetime(2026, 7, 31, 10, 5),
        )

        assert _read_metrics_complete(run_id) is True


# ── Section 11 — Global failure (no events, non-zero exit) ────────────────────

class TestGlobalFailure:
    def test_metrics_complete_false_on_failure_no_events(self):
        """Run exits non-zero with zero events → metrics_complete remains False."""
        run_id = _make_run()
        # no events emitted

        from radar_v2.app.services.run_service import RunService
        RunService._close_run_metrics(
            run_id, "dl_enel_sp", 1,
            dt.datetime(2026, 7, 31, 10, 0),
            dt.datetime(2026, 7, 31, 10, 0, 30),
        )

        assert _read_metrics_complete(run_id) is False

    def test_no_item_error_count_on_global_failure(self):
        """Global failure must NOT auto-generate item_error events."""
        run_id = _make_run()

        from radar_v2.app.services.run_service import RunService
        RunService._close_run_metrics(
            run_id, "dl_enel_sp", 1,
            dt.datetime(2026, 7, 31, 10, 0),
            dt.datetime(2026, 7, 31, 10, 0, 30),
        )

        from radar_v2.app.repositories.storage import get_run_metric_counts
        counts = get_run_metric_counts(run_id)
        assert counts is not None
        assert counts["item_error"] == 0
        assert counts["downloaded"] == 0

    def test_calendar_shows_zero_downloaded_on_failure(self):
        run_id = _make_run()

        from radar_v2.app.services.run_service import RunService
        RunService._close_run_metrics(
            run_id, "dl_enel_sp", 1,
            dt.datetime(2026, 7, 31, 10, 0),
            dt.datetime(2026, 7, 31, 10, 0, 30),
        )

        from radar_v2.app.repositories.storage import calendar_metric_summary
        result = calendar_metric_summary("2026-07-31", "2026-07-31")
        assert result["totals"]["downloaded"] == 0


# ── Anti-double-counting: pipeline tasks not in METRIC_TASKS ─────────────────

class TestAntiDoubleCountPipeline:
    def test_pipeline_task_not_initialized(self):
        """pl_light_bt is NOT in METRIC_TASKS → initialize_run_metrics never called
        → get_run_metric_counts returns None → no calendar row → zero double-count risk."""
        from radar_v2.app.repositories.storage import create_run, get_run_metric_counts, ensure_db
        ensure_db()
        run_id = create_run(
            started_at=dt.datetime(2026, 7, 31, 10, 0).strftime("%Y-%m-%d %H:%M:%S"),
            task_id="pl_light_bt", task_name="Light BT Pipeline",
            category="Pipeline", command="test",
        )
        # Do NOT call initialize_run_metrics (replicates RunService.launch behaviour)
        assert get_run_metric_counts(run_id) is None

    def test_pipeline_task_not_in_metric_tasks(self):
        """All pl_* tasks must be absent from METRIC_TASKS."""
        from radar_v2.app.services.run_service import METRIC_TASKS
        pipeline_ids = [t for t in METRIC_TASKS if t.startswith("pl_")]
        assert pipeline_ids == [], f"Pipeline IDs found in METRIC_TASKS: {pipeline_ids}"

    def test_dl_light_rj_not_in_metric_tasks(self):
        """dl_light_rj uses BB modal — it must remain excluded from METRIC_TASKS."""
        from radar_v2.app.services.run_service import METRIC_TASKS
        assert "dl_light_rj" not in METRIC_TASKS

    def test_close_run_metrics_noop_for_pipeline_run(self):
        """_close_run_metrics returns None (no-op) when run has no metric row."""
        from radar_v2.app.repositories.storage import create_run, ensure_db
        from radar_v2.app.services.run_service import RunService
        ensure_db()
        run_id = create_run(
            started_at=dt.datetime(2026, 7, 31, 10, 0).strftime("%Y-%m-%d %H:%M:%S"),
            task_id="pl_light_bt", task_name="Light BT Pipeline",
            category="Pipeline", command="test",
        )
        result = RunService._close_run_metrics(
            run_id, "pl_light_bt", 0,
            dt.datetime(2026, 7, 31, 10, 0),
            dt.datetime(2026, 7, 31, 10, 5),
        )
        assert result is None
