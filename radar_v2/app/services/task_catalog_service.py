"""TaskCatalogService V2 — carrega do YAML; fallback automático para catalog.py legado."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
TASKS_YAML = CONFIG_DIR / "tasks.yaml"
ROOT_DIR   = Path(__file__).resolve().parent.parent.parent.parent


@dataclass
class Task:
    task_id:   str
    name:      str
    category:  str
    script:    str          # caminho relativo à ROOT_DIR
    notes:     str = ""
    supports_month_year:   bool = False
    supports_type:         bool = False
    supports_stage_flags:  bool = False
    supports_pasta:        bool = False
    pasta_template:        str  = ""
    download_condition_options: list = field(default_factory=list)
    default_type: str = "ambos"
    extra_args:   list = field(default_factory=list)

    @property
    def abs_script(self) -> str:
        return str(ROOT_DIR / self.script)

    def exists(self) -> bool:
        return Path(self.abs_script).exists()


class TaskCatalogService:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self.reload()

    def reload(self) -> None:
        if TASKS_YAML.exists():
            self._load_yaml()
        else:
            self._load_legacy()

    def _load_yaml(self) -> None:
        import yaml
        data = yaml.safe_load(TASKS_YAML.read_text(encoding="utf-8"))
        self._tasks = {}
        for e in data.get("tasks", []):
            # O Radar e dono apenas da etapa de download. Os pipelines continuam
            # versionados no YAML para auditoria historica, mas nao fazem parte
            # do catalogo operacional nem podem ser iniciados pela interface.
            if e.get("category") != "Downloaders" or not str(e.get("task_id", "")).startswith("dl_"):
                continue
            t = Task(
                task_id=e["task_id"],
                name=e["name"],
                category=e["category"],
                script=e["script"],
                notes=e.get("notes", ""),
                supports_month_year=e.get("supports_month_year", False),
                supports_type=e.get("supports_type", False),
                supports_stage_flags=e.get("supports_stage_flags", False),
                supports_pasta=e.get("supports_pasta", False),
                pasta_template=e.get("pasta_template", ""),
                download_condition_options=list(e.get("download_condition_options", [])),
                default_type=e.get("default_type", "ambos"),
                extra_args=list(e.get("extra_args", [])),
            )
            self._tasks[t.task_id] = t

    def _load_legacy(self) -> None:
        import sys
        sys.path.insert(0, str(ROOT_DIR))
        from radar.web_app.catalog import load_tasks
        self._tasks = {}
        for old in load_tasks():
            if old.category != "Downloaders" or not old.task_id.startswith("dl_"):
                continue
            t = Task(
                task_id=old.task_id,
                name=old.name,
                category=old.category,
                script=str(old.script.relative_to(ROOT_DIR)) if old.script.is_absolute() else str(old.script),
                notes=old.notes,
                supports_month_year=old.supports_month_year,
                supports_type=old.supports_type,
                supports_stage_flags=old.supports_stage_flags,
                supports_pasta=old.supports_pasta,
                pasta_template=old.pasta_template,
                download_condition_options=list(old.download_condition_options or []),
                default_type=old.default_type,
                extra_args=list(old.extra_args),
            )
            self._tasks[t.task_id] = t

    def all(self) -> list[Task]:
        return list(self._tasks.values())

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def by_category(self) -> dict[str, list[Task]]:
        grouped: dict[str, list[Task]] = {}
        for t in self._tasks.values():
            grouped.setdefault(t.category, []).append(t)
        return grouped

    def to_dict(self, t: Task) -> dict:
        return {
            "task_id":    t.task_id,
            "name":       t.name,
            "category":   t.category,
            "script":     t.script,
            "exists":     t.exists(),
            "notes":      t.notes,
            "supports_month_year":   t.supports_month_year,
            "supports_type":         t.supports_type,
            "supports_stage_flags":  t.supports_stage_flags,
            "supports_pasta":        t.supports_pasta,
            "pasta_template":        t.pasta_template,
            "download_condition_options": t.download_condition_options,
            "default_type": t.default_type,
        }
