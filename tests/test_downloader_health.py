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


def test_resource_policy_is_bounded():
    policy = policy_for("dl_cpfl_bt")
    assert policy.resource_group == "CPFL_RGE"
    assert policy.max_instances == 1
    assert policy.max_retries < 3
    assert policy.timeout_seconds > 0
