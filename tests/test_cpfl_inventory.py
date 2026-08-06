import csv
from pathlib import Path

from core.downloaders.cpfl.cpfl_inventory import (
    build_canonical_rows,
    build_pdf_sha_index,
    load_pdf_sha_index,
    normalize_uc,
)


def _row(uc: str, status: str, scan: str, titular: str = "T1", perfil: str = "BT") -> dict:
    return {"TITULAR_ID": titular, "TITULAR_TEXTO": titular, "PERFIL": perfil, "UC": uc,
            "STATUS": status, "LINHA": "linha", "DATA_SCAN": scan}


def test_canonical_key_uses_real_installation_not_holder() -> None:
    rows = [
        _row("001234", "ATIVA", "01/08/2026 10:00:00", "ANTIGO"),
        _row("1234", "ATIVA", "02/08/2026 10:00:00", "NOVO"),
    ]
    canonical, metrics = build_canonical_rows(rows)
    assert len(canonical) == 1
    assert canonical[0]["CANONICAL_KEY"] == "CPFL:1234"
    assert canonical[0]["TITULAR_ID"] == "NOVO"
    assert metrics["DUPLICATE_ROWS_REMOVED"] == 1


def test_latest_inactive_installation_is_not_operational() -> None:
    rows = [
        _row("1234", "ATIVA", "01/08/2026 10:00:00"),
        _row("1234", "INATIVA", "02/08/2026 10:00:00"),
    ]
    canonical, metrics = build_canonical_rows(rows)
    assert canonical == []
    assert metrics["UNIQUE_UCS"] == 1
    assert metrics["ACTIVE_UNIQUE_UCS"] == 0
    assert metrics["INACTIVE_UCS"] == 1


def test_exact_manual_duplicate_is_counted() -> None:
    row = _row("1234", "ATIVA", "01/08/2026 10:00:00")
    _, metrics = build_canonical_rows([row, dict(row)])
    assert metrics["MANUAL_DUPLICATE_ROWS"] == 1


def test_invalid_installations_are_excluded() -> None:
    canonical, metrics = build_canonical_rows([_row("", "ATIVA", "01/08/2026 10:00:00")])
    assert canonical == []
    assert metrics["VALID_IDENTIFIED_ROWS"] == 0


def test_pdf_sha_index_detects_duplicate_content(tmp_path: Path) -> None:
    first = tmp_path / "a.pdf"; second = tmp_path / "b.pdf"
    first.write_bytes(b"same-pdf"); second.write_bytes(b"same-pdf")
    target = tmp_path / "sha.csv"
    index = build_pdf_sha_index(tmp_path, target)
    assert len(index) == 1
    assert len(list(csv.DictReader(target.open(encoding="utf-8-sig")))) == 2
    assert load_pdf_sha_index(target) == index


def test_normalize_uc_rejects_missing_identifier() -> None:
    assert normalize_uc("UC 000123") == "123"
    assert normalize_uc("sem identificador") == ""
