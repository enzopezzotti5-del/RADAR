"""Publisher fail-open para a outbox filesystem Radar -> Orbit."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

import pdfplumber


PROJECT_ROOT = Path(__file__).resolve().parents[4]
TASK_UTILITY = {
    "dl_copel_bt": "COPEL",
    "dl_enel_sp": "ENEL",
    "dl_neo_coelba": "NEOENERGIA/COELBA",
    "dl_neo_celpe": "NEOENERGIA/CELPE",
    "dl_neo_cosern": "NEOENERGIA/COSERN",
    "dl_neo_elektro": "NEOENERGIA/ELEKTRO",
    "dl_celesc_mt": "CELESC",
    "dl_celesc_bt": "CELESC",
    "dl_light_rj": "LIGHT",
    "dl_cpfl_bt": "CPFL",
    "dl_cemig": "CEMIG",
}
_TEMP_SUFFIXES = (".part", ".tmp", ".crdownload")


def _enabled() -> bool:
    return os.environ.get("RADAR_ORBIT_HANDOFF_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _root() -> Path:
    configured = os.environ.get("RADAR_ORBIT_HANDOFF_ROOT", "").strip()
    return Path(configured) if configured else PROJECT_ROOT / "handoff" / "orbit"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_pdf(path: Path) -> tuple[str, int]:
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise ValueError("arquivo final nao e PDF")
    if path.name.lower().endswith(_TEMP_SUFFIXES):
        raise ValueError("arquivo temporario")
    before = path.stat()
    settle = float(os.environ.get("RADAR_ORBIT_HANDOFF_SETTLE_SECONDS", "0.05"))
    if settle > 0:
        time.sleep(settle)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("arquivo ainda esta sendo alterado")
    if after.st_size < int(os.environ.get("RADAR_ORBIT_HANDOFF_MIN_BYTES", "128")):
        raise ValueError("PDF abaixo do tamanho minimo")
    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise ValueError("assinatura PDF ausente")
        handle.seek(-min(after.st_size, 4096), os.SEEK_END)
        if b"%%EOF" not in handle.read(min(after.st_size, 4096)):
            raise ValueError("marcador EOF ausente")
    with pdfplumber.open(path) as pdf:
        if not pdf.pages:
            raise ValueError("PDF sem paginas")
    return _sha256(path), after.st_size


def _event(*, task_id: str, utility: str, status: str, run_id: str, handoff_id: str = "", file_hash: str = "", error: str = "") -> None:
    payload = {
        "event": "HANDOFF_EVENT", "handoff_id": handoff_id,
        "run_id": run_id, "task_id": task_id, "utility": utility,
        "status": status, "timestamp": dt.datetime.now().astimezone().isoformat(),
        "file_hash": file_hash[:12],
    }
    if error:
        payload["error"] = error
    print("HANDOFF_EVENT " + json.dumps(payload, ensure_ascii=True, separators=(",", ":")), flush=True)


def request_orbit_handoff(
    path: str | Path,
    *,
    task_id: str,
    utility: str,
    run_id: str | int | None = None,
) -> dict[str, object]:
    """Preserva o original e nunca propaga falha para o downloader."""
    context_run = str(run_id or os.environ.get("RADAR_RUN_ID", ""))
    utility_norm = utility.strip().upper()
    if not _enabled():
        return {"ok": True, "disabled": True}
    try:
        expected = TASK_UTILITY.get(task_id)
        if expected is None or expected != utility_norm:
            raise ValueError("task/utility fora da allowlist")
        source = Path(path)
        file_hash, size = _validate_pdf(source)
        handoff_id = hashlib.sha256(f"{utility_norm}\0{file_hash}".encode("utf-8")).hexdigest()
        outbox = _root() / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        final_pdf = outbox / f"{handoff_id}.pdf"
        final_json = outbox / f"{handoff_id}.json"
        if final_pdf.exists() and final_json.exists():
            if _sha256(final_pdf) != file_hash or final_pdf.stat().st_size != size:
                raise ValueError("handoff existente diverge da origem")
            return {"ok": True, "already_staged": True, "handoff_id": handoff_id}
        token = uuid.uuid4().hex
        temp_pdf = outbox / f".{handoff_id}.{token}.pdf.part"
        temp_json = outbox / f".{handoff_id}.{token}.json.tmp"
        try:
            shutil.copy2(source, temp_pdf)
            if temp_pdf.stat().st_size != size or _sha256(temp_pdf) != file_hash:
                raise ValueError("copia da outbox divergiu")
            os.replace(temp_pdf, final_pdf)
            envelope = {
                "contract_version": 1, "handoff_id": handoff_id,
                "file_hash": file_hash, "source_path": str(source),
                "source_name": re.sub(r"[^A-Za-z0-9._-]+", "_", source.name),
                "source_size": size, "task_id": task_id,
                "utility": utility_norm, "run_id": context_run,
                "created_at": dt.datetime.now().astimezone().isoformat(),
            }
            temp_json.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp_json, final_json)
        finally:
            temp_pdf.unlink(missing_ok=True)
            temp_json.unlink(missing_ok=True)
        _event(task_id=task_id, utility=utility_norm, status="READY", run_id=context_run,
               handoff_id=handoff_id, file_hash=file_hash)
        return {"ok": True, "already_staged": False, "handoff_id": handoff_id}
    except Exception as exc:
        _event(task_id=task_id, utility=utility_norm, status="ERROR", run_id=context_run,
               error=type(exc).__name__)
        return {"ok": False, "error": type(exc).__name__}
