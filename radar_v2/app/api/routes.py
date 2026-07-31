"""
Rotas Flask V2 — API completa.

Compatível com o frontend (drawer usa: log, start_line, next_line, total_lines, is_running, status_text).
"""
from __future__ import annotations

import datetime as dt
import json

from flask import Blueprint, current_app, jsonify, request

bp = Blueprint("api_v2", __name__, url_prefix="/api")


def _run()  : return current_app.extensions["run_service"]
def _cat()  : return current_app.extensions["task_catalog"]
def _sched(): return current_app.extensions["schedule_service"]


# ── health ────────────────────────────────────────────────────────────────────

@bp.get("/health")
def health():
    return jsonify({"ok": True, "ts": dt.datetime.now().isoformat()})


@bp.get("/session")
def session_status():
    from flask import session
    return jsonify({"ok": True, "authenticated": bool(session.get("authenticated"))})


# ── tasks ─────────────────────────────────────────────────────────────────────

@bp.get("/tasks")
def list_tasks():
    cat = _cat()
    return jsonify({"ok": True, "tasks": {
        grp: [cat.to_dict(t) for t in tasks]
        for grp, tasks in cat.by_category().items()
    }})


# ── runs ──────────────────────────────────────────────────────────────────────

@bp.get("/dashboard")
def dashboard():
    return jsonify(_run().dashboard())


@bp.get("/calendar/summary")
def calendar_summary():
    """Expose an explicit empty state until invoice metrics are imported.

    Missing invoice evidence is deliberately not represented as completed or
    zero-valued processing. The React calendar uses ``has_metrics`` to render
    an honest empty state for a partially populated competence.
    """
    try:
        start = dt.date.fromisoformat(request.args.get("start", ""))
        end = dt.date.fromisoformat(request.args.get("end", ""))
    except ValueError:
        return jsonify({"ok": False, "error": "Datas devem usar YYYY-MM-DD"}), 400
    if end < start or (end - start).days > 370:
        return jsonify({"ok": False, "error": "Intervalo de competencia invalido"}), 400
    days = []
    current = start
    while current <= end:
        days.append({
            "date": current.isoformat(), "downloaded": 0, "skipped_existing": 0,
            "errors": 0, "other": 0, "processed": 0, "metrics_complete": False,
        })
        current += dt.timedelta(days=1)
    return jsonify({
        "ok": True, "start": start.isoformat(), "end": end.isoformat(),
        "timezone": "America/Sao_Paulo", "has_metrics": False,
        "metrics_complete": False, "days": days, "utilities": [],
        "totals": {"downloaded": 0, "skipped_existing": 0, "errors": 0, "other": 0, "processed": 0},
    })


@bp.get("/runs/live")
def live_runs():
    status_filter = request.args.get("status") or "Todas"
    return jsonify({"runs": _run().list_live(status_filter=status_filter)})


@bp.get("/runs/history")
def history():
    limit = min(int(request.args.get("limit") or 150), 500)
    return jsonify({"runs": _run().list_history(limit=limit)})


@bp.post("/runs/start")
def start_run():
    payload = request.get_json(force=True, silent=True) or {}
    cat = _cat()
    svc = _run()
    task = cat.get(payload.get("task_id",""))
    if task is None:
        return jsonify({"ok": False, "error": "Tarefa não encontrada"}), 400
    today = dt.date.today()
    try:
        _, args, label = svc.build_args(
            task,
            month=payload.get("month") or f"{today.month:02d}",
            year=payload.get("year") or str(today.year),
            selected_type=payload.get("selected_type") or task.default_type or "",
            stage_flag=payload.get("stage_flag") or "",
            pasta=payload.get("pasta") or "",
            download_condition=payload.get("download_condition") or "",
            extra_text=payload.get("extra_text") or "",
        )
        run = svc.launch(task.task_id, task.name, task.category, args)
        return jsonify({"ok": True, "run": run.to_dict()})
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.post("/runs/<int:run_id>/stop")
def stop_run(run_id: int):
    try:
        _run().stop(run_id)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.post("/runs/<int:run_id>/rerun")
def rerun_run(run_id: int):
    try:
        run = _run().rerun(run_id)
        return jsonify({"ok": True, "run": run.to_dict()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.get("/runs/<int:run_id>/log")
def run_log(run_id: int):
    try:
        after    = int(request.args.get("after") or 0)
        max_lines = min(int(request.args.get("max_lines") or 1200), 5000)
    except ValueError:
        after, max_lines = 0, 1200
    payload = _run().get_log(run_id, after_line=max(0, after), max_lines=max(100, max_lines))
    payload["run_id"] = run_id
    return jsonify(payload)


# ── schedules ─────────────────────────────────────────────────────────────────

_DOW_LABELS = ["Segunda","Terça","Quarta","Quinta","Sexta","Sábado","Domingo"]


def _fmt_next(next_run_at: str | None) -> str:
    if not next_run_at:
        return "-"
    try:
        nxt = dt.datetime.strptime(next_run_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return next_run_at
    now   = dt.datetime.now()
    today = now.date()
    d     = nxt.date()
    t     = nxt.strftime("%H:%M")
    if d == today:
        return f"Hoje às {t}" if nxt > now else f"Hoje às {t} (atrasado)"
    if d == today + dt.timedelta(days=1):
        return f"Amanhã às {t}"
    diff = (d - today).days
    if diff <= 6:
        return f"{_DOW_LABELS[nxt.weekday()]} às {t}"
    return nxt.strftime("%d/%m") + f" às {t}"


def _serialize_sched(s: dict) -> dict:
    freq = s.get("frequency","")
    dow  = s.get("day_of_week")
    dom  = s.get("day_of_month")
    if freq == "weekly" and dow is not None:
        freq_label = f"Semanal ({_DOW_LABELS[int(dow) % 7]})"
    elif freq == "monthly" and dom is not None:
        freq_label = f"Mensal (dia {dom})"
    else:
        freq_label = "Diário"
    return {
        **s,
        "freq_label":     freq_label,
        "next_run_label": _fmt_next(s.get("next_run_at")),
    }


@bp.get("/schedules")
def list_schedules():
    from ..repositories.storage import list_schedules as _ls
    return jsonify({"ok": True, "schedules": [_serialize_sched(s) for s in _ls()]})


@bp.post("/schedules")
def create_schedule():
    from ..repositories.storage import create_schedule as _cs
    payload = request.get_json(force=True, silent=True) or {}
    task = _cat().get(payload.get("task_id",""))
    if task is None:
        return jsonify({"ok": False, "error": "Tarefa não encontrada"}), 400
    today = dt.date.today()
    params = {
        "month": payload.get("month") or f"{today.month:02d}",
        "year":  payload.get("year") or str(today.year),
        "selected_type": payload.get("selected_type") or task.default_type or "",
        "stage_flag":    payload.get("stage_flag") or "",
        "pasta":         payload.get("pasta") or "",
        "download_condition": payload.get("download_condition") or "",
        "extra_text":    payload.get("extra_text") or "",
        "use_current_date": bool(payload.get("use_current_date")),
    }
    try:
        sched_id = _cs(
            label=payload.get("label") or task.name,
            task_id=task.task_id,
            task_name=task.name,
            category=task.category,
            params_json=json.dumps(params),
            frequency=payload.get("frequency") or "daily",
            time_of_day=payload.get("time_of_day") or "08:00",
            day_of_week=int(payload["day_of_week"]) if payload.get("day_of_week") is not None else None,
            day_of_month=int(payload["day_of_month"]) if payload.get("day_of_month") is not None else None,
        )
        from ..repositories.storage import list_schedules as _ls
        all_s = [_serialize_sched(s) for s in _ls()]
        created = next((s for s in all_s if s["id"] == sched_id), None)
        return jsonify({"ok": True, "schedule": created})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.post("/schedules/<int:schedule_id>/toggle")
def toggle_schedule(schedule_id: int):
    from ..repositories.storage import toggle_schedule as _tg
    payload = request.get_json(force=True, silent=True) or {}
    _tg(schedule_id, bool(payload.get("enabled", True)))
    return jsonify({"ok": True})


@bp.delete("/schedules/<int:schedule_id>")
def delete_schedule(schedule_id: int):
    from ..repositories.storage import delete_schedule as _del
    _del(schedule_id)
    return jsonify({"ok": True})


# ── presets ───────────────────────────────────────────────────────────────────

@bp.post("/schedules/presets/neoenergia-21h")
def preset_neoenergia():
    from ..services.schedule_service import apply_neoenergia_21h
    try:
        result = apply_neoenergia_21h(_cat())
        from ..repositories.storage import list_schedules as _ls
        return jsonify({"ok": True, "result": result,
                        "schedules": [_serialize_sched(s) for s in _ls()]})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.post("/schedules/presets/celesc-pipelines")
def preset_celesc():
    from ..services.schedule_service import apply_celesc_pipelines
    try:
        result = apply_celesc_pipelines(_cat())
        from ..repositories.storage import list_schedules as _ls
        return jsonify({"ok": True, "result": result,
                        "schedules": [_serialize_sched(s) for s in _ls()]})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.post("/schedules/presets/light-madrugada")
def preset_light_madrugada():
    from ..services.schedule_service import apply_light_madrugada
    try:
        result = apply_light_madrugada(_cat())
        from ..repositories.storage import list_schedules as _ls
        return jsonify({"ok": True, "result": result,
                        "schedules": [_serialize_sched(s) for s in _ls()]})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
