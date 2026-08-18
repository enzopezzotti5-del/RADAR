import logging

from scripts.infra import radar_orbit_daily_publisher as publisher


def _write_master_csv(path, bbs):
    path.write_text(
        "INDICE,ARQUIVO\n" + "".join(f"{bb},\n" for bb in bbs),
        encoding="utf-8-sig",
    )


def test_scan_ready_finds_bb_pdfs_across_scan_sources(tmp_path, monkeypatch):
    copel_dir = tmp_path / "DOWNLOAD COPEL" / "08.2026" / "BT"
    copel_dir.mkdir(parents=True)
    (copel_dir / "BB_2024004.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
    (copel_dir / "BB_2024005.pdf").write_bytes(b"%PDF-1.4\n%%EOF")

    monkeypatch.setattr(
        publisher,
        "SCAN_SOURCES",
        [("dl_copel_bt", "COPEL", str(copel_dir))],
    )

    known = {"BB_2024004", "BB_2024005"}
    ready = publisher._scan_ready(skip=set(), known_bbs=known, log=logging.getLogger("test"))

    found = {bb for bb, *_ in ready}
    assert found == {"BB_2024004", "BB_2024005"}
    assert all(task_id == "dl_copel_bt" and utility == "COPEL" for _, task_id, utility, _ in ready)


def test_scan_ready_skips_already_terminal_or_staged(tmp_path, monkeypatch):
    d = tmp_path / "DOWNLOAD CELESC" / "08.2026" / "BT"
    d.mkdir(parents=True)
    (d / "BB_2023747.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
    (d / "BB_2023748.pdf").write_bytes(b"%PDF-1.4\n%%EOF")

    monkeypatch.setattr(publisher, "SCAN_SOURCES", [("dl_celesc_bt", "CELESC", str(d))])

    known = {"BB_2023747", "BB_2023748"}
    ready = publisher._scan_ready(
        skip={"BB_2023747"}, known_bbs=known, log=logging.getLogger("test")
    )

    found = {bb for bb, *_ in ready}
    assert found == {"BB_2023748"}


def test_scan_ready_skips_bb_missing_from_master_index(tmp_path, monkeypatch, caplog):
    d = tmp_path / "DOWNLOAD CELESC" / "08.2026" / "BT"
    d.mkdir(parents=True)
    (d / "BB_2023747.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
    # BB_2000001 simulates the IndiceLocalCelesc fallback counter (started at
    # 2_000_000 when carregar_master() failed at download time) — it was
    # never reserved in the real master index and must not be delivered.
    (d / "BB_2000001.pdf").write_bytes(b"%PDF-1.4\n%%EOF")

    monkeypatch.setattr(publisher, "SCAN_SOURCES", [("dl_celesc_bt", "CELESC", str(d))])

    known = {"BB_2023747"}  # BB_2000001 deliberately absent
    with caplog.at_level(logging.WARNING):
        ready = publisher._scan_ready(skip=set(), known_bbs=known, log=logging.getLogger("test"))

    found = {bb for bb, *_ in ready}
    assert found == {"BB_2023747"}
    assert "SKIP_UNKNOWN_BB" in caplog.text
    assert "BB_2000001" in caplog.text


def test_scan_ready_empty_known_bbs_disables_the_master_check(tmp_path, monkeypatch):
    """When the master index cannot be read at all, known_bbs is empty and the
    safety check must not accidentally skip every single PDF."""
    d = tmp_path / "DOWNLOAD CEMIG" / "08.2026" / "BT"
    d.mkdir(parents=True)
    (d / "BB_2099999.pdf").write_bytes(b"%PDF-1.4\n%%EOF")

    monkeypatch.setattr(publisher, "SCAN_SOURCES", [("dl_cemig", "CEMIG", str(d))])

    ready = publisher._scan_ready(skip=set(), known_bbs=set(), log=logging.getLogger("test"))

    assert {bb for bb, *_ in ready} == {"BB_2099999"}


def test_load_all_master_bbs_reads_indice_column(tmp_path, monkeypatch):
    master_csv = tmp_path / "indice_master.csv"
    _write_master_csv(master_csv, ["BB_2024004", "BB_2024005"])
    monkeypatch.setattr(publisher, "MASTER", str(master_csv))

    assert publisher._load_all_master_bbs() == {"BB_2024004", "BB_2024005"}


def test_active_downloader_prefixes_cover_all_scan_source_task_ids():
    scanned_task_ids = {task_id for task_id, _, _ in publisher.SCAN_SOURCES}
    for task_id in scanned_task_ids:
        assert any(task_id.startswith(p) for p in publisher.ACTIVE_DOWNLOADER_TASK_PREFIXES), (
            f"{task_id} nao e coberto por ACTIVE_DOWNLOADER_TASK_PREFIXES — o guard de "
            "'downloader ativo' nao pausaria a publicacao em lote durante um run real"
        )
