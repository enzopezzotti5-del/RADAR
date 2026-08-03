#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pipeline_enel_erros.py
----------------------
Pipeline para reprocessar PDFs que estavam em ERROS DIGITADOS.

Fluxo:
  1. OCR     processa DOWNLOAD ENEL/Faltantes/erros-2026/BT/
              ? gera ocr_enel_BT_erros-2026.xlsx
  2. Digitação  digita o xlsx no Consen (pula os que já existem)
  3. Filtro  move os PDFs digitados para Digitadas

Uso:
    python pipeline_enel_erros.py
    python pipeline_enel_erros.py --so-ocr
    python pipeline_enel_erros.py --so-digitacao
    python pipeline_enel_erros.py --so-filtro
    python pipeline_enel_erros.py --recriar   # recria o xlsx do zero
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

LOCAL_DIR        = Path(__file__).parent.parent

OCR_SCRIPT       = LOCAL_DIR / "ocr"              / "ocr_enel.py"
DIGITACAO_SCRIPT = LOCAL_DIR / "digitacao_consen" / "digitacao_consen_enel.py"
FILTRO_SCRIPT    = LOCAL_DIR / "digitacao_consen" / "enel_filtro.py"

SERVIDOR         = Path("//10.10.250.21/Energia")
FALTANTES_DIR    = SERVIDOR / "ARQUIVOS ENZO" / "DOWNLOAD ENEL" / "Faltantes"
PASTA_ERROS      = FALTANTES_DIR / "erros-2026"          # OCR lê daqui
PASTA_BT         = PASTA_ERROS / "BT"                    # PDFs ficam aqui
OCR_SAIDA_DIR    = SERVIDOR / "ARQUIVOS ENZO" / "OCR ENEL"
XLSX_ERROS       = OCR_SAIDA_DIR / "ocr_enel_BT_erros-2026.xlsx"
PIPELINE_SAIDA   = SERVIDOR / "ARQUIVOS ENZO" / "ENEL_pipeline_saida"
DIGITADAS_DIR    = SERVIDOR / "CONTASDEENERGIAELETRICA" / "BB" / "ENZO" / "Digitadas"

PYTHON_EXE = str(Path(sys.executable).parent / "python.exe")

# =============================================================================
# LOG
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


# =============================================================================
# HELPERS
# =============================================================================

def _rodar(descricao: str, cmd: list[str], env_extra: dict | None = None) -> int:
    log.info("=" * 60)
    log.info(f"  {descricao}")
    log.info("=" * 60)
    log.info(f"  Comando: {' '.join(cmd)}")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    if env_extra:
        env.update(env_extra)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
    )

    for linha in iter(proc.stdout.readline, ""):
        linha = linha.rstrip()
        if linha:
            log.info(f"  [{descricao[:8]}] {linha}")

    proc.wait()
    codigo = proc.returncode
    log.info(f"{'OK' if codigo == 0 else 'FALHA'}  {descricao}  exit {codigo}")
    return codigo


# =============================================================================
# ETAPAS
# =============================================================================

def etapa_ocr(recriar: bool = False) -> int:
    """OCR da pasta erros-2026/BT ? ocr_enel_BT_erros-2026.xlsx"""
    pdfs = list(PASTA_BT.glob("*.pdf")) if PASTA_BT.exists() else []
    if not pdfs:
        log.error(f"Nenhum PDF em {PASTA_BT}")
        return 1

    log.info(f"  PDFs encontrados em erros-2026/BT: {len(pdfs)}")

    cmd = [
        PYTHON_EXE, str(OCR_SCRIPT),
        "--pasta", "erros-2026",
        "--tipo", "bt",
    ]
    if recriar:
        cmd.append("--recriar")

    return _rodar(
        "OCR ERROS",
        cmd,
        env_extra={
            "OCR_ENEL_DOWNLOAD_DIR": str(FALTANTES_DIR),
            "OCR_ENEL_SAIDA_DIR":    str(OCR_SAIDA_DIR),
        },
    )


def etapa_digitacao() -> int:
    """Digitação do xlsx de erros no Consen."""
    if not XLSX_ERROS.exists():
        log.error(f"  Planilha não encontrada: {XLSX_ERROS}")
        return 1

    log.info("=" * 60)
    log.info(f"  Digitação ENEL Erros  {XLSX_ERROS.name}")
    log.info("=" * 60)

    env = os.environ.copy()
    env["ENEL_EXCEL_PATH"]        = str(XLSX_ERROS)
    env["CONSEN_PIPELINE_SAIDA"]  = str(PIPELINE_SAIDA)
    env["CONSEN_INTERATIVO_FECHAR"] = "0"
    env["DIGITACAO_FATOR_VELOCIDADE"] = "0.25"
    env["CONSEN_PERMITIR_LOTE_COMPLETO"] = "1"    env["CONSEN_SENHA"]              = "Acao2026"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        [PYTHON_EXE, str(DIGITACAO_SCRIPT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )

    def _drenar(stream, prefixo):
        for linha in iter(stream.readline, ""):
            linha = linha.rstrip()
            if linha:
                log.info(f"  [{prefixo}] {linha}")

    t_out = threading.Thread(target=_drenar, args=(proc.stdout, "DIG"), daemon=True)
    t_err = threading.Thread(target=_drenar, args=(proc.stderr, "DIG-ERR"), daemon=True)
    t_out.start(); t_err.start()
    t_out.join();  t_err.join()

    proc.wait()
    codigo = proc.returncode
    log.info(f"{'OK' if codigo == 0 else 'FALHA'}  Digitação  exit {codigo}")
    return codigo


def _atualizar_master(auditoria_csv: Path) -> None:
    try:
        sys.path.insert(0, str(LOCAL_DIR))
        from indice_master import MasterIndice, marcar_digitados_do_auditoria
        contadores = marcar_digitados_do_auditoria(auditoria_csv, MasterIndice())
        log.info(f"  [MASTER] {contadores}")
    except Exception as exc:
        log.warning(f"  [MASTER] Não foi possível atualizar: {exc}")


def etapa_filtro() -> int:
    """Filtro: move os PDFs digitados de erros-2026/BT para Digitadas."""
    auditoria_csv = PIPELINE_SAIDA / "auditoria_resultados.csv"
    if not auditoria_csv.exists():
        log.warning("  auditoria_resultados.csv não encontrado  filtro pulado")
        return 0

    log.info("=" * 60)
    log.info(f"  Filtro ERROS  {PASTA_BT.name} ? Digitadas")
    log.info("=" * 60)

    env = os.environ.copy()
    env["ENEL_FILTRO_CSV"]     = str(auditoria_csv)
    env["ENEL_FILTRO_PDFS"]    = str(PASTA_BT)
    env["ENEL_FILTRO_DESTINO"] = str(DIGITADAS_DIR)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        [PYTHON_EXE, str(FILTRO_SCRIPT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
    )

    for linha in iter(proc.stdout.readline, ""):
        linha = linha.rstrip()
        if linha:
            log.info(f"  [FILTRO] {linha}")

    proc.wait()
    codigo = proc.returncode
    log.info(f"{'OK' if codigo == 0 else 'FALHA'}  Filtro  exit {codigo}")
    if codigo == 0:
        _atualizar_master(auditoria_csv)
    return codigo


# =============================================================================
# MAIN
# =============================================================================

def parse_args():
    p = argparse.ArgumentParser(description="Pipeline ENEL Erros: OCR ? Digitação ? Filtro")
    p.add_argument("--so-ocr",       action="store_true")
    p.add_argument("--so-digitacao", action="store_true")
    p.add_argument("--so-filtro",    action="store_true")
    p.add_argument("--recriar",      action="store_true",
                   help="Recria o xlsx do zero (ignora dedup)")
    return p.parse_args()


def main():
    args = parse_args()

    tudo         = not (args.so_ocr or args.so_digitacao or args.so_filtro)
    fazer_ocr    = tudo or args.so_ocr
    fazer_dig    = tudo or args.so_digitacao
    fazer_filtro = tudo or args.so_filtro

    PIPELINE_SAIDA.mkdir(parents=True, exist_ok=True)
    DIGITADAS_DIR.mkdir(parents=True, exist_ok=True)

    falhou = False

    if fazer_ocr:
        cod = etapa_ocr(recriar=args.recriar)
        if cod != 0:
            log.error("OCR falhou  abortando")
            sys.exit(1)

    if fazer_dig:
        cod = etapa_digitacao()
        if cod != 0:
            log.warning(f"Digitação terminou com exit {cod}  continuando para filtro")

    if fazer_filtro:
        cod = etapa_filtro()
        if cod != 0:
            log.error(f"Filtro falhou (exit {cod})")
            falhou = True

    log.info("")
    log.info("=" * 60)
    log.info("  Pipeline ENEL Erros finalizado " + ("COM FALHAS" if falhou else "com SUCESSO"))
    log.info("=" * 60)
    sys.exit(1 if falhou else 0)


if __name__ == "__main__":
    main()
