"""Testes de cobertura do catálogo de tarefas vs METRIC_TASKS.

MODELO DE OUTCOMES (decisão formal):
  - Outcome final único por item por run (last-write-wins na item_key).
  - Downloaders: downloaded | skipped_existing | item_error | other
  - Pipelines:   processed via emit_other (schema atual não tem coluna processed separada)
  - Uma fatura NÃO pode ter downloaded e item_error no mesmo run para o mesmo item_key;
    downloaded é emitido APENAS no ponto final confirmado (após move/rename + index).
  - item_error é emitido APENAS para itens que falharam sem PDF salvo.
  - Regra de segurança: nunca chamar emit_item_error após emit_downloaded para o mesmo item.

SINCRONISMO tasks.yaml ↔ METRIC_TASKS:
  - Toda nova task dl_* de download real deve ser adicionada a METRIC_TASKS.
  - Tarefas pl_* (pipeline) e dl_light_rj são excluídas explicitamente.
  - Este teste falha quando task_id ativo de download não tem classificação.
"""
from __future__ import annotations

import yaml
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS_YAML = REPO_ROOT / "radar_v2" / "config" / "tasks.yaml"

# Tarefas que não geram faturas próprias — excluídas de METRIC_TASKS intencionalmente.
METRIC_TASKS_EXCLUDED = {
    "dl_light_rj",   # automatiza modal Banco do Brasil; não gera PDFs de fatura
}
# Tarefas bloqueadas operacionalmente — código presente, portal desativado.
METRIC_TASKS_BLOCKED = {
    "dl_celesc_bt",
    "dl_celesc_mt",
}


def _load_tasks() -> list[dict]:
    with open(TASKS_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f)["tasks"]


def test_metric_tasks_covers_all_active_downloaders():
    """METRIC_TASKS deve conter todos os task_ids dl_* que geram faturas reais.

    Quando um novo downloader for adicionado a tasks.yaml, este teste falha até
    que o task_id seja adicionado a METRIC_TASKS ou a METRIC_TASKS_EXCLUDED.
    """
    from radar_v2.app.services.run_service import METRIC_TASKS

    all_tasks = _load_tasks()
    downloader_ids = {t["task_id"] for t in all_tasks if t["task_id"].startswith("dl_")}

    unclassified = (
        downloader_ids
        - set(METRIC_TASKS)
        - METRIC_TASKS_EXCLUDED
        - METRIC_TASKS_BLOCKED
    )
    assert unclassified == set(), (
        f"Tarefas dl_* sem classificação em METRIC_TASKS ou listas de exceção: {unclassified}\n"
        "Adicione o task_id a METRIC_TASKS (se gera faturas) ou a METRIC_TASKS_EXCLUDED/BLOCKED."
    )


def test_metric_tasks_contains_no_unknown_task_ids():
    """Todos os task_ids em METRIC_TASKS devem existir em tasks.yaml."""
    from radar_v2.app.services.run_service import METRIC_TASKS

    all_tasks = _load_tasks()
    known_ids = {t["task_id"] for t in all_tasks}

    unknown = set(METRIC_TASKS) - known_ids
    assert unknown == set(), (
        f"METRIC_TASKS contém task_ids que não existem em tasks.yaml: {unknown}"
    )


def test_metric_tasks_no_pipeline_ids():
    """Pipelines (pl_*) não devem estar em METRIC_TASKS — não são donos do evento downloaded."""
    from radar_v2.app.services.run_service import METRIC_TASKS

    pipeline_ids = {tid for tid in METRIC_TASKS if tid.startswith("pl_")}
    assert pipeline_ids == set(), (
        f"METRIC_TASKS contém task_ids de pipeline que não devem gerar downloaded: {pipeline_ids}"
    )


def test_canonical_utility_names_have_no_underscore():
    """Nomes canônicos de utility não devem ter underscore (usar espaço)."""
    from radar_v2.app.services.run_service import METRIC_TASKS

    underscore_names = {
        tid: name for tid, name in METRIC_TASKS.items() if "_" in name
    }
    assert underscore_names == {}, (
        f"Utility names com underscore (usar espaço): {underscore_names}"
    )


def test_canonical_utility_names_have_no_neoenergia_prefix():
    """Utility names não devem ter prefixo 'Neoenergia' — usar só o nome da concessionária."""
    from radar_v2.app.services.run_service import METRIC_TASKS

    wrong = {
        tid: name for tid, name in METRIC_TASKS.items()
        if "neoenergia" in name.lower()
    }
    assert wrong == {}, (
        f"Utility names com prefixo Neoenergia (remover): {wrong}"
    )


def test_metric_tasks_blocked_tasks_are_classified():
    """Tarefas bloqueadas devem existir em tasks.yaml e estar em METRIC_TASKS (código presente, portal desativado)."""
    from radar_v2.app.services.run_service import METRIC_TASKS

    all_tasks = _load_tasks()
    known_ids = {t["task_id"] for t in all_tasks}

    for tid in METRIC_TASKS_BLOCKED:
        assert tid in known_ids, f"Tarefa bloqueada '{tid}' não existe em tasks.yaml"
        assert tid in METRIC_TASKS, (
            f"Tarefa bloqueada '{tid}' deve estar em METRIC_TASKS "
            "(código de instrumentação presente, portal desativado via preflight)"
        )


def test_outcomes_are_semantically_valid():
    """OUTCOMES em radar_metrics deve conter os 4 outcomes básicos para downloaders."""
    from core.metrics.radar_metrics import OUTCOMES
    required = {"downloaded", "skipped_existing", "item_error", "other"}
    assert required.issubset(OUTCOMES), (
        f"OUTCOMES ausentes: {required - OUTCOMES}"
    )


def test_valid_outcomes_in_metric_events():
    """metric_events.py aceita o superset incluindo 'processed' para pipelines futuros."""
    from radar_v2.app.services.metric_events import VALID_OUTCOMES
    assert "downloaded" in VALID_OUTCOMES
    assert "skipped_existing" in VALID_OUTCOMES
    assert "item_error" in VALID_OUTCOMES
