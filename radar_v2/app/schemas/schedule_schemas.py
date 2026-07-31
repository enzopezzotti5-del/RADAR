"""DTOs de request/response para schedules."""
from __future__ import annotations

from pydantic import BaseModel


class ScheduleCreateRequest(BaseModel):
    label:              str = ""
    task_id:            str
    month:              str = ""
    year:               str = ""
    selected_type:      str = ""
    stage_flag:         str = ""
    pasta:              str = ""
    download_condition: str = ""
    extra_text:         str = ""
    use_current_date:   bool = False
    frequency:          str = "daily"   # daily | weekly | monthly
    time_of_day:        str = "08:00"
    day_of_week:        int | None = None
    day_of_month:       int | None = None


class ScheduleResponse(BaseModel):
    schedule_id:   int
    label:         str
    task_id:       str
    task_name:     str
    category:      str
    frequency:     str
    freq_label:    str
    time_of_day:   str
    enabled:       bool
    next_run_at:   str | None
    next_run_label: str
    last_run_at:   str | None
    last_status:   str | None
