#!/usr/bin/env python3
"""
pipeline_cemig_190.py  —  Pipeline CEMIG para as 190 instalacoes especificas
=============================================================================
Mesmo fluxo do pipeline_cemig.py (OCR -> Digitacao -> Filtro),
mas lendo os PDFs de:
    DOWNLOAD CEMIG\\Faltantes\\MM.AAAA\\BT|MT

Uso:
    python pipeline_cemig_190.py                    # mes/ano atual
    python pipeline_cemig_190.py --mes 03 --ano 2026
    python pipeline_cemig_190.py --so-ocr
    python pipeline_cemig_190.py --so-digitacao
    python pipeline_cemig_190.py --so-filtro
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import subprocess
import sys
from pathlib import Path

try:
    from core.pipelines._visual import _p, _info, _ok, _fail, _warn, _sep, _banner, _rodar as _rodar_visual
except ModuleNotFoundError:
    from _visual import _p, _info, _ok, _fail, _warn, _sep, _banner, _rodar as _rodar_visual

# =============================================================================
# CONFIGURACAO
# =============================================================================

RAIZ            = Path(r"C:\Users\Revit\Desktop\ENERGIA")
SERVIDOR        = Path("//10.10.250.21/Energia")
PASTA_DOWNLOAD  = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD CEMIG")

# Pasta raiz dos PDFs das 190 — difere do pipeline normal aqui
FALTANTES_DIR   = PASTA_DOWNLOAD / "Faltantes"

PASTA_OCR       = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/OCR CEMIG")
PASTA_LOGS      = Path(__file__).resolve().parent / "logs"
PIPELINE_SAIDA  = SERVIDOR / "ARQUIVOS ENZO" / "CEMIG_pipeline_saida"

SCRIPT_OCR       = RAIZ / "ocr"              / "OCR_Cemig.py"
SCRIPT_DIGITACAO = RAIZ / "digitacao_consen" / "digitacao_consen_cemig.py"
SCRIPT_FILTRO    = RAIZ / "digitacao_consen" / "cemig_filtro.py"

# =============================================================================
# LOGGING
# =============================================================================

def _mkdir_seguro(pasta: Path) -> None:
    try:
        pasta.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


_mkdir_seguro(PASTA_LOGS)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            PASTA_LOGS / "pipeline_cemig_190.log",
            encoding="utf-8",
            errors="replace",
        ),
    ],
)
log = logging.getLogger("pipeline_cemig_190")


# =============================================================================
# HELPERS
# =============================================================================

def _xlsx_bt(mes: str, ano: str) -> Path:
    return PASTA_OCR / f"ocr_cemig_BT_{mes}{ano}.xlsx"

def _xlsx_mt(mes: str, ano: str) -> Path:
    return PASTA_OCR / f"ocr_cemig_MT_{mes}{ano}.xlsx"

def _pasta_mes_faltantes(mes: str, ano: str) -> Path:
    """Retorna o Path da subpasta de mes dentro de Faltantes."""
    for sep in [".", "-", "_", " ", ""]:
        p = FALTANTES_DIR / f"{mes}{sep}{ano}"
        try:
            if p.is_dir():
                return p
        except OSError:
            return p
    return FALTANTES_DIR / f"{mes}.{ano}"

def _script_digitacao() -> Path | None:
    candidatos = [
        SCRIPT_DIGITACAO,
        RAIZ / "digitacao_consen" / "digitacao_consen_cemig.py",
    ]
    candidatos += sorted((RAIZ / "digitacao_consen").glob("*Consen*CEMIG.py"))
    return next((p for p in candidatos if p.exists()), None)


def _rodar(descricao: str, cmd: list, env_extra: dict | None = None) -> bool:
    """Executa subprocess com saída visual. Retorna True se exit 0."""
    code = _rodar_visual(descricao, cmd, env_extra=env_extra)
    if code == 130:
        return False
    return code == 0


# =============================================================================
# ETAPAS
# =============================================================================

def etapa_ocr(mes: str, ano: str, tipo: str = "ambos") -> bool:
    log.info("=" * 60)
    log.info(f"  ETAPA 1 — OCR CEMIG {tipo.upper()} (Faltantes)")
    log.info("=" * 60)
    if not SCRIPT_OCR.exists():
        log.error(f"  Script nao encontrado: {SCRIPT_OCR}")
        return False
    cmd = [
        sys.executable, str(SCRIPT_OCR),
        "--tipo", tipo,
        "--mes",  mes,
        "--ano",  ano,
        "--base-dir", str(FALTANTES_DIR),   # <-- aponta para Faltantes
    ]
    return _rodar(f"OCR {tipo.upper()}", cmd)


def etapa_digitacao(xlsx: Path, mes: str, ano: str, tipo: str) -> bool:
    log.info("=" * 60)
    log.info(f"  ETAPA 2 — DIGITACAO CEMIG {tipo.upper()} (Faltantes)")
    log.info("=" * 60)
    script = _script_digitacao()
    if script is None:
        log.error("  Script de digitacao CEMIG nao encontrado.")
        return False
    if not xlsx.exists():
        if tipo == "mt":
            log.warning(f"  xlsx MT nao encontrado — sem MT no mes: {xlsx}")
            return True   # nao e falha critica
        log.error(f"  xlsx {tipo.upper()} nao encontrado: {xlsx}")
        return False

    # Pasta de origem: Faltantes/MM.AAAA/BT ou MT
    pasta_origem = _pasta_mes_faltantes(mes, ano) / tipo.upper()
    log.info(f"  xlsx    : {xlsx}")
    log.info(f"  origem  : {pasta_origem}")

    # Evita limpar o CSV de auditoria do BT se a pasta MT nao existe
    if tipo == "mt" and not pasta_origem.exists():
        log.warning(f"  Pasta MT nao existe — pulando digitacao MT para preservar auditoria BT: {pasta_origem}")
        return True

    cmd = [
        sys.executable, str(script),
        "--xlsx",        str(xlsx),
        "--linha-inicio", "2",
        "--pasta-origem", str(pasta_origem),
    ]
    env_extra = {"CONSEN_PIPELINE_SAIDA": str(PIPELINE_SAIDA)}
    return _rodar(f"Digitacao {tipo.upper()}", cmd, env_extra=env_extra)


def _atualizar_master(auditoria_csv: Path) -> None:
    try:
        sys.path.insert(0, str(RAIZ))
        from indice_master import MasterIndice, marcar_digitados_do_auditoria
        contadores = marcar_digitados_do_auditoria(auditoria_csv, MasterIndice())
        log.info(f"  [MASTER] {contadores}")
    except Exception as e:
        log.warning(f"  [MASTER] Nao foi possivel atualizar: {e}")


def etapa_filtro(mes: str, ano: str, tipo: str = "bt") -> bool:
    tipo = (tipo or "bt").lower()
    rotulo = tipo.upper()

    log.info("=" * 60)
    log.info(f"  ETAPA 3 - FILTRO / MOVIMENTACAO {rotulo}")
    log.info("=" * 60)
    if not SCRIPT_FILTRO.exists():
        log.error(f"  Script nao encontrado: {SCRIPT_FILTRO}")
        return False

    auditoria_csv = PIPELINE_SAIDA / "auditoria_resultados.csv"
    pasta_origem = _pasta_mes_faltantes(mes, ano) / rotulo
    cmd = [
        sys.executable,
        str(SCRIPT_FILTRO),
        "--mes", mes,
        "--ano", ano,
        "--csv", str(auditoria_csv),
        "--origem", str(pasta_origem),
        "--rotulo-origem", rotulo,
    ]
    ok = _rodar(f"Filtro {rotulo}", cmd)
    if not ok:
        log.warning(f"  Filtro {rotulo} retornou falhas - verifique falhas_mover_cemig.csv")
    return ok


# =============================================================================
# CLI + MAIN
# =============================================================================

def parse_args():
    hoje = dt.date.today()
    p = argparse.ArgumentParser(
        description="Pipeline CEMIG 190 — OCR -> Digitacao -> Filtro (pasta Faltantes)"
    )
    p.add_argument("--mes", default=f"{hoje.month:02d}")
    p.add_argument("--ano", default=str(hoje.year))
    p.add_argument("--so-ocr",       action="store_true")
    p.add_argument("--so-digitacao", action="store_true")
    p.add_argument("--so-filtro",    action="store_true")
    p.add_argument("--so-mt",        action="store_true", help="Roda so OCR+Digitacao MT (sem BT)")
    p.add_argument("--so-bt",        action="store_true", help="Roda pipeline BT completo (sem MT)")
    return p.parse_args()


def main():
    args    = parse_args()
    mes     = args.mes
    ano     = args.ano
    xlsx_bt = _xlsx_bt(mes, ano)
    xlsx_mt = _xlsx_mt(mes, ano)
    fazer_bt = not args.so_mt
    fazer_mt = not args.so_bt
    modo_debug = args.so_ocr or args.so_digitacao or args.so_filtro or args.so_mt or args.so_bt

    _mkdir_seguro(PIPELINE_SAIDA)

    _banner("PIPELINE CEMIG 190  —  Faltantes", [
        f"Referência : {mes}/{ano}",
        f"Origem     : {FALTANTES_DIR / f'{mes}.{ano}'}",
        f"xlsx BT    : {xlsx_bt}",
        f"xlsx MT    : {xlsx_mt}",
        f"Saída dig. : {PIPELINE_SAIDA}",
    ])

    falhas = []

    # Etapa 1a: OCR BT
    if fazer_bt and not args.so_digitacao and not args.so_filtro:
        if not etapa_ocr(mes, ano, "bt"):
            falhas.append("OCR BT")
            if not modo_debug:
                log.error("  OCR BT falhou - abortando.")
                sys.exit(1)
    elif not fazer_bt:
        log.info("  [skip] OCR BT.")

    # Etapa 1b: OCR MT
    if fazer_mt and not args.so_digitacao and not args.so_filtro:
        if not etapa_ocr(mes, ano, "mt"):
            falhas.append("OCR MT")
            log.warning("  OCR MT falhou - digitacao MT sera pulada.")
            fazer_mt = False
    elif not fazer_mt:
        log.info("  [skip] OCR MT.")

    # Etapa 2a: Digitacao BT
    if fazer_bt and not args.so_ocr and not args.so_filtro:
        if not etapa_digitacao(xlsx_bt, mes, ano, "bt"):
            falhas.append("Digitacao BT")
            if not modo_debug:
                log.error("  Digitacao BT falhou - abortando.")
                sys.exit(1)
    elif not fazer_bt:
        log.info("  [skip] Digitacao BT.")

    # Etapa 2b: Digitacao MT
    if fazer_mt and not args.so_ocr and not args.so_filtro:
        if not etapa_digitacao(xlsx_mt, mes, ano, "mt"):
            falhas.append("Digitacao MT")
            log.warning("  Digitacao MT falhou - pipeline continua (filtro BT sera executado).")
    elif not fazer_mt:
        log.info("  [skip] Digitacao MT.")

    # Etapa 3a: Filtro BT
    filtro_bt_ok = True
    if fazer_bt and not args.so_ocr and not args.so_digitacao:
        filtro_bt_ok = etapa_filtro(mes, ano, "bt")
    elif args.so_ocr or args.so_digitacao:
        log.info("  [debug] Pulando filtro BT.")
    else:
        log.info("  [skip] Filtro BT.")

    # Etapa 3b: Filtro MT
    filtro_mt_ok = True
    if fazer_mt and not args.so_ocr and not args.so_digitacao:
        filtro_mt_ok = etapa_filtro(mes, ano, "mt")
    elif args.so_ocr or args.so_digitacao:
        log.info("  [debug] Pulando filtro MT.")
    else:
        log.info("  [skip] Filtro MT.")

    if (fazer_bt and filtro_bt_ok) or (fazer_mt and filtro_mt_ok):
        _atualizar_master(PIPELINE_SAIDA / "auditoria_resultados.csv")

    _p()
    _sep("═")
    if falhas:
        _fail(f"PIPELINE CEMIG 190 COM FALHAS: {', '.join(falhas)}")
    else:
        _ok("PIPELINE CEMIG 190 CONCLUÍDO COM SUCESSO")
    _sep("═")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
