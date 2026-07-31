#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Filtro COPEL
===============

Le auditoria_resultados.csv da digitacao e move os PDFs por carimbo.
"""

from __future__ import annotations

import csv
import logging
import re
import shutil
import sys
import time
from pathlib import Path

try:
    from digitacao_consen.auditoria_schema import (
        extrair_status_auditoria,
        ler_auditoria_csv_flexivel,
    )
except ModuleNotFoundError:
    from auditoria_schema import (  # type: ignore
        extrair_status_auditoria,
        ler_auditoria_csv_flexivel,
    )


CSV_PATH = Path(
    __import__("os").environ.get(
        "COPEL_FILTRO_CSV",
        "//10.10.250.21/Energia/ARQUIVOS ENZO/COPEL_pipeline_saida/auditoria_resultados.csv",
    )
)
ROOT_PDFS = Path(
    __import__("os").environ.get(
        "COPEL_FILTRO_ROOT",
        "//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD COPEL/03.2026/BT",
    )
)
DESTINO = Path(
    __import__("os").environ.get(
        "COPEL_FILTRO_DESTINO",
        "//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO/Digitadas",
    )
)
DESTINO_EXISTENTES = Path(
    __import__("os").environ.get(
        "COPEL_FILTRO_JA_EXISTIAM",
        "//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO/Ja_existiam_no_Consen",
    )
)
ROTULO = __import__("os").environ.get("COPEL_FILTRO_ROTULO", "BT").strip().upper() or "BT"

_COUNTER_FILE = Path(r"\\10.10.250.21\Energia\ARQUIVOS ENZO\indice_master_next.txt")

STATUS_NAO_MOVER: set[str] = set()
STATUS_JA_EXISTIAM = {"pulado_referencia_existente"}
STATUS_MOVER_EXATOS = {"sucesso_auditoria", "auditoria_sem_valor", "pulado_carimbo_existente"}
TENTATIVAS_MOVER = 3
ESPERA = 1


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("copel_filtro")


def _normalizar(v) -> str:
    return "" if v is None else str(v).strip()


def _normalizar_carimbo(v) -> str:
    txt = _normalizar(v).upper().replace("BB_", "")
    return txt[:-2] if txt.endswith(".0") else txt


def _ler_proximo_carimbo() -> int:
    try:
        return int(_COUNTER_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        raise RuntimeError(f"Nao foi possivel ler o contador de carimbos em {_COUNTER_FILE}")


def _incrementar_carimbo(valor: int) -> None:
    _COUNTER_FILE.write_text(str(valor + 1), encoding="utf-8")


def _encontrar_pdf_por_instalacao(instalacao: str, raiz: Path) -> Path | None:
    inst = re.sub(r"\D", "", _normalizar(instalacao)).lstrip("0")
    for pdf in raiz.rglob("*.pdf"):
        nome_stem = _normalizar(pdf.stem)
        nome_digits = re.sub(r"\D", "", nome_stem).lstrip("0")
        nome_prefixo = re.sub(r"\D", "", nome_stem.split(" - ", 1)[0]).lstrip("0")
        if nome_digits == inst or nome_prefixo == inst:
            return pdf
    return None


def _encontrar_pdf(carimbo: str, instalacao: str, raiz: Path) -> Path | None:
    carimbo_norm = _normalizar_carimbo(carimbo)
    if carimbo_norm:
        alvo = f"BB_{carimbo_norm}.PDF"
        for pdf in raiz.rglob("*.pdf"):
            if pdf.name.upper() == alvo:
                return pdf

    if instalacao:
        return _encontrar_pdf_por_instalacao(instalacao, raiz)

    return None


def _deve_mover(status: str) -> bool:
    s = _normalizar(status).lower()
    if not s or s in STATUS_NAO_MOVER:
        return False
    if s in STATUS_JA_EXISTIAM or s in STATUS_MOVER_EXATOS:
        return True
    if s.startswith("erro_no_fluxo:"):
        return False
    return False


def _destino_para(status: str) -> Path:
    return DESTINO_EXISTENTES if _normalizar(status).lower() in STATUS_JA_EXISTIAM else DESTINO


def _ler_csv(csv_path: Path) -> list[dict]:
    rows = ler_auditoria_csv_flexivel(csv_path)
    if rows:
        return rows
    tentativas = [("utf-8-sig", ";"), ("utf-8-sig", ","), ("utf-8", ";"), ("utf-8", ","), ("latin1", ";"), ("latin1", ",")]
    last_err = None
    for enc, sep in tentativas:
        try:
            with csv_path.open("r", newline="", encoding=enc) as f:
                reader = csv.DictReader(f, delimiter=sep)
                rows = list(reader)
                if rows and len(reader.fieldnames or []) >= 2:
                    return rows
        except Exception as exc:
            last_err = exc
    raise RuntimeError(f"Falha ao ler CSV {csv_path}: {last_err}")


def _encontrar_pdfs_por_carimbo(carimbo: str, raiz: Path, instalacao: str = "") -> list[Path]:
    carimbo = _normalizar_carimbo(carimbo)
    matches = []
    for pdf in raiz.rglob("*.pdf"):
        nome = pdf.name.upper()
        if nome == f"BB_{carimbo}.PDF" or nome.startswith(f"BB_{carimbo}_"):
            matches.append(pdf)
    if not matches and instalacao:
        inst = _normalizar(instalacao).lstrip("0")
        for pdf in raiz.rglob("*.pdf"):
            nome = pdf.name.upper().replace("BB_", "").replace(".PDF", "").lstrip("0")
            if nome == inst:
                matches.append(pdf)
    return matches


def _destino_unico(dest: Path) -> Path:
    if not dest.exists():
        return dest
    i = 1
    while True:
        alt = dest.with_name(f"{dest.stem}_{i}{dest.suffix}")
        if not alt.exists():
            return alt
        i += 1


def _mover_com_retry(origem: Path, destino: Path) -> None:
    last_err = None
    for tentativa in range(1, TENTATIVAS_MOVER + 1):
        try:
            shutil.move(str(origem), str(destino))
            return
        except PermissionError as exc:
            last_err = exc
            log.warning(f"[retry {tentativa}/{TENTATIVAS_MOVER}] arquivo em uso: {origem.name}")
            time.sleep(ESPERA)
    if last_err:
        raise last_err


def main() -> int:
    log.info("=" * 64)
    log.info(f"FILTRO COPEL {ROTULO}")
    log.info("=" * 64)
    log.info(f"CSV   : {CSV_PATH}")
    log.info(f"ROOT  : {ROOT_PDFS}")
    log.info(f"DEST  : {DESTINO}")

    if not CSV_PATH.exists():
        log.error(f"CSV nao encontrado: {CSV_PATH}")
        return 1
    if not ROOT_PDFS.exists():
        log.error(f"Raiz de PDFs nao encontrada: {ROOT_PDFS}")
        return 1
    DESTINO.mkdir(parents=True, exist_ok=True)
    DESTINO_EXISTENTES.mkdir(parents=True, exist_ok=True)

    rows = _ler_csv(CSV_PATH)
    movidos = 0
    nao_encontrado = 0
    ignorados = 0
    falhas = 0
    ja_movidos: set[str] = set()

    for row in rows:
        status = _normalizar(extrair_status_auditoria(row)).lower()
        carimbo = _normalizar(row.get("carimbo", ""))
        instalacao = _normalizar(row.get("instalacao", ""))
        linha_excel = _normalizar(row.get("linha_excel", ""))

        if not _deve_mover(status):
            ignorados += 1
            continue

        pdf = _encontrar_pdf(carimbo, instalacao, ROOT_PDFS)
        if pdf is None:
            nao_encontrado += 1
            carimbo_log = _normalizar_carimbo(carimbo)
            detalhe = f"carimbo={carimbo_log} " if carimbo_log else ""
            log.warning(f"[nao encontrado] {detalhe}instalacao={instalacao} linha={linha_excel}")
            continue

        key = str(pdf.resolve()).lower()
        if key in ja_movidos:
            continue

        # Quando o PDF foi localizado pela instalação, o nome do arquivo pode ainda
        # estar sem o carimbo BB_XXXXXXX. Nesses casos priorizamos o carimbo
        # registrado na auditoria/digitação e caímos para o stem atual apenas como
        # último recurso.
        carimbo_pdf = _normalizar_carimbo(carimbo) or _normalizar_carimbo(pdf.stem) or pdf.stem
        dst_nome = f"BB_{carimbo_pdf}.pdf"
        pasta_dst = _destino_para(status)
        dst = _destino_unico(pasta_dst / dst_nome)
        try:
            _mover_com_retry(pdf, dst)
            ja_movidos.add(key)
            movidos += 1
            log.info(f"[movido] {pdf.name} -> {dst}  (carimbo={carimbo_pdf})")
        except Exception as exc:
            falhas += 1
            log.error(f"[erro] {pdf} -> {dst}: {exc}")

    log.info("")
    log.info(f"RESUMO FILTRO COPEL {ROTULO}")
    log.info(f"linhas lidas        : {len(rows)}")
    log.info(f"ignoradas           : {ignorados}")
    log.info(f"pdf nao encontrado  : {nao_encontrado}")
    log.info(f"movidos             : {movidos}")
    log.info(f"falhas              : {falhas}")
    return 0 if falhas == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
