"""Emit non-sensitive, idempotent operational results to Radar V2 stdout."""
from __future__ import annotations

import hashlib
import json
import os
import re


OUTCOMES = frozenset({"downloaded", "skipped_existing", "item_error", "other"})
_YYYY_MM = re.compile(r"^(\d{4})[-/](\d{2})$")
_MM_YYYY = re.compile(r"^(\d{2})[-/](\d{4})$")


def normalize_competence(value: object) -> str:
    text = str(value or "").strip()
    if match := _YYYY_MM.fullmatch(text):
        return f"{match.group(1)}-{match.group(2)}"
    if match := _MM_YYYY.fullmatch(text):
        return f"{match.group(2)}-{match.group(1)}"
    return ""


def build_item_key(*, utility: str, account_id: object, competence: object, invoice_id: object) -> str:
    """Hashes only the stable item identity; raw customer identifiers never leave the robot."""
    normalized_competence = normalize_competence(competence)
    raw = "|".join((
        str(utility or "").strip().upper(),
        str(account_id or "").strip().lstrip("0") or "0",
        normalized_competence,
        str(invoice_id or "").strip(),
    ))
    return "invoice:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def emit_outcome(
    outcome: str,
    *,
    utility: str,
    account_id: object,
    competence: object,
    invoice_id: object,
) -> None:
    """Writes one versioned metric event when launched by an instrumented Radar run."""
    task_id = os.environ.get("RADAR_TASK_ID", "").strip()
    if not os.environ.get("RADAR_RUN_ID") or not task_id or outcome not in OUTCOMES:
        return
    normalized_competence = normalize_competence(competence)
    if not normalized_competence:
        return
    payload = {
        "version": 1,
        "item_key": build_item_key(
            utility=utility, account_id=account_id, competence=normalized_competence, invoice_id=invoice_id,
        ),
        "outcome": outcome,
        "utility": utility,
        "task_id": task_id,
        "competence": normalized_competence,
    }
    print("RADAR_METRIC " + json.dumps(payload, ensure_ascii=True, sort_keys=True), flush=True)


def emit_downloaded(**kwargs) -> None:
    emit_outcome("downloaded", **kwargs)


def emit_skipped_existing(**kwargs) -> None:
    emit_outcome("skipped_existing", **kwargs)


def emit_item_error(**kwargs) -> None:
    emit_outcome("item_error", **kwargs)


def emit_other(**kwargs) -> None:
    emit_outcome("other", **kwargs)


def emit_progress(
    *,
    holder_current: int | None = None,
    holder_total: int | None = None,
    uc_current: int | None = None,
    uc_total: int | None = None,
) -> None:
    """Emits non-sensitive title-holder and UC progress for Radar runs."""
    if not os.environ.get("RADAR_RUN_ID") or not os.environ.get("RADAR_TASK_ID"):
        return
    values = (holder_current, holder_total, uc_current, uc_total)
    if any(value is not None and (not isinstance(value, int) or value < 0) for value in values):
        return
    print("RADAR_PROGRESS " + json.dumps({
        "version": 1,
        "holder_current": holder_current,
        "holder_total": holder_total,
        "uc_current": uc_current,
        "uc_total": uc_total,
    }, ensure_ascii=True, sort_keys=True), flush=True)
