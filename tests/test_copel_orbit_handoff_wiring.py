from pathlib import Path

from core.downloaders.copel import copel_bt


def test_request_handoff_calls_canonical_publisher_with_copel_bt_task(monkeypatch, tmp_path):
    calls = []

    def fake_publish(path, *, task_id, utility, run_id=None):
        calls.append({"path": Path(path), "task_id": task_id, "utility": utility, "run_id": run_id})
        return {"ok": True}

    monkeypatch.setattr(copel_bt, "_request_orbit_handoff", fake_publish)

    destino = tmp_path / "BB_2024099.pdf"
    destino.write_bytes(b"%PDF-1.4\n%%EOF")

    copel_bt._request_handoff(destino, run_id="77")

    assert len(calls) == 1
    assert calls[0]["path"] == destino
    assert calls[0]["task_id"] == "dl_copel_bt"
    assert calls[0]["utility"] == "COPEL"
    assert calls[0]["run_id"] == "77"


def test_request_handoff_is_fail_open_on_publisher_exception(monkeypatch, tmp_path):
    def boom(*_args, **_kwargs):
        raise RuntimeError("outbox indisponível")

    monkeypatch.setattr(copel_bt, "_request_orbit_handoff", boom)

    destino = tmp_path / "BB_2024099.pdf"
    destino.write_bytes(b"%PDF-1.4\n%%EOF")

    # Nao deve levantar excecao — uma falha no handoff jamais pode
    # interromper o loop de download do COPEL.
    copel_bt._request_handoff(destino)
