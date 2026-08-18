from pathlib import Path

from core.downloaders.celesc import celesc_grupo_a


def test_request_handoff_uses_bt_task_id_when_tensao_is_bt(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(celesc_grupo_a, "TENSAO_GRUPO_A", "BT")
    monkeypatch.setattr(
        celesc_grupo_a,
        "_request_orbit_handoff",
        lambda path, **kw: calls.append({"path": Path(path), **kw}),
    )

    destino = tmp_path / "BB_2023747.pdf"
    destino.write_bytes(b"%PDF-1.4\n%%EOF")
    celesc_grupo_a._request_handoff(destino, run_id="1")

    assert len(calls) == 1
    assert calls[0]["task_id"] == "dl_celesc_bt"
    assert calls[0]["utility"] == "CELESC"


def test_request_handoff_uses_mt_task_id_when_tensao_is_mt(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(celesc_grupo_a, "TENSAO_GRUPO_A", "MT")
    monkeypatch.setattr(
        celesc_grupo_a,
        "_request_orbit_handoff",
        lambda path, **kw: calls.append({"path": Path(path), **kw}),
    )

    destino = tmp_path / "BB_2023996.pdf"
    destino.write_bytes(b"%PDF-1.4\n%%EOF")
    celesc_grupo_a._request_handoff(destino)

    assert len(calls) == 1
    assert calls[0]["task_id"] == "dl_celesc_mt"
    assert calls[0]["utility"] == "CELESC"


def test_request_handoff_is_fail_open(monkeypatch, tmp_path):
    def boom(*_a, **_k):
        raise RuntimeError("outbox indisponível")

    monkeypatch.setattr(celesc_grupo_a, "_request_orbit_handoff", boom)
    destino = tmp_path / "BB_2023747.pdf"
    destino.write_bytes(b"%PDF-1.4\n%%EOF")

    # Nao deve levantar excecao.
    celesc_grupo_a._request_handoff(destino)
