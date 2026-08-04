"""Safe production preflight for the Radar runtime and task catalog.

The checks intentionally report names and paths only. Secret values are never
included in logs or API payloads.
"""
from __future__ import annotations

import importlib
import os
import py_compile
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
PYTHON_EXE = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
DB_PATH = PROJECT_ROOT / "logs" / "web_app" / "history.sqlite3"
RUN_LOG_DIR = PROJECT_ROOT / "logs" / "web_app" / "run_logs"

REQUIRED_ENV_KEYS = ("RADAR_V2_SECRET_KEY",)
GLOBAL_REQUIRED_MODULES = (
    "flask", "yaml", "dotenv", "waitress", "requests", "selenium",
    "openpyxl", "pandas", "pdfplumber", "curl_cffi", "pikepdf",
)

TASK_MODULES: dict[str, tuple[str, ...]] = {
    "dl_cpfl": ("selenium", "requests", "openpyxl"),
    "dl_rge": ("selenium", "requests", "openpyxl"),
    "dl_cemig": ("selenium", "requests", "pandas", "openpyxl"),
    "dl_copel": ("selenium", "requests", "pandas", "openpyxl"),
    "dl_neo": ("selenium", "requests", "pandas", "openpyxl", "pdfplumber", "curl_cffi"),
    "dl_enel": ("requests", "pandas", "openpyxl", "pdfplumber"),
    "dl_celesc": ("selenium", "requests", "openpyxl"),
    "dl_equatorial": ("selenium", "requests", "openpyxl"),
    "dl_light": ("selenium", "openpyxl"),
    "pl_": ("pandas", "openpyxl", "pdfplumber"),
}


@dataclass(frozen=True)
class PreflightIssue:
    code: str
    requirement: str
    detail: str


@dataclass(frozen=True)
class PreflightResult:
    status: str
    issues: tuple[PreflightIssue, ...]

    @property
    def ready(self) -> bool:
        return self.status == "READY"

    def to_dict(self) -> dict:
        official = "PREFLIGHT_PASS" if self.ready else (
            "BLOCKED_EXTERNAL" if self.status == "BLOCKED_EXTERNAL" else "PREFLIGHT_FAIL"
        )
        return {
            "status": self.status, "official_status": official,
            "issues": [asdict(issue) for issue in self.issues],
        }


class TaskPreflightError(RuntimeError):
    def __init__(self, task_id: str, result: PreflightResult) -> None:
        self.task_id = task_id
        self.result = result
        requirements = ", ".join(issue.requirement for issue in result.issues) or "desconhecido"
        super().__init__(f"PREFLIGHT {result.status}: {task_id}: {requirements}")


def _status_for(issues: Iterable[PreflightIssue]) -> str:
    codes = {issue.code for issue in issues}
    priority = (
        "BLOCKED_EXTERNAL",
        "BLOCKED_MISSING_ENV", "BLOCKED_MISSING_FILE", "BLOCKED_MISSING_LIBRARY",
        "BLOCKED_INVALID_PATH", "BLOCKED_BROWSER", "BLOCKED_PERMISSION", "BLOCKED_OTHER",
    )
    return next((code for code in priority if code in codes), "READY")


def _module_missing(name: str) -> bool:
    try:
        importlib.import_module(name)
        return False
    except Exception:
        return True


def _browser_path() -> Path | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    ]
    return next((path for path in candidates if path.is_file()), None)


def _operator_blocked_tasks() -> set[str]:
    raw = os.environ.get("RADAR_V2_BLOCKED_TASKS", "")
    return {value.strip() for value in raw.split(",") if value.strip()}


def _check_writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".radar_preflight_", dir=directory, delete=True):
            pass
        return True
    except OSError:
        return False


class PreflightService:
    def __init__(self, catalog=None, *, project_root: Path | None = None) -> None:
        self.catalog = catalog
        self.project_root = (project_root or PROJECT_ROOT).resolve()
        self.env_file = self.project_root / ".env"
        self.python_exe = self.project_root / ".venv" / "Scripts" / "python.exe"
        self.db_path = self.project_root / "logs" / "web_app" / "history.sqlite3"
        self.run_log_dir = self.project_root / "logs" / "web_app" / "run_logs"

    def global_report(self) -> dict:
        issues: list[PreflightIssue] = []
        if not self.env_file.is_file():
            issues.append(PreflightIssue("BLOCKED_MISSING_FILE", ".env", str(self.env_file)))
        if not self.python_exe.is_file():
            issues.append(PreflightIssue("BLOCKED_INVALID_PATH", "python", str(self.python_exe)))

        missing_env = [key for key in REQUIRED_ENV_KEYS if not os.environ.get(key)]
        issues.extend(
            PreflightIssue("BLOCKED_MISSING_ENV", key, "variavel obrigatoria ausente")
            for key in missing_env
        )
        missing_modules = [name for name in GLOBAL_REQUIRED_MODULES if _module_missing(name)]
        issues.extend(
            PreflightIssue("BLOCKED_MISSING_LIBRARY", name, "modulo Python nao importavel")
            for name in missing_modules
        )
        if not _check_writable(self.run_log_dir):
            issues.append(PreflightIssue("BLOCKED_PERMISSION", "run_logs", str(self.run_log_dir)))
        if self.db_path.exists():
            try:
                conn = sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=rw", uri=True, timeout=5)
                conn.execute("PRAGMA quick_check").fetchone()
                conn.close()
            except sqlite3.Error as exc:
                issues.append(PreflightIssue("BLOCKED_OTHER", "history.sqlite3", type(exc).__name__))
        else:
            issues.append(PreflightIssue("BLOCKED_MISSING_FILE", "history.sqlite3", str(self.db_path)))

        browser = _browser_path()
        return {
            "status": _status_for(issues),
            "env_file_loaded": str(self.env_file),
            "env_keys_available": len(REQUIRED_ENV_KEYS) - len(missing_env),
            "env_keys_required": len(REQUIRED_ENV_KEYS),
            "missing_required_keys": missing_env,
            "python": str(self.python_exe),
            "project_root": str(self.project_root),
            "database": str(self.db_path),
            "browser": str(browser) if browser else None,
            "missing_modules": missing_modules,
            "issues": [asdict(issue) for issue in issues],
        }

    def _required_modules(self, task_id: str) -> tuple[str, ...]:
        for prefix, modules in TASK_MODULES.items():
            if task_id.startswith(prefix):
                return modules
        return ()

    @staticmethod
    def _argument_value(args: list[str] | None, option: str) -> str | None:
        if not args:
            return None
        try:
            index = args.index(option)
        except ValueError:
            return None
        return args[index + 1] if index + 1 < len(args) else None

    def _dynamic_input_dir(self, task_id: str, args: list[str] | None) -> Path | None:
        if task_id not in {"pl_copel_bt", "pl_copel_mt"} or not args:
            return None
        explicit = self._argument_value(args, "--pasta")
        if explicit:
            return Path(explicit)
        month = self._argument_value(args, "--mes")
        year = self._argument_value(args, "--ano")
        if not month or not year:
            return None
        voltage = "BT" if task_id.endswith("_bt") else "MT"
        return Path(r"\\10.10.250.21\Energia\ARQUIVOS ENZO\DOWNLOAD COPEL") / f"{month}.{year}" / voltage

    def check_task(self, task, args: list[str] | None = None) -> PreflightResult:
        issues: list[PreflightIssue] = []
        if task.task_id == "dl_neo_ceb":
            issues.append(PreflightIssue(
                "BLOCKED_EXTERNAL", "captcha_manual",
                "portal exige resolucao humana; nao e autonomo pelo scheduler",
            ))
        if task.task_id in _operator_blocked_tasks():
            issues.append(PreflightIssue(
                "BLOCKED_OTHER", "operator_block",
                "tarefa bloqueada explicitamente em RADAR_V2_BLOCKED_TASKS",
            ))
        script = Path(task.script)
        if not script.is_absolute():
            script = self.project_root / script
        if not script.is_file():
            issues.append(PreflightIssue("BLOCKED_MISSING_FILE", "entrypoint", str(script)))
        else:
            try:
                py_compile.compile(str(script), doraise=True)
            except py_compile.PyCompileError as exc:
                issues.append(PreflightIssue(
                    "BLOCKED_OTHER", "entrypoint_compile", exc.exc_type_name,
                ))
        if not self.python_exe.is_file():
            issues.append(PreflightIssue("BLOCKED_INVALID_PATH", "python", str(self.python_exe)))
        if not self.project_root.is_dir():
            issues.append(PreflightIssue("BLOCKED_INVALID_PATH", "cwd", str(self.project_root)))

        for module in self._required_modules(task.task_id):
            if _module_missing(module):
                issues.append(PreflightIssue("BLOCKED_MISSING_LIBRARY", module, "modulo Python nao importavel"))

        if task.task_id.startswith("dl_") and _browser_path() is None:
            issues.append(PreflightIssue("BLOCKED_BROWSER", "chrome_or_edge", "browser nao encontrado"))

        input_dir = self._dynamic_input_dir(task.task_id, args)
        if input_dir is not None and not input_dir.is_dir():
            issues.append(PreflightIssue(
                "BLOCKED_MISSING_FILE", "pipeline_input_dir", str(input_dir),
            ))

        return PreflightResult(status=_status_for(issues), issues=tuple(issues))

    def check_task_id(self, task_id: str, args: list[str] | None = None) -> PreflightResult:
        if self.catalog is None:
            report = self.global_report()
            issues = tuple(PreflightIssue(**issue) for issue in report["issues"])
            return PreflightResult(status=report["status"], issues=issues)
        task = self.catalog.get(task_id)
        if task is None:
            issue = PreflightIssue("BLOCKED_OTHER", "task_catalog", f"task_id desconhecido: {task_id}")
            return PreflightResult(status=issue.code, issues=(issue,))
        return self.check_task(task, args=args)

    def all_tasks_report(self) -> dict[str, dict]:
        if self.catalog is None:
            return {}
        return {task.task_id: self.check_task(task).to_dict() for task in self.catalog.all()}
