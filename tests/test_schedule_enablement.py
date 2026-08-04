import datetime as dt


def test_enabling_schedule_recomputes_next_run(monkeypatch, tmp_path):
    from radar_v2.app.repositories import storage
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "history.sqlite3")
    monkeypatch.setattr(storage, "APP_DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "LEGACY_DB", tmp_path / "legacy.sqlite3")
    schedule_id = storage.create_schedule(
        label="Test", task_id="dl_test", task_name="Test", category="Downloaders",
        params_json="{}", frequency="daily", time_of_day="23:59",
        day_of_week=None, day_of_month=None,
    )
    with storage.sqlite3.connect(storage.DB_PATH) as conn:
        conn.execute("UPDATE schedules SET enabled=0,next_run_at='2020-01-01 00:00:00' WHERE id=?", (schedule_id,))
        conn.commit()
    storage.toggle_schedule(schedule_id, True)
    row = next(x for x in storage.list_schedules() if x["id"] == schedule_id)
    assert row["enabled"] == 1
    assert dt.datetime.fromisoformat(row["next_run_at"]) > dt.datetime.now()
