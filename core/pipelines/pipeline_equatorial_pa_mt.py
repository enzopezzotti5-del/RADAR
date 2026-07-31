#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pipeline EQUATORIAL PA MT: OCR -> Digitacao -> Renomear -> Filtro.

Uso:
    python pipeline_equatorial_pa_mt.py --pasta "\\\\srv\\...\\MT" --mes 07 --ano 2026
    python pipeline_equatorial_pa_mt.py --so-ocr --pasta "..."
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re as _re
import sys
from pathlib import Path

try:
    from core.pipelines._visual import _p, _info, _ok, _fail, _warn, _sep, _banner, _rodar as _rodar_visual
except ModuleNotFoundError:
    from _visual import _p, _info, _ok, _fail, _warn, _sep, _banner, _rodar as _rodar_visual

LOCAL_DIR = Path(__file__).resolve().parent.parent
SERVIDOR  = Path("//10.10.250.21/Energia")

OCR_SCRIPT       = LOCAL_DIR / "ocr" / "ocr_equatorial_pa_mt.py"
DIGITACAO_SCRIPT = LOCAL_DIR / "digitacao_consen" / "digitacao_consen_enel.py"
FILTRO_SCRIPT    = LOCAL_DIR / "digitacao_consen" / "neoenergia_filtro.py"

OCR_SAIDA_DIR  = SERVIDOR / "ARQUIVOS ENZO" / "OCR EQUATORIAL PA"
PIPELINE_SAIDA = SERVIDOR / "ARQUIVOS ENZO" / "EQUATORIAL_PA_MT_pipeline_saida"
DIGITADAS_DIR  = Path("//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO/Digitadas")
PIPELINE_NOME  = "EQUATORIAL PA MT"

CONSEN_LOGIN_URL   = "https://consen.acaoengenharia.com.br/login.php"
CONSEN_TARGET_HASH = "#bpg/gestao/fatura/cadastroTabFatura.php"
CONSEN_TARGET_URL  = f"{CONSEN_LOGIN_URL.rsplit('/', 1)[0]}/index.php{CONSEN_TARGET_HASH}"
CONSEN_LINK_HREF   = "bpg/gestao/fatura/cadastroTabFatura.php"
CONSEN_LINK_TEXTO  = "Instalacao"
CONSEN_USUARIO     = "Robo Digitador"
CONSEN_SENHA       = "Acao2026"

PYTHON_EXE = str(Path(sys.executable).parent / "python.exe")

PASTA_PDF_DEFAULT = Path(
    "//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO/Faturas/EQUATORIAL/PARA/MT"
)


def _mkdir_seguro(pasta: Path) -> None:
    try:
        pasta.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def _resetar_auditoria(pasta_saida: Path) -> None:
    arq = pasta_saida / "auditoria_resultados.csv"
    if arq.exists():
        arq.unlink()


def _ler_resumo_auditoria(pasta_saida: Path) -> dict[str, int]:
    auditoria = pasta_saida / "auditoria_resultados.csv"
    resumo = {"total": 0, "sucesso": 0, "moviveis": 0, "pendentes": 0}
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
            _warn(f"Falha ao ler auditoria: {exc}")
            return resumo
    return resumo


def etapa_ocr(mes: str, ano: str, pasta: str, xlsx_saida: Path) -> int:
    if not OCR_SCRIPT.exists():
        _fail(f"Script OCR nao encontrado: {OCR_SCRIPT}")
        return 1
    _mkdir_seguro(xlsx_saida.parent)
    cmd = [
        PYTHON_EXE, str(OCR_SCRIPT),
        "--pasta", pasta,
        "--saida", str(xlsx_saida),
        "--mes", str(int(mes)),
        "--ano", str(int(ano)),
    ]
    return _rodar_visual(f"OCR {PIPELINE_NOME} {mes}/{ano}", cmd)


def etapa_digitacao(xlsx: Path, pasta_saida: Path) -> int:
    if not DIGITACAO_SCRIPT.exists() or not xlsx.exists():
        _fail("Script ou planilha nao encontrado")
        return 1
    pasta_saida.mkdir(parents=True, exist_ok=True)
    env_extra = {
        "ENEL_EXCEL_PATH":              str(xlsx),
        "CONSEN_PIPELINE_SAIDA":        str(pasta_saida),
        "CONSEN_INTERATIVO_FECHAR":     "0",
        "DIGITACAO_FATOR_VELOCIDADE":   "0.25",
        "CONSEN_PERMITIR_LOTE_COMPLETO": "1",
        "CONSEN_LOGIN_URL":             CONSEN_LOGIN_URL,
        "CONSEN_TARGET_HASH":           CONSEN_TARGET_HASH,
        "CONSEN_TARGET_URL":            CONSEN_TARGET_URL,
        "CONSEN_LINK_HREF":             CONSEN_LINK_HREF,
        "CONSEN_LINK_TEXTO":            CONSEN_LINK_TEXTO,
        "CONSEN_USUARIO":               CONSEN_USUARIO,
        "CONSEN_SENHA":                 CONSEN_SENHA,
    }
    return _rodar_visual(f"DIGITACAO {PIPELINE_NOME} ({xlsx.name})", [PYTHON_EXE, str(DIGITACAO_SCRIPT)], env_extra=env_extra)


def etapa_renomear_pdfs(root_pdfs: Path, pasta_saida: Path) -> int:
    """Renomeia PDFs instalacao-nome -> BB_XXXXXXX.pdf usando auditoria CSV."""
    auditoria = pasta_saida / "auditoria_resultados.csv"
    if not auditoria.exists():
        _warn("auditoria nao encontrada -- renomeacao pulada")
        return 0

    mapa: dict[str, str] = {}
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with auditoria.open("r", newline="", encoding=enc) as f:
                for row in csv.DictReader(f, delimiter=";"):
                    inst = str(row.get("instalacao", "")).strip()
                    car  = str(row.get("carimbo", "")).strip().lstrip("0")
                    if inst and car:
                        mapa[inst] = car
            break
        except UnicodeDecodeError:
            continue

    if not mapa:
        _warn("Mapa instalacao->carimbo vazio")
        return 0

    renomeados = 0
    nao_encontrados = 0
    for pdf in sorted(root_pdfs.glob("*.pdf")):
        stem = pdf.stem
        # Remove sufixo de data: "132.778.013-50 26.07.26 celpa" -> "132.778.013-50"
        instalacao_fmt = _re.sub(r"(\s*-+\s*|\s+)\d{1,2}\.\d{2}.*$", "", stem).strip()
        carimbo = mapa.get(instalacao_fmt)
        if not carimbo:
            digitos = _re.sub(r"\D", "", instalacao_fmt)
            carimbo = next((v for k, v in mapa.items() if _re.sub(r"\D", "", k) == digitos), None)
        if not carimbo:
            nao_encontrados += 1
            continue
        novo_nome = pdf.parent / f"BB_{carimbo}.pdf"
        if novo_nome.exists():
            _warn(f"Ja existe: {novo_nome.name} -- pulando {pdf.name}")
            continue
        try:
            pdf.rename(novo_nome)
            renomeados += 1
            _info(f"Renomeado: {pdf.name} -> BB_{carimbo}.pdf")
        except Exception as exc:
            _warn(f"Erro ao renomear {pdf.name}: {exc}")
            nao_encontrados += 1

    _ok(f"Renomeacao: {renomeados} PDFs renomeados, {nao_encontrados} nao encontrados")
    return 0


def etapa_filtro(root_pdfs: Path, pasta_saida: Path) -> int:
    if not FILTRO_SCRIPT.exists():
        _fail(f"Script de filtro nao encontrado: {FILTRO_SCRIPT}")
        return 1
    auditoria = pasta_saida / "auditoria_resultados.csv"
    if not auditoria.exists():
        _warn("auditoria nao encontrada -- filtro pulado")
        return 0
    env_extra = {
        "NEO_FILTRO_CSV":     str(auditoria),
        "NEO_FILTRO_ROOT":    str(root_pdfs),
        "NEO_FILTRO_DESTINO": str(DIGITADAS_DIR),
    }
    return _rodar_visual(f"FILTRO {PIPELINE_NOME}", [PYTHON_EXE, str(FILTRO_SCRIPT)], env_extra=env_extra)


def parse_args() -> argparse.Namespace:
    hoje = dt.date.today()
    p = argparse.ArgumentParser(description=f"Pipeline {PIPELINE_NOME}")
    p.add_argument("--mes",  type=str, default=f"{hoje.month:02d}")
    p.add_argument("--ano",  type=str, default=str(hoje.year))
    p.add_argument("--pasta", type=str, default=str(PASTA_PDF_DEFAULT))
    p.add_argument("--so-ocr",       action="store_true")
    p.add_argument("--so-digitacao", action="store_true")
    p.add_argument("--so-filtro",    action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    mes  = f"{int(args.mes):02d}"
    ano  = str(int(args.ano))
    pasta_pdfs  = Path(str(args.pasta).strip())
    xlsx        = OCR_SAIDA_DIR / f"ocr_equatorial_pa_MT_{mes}{ano}.xlsx"
    pasta_saida = PIPELINE_SAIDA / "MT"
    modo_debug  = args.so_ocr or args.so_digitacao or args.so_filtro

    _mkdir_seguro(OCR_SAIDA_DIR)
    _mkdir_seguro(PIPELINE_SAIDA)

    _banner(f"PIPELINE {PIPELINE_NOME}", [
        f"Referencia : {mes}/{ano}",
        f"Pasta PDFs : {pasta_pdfs}",
    ])

    falhas: list[str] = []

    if not args.so_digitacao and not args.so_filtro:
        if not pasta_pdfs.exists():
            _fail(f"Pasta nao encontrada: {pasta_pdfs}"); return 1
        cod = etapa_ocr(mes, ano, str(pasta_pdfs), xlsx)
        if cod != 0:
            falhas.append("OCR")
            if not modo_debug: return 1
    else:
        _info("[debug] Pulando OCR.")

    if not args.so_ocr and not args.so_filtro:
        if not xlsx.exists():
            _fail(f"Planilha nao encontrada: {xlsx}"); return 1
        _resetar_auditoria(pasta_saida)
        cod = etapa_digitacao(xlsx, pasta_saida)
        resumo = _ler_resumo_auditoria(pasta_saida)
        if resumo["total"] > 0:
            _info(f"Auditoria: total={resumo['total']} sucesso={resumo['sucesso']} moviveis={resumo['moviveis']} pendentes={resumo['pendentes']}")
        if cod != 0:
            if resumo["moviveis"] > 0:
                _warn(f"Digitacao exit {cod}, mas ha {resumo['moviveis']} moviveis. Continuando.")
            else:
                falhas.append("DIGITACAO")
                if not modo_debug: return 1
    else:
        _info("[debug] Pulando Digitacao.")

    if not args.so_ocr and not args.so_digitacao:
        etapa_renomear_pdfs(pasta_pdfs, pasta_saida)
        etapa_filtro(pasta_pdfs, pasta_saida)
    else:
        _info("[debug] Pulando Filtro.")

    _p(); _sep("=")
    if falhas:
        _fail(f"PIPELINE {PIPELINE_NOME} COM FALHAS: {', '.join(falhas)}"); _sep("="); return 1
    _ok(f"PIPELINE {PIPELINE_NOME} CONCLUIDO COM SUCESSO"); _sep("=")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
