#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path


LOCAL_DIR = Path(__file__).parent.parent

OCR_SCRIPT = LOCAL_DIR / "ocr" / "ocr_enel.py"
DIGITACAO_SCRIPT = LOCAL_DIR / "digitacao_consen" / "digitacao_consen_enel.py"
FILTRO_SCRIPT = LOCAL_DIR / "digitacao_consen" / "enel_filtro.py"

SERVIDOR = Path("//10.10.250.21/Energia")
DOWNLOAD_DIR = SERVIDOR / "ARQUIVOS ENZO" / "DOWNLOAD ENEL" / "Faltantes"
OCR_SAIDA_DIR = SERVIDOR / "ARQUIVOS ENZO" / "OCR ENEL"
PIPELINE_SAIDA = SERVIDOR / "ARQUIVOS ENZO" / "ENEL_pipeline_saida"
DIGITADAS_DIR = SERVIDOR / "CONTASDEENERGIAELETRICA" / "BB" / "ENZO" / "Digitadas"

PYTHON_EXE = str(Path(sys.executable).parent / "python.exe")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


def _rodar(descricao: str, cmd: list[str], env_extra: dict[str, str] | None = None) -> int:
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
            log.info(f"  [{descricao[:6]}] {linha}")

    proc.wait()
    codigo = proc.returncode
    simbolo = "OK" if codigo == 0 else "FALHA"
    log.info(f"{simbolo}  {descricao} - exit {codigo}")
    return codigo


def _xlsx_bt(mes: str, ano: str) -> Path:
    return OCR_SAIDA_DIR / f"ocr_enel_BT_{mes}{ano}.xlsx"


def _xlsx_mt(mes: str, ano: str) -> Path:
    return OCR_SAIDA_DIR / f"ocr_enel_MT_{mes}{ano}.xlsx"


def _pasta_download_bt(mes: str, ano: str) -> Path:
    return DOWNLOAD_DIR / f"{mes}-{ano}" / "BT"


def _pasta_download_mt(mes: str, ano: str) -> Path:
    return DOWNLOAD_DIR / f"{mes}-{ano}" / "MT"


def etapa_ocr(mes: str, ano: str, tipo: str, recriar: bool = False) -> int:
    cmd = [PYTHON_EXE, str(OCR_SCRIPT), "--pasta", f"{mes}-{ano}", "--tipo", tipo]
    if recriar:
        cmd.append("--recriar")
    return _rodar(
        f"OCR ENEL FALTANTES {tipo.upper()} {mes}/{ano}",
        cmd,
        env_extra={"OCR_ENEL_DOWNLOAD_DIR": str(DOWNLOAD_DIR)},
    )


def etapa_digitacao(xlsx: Path) -> int:
    if not xlsx.exists():
        log.error(f"  Planilha não encontrada: {xlsx}")
        return 1

    env = os.environ.copy()
    env["ENEL_EXCEL_PATH"] = str(xlsx)
    env["CONSEN_PIPELINE_SAIDA"] = str(PIPELINE_SAIDA)
    env["CONSEN_INTERATIVO_FECHAR"] = "0"
    env["DIGITACAO_FATOR_VELOCIDADE"] = "0.25"
    env["CONSEN_PERMITIR_LOTE_COMPLETO"] = "1"    env["CONSEN_SENHA"]              = "Acao2026"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    log.info("=" * 60)
    log.info(f"  Digitação ENEL Faltantes  {xlsx.name}")
    log.info("=" * 60)

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
    t_out.start()
    t_err.start()
    t_out.join()
    t_err.join()

    proc.wait()
    codigo = proc.returncode
    simbolo = "OK" if codigo == 0 else "FALHA"
    log.info(f"{simbolo}  Digitacao - exit {codigo}")
    return codigo


def _atualizar_master_pos_filtro(auditoria_csv: Path) -> None:
    try:
        import sys as _sys
        _sys.path.insert(0, str(LOCAL_DIR))
        from indice_master import MasterIndice, marcar_digitados_do_auditoria
        contadores = marcar_digitados_do_auditoria(auditoria_csv, MasterIndice())
        log.info(f"  [MASTER] Digitacao atualizada: {contadores}")
    except Exception as exc:
        log.warning(f"  [MASTER] Nao foi possivel atualizar o indice master: {exc}")


def etapa_filtro(pasta_pdfs: Path, pasta_destino: Path) -> int:
    auditoria_csv = PIPELINE_SAIDA / "auditoria_resultados.csv"
    if not auditoria_csv.exists():
        log.warning(f"  auditoria_resultados.csv não encontrado em {PIPELINE_SAIDA}  filtro pulado")
        return 0

    env = os.environ.copy()
    env["ENEL_FILTRO_CSV"] = str(auditoria_csv)
    env["ENEL_FILTRO_PDFS"] = str(pasta_pdfs)
    env["ENEL_FILTRO_DESTINO"] = str(pasta_destino)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    log.info("=" * 60)
    log.info(f"  Filtro ENEL Faltantes - {pasta_pdfs.name} para {pasta_destino.name}")
    log.info("=" * 60)

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
    simbolo = "OK" if codigo == 0 else "FALHA"
    log.info(f"{simbolo}  Filtro -- exit {codigo}")
    if codigo == 0:
        _atualizar_master_pos_filtro(auditoria_csv)
    else:
        log.warning("  [MASTER] Atualizacao do indice pulada porque o filtro falhou.")
    return codigo


def parse_args():
    p = argparse.ArgumentParser(description="Pipeline ENEL Faltantes: OCR -> Digitação -> Filtro")
    p.add_argument("--mes", type=str)
    p.add_argument("--ano", type=str)
    p.add_argument("--tipo", choices=["bt", "mt", "ambos"], default="bt")
    p.add_argument("--so-ocr", action="store_true")
    p.add_argument("--so-digitacao", action="store_true")
    p.add_argument("--so-filtro", action="store_true")
    p.add_argument("--recriar", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()

    hoje = dt.date.today()
    mes = args.mes or f"{hoje.month:02d}"
    ano = args.ano or str(hoje.year)
    tipo = args.tipo

    tudo = not (args.so_ocr or args.so_digitacao or args.so_filtro)
    fazer_ocr = tudo or args.so_ocr
    fazer_dig = tudo or args.so_digitacao
    fazer_filtro = tudo or args.so_filtro

    PIPELINE_SAIDA.mkdir(parents=True, exist_ok=True)
    DIGITADAS_DIR.mkdir(parents=True, exist_ok=True)

    falhou = False

    tipos = []
    if tipo in ("bt", "ambos"):
        tipos.append("bt")
    if tipo in ("mt", "ambos"):
        tipos.append("mt")

    for t in tipos:
        xlsx = _xlsx_bt(mes, ano) if t == "bt" else _xlsx_mt(mes, ano)
        pdfs = _pasta_download_bt(mes, ano) if t == "bt" else _pasta_download_mt(mes, ano)

        if fazer_ocr:
            cod = etapa_ocr(mes, ano, t, recriar=getattr(args, "recriar", False))
            if cod != 0:
                log.error(f"OCR {t.upper()} falhou (exit {cod})  abortando pipeline {t.upper()}")
                falhou = True
                continue

        if fazer_dig:
            cod = etapa_digitacao(xlsx)
            if cod != 0:
                log.warning(f"Digitação {t.upper()} terminou com exit {cod}  continuando para filtro")

        if fazer_filtro:
            cod = etapa_filtro(pdfs, DIGITADAS_DIR)
            if cod != 0:
                log.error(f"Filtro {t.upper()} falhou (exit {cod})")
                falhou = True

    log.info("")
    log.info("=" * 60)
    log.info("  Pipeline ENEL Faltantes finalizado COM FALHAS" if falhou else "  Pipeline ENEL Faltantes finalizado com SUCESSO")
    log.info("=" * 60)
    sys.exit(1 if falhou else 0)


if __name__ == "__main__":
    main()
