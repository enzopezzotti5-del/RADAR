"""
ScheduleService V2 — scheduler em thread + presets do v1.

Roda como thread daemon dentro do processo web (idêntico ao v1).
Pode ser extraído para processo separado futuramente sem mudança de interface.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time

from ..repositories.storage import (
    defer_schedule_after_preflight,
    list_schedules,
    update_schedule_after_run,
    upsert_schedule,
    compute_next_run,
    delete_schedule as _delete_by_id,
)
from .run_service import RunConflictError
from .preflight_service import TaskPreflightError

log = logging.getLogger("radar_v2.scheduler")

POLL_INTERVAL = 60  # segundos — mesmo que v1


class ScheduleService:
    def __init__(self, run_service, catalog_service) -> None:
        self._run_svc = run_service
        self._catalog = catalog_service

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="radar_v2_scheduler").start()

    def _loop(self) -> None:
        while True:
            try:
                self._check_due()
            except Exception:
                log.exception("Erro no loop do scheduler")
            time.sleep(POLL_INTERVAL)

    def _check_due(self) -> None:
        now = dt.datetime.now()
        for sched in list_schedules():
            if not sched.get("enabled"):
                continue
            try:
                next_run = dt.datetime.fromisoformat(sched["next_run_at"])
            except (ValueError, KeyError, TypeError):
                continue
            if now >= next_run:
                self._fire(sched, now)

    def _fire(self, sched: dict, now: dt.datetime) -> None:
        fired = False
        try:
            params = json.loads(sched.get("params_json") or "{}")
            if params.get("use_current_date"):
                params["month"] = f"{now.month:02d}"
                params["year"]  = str(now.year)

            task = self._catalog.get(sched["task_id"])
            if task is None:
                log.warning("Schedule %s: tarefa '%s' não encontrada", sched.get("id"), sched.get("task_id"))
                return

            _, args, label = self._run_svc.build_args(
                task,
                month=params.get("month") or f"{now.month:02d}",
                year=params.get("year") or str(now.year),
                selected_type=params.get("selected_type") or "ambos",
                stage_flag=params.get("stage_flag") or "",
                pasta=params.get("pasta") or "",
                download_condition=params.get("download_condition") or "",
                extra_text=params.get("extra_text") or "",
            )
            self._run_svc.launch(
                sched["task_id"], f"[Auto] {label}", sched["category"], args,
            )
            fired = True
        except RunConflictError as exc:
            log.warning("Schedule %s aguardando janela livre: %s", sched.get("id"), exc)
        except TaskPreflightError as exc:
            log.error(
                "PREFLIGHT_BLOCKED schedule=%s task=%s status=%s requirements=%s",
                sched.get("id"), sched.get("task_id"), exc.result.status,
                [issue.requirement for issue in exc.result.issues],
            )
            defer_schedule_after_preflight(sched["id"])
        except Exception:
            log.exception("Falha ao disparar schedule %s (%s)", sched.get("id"), sched.get("task_id"))

        if fired:
            update_schedule_after_run(sched["id"], now.strftime("%Y-%m-%d %H:%M:%S"))


# ── Presets (portados do schedule_presets.py v1) ──────────────────────────────

from dataclasses import dataclass


@dataclass(frozen=True)
class ScheduleSpec:
    task_id:      str
    label:        str
    time_of_day:  str
    frequency:    str = "daily"
    day_of_week:  int | None = None
    day_of_month: int | None = None
    selected_type: str = "ambos"
    stage_flag:   str = ""
    pasta:        str = ""
    download_condition: str = ""
    extra_text:   str = ""
    use_current_date: bool = True

    def params_json(self) -> str:
        today = dt.date.today()
        return json.dumps({
            "month": f"{today.month:02d}",
            "year":  str(today.year),
            "selected_type": self.selected_type,
            "stage_flag":    self.stage_flag,
            "pasta":         self.pasta,
            "download_condition": self.download_condition,
            "extra_text":    self.extra_text,
            "use_current_date": self.use_current_date,
        }, ensure_ascii=False)


NEOENERGIA_21H = [
    ScheduleSpec("dl_neo_celpe",        "Downloader Neoenergia CELPE",           "21:00"),
    ScheduleSpec("dl_neo_elektro",      "Downloader Neoenergia ELEKTRO",         "01:45"),
    ScheduleSpec("orq_coelba",          "Orquestrador COELBA completo",          "05:45"),
    ScheduleSpec("dl_neo_cosern",       "Downloader Neoenergia COSERN",          "07:15"),
    ScheduleSpec("pl_neo_pernambuco",   "Pipeline Neoenergia Pernambuco BT",     "08:15", selected_type="bt"),
    ScheduleSpec("pl_neo_elektro",      "Pipeline Neoenergia Elektro BT",        "08:35", selected_type="bt"),
    ScheduleSpec("pl_neo_cosern",       "Pipeline Neoenergia COSERN BT",         "09:15", selected_type="bt"),
]

NEOENERGIA_OBSOLETOS = {"dl_neo_todos", "dl_neo_coelba", "pl_neo_bahia"}

CELESC_PIPELINES = [
    ScheduleSpec("pl_celesc_bt", "Pipeline CELESC BT", "08:10"),
    ScheduleSpec("pl_celesc_mt", "Pipeline CELESC MT", "08:30"),
]

LIGHT_MADRUGADA = [
    ScheduleSpec("dl_light_rj", "Downloader Light RJ", "02:25", use_current_date=False),
]


def _apply_preset(specs: list[ScheduleSpec], obsoletos: set[str],
                  catalog) -> dict:
    tasks = {t.task_id: t for t in catalog.all()}
    missing = [s.task_id for s in specs if s.task_id not in tasks]
    if missing:
        raise ValueError(f"Tarefas ausentes no catálogo: {', '.join(missing)}")

    from ..repositories.storage import DB_PATH
    import sqlite3
    with sqlite3.connect(DB_PATH) as conn:
        for tid in sorted(obsoletos):
            conn.execute("DELETE FROM schedules WHERE task_id=?", (tid,))
        conn.commit()

    applied = []
    for spec in specs:
        task = tasks[spec.task_id]
        schedule_id = upsert_schedule(
            task_id=spec.task_id,
            label=spec.label,
            task_name=task.name,
            category=task.category,
            params_json=spec.params_json(),
            frequency=spec.frequency,
            time_of_day=spec.time_of_day,
            day_of_week=spec.day_of_week,
            day_of_month=spec.day_of_month,
        )
        applied.append({
            "id":          schedule_id,
            "task_id":     spec.task_id,
            "label":       spec.label,
            "time_of_day": spec.time_of_day,
            "next_run_at": compute_next_run(spec.frequency, spec.time_of_day),
        })

    return {"removed_task_ids": sorted(obsoletos), "applied": applied}


def apply_neoenergia_21h(catalog) -> dict:
    return _apply_preset(NEOENERGIA_21H, NEOENERGIA_OBSOLETOS, catalog)


def apply_celesc_pipelines(catalog) -> dict:
    return _apply_preset(CELESC_PIPELINES, set(), catalog)


def apply_light_madrugada(catalog) -> dict:
    return _apply_preset(LIGHT_MADRUGADA, set(), catalog)
