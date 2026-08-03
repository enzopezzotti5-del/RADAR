#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Etapa 3 do pipeline CELESC.

Lê auditoria_resultados.csv da digitação, move os PDFs digitados da árvore
DOWNLOAD CELESC para a pasta de Digitadas e marca no índice master.

Variáveis de ambiente (usadas pelo pipeline):
    CELESC_FILTRO_CSV     — caminho do auditoria_resultados.csv
    CELESC_FILTRO_ROOT    — raiz dos PDFs (ex: DOWNLOAD CELESC\\04.2026\\MT)
    CELESC_FILTRO_DESTINO — pasta Digitadas
"""

from __future__ import annotations

import csv
import logging
import os
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


CSV_PATH          = Path(os.environ.get("CELESC_FILTRO_CSV",         "//10.10.250.21/Energia/ARQUIVOS ENZO/CELESC_pipeline_saida/auditoria_resultados.csv"))
ROOT_PDFS         = Path(os.environ.get("CELESC_FILTRO_ROOT",        "//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD CELESC"))
DESTINO           = Path(os.environ.get("CELESC_FILTRO_DESTINO",     "//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO/Digitadas"))
DESTINO_EXISTENTES= Path(os.environ.get("CELESC_FILTRO_JA_EXISTIAM", "//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO/Ja_existiam_no_Consen"))
DESTINO_INVESTIGAR= Path(os.environ.get("CELESC_FILTRO_INVESTIGAR",  "//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO/Watcher_V2/Investigar"))

TENTATIVAS_MOVER = 3
ESPERA = 1

STATUS_NAO_MOVER    = set()
STATUS_INVESTIGAR   = {"auditoria_sem_valor", "erro_referencia_nao_abriu"}
STATUS_MOVER        = {"sucesso_auditoria", "pulado_carimbo_existente",
                       "pulado_referencia_existente"} | STATUS_INVESTIGAR
STATUS_JA_EXISTIAM  = {"pulado_referencia_existente"}


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("celesc_filtro")


def _norm(v) -> str:
    return str(v).strip() if v is not None else ""


def _norm_carimbo(v) -> str:
    txt = _norm(v).upper().replace("BB_", "")
    return txt[:-2] if txt.endswith(".0") else txt


def _deve_mover(status: str) -> bool:
    s = _norm(status).lower()
    return s in STATUS_MOVER


def _ler_csv(path: Path) -> list[dict]:
    rows = ler_auditoria_csv_flexivel(path)
    if rows:
        return rows
    for enc, sep in [("utf-8-sig", ";"), ("utf-8-sig", ","), ("utf-8", ";"), ("latin1", ";")]:
        try:
            with path.open("r", newline="", encoding=enc) as f:
                reader = csv.DictReader(f, delimiter=sep)
                rows = list(reader)
                if rows and len(reader.fieldnames or []) >= 2:
                    return rows
        except Exception:
            continue
    raise RuntimeError(f"Não foi possível ler CSV: {path}")


def _encontrar_pdfs(carimbo: str, raiz: Path) -> list[Path]:
    """Busca recursivamente o PDF pelo carimbo dentro da raiz."""
    c = _norm_carimbo(carimbo)
    exatos = {f"BB_{c}.pdf".upper(), f"{c}.pdf".upper()}
    encontrados = []
    for p in raiz.rglob("*.pdf"):
        if p.name.upper() in exatos:
            encontrados.append(p)
    return encontrados


def _destino_unico(dest: Path) -> Path:
    if not dest.exists():
        return dest
    i = 1
    while True:
        alt = dest.with_name(f"{dest.stem}_{i}{dest.suffix}")
        if not alt.exists():
            return alt
        i += 1


def _mover(origem: Path, destino: Path) -> None:
    for tentativa in range(1, TENTATIVAS_MOVER + 1):
        try:
            shutil.move(str(origem), str(destino))
            return
        except PermissionError as exc:
            log.warning(f"  [retry {tentativa}/{TENTATIVAS_MOVER}] arquivo em uso: {origem.name}")
            time.sleep(ESPERA)
            last = exc
    raise last


def main() -> int:
    log.info("=" * 60)
    log.info("  FILTRO CELESC")
    log.info("=" * 60)
    log.info(f"  CSV    : {CSV_PATH}")
    log.info(f"  ROOT   : {ROOT_PDFS}")
    log.info(f"  DESTINO: {DESTINO}")

    if not CSV_PATH.exists():
        log.error(f"CSV não encontrado: {CSV_PATH}")
        return 1
    if not ROOT_PDFS.exists():
        log.error(f"Raiz de PDFs não encontrada: {ROOT_PDFS}")
        return 1

    try:
        DESTINO.mkdir(parents=True, exist_ok=True)
        DESTINO_EXISTENTES.mkdir(parents=True, exist_ok=True)
        DESTINO_INVESTIGAR.mkdir(parents=True, exist_ok=True)
    except OSError:
        if not DESTINO.is_dir():
            log.error(f"Destino inacessível: {DESTINO}")
            return 1

    rows = _ler_csv(CSV_PATH)
    movidos = 0
    sem_carimbo = 0
    nao_encontrado = 0
    ignorados = 0
    falhas = 0
    ja_movidos: set[str] = set()

    for row in rows:
        status  = _norm(extrair_status_auditoria(row))
        carimbo = _norm_carimbo(row.get("carimbo", ""))

        if not _deve_mover(status):
            ignorados += 1
            continue

        if not carimbo:
            sem_carimbo += 1
            log.warning(f"  [sem carimbo] status={status}")
            continue

        candidatos = _encontrar_pdfs(carimbo, ROOT_PDFS)
        if not candidatos:
            nao_encontrado += 1
            log.warning(f"  [não encontrado] BB_{carimbo}.pdf")
            continue

        if status in STATUS_JA_EXISTIAM:
            pasta_dst = DESTINO_EXISTENTES
        elif status in STATUS_INVESTIGAR:
            pasta_dst = DESTINO_INVESTIGAR
        else:
            pasta_dst = DESTINO
        for pdf in candidatos:
            key = str(pdf.resolve()).lower()
            if key in ja_movidos:
                continue
            dst = _destino_unico(pasta_dst / pdf.name)
            try:
                _mover(pdf, dst)
                ja_movidos.add(key)
                movidos += 1
                log.info(f"  [movido] {pdf.name} -> {dst.name}")
            except Exception as exc:
                falhas += 1
                log.error(f"  [erro] {pdf.name}: {exc}")

    log.info("")
    log.info(f"  Lidas      : {len(rows)}")
    log.info(f"  Ignoradas  : {ignorados}")
    log.info(f"  Sem carimbo: {sem_carimbo}")
    log.info(f"  Não achadas: {nao_encontrado}")
    log.info(f"  Movidos    : {movidos}")
    log.info(f"  Falhas     : {falhas}")
    return 0 if falhas == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
