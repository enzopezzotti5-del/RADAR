"""DTOs de request/response para runs e tasks."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RunStartRequest(BaseModel):
    task_id:            str
    month:              str = ""
    year:               str = ""
    selected_type:      str = ""
    stage_flag:         str = ""
    pasta:              str = ""
    download_condition: str = ""
    extra_text:         str = ""


class RunResponse(BaseModel):
    run_id:    int
    task_id:   str
    task_name: str
    category:  str
    status:    str
    origin:    str
    started_at: str
    ended_at:   str | None
    duration_seconds: float | None
    pid:       int | None


class LogResponse(BaseModel):
    run_id:      int
    lines:       list[str]
    total_lines: int
    after_line:  int
    done:        bool


class TaskResponse(BaseModel):
    task_id:    str
    name:       str
    category:   str
    script:     str
    exists:     bool
    notes:      str
    supports_month_year:  bool
    supports_type:        bool
    supports_stage_flags: bool
    supports_pasta:       bool
    default_type: str
    download_condition_options: list[str]
