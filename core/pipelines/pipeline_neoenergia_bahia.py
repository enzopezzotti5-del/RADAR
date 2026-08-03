#!/usr/bin/env python3

# -*- coding: utf-8 -*-

"""

Pipeline Neoenergia Bahia: OCR -> Digitacao -> Filtro.



Uso:

    python pipeline_neoenergia_bahia.py

    python pipeline_neoenergia_bahia.py --mes 03 --ano 2026

    python pipeline_neoenergia_bahia.py --so-ocr

    python pipeline_neoenergia_bahia.py --so-digitacao

    python pipeline_neoenergia_bahia.py --so-filtro

"""



from __future__ import annotations



import argparse

import csv

import datetime as dt

import hashlib

import os

import re

import subprocess

import sys

import threading

from pathlib import Path



try:

    from core.pipelines._visual import _p, _info, _ok, _fail, _warn, _sep, _banner, _rodar as _rodar_visual

except ModuleNotFoundError:

    from _visual import _p, _info, _ok, _fail, _warn, _sep, _banner, _rodar as _rodar_visual





LOCAL_DIR = Path(__file__).resolve().parent.parent

SERVIDOR = Path("//10.10.250.21/Energia")



OCR_SCRIPT = LOCAL_DIR / "ocr" / "ocr_neoenergia.py"

DIGITACAO_SCRIPT = LOCAL_DIR / "digitacao_consen" / "digitacao_consen_enel.py"

FILTRO_SCRIPT = LOCAL_DIR / "digitacao_consen" / "neoenergia_filtro.py"



DOWNLOAD_ROOT = SERVIDOR / "ARQUIVOS ENZO" / "DOWNLOAD NEOENERGIA"

OCR_SAIDA_DIR = SERVIDOR / "ARQUIVOS ENZO" / "OCR NEOENERGIA" / "BAHIA"

PIPELINE_SAIDA = SERVIDOR / "ARQUIVOS ENZO" / "NEOENERGIA_BAHIA_pipeline_saida"

DIGITADAS_DIR = Path("//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO/Digitadas")

PIPELINE_NOME = "NEOENERGIA BAHIA"



PYTHON_EXE = str(Path(sys.executable).parent / "python.exe")



# Ambiente de testes para digitacao Consen (Neoenergia)

NEO_TEST_LOGIN_URL = "https://consen.acaoengenharia.com.br/login.php"

NEO_TEST_TARGET_HASH = "#bpg/gestao/fatura/cadastroTabFatura.php"

NEO_TEST_TARGET_URL = f"{NEO_TEST_LOGIN_URL.rsplit('/', 1)[0]}/index.php{NEO_TEST_TARGET_HASH}"

NEO_TEST_LINK_HREF = "bpg/gestao/fatura/cadastroTabFatura.php"

NEO_TEST_LINK_TEXTO = "Instalacao"

NEO_TEST_USUARIO = "Robo Digitador"

NEO_TEST_SENHA = "Acao2026"







def _mkdir_seguro(pasta: Path) -> None:

    try:

        pasta.mkdir(parents=True, exist_ok=True)

    except OSError:

        pass





def _resetar_auditoria(pasta_saida: Path) -> None:

    arq = pasta_saida / "auditoria_resultados.csv"

    if arq.exists():

        arq.unlink()

        _info(f"[reset] auditoria_resultados.csv removido ({pasta_saida.name})")





def _pasta_default(mes: str, ano: str) -> Path:

    return DOWNLOAD_ROOT / "BAHIA" / f"{ano}-{mes}"





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
    base = base[:60]  # evita WinError 123 em paths UNC com muitos carimbos

    assinatura = hashlib.md5(f"{_path_key(pasta)}|{'|'.join(sorted(carimbos))}".encode("utf-8")).hexdigest()[:8]

    return f"{base}_{assinatura}"





def _is_resgate(pasta: Path, mes: str, ano: str, carimbos: list[str]) -> bool:

    if any(c.strip() for c in carimbos):

        return True

    mensal = _pasta_default(mes, ano)

    if _path_key(pasta) == _path_key(mensal):

        return False

    # Quando a pasta mensal ja foi organizada, rodar diretamente em BT/MT

    # ainda e o fluxo mensal normal, nao um resgate pontual.

    if pasta.name.upper() in {"BT", "MT"} and _path_key(pasta.parent) == _path_key(mensal):

        return False

    return True





def _xlsx_ocr_generico(mes: str, ano: str, tipo: str) -> Path:

    return SERVIDOR / "ARQUIVOS ENZO" / "OCR NEOENERGIA" / f"ocr_neoenergia_{tipo.upper()}_{mes}{ano}.xlsx"





def _xlsx_saida(mes: str, ano: str, tipo: str) -> Path:

    return OCR_SAIDA_DIR / f"ocr_neoenergia_bahia_{tipo.upper()}_{mes}{ano}.xlsx"





def _xlsx_resgate(tipo: str, slug: str) -> Path:

    return OCR_SAIDA_DIR / "_resgates" / f"ocr_neoenergia_bahia_{tipo.upper()}_{slug}.xlsx"





def _pipeline_saida_tipo(tipo: str) -> Path:

    return PIPELINE_SAIDA / tipo.upper()





def _pipeline_saida_resgate(tipo: str, slug: str) -> Path:

    return PIPELINE_SAIDA / "_resgates" / tipo.upper() / slug





def _materializar_xlsx_bahia(mes: str, ano: str, tipo: str, destino_override: Path | None = None) -> Path:

    origem = _xlsx_ocr_generico(mes, ano, tipo)

    destino = destino_override or _xlsx_saida(mes, ano, tipo)

    if not origem.exists():

        return destino

    _mkdir_seguro(destino.parent)

    if origem.resolve() == destino.resolve():

        return destino

    try:

        import shutil



        shutil.copy2(origem, destino)

        return destino

    except Exception as exc:

        _warn(f"Falha ao copiar XLSX {tipo} para pasta da Bahia: {exc}")

        return origem





def _rodar(descricao: str, cmd: list[str], env_extra: dict[str, str] | None = None) -> int:

    return _rodar_visual(descricao, cmd, env_extra=env_extra)





def _ler_resumo_auditoria(pasta_saida: Path) -> dict[str, int]:

    auditoria = pasta_saida / "auditoria_resultados.csv"

    resumo = {

        "total": 0,

        "sucesso": 0,

        "moviveis": 0,

        "pendentes": 0,

    }

    if not auditoria.exists():

        return resumo



    status_moviveis = {"sucesso_auditoria", "auditoria_sem_valor", "pulado_carimbo_existente"}

    status_ok = status_moviveis | {"pulado_referencia_existente"}



    for enc in ("utf-8-sig", "utf-8", "latin-1"):

        try:

            with auditoria.open("r", newline="", encoding=enc) as f:

                for row in csv.DictReader(f, delimiter=";"):

                    resumo["total"] += 1

                    status = str(row.get("status", "")).strip().lower()

                    if status in status_ok:

                        resumo["sucesso"] += 1

                    else:

                        resumo["pendentes"] += 1

                    if status in status_moviveis:

                        resumo["moviveis"] += 1

            return resumo

        except UnicodeDecodeError:

            continue

        except Exception as exc:

            _warn(f"Falha ao ler auditoria em {auditoria}: {exc}")

            return resumo

    return resumo





def etapa_ocr(

    mes: str,

    ano: str,

    tipo: str = "ambos",

    carimbos: list[str] | None = None,

    pasta: str | None = None,

    saida_por_tipo: dict[str, Path] | None = None,

) -> int:

    if not OCR_SCRIPT.exists():

        _fail(f"Script OCR nao encontrado: {OCR_SCRIPT}")

        return 1

    cmd = [

        PYTHON_EXE,

        str(OCR_SCRIPT),

        "--mes",

        str(int(mes)),

        "--ano",

        str(int(ano)),

        "--tipo",

        str(tipo),

    ]

    if pasta:

        cmd.extend(["--pasta", str(pasta)])

    if saida_por_tipo:

        saida_bt = saida_por_tipo.get("bt")

        saida_mt = saida_por_tipo.get("mt")

        if saida_bt:

            _mkdir_seguro(saida_bt.parent)

            cmd.extend(["--saida-bt", str(saida_bt)])

        if saida_mt:

            _mkdir_seguro(saida_mt.parent)

            cmd.extend(["--saida-mt", str(saida_mt)])

    for c in (carimbos or []):

        c_limpo = str(c).strip()

        if c_limpo:

            cmd.extend(["--carimbo", c_limpo])

    return _rodar(f"OCR {PIPELINE_NOME} {mes}/{ano}", cmd)





def etapa_digitacao(xlsx: Path, tipo: str, pasta_pdfs: Path, pasta_saida: Path) -> int:

    if not DIGITACAO_SCRIPT.exists():

        _fail(f"Script de digitacao nao encontrado: {DIGITACAO_SCRIPT}")

        return 1

    if not xlsx.exists():

        _fail(f"Planilha de OCR nao encontrada: {xlsx}")

        return 1



    pasta_saida.mkdir(parents=True, exist_ok=True)



    env_extra = {

        "ENEL_EXCEL_PATH": str(xlsx),
        "ENEL_DIGITACAO_PASTA_PDFS": str(pasta_pdfs),
        "ENEL_DIGITACAO_INVESTIGAR_DIR": "//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO/Investigar",

        "CONSEN_PIPELINE_SAIDA": str(pasta_saida),

        "CONSEN_INTERATIVO_FECHAR": "0",

        "DIGITACAO_FATOR_VELOCIDADE":    "0.25",

        "CONSEN_PERMITIR_LOTE_COMPLETO": "1",

        "CONSEN_LOGIN_URL": NEO_TEST_LOGIN_URL,

        "CONSEN_TARGET_HASH": NEO_TEST_TARGET_HASH,

        "CONSEN_TARGET_URL": NEO_TEST_TARGET_URL,

        "CONSEN_LINK_HREF": NEO_TEST_LINK_HREF,

        "CONSEN_LINK_TEXTO": NEO_TEST_LINK_TEXTO,

        "CONSEN_USUARIO": NEO_TEST_USUARIO,

        "CONSEN_SENHA": NEO_TEST_SENHA,

    }

    cmd = [PYTHON_EXE, str(DIGITACAO_SCRIPT)]

    return _rodar(f"DIGITACAO {PIPELINE_NOME} {tipo.upper()} ({xlsx.name})", cmd, env_extra=env_extra)





def _atualizar_master_pos_filtro(auditoria_csv: Path) -> None:

    try:

        import sys as _sys

        _sys.path.insert(0, str(LOCAL_DIR.parent / "scripts"))

        from indice_master import MasterIndice, marcar_digitados_do_auditoria

        contadores = marcar_digitados_do_auditoria(auditoria_csv, MasterIndice())

        _info(f"[MASTER] Digitacao atualizada: {contadores}")

    except Exception as _e:

        _warn(f"[MASTER] Nao foi possivel atualizar o indice master: {_e}")





def etapa_filtro(tipo: str, root_pdfs: Path, pasta_saida: Path) -> int:

    if not FILTRO_SCRIPT.exists():

        _fail(f"Script de filtro nao encontrado: {FILTRO_SCRIPT}")

        return 1



    auditoria = pasta_saida / "auditoria_resultados.csv"

    if not auditoria.exists():

        _warn(f"auditoria_resultados.csv nao encontrado em {pasta_saida} - filtro pulado")

        return 0



    env_extra = {

        "NEO_FILTRO_CSV": str(auditoria),

        "NEO_FILTRO_ROOT": str(root_pdfs),

        "NEO_FILTRO_DESTINO": str(DIGITADAS_DIR),

    }

    cmd = [PYTHON_EXE, str(FILTRO_SCRIPT)]

    codigo = _rodar(f"FILTRO {PIPELINE_NOME} {tipo.upper()}", cmd, env_extra=env_extra)

    _atualizar_master_pos_filtro(auditoria)

    return codigo





def parse_args():

    hoje = dt.date.today()

    p = argparse.ArgumentParser(description="Pipeline Neoenergia Bahia: OCR -> Digitacao -> Filtro")

    p.add_argument("--mes", type=str, default=f"{hoje.month:02d}")

    p.add_argument("--ano", type=str, default=str(hoje.year))

    p.add_argument("--so-ocr", action="store_true")

    p.add_argument("--so-digitacao", action="store_true")

    p.add_argument("--so-filtro", action="store_true")

    p.add_argument("--tipo", choices=["bt", "mt", "ambos"], default="ambos")

    p.add_argument(

        "--pasta",

        type=str,

        default="",

        help="Pasta mensal da Neoenergia Bahia com PDFs misturados",

    )

    p.add_argument(

        "--carimbo",

        action="append",

        default=[],

        help="Carimbo(s) especifico(s) para OCR (pode repetir). Ex: --carimbo BB_2001242",

    )

    return p.parse_args()





def main() -> int:

    args = parse_args()

    mes = f"{int(args.mes):02d}"

    ano = str(int(args.ano))

    tipos = ["BT", "MT"] if args.tipo == "ambos" else [args.tipo.upper()]

    pasta_pdfs = Path(str(args.pasta).strip()) if str(args.pasta).strip() else _pasta_default(mes, ano)

    carimbos = [str(c).strip() for c in (args.carimbo or []) if str(c).strip()]

    resgate = _is_resgate(pasta_pdfs, mes, ano, carimbos)

    slug = _slug_resgate(pasta_pdfs, carimbos) if resgate else ""

    xlsxs = {

        tipo: (_xlsx_resgate(tipo, slug) if resgate else _xlsx_saida(mes, ano, tipo))

        for tipo in tipos

    }

    saidas = {

        tipo: (_pipeline_saida_resgate(tipo, slug) if resgate else _pipeline_saida_tipo(tipo))

        for tipo in tipos

    }

    modo_debug = args.so_ocr or args.so_digitacao or args.so_filtro



    _mkdir_seguro(OCR_SAIDA_DIR)

    _mkdir_seguro(PIPELINE_SAIDA)



    detalhes = [

        f"Referência : {mes}/{ano}",

        f"Pasta PDFs : {pasta_pdfs}",

        f"Tipo       : {args.tipo}",

        f"Resgate    : {'sim' if resgate else 'nao'}",

    ]

    if carimbos:

        detalhes.append(f"Carimbos   : {', '.join(carimbos)}")

    _banner(f"PIPELINE {PIPELINE_NOME}", detalhes)



    falhas_criticas: list[str] = []



    # 1) OCR

    if not args.so_digitacao and not args.so_filtro:

        cod = etapa_ocr(

            mes,

            ano,

            tipo=args.tipo,

            carimbos=carimbos,

            pasta=str(pasta_pdfs),

            saida_por_tipo={tipo.lower(): xlsxs[tipo] for tipo in tipos},

        )

        if cod != 0:

            falhas_criticas.append("OCR")

            if not modo_debug:

                return 1

    else:

        _info("[debug] Pulando OCR.")



    # 2) Digitacao

    if not args.so_ocr and not args.so_filtro:

        algum_xlsx = False

        for tipo, xlsx in xlsxs.items():

            if not xlsx.exists() and not resgate:

                xlsx = _materializar_xlsx_bahia(mes, ano, tipo, destino_override=xlsx)

                xlsxs[tipo] = xlsx

            if not xlsx.exists():

                _warn(f"Planilha {tipo} nao encontrada apos OCR, digitacao pulada: {xlsx}")

                continue

            algum_xlsx = True

            _resetar_auditoria(saidas[tipo])

            cod = etapa_digitacao(xlsx, tipo, pasta_pdfs, saidas[tipo])

            resumo_aud = _ler_resumo_auditoria(saidas[tipo])

            if resumo_aud["total"] > 0:

                _info(

                    f"Auditoria {tipo}: total={resumo_aud['total']} "

                    f"sucesso={resumo_aud['sucesso']} moviveis={resumo_aud['moviveis']} "

                    f"pendentes={resumo_aud['pendentes']}"

                )

            if cod != 0:

                # Se a digitacao retornou erro, mas ainda assim produziu

                # auditoria valida para pelo menos uma conta, seguimos para o

                # filtro daquele tipo para nao perder as digitadas com sucesso.

                if resumo_aud["moviveis"] > 0:

                    _warn(

                        f"Digitacao {tipo} terminou com exit {cod}, mas ha "

                        f"{resumo_aud['moviveis']} conta(s) aptas ao filtro. Continuando."

                    )

                else:

                    falhas_criticas.append(f"DIGITACAO_{tipo}")

                    if not modo_debug:

                        return 1

        if not algum_xlsx:

            falhas_criticas.append("DIGITACAO_SEM_PLANILHA")

            if not modo_debug:

                return 1

    else:

        _info("[debug] Pulando Digitacao.")



    # 3) Filtro (best effort)

    if not args.so_ocr and not args.so_digitacao:

        root_filtro = pasta_pdfs

        for tipo in tipos:

            etapa_filtro(tipo, root_filtro, saidas[tipo])

    else:

        _info("[debug] Pulando Filtro.")



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

