from types import SimpleNamespace

from radar_v2.app.services.downloader_health_service import DownloaderHealthService, policy_for
from radar_v2.app.services.preflight_service import PreflightResult


class Catalog:
    @staticmethod
    def all():
        return [SimpleNamespace(task_id="dl_enel_sp", name="Downloader ENEL SP")]


class Preflight:
    @staticmethod
    def check_task(_task):
        return PreflightResult("READY", ())


def test_health_reports_ready_unobserved(monkeypatch):
    from radar_v2.app.services import downloader_health_service as module
    monkeypatch.setattr(module, "list_schedules", lambda: [{
        "task_id": "dl_enel_sp", "enabled": 1, "next_run_at": "2026-08-05 01:00:00",
    }])
    monkeypatch.setattr(module, "list_runs", lambda limit: [])
    item = DownloaderHealthService(Catalog(), Preflight()).report()["downloaders"][0]
    assert item["health"] == "READY_UNOBSERVED"
    assert item["autonomous_ready"] is True
    assert item["production_observed"] is False


def test_health_degrades_after_consecutive_zero_download_successes(monkeypatch):
    from radar_v2.app.services import downloader_health_service as module

    monkeypatch.setattr(module, "list_schedules", lambda: [{
        "task_id": "dl_neo_coelba", "enabled": 1, "next_run_at": "2026-08-19 05:45:00",
    }])
    runs = [
        {"id": i, "task_id": "dl_neo_coelba", "status": "success", "finished_at": f"2026-08-{18 - i:02d}"}
        for i in range(5)
    ]
    monkeypatch.setattr(module, "list_runs", lambda limit: runs)
    monkeypatch.setattr(module, "get_run_metric_counts", lambda run_id: {"downloaded": 0})

    catalog = SimpleNamespace(all=lambda: [SimpleNamespace(task_id="dl_neo_coelba", name="Downloader Neoenergia COELBA")])
    item = DownloaderHealthService(catalog, Preflight()).report()["downloaders"][0]

    assert item["health"] == "DEGRADED_NO_NEW_INVOICES"
    assert item["consecutive_zero_downloads"] == 5
    assert item["last_valid_download"] is None


def test_health_stays_healthy_with_recent_real_downloads(monkeypatch):
    from radar_v2.app.services import downloader_health_service as module

    monkeypatch.setattr(module, "list_schedules", lambda: [{
        "task_id": "dl_enel_sp", "enabled": 1, "next_run_at": "2026-08-19 03:00:00",
    }])
    runs = [
        {"id": 2, "task_id": "dl_enel_sp", "status": "success", "finished_at": "2026-08-18"},
        {"id": 1, "task_id": "dl_enel_sp", "status": "success", "finished_at": "2026-08-17"},
    ]
    counts = {2: {"downloaded": 4}, 1: {"downloaded": 2}}
    monkeypatch.setattr(module, "list_runs", lambda limit: runs)
    monkeypatch.setattr(module, "get_run_metric_counts", lambda run_id: counts[run_id])

    item = DownloaderHealthService(Catalog(), Preflight()).report()["downloaders"][0]

    assert item["health"] == "HEALTHY"
    assert item["consecutive_zero_downloads"] == 0
    assert item["last_valid_download"] == "2026-08-18"


def test_health_does_not_degrade_before_reaching_the_threshold(monkeypatch):
    from radar_v2.app.services import downloader_health_service as module

    monkeypatch.setattr(module, "list_schedules", lambda: [{
        "task_id": "dl_enel_sp", "enabled": 1, "next_run_at": "2026-08-19 03:00:00",
    }])
    runs = [
        {"id": 2, "task_id": "dl_enel_sp", "status": "success", "finished_at": "2026-08-18"},
        {"id": 1, "task_id": "dl_enel_sp", "status": "success", "finished_at": "2026-08-17"},
    ]
    monkeypatch.setattr(module, "list_runs", lambda limit: runs)
    monkeypatch.setattr(module, "get_run_metric_counts", lambda run_id: {"downloaded": 0})

    item = DownloaderHealthService(Catalog(), Preflight()).report()["downloaders"][0]

    assert item["health"] == "HEALTHY"
    assert item["consecutive_zero_downloads"] == 2


def test_resource_policy_is_bounded():
    policy = policy_for("dl_cpfl_bt")
    assert policy.resource_group == "CPFL_RGE"
    assert policy.max_instances == 1
    assert policy.max_retries < 3
    assert policy.timeout_seconds > 0


def test_same_resource_group_conflicts_even_for_different_tasks(tmp_path, monkeypatch):
    from radar_v2.app.services import run_service
    monkeypatch.setattr(run_service, "RUN_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(run_service, "RUN_METRICS_DIR", tmp_path / "metrics")
    service = run_service.RunService()
    service._live[10] = SimpleNamespace(task_id="dl_cpfl_bt", run_id=10, exit_code=None)
    assert service._find_resource_conflict("dl_rge_bt").run_id == 10
    assert service._find_resource_conflict("dl_enel_sp") is None


def test_timeout_stops_only_the_target_live_run(tmp_path, monkeypatch):
    from radar_v2.app.services import run_service
    monkeypatch.setattr(run_service, "RUN_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(run_service, "RUN_METRICS_DIR", tmp_path / "metrics")
    service = run_service.RunService()
    target = SimpleNamespace(task_id="dl_enel_sp", run_id=20, exit_code=None, log_lines=[])
    service._live[20] = target
    stopped = []
    monkeypatch.setattr(service, "_kill_tree", stopped.append)
    service._timeout_run(20, 60)
    assert stopped == [target]
