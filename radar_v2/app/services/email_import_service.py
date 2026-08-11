"""Read-only importer for energia-automacao's email invoice manifest.

Design constraints (see mission brief):
  - Never writes to manifest.jsonl or imap_state.json.
  - Never connects to IMAP, never invokes WatcherV2Engine/Orbit/CONSEN.
  - Never logs/persists passwords, tokens, cookies, or full authorized URLs.
  - Idempotent: safe to re-run, safe if two syncs overlap (idempotency_key is
    UNIQUE at the DB layer; the watermark is just a read-position hint).

Classification categories (mutually exclusive, computed per manifest line):
  SUCCESS               - a real invoice file was captured
  DUPLICATE             - same file (by sha256) already captured before
  LINK_PENDING          - portal requires a follow-up click/download (never
                           the raw authorized URL itself, only a sanitized
                           reason)
  IGNORED_NON_INVOICE   - OTP/marketing/non-PDF/tariff-notice/other non-
                           invoice content; explicitly excluded from any
                           "invoice captured" count
  CORRECTION            - a metadata overlay on a previously recorded event,
                           not a new capture; recorded for audit but never
                           counted and never turned into a run/metric item
  ERROR                 - manifest line present but its outcome could not be
                           determined from any known field combination
  UNCLASSIFIED_LEGACY   - pre-classification-era rows (no result/final_result/
                           event_type field at all); treated as SUCCESS for
                           counting purposes only when a filename+saved_path
                           are present (a file really was captured), else
                           ERROR
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Iterable

SUCCESS = "SUCCESS"
DUPLICATE = "DUPLICATE"
LINK_PENDING = "LINK_PENDING"
IGNORED_NON_INVOICE = "IGNORED_NON_INVOICE"
CORRECTION = "CORRECTION"
ERROR = "ERROR"

# Categories that represent a genuinely new file-capture event and therefore
# get an entry in run_metric_items (drives the calendar/dashboard). All other
# categories are recorded in email_capture_events for audit/UI but never
# turned into a metric item, so duplicates/pending/errors/corrections never
# inflate the "downloaded" count.
_OUTCOME_FOR_CATEGORY = {
    SUCCESS: "downloaded",
    DUPLICATE: "skipped_existing",
    ERROR: "item_error",
    LINK_PENDING: "other",
}

# Fields that must never be persisted or logged anywhere downstream, even
# indirectly (e.g. inside a details_json blob). Enforced defensively in
# `_sanitized_pending_reason` and by simply never reading these fields.
_FORBIDDEN_FIELDS = {
    "password_hint", "authorized_url", "cookie", "cookies", "token",
    "edp_token", "session_cookie",
}


def classify_event(obj: dict) -> str:
    """Pure function: manifest JSON object -> one of the category constants."""
    event_type = obj.get("event_type")
    final_result = obj.get("final_result")
    result = obj.get("result")
    processing_policy = obj.get("processing_policy")
    duplicate_sha = obj.get("duplicate_sha")

    if event_type == "correction":
        return CORRECTION

    if event_type == "link_pending" or final_result == "link_pending":
        return LINK_PENDING

    if (
        final_result == "ignored_non_invoice"
        or result == "ignored_non_pdf"
        or processing_policy == "ignored_non_invoice"
    ):
        return IGNORED_NON_INVOICE

    if final_result == "duplicate_sha" or result == "duplicate_sha" or duplicate_sha is True:
        return DUPLICATE

    if final_result == "saved" or result == "ok":
        return SUCCESS

    if final_result == "pending_watcher":
        # A real FATURA captured and awaiting the separate Watcher pipeline's
        # own processing; from RADAR's read-only perspective the capture
        # itself already happened, so it counts as SUCCESS.
        return SUCCESS

    if event_type == "capture":
        return SUCCESS if obj.get("document_type", "FATURA") == "FATURA" else IGNORED_NON_INVOICE

    # Legacy, pre-classification-era rows: no result/final_result/event_type
    # field at all. If a file was clearly saved, count it. If instead the
    # row only recorded portal link candidates (no file), it is the older
    # equivalent of today's link_pending events. Anything else we can't
    # explain is recorded as ERROR for manual review.
    if not any(k in obj for k in ("event_type", "final_result", "result")):
        if obj.get("filename") and obj.get("saved_path"):
            return SUCCESS
        if obj.get("link_candidates") or obj.get("link_domains") or obj.get("link_url_sha256_list"):
            return LINK_PENDING
        return ERROR

    return ERROR


def _sanitized_pending_reason(obj: dict) -> str | None:
    """Only ever returns short, pre-sanitized human text — never a raw value
    from a forbidden field, never a URL, never a token."""
    reason = obj.get("pending_reason")
    if reason and isinstance(reason, str):
        return reason[:300]
    reason_code = obj.get("reason_code")
    if reason_code:
        # Known safe mapping to short Portuguese text; unknown codes fall
        # back to the code itself (still just a short enum-like label, never
        # a URL/token/credential).
        mapping = {
            "CPFL_RECAPTCHA_BLOCKER": "CPFL - reCAPTCHA requerido",
            "ENEL_TARIFF_ADJUSTMENT_NOTICE": "ENEL - aviso de reajuste tarifario (nao e fatura)",
        }
        return mapping.get(reason_code, str(reason_code)[:120])
    return None


def build_idempotency_key(obj: dict, line_no: int, seen_keys: set[str]) -> str:
    """Stable per-event key. Prefers the manifest's own `key` field (a
    content hash), disambiguating the rare collision with the line number so
    uniqueness is always guaranteed even across re-imports."""
    base = obj.get("key") or obj.get("message_id") or f"line-{line_no}"
    candidate = str(base)
    if candidate in seen_keys:
        candidate = f"{base}#L{line_no}"
    seen_keys.add(candidate)
    return candidate


def _provider_label(obj: dict) -> str:
    provider = obj.get("provider") or obj.get("concessionaria") or "DESCONHECIDO"
    return str(provider).strip().upper() or "DESCONHECIDO"


def _capture_date(obj: dict) -> str:
    raw = obj.get("captured_at") or obj.get("date") or ""
    raw = str(raw)
    # Accept "YYYY-MM-DD..." or full ISO timestamps; fall back to today only
    # if the manifest line is genuinely undated (should not happen).
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return raw[:10]
    try:
        return dt.datetime.fromisoformat(raw).strftime("%Y-%m-%d")
    except Exception:
        return dt.date.today().isoformat()


def iter_manifest_lines(manifest_path: Path, *, start_line: int = 0) -> Iterable[tuple[int, dict]]:
    """Yields (1-based line number, parsed object) for lines after start_line.

    Read-only: opens the file for reading only, never writes to it.
    """
    if not manifest_path.is_file():
        return
    with manifest_path.open("r", encoding="utf-8") as fh:
        for line_no, raw_line in enumerate(fh, start=1):
            if line_no <= start_line:
                continue
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                obj = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            yield line_no, obj


def sync_email_manifest(manifest_path: Path | str, *, storage_module=None, seen_keys: set[str] | None = None) -> dict:
    """Reads new manifest lines past the stored watermark and imports them.

    Idempotent: if called again with no new lines, or if an event's
    idempotency_key was already recorded, it is a no-op for that event. Never
    writes to manifest_path.
    """
    if storage_module is None:
        from radar_v2.app.repositories import storage as storage_module  # noqa: PLC0415

    manifest_path = Path(manifest_path)
    start_line = storage_module.email_sync_get_watermark()
    seen_keys = seen_keys if seen_keys is not None else set()

    counts = {SUCCESS: 0, DUPLICATE: 0, LINK_PENDING: 0, IGNORED_NON_INVOICE: 0, CORRECTION: 0, ERROR: 0}
    imported = 0
    skipped_existing = 0
    last_line_no = start_line

    for line_no, obj in iter_manifest_lines(manifest_path, start_line=start_line):
        last_line_no = line_no
        category = classify_event(obj)
        counts[category] = counts.get(category, 0) + 1

        idem_key = build_idempotency_key(obj, line_no, seen_keys)
        if storage_module.email_event_exists(idem_key):
            skipped_existing += 1
            continue

        provider = _provider_label(obj)
        captured_at_raw = obj.get("captured_at") or obj.get("date")
        date_str = _capture_date(obj)

        run_id = None
        if category in _OUTCOME_FOR_CATEGORY:
            run_id = storage_module.find_or_create_email_day_run(date_str=date_str, provider=provider)
            storage_module.upsert_run_metric_item(
                run_id,
                item_key=idem_key,
                utility=provider,
                task_id=f"email_import_{provider.lower()}",
                competence=date_str[:7],
                outcome=_OUTCOME_FOR_CATEGORY[category],
            )
            storage_module.set_run_metrics_complete(run_id, complete=True)

        storage_module.record_email_event(
            idempotency_key=idem_key,
            manifest_key=obj.get("key"),
            manifest_line_no=line_no,
            imap_uid=str(obj.get("uid") or obj.get("imap_uid") or "") or None,
            message_id=obj.get("message_id"),
            captured_at=str(captured_at_raw) if captured_at_raw else None,
            provider=provider,
            uc=obj.get("uc"),
            subject=obj.get("subject"),
            original_filename=obj.get("original_filename") or obj.get("filename"),
            normalized_name=obj.get("normalized_name"),
            sha256=obj.get("sha256"),
            document_type=obj.get("document_type"),
            category=category,
            pending_reason=_sanitized_pending_reason(obj),
            run_id=run_id,
        )
        imported += 1

    if last_line_no > start_line:
        storage_module.email_sync_set_watermark(last_line_no)

    return {
        "start_line": start_line,
        "end_line": last_line_no,
        "lines_read": max(0, last_line_no - start_line),
        "imported": imported,
        "already_present": skipped_existing,
        "by_category": counts,
    }
