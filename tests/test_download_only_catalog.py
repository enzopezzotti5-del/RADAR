"""Garante que o catalogo operacional do Radar exponha apenas downloads."""

from radar_v2.app.services.task_catalog_service import TaskCatalogService


def test_active_catalog_contains_only_downloaders() -> None:
    catalog = TaskCatalogService()

    assert catalog.all()
    assert all(task.category == "Downloaders" for task in catalog.all())
    assert all(task.task_id.startswith("dl_") for task in catalog.all())


def test_pipeline_remains_outside_operational_catalog() -> None:
    catalog = TaskCatalogService()

    assert catalog.get("pl_copel_bt") is None
