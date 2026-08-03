#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pipeline_copel_bt.py
--------------------
Pipeline sequencial COPEL BT:
  1. OCR
  2. Digitacao
  3. Filtro
"""

from __future__ import annotations

import argparse
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

LOCAL_DIR = Path(__file__).parent.parent
ROOT_DIR = LOCAL_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

OCR_SCRIPT = LOCAL_DIR / "ocr" / "ocr_copel_bt.py"
DIGITACAO_SCRIPT = LOCAL_DIR / "digitacao_consen" / "digitacao_consen_enel.py"
FILTRO_SCRIPT = LOCAL_DIR / "digitacao_consen" / "copel_filtro.py"

SERVIDOR = Path("//10.10.250.21/Energia")
DOWNLOAD_DIR = SERVIDOR / "ARQUIVOS ENZO" / "DOWNLOAD COPEL"
OCR_SAIDA_DIR = SERVIDOR / "ARQUIVOS ENZO" / "OCR COPEL"
PIPELINE_SAIDA = SERVIDOR / "ARQUIVOS ENZO" / "COPEL_pipeline_saida"
DIGITADAS_DIR = SERVIDOR / "CONTASDEENERGIAELETRICA" / "BB" / "ENZO" / "Digitadas"
FALLBACK_ROOT = LOCAL_DIR / "_runtime_fallback" / "pipeline_copel_bt"

PYTHON_EXE = str(Path(sys.executable).parent / "python.exe")


def _mkdir_seguro(pasta: Path) -> None:
    try:
        pasta.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def _safe_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _resolver_dir(preferido: Path, fallback: Path, rotulo: str) -> Path:
    try:
        preferido.mkdir(parents=True, exist_ok=True)
        return preferido
    except OSError as e:
        fallback.mkdir(parents=True, exist_ok=True)
        _warn(f"{rotulo} indisponível em {preferido}: {e}. Usando fallback local {fallback}")
        return fallback


def _resetar_auditoria(pasta_saida: Path) -> None:
    arq = pasta_saida / "auditoria_resultados.csv"
    if _safe_exists(arq):
        arq.unlink()
        _info("[reset] auditoria_resultados.csv removido antes da digitacao.")


def _rodar(descricao: str, cmd: list[str], env: dict | None = None) -> int:
    return _rodar_visual(descricao, cmd, env_extra=env)


def _xlsx_bt(mes: str, ano: str) -> Path:
    return OCR_SAIDA_DIR / f"ocr_copel_BT_{mes}{ano}.xlsx"


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _slug_resgate(pasta: Path) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", pasta.name.strip() or "resgate").strip("_") or "resgate"
    assinatura = hashlib.md5(_path_key(pasta).encode("utf-8")).hexdigest()[:8]
    return f"{base}_{assinatura}"


def _is_resgate(pasta: Path, mes: str, ano: str) -> bool:
    return _path_key(pasta) != _path_key(_pasta_download_bt(mes, ano))


def _xlsx_resgate(slug: str) -> Path:
    return OCR_SAIDA_DIR / "_resgates" / f"ocr_copel_BT_{slug}.xlsx"


def _saida_resgate(slug: str) -> Path:
    return PIPELINE_SAIDA / "_resgates" / "BT" / slug


def _pasta_download_bt(mes: str, ano: str) -> Path:
    return DOWNLOAD_DIR / f"{mes}.{ano}" / "BT"


def etapa_ocr(mes: str, ano: str, pasta: str, xlsx_saida: Path) -> int:
    _mkdir_seguro(xlsx_saida.parent)
    cmd = [PYTHON_EXE, str(OCR_SCRIPT), "--mes", mes, "--ano", ano, "--saida", str(xlsx_saida)]
    if pasta.strip():
        cmd.extend(["--pasta", pasta.strip()])
    return _rodar(f"OCR COPEL BT {mes}/{ano}", cmd)


def etapa_digitacao(xlsx: Path, pasta_saida: Path, pasta_pdfs: Path) -> int:
    if not xlsx.exists():
        _fail(f"Planilha nao encontrada: {xlsx}")
        return 1
    _mkdir_seguro(pasta_saida)
    env = {
        "ENEL_EXCEL_PATH": str(xlsx),
        "CONSEN_PIPELINE_SAIDA": str(pasta_saida),
        "ENEL_DIGITACAO_PASTA_PDFS": str(pasta_pdfs),
        "CONSEN_INTERATIVO_FECHAR": "0",
        "DIGITACAO_FATOR_VELOCIDADE":    "0.25",
        "CONSEN_PERMITIR_LOTE_COMPLETO": "1",
        "CONSEN_USUARIO": "Robo Digitador",
        "CONSEN_SENHA":   "Acao2026",
    }
    candidatos_manifesto = [
        pasta_pdfs / "_sessao_manifesto.txt",
        pasta_pdfs.parent / "_sessao_manifesto.txt",
    ]
    manifesto = next((c for c in candidatos_manifesto if c.exists()), None)
    if manifesto is not None:
        env["ENEL_DIGITACAO_SOMENTE_NOMES"] = str(manifesto)
        _info(f"[escopo] Manifesto da sessao aplicado: {manifesto}")
    else:
        _warn(f"[escopo] Manifesto da sessao nao encontrado em {pasta_pdfs}; usando todos os PDFs da pasta.")

    return _rodar_visual(f"DIGITAÇÃO COPEL BT  ({xlsx.name})", [PYTHON_EXE, str(DIGITACAO_SCRIPT)], env_extra=env)


def _atualizar_master_pos_filtro(auditoria_csv: Path) -> None:
    try:
        import sys as _sys
        _sys.path.insert(0, str(LOCAL_DIR))
        from indice_master import MasterIndice, marcar_digitados_do_auditoria
        contadores = marcar_digitados_do_auditoria(auditoria_csv, MasterIndice())
        _info(f"[MASTER] Digitacao atualizada: {contadores}")
    except Exception as _e:
        _warn(f"[MASTER] Nao foi possivel atualizar o indice master: {_e}")


def etapa_filtro(pasta_pdfs: Path, pasta_saida: Path) -> int:
    auditoria_csv = pasta_saida / "auditoria_resultados.csv"
    if not auditoria_csv.exists():
        _warn(f"auditoria_resultados.csv nao encontrado em {pasta_saida} - filtro pulado")
        return 0

    env = {
        "COPEL_FILTRO_CSV": str(auditoria_csv),
        "COPEL_FILTRO_ROOT": str(pasta_pdfs),
        "COPEL_FILTRO_DESTINO": str(DIGITADAS_DIR),
    }
    codigo = _rodar("Filtro COPEL BT", [PYTHON_EXE, str(FILTRO_SCRIPT)], env=env)
    _atualizar_master_pos_filtro(auditoria_csv)
    return codigo


def parse_args():
    hoje = dt.date.today()
    p = argparse.ArgumentParser(description="Pipeline COPEL BT: OCR -> Digitacao -> Filtro")
    p.add_argument("--mes", type=str, default=f"{hoje.month:02d}")
    p.add_argument("--ano", type=str, default=str(hoje.year))
    p.add_argument("--pasta", type=str, default="")
    p.add_argument("--so-ocr", action="store_true")
    p.add_argument("--so-digitacao", action="store_true")
    p.add_argument("--so-filtro", action="store_true")
    return p.parse_args()


def main() -> int:
    global PIPELINE_SAIDA, DIGITADAS_DIR
    args = parse_args()
    mes, ano = args.mes, args.ano
    pasta_bt = Path(args.pasta) if args.pasta.strip() else _pasta_download_bt(mes, ano)
    resgate = _is_resgate(pasta_bt, mes, ano)
    slug = _slug_resgate(pasta_bt) if resgate else ""
    xlsx = _xlsx_resgate(slug) if resgate else _xlsx_bt(mes, ano)
    pasta_saida = _saida_resgate(slug) if resgate else PIPELINE_SAIDA
    modo_debug = args.so_ocr or args.so_digitacao or args.so_filtro

    PIPELINE_SAIDA = _resolver_dir(PIPELINE_SAIDA, FALLBACK_ROOT / "saida", "Saída do pipeline COPEL")
    DIGITADAS_DIR = _resolver_dir(DIGITADAS_DIR, FALLBACK_ROOT / "digitadas", "Pasta Digitadas COPEL")

    _banner("PIPELINE COPEL BT", [
        f"Referência : {mes}/{ano}",
        f"Pasta BT   : {pasta_bt}",
        f"Resgate    : {'sim' if resgate else 'nao'}",
        f"XLSX BT    : {xlsx}",
        f"Saída      : {pasta_saida}",
    ])

    falhas_criticas: list[str] = []

    if not args.so_digitacao and not args.so_filtro:
        if etapa_ocr(mes, ano, str(pasta_bt), xlsx) != 0:
            falhas_criticas.append("OCR")
            if not modo_debug:
                _fail("OCR falhou - abortando pipeline.")
                return 1
    else:
        _info("[debug] Pulando OCR.")

    if not args.so_ocr and not args.so_filtro:
        _resetar_auditoria(pasta_saida)
        if etapa_digitacao(xlsx, pasta_saida, pasta_bt) != 0:
            falhas_criticas.append("Digitacao")
            if not modo_debug:
                _fail("Digitacao falhou - filtro continuara disponivel apenas por debug.")
                return 1
    else:
        _info("[debug] Pulando digitacao.")

    if not args.so_ocr and not args.so_digitacao:
        etapa_filtro(pasta_bt, pasta_saida)
    else:
        _info("[debug] Pulando filtro.")

    _p()
    _sep("-")
    if falhas_criticas:
        _fail(f"PIPELINE COPEL BT COM FALHAS: {', '.join(falhas_criticas)}")
        _sep("-")
        return 1
    _ok("PIPELINE COPEL BT CONCLUÍDO COM SUCESSO")
    _sep("-")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
