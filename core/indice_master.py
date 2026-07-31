"""API canônica, sem efeitos de CLI, para atualizações seguras do índice mestre."""
from __future__ import annotations

import csv
import datetime as dt
import os
import re
import tempfile
from pathlib import Path

try:
    from scripts.infra.indice_master import (  # compatibilidade para parsers legados
        MasterIndice,
        marcar_digitados_do_auditoria as _legacy_marcar_digitados_do_auditoria,
    )
except Exception:  # pragma: no cover
    MasterIndice = None
    _legacy_marcar_digitados_do_auditoria = None

CARIMBO_RE = re.compile(r"^BB_\d+$")
DEFAULT_MASTER = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/indice_master.csv")
MASTER_FILE = DEFAULT_MASTER
MASTER_FIELDS = [
    "INDICE",
    "CONCESSIONARIA",
    "SISTEMA",
    "ESTADO",
    "UC",
    "MES_REF",
    "FATURA_ID",
    "CNPJ",
    "DATA_DOWNLOAD",
    "ARQUIVO",
    "STATUS_DIGITACAO",
    "DATA_DIGITACAO",
]

# Mapeamento do status do auditoria_resultados.csv para STATUS_DIGITACAO.
# Deve permanecer sincronizado com scripts/infra/indice_master._STATUS_AUDITORIA_PARA_MASTER.
AUDITORIA_PARA_STATUS: dict[str, str] = {
    "sucesso_auditoria":           "DIGITADO",
    "auditoria_sem_valor":         "PENDENTE",
    "pulado_carimbo_existente":    "DIGITADO",
    "pulado_referencia_existente": "PULADO",
}

_NORMALIZA_RE = re.compile(r"^\d+$")


class IndiceMasterError(RuntimeError):
    pass


def _normalizar_bb(valor: str) -> str:
    txt = (valor or "").strip().upper()
    if not txt:
        return ""
    if txt.endswith(".0"):
        txt = txt[:-2]
    if txt.startswith("BB_"):
        return txt
    if _NORMALIZA_RE.fullmatch(txt):
        return f"BB_{txt}"
    return txt


def _read(path: Path) -> tuple[list[str], list[dict[str, str]], str]:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with path.open(encoding=encoding, newline="") as fh:
                reader = csv.DictReader(fh)
                return list(reader.fieldnames or []), list(reader), encoding
        except UnicodeDecodeError:
            continue
    raise IndiceMasterError(f"encoding inválido: {path}")


def atualizar_status_carimbos(
    carimbos: list[str],
    status: str,
    *,
    master_path: Path = DEFAULT_MASTER,
    data_digitacao: str | None = None,
) -> int:
    """Atualiza STATUS_DIGITACAO (e opcionalmente DATA_DIGITACAO) de forma atômica.

    Nunca cria carimbos. Levanta IndiceMasterError em caso de inconsistência —
    não há fallback silencioso.
    """
    if not carimbos or any(not CARIMBO_RE.fullmatch(c) for c in carimbos):
        raise IndiceMasterError("carimbo inválido")
    if len(set(carimbos)) != len(carimbos):
        raise IndiceMasterError("carimbo repetido na requisição")
    fields, rows, encoding = _read(master_path)
    positions = {c: [i for i, row in enumerate(rows) if row.get("INDICE", "") == c] for c in carimbos}
    invalid = {c: p for c, p in positions.items() if len(p) != 1}
    if invalid:
        raise IndiceMasterError(f"carimbo ausente ou duplicado: {invalid}")
    changed = 0
    status_norm = (status or "").strip().upper()
    agora = data_digitacao or dt.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    for carimbo, pos in positions.items():
        row = rows[pos[0]]
        status_atual = row.get("STATUS_DIGITACAO", "")
        data_atual = row.get("DATA_DIGITACAO", "")
        data_alvo = (
            data_atual or agora
            if status_norm in {"DIGITADO", "PULADO"}
            else ""
        )
        data_divergente = "DATA_DIGITACAO" in fields and data_atual != data_alvo
        if status_atual != status_norm or data_divergente:
            row["STATUS_DIGITACAO"] = status_norm
            if "DATA_DIGITACAO" in fields:
                row["DATA_DIGITACAO"] = data_alvo
            changed += 1
    if not changed:
        return 0
    fd, temp_name = tempfile.mkstemp(prefix="indice_master_", suffix=".tmp", dir=master_path.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp_name, master_path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return changed


def marcar_digitados_auditoria(
    auditoria_csv: "Path | str",
    *,
    master_path: Path = DEFAULT_MASTER,
) -> dict[str, int]:
    """Lê auditoria_resultados.csv e atualiza STATUS_DIGITACAO no índice mestre.

    Retorna contadores: {'digitado': N, 'pulado': N, 'ignorado': N}.
    Levanta IndiceMasterError se a atualização falhar — nunca silencia o erro.
    """
    auditoria_path = Path(auditoria_csv)
    if not auditoria_path.exists():
        raise IndiceMasterError(f"auditoria_resultados.csv não encontrado: {auditoria_path}")

    por_status: dict[str, list[str]] = {}  # status_master → [BB_xxx, ...]
    contadores: dict[str, int] = {"digitado": 0, "pulado": 0, "ignorado": 0}

    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with auditoria_path.open(newline="", encoding=enc) as fh:
                reader = csv.DictReader(fh, delimiter=";")
                for row in reader:
                    carimbo_raw = (row.get("carimbo") or "").strip()
                    status_audit = (row.get("status") or "").strip().lower()
                    if not carimbo_raw:
                        # Último campo pode conter o status quando há ; extras
                        cols = list(row.values())
                        if cols:
                            status_audit = cols[-1].strip().lower()
                        carimbo_raw = ""
                    bb = _normalizar_bb(carimbo_raw)
                    if not bb or not CARIMBO_RE.fullmatch(bb):
                        contadores["ignorado"] += 1
                        continue
                    if status_audit in AUDITORIA_PARA_STATUS:
                        status_master = AUDITORIA_PARA_STATUS[status_audit]
                    elif status_audit.startswith("erro"):
                        status_master = "ERRO"
                    else:
                        contadores["ignorado"] += 1
                        continue
                    por_status.setdefault(status_master, []).append(bb)
            break
        except UnicodeDecodeError:
            continue

    for status_master, carimbos in por_status.items():
        n = atualizar_status_carimbos(carimbos, status_master, master_path=master_path)
        chave = status_master.lower()
        contadores[chave] = contadores.get(chave, 0) + n

    return contadores


def marcar_digitados_do_auditoria(
    auditoria_csv: "Path | str",
    master=None,
) -> dict[str, int]:
    """Compatibilidade para pipelines legados que importam indice_master plano."""
    if _legacy_marcar_digitados_do_auditoria is not None:
        if master is None:
            if MasterIndice is None:
                raise IndiceMasterError("MasterIndice indisponível")
            master = MasterIndice()
        return _legacy_marcar_digitados_do_auditoria(auditoria_csv, master)
    return marcar_digitados_auditoria(auditoria_csv)
