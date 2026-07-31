#!/usr/bin/env python3

# -*- coding: utf-8 -*-

"""

pipeline_celesc.py

------------------

Pipeline CELESC BT + MT:

  1. OCR

  2. Digitacao

  3. Filtro



Uso:

    python pipeline_celesc.py

    python pipeline_celesc.py --mes 04 --ano 2026

    python pipeline_celesc.py --so-ocr

    python pipeline_celesc.py --so-digitacao

    python pipeline_celesc.py --so-filtro

    python pipeline_celesc.py --so-bt

    python pipeline_celesc.py --so-mt

    python pipeline_celesc.py --pasta "\\\\servidor\\DOWNLOAD CELESC\\04.2026"

    python pipeline_celesc.py --carimbo BB_2003260

"""



from __future__ import annotations



import argparse

import csv

import datetime as dt

import hashlib

import os

import re

import subprocess

import io
import sys

import threading

from pathlib import Path

if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, "encoding", "").lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")





LOCAL_DIR = Path(__file__).resolve().parent.parent



OCR_SCRIPT_BT    = LOCAL_DIR / "ocr"              / "ocr_celesc_bt.py"
OCR_SCRIPT_MT    = LOCAL_DIR / "ocr"              / "ocr_celesc_mt.py"

DIGITACAO_SCRIPT = LOCAL_DIR / "digitacao_consen" / "digitacao_consen_enel.py"

FILTRO_SCRIPT    = LOCAL_DIR / "digitacao_consen" / "celesc_filtro.py"



SERVIDOR           = Path("//10.10.250.21/Energia")

DOWNLOAD_DIR       = SERVIDOR / "ARQUIVOS ENZO" / "DOWNLOAD CELESC"

OCR_SAIDA_DIR      = SERVIDOR / "ARQUIVOS ENZO" / "OCR CELESC"

PIPELINE_SAIDA_ROOT= SERVIDOR / "ARQUIVOS ENZO" / "CELESC_pipeline_saida"

DIGITADAS_DIR      = Path("//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO/Digitadas")



PIPELINE_NOME  = "CELESC"

TIPOS_VALIDOS  = ("bt", "mt")



CONSEN_LOGIN_URL   = "https://consen.acaoengenharia.com.br/login.php"

CONSEN_TARGET_HASH = "#bpg/gestao/fatura/cadastroTabFatura.php"

CONSEN_TARGET_URL  = f"{CONSEN_LOGIN_URL.rsplit('/', 1)[0]}/index.php{CONSEN_TARGET_HASH}"

CONSEN_LINK_HREF   = "bpg/gestao/fatura/cadastroTabFatura.php"

CONSEN_LINK_TEXTO  = "Instalacao"

CONSEN_USUARIO     = "Robo Digitador"

CONSEN_SENHA       = "Acao2026"



PYTHON_EXE = str(Path(sys.executable).parent / "python.exe")





# =============================================================================

# HELPERS VISUAIS

# =============================================================================



_W = 64  # largura dos separadores





def _ts() -> str:

    return dt.datetime.now().strftime("%H:%M:%S")





def _p(msg: str = "") -> None:

    print(msg, flush=True)





def _info(msg: str) -> None:

    print(f"[{_ts()}]  {msg}", flush=True)





def _ok(msg: str, elapsed: float | None = None) -> None:

    sufixo = f"  ({elapsed:.0f}s)" if elapsed is not None else ""

    print(f"[{_ts()}] ?  {msg}{sufixo}", flush=True)





def _fail(msg: str, code: int | None = None) -> None:

    sufixo = f"  (exit {code})" if code is not None else ""

    print(f"[{_ts()}] ?  {msg}{sufixo}", flush=True)





def _warn(msg: str) -> None:

    print(f"[{_ts()}] ?  {msg}", flush=True)





def _sep(char: str = "-", w: int = _W) -> None:

    print(char * w, flush=True)





def _banner(titulo: str, detalhes: list[str] | None = None) -> None:

    _p()

    _sep("-")

    _p(f"  {titulo}")

    if detalhes:

        _sep("-")

        for linha in detalhes:

            _p(f"  {linha}")

    _sep("-")

    _p()





def _step(nome: str) -> None:

    _p()

    _p(f"?  {nome}")

    _sep("·" * _W if False else "·", _W)





def _step_fim(nome: str, ok: bool, elapsed: float) -> None:

    _sep("·", _W)

    if ok:

        _ok(nome, elapsed)

    else:

        _fail(nome)

    _p()





# =============================================================================

# UTILITARIOS

# =============================================================================



def _mkdir_seguro(pasta: Path) -> None:

    try:

        pasta.mkdir(parents=True, exist_ok=True)

    except OSError:

        pass





def _pipeline_saida(tipo: str) -> Path:

    return PIPELINE_SAIDA_ROOT / tipo.upper()





def _path_key(path: Path) -> str:

    return os.path.normcase(os.path.normpath(str(path)))





def _pasta_mes_padrao(mes: str, ano: str, tipo: str) -> Path:

    return DOWNLOAD_DIR / f"{mes}.{ano}" / tipo.upper()





def _resolver_pasta_tipo(pasta: str, mes: str, ano: str, tipo: str) -> Path:

    tipo_upper = tipo.upper()

    if not pasta.strip():

        return _pasta_mes_padrao(mes, ano, tipo)

    base = Path(pasta.strip())

    if base.name.upper() == tipo_upper:

        return base

    candidato = base / tipo_upper

    if candidato.exists():

        return candidato

    return base





def _slug_resgate(pasta: Path, carimbos: list[str]) -> str:

    partes: list[str] = []

    nome_base = pasta.name.strip() if pasta.name else ""

    if nome_base:

        partes.append(nome_base)

    cs = [c.strip() for c in carimbos if c.strip()]
    if len(cs) <= 5:
        partes.extend(cs)
    elif cs:
        partes += [cs[0], f"mais{len(cs) - 2}", cs[-1]]

    base = "_".join(partes) or "resgate"

    base = re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_") or "resgate"

    assinatura = hashlib.md5(

        f"{_path_key(pasta)}|{'|'.join(sorted(carimbos))}".encode("utf-8")

    ).hexdigest()[:8]

    return f"{base}_{assinatura}"





def _is_resgate(pasta: str, mes: str, ano: str, tipo: str, carimbos: list[str]) -> bool:

    if any(c.strip() for c in carimbos):

        return True

    if not pasta.strip():

        return False

    return _path_key(_resolver_pasta_tipo(pasta, mes, ano, tipo)) != _path_key(_pasta_mes_padrao(mes, ano, tipo))





def _xlsx_ocr(mes: str, ano: str, tipo: str) -> Path:

    return OCR_SAIDA_DIR / f"ocr_celesc_{tipo.upper()}_{mes}{ano}.xlsx"





def _xlsx_ocr_resgate(tipo: str, slug: str) -> Path:

    return OCR_SAIDA_DIR / "_resgates" / f"ocr_celesc_{tipo.upper()}_{slug}.xlsx"





def _pipeline_saida_resgate(tipo: str, slug: str) -> Path:

    return PIPELINE_SAIDA_ROOT / tipo.upper() / "_resgates" / slug





# =============================================================================

# EXECUTOR DE SUBPROCESSO

# =============================================================================



def _rodar(descricao: str, cmd: list[str], env_extra: dict[str, str] | None = None) -> int:

    _step(descricao)

    _info(f"Cmd: {' '.join(str(c) for c in cmd)}")

    _p()



    env = os.environ.copy()

    env["PYTHONUTF8"]       = "1"

    env["PYTHONIOENCODING"] = "utf-8"

    env["PYTHONUNBUFFERED"] = "1"

    if env_extra:

        env.update({k: str(v) for k, v in env_extra.items()})



    t_inicio = dt.datetime.now()



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



    def _drenar(stream, prefixo: str) -> None:

        for linha in iter(stream.readline, ""):

            linha = linha.rstrip()

            if linha:

                tag = f"[{prefixo}] " if prefixo else "  "

                print(f"{tag}{linha}", flush=True)



    t_out = threading.Thread(target=_drenar, args=(proc.stdout, ""), daemon=True)

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

                _warn("Interrompido manualmente. Encerrando subprocesso...")

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

        _step_fim(descricao, ok=False, elapsed=0)

        return 130



    elapsed = (dt.datetime.now() - t_inicio).total_seconds()

    code    = int(proc.returncode or 0)

    _step_fim(descricao, ok=(code == 0), elapsed=elapsed)

    return code





# =============================================================================

# AUDITORIA + MASTER

# =============================================================================



def _ler_resumo_auditoria(pasta_saida: Path) -> dict[str, int]:

    auditoria = pasta_saida / "auditoria_resultados.csv"

    resumo = {"total": 0, "sucesso": 0, "moviveis": 0, "pendentes": 0}

    if not auditoria.exists():

        return resumo



    status_moviveis = {"sucesso_auditoria", "auditoria_sem_valor", "pulado_carimbo_existente"}

    status_ok       = status_moviveis | {"pulado_referencia_existente"}

    for enc, sep in [("utf-8-sig", ";"), ("utf-8", ";"), ("latin1", ";")]:

        try:

            with auditoria.open("r", newline="", encoding=enc) as f:

                for row in csv.DictReader(f, delimiter=sep):

                    resumo["total"]    += 1

                    status = str(row.get("status", "")).strip().lower()

                    resumo["sucesso"]  += status in status_ok

                    resumo["pendentes"]+= status not in status_ok

                    resumo["moviveis"] += status in status_moviveis

            return resumo

        except UnicodeDecodeError:

            continue

        except Exception as exc:

            _warn(f"Falha ao ler auditoria em {pasta_saida}: {exc}")

            return resumo

    return resumo





def _atualizar_master(pasta_saida: Path) -> None:

    auditoria = pasta_saida / "auditoria_resultados.csv"

    if not auditoria.exists():

        return

    try:

        _ROOT_DIR = LOCAL_DIR.parent
        if str(_ROOT_DIR) not in sys.path:
            sys.path.insert(0, str(_ROOT_DIR))

        from indice_master import MasterIndice, marcar_digitados_do_auditoria

        contadores = marcar_digitados_do_auditoria(auditoria, MasterIndice())

        _info(f"[MASTER] {pasta_saida.name} ? {contadores}")

    except Exception as exc:

        _warn(f"[MASTER] Falha ao atualizar índice master ({pasta_saida.name}): {exc}")





# =============================================================================

# ETAPAS

# =============================================================================



def etapa_ocr_tipo(

    mes: str,

    ano: str,

    tipo: str,

    pasta: Path,

    xlsx_saida: Path,

    carimbos: list[str] | None = None,

) -> int:
    ocr_script = OCR_SCRIPT_BT if str(tipo).lower() == "bt" else OCR_SCRIPT_MT
    if not ocr_script.exists():

        _fail(f"Script OCR nao encontrado: {ocr_script}")

        return 1



    _mkdir_seguro(xlsx_saida.parent)

    cmd = [

        PYTHON_EXE, str(ocr_script),

        "--mes", mes, "--ano", ano,

        "--pasta", str(pasta),

        "--saida", str(xlsx_saida),

    ]

    for c in (carimbos or []):

        c_limpo = c.strip()

        if c_limpo:

            cmd.extend(["--carimbo", c_limpo])

    return _rodar(f"OCR {PIPELINE_NOME} {tipo.upper()}  {mes}/{ano}", cmd)





def etapa_digitacao_tipo(tipo: str, xlsx: Path, pasta_saida: Path, pasta_pdfs: Path) -> int:
    if not DIGITACAO_SCRIPT.exists():

        _fail(f"Script de digitacao nao encontrado: {DIGITACAO_SCRIPT}")

        return 1

    if not xlsx.exists():

        _fail(f"Planilha OCR nao encontrada: {xlsx}")

        return 1



    _mkdir_seguro(pasta_saida)

    for nome in ("auditoria_resultados.csv", "resultado_preenchimento_csv.csv"):

        arquivo = pasta_saida / nome

        if arquivo.exists():

            try:

                arquivo.unlink()

                _info(f"Saída anterior removida ({tipo.upper()}): {arquivo.name}")

            except Exception as exc:

                _warn(f"Falha ao remover saída anterior {arquivo}: {exc}")



    for pendente in pasta_saida.glob("pendentes_*.csv"):

        try:

            pendente.unlink()

        except Exception:

            pass



    env_extra = {
        "ENEL_EXCEL_PATH":               str(xlsx),
        "CONSEN_PIPELINE_SAIDA":         str(pasta_saida),
        "ENEL_DIGITACAO_PASTA_PDFS":    str(pasta_pdfs),
        "CONSEN_INTERATIVO_FECHAR":      "0",
        "DIGITACAO_FATOR_VELOCIDADE":    "0.25",
        "CONSEN_PERMITIR_LOTE_COMPLETO": "1",
        "CONSEN_INVESTIGAR_ZEROS":       "0",

        "CONSEN_LOGIN_URL":              CONSEN_LOGIN_URL,

        "CONSEN_TARGET_HASH":            CONSEN_TARGET_HASH,

        "CONSEN_TARGET_URL":             CONSEN_TARGET_URL,

        "CONSEN_LINK_HREF":              CONSEN_LINK_HREF,

        "CONSEN_LINK_TEXTO":             CONSEN_LINK_TEXTO,

        "CONSEN_USUARIO":                CONSEN_USUARIO,

        "CONSEN_SENHA":                  CONSEN_SENHA,

    }

    return _rodar(

        f"DIGITAÇÃO {PIPELINE_NOME} {tipo.upper()}  ({xlsx.name})",

        [PYTHON_EXE, str(DIGITACAO_SCRIPT)],

        env_extra=env_extra,

    )





def etapa_filtro_tipo(tipo: str, root_pdfs: Path, pasta_saida: Path) -> int:

    if not FILTRO_SCRIPT.exists():

        _fail(f"Script de filtro nao encontrado: {FILTRO_SCRIPT}")

        return 1



    auditoria = pasta_saida / "auditoria_resultados.csv"

    if not auditoria.exists():

        _warn(f"auditoria_resultados.csv nao encontrado para {tipo.upper()}  filtro pulado.")

        return 0



    env_extra = {

        "CELESC_FILTRO_CSV":     str(auditoria),

        "CELESC_FILTRO_ROOT":    str(root_pdfs),

        "CELESC_FILTRO_DESTINO": str(DIGITADAS_DIR),

    }

    codigo = _rodar(

        f"FILTRO {PIPELINE_NOME} {tipo.upper()}",

        [PYTHON_EXE, str(FILTRO_SCRIPT)],

        env_extra=env_extra,

    )

    _atualizar_master(pasta_saida)

    return codigo





# =============================================================================

# CLI

# =============================================================================



def parse_args() -> argparse.Namespace:

    hoje = dt.date.today()

    parser = argparse.ArgumentParser(description="Pipeline CELESC BT+MT: OCR -> Digitacao -> Filtro")

    parser.add_argument("--mes",          type=str, default=f"{hoje.month:02d}")

    parser.add_argument("--ano",          type=str, default=str(hoje.year))

    parser.add_argument("--so-ocr",       action="store_true")

    parser.add_argument("--so-digitacao", action="store_true")

    parser.add_argument("--so-filtro",    action="store_true")

    parser.add_argument("--so-bt",        action="store_true")

    parser.add_argument("--so-mt",        action="store_true")

    parser.add_argument("--pasta",        type=str, default="",

                        help="Pasta mensal base da CELESC (ex.: ...\\04.2026) ou pasta ja apontando para BT/MT")

    parser.add_argument("--carimbo", action="append", default=[],

                        help="Restringe o OCR a este(s) carimbo(s). Ex: --carimbo BB_2003260")

    return parser.parse_args()





# =============================================================================

# MAIN

# =============================================================================



def main() -> int:

    args = parse_args()

    if args.so_bt and args.so_mt:

        _fail("Use apenas um entre --so-bt e --so-mt.")

        return 1



    mes      = f"{int(args.mes):02d}"

    ano      = str(int(args.ano))

    carimbos = [c.strip() for c in args.carimbo if c.strip()]

    modo_debug = args.so_ocr or args.so_digitacao or args.so_filtro or args.so_bt or args.so_mt



    fazer_bt = not args.so_mt

    fazer_mt = not args.so_bt



    OCR_SAIDA_DIR.mkdir(parents=True, exist_ok=True)

    PIPELINE_SAIDA_ROOT.mkdir(parents=True, exist_ok=True)



    pasta_bt = _resolver_pasta_tipo(args.pasta, mes, ano, "bt")

    pasta_mt = _resolver_pasta_tipo(args.pasta, mes, ano, "mt")



    bt_resgate = _is_resgate(args.pasta, mes, ano, "bt", carimbos)

    mt_resgate = _is_resgate(args.pasta, mes, ano, "mt", carimbos)

    bt_slug    = _slug_resgate(pasta_bt, carimbos) if bt_resgate else ""

    mt_slug    = _slug_resgate(pasta_mt, carimbos) if mt_resgate else ""



    xlsx_bt  = _xlsx_ocr_resgate("bt", bt_slug)  if bt_resgate else _xlsx_ocr(mes, ano, "bt")

    xlsx_mt  = _xlsx_ocr_resgate("mt", mt_slug)  if mt_resgate else _xlsx_ocr(mes, ano, "mt")

    saida_bt = _pipeline_saida_resgate("bt", bt_slug) if bt_resgate else _pipeline_saida("bt")

    saida_mt = _pipeline_saida_resgate("mt", mt_slug) if mt_resgate else _pipeline_saida("mt")



    # -- Banner inicial -------------------------------------------------------

    detalhes: list[str] = [f"Referência : {mes}/{ano}"]

    if fazer_bt:

        detalhes.append(f"Pasta BT   : {pasta_bt}")

        if bt_resgate:

            detalhes.append(f"Resgate BT : {bt_slug}")

    if fazer_mt:

        detalhes.append(f"Pasta MT   : {pasta_mt}")

        if mt_resgate:

            detalhes.append(f"Resgate MT : {mt_slug}")

    if carimbos:

        detalhes.append(f"Carimbos   : {', '.join(carimbos)}")

    modos = []

    if args.so_ocr:       modos.append("só-OCR")

    if args.so_digitacao: modos.append("só-digitação")

    if args.so_filtro:    modos.append("só-filtro")

    if args.so_bt:        modos.append("só-BT")

    if args.so_mt:        modos.append("só-MT")

    if modos:

        detalhes.append(f"Modo       : {', '.join(modos)}")



    _banner(f"PIPELINE {PIPELINE_NOME}  BT + MT", detalhes)



    falhas_criticas: list[str] = []



    # -- OCR -----------------------------------------------------------------

    if fazer_bt and not args.so_digitacao and not args.so_filtro:

        cod = etapa_ocr_tipo(mes, ano, "bt", pasta_bt, xlsx_bt, carimbos=carimbos)

        if cod == 130:

            return 130

        if cod not in (0, 2, 3):

            falhas_criticas.append("OCR BT")

            if not modo_debug:

                _fail("OCR BT falhou  abortando pipeline.")

                return 1

    elif not fazer_bt:

        _info("[skip] OCR BT")



    if fazer_mt and not args.so_digitacao and not args.so_filtro:

        cod = etapa_ocr_tipo(mes, ano, "mt", pasta_mt, xlsx_mt, carimbos=carimbos)

        if cod == 130:

            return 130

        if cod not in (0, 2, 3):

            falhas_criticas.append("OCR MT")

            if not modo_debug:

                _fail("OCR MT falhou  abortando pipeline.")

                return 1

    elif not fazer_mt:

        _info("[skip] OCR MT")



    # -- DIGITAÇÃO -----------------------------------------------------------

    if fazer_bt and not args.so_ocr and not args.so_filtro:

        if not xlsx_bt.exists():

            _warn(f"Planilha OCR BT não encontrada  digitação BT pulada: {xlsx_bt}")

        else:

            cod    = etapa_digitacao_tipo("bt", xlsx_bt, saida_bt, pasta_bt)
            resumo = _ler_resumo_auditoria(saida_bt)

            if resumo["total"] > 0:

                _info(

                    f"Auditoria BT ? total={resumo['total']}  "

                    f"sucesso={resumo['sucesso']}  "

                    f"movíveis={resumo['moviveis']}  "

                    f"pendentes={resumo['pendentes']}"

                )

            if cod != 0 and resumo["moviveis"] == 0:

                falhas_criticas.append("DIGITAÇÃO BT")

                if not modo_debug:

                    _fail("Digitação BT falhou  abortando pipeline.")

                    return 1

            elif cod != 0:

                _warn(f"Digitação BT exit {cod} mas {resumo['moviveis']} conta(s) apta(s) ao filtro.")

    elif not fazer_bt:

        _info("[skip] Digitação BT")



    if fazer_mt and not args.so_ocr and not args.so_filtro:

        if not xlsx_mt.exists():

            _warn(f"Planilha OCR MT não encontrada  digitação MT pulada: {xlsx_mt}")

        else:

            cod    = etapa_digitacao_tipo("mt", xlsx_mt, saida_mt, pasta_mt)
            resumo = _ler_resumo_auditoria(saida_mt)

            if resumo["total"] > 0:

                _info(

                    f"Auditoria MT ? total={resumo['total']}  "

                    f"sucesso={resumo['sucesso']}  "

                    f"movíveis={resumo['moviveis']}  "

                    f"pendentes={resumo['pendentes']}"

                )

            if cod != 0 and resumo["moviveis"] == 0:

                falhas_criticas.append("DIGITAÇÃO MT")

                if not modo_debug:

                    _fail("Digitação MT falhou  abortando pipeline.")

                    return 1

            elif cod != 0:

                _warn(f"Digitação MT exit {cod} mas {resumo['moviveis']} conta(s) apta(s) ao filtro.")

    elif not fazer_mt:

        _info("[skip] Digitação MT")



    # -- FILTRO ---------------------------------------------------------------

    if fazer_bt and not args.so_ocr and not args.so_digitacao:

        cod = etapa_filtro_tipo("bt", pasta_bt, saida_bt)

        if cod != 0:

            falhas_criticas.append("FILTRO BT")

    elif args.so_ocr or args.so_digitacao:

        _info("[debug] Filtro BT pulado")

    else:

        _info("[skip] Filtro BT")



    if fazer_mt and not args.so_ocr and not args.so_digitacao:

        cod = etapa_filtro_tipo("mt", pasta_mt, saida_mt)

        if cod != 0:

            falhas_criticas.append("FILTRO MT")

    elif args.so_ocr or args.so_digitacao:

        _info("[debug] Filtro MT pulado")

    else:

        _info("[skip] Filtro MT")



    # -- Resultado final -------------------------------------------------------

    _p()

    _sep("-")

    if falhas_criticas:

        _fail(f"PIPELINE {PIPELINE_NOME} COM FALHAS: {', '.join(falhas_criticas)}")

        _sep("-")

        return 1



    _ok(f"PIPELINE {PIPELINE_NOME} CONCLUÍDO COM SUCESSO")

    _sep("-")

    return 0





if __name__ == "__main__":

    raise SystemExit(main())

