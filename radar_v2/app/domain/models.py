"""
Entidades centrais do domínio Radar V2.

Regras:
- Sem dependência de Flask, SQLite ou qualquer infra aqui.
- Toda lógica de estado de Run vive neste módulo.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RunStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    SUCCESS   = "success"
    ERROR     = "error"
    STOPPED   = "stopped"


class RunOrigin(str, Enum):
    MANUAL    = "manual"
    SCHEDULED = "scheduled"
    PRESET    = "preset"
    RERUN     = "rerun"


class ScheduleFrequency(str, Enum):
    DAILY   = "daily"
    WEEKLY  = "weekly"
    MONTHLY = "monthly"


@dataclass(slots=True)
class Task:
    task_id:   str
    name:      str
    category:  str
    script:    str
    notes:     str = ""
    supports_month_year:   bool = False
    supports_type:         bool = False
    supports_stage_flags:  bool = False
    supports_pasta:        bool = False
    pasta_template:        str  = ""
    download_condition_options: list[str] = field(default_factory=list)
    default_type: str = "ambos"
    extra_args:   list[str] = field(default_factory=list)


@dataclass(slots=True)
class RunParams:
    month:              str = ""
    year:               str = ""
    selected_type:      str = ""
    stage_flag:         str = ""
    pasta:              str = ""
    download_condition: str = ""
    extra_text:         str = ""
    use_current_date:   bool = False


@dataclass(slots=True)
class Run:
    run_id:     int
    task_id:    str
    task_name:  str
    category:   str
    command:    list[str]
    params:     RunParams
    origin:     RunOrigin
    status:     RunStatus
    started_at: dt.datetime
    ended_at:   dt.datetime | None = None
    exit_code:  int | None = None
    pid:        int | None = None
    log_path:   str = ""

    # Máquina de estados
    _TRANSITIONS: dict[RunStatus, set[RunStatus]] = field(default_factory=lambda: {
        RunStatus.PENDING:  {RunStatus.RUNNING},
        RunStatus.RUNNING:  {RunStatus.SUCCESS, RunStatus.ERROR, RunStatus.STOPPED},
        RunStatus.SUCCESS:  set(),
        RunStatus.ERROR:    set(),
        RunStatus.STOPPED:  set(),
    }, compare=False, repr=False)

    def transition(self, new_status: RunStatus) -> None:
        allowed = self._TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise ValueError(f"Transição inválida: {self.status} → {new_status}")
        self.status = new_status
        if new_status in (RunStatus.SUCCESS, RunStatus.ERROR, RunStatus.STOPPED):
            self.ended_at = dt.datetime.now()

    @property
    def duration_seconds(self) -> float | None:
        if self.ended_at:
            return (self.ended_at - self.started_at).total_seconds()
        return None


@dataclass(slots=True)
class Schedule:
    schedule_id:  int
    label:        str
    task_id:      str
    task_name:    str
    category:     str
    params:       RunParams
    frequency:    ScheduleFrequency
    time_of_day:  str          # "HH:MM"
    day_of_week:  int | None   # 0=segunda … 6=domingo
    day_of_month: int | None   # 1–28
    enabled:      bool
    next_run_at:  dt.datetime | None = None
    last_run_at:  dt.datetime | None = None
    last_status:  RunStatus | None   = None
