#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Etapa 3 do pipeline Neoenergia.

Le auditoria_resultados.csv da digitacao e move os PDFs por carimbo
da arvore DOWNLOAD NEOENERGIA para a pasta de Digitadas.
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


CSV_PATH = Path(
    "//10.10.250.21/Energia/ARQUIVOS ENZO/NEOENERGIA_pipeline_saida/auditoria_resultados.csv"
)
ROOT_PDFS = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD NEOENERGIA")
DESTINO = Path("//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO/Digitadas")
DESTINO_EXISTENTES = DESTINO.parent / "Ja_existiam_no_Consen"
DESTINO_INVESTIGAR = DESTINO.parent / "Watcher_V2" / "Investigar"

CSV_PATH = Path(str(os.environ.get("NEO_FILTRO_CSV", CSV_PATH)))
ROOT_PDFS = Path(str(os.environ.get("NEO_FILTRO_ROOT", ROOT_PDFS)))
DESTINO = Path(str(os.environ.get("NEO_FILTRO_DESTINO", DESTINO)))
DESTINO_EXISTENTES = Path(str(os.environ.get("NEO_FILTRO_DESTINO_EXISTENTES", DESTINO_EXISTENTES)))
DESTINO_INVESTIGAR = Path(str(os.environ.get("NEO_FILTRO_DESTINO_INVESTIGAR", DESTINO_INVESTIGAR)))

TENTATIVAS_MOVER = 3
ESPERA = 1

STATUS_EXISTENTE_CONSEN = {"pulado_referencia_existente"}
STATUS_NAO_MOVER: set[str] = set()
STATUS_INVESTIGAR = {"auditoria_sem_valor"}
STATUS_MOVER_EXATOS = {"sucesso_auditoria", "pulado_carimbo_existente"}
STATUS_CONHECIDOS = STATUS_NAO_MOVER | STATUS_INVESTIGAR | STATUS_EXISTENTE_CONSEN | STATUS_MOVER_EXATOS


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("neoenergia_filtro")


def _normalizar(v) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _normalizar_carimbo(v) -> str:
    txt = _normalizar(v)
    txt = txt.upper().replace("BB_", "")
    if txt.endswith(".0"):
        txt = txt[:-2]
    return txt


def _deve_mover(status: str) -> bool:
    s = _normalizar(status).lower()
    if not s:
        return False
    if s in STATUS_NAO_MOVER:
        return False
    if s in STATUS_EXISTENTE_CONSEN:
        return True
    if s in STATUS_INVESTIGAR:
        return True
    if s in STATUS_MOVER_EXATOS:
        return True
    if s.startswith("erro_no_fluxo:"):
        return False
    return False


def _destino_por_status(status: str) -> Path:
    s = _normalizar(status).lower()
    if s in STATUS_EXISTENTE_CONSEN:
        return DESTINO_EXISTENTES
    if s in STATUS_INVESTIGAR:
        return DESTINO_INVESTIGAR
    return DESTINO


def _extrair_status_row(row: dict) -> str:
    """
    Suporta auditoria_resultados.csv com cabecalho antigo (6 colunas) e
    linhas novas (8 colunas) appendadas no mesmo arquivo.
    """
    status_padrao = extrair_status_auditoria(row)
    if status_padrao:
        return status_padrao

    candidatos: list[str] = []

    status = _normalizar(row.get("status", ""))
    if status:
        candidatos.append(status)

    extras = row.get(None) or []
    if not isinstance(extras, list):
        extras = [extras]
    candidatos.extend(_normalizar(item) for item in extras if _normalizar(item))

    for cand in reversed(candidatos):
        cand_norm = cand.lower()
        if cand_norm in STATUS_CONHECIDOS or cand_norm.startswith("erro_no_fluxo:"):
            return cand_norm

    return _normalizar(status).lower()


def _ler_csv_flex(csv_path: Path) -> list[dict]:
    rows = ler_auditoria_csv_flexivel(csv_path)
    if rows:
        return rows
    tentativas = [
        ("utf-8-sig", ";"),
        ("utf-8-sig", ","),
        ("utf-8", ";"),
        ("utf-8", ","),
        ("latin1", ";"),
        ("latin1", ","),
    ]
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


def _encontrar_pdfs_por_carimbo(carimbo: str, raiz: Path) -> list[Path]:
    encontrados: list[Path] = []
    carimbo = _normalizar_carimbo(carimbo)
    padroes_exatos = {
        f"BB_{carimbo}.pdf",
        f"{carimbo}.pdf",
    }
    padroes_prefixo = (
        f"BB_{carimbo}_",
        f"{carimbo}_",
    )

    for p in raiz.rglob("*.pdf"):
        nome = p.name.upper()
        if nome in {x.upper() for x in padroes_exatos} or any(nome.startswith(prefix.upper()) for prefix in padroes_prefixo):
            encontrados.append(p)

    return encontrados


def _destino_unico(dest: Path) -> Path:
    """Retorna dest se não existe. Levanta FileExistsError se já existe,
    em vez de criar nomes _1, _2… que mascaram colisões de carimbo."""
    if not dest.exists():
        return dest
    raise FileExistsError(dest)


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


def _garantir_dir(pasta: Path, rotulo: str) -> bool:
    try:
        pasta.mkdir(parents=True, exist_ok=True)
        return True
    except OSError as exc:
        # Em UNC paths o mkdir pode lancar WinError 1168 mesmo quando o
        # diretorio ja existe no servidor. Verifica antes de abortar.
        try:
            acessivel = pasta.is_dir()
        except OSError:
            acessivel = False
        if not acessivel:
            log.error(f"{rotulo} nao acessivel e nao pode ser criado: {pasta} ({exc})")
            return False
    return True


def main() -> int:
    log.info("=" * 64)
    log.info("FILTRO NEOENERGIA")
    log.info("=" * 64)
    log.info(f"CSV   : {CSV_PATH}")
    log.info(f"ROOT  : {ROOT_PDFS}")
    log.info(f"DEST  : {DESTINO}")
    log.info(f"EXIST : {DESTINO_EXISTENTES}")
    log.info(f"INVEST: {DESTINO_INVESTIGAR}")

    try:
        if not CSV_PATH.exists():
            log.error(f"CSV nao encontrado: {CSV_PATH}")
            return 1
    except PermissionError as exc:
        log.error(f"Sem permissao para acessar CSV: {CSV_PATH} ({exc})")
        return 1

    try:
        if not ROOT_PDFS.exists():
            log.error(f"Raiz de PDFs nao encontrada: {ROOT_PDFS}")
            return 1
    except PermissionError as exc:
        log.error(f"Sem permissao para acessar raiz de PDFs: {ROOT_PDFS} ({exc})")
        return 1

    if not _garantir_dir(DESTINO, "Destino"):
        return 1
    if not _garantir_dir(DESTINO_EXISTENTES, "Destino de existentes"):
        return 1
    if not _garantir_dir(DESTINO_INVESTIGAR, "Destino de investigar"):
        return 1
    rows = _ler_csv_flex(CSV_PATH)

    movidos = 0
    movidos_existentes = 0
    movidos_investigar = 0
    sem_carimbo = 0
    nao_encontrado = 0
    ignorados = 0
    falhas = 0
    ja_movidos: set[str] = set()

    for row in rows:
        status = _extrair_status_row(row)
        carimbo = _normalizar_carimbo(row.get("carimbo", ""))
        linha_excel = _normalizar(row.get("linha_excel", ""))

        if not _deve_mover(status):
            ignorados += 1
            continue

        if not carimbo:
            sem_carimbo += 1
            log.warning(f"[sem carimbo] linha={linha_excel} status={status}")
            continue

        candidatos = _encontrar_pdfs_por_carimbo(carimbo, ROOT_PDFS)
        if not candidatos:
            nao_encontrado += 1
            log.warning(f"[nao encontrado] carimbo={carimbo} linha={linha_excel}")
            continue

        for pdf in candidatos:
            key = str(pdf.resolve()).lower()
            if key in ja_movidos:
                continue
            destino_base = _destino_por_status(status)
            try:
                dst = _destino_unico(destino_base / pdf.name)
            except FileExistsError:
                log.warning(
                    f"[pulado] {pdf.name} ja existe em {destino_base.name} "
                    f"— possivel re-run ou colisao de carimbo. Verifique manualmente."
                )
                ja_movidos.add(key)
                continue
            try:
                _mover_com_retry(pdf, dst)
                ja_movidos.add(key)
                if destino_base == DESTINO_EXISTENTES:
                    movidos_existentes += 1
                elif destino_base == DESTINO_INVESTIGAR:
                    movidos_investigar += 1
                else:
                    movidos += 1
                log.info(f"[movido] {pdf.name} -> {dst.name}")
            except Exception as exc:
                falhas += 1
                log.error(f"[erro] {pdf} -> {dst}: {exc}")

    log.info("")
    log.info("RESUMO FILTRO NEOENERGIA")
    log.info(f"linhas lidas        : {len(rows)}")
    log.info(f"ignoradas           : {ignorados}")
    log.info(f"sem carimbo         : {sem_carimbo}")
    log.info(f"pdf nao encontrado  : {nao_encontrado}")
    log.info(f"movidos digitadas   : {movidos}")
    log.info(f"movidos existentes  : {movidos_existentes}")
    log.info(f"movidos investigar  : {movidos_investigar}")
    log.info(f"falhas              : {falhas}")

    return 0 if falhas == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
