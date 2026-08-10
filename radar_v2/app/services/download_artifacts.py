"""Canonical receipt and idempotent handoff request for real PDF downloads."""
from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
from pathlib import Path

from radar_v2.app.repositories import storage
from radar_v2.app.services.orbit_handoff import request_orbit_handoff


RECEIPT_VERSION = 1
PENDING = "PENDING"
DELIVERED = "DELIVERED"
ACKNOWLEDGED = "ACKNOWLEDGED"
FAILED_RETRYABLE = "FAILED_RETRYABLE"
FAILED_TERMINAL = "FAILED_TERMINAL"
_VALID_STATUSES = frozenset({PENDING, DELIVERED, ACKNOWLEDGED, FAILED_RETRYABLE, FAILED_TERMINAL})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat()


def _receipt_id(task_id: str, sha256: str) -> str:
    return hashlib.sha256(f"receipt-v{RECEIPT_VERSION}\\0{task_id}\\0{sha256}".encode()).hexdigest()


def confirm_download_artifact(
    path: str | Path,
    *,
    run_id: int,
    task_id: str,
    utility: str,
    handoff_required: bool = True,
) -> dict[str, object]:
    """Persist a real, stable PDF before requesting the common Orbit publisher.

    The unique `(task_id, sha256)` constraint makes retries and Radar restarts
    reuse the same logical receipt and handoff identity.
    """
    source = Path(path)
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise ValueError("download artifact must be a final PDF file")
    before = source.stat()
    file_hash = _sha256(source)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("download artifact is still changing")
    receipt_id = _receipt_id(task_id, file_hash)
    now = _now()
    storage.ensure_db()
    with storage._connection() as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            """INSERT OR IGNORE INTO download_artifact_receipts
               (receipt_id,receipt_version,run_id,task_id,utility,original_path,filename,
                sha256,size_bytes,downloaded_at,handoff_required,handoff_status,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (receipt_id, RECEIPT_VERSION, run_id, task_id, utility, str(source), source.name,
             file_hash, after.st_size, now, int(handoff_required), PENDING, now, now),
        )
        row = conn.execute(
            "SELECT * FROM download_artifact_receipts WHERE receipt_id=?", (receipt_id,)
        ).fetchone()
    result = dict(row)
    if not handoff_required or result["handoff_status"] in {DELIVERED, ACKNOWLEDGED}:
        return result
    published = request_orbit_handoff(source, task_id=task_id, utility=utility, run_id=run_id)
    # A staged outbox file is durable, but Orbit has not delivered it yet.
    status = PENDING if published.get("ok") else FAILED_RETRYABLE
    with storage._connection() as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            """UPDATE download_artifact_receipts
               SET handoff_id=COALESCE(?, handoff_id), handoff_status=?,
                   last_error=?, retry_count=retry_count+1, updated_at=?
               WHERE receipt_id=?""",
            (published.get("handoff_id"), status, published.get("error"), _now(), receipt_id),
        )
        row = conn.execute("SELECT * FROM download_artifact_receipts WHERE receipt_id=?", (receipt_id,)).fetchone()
    return dict(row)
