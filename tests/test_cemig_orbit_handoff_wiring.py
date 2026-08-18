from pathlib import Path

from core.downloaders.cemig import cemig


def test_request_handoff_calls_canonical_publisher_with_cemig_task(monkeypatch, tmp_path):
    calls = []

    def fake_publish(path, *, task_id, utility, run_id=None):
        calls.append({"path": Path(path), "task_id": task_id, "utility": utility, "run_id": run_id})
        return {"ok": True}

    monkeypatch.setattr(cemig, "_request_orbit_handoff", fake_publish)

    destino = tmp_path / "BB_2024020.pdf"
    destino.write_bytes(b"%PDF-1.4\n%%EOF")
    cemig._request_handoff(destino, run_id="42")

    assert len(calls) == 1
    assert calls[0]["path"] == destino
    assert calls[0]["task_id"] == "dl_cemig"
    assert calls[0]["utility"] == "CEMIG"
    assert calls[0]["run_id"] == "42"


def test_request_handoff_is_fail_open_on_publisher_exception(monkeypatch, tmp_path):
    def boom(*_args, **_kwargs):
        raise RuntimeError("outbox indisponível")

    monkeypatch.setattr(cemig, "_request_orbit_handoff", boom)

    destino = tmp_path / "BB_2024020.pdf"
    destino.write_bytes(b"%PDF-1.4\n%%EOF")

    # Nao deve levantar excecao.
    cemig._request_handoff(destino)
