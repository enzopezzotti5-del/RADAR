from pathlib import Path

from radar_v2.app.repositories import storage
from radar_v2.app.services import download_artifacts


def _run(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "history.sqlite3")
    monkeypatch.setattr(storage, "APP_DATA_DIR", tmp_path)
    return storage.create_run("2026-08-10T10:00:00-03:00", "dl_copel_bt", "COPEL", "Downloaders", "test")


def test_confirm_download_artifact_is_idempotent_by_task_and_sha(tmp_path, monkeypatch):
    run_id = _run(tmp_path, monkeypatch)
    source = tmp_path / "invoice.pdf"
    source.write_bytes(b"%PDF-1.4\nfixture\n%%EOF")
    calls = []

    def publish(path, **kwargs):
        calls.append((Path(path), kwargs))
        return {"ok": True, "handoff_id": "handoff-1"}

    monkeypatch.setattr(download_artifacts, "request_orbit_handoff", publish)
    first = download_artifacts.confirm_download_artifact(source, run_id=run_id, task_id="dl_copel_bt", utility="COPEL")
    second = download_artifacts.confirm_download_artifact(source, run_id=run_id, task_id="dl_copel_bt", utility="COPEL")

    assert first["receipt_id"] == second["receipt_id"]
    assert first["sha256"] == second["sha256"]
    assert second["handoff_id"] == "handoff-1"
    assert len(calls) == 2  # a retry reuses the same publisher identity/outbox item


def test_confirm_download_artifact_keeps_retryable_failure(tmp_path, monkeypatch):
    run_id = _run(tmp_path, monkeypatch)
    source = tmp_path / "invoice.pdf"
    source.write_bytes(b"%PDF-1.4\nfixture\n%%EOF")
    monkeypatch.setattr(download_artifacts, "request_orbit_handoff", lambda *a, **k: {"ok": False, "error": "OSError"})

    receipt = download_artifacts.confirm_download_artifact(source, run_id=run_id, task_id="dl_copel_bt", utility="COPEL")

    assert receipt["handoff_status"] == download_artifacts.FAILED_RETRYABLE
    assert receipt["last_error"] == "OSError"
