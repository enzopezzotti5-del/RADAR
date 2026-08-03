from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from radar_v2.app.services import preflight_service
from radar_v2.app.services.preflight_service import (
    PreflightIssue,
    PreflightResult,
    PreflightService,
    TaskPreflightError,
)
from radar_v2.app.services.run_service import ROOT_DIR, RunService


def test_project_root_is_derived_from_source_not_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    assert preflight_service.PROJECT_ROOT == ROOT_DIR
    assert preflight_service.ENV_FILE == ROOT_DIR / ".env"


def test_legacy_launcher_python_is_derived_from_project_root():
    import config

    assert Path(config.PYTHON_EXE) == ROOT_DIR / ".venv" / "Scripts" / "python.exe"


def test_server_loads_project_env_when_started_from_other_cwd(tmp_path: Path):
    code = (
        "import os; from radar_v2.app.api.server import PROJECT_ROOT; "
        "raise SystemExit(0 if PROJECT_ROOT.name == 'Radar' and os.environ.get('RADAR_V2_SECRET_KEY') else 2)"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT_DIR)
    result = subprocess.run(
        [str(ROOT_DIR / ".venv" / "Scripts" / "python.exe"), "-c", code],
        cwd=tmp_path, env=env, timeout=60,
    )
    assert result.returncode == 0


def test_parent_environment_is_preserved_for_subprocess(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RADAR_TEST_PROPAGATION", "configured")
    child_env = RunService._env()
    assert child_env["RADAR_TEST_PROPAGATION"] == "configured"
    assert child_env["PYTHONPATH"].split(os.pathsep)[0] == str(ROOT_DIR)


def test_missing_library_blocks_task(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    script = tmp_path / "core" / "downloaders" / "cemig" / "cemig.py"
    script.parent.mkdir(parents=True)
    script.touch()
    monkeypatch.setattr(preflight_service, "_browser_path", lambda: tmp_path / "chrome.exe")
    monkeypatch.setattr(preflight_service, "_module_missing", lambda name: name == "pandas")
    task = SimpleNamespace(task_id="dl_cemig", script="core/downloaders/cemig/cemig.py")
    result = PreflightService(project_root=tmp_path).check_task(task)
    assert result.status == "BLOCKED_MISSING_LIBRARY"
    assert [issue.requirement for issue in result.issues] == ["pandas"]


def test_library_check_imports_module_instead_of_only_locating_it(monkeypatch: pytest.MonkeyPatch):
    def broken_import(name: str):
        raise ModuleNotFoundError("transitive binary missing", name="_cffi_backend")

    monkeypatch.setattr(preflight_service.importlib, "import_module", broken_import)
    assert preflight_service._module_missing("cryptography") is True


def test_missing_entrypoint_has_explicit_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(preflight_service, "_browser_path", lambda: tmp_path / "chrome.exe")
    monkeypatch.setattr(preflight_service, "_module_missing", lambda _name: False)
    task = SimpleNamespace(task_id="dl_cemig", script="missing.py")
    result = PreflightService(project_root=tmp_path).check_task(task)
    assert result.status == "BLOCKED_MISSING_FILE"
    assert result.issues[0].requirement == "entrypoint"


def test_missing_required_environment_is_reported_by_name_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("RADAR_V2_SECRET_KEY", raising=False)
    monkeypatch.setattr(preflight_service, "GLOBAL_REQUIRED_MODULES", ())
    monkeypatch.setattr(preflight_service, "_browser_path", lambda: tmp_path / "chrome.exe")
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    (tmp_path / ".env").touch()
    log_dir = tmp_path / "logs" / "web_app" / "run_logs"
    log_dir.mkdir(parents=True)
    db = tmp_path / "logs" / "web_app" / "history.sqlite3"
    import sqlite3
    sqlite3.connect(db).close()

    report = PreflightService(project_root=tmp_path).global_report()
    assert report["status"] == "BLOCKED_MISSING_ENV"
    assert report["missing_required_keys"] == ["RADAR_V2_SECRET_KEY"]


def test_operator_can_block_known_bad_task_without_blocking_global_scheduler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    script = tmp_path / "light.py"
    script.touch()
    monkeypatch.setenv("RADAR_V2_BLOCKED_TASKS", "dl_light_rj")
    monkeypatch.setattr(preflight_service, "_browser_path", lambda: tmp_path / "chrome.exe")
    monkeypatch.setattr(preflight_service, "_module_missing", lambda _name: False)
    task = SimpleNamespace(task_id="dl_light_rj", script="light.py")

    result = PreflightService(project_root=tmp_path).check_task(task)
    assert result.status == "BLOCKED_OTHER"
    assert result.issues[0].requirement == "operator_block"


def test_copel_pipeline_missing_month_directory_is_blocked_before_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    script = tmp_path / "pipeline.py"
    script.touch()
    monkeypatch.setattr(preflight_service, "_module_missing", lambda _name: False)
    task = SimpleNamespace(task_id="pl_copel_bt", script="pipeline.py")
    service = PreflightService(project_root=tmp_path)
    missing = tmp_path / "08.2026" / "BT"
    args = [str(python), "-m", "pipeline", "--mes", "08", "--ano", "2026", "--pasta", str(missing)]

    result = service.check_task(task, args=args)

    assert result.status == "BLOCKED_MISSING_FILE"
    assert result.issues[0].requirement == "pipeline_input_dir"


def test_copel_pipeline_existing_explicit_directory_is_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    script = tmp_path / "pipeline.py"
    script.touch()
    input_dir = tmp_path / "07.2026" / "BT"
    input_dir.mkdir(parents=True)
    monkeypatch.setattr(preflight_service, "_module_missing", lambda _name: False)
    task = SimpleNamespace(task_id="pl_copel_bt", script="pipeline.py")
    args = [str(python), "-m", "pipeline", "--pasta", str(input_dir)]

    result = PreflightService(project_root=tmp_path).check_task(task, args=args)

    assert result.status == "READY"


def test_scheduler_defers_preflight_failure_without_launch(monkeypatch: pytest.MonkeyPatch):
    from radar_v2.app.services import schedule_service

    issue = PreflightIssue("BLOCKED_MISSING_LIBRARY", "pandas", "modulo Python nao importavel")
    result = PreflightResult("BLOCKED_MISSING_LIBRARY", (issue,))

    class BlockedRunService:
        def build_args(self, *args, **kwargs):
            raise TaskPreflightError("dl_cemig", result)

    class Catalog:
        @staticmethod
        def get(_task_id):
            return SimpleNamespace(task_id="dl_cemig")

    deferred: list[int] = []
    monkeypatch.setattr(schedule_service, "defer_schedule_after_preflight", deferred.append)
    service = schedule_service.ScheduleService(BlockedRunService(), Catalog())
    service._fire({
        "id": 3, "task_id": "dl_cemig", "category": "Downloaders",
        "params_json": "{}",
    }, __import__("datetime").datetime.now())
    assert deferred == [3]
