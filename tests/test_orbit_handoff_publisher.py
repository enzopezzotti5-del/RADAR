from pathlib import Path

import pikepdf


def _pdf(path: Path) -> Path:
    with pikepdf.new() as pdf:
        pdf.add_blank_page(page_size=(595, 842))
        pdf.save(path)
    return path


def test_disabled_is_noop(monkeypatch, tmp_path):
    from radar_v2.app.services.orbit_handoff import request_orbit_handoff
    monkeypatch.delenv("RADAR_ORBIT_HANDOFF_ENABLED", raising=False)
    result = request_orbit_handoff(tmp_path / "absent.pdf", task_id="dl_enel_sp", utility="ENEL")
    assert result == {"ok": True, "disabled": True}


def test_atomic_idempotent_publish(monkeypatch, tmp_path):
    from radar_v2.app.services.orbit_handoff import request_orbit_handoff
    source = _pdf(tmp_path / "BB_123.pdf")
    root = tmp_path / "handoff"
    monkeypatch.setenv("RADAR_ORBIT_HANDOFF_ENABLED", "true")
    monkeypatch.setenv("RADAR_ORBIT_HANDOFF_ROOT", str(root))
    monkeypatch.setenv("RADAR_ORBIT_HANDOFF_SETTLE_SECONDS", "0")
    first = request_orbit_handoff(source, task_id="dl_enel_sp", utility="ENEL", run_id=1)
    second = request_orbit_handoff(source, task_id="dl_enel_sp", utility="ENEL", run_id=2)
    assert first["ok"] is True and first["already_staged"] is False
    assert second["ok"] is True and second["already_staged"] is True
    assert source.is_file()
    assert len(list((root / "outbox").glob("*.pdf"))) == 1
    assert len(list((root / "outbox").glob("*.json"))) == 1
    assert not list((root / "outbox").glob("*.part"))


def test_invalid_and_unsupported_are_fail_open(monkeypatch, tmp_path):
    from radar_v2.app.services.orbit_handoff import request_orbit_handoff
    bad = tmp_path / "bad.pdf"
    bad.write_text("html", encoding="utf-8")
    monkeypatch.setenv("RADAR_ORBIT_HANDOFF_ENABLED", "true")
    monkeypatch.setenv("RADAR_ORBIT_HANDOFF_ROOT", str(tmp_path / "handoff"))
    assert request_orbit_handoff(bad, task_id="dl_enel_sp", utility="ENEL")["ok"] is False
    valid = _pdf(tmp_path / "valid.pdf")
    assert request_orbit_handoff(valid, task_id="dl_cemig", utility="CEMIG")["ok"] is False
