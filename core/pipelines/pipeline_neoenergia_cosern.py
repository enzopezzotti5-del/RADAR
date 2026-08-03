#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pipeline Neoenergia COSERN (Rio Grande do Norte): OCR -> Digitacao -> Filtro.

Uso:
    python pipeline_neoenergia_cosern.py
    python pipeline_neoenergia_cosern.py --mes 03 --ano 2026
    python pipeline_neoenergia_cosern.py --so-ocr
    python pipeline_neoenergia_cosern.py --so-digitacao
    python pipeline_neoenergia_cosern.py --so-filtro
    python pipeline_neoenergia_cosern.py --pasta "//servidor/DOWNLOAD NEOENERGIA/RIO_GRANDE_DO_NORTE/2026-05"
    python pipeline_neoenergia_cosern.py --carimbo BB_2001037
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import os
import re
import shutil
import sys
from pathlib import Path

try:
    from core.pipelines._visual import _p, _info, _ok, _fail, _warn, _sep, _banner, _rodar as _rodar_visual
except ModuleNotFoundError:
    from _visual import _p, _info, _ok, _fail, _warn, _sep, _banner, _rodar as _rodar_visual

LOCAL_DIR = Path(__file__).resolve().parent.parent
SERVIDOR  = Path("//10.10.250.21/Energia")

OCR_SCRIPT       = LOCAL_DIR / "ocr"              / "ocr_neoenergia.py"
DIGITACAO_SCRIPT = LOCAL_DIR / "digitacao_consen" / "digitacao_consen_enel.py"
FILTRO_SCRIPT    = LOCAL_DIR / "digitacao_consen" / "neoenergia_filtro.py"

DOWNLOAD_ROOT  = SERVIDOR / "ARQUIVOS ENZO" / "DOWNLOAD NEOENERGIA"
OCR_SAIDA_DIR  = SERVIDOR / "ARQUIVOS ENZO" / "OCR NEOENERGIA" / "RIO_GRANDE_DO_NORTE"
PIPELINE_SAIDA = SERVIDOR / "ARQUIVOS ENZO" / "NEOENERGIA_COSERN_pipeline_saida"
DIGITADAS_DIR  = Path("//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO/Digitadas")
JA_EXISTIAM_DIR= Path("//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO/Ja_existiam_no_Consen")
PIPELINE_NOME  = "NEOENERGIA COSERN"

CONSEN_LOGIN_URL   = "https://consen.acaoengenharia.com.br/login.php"
CONSEN_TARGET_HASH = "#bpg/gestao/fatura/cadastroTabFatura.php"
CONSEN_TARGET_URL  = f"{CONSEN_LOGIN_URL.rsplit('/', 1)[0]}/index.php{CONSEN_TARGET_HASH}"
CONSEN_LINK_HREF   = "bpg/gestao/fatura/cadastroTabFatura.php"
CONSEN_LINK_TEXTO  = "Instalacao"
CONSEN_USUARIO     = "Robo Digitador"
CONSEN_SENHA       = "Acao2026"

PYTHON_EXE = str(Path(sys.executable).parent / "python.exe")


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

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
    return DOWNLOAD_ROOT / "RIO_GRANDE_DO_NORTE" / f"{ano}-{mes}"


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
        f"{_path_key(pasta)}|{'|'.join(sorted(carimbos))}".encode("utf-8")
    ).hexdigest()[:8]
    return f"{base}_{assinatura}"


def _is_resgate(pasta: Path, mes: str, ano: str, carimbos: list[str]) -> bool:
    if any(c.strip() for c in carimbos):
        return True
    return _path_key(pasta) != _path_key(_pasta_default(mes, ano))


def _xlsx_ocr_generico(mes: str, ano: str, tipo: str) -> Path:
    return SERVIDOR / "ARQUIVOS ENZO" / "OCR NEOENERGIA" / f"ocr_neoenergia_{tipo.upper()}_{mes}{ano}.xlsx"


def _xlsx_saida(mes: str, ano: str, tipo: str) -> Path:
    return OCR_SAIDA_DIR / f"ocr_neoenergia_cosern_{tipo.upper()}_{mes}{ano}.xlsx"


def _xlsx_resgate(tipo: str, slug: str) -> Path:
    return OCR_SAIDA_DIR / "_resgates" / f"ocr_neoenergia_cosern_{tipo.upper()}_{slug}.xlsx"


def _pipeline_saida_tipo(tipo: str) -> Path:
    return PIPELINE_SAIDA / tipo.upper()


def _pipeline_saida_resgate(tipo: str, slug: str) -> Path:
    return PIPELINE_SAIDA / "_resgates" / tipo.upper() / slug


def _materializar_xlsx_cosern(mes: str, ano: str, tipo: str, destino_override: Path | None = None) -> Path:
    origem  = _xlsx_ocr_generico(mes, ano, tipo)
    destino = destino_override or _xlsx_saida(mes, ano, tipo)
    if not origem.exists():
        return destino
    _mkdir_seguro(destino.parent)
    if origem.resolve() == destino.resolve():
        return destino
    try:
        shutil.copy2(origem, destino)
        return destino
    except Exception as exc:
        _warn(f"Falha ao copiar XLSX {tipo} para pasta da COSERN: {exc}")
        return origem


def _ler_resumo_auditoria(pasta_saida: Path) -> dict[str, int]:
    auditoria = pasta_saida / "auditoria_resultados.csv"
    resumo = {"total": 0, "sucesso": 0, "moviveis": 0, "pendentes": 0}
    if not auditoria.exists():
        return resumo
    status_moviveis = {"sucesso_auditoria", "auditoria_sem_valor"}
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


def _publicar_auditoria_sessao(
    saidas: dict[str, Path],
    auditoria_saida: Path | None,
    carimbos: list[str],
) -> bool:
    """Publica a auditoria da sessão em caminho explícito para o wrapper.

    Para resgates BT/MT, a auditoria fica em diretório próprio do pipeline. O
    wrapper só deve consumir um arquivo associado aos carimbos da sessão, nunca
    um CSV escolhido por recência.
    """
    if auditoria_saida is None:
        return True

    auditorias = [pasta / "auditoria_resultados.csv" for pasta in saidas.values()]
    existentes = [p for p in auditorias if p.exists()]
    if not existentes:
        _warn("Nenhum auditoria_resultados.csv encontrado para publicar")
        return False

    esperados = {c.replace("BB_", "").strip() for c in carimbos if c.strip()}
    candidatas: list[Path] = []
    for auditoria in existentes:
        texto = auditoria.read_text(encoding="utf-8-sig", errors="replace")
        if not esperados or all(c in texto for c in esperados):
            candidatas.append(auditoria)

    if len(candidatas) != 1:
        _warn(
            "Auditoria da sessao ambigua ou ausente para publicacao: "
            f"{[str(p) for p in candidatas]}"
        )
        return False

    auditoria_saida.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidatas[0], auditoria_saida)
    _info(f"Auditoria da sessao publicada em {auditoria_saida}")
    return True


# ---------------------------------------------------------------------------
# ETAPAS
# ---------------------------------------------------------------------------

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
    cmd = [PYTHON_EXE, str(OCR_SCRIPT), "--mes", str(int(mes)), "--ano", str(int(ano)), "--tipo", tipo]
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
        if c.strip():
            cmd.extend(["--carimbo", c.strip()])
    return _rodar_visual(f"OCR {PIPELINE_NOME} {mes}/{ano}", cmd)


def etapa_digitacao(xlsx: Path, tipo: str, pasta_saida: Path) -> int:
    if not DIGITACAO_SCRIPT.exists():
        _fail(f"Script de digitacao nao encontrado: {DIGITACAO_SCRIPT}")
        return 1
    if not xlsx.exists():
        _fail(f"Planilha de OCR nao encontrada: {xlsx}")
        return 1
    pasta_saida.mkdir(parents=True, exist_ok=True)
    env_extra = {
        "ENEL_EXCEL_PATH":               str(xlsx),
        "CONSEN_PIPELINE_SAIDA":         str(pasta_saida),
        "CONSEN_INTERATIVO_FECHAR":      "0",
        "CONSEN_INVESTIGAR_ZEROS":       "0",
        "DIGITACAO_FATOR_VELOCIDADE":    "0.25",
        "CONSEN_PERMITIR_LOTE_COMPLETO": "1",
        "CONSEN_LOGIN_URL":              CONSEN_LOGIN_URL,
        "CONSEN_TARGET_HASH":            CONSEN_TARGET_HASH,
        "CONSEN_TARGET_URL":             CONSEN_TARGET_URL,
        "CONSEN_LINK_HREF":              CONSEN_LINK_HREF,
        "CONSEN_LINK_TEXTO":             CONSEN_LINK_TEXTO,
        "CONSEN_USUARIO":                CONSEN_USUARIO,
        "CONSEN_SENHA":                  CONSEN_SENHA,
    }
    return _rodar_visual(
        f"DIGITACAO {PIPELINE_NOME} {tipo.upper()} ({xlsx.name})",
        [PYTHON_EXE, str(DIGITACAO_SCRIPT)],
        env_extra=env_extra,
    )


def _atualizar_master_pos_filtro(auditoria_csv: Path) -> None:
    try:
        sys.path.insert(0, str(LOCAL_DIR))
        from indice_master import MasterIndice, marcar_digitados_do_auditoria
        contadores = marcar_digitados_do_auditoria(auditoria_csv, MasterIndice())
        _info(f"[MASTER] Digitacao atualizada: {contadores}")
    except Exception as e:
        _warn(f"[MASTER] Nao foi possivel atualizar o indice master: {e}")


def etapa_filtro(tipo: str, root_pdfs: Path, pasta_saida: Path) -> int:
    if not FILTRO_SCRIPT.exists():
        _fail(f"Script de filtro nao encontrado: {FILTRO_SCRIPT}")
        return 1
    auditoria = pasta_saida / "auditoria_resultados.csv"
    if not auditoria.exists():
        _warn(f"auditoria_resultados.csv nao encontrado em {pasta_saida} - filtro pulado")
        return 0
    env_extra = {
        "NEO_FILTRO_CSV":               str(auditoria),
        "NEO_FILTRO_ROOT":              str(root_pdfs),
        "NEO_FILTRO_DESTINO":           str(DIGITADAS_DIR),
        "NEO_FILTRO_DESTINO_EXISTENTES":str(JA_EXISTIAM_DIR),
    }
    codigo = _rodar_visual(
        f"FILTRO {PIPELINE_NOME} {tipo.upper()}",
        [PYTHON_EXE, str(FILTRO_SCRIPT)],
        env_extra=env_extra,
    )
    _atualizar_master_pos_filtro(auditoria)
    return codigo


# ---------------------------------------------------------------------------
# CLI / MAIN
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    hoje = dt.date.today()
    p = argparse.ArgumentParser(description=f"Pipeline {PIPELINE_NOME}: OCR -> Digitacao -> Filtro")
    p.add_argument("--mes",          type=str, default=f"{hoje.month:02d}")
    p.add_argument("--ano",          type=str, default=str(hoje.year))
    p.add_argument("--tipo",         choices=["bt", "mt", "ambos"], default="ambos")
    p.add_argument("--pasta",        type=str, default="",
                   help="Pasta mensal da COSERN com PDFs (substitui o default)")
    p.add_argument("--carimbo",      action="append", default=[],
                   help="Carimbo(s) específico(s) para OCR (pode repetir)")
    p.add_argument("--so-ocr",       action="store_true")
    p.add_argument("--so-digitacao", action="store_true")
    p.add_argument("--so-filtro",    action="store_true")
    p.add_argument("--auditoria-saida", type=str, default="")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    mes      = f"{int(args.mes):02d}"
    ano      = str(int(args.ano))
    tipos    = ["BT", "MT"] if args.tipo == "ambos" else [args.tipo.upper()]
    pasta_pdfs = Path(str(args.pasta).strip()) if str(args.pasta).strip() else _pasta_default(mes, ano)
    carimbos   = [str(c).strip() for c in (args.carimbo or []) if str(c).strip()]
    resgate    = _is_resgate(pasta_pdfs, mes, ano, carimbos)
    slug       = _slug_resgate(pasta_pdfs, carimbos) if resgate else ""

    xlsxs = {
        tipo: (_xlsx_resgate(tipo, slug) if resgate else _xlsx_saida(mes, ano, tipo))
        for tipo in tipos
    }
    saidas = {
        tipo: (_pipeline_saida_resgate(tipo, slug) if resgate else _pipeline_saida_tipo(tipo))
        for tipo in tipos
    }
    auditoria_saida = Path(args.auditoria_saida) if str(args.auditoria_saida).strip() else None
    modo_debug = args.so_ocr or args.so_digitacao or args.so_filtro

    _mkdir_seguro(OCR_SAIDA_DIR)
    _mkdir_seguro(PIPELINE_SAIDA)

    detalhes = [
        f"Referencia : {mes}/{ano}",
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
            mes, ano,
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
                xlsx = _materializar_xlsx_cosern(mes, ano, tipo, destino_override=xlsx)
                xlsxs[tipo] = xlsx
            if not xlsx.exists():
                _warn(f"Planilha {tipo} nao encontrada apos OCR, digitacao pulada: {xlsx}")
                continue
            algum_xlsx = True
            _resetar_auditoria(saidas[tipo])
            cod = etapa_digitacao(xlsx, tipo, saidas[tipo])
            resumo = _ler_resumo_auditoria(saidas[tipo])
            if resumo["total"] > 0:
                _info(
                    f"Auditoria {tipo}: total={resumo['total']} "
                    f"sucesso={resumo['sucesso']} moviveis={resumo['moviveis']} "
                    f"pendentes={resumo['pendentes']}"
                )
            if cod != 0:
                if resumo["moviveis"] > 0:
                    _warn(
                        f"Digitacao {tipo} terminou com exit {cod}, mas ha "
                        f"{resumo['moviveis']} conta(s) aptas ao filtro. Continuando."
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

    # 3) Filtro
    if not args.so_ocr and not args.so_digitacao:
        for tipo in tipos:
            etapa_filtro(tipo, pasta_pdfs, saidas[tipo])
    else:
        _info("[debug] Pulando Filtro.")

    if not args.so_ocr:
        if not _publicar_auditoria_sessao(saidas, auditoria_saida, carimbos):
            falhas_criticas.append("AUDITORIA_SESSAO")

    _p()
    _sep("═")
    if falhas_criticas:
        _fail(f"PIPELINE {PIPELINE_NOME} COM FALHAS: {', '.join(falhas_criticas)}")
        _sep("═")
        return 1
    _ok(f"PIPELINE {PIPELINE_NOME} CONCLUIDO COM SUCESSO")
    _sep("═")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
