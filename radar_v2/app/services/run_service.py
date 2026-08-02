"""
RunService V2 — porta o RunManager do Radar v1 com arquitetura limpa.

Mantém:
- captura PIPE de stdout/stderr em threads (streaming real)
- taskkill /T /F no Windows para matar árvore de processos
- rerun a partir de live run ou histórico
- log slicing incremental (start_line / next_line / total_lines)
- reconcile/poll de processos zumbis
- dashboard metrics
- ANSI stripping
"""
from __future__ import annotations

import datetime as dt
import os
import re
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    from core.pipelines._session_runtime import build_session_command
except Exception:  # pragma: no cover - fallback quando core ainda nao estiver no path
    build_session_command = lambda cmd: cmd  # type: ignore[assignment]

from ..repositories.storage import (
    APP_DATA_DIR,
    compact_run_history,
    count_enabled_schedules,
    create_run,
    finish_run,
    get_run_log,
    list_runs,
    save_run_log,
)
from .preflight_service import ENV_FILE, REQUIRED_ENV_KEYS, TaskPreflightError

ROOT_DIR    = Path(__file__).resolve().parent.parent.parent.parent
RUN_LOG_DIR = APP_DATA_DIR / "run_logs"
ANSI_RE     = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
MAX_LOG_FILES = 400


class RunConflictError(RuntimeError):
    pass


@dataclass
class LiveRun:
    run_id:   int
    task_id:  str
    task_name: str
    category: str
    args:     list[str]
    command:  str
    command_signature: str
    started_at: dt.datetime
    process: subprocess.Popen
    log_lines: list[str] = field(default_factory=list)
    status_text: str = "Rodando"
    exit_code:   int | None = None

    @property
    def pid(self) -> int | None:
        try:
            return int(self.process.pid) if self.process.pid else None
        except Exception:
            return None

    def to_dict(self) -> dict:
        return {
            "run_id":     self.run_id,
            "task_id":    self.task_id,
            "task_name":  self.task_name,
            "category":   self.category,
            "command":    self.command,
            "started_at": self.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            "status_text": self.status_text,
            "exit_code":  self.exit_code,
            "pid":        self.pid,
            "is_running": self.exit_code is None,
        }


class RunService:

    def __init__(self, preflight=None) -> None:
        self._lock = threading.RLock()
        self._live: dict[int, LiveRun] = {}
        self._preflight = preflight
        RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
        self._prune_log_files()
        try:
            compact_run_history()
        except Exception:
            pass

    # ── catálogo de tarefas ───────────────────────────────────────────────────

    @staticmethod
    def _script_to_module(script_path: str, root: Path) -> str | None:
        """Converte caminho de script para notação de módulo Python (-m).

        Exemplo: core/downloaders/cemig/cemig.py → core.downloaders.cemig.cemig
        Retorna None se a conversão não for possível (script fora do root, etc.).
        """
        try:
            p = Path(script_path)
            if not p.is_absolute():
                p = root / p
            rel = p.resolve().relative_to(root.resolve())
            parts = rel.with_suffix("").parts
            return ".".join(parts)
        except (ValueError, Exception):
            return None

    def build_args(self, task, *, month: str, year: str, selected_type: str,
                   stage_flag: str, pasta: str, download_condition: str,
                   extra_text: str) -> tuple:
        """Retorna (task, args_list, label)."""
        if self._preflight is not None:
            result = self._preflight.check_task(task)
            if not result.ready:
                raise TaskPreflightError(task.task_id, result)

        abs_script = Path(task.script)
        if not abs_script.is_absolute():
            abs_script = ROOT_DIR / abs_script
        if not abs_script.exists():
            raise FileNotFoundError(f"Script não encontrado: {task.script}")

        root = ROOT_DIR
        python_exe = str(root / ".venv" / "Scripts" / "python.exe")

        # Usa -m <módulo> em vez de passar o caminho do script directamente.
        # Isso garante que sys.path[0] seja o cwd (root do projeto) e não o
        # diretório do script, eliminando o ModuleNotFoundError: No module 'core'
        # independente de PYTHONPATH estar ou não carregado pelo processo pai.
        module_name = self._script_to_module(task.script, root)
        if module_name:
            args = [python_exe, "-m", module_name]
        else:
            args = [python_exe, str(abs_script)]
        tipo = (selected_type or task.default_type or "").strip().lower()

        if task.supports_month_year:
            args += ["--mes", month, "--ano", year]
        if task.supports_type and tipo:
            args += ["--tipo", tipo]
        if task.supports_stage_flags and stage_flag:
            args.append(stage_flag)
        if task.supports_pasta and pasta.strip():
            args += ["--pasta", pasta.strip()]
        elif getattr(task, "pasta_template", "") and not pasta.strip():
            args += ["--pasta", task.pasta_template.format(mes=month, ano=year)]
        if getattr(task, "download_condition_options", []) and download_condition.strip():
            args += ["--condicao", download_condition.strip()]
        args.extend(getattr(task, "extra_args", []))
        if extra_text.strip():
            args.extend(extra_text.strip().split())

        label = f"{task.name} [{task.category}]"
        return task, args, label

    # ── lançamento ────────────────────────────────────────────────────────────

    def launch(self, task_id: str, task_name: str, category: str,
               args: list[str], *, allow_parallel: bool = False) -> LiveRun:
        if self._preflight is not None:
            result = self._preflight.check_task_id(task_id, args=args)
            if not result.ready:
                raise TaskPreflightError(task_id, result)
        args = build_session_command(list(args))
        command = subprocess.list2cmdline(args)
        sig     = command

        self._reconcile_all()
        with self._lock:
            # CEMIG usa navegador e pasta de download proprios; nunca permita
            # outra execucao, inclusive pelo botao de rerun.
            if task_id == "dl_cemig":
                existing = next((run for run in self._live.values()
                                 if run.task_id == task_id and run.exit_code is None), None)
                if existing:
                    raise RunConflictError("Ja existe uma execucao CEMIG ativa.")
            elif not allow_parallel:
                existing = self._find_running_by_sig(task_id, sig)
                if existing:
                    return existing

            started_at = dt.datetime.now()
            run_id = create_run(
                started_at=started_at.strftime("%Y-%m-%d %H:%M:%S"),
                task_id=task_id, task_name=task_name,
                category=category, command=command,
            )
            # Determina o diretório do script para incluir no PYTHONPATH,
            # necessário para imports relativos (ex: worker_coelba importa
            # classificacao_ocr.py do mesmo diretório).
            script_dir: Path | None = None
            try:
                # args pode ser: [python, script.py, ...] ou [python, -m, mod, ...]
                if len(args) >= 2 and args[1] != "-m":
                    candidate = Path(args[1])
                    if not candidate.is_absolute():
                        candidate = ROOT_DIR / candidate
                    if candidate.exists():
                        script_dir = candidate.parent
            except Exception:
                pass

            child_env = self._env(script_dir)
            metrics_file = RUN_LOG_DIR / f"run_{run_id}_metrics.jsonl"
            child_env.update({
                "RADAR_RUN_ID": str(run_id),
                "RADAR_TASK_ID": task_id,
                "RADAR_METRICS_FILE": str(metrics_file),
            })
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            proc = subprocess.Popen(
                args, cwd=ROOT_DIR, env=child_env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1, creationflags=flags,
            )
            run = LiveRun(
                run_id=run_id, task_id=task_id, task_name=task_name,
                category=category, args=args, command=command,
                command_signature=sig, started_at=started_at, process=proc,
            )
            self._live[run_id] = run

        self._reset_log(run_id)
        self._append_log(run_id, f"[START] {command}")
        entrypoint = " ".join(args[1:3]) if len(args) > 2 and args[1] == "-m" else (args[1] if len(args) > 1 else "")
        env_available = sum(1 for key in REQUIRED_ENV_KEYS if child_env.get(key))
        for context_line in (
            f"[CONTEXT] RUN_ID={run_id}",
            f"[CONTEXT] TASK_ID={task_id}",
            f"[CONTEXT] ENTRYPOINT={entrypoint}",
            f"[CONTEXT] PYTHON={args[0] if args else ''}",
            f"[CONTEXT] CWD={ROOT_DIR}",
            f"[CONTEXT] ENV_FILE={ENV_FILE}",
            f"[CONTEXT] ENV_KEYS_AVAILABLE={env_available}/{len(REQUIRED_ENV_KEYS)}",
            f"[CONTEXT] RADAR_METRICS_FILE={metrics_file}",
            f"[CONTEXT] LOG_FILE={RUN_LOG_DIR / f'run_{run_id}.log'}",
        ):
            self._append_log(run_id, context_line)
        self._spawn_reader(run_id, proc.stdout, prefix="")
        self._spawn_reader(run_id, proc.stderr, prefix="[ERR] ")
        threading.Thread(target=self._wait, args=(run_id,), daemon=True).start()
        return run

    def rerun(self, run_id: int) -> LiveRun:
        self._reconcile_all()
        with self._lock:
            run = self._live.get(run_id)
        if run is not None:
            self._reconcile_one(run_id, run)
            if run.exit_code is None:
                raise RuntimeError("Execução ainda em andamento.")
            return self.launch(run.task_id, run.task_name, run.category, list(run.args), allow_parallel=True)

        history = next((r for r in list_runs(limit=400) if int(r["id"]) == run_id), None)
        if history is None:
            raise ValueError(f"Run {run_id} não encontrado.")
        args = shlex.split(history["command"], posix=False)
        return self.launch(
            history["task_id"] or "manual",
            history["task_name"] or "Execução",
            history["category"] or "-",
            args, allow_parallel=True,
        )

    def stop(self, run_id: int) -> None:
        self._reconcile_all()
        with self._lock:
            run = self._live.get(run_id)
        if run is None:
            raise ValueError(f"Run {run_id} não encontrado.")
        self._reconcile_one(run_id, run)
        if run.exit_code is not None:
            return
        self._append_log(run_id, "[STOP] Encerrando processo...")
        self._kill_tree(run)
        self._reconcile_one(run_id, run)

    # ── log ───────────────────────────────────────────────────────────────────

    def get_log(self, run_id: int, *, after_line: int = 0, max_lines: int = 1200) -> dict:
        self._reconcile_all()
        with self._lock:
            run = self._live.get(run_id)
            if run is not None:
                payload = self._slice(run.log_lines, after_line=after_line, max_lines=max_lines)
                payload.update({
                    "is_live":    True,
                    "is_running": run.exit_code is None,
                    "status_text": run.status_text,
                })
                return payload

        history = next((r for r in list_runs(limit=400) if int(r["id"]) == run_id), None)
        status_text = {
            "running": "Rodando", "success": "Concluído", "error": "Falhou",
        }.get((history or {}).get("status", "").lower(), (history or {}).get("status") or "-")

        lines = self._read_log_file(run_id)
        if lines:
            payload = self._slice(lines, after_line=after_line, max_lines=max_lines)
            payload.update({"is_live": False, "is_running": False, "status_text": status_text})
            return payload

        persisted = get_run_log(run_id) if history else ""
        if persisted:
            payload = self._slice(persisted.splitlines(), after_line=after_line, max_lines=max_lines)
            payload.update({"is_live": False, "is_running": False, "status_text": status_text})
            return payload

        return {"log": "", "start_line": 0, "next_line": 0, "total_lines": 0,
                "is_live": False, "is_running": False, "status_text": status_text}

    # ── listagem / métricas ───────────────────────────────────────────────────

    def list_live(self, status_filter: str = "Todas") -> list[dict]:
        self._reconcile_all()
        with self._lock:
            runs = sorted(self._live.values(), key=lambda r: r.started_at, reverse=True)
        if status_filter != "Todas":
            runs = [r for r in runs if r.status_text == status_filter]
        return [r.to_dict() for r in runs]

    def list_history(self, limit: int = 150) -> list[dict]:
        return list_runs(limit=limit)

    def dashboard(self) -> dict:
        self._reconcile_all()
        history = list_runs(limit=150)
        today = dt.date.today().isoformat()
        with self._lock:
            running = sum(1 for r in self._live.values() if r.exit_code is None)
        success_today = sum(1 for r in history if r.get("status") == "success" and str(r.get("started_at","")).startswith(today))
        failed_today  = sum(1 for r in history if r.get("status") == "error"   and str(r.get("started_at","")).startswith(today))
        finished = [r for r in history if r.get("status") in ("success","error")]
        success_rate = round(sum(1 for r in finished if r["status"]=="success") / len(finished) * 100) if finished else 0
        durs = [r["duration_s"] for r in history[:20] if r.get("status")=="success" and r.get("duration_s")]
        avg_dur = round(sum(durs)/len(durs),1) if durs else 0
        try:
            sched_count = count_enabled_schedules()
        except Exception:
            sched_count = 0
        return {
            "running_now":    running,
            "success_today":  success_today,
            "failed_today":   failed_today,
            "last_task":      history[0]["task_name"] if history else "-",
            "history_total":  len(history),
            "recent_runs":    history[:8],
            "success_rate":   success_rate,
            "avg_duration_s": avg_dur,
            "scheduled_count": sched_count,
        }

    # ── internos ─────────────────────────────────────────────────────────────

    @staticmethod
    def _env(script_dir: Path | None = None) -> dict:
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        # Garante que o projeto raiz esteja no PYTHONPATH para que scripts em
        # core/downloaders/** possam importar pacotes como `core.project_paths`
        # independente do diretório onde o script está localizado.
        # Também inclui o diretório do script para suportar imports relativos de
        # módulos vizinhos (ex: worker_coelba.py importa classificacao_ocr.py do
        # mesmo diretório core/downloaders/neoenergia/).
        existing_pp = env.get("PYTHONPATH", "")
        root_str = str(ROOT_DIR)
        extra_parts = [root_str]
        if script_dir is not None:
            script_dir_str = str(script_dir)
            if script_dir_str != root_str:
                extra_parts.append(script_dir_str)
        existing_list = existing_pp.split(os.pathsep) if existing_pp else []
        new_parts = [p for p in extra_parts if p not in existing_list]
        if new_parts:
            env["PYTHONPATH"] = os.pathsep.join(new_parts + (existing_list if existing_list else []))
        return env

    @staticmethod
    def _clean(text: str) -> str:
        return ANSI_RE.sub("", text or "").replace("\r","").replace("\x08","")

    @staticmethod
    def _slice(lines: list[str], *, after_line: int, max_lines: int) -> dict:
        total = len(lines)
        if after_line <= 0:
            start = max(0, total - max(1, max_lines))
        else:
            start = max(0, min(after_line, total))
        visible = lines[start:]
        return {"log": "\n".join(visible), "start_line": start,
                "next_line": total, "total_lines": total}

    def _find_running_by_sig(self, task_id: str, sig: str) -> LiveRun | None:
        for run in self._live.values():
            if run.task_id == task_id and run.exit_code is None and run.command_signature == sig:
                return run
        return None

    def _spawn_reader(self, run_id: int, stream, prefix: str) -> None:
        def _read():
            if stream is None:
                return
            try:
                for line in iter(stream.readline, ""):
                    if not line:
                        break
                    text = self._clean(line.rstrip("\n"))
                    if text:
                        self._append_log(run_id, f"{prefix}{text}" if prefix else text)
            finally:
                try:
                    stream.close()
                except Exception:
                    pass
        threading.Thread(target=_read, daemon=True).start()

    def _append_log(self, run_id: int, text: str) -> None:
        if not text:
            return
        lines = text.splitlines()
        if not lines:
            return
        with self._lock:
            run = self._live.get(run_id)
            if run is not None:
                run.log_lines.extend(lines)
                if len(run.log_lines) > 5000:
                    run.log_lines = run.log_lines[-5000:]
        self._append_log_file(run_id, lines)

    def _wait(self, run_id: int) -> None:
        with self._lock:
            run = self._live.get(run_id)
        if run is None:
            return
        code = int(run.process.wait())
        self._finalize(run_id, code)

    def _finalize(self, run_id: int, exit_code: int) -> None:
        with self._lock:
            run = self._live.get(run_id)
            if run is None or run.exit_code is not None:
                return
            run.exit_code    = int(exit_code)
            run.status_text  = "Concluído" if exit_code == 0 else "Falhou"
            started_at = run.started_at
        finished_at = dt.datetime.now()
        duration_s  = (finished_at - started_at).total_seconds()
        status = "success" if exit_code == 0 else "error"
        finish_run(run_id, finished_at.strftime("%Y-%m-%d %H:%M:%S"), status, exit_code, duration_s)
        if exit_code != 0:
            self._append_log(
                run_id,
                f"[FAILURE] TYPE=EXECUTION STAGE=SUBPROCESS EXIT_CODE={exit_code} DURATION={duration_s:.1f}s",
            )
        self._append_log(run_id, f"[END] exit={exit_code} duração={duration_s:.1f}s")
        try:
            log_text = "\n".join(self._read_log_file(run_id))
            if log_text:
                save_run_log(run_id, log_text)
        except Exception:
            pass

    def _kill_tree(self, run: LiveRun) -> None:
        pid = run.pid
        if pid and os.name == "nt":
            try:
                res = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                for line in ((res.stdout or "") + (res.stderr or "")).splitlines():
                    if line.strip():
                        self._append_log(run.run_id, f"[STOP] {line.strip()}")
                if res.returncode == 0:
                    return
            except Exception as exc:
                self._append_log(run.run_id, f"[STOP] taskkill falhou: {exc}")
        try:
            run.process.terminate()
            run.process.wait(timeout=5)
        except Exception:
            try:
                run.process.kill()
                run.process.wait(timeout=5)
            except Exception:
                pass
        time.sleep(0.3)

    def _reconcile_all(self) -> None:
        with self._lock:
            snapshot = list(self._live.items())
        for run_id, run in snapshot:
            self._reconcile_one(run_id, run)

    def _reconcile_one(self, run_id: int, run: LiveRun | None = None) -> None:
        if run is None:
            with self._lock:
                run = self._live.get(run_id)
        if run is None or run.exit_code is not None:
            return
        try:
            polled = run.process.poll()
        except Exception:
            polled = None
        if polled is not None:
            self._finalize(run_id, int(polled))

    # ── log files ─────────────────────────────────────────────────────────────

    def _log_path(self, run_id: int) -> Path:
        return RUN_LOG_DIR / f"run_{run_id}.log"

    def _reset_log(self, run_id: int) -> None:
        p = self._log_path(run_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")

    def _append_log_file(self, run_id: int, lines: list[str]) -> None:
        p = self._log_path(run_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8", newline="\n") as f:
            for line in lines:
                f.write(line + "\n")

    def _read_log_file(self, run_id: int) -> list[str]:
        p = self._log_path(run_id)
        if not p.exists():
            return []
        try:
            return p.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            return []

    def _prune_log_files(self) -> None:
        try:
            files = sorted(RUN_LOG_DIR.glob("run_*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
            for f in files[MAX_LOG_FILES:]:
                f.unlink(missing_ok=True)
        except OSError:
            pass
