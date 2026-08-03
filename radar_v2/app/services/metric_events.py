"""Validation protocol for structured operational events from child processes."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re


METRIC_PREFIX = "RADAR_METRIC "
PROGRESS_PREFIX = "RADAR_PROGRESS "
METRIC_VERSION = 1
VALID_OUTCOMES = frozenset({
    "downloaded", "processed", "skipped_existing", "item_error", "other",
})
_COMPETENCE_RE = re.compile(r"^\d{4}-\d{2}$")
_ITEM_KEY_RE = re.compile(r"^invoice:[0-9a-f]{64}$")


@dataclass(frozen=True)
class MetricEvent:
    item_key: str
    outcome: str
    utility: str
    task_id: str
    competence: str
    version: int = METRIC_VERSION


@dataclass(frozen=True)
class ProgressEvent:
    holder_current: int | None
    holder_total: int | None
    uc_current: int | None
    uc_total: int | None


def parse_metric_event(line: str) -> MetricEvent | None:
    text = str(line or "").strip()
    if not text.startswith(METRIC_PREFIX):
        return None
    try:
        payload = json.loads(text[len(METRIC_PREFIX):])
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != METRIC_VERSION:
        return None
    item_key = str(payload.get("item_key") or "").strip()
    outcome = str(payload.get("outcome") or "").strip()
    utility = str(payload.get("utility") or "").strip()
    task_id = str(payload.get("task_id") or "").strip()
    competence = str(payload.get("competence") or "").strip()
    if not _ITEM_KEY_RE.fullmatch(item_key) or outcome not in VALID_OUTCOMES:
        return None
    if not utility or not task_id or not _COMPETENCE_RE.fullmatch(competence):
        return None
    return MetricEvent(item_key, outcome, utility, task_id, competence)


def parse_progress_event(line: str) -> ProgressEvent | None:
    text = str(line or "").strip()
    if not text.startswith(PROGRESS_PREFIX):
        return None
    try:
        payload = json.loads(text[len(PROGRESS_PREFIX):])
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != METRIC_VERSION:
        return None
    values = tuple(payload.get(name) for name in (
        "holder_current", "holder_total", "uc_current", "uc_total",
    ))
    if any(value is not None and (not isinstance(value, int) or value < 0)
           for value in values):
        return None
    holder_current, holder_total, uc_current, uc_total = values
    if holder_current is not None and holder_total is not None and holder_current > holder_total:
        return None
    if uc_current is not None and uc_total is not None and uc_current > uc_total:
        return None
    return ProgressEvent(holder_current, holder_total, uc_current, uc_total)
