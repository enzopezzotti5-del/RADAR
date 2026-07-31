#!/usr/bin/env python3

# -*- coding: utf-8 -*-

"""

pipeline_celesc_bt.py

---------------------

Pipeline CELESC BT (B3, convencional): OCR ? Digitação ? Filtro.



Uso:

    python pipeline_celesc_bt.py

    python pipeline_celesc_bt.py --mes 04 --ano 2026

    python pipeline_celesc_bt.py --so-ocr

    python pipeline_celesc_bt.py --so-digitacao

    python pipeline_celesc_bt.py --so-filtro

    python pipeline_celesc_bt.py --pasta "\\\\servidor\\DOWNLOAD CELESC\\04.2026\\BT"

    python pipeline_celesc_bt.py --carimbo BB_2003677

"""



from __future__ import annotations



import argparse

import datetime as dt

import hashlib

import logging

import os

import re

import subprocess

import sys

import threading

from pathlib import Path





LOCAL_DIR = Path(__file__).resolve().parent.parent



OCR_SCRIPT       = LOCAL_DIR / "ocr" / "ocr_celesc_bt.py"

DIGITACAO_SCRIPT = LOCAL_DIR / "digitacao_consen" / "digitacao_consen_enel.py"

FILTRO_SCRIPT    = LOCAL_DIR / "digitacao_consen" / "celesc_filtro.py"



SERVIDOR      = Path("//10.10.250.21/Energia")

DOWNLOAD_DIR  = SERVIDOR / "ARQUIVOS ENZO" / "DOWNLOAD CELESC"

OCR_SAIDA_DIR = SERVIDOR / "ARQUIVOS ENZO" / "OCR CELESC"

PIPELINE_SAIDA = SERVIDOR / "ARQUIVOS ENZO" / "CELESC_BT_pipeline_saida"

DIGITADAS_DIR  = SERVIDOR / "CONTASDEENERGIAELETRICA" / "BB" / "ENZO" / "Digitadas"



CONSEN_LOGIN_URL   = "https://consen.acaoengenharia.com.br/login.php"

CONSEN_TARGET_HASH = "#bpg/gestao/fatura/cadastroTabFatura.php"

CONSEN_TARGET_URL  = f"{CONSEN_LOGIN_URL.rsplit('/', 1)[0]}/index.php{CONSEN_TARGET_HASH}"

CONSEN_LINK_HREF   = "bpg/gestao/fatura/cadastroTabFatura.php"

CONSEN_LINK_TEXTO  = "Instalacao"

CONSEN_USUARIO     = "Robo Digitador"

CONSEN_SENHA       = "Acao2026"



PYTHON_EXE = str(Path(sys.executable).parent / "python.exe")





logging.basicConfig(

    level=logging.INFO,

    format="%(asctime)s  %(levelname)-8s  %(message)s",

    datefmt="%Y-%m-%d %H:%M:%S",

    handlers=[logging.StreamHandler(sys.stdout)],

)

log = logging.getLogger("pipeline_celesc_bt")





def _mkdir_seguro(pasta: Path) -> None:

    try:

        pasta.mkdir(parents=True, exist_ok=True)

    except OSError:

        pass





def _resetar_auditoria(pasta_saida: Path) -> None:

    csv = pasta_saida / "auditoria_resultados.csv"

    if csv.exists():

        csv.unlink()

        log.info("[reset] auditoria_resultados.csv removido antes da digitacao.")





def _pasta_bt(mes: str, ano: str) -> Path:

    return DOWNLOAD_DIR / f"{mes}.{ano}" / "BT"





def _pasta_mes(mes: str, ano: str) -> Path:

    return DOWNLOAD_DIR / f"{mes}.{ano}"





def _xlsx_bt(mes: str, ano: str) -> Path:

    return OCR_SAIDA_DIR / f"ocr_celesc_BT_{mes}{ano}.xlsx"





def _path_key(path: Path) -> str:

    return os.path.normcase(os.path.normpath(str(path)))





def _slug_resgate(pasta: Path, carimbos: list[str]) -> str:

    partes: list[str] = []

    if pasta.name.strip():

        partes.append(pasta.name.strip())

    cs = [c.strip() for c in carimbos if c.strip()]
    if len(cs) <= 5:
        partes.extend(cs)
    elif cs:
        partes += [cs[0], f"mais{len(cs) - 2}", cs[-1]]

    base = "_".join(partes) or "resgate"

    base = re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_") or "resgate"

    assinatura = hashlib.md5(f"{_path_key(pasta)}|{'|'.join(sorted(carimbos))}".encode("utf-8")).hexdigest()[:8]

    return f"{base}_{assinatura}"





def _is_resgate(pasta: Path, mes: str, ano: str, carimbos: list[str]) -> bool:

    if any(c.strip() for c in carimbos):

        return True

    return _path_key(pasta) != _path_key(_pasta_bt(mes, ano))





def _xlsx_resgate(slug: str) -> Path:

    return OCR_SAIDA_DIR / "_resgates" / f"ocr_celesc_BT_{slug}.xlsx"





def _saida_resgate(slug: str) -> Path:

    return PIPELINE_SAIDA / "_resgates" / slug





def _rodar(descricao: str, cmd: list[str], env_extra: dict | None = None) -> int:

    log.info("=" * 60)

    log.info(f"  {descricao}")

    log.info("=" * 60)

    log.info(f"  Comando: {' '.join(str(c) for c in cmd)}")



    env = os.environ.copy()

    env["PYTHONUTF8"] = "1"

    env["PYTHONIOENCODING"] = "utf-8"

    env["PYTHONUNBUFFERED"] = "1"

    if env_extra:

        env.update({k: str(v) for k, v in env_extra.items()})



    proc = subprocess.Popen(

        cmd,

        stdout=subprocess.PIPE,

        stderr=subprocess.PIPE,

        text=True,

        encoding="utf-8",

        errors="replace",

        bufsize=1,

        env=env,

        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,

    )



    def _drenar(stream, prefixo):

        for linha in iter(stream.readline, ""):

            linha = linha.rstrip()

            if linha:

                log.info(f"  [{prefixo}] {linha}")



    t_out = threading.Thread(target=_drenar, args=(proc.stdout, "OUT"), daemon=True)

    t_err = threading.Thread(target=_drenar, args=(proc.stderr, "ERR"), daemon=True)

    t_out.start()

    t_err.start()



    interrupted = False

    try:

        while True:

            try:

                proc.wait(timeout=0.5)

                break

            except subprocess.TimeoutExpired:

                continue

            except KeyboardInterrupt:

                if proc.poll() is not None:

                    break

                interrupted = True

                log.error("Interrompido manualmente. Encerrando subprocesso...")

                proc.terminate()

                try:

                    proc.wait(timeout=10)

                except subprocess.TimeoutExpired:

                    proc.kill()

                    proc.wait()

                break

    finally:

        t_out.join(timeout=5)

        t_err.join(timeout=5)



    if interrupted:

        return 130



    code = int(proc.returncode or 0)

    log.info(f"{'OK' if code == 0 else 'FALHA'}  {descricao} -> exit {code}")

    return code





def etapa_ocr(

    mes: str,

    ano: str,

    xlsx_saida: Path,

    pasta: str = "",

    carimbos: list[str] | None = None,

    recursivo: bool = False,

    somente_bt: bool = False,

) -> int:

    if not OCR_SCRIPT.exists():

        log.error(f"Script OCR não encontrado: {OCR_SCRIPT}")

        return 1

    _mkdir_seguro(xlsx_saida.parent)

    cmd = [PYTHON_EXE, str(OCR_SCRIPT), "--mes", mes, "--ano", ano, "--saida", str(xlsx_saida)]

    if pasta:

        cmd.extend(["--pasta", pasta])

    if recursivo:

        cmd.append("--recursivo")

    if somente_bt:

        cmd.append("--somente-bt")

    for c in (carimbos or []):

        if c.strip():

            cmd.extend(["--carimbo", c.strip()])

    return _rodar(f"OCR CELESC BT {mes}/{ano}", cmd)





def etapa_digitacao(xlsx: Path, pasta_saida: Path) -> int:

    if not DIGITACAO_SCRIPT.exists():

        log.error(f"Script de digitação não encontrado: {DIGITACAO_SCRIPT}")

        return 1

    if not xlsx.exists():

        log.error(f"Planilha OCR não encontrada: {xlsx}")

        return 1

    _mkdir_seguro(pasta_saida)

    env_extra = {

        "ENEL_EXCEL_PATH": str(xlsx),

        "CONSEN_PIPELINE_SAIDA": str(pasta_saida),

        "CONSEN_INTERATIVO_FECHAR": "0",

        "DIGITACAO_FATOR_VELOCIDADE":    "0.25",

        "CONSEN_PERMITIR_LOTE_COMPLETO": "1",

        "CONSEN_LOGIN_URL": CONSEN_LOGIN_URL,

        "CONSEN_TARGET_HASH": CONSEN_TARGET_HASH,

        "CONSEN_TARGET_URL": CONSEN_TARGET_URL,

        "CONSEN_LINK_HREF": CONSEN_LINK_HREF,

        "CONSEN_LINK_TEXTO": CONSEN_LINK_TEXTO,

        "CONSEN_USUARIO": CONSEN_USUARIO,

        "CONSEN_SENHA": CONSEN_SENHA,

    }

    return _rodar(f"DIGITAÇÃO CELESC BT ({xlsx.name})", [PYTHON_EXE, str(DIGITACAO_SCRIPT)], env_extra=env_extra)





def etapa_filtro(root_pdfs: Path, pasta_saida: Path) -> int:

    if not FILTRO_SCRIPT.exists():

        log.error(f"Script de filtro não encontrado: {FILTRO_SCRIPT}")

        return 1

    auditoria = pasta_saida / "auditoria_resultados.csv"

    if not auditoria.exists():

        log.warning("auditoria_resultados.csv não encontrado  filtro pulado.")

        return 0

    env_extra = {

        "CELESC_FILTRO_CSV": str(auditoria),

        "CELESC_FILTRO_ROOT": str(root_pdfs),

        "CELESC_FILTRO_DESTINO": str(DIGITADAS_DIR),

    }

    return _rodar("FILTRO CELESC BT", [PYTHON_EXE, str(FILTRO_SCRIPT)], env_extra=env_extra)





def _atualizar_master(pasta_saida: Path) -> None:

    auditoria = pasta_saida / "auditoria_resultados.csv"

    if not auditoria.exists():

        return

    try:

        sys.path.insert(0, str(LOCAL_DIR))

        from indice_master import MasterIndice, marcar_digitados_do_auditoria

        contadores = marcar_digitados_do_auditoria(auditoria, MasterIndice())

        log.info(f"  [MASTER] Atualizado: {contadores}")

    except Exception as exc:

        log.warning(f"  [MASTER] Falha ao atualizar índice master: {exc}")





def parse_args():

    hoje = dt.date.today()

    p = argparse.ArgumentParser(description="Pipeline CELESC BT: OCR ? Digitação ? Filtro")

    p.add_argument("--mes", type=str, default=f"{hoje.month:02d}")

    p.add_argument("--ano", type=str, default=str(hoje.year))

    p.add_argument("--so-ocr",       action="store_true", help="Executa apenas o OCR")

    p.add_argument("--so-digitacao", action="store_true", help="Executa apenas a digitação")

    p.add_argument("--so-filtro",    action="store_true", help="Executa apenas o filtro/mover")

    p.add_argument("--pasta", type=str, default="",

                   help="Pasta de PDFs (override do padrão MM.YYYY/BT)")

    p.add_argument("--meses", type=str, default="")

    p.add_argument("--varrer-mes-inteiro", action="store_true")

    p.add_argument("--carimbo", action="append", default=[],

                   help="Restringe OCR a este carimbo. Ex: --carimbo BB_2003677")

    return p.parse_args()





def main() -> int:

    args = parse_args()

    mes = f"{int(args.mes):02d}"

    ano = str(int(args.ano))

    carimbos = [c.strip() for c in args.carimbo if c.strip()]

    pasta_pdfs = Path(args.pasta.strip()) if args.pasta.strip() else _pasta_bt(mes, ano)

    resgate = _is_resgate(pasta_pdfs, mes, ano, carimbos)

    slug = _slug_resgate(pasta_pdfs, carimbos) if resgate else ""

    xlsx = _xlsx_resgate(slug) if resgate else _xlsx_bt(mes, ano)

    pasta_saida = _saida_resgate(slug) if resgate else PIPELINE_SAIDA

    modo_debug = args.so_ocr or args.so_digitacao or args.so_filtro



    _mkdir_seguro(OCR_SAIDA_DIR)

    _mkdir_seguro(PIPELINE_SAIDA)



    log.info("=" * 60)

    log.info("  PIPELINE CELESC BT".center(60))

    log.info("=" * 60)

    log.info(f"  Referência : {mes}/{ano}")

    log.info(f"  Pasta PDFs : {pasta_pdfs}")

    log.info(f"  Resgate    : {'sim' if resgate else 'nao'}")

    log.info(f"  XLSX BT    : {xlsx}")

    log.info(f"  Saida pipe : {pasta_saida}")

    if carimbos:

        log.info(f"  Carimbos   : {', '.join(carimbos)}")



    falhas: list[str] = []



    # -- 1. OCR ---------------------------------------------------------------

    if not args.so_digitacao and not args.so_filtro:

        cod = etapa_ocr(mes, ano, xlsx, pasta=str(pasta_pdfs), carimbos=carimbos)

        if cod == 130:

            return 130

        if cod not in (0, 3):

            falhas.append("OCR")

            if not modo_debug:

                log.error("OCR falhou  abortando pipeline.")

                return 1

    else:

        log.info("[debug] Pulando OCR.")



    # -- 2. Digitação ----------------------------------------------------------

    if not args.so_ocr and not args.so_filtro:

        if not xlsx.exists():

            log.error(f"Planilha OCR não encontrada: {xlsx}")

            falhas.append("DIGITACAO_SEM_PLANILHA")

            if not modo_debug:

                return 1

        else:

            _resetar_auditoria(pasta_saida)

            cod = etapa_digitacao(xlsx, pasta_saida)

            if cod != 0:

                falhas.append("DIGITACAO")

                if not modo_debug:

                    return 1

    else:

        log.info("[debug] Pulando digitação.")



    # -- 3. Filtro + Master ----------------------------------------------------

    if not args.so_ocr and not args.so_digitacao:

        cod = etapa_filtro(pasta_pdfs, pasta_saida)

        if cod != 0:

            falhas.append("FILTRO")

        _atualizar_master(pasta_saida)

    else:

        log.info("[debug] Pulando filtro.")



    log.info("=" * 60)

    if falhas:

        log.error(f"  PIPELINE COM FALHAS: {', '.join(falhas)}")

        return 1

    log.info("  PIPELINE CELESC BT CONCLUIDO COM SUCESSO")

    log.info("=" * 60)

    return 0



def _parse_meses(args) -> list[str]:

    if args.meses.strip():

        meses = []

        for item in args.meses.split(","):

            item = item.strip()

            if not item:

                continue

            meses.append(f"{int(item):02d}")

        return meses

    return [f"{int(args.mes):02d}"]





def _executar_referencia(args, mes: str, ano: str, lote_multimeses: bool) -> int:

    carimbos = [c.strip() for c in args.carimbo if c.strip()]

    varrer_mes_inteiro = bool(args.varrer_mes_inteiro and not args.pasta.strip())

    pasta_pdfs = Path(args.pasta.strip()) if args.pasta.strip() else (_pasta_mes(mes, ano) if varrer_mes_inteiro else _pasta_bt(mes, ano))

    root_filtro = _pasta_mes(mes, ano) if varrer_mes_inteiro else pasta_pdfs

    resgate = _is_resgate(pasta_pdfs, mes, ano, carimbos) if not varrer_mes_inteiro else False

    slug = _slug_resgate(pasta_pdfs, carimbos) if resgate else ""

    xlsx = _xlsx_resgate(slug) if resgate else _xlsx_bt(mes, ano)

    pasta_saida = _saida_resgate(slug) if resgate else (PIPELINE_SAIDA / f"{mes}.{ano}" if lote_multimeses or varrer_mes_inteiro else PIPELINE_SAIDA)

    modo_debug = args.so_ocr or args.so_digitacao or args.so_filtro



    _mkdir_seguro(OCR_SAIDA_DIR)

    _mkdir_seguro(PIPELINE_SAIDA)

    _mkdir_seguro(pasta_saida)



    log.info("=" * 60)

    log.info("  PIPELINE CELESC BT".center(60))

    log.info("=" * 60)

    log.info(f"  ReferÃªncia : {mes}/{ano}")

    log.info(f"  Pasta PDFs : {pasta_pdfs}")

    log.info(f"  Root filtro: {root_filtro}")

    log.info(f"  Varrer mes : {'sim' if varrer_mes_inteiro else 'nao'}")

    log.info(f"  Resgate    : {'sim' if resgate else 'nao'}")

    log.info(f"  XLSX BT    : {xlsx}")

    log.info(f"  Saida pipe : {pasta_saida}")

    if carimbos:

        log.info(f"  Carimbos   : {', '.join(carimbos)}")



    falhas: list[str] = []



    if not args.so_digitacao and not args.so_filtro:

        cod = etapa_ocr(

            mes,

            ano,

            xlsx,

            pasta=str(pasta_pdfs),

            carimbos=carimbos,

            recursivo=varrer_mes_inteiro,

            somente_bt=varrer_mes_inteiro,

        )

        if cod == 130:

            return 130

        if cod not in (0, 3):

            falhas.append("OCR")

            if not modo_debug:

                log.error("OCR falhou â abortando pipeline.")

                return 1

    else:

        log.info("[debug] Pulando OCR.")



    if not args.so_ocr and not args.so_filtro:

        if not xlsx.exists():

            log.error(f"Planilha OCR nÃ£o encontrada: {xlsx}")

            falhas.append("DIGITACAO_SEM_PLANILHA")

            if not modo_debug:

                return 1

        else:

            _resetar_auditoria(pasta_saida)

            cod = etapa_digitacao(xlsx, pasta_saida)

            if cod != 0:

                falhas.append("DIGITACAO")

                if not modo_debug:

                    return 1

    else:

        log.info("[debug] Pulando digitaÃ§Ã£o.")



    if not args.so_ocr and not args.so_digitacao:

        cod = etapa_filtro(root_filtro, pasta_saida)

        if cod != 0:

            falhas.append("FILTRO")

        _atualizar_master(pasta_saida)

    else:

        log.info("[debug] Pulando filtro.")



    log.info("=" * 60)

    if falhas:

        log.error(f"  PIPELINE COM FALHAS: {', '.join(falhas)}")

        return 1

    log.info("  PIPELINE CELESC BT CONCLUIDO COM SUCESSO")

    log.info("=" * 60)

    return 0





def main() -> int:

    args = parse_args()

    ano = str(int(args.ano))

    meses = _parse_meses(args)



    if args.pasta.strip() and len(meses) > 1:

        log.error("--pasta nÃ£o pode ser combinado com --meses em lote.")

        return 1



    falhas: list[str] = []

    for mes in meses:

        cod = _executar_referencia(args, mes, ano, lote_multimeses=len(meses) > 1)

        if cod == 130:

            return 130

        if cod != 0:

            falhas.append(f"{mes}/{ano}")



    if falhas:

        log.error("Lote finalizado com falhas em: %s", ", ".join(falhas))

        return 1

    return 0





if __name__ == "__main__":

    raise SystemExit(main())

