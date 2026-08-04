"""Consolidated, read-only downloader readiness and health view."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass

from ..repositories.storage import list_runs, list_schedules


@dataclass(frozen=True)
class DownloaderPolicy:
    resource_group: str
    max_retries: int = 1
    retry_interval_seconds: int = 900
    timeout_seconds: int = 14400
    max_instances: int = 1
    consecutive_failure_threshold: int = 3


GROUPS = {
    "dl_cpfl": "CPFL_RGE", "dl_rge": "CPFL_RGE", "dl_neo": "NEOENERGIA",
    "dl_enel": "ENEL", "dl_copel": "COPEL", "dl_celesc": "CELESC",
    "dl_light": "LIGHT", "dl_cemig": "CEMIG", "dl_equatorial": "EQUATORIAL",
}


def policy_for(task_id: str) -> DownloaderPolicy:
    group = next((value for prefix, value in GROUPS.items() if task_id.startswith(prefix)), "OTHER")
    timeout = 28800 if group in {"CPFL_RGE", "NEOENERGIA"} else 14400
    return DownloaderPolicy(resource_group=group, timeout_seconds=timeout)


class DownloaderHealthService:
    def __init__(self, catalog, preflight) -> None:
        self.catalog = catalog
        self.preflight = preflight

    def report(self) -> dict:
        schedules = defaultdict(list)
        for row in list_schedules():
            schedules[row["task_id"]].append(row)
        histories = defaultdict(list)
        for row in list_runs(limit=2000):
            histories[row["task_id"]].append(row)

        items = []
        for task in self.catalog.all():
            if not task.task_id.startswith("dl_"):
                continue
            preflight = self.preflight.check_task(task)
            runs = histories.get(task.task_id, [])
            last = runs[0] if runs else None
            last_success = next((r for r in runs if r.get("status") == "success"), None)
            failures = 0
            for run in runs:
                if run.get("status") != "error":
                    break
                failures += 1
            task_schedules = schedules.get(task.task_id, [])
            enabled = any(bool(s.get("enabled")) for s in task_schedules)
            next_runs = sorted(s["next_run_at"] for s in task_schedules if s.get("enabled") and s.get("next_run_at"))
            autonomous_ready = preflight.ready and bool(task_schedules)
            if not preflight.ready:
                health = "BLOCKED_EXTERNAL" if preflight.status == "BLOCKED_EXTERNAL" else "FAILED"
            elif failures:
                health = "DEGRADED"
            elif last and last.get("status") == "skipped":
                health = "NO_INPUT"
            elif last_success:
                health = "HEALTHY"
            elif autonomous_ready:
                health = "READY_UNOBSERVED"
            else:
                health = "DISABLED"
            items.append({
                "task_id": task.task_id, "utility": task.name.removeprefix("Downloader "),
                "enabled": enabled,
                "preflight_status": preflight.to_dict()["official_status"],
                "last_run": last.get("started_at") if last else None,
                "last_status": last.get("status") if last else None,
                "last_success": last_success.get("finished_at") if last_success else None,
                "last_valid_download": None, "consecutive_failures": failures,
                "next_run": next_runs[0] if next_runs else None,
                "block_reason": "; ".join(i.requirement for i in preflight.issues) or None,
                "autonomous_ready": autonomous_ready,
                "production_observed": bool(last_success), "health": health,
                "policy": asdict(policy_for(task.task_id)),
            })
        return {"total": len(items), "downloaders": items}
