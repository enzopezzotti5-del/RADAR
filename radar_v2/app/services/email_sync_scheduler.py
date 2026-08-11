"""Lightweight periodic job: "Sincronizar Faturas por Email".

Deliberately independent from ScheduleService/TaskCatalogService, which are
built around launching subprocess downloaders from the `schedules` table.
The email sync has no subprocess, no browser, no credentials of its own — it
only reads a JSONL file and writes to RADAR's own sqlite3 — so a dedicated
daemon thread with its own interval and an overlap guard is a much smaller,
safer surface than teaching the shared task/schedule machinery about a new
kind of non-downloader task.

Never touches IMAP, Orbit, CONSEN, or WatcherV2Engine. Never writes to
manifest.jsonl. Idempotent by construction (see email_import_service).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("radar_v2.email_sync")

DEFAULT_MANIFEST_PATH = (
    r"C:\Users\Revit\energia-automacao\runtime\email_faturas\manifest.jsonl"
)
DEFAULT_INTERVAL_SECONDS = 900  # 15 min


class EmailSyncScheduler:
    def __init__(self, *, storage_module=None, manifest_path: str | Path | None = None,
                 interval_seconds: int | None = None) -> None:
        if storage_module is None:
            from ..repositories import storage as storage_module  # noqa: PLC0415
        self._storage = storage_module
        self._manifest_path = Path(
            manifest_path
            or os.environ.get("RADAR_EMAIL_MANIFEST_PATH")
            or DEFAULT_MANIFEST_PATH
        )
        self._interval = interval_seconds or int(
            os.environ.get("RADAR_EMAIL_SYNC_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)
        )
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.last_result: dict | None = None
        self.last_run_at: str | None = None
        self.last_error: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="radar_v2_email_sync"
        )
        self._thread.start()

    def _loop(self) -> None:
        while True:
            self.run_once()
            time.sleep(self._interval)

    def run_once(self) -> dict | None:
        """Runs one sync pass. Safe to call directly (e.g. for a canary/manual
        trigger); guarded so overlapping calls never run concurrently."""
        if not self._lock.acquire(blocking=False):
            log.info("Sincronizacao de e-mail ja em andamento; ciclo ignorado.")
            return None
        try:
            import datetime as dt
            from .email_import_service import sync_email_manifest

            result = sync_email_manifest(self._manifest_path, storage_module=self._storage)
            self.last_result = result
            self.last_run_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.last_error = None
            if result["imported"]:
                log.info(
                    "Sincronizacao de e-mail: %s novos eventos (SUCCESS=%s, DUPLICATE=%s, "
                    "LINK_PENDING=%s, IGNORED=%s, ERROR=%s)",
                    result["imported"],
                    result["by_category"].get("SUCCESS", 0),
                    result["by_category"].get("DUPLICATE", 0),
                    result["by_category"].get("LINK_PENDING", 0),
                    result["by_category"].get("IGNORED_NON_INVOICE", 0),
                    result["by_category"].get("ERROR", 0),
                )
            return result
        except Exception as exc:  # pragma: no cover - defensive
            self.last_error = str(exc)
            log.exception("Erro na sincronizacao de e-mail")
            return None
        finally:
            self._lock.release()

    def status(self) -> dict:
        return {
            "manifest_path": str(self._manifest_path),
            "interval_seconds": self._interval,
            "running": bool(self._thread and self._thread.is_alive()),
            "last_run_at": self.last_run_at,
            "last_error": self.last_error,
            "last_result": self.last_result,
        }
