"""Tests for the read-only email manifest importer.

Covers: classification, idempotency-key stability/collision handling,
dedup on re-run, incremental (watermark-based) sync, secret redaction, and
that duplicates/pending/errors never inflate the "downloaded" count.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from radar_v2.app.services.email_import_service import (
    CORRECTION,
    DUPLICATE,
    ERROR,
    IGNORED_NON_INVOICE,
    LINK_PENDING,
    SUCCESS,
    build_idempotency_key,
    classify_event,
    sync_email_manifest,
)


# ── classification ──────────────────────────────────────────────────────────

def test_classify_saved_and_ok_are_success():
    assert classify_event({"final_result": "saved"}) == SUCCESS
    assert classify_event({"result": "ok"}) == SUCCESS


def test_classify_pending_watcher_without_duplicate_is_success():
    assert classify_event({"final_result": "pending_watcher", "document_type": "FATURA"}) == SUCCESS


def test_classify_duplicate_sha_variants():
    assert classify_event({"final_result": "duplicate_sha"}) == DUPLICATE
    assert classify_event({"result": "duplicate_sha"}) == DUPLICATE
    assert classify_event({"final_result": "pending_watcher", "duplicate_sha": True}) == DUPLICATE


def test_classify_link_pending():
    assert classify_event({"event_type": "link_pending"}) == LINK_PENDING
    assert classify_event({"final_result": "link_pending"}) == LINK_PENDING


def test_classify_ignored_non_invoice_variants():
    assert classify_event({"final_result": "ignored_non_invoice"}) == IGNORED_NON_INVOICE
    assert classify_event({"result": "ignored_non_pdf"}) == IGNORED_NON_INVOICE
    assert classify_event({"processing_policy": "ignored_non_invoice"}) == IGNORED_NON_INVOICE


def test_classify_correction_is_never_a_new_capture():
    assert classify_event({"event_type": "correction"}) == CORRECTION


def test_classify_legacy_row_with_file_is_success():
    assert classify_event({"filename": "fatura.pdf", "saved_path": "/x/fatura.pdf"}) == SUCCESS


def test_classify_legacy_row_without_file_is_error():
    assert classify_event({"from": "a@b.com", "subject": "oi"}) == ERROR


def test_classify_legacy_row_with_link_candidates_but_no_file_is_link_pending():
    assert classify_event({"filename": "", "saved_path": "", "link_candidates": ["https://x"]}) == LINK_PENDING


def test_classify_legacy_row_with_link_domains_but_no_file_is_link_pending():
    assert classify_event({"filename": "", "saved_path": "", "link_domains": ["cpfl.com.br"],
                            "link_url_sha256_list": ["abc"]}) == LINK_PENDING


def test_classify_unknown_combination_is_error():
    assert classify_event({"final_result": "some_new_unmapped_state"}) == ERROR


# ── idempotency key ──────────────────────────────────────────────────────────

def test_idempotency_key_prefers_manifest_key():
    seen: set[str] = set()
    key = build_idempotency_key({"key": "abc123"}, 5, seen)
    assert key == "abc123"


def test_idempotency_key_disambiguates_collision():
    seen: set[str] = set()
    k1 = build_idempotency_key({"key": "dup"}, 10, seen)
    k2 = build_idempotency_key({"key": "dup"}, 11, seen)
    assert k1 != k2
    assert k1 == "dup"
    assert k2 == "dup#L11"


def test_idempotency_key_falls_back_to_line_no_when_no_key_or_message_id():
    seen: set[str] = set()
    key = build_idempotency_key({}, 42, seen)
    assert key == "line-42"


# ── end-to-end sync against an isolated sqlite db ──────────────────────────

@pytest.fixture()
def db_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from radar_v2.app.repositories import storage

    data_dir = tmp_path / "web_app"
    monkeypatch.setattr(storage, "APP_DATA_DIR", data_dir)
    monkeypatch.setattr(storage, "DB_PATH", data_dir / "history.sqlite3")
    monkeypatch.setattr(storage, "LEGACY_DB", tmp_path / "legacy.sqlite3")
    storage.ensure_db()
    return storage


def _write_manifest(tmp_path: Path, lines: list[dict]) -> Path:
    p = tmp_path / "manifest.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for obj in lines:
            fh.write(json.dumps(obj) + "\n")
    return p


SAMPLE_LINES = [
    {"key": "k1", "provider": "CPFL", "captured_at": "2026-08-01T10:00:00", "uc": "111",
     "filename": "a.pdf", "final_result": "saved", "document_type": "FATURA"},
    {"key": "k2", "provider": "CPFL", "captured_at": "2026-08-01T11:00:00", "uc": "111",
     "filename": "a.pdf", "final_result": "duplicate_sha", "duplicate_sha": True},
    {"key": "k3", "provider": "ENEL", "captured_at": "2026-08-02T09:00:00",
     "final_result": "ignored_non_invoice", "document_type": "INFORMATIVO_TARIFARIO"},
    {"key": "k4", "provider": "CPFL", "captured_at": "2026-08-02T09:30:00",
     "event_type": "link_pending", "reason_code": "CPFL_RECAPTCHA_BLOCKER"},
    {"key": "k5", "provider": "CPFL", "captured_at": "2026-08-02T09:30:00", "event_type": "correction"},
    {"key": "k6", "captured_at": "2026-08-03T09:30:00", "from": "x@y.com", "subject": "hi"},
]


def test_full_backfill_counts_and_never_inflates_downloaded(db_env, tmp_path):
    manifest = _write_manifest(tmp_path, SAMPLE_LINES)
    result = sync_email_manifest(manifest, storage_module=db_env)

    assert result["imported"] == 6
    assert result["already_present"] == 0
    assert result["by_category"][SUCCESS] == 1
    assert result["by_category"][DUPLICATE] == 1
    assert result["by_category"][IGNORED_NON_INVOICE] == 1
    assert result["by_category"][LINK_PENDING] == 1
    assert result["by_category"][CORRECTION] == 1
    assert result["by_category"][ERROR] == 1

    summary = db_env.email_sync_summary()
    assert summary["total_imported"] == 6
    assert summary["by_category"][SUCCESS] == 1
    assert summary["by_category"][DUPLICATE] == 1

    # The duplicate and the correction must never show up as a "downloaded"
    # metric item — only the true SUCCESS event does.
    cal = db_env.calendar_metric_summary("2026-08-01", "2026-08-03")
    assert cal["totals"]["downloaded"] == 1
    assert cal["totals"]["skipped_existing"] == 1  # the duplicate


def test_rerunning_full_backfill_is_a_pure_no_op(db_env, tmp_path):
    manifest = _write_manifest(tmp_path, SAMPLE_LINES)
    sync_email_manifest(manifest, storage_module=db_env)
    before = db_env.email_sync_summary()
    before_cal = db_env.calendar_metric_summary("2026-08-01", "2026-08-03")

    result_again = sync_email_manifest(manifest, storage_module=db_env)

    after = db_env.email_sync_summary()
    after_cal = db_env.calendar_metric_summary("2026-08-01", "2026-08-03")

    assert result_again["imported"] == 0
    assert after == before
    assert after_cal == before_cal


def test_incremental_sync_only_reads_new_lines(db_env, tmp_path):
    manifest = _write_manifest(tmp_path, SAMPLE_LINES[:3])
    first = sync_email_manifest(manifest, storage_module=db_env)
    assert first["imported"] == 3
    assert first["end_line"] == 3

    # Append more lines (simulating new IMAP captures) without touching
    # existing ones.
    with manifest.open("a", encoding="utf-8") as fh:
        for obj in SAMPLE_LINES[3:]:
            fh.write(json.dumps(obj) + "\n")

    second = sync_email_manifest(manifest, storage_module=db_env)
    assert second["start_line"] == 3
    assert second["imported"] == 3
    assert second["already_present"] == 0

    summary = db_env.email_sync_summary()
    assert summary["total_imported"] == 6


def test_duplicate_key_across_two_lines_still_dedups_correctly(db_env, tmp_path):
    manifest = _write_manifest(tmp_path, [
        {"key": "dup", "provider": "CPFL", "captured_at": "2026-08-01", "event_type": "correction"},
        {"key": "dup", "provider": "CPFL", "captured_at": "2026-08-01", "event_type": "correction"},
    ])
    result = sync_email_manifest(manifest, storage_module=db_env)
    assert result["imported"] == 2  # both recorded (disambiguated keys)
    assert result["by_category"][CORRECTION] == 2

    # Re-run must still be a no-op even though the raw manifest key collided.
    result2 = sync_email_manifest(manifest, storage_module=db_env)
    assert result2["imported"] == 0


def test_pending_reason_is_sanitized_never_raw_secret_fields(db_env, tmp_path):
    manifest = _write_manifest(tmp_path, [
        {"key": "k1", "provider": "CPFL", "captured_at": "2026-08-01", "event_type": "link_pending",
         "reason_code": "CPFL_RECAPTCHA_BLOCKER",
         "password_hint": "super-secret-should-never-appear",
         "authorized_url": "https://portal.example/download?token=SHOULD_NEVER_APPEAR"},
    ])
    sync_email_manifest(manifest, storage_module=db_env)
    history = db_env.email_event_history()
    assert len(history) == 1
    row = history[0]
    serialized = json.dumps(row)
    assert "super-secret" in row.get("pending_reason", "") or row["pending_reason"] == "CPFL - reCAPTCHA requerido"
    assert "SHOULD_NEVER_APPEAR" not in serialized
    assert "super-secret-should-never-appear" not in serialized


def test_ignored_and_error_events_produce_no_run_metric_item(db_env, tmp_path):
    manifest = _write_manifest(tmp_path, [
        {"key": "k1", "provider": "ENEL", "captured_at": "2026-08-05",
         "final_result": "ignored_non_invoice"},
        {"key": "k2", "from": "x@y.com", "subject": "no classifiable fields"},
    ])
    sync_email_manifest(manifest, storage_module=db_env)
    cal = db_env.calendar_metric_summary("2026-08-05", "2026-08-05")
    # IGNORED_NON_INVOICE and unclassifiable rows never touch run_metrics at all.
    assert cal["has_metrics"] is False
