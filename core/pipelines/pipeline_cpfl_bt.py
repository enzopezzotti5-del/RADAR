#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pipeline CPFL BT: OCR -> Digitacao -> Filtro.

Uso:
    python pipeline_cpfl_bt.py
    python pipeline_cpfl_bt.py --mes 05 --ano 2026 --pasta "//servidor/CPFL/2026-05"
    python pipeline_cpfl_bt.py --so-ocr
    python pipeline_cpfl_bt.py --so-digitacao
    python pipeline_cpfl_bt.py --so-filtro
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import os
import re
import sys
from pathlib import Path

try:
    from core.pipelines._visual import _p, _info, _ok, _fail, _warn, _sep, _banner, _rodar as _rodar_visual
except ModuleNotFoundError:
    from _visual import _p, _info, _ok, _fail, _warn, _sep, _banner, _rodar as _rodar_visual

LOCAL_DIR = Path(__file__).resolve().parent.parent
SERVIDOR = Path("//10.10.250.21/Energia")

OCR_SCRIPT = LOCAL_DIR / "ocr" / "ocr_cpfl_bt.py"
DIGITACAO_SCRIPT = LOCAL_DIR / "digitacao_consen" / "digitacao_consen_enel.py"
FILTRO_SCRIPT = LOCAL_DIR / "digitacao_consen" / "neoenergia_filtro.py"

OCR_SAIDA_DIR = SERVIDOR / "ARQUIVOS ENZO" / "OCR CPFL"
PIPELINE_SAIDA = SERVIDOR / "ARQUIVOS ENZO" / "CPFL_pipeline_saida"
DIGITADAS_DIR = Path("//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO/Digitadas")
JA_EXISTIAM_DIR = DIGITADAS_DIR.parent / "Ja_existiam_no_Consen"
INVESTIGAR_DIR = DIGITADAS_DIR.parent / "Watcher_V2" / "Investigar"
PIPELINE_NOME = "CPFL BT"

CONSEN_LOGIN_URL   = "https://consen.acaoengenharia.com.br/login.php"
CONSEN_TARGET_HASH = "#bpg/gestao/fatura/cadastroTabFatura.php"
CONSEN_TARGET_URL  = f"{CONSEN_LOGIN_URL.rsplit('/', 1)[0]}/index.php{CONSEN_TARGET_HASH}"
CONSEN_LINK_HREF   = "bpg/gestao/fatura/cadastroTabFatura.php"
CONSEN_LINK_TEXTO  = "Instalacao"
CONSEN_USUARIO     = "Robo Digitador"
CONSEN_SENHA       = "Acao2026"

PYTHON_EXE = str(Path(sys.executable).parent / "python.exe")


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


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _slug_resgate(pasta: Path, carimbos: list[str]) -> str:
    partes: list[str] = [pasta.name.strip()] if pasta.name.strip() else []
    cs = [c.strip() for c in carimbos if c.strip()]
    if len(cs) <= 5:
        partes.extend(cs)
    elif cs:
        partes += [cs[0], f"mais{len(cs) - 2}", cs[-1]]
    base = "_".join(partes) or "resgate"
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_") or "resgate"
    assinatura = hashlib.md5(
        f"{_path_key(pasta)}|{'|'.join(sorted(carimbos))}".encode()
    ).hexdigest()[:8]
    return f"{base}_{assinatura}"


def _xlsx_saida(mes: str, ano: str) -> Path:
    return OCR_SAIDA_DIR / f"ocr_cpfl_BT_{mes}{ano}.xlsx"


def _xlsx_resgate(slug: str) -> Path:
    return OCR_SAIDA_DIR / "_resgates" / f"ocr_cpfl_BT_{slug}.xlsx"


def _pipeline_saida_dir(slug: str = "") -> Path:
    return PIPELINE_SAIDA / "_resgates" / slug if slug else PIPELINE_SAIDA / "BT"


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


def etapa_ocr(mes: str, ano: str, pasta: str, xlsx_saida: Path, carimbos: list[str]) -> int:
    if not OCR_SCRIPT.exists():
        _fail(f"Script OCR nao encontrado: {OCR_SCRIPT}")
        return 1
    _mkdir_seguro(xlsx_saida.parent)
    cmd = [
        PYTHON_EXE, str(OCR_SCRIPT),
        "--mes", str(int(mes)),
        "--ano", str(int(ano)),
        "--pasta", pasta,
        "--saida", str(xlsx_saida),
    ]
    for c in carimbos:
        cmd.extend(["--carimbo", c])
    return _rodar_visual(f"OCR {PIPELINE_NOME} {mes}/{ano}", cmd)


def etapa_digitacao(xlsx: Path, pasta_saida: Path) -> int:
    if not DIGITACAO_SCRIPT.exists():
        _fail(f"Script de digitacao nao encontrado: {DIGITACAO_SCRIPT}")
        return 1
    if not xlsx.exists():
        _fail(f"Planilha OCR nao encontrada: {xlsx}")
        return 1
    pasta_saida.mkdir(parents=True, exist_ok=True)
    env_extra = {
        "ENEL_EXCEL_PATH":            str(xlsx),
        "CONSEN_PIPELINE_SAIDA":      str(pasta_saida),
        "CONSEN_INTERATIVO_FECHAR":   "0",
        "DIGITACAO_FATOR_VELOCIDADE":    "0.25",
        "CONSEN_PERMITIR_LOTE_COMPLETO": "1",
        "CONSEN_LOGIN_URL":           CONSEN_LOGIN_URL,
        "CONSEN_TARGET_HASH":         CONSEN_TARGET_HASH,
        "CONSEN_TARGET_URL":          CONSEN_TARGET_URL,
        "CONSEN_LINK_HREF":           CONSEN_LINK_HREF,
        "CONSEN_LINK_TEXTO":          CONSEN_LINK_TEXTO,
        "CONSEN_USUARIO":             CONSEN_USUARIO,
        "CONSEN_SENHA":               CONSEN_SENHA,
    }
    return _rodar_visual(f"DIGITACAO {PIPELINE_NOME} ({xlsx.name})", [PYTHON_EXE, str(DIGITACAO_SCRIPT)], env_extra=env_extra)


def _atualizar_master(auditoria_csv: Path) -> None:
    try:
        sys.path.insert(0, str(LOCAL_DIR.parent))
        from indice_master import MasterIndice, marcar_digitados_do_auditoria
        contadores = marcar_digitados_do_auditoria(auditoria_csv, MasterIndice())
        _info(f"[MASTER] Digitacao atualizada: {contadores}")
    except Exception as e:
        _warn(f"[MASTER] Nao foi possivel atualizar o indice master: {e}")


def _normalizar_carimbo_auditoria(v: object) -> str:
    txt = str(v or "").strip().upper().replace("BB_", "")
    if txt.endswith(".0"):
        txt = txt[:-2]
    return f"BB_{txt}" if txt else ""


def _status_auditoria(row: dict) -> str:
    status = str(row.get("status") or "").strip().lower()
    extras = row.get(None) or []
    if isinstance(extras, list):
        for item in reversed(extras):
            item_norm = str(item or "").strip().lower()
            if item_norm:
                status = item_norm
                break
    return status


def _linhas_auditoria(auditoria_csv: Path) -> list[dict]:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        for sep in (";", ","):
            try:
                with auditoria_csv.open("r", newline="", encoding=enc) as f:
                    rows = list(csv.DictReader(f, delimiter=sep))
                if rows:
                    return rows
            except UnicodeDecodeError:
                continue
            except Exception:
                break
    return []


def _destino_esperado_por_status(status: str) -> Path | None:
    s = status.strip().lower()
    if s in {"sucesso_auditoria", "pulado_carimbo_existente"}:
        return DIGITADAS_DIR
    if s == "pulado_referencia_existente":
        return JA_EXISTIAM_DIR
    if s == "auditoria_sem_valor":
        return INVESTIGAR_DIR
    return None


def _pdf_final_do_carimbo(carimbo: str, destino: Path) -> Path | None:
    candidatos = [
        destino / f"{carimbo}.pdf",
        destino / f"{carimbo.upper()}.pdf",
        destino / f"{carimbo.lower()}.pdf",
    ]
    for p in candidatos:
        if p.exists() and p.is_file():
            return p
    encontrados = sorted(destino.glob(f"{carimbo}*.pdf"))
    return encontrados[0] if encontrados else None


def _reconciliar_arquivos_finais(auditoria_csv: Path, master) -> dict[str, int]:
    contadores = {"atualizado": 0, "sem_destino": 0, "sem_pdf": 0, "erro": 0}
    for row in _linhas_auditoria(auditoria_csv):
        carimbo = _normalizar_carimbo_auditoria(row.get("carimbo") or row.get("fatCarimbo"))
        if not carimbo:
            continue
        destino = _destino_esperado_por_status(_status_auditoria(row))
        if destino is None:
            contadores["sem_destino"] += 1
            continue
        pdf_final = _pdf_final_do_carimbo(carimbo, destino)
        if pdf_final is None:
            contadores["sem_pdf"] += 1
            continue
        if master.atualizar_arquivo_final(carimbo, pdf_final, log_evento=False):
            contadores["atualizado"] += 1
        else:
            contadores["erro"] += 1
    return contadores


def _atualizar_master(auditoria_csv: Path) -> None:
    try:
        sys.path.insert(0, str(LOCAL_DIR.parent))
        from indice_master import MasterIndice, marcar_digitados_do_auditoria

        master = MasterIndice()
        contadores = marcar_digitados_do_auditoria(auditoria_csv, master)
        _info(f"[MASTER] Digitacao atualizada: {contadores}")
        reconciliados = _reconciliar_arquivos_finais(auditoria_csv, master)
        _info(f"[MASTER] Arquivos finais reconciliados: {reconciliados}")
    except Exception as e:
        _warn(f"[MASTER] Nao foi possivel atualizar o indice master: {e}")


def etapa_filtro(root_pdfs: Path, pasta_saida: Path) -> int:
    if not FILTRO_SCRIPT.exists():
        _fail(f"Script de filtro nao encontrado: {FILTRO_SCRIPT}")
        return 1
    auditoria = pasta_saida / "auditoria_resultados.csv"
    if not auditoria.exists():
        _warn(f"auditoria_resultados.csv nao encontrado em {pasta_saida}  filtro pulado")
        return 0
    env_extra = {
        "NEO_FILTRO_CSV":     str(auditoria),
        "NEO_FILTRO_ROOT":    str(root_pdfs),
        "NEO_FILTRO_DESTINO": str(DIGITADAS_DIR),
    }
    codigo = _rodar_visual(f"FILTRO {PIPELINE_NOME}", [PYTHON_EXE, str(FILTRO_SCRIPT)], env_extra=env_extra)
    _atualizar_master(auditoria)
    return codigo


def parse_args() -> argparse.Namespace:
    hoje = dt.date.today()
    p = argparse.ArgumentParser(description=f"Pipeline {PIPELINE_NOME}: OCR -> Digitacao -> Filtro")
    p.add_argument("--mes",          type=str, default=f"{hoje.month:02d}")
    p.add_argument("--ano",          type=str, default=str(hoje.year))
    p.add_argument("--pasta",        type=str, default="")
    p.add_argument("--so-ocr",       action="store_true")
    p.add_argument("--so-digitacao", action="store_true")
    p.add_argument("--so-filtro",    action="store_true")
    p.add_argument("--carimbo",      action="append", default=[])
    return p.parse_args()


def main() -> int:
    args = parse_args()
    mes = f"{int(args.mes):02d}"
    ano = str(int(args.ano))
    carimbos = [str(c).strip() for c in (args.carimbo or []) if str(c).strip()]
    pasta_pdfs = Path(str(args.pasta).strip()) if str(args.pasta).strip() else Path("")
    resgate = bool(carimbos) or bool(args.pasta)
    slug = _slug_resgate(pasta_pdfs, carimbos) if resgate else ""
    xlsx = _xlsx_resgate(slug) if resgate else _xlsx_saida(mes, ano)
    pasta_saida = _pipeline_saida_dir(slug)
    modo_debug = args.so_ocr or args.so_digitacao or args.so_filtro

    _mkdir_seguro(OCR_SAIDA_DIR)
    _mkdir_seguro(PIPELINE_SAIDA)

    _banner(f"PIPELINE {PIPELINE_NOME}", [
        f"Referência : {mes}/{ano}",
        f"Pasta PDFs : {pasta_pdfs or '(default)'}",
        f"Resgate    : {'sim' if resgate else 'nao'}",
    ])

    falhas: list[str] = []

    if not args.so_digitacao and not args.so_filtro:
        if not pasta_pdfs or not pasta_pdfs.exists():
            _fail(f"Pasta de PDFs nao encontrada: {pasta_pdfs}")
            return 1
        cod = etapa_ocr(mes, ano, str(pasta_pdfs), xlsx, carimbos)
        if cod != 0:
            falhas.append("OCR")
            if not modo_debug:
                return 1
    else:
        _info("[debug] Pulando OCR.")

    if not args.so_ocr and not args.so_filtro:
        if not xlsx.exists():
            _fail(f"Planilha nao encontrada apos OCR: {xlsx}")
            return 1
        _resetar_auditoria(pasta_saida)
        cod = etapa_digitacao(xlsx, pasta_saida)
        resumo = _ler_resumo_auditoria(pasta_saida)
        if resumo["total"] > 0:
            _info(f"Auditoria: total={resumo['total']} sucesso={resumo['sucesso']} "
                  f"moviveis={resumo['moviveis']} pendentes={resumo['pendentes']}")
        if cod != 0:
            if resumo["moviveis"] > 0:
                _warn(f"Digitacao terminou com exit {cod}, mas ha {resumo['moviveis']} conta(s) aptas ao filtro. Continuando.")
            else:
                falhas.append("DIGITACAO")
                if not modo_debug:
                    return 1
    else:
        _info("[debug] Pulando Digitacao.")

    if not args.so_ocr and not args.so_digitacao:
        etapa_filtro(pasta_pdfs, pasta_saida)
    else:
        _info("[debug] Pulando Filtro.")

    _p()
    _sep("-")
    if falhas:
        _fail(f"PIPELINE {PIPELINE_NOME} COM FALHAS: {', '.join(falhas)}")
        _sep("-")
        return 1
    _ok(f"PIPELINE {PIPELINE_NOME} CONCLUÍDO COM SUCESSO")
    _sep("-")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
