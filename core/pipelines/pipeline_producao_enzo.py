#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pipeline de producao da pasta ENZO para COELBA e CELESC.

Fluxo:
1. Varre os PDFs da raiz da pasta ENZO.
2. Identifica concessionaria, UC, referencia e grupo tarifario.
3. Reaproveita BB_ ja presente no nome; quando faltar, consome novo carimbo.
4. Copia para staging por concessionaria/tensao.
5. Dispara os pipelines existentes:
   - COELBA BT/MT -> pipeline_neoenergia_bahia.py
   - CELESC BT/MT -> pipeline_celesc.py
6. Remove os originais da ENZO para os carimbos ja digitados/movidos.

Os pipelines de destino continuam sendo a fonte oficial de OCR, digitacao,
auditoria e atualizacao de status no master.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unicodedata
from pathlib import Path

import pdfplumber

CORE_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = CORE_DIR.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from indice_master import MASTER_FIELDS, MasterIndice


SERVIDOR = Path("//10.10.250.21/Energia")
PASTA_ENZO = SERVIDOR / "CONTASDEENERGIAELETRICA" / "BB" / "ENZO"
DIGITADAS_DIR = PASTA_ENZO / "Digitadas"

PIPELINE_COELBA      = CORE_DIR / "pipelines" / "pipeline_neoenergia_bahia.py"
PIPELINE_CELESC      = CORE_DIR / "pipelines" / "pipeline_celesc.py"
PIPELINE_CEMIG       = CORE_DIR / "pipelines" / "pipeline_cemig.py"
PIPELINE_COPEL_BT    = CORE_DIR / "pipelines" / "pipeline_copel_bt.py"
PIPELINE_COPEL_MT    = CORE_DIR / "pipelines" / "pipeline_copel_mt.py"
PIPELINE_ENEL        = CORE_DIR / "pipelines" / "pipeline_enel.py"
PIPELINE_CELPE       = CORE_DIR / "pipelines" / "pipeline_neoenergia_pernambuco.py"
PIPELINE_ELEKTRO     = CORE_DIR / "pipelines" / "pipeline_neoenergia_elektro.py"
PIPELINE_COSERN      = CORE_DIR / "pipelines" / "pipeline_neoenergia_cosern.py"
PIPELINE_EQUATORIAL  = CORE_DIR / "pipelines" / "pipeline_equatorial_go.py"
PIPELINE_EQUATORIAL_MA_BT = CORE_DIR / "pipelines" / "pipeline_equatorial_ma_bt.py"
PIPELINE_EQUATORIAL_MA_MT = CORE_DIR / "pipelines" / "pipeline_equatorial_ma_mt.py"
PIPELINE_EQUATORIAL_PA_BT = CORE_DIR / "pipelines" / "pipeline_equatorial_pa_bt.py"
PIPELINE_EQUATORIAL_PA_MT = CORE_DIR / "pipelines" / "pipeline_equatorial_pa_mt.py"
PIPELINE_EQUATORIAL_PI_BT = CORE_DIR / "pipelines" / "pipeline_equatorial_pi_bt.py"
PIPELINE_EQUATORIAL_PI_MT = CORE_DIR / "pipelines" / "pipeline_equatorial_pi_mt.py"
PIPELINE_EQUATORIAL_AL_BT = CORE_DIR / "pipelines" / "pipeline_equatorial_al_bt.py"
PIPELINE_EQUATORIAL_AP_BT = CORE_DIR / "pipelines" / "pipeline_equatorial_ap_bt.py"
PIPELINE_EDP_SP_BT   = CORE_DIR / "pipelines" / "pipeline_edp_sp_bt.py"
PIPELINE_EDP_ES_BT   = CORE_DIR / "pipelines" / "pipeline_edp_es_bt.py"
PIPELINE_ENERGISA_BT = CORE_DIR / "pipelines" / "pipeline_energisa_bt.py"
PIPELINE_RGE_SUL_BT  = CORE_DIR / "pipelines" / "pipeline_rge_sul_bt.py"
RGE_SAIDA_ROOT       = SERVIDOR / "ARQUIVOS ENZO" / "RGE_pipeline_saida" / "BT"
STAGING_SERVER       = SERVIDOR / "ARQUIVOS ENZO" / "tmp_pipeline_staging"
OCR_CHESP           = CORE_DIR / "ocr" / "ocr_chesp.py"
OCR_CEEE_BT         = CORE_DIR / "ocr" / "ocr_ceee_bt.py"
OCR_CEMIG           = CORE_DIR / "ocr" / "OCR_Cemig.py"
OCR_CPFL_BT         = CORE_DIR / "ocr" / "ocr_cpfl_bt.py"
OCR_LIGHT_RJ_BT     = CORE_DIR / "ocr" / "ocr_light_rj_bt.py"
DIGITACAO_SCRIPT    = CORE_DIR / "digitacao_consen" / "digitacao_consen_enel.py"
FILTRO_NEO          = CORE_DIR / "digitacao_consen" / "neoenergia_filtro.py"

COELBA_SAIDA_ROOT    = SERVIDOR / "ARQUIVOS ENZO" / "NEOENERGIA_BAHIA_pipeline_saida" / "_resgates"
CELESC_SAIDA_ROOT    = SERVIDOR / "ARQUIVOS ENZO" / "CELESC_pipeline_saida"
CHESP_SAIDA_ROOT     = SERVIDOR / "ARQUIVOS ENZO" / "CHESP_pipeline_saida" / "_resgates"
CEEE_SAIDA_ROOT      = SERVIDOR / "ARQUIVOS ENZO" / "OCR CEEE"
CEMIG_SAIDA_ROOT     = SERVIDOR / "ARQUIVOS ENZO" / "OCR CEMIG"
CPFL_SAIDA_ROOT      = SERVIDOR / "ARQUIVOS ENZO" / "OCR CPFL"

# Pastas de download para pipelines sem suporte a --pasta
CEMIG_DOWNLOAD_DIR   = SERVIDOR / "ARQUIVOS ENZO" / "DOWNLOAD CEMIG"
ENEL_DOWNLOAD_DIR    = SERVIDOR / "ARQUIVOS ENZO" / "DOWNLOAD ENEL"
EQUATORIAL_DOWNLOAD_DIR = SERVIDOR / "ARQUIVOS ENZO" / "DOWNLOAD EQUATORIAL"

PYTHON_EXE = str(Path(sys.executable).parent / "python.exe")
STATUS_MOVIVEIS = {
    "sucesso_auditoria",
    "auditoria_sem_valor",
    "pulado_carimbo_existente",
    "pulado_referencia_existente",
}
SLUG_FILE = CORE_DIR / "pipelines" / ".producao_enzo_slug"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("pipeline_producao_enzo")

try:
    from pipelines._session_runtime import build_session_command
except ModuleNotFoundError:  # pragma: no cover - fallback para execucoes diretas
    from _session_runtime import build_session_command  # type: ignore[no-redef]


def _mkdir_seguro(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def _to_ascii_upper(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(ch)
    ).upper()


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _normalizar_carimbo(valor: str) -> str:
    txt = str(valor or "").strip().upper()
    if not txt:
        return ""
    if txt.endswith(".0"):
        txt = txt[:-2]
    if txt.startswith("BB_"):
        return txt
    if txt.isdigit():
        return f"BB_{txt}"
    return txt


def _rodar(descricao: str, cmd: list[str], env_extra: dict[str, str] | None = None) -> int:
    cmd = build_session_command(cmd)
    log.info("=" * 64)
    log.info("  %s", descricao)
    log.info("=" * 64)
    log.info("  Comando: %s", " ".join(str(c) for c in cmd))

    env = os.environ.copy()
    env.update({
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
    })
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

    def _drenar(stream, prefixo: str) -> None:
        for linha in iter(stream.readline, ""):
            linha = linha.rstrip()
            if linha:
                linha_segura = linha.encode("ascii", "replace").decode("ascii")
                log.info("  [%s] %s", prefixo, linha_segura)

    t_out = threading.Thread(target=_drenar, args=(proc.stdout, "OUT"), daemon=True)
    t_err = threading.Thread(target=_drenar, args=(proc.stderr, "ERR"), daemon=True)
    t_out.start()
    t_err.start()

    try:
        proc.wait()
    finally:
        t_out.join(timeout=5)
        t_err.join(timeout=5)

    code = int(proc.returncode or 0)
    log.info("%s  %s -> exit %d", "OK" if code == 0 else "FALHA", descricao, code)
    return code


def _ler_primeira_pagina(pdf_path: Path) -> str:
    with pdfplumber.open(pdf_path) as pdf:
        if not pdf.pages:
            return ""
        return pdf.pages[0].extract_text(x_tolerance=1, y_tolerance=1) or ""


def _estado_por_sistema(sistema: str) -> str:
    return {
        "COELBA":      "BAHIA",
        "CELESC":      "SANTA CATARINA",
        "CEMIG":       "MINAS GERAIS",
        "COPEL":       "PARANA",
        "ENEL CE":     "CEARA",
        "ENEL SP":     "SAO PAULO",
        "CELPE":       "PERNAMBUCO",
        "ELEKTRO":     "SAO PAULO",
        "COSERN":      "RIO GRANDE DO NORTE",
        "EQUATORIAL":  "GOIAS",
        "EQUATORIAL MA": "MARANHAO",
        "EQUATORIAL PA": "PARA",
        "EQUATORIAL PI": "PIAUI",
        "EQUATORIAL AL": "ALAGOAS",
        "EQUATORIAL AP": "AMAPA",
        "CHESP":       "GOIAS",
    }.get(str(sistema or "").strip().upper(), "")


def _sistema_hint_por_caminho(pdf_path: Path) -> str:
    partes = {_to_ascii_upper(part) for part in pdf_path.parts}
    if "NEOENERGIA" in partes and "ELEKTRO" in partes:
        return "ELEKTRO"
    if "EQUATORIAL" in partes:
        if "MARANHAO" in partes:
            return "EQUATORIAL MA"
        if "PARA" in partes:
            return "EQUATORIAL PA"
        if "PIAUI" in partes:
            return "EQUATORIAL PI"
        if "ALAGOAS" in partes:
            return "EQUATORIAL AL"
        if "AMAPA" in partes:
            return "EQUATORIAL AP"
        return "EQUATORIAL"
    return ""


def _mes_ref_por_nome_arquivo(nome: str) -> str:
    stem = Path(nome).stem
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{2})$", stem)
    if m:
        mes_venc = int(m.group(2))
        ano_venc = 2000 + int(m.group(3))
        mes_ref = mes_venc - 1 if mes_venc > 1 else 12
        ano_ref = ano_venc if mes_venc > 1 else ano_venc - 1
        return f"{mes_ref:02d}-{ano_ref}"
    m = re.search(r"(\d{2})\.(\d{2})$", stem)
    if m:
        mes_venc = int(m.group(2))
        ano_base = dt.date.today().year
        mes_ref = mes_venc - 1 if mes_venc > 1 else 12
        ano_ref = ano_base if mes_venc > 1 else ano_base - 1
        return f"{mes_ref:02d}-{ano_ref}"
    return ""


def _mes_ref_generico(texto: str, nome_arquivo: str = "") -> str:
    txt = _to_ascii_upper(texto)
    meses_pt = {
        "JAN": "01", "JANEIRO": "01",
        "FEV": "02", "FEVEREIRO": "02",
        "MAR": "03", "MARCO": "03",
        "ABR": "04", "ABRIL": "04",
        "MAI": "05", "MAIO": "05",
        "JUN": "06", "JUNHO": "06",
        "JUL": "07", "JULHO": "07",
        "AGO": "08", "AGOSTO": "08",
        "SET": "09", "SETEMBRO": "09",
        "OUT": "10", "OUTUBRO": "10",
        "NOV": "11", "NOVEMBRO": "11",
        "DEZ": "12", "DEZEMBRO": "12",
    }
    m = re.search(r"\b(" + "|".join(sorted(meses_pt, key=len, reverse=True)) + r")/(\d{4})\b", txt)
    if m:
        return f"{meses_pt[m.group(1)]}-{m.group(2)}"
    m = re.search(r"\b(0[1-9]|1[0-2])/(\d{4})\b", txt)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return _mes_ref_por_nome_arquivo(nome_arquivo)


def _grupo_generico(texto: str) -> str:
    """Detecta grupo A (MT) ou B (BT) por padrões comuns em faturas brasileiras."""
    if re.search(r"\bSUBGRUPO\s+A\d\b|\bGRUPO\s*:\s*A\b|TENSAO\s+MEDIA|MEDIA\s+TENSAO|\bMT\b", texto):
        return "A"
    if re.search(r"\bSUBGRUPO\s+B\d\b|\bGRUPO\s*:\s*B\b|BAIXA\s+TENSAO|TENSAO\s+BAIXA|\bBT\b", texto):
        return "B"
    # Fallback: A indica MT, demais BT
    if re.search(r"\b(A[1-4]|AS)\b", texto):
        return "A"
    return "B"


def _grupo_por_coelba(texto: str) -> str:
    if re.search(r"CLASSIFICACAO\s*:\s*A\d", texto):
        return "A"
    if re.search(r"CLASSIFICACAO\s*:\s*B\d", texto):
        return "B"
    if "LIVRE - VERDE" in texto or "LIVRE - AZUL" in texto:
        return "A"
    if "CONV. MONOMIA" in texto or "CONVENCIONAL" in texto:
        return "B"
    return ""


def _grupo_por_celesc(texto: str) -> str:
    m = re.search(r"GRUPO/SUBGRUPO TENSAO\s*:\s*([AB])\s*/\s*([AB]\d)", texto)
    if m:
        return m.group(1)
    if re.search(r"\bA\d\b", texto) and ("HORARIA VERDE" in texto or "HORARIA AZUL" in texto):
        return "A"
    if re.search(r"\bB\d\b", texto) or "CONSUMO TUSD" in texto:
        return "B"
    return ""


def _identificar_pdf(pdf_path: Path) -> dict[str, str]:
    info = {
        "sistema": "DESCONHECIDA",
        "instalacao": "",
        "mes_ref": "",
        "grupo": "",
    }
    sistema_hint = _sistema_hint_por_caminho(pdf_path)
    try:
        texto = _to_ascii_upper(_ler_primeira_pagina(pdf_path))
    except Exception as exc:
        log.warning("  Falha ao ler %s: %s", pdf_path.name, exc)
        return info

    if "COMPANHIA DE ELETRICIDADE DO ESTADO DA BAHIA" in texto or "NEOENERGIA.COM" in texto:
        info["sistema"] = "COELBA"
        info["grupo"] = _grupo_por_coelba(texto)

        m_stem = re.match(r"^(\d+)", pdf_path.stem)
        if m_stem:
            info["instalacao"] = m_stem.group(1)
        if not info["instalacao"]:
            for pattern in (
                r"CODIGO DA INSTALACAO.*?\b(\d+)\s+NOTA FISCAL",
                r"CODIGO DA INSTALACAO\s+(\d+)",
                r"\b(\d+)\s+NOTA FISCAL N",
            ):
                m_uc = re.search(pattern, texto, flags=re.S)
                if m_uc:
                    info["instalacao"] = m_uc.group(1)
                    break

        m_ref = re.search(r"REF:MES/ANO.*?(\d{2})/(\d{4})", texto, flags=re.S)
        if m_ref:
            info["mes_ref"] = f"{m_ref.group(1)}-{m_ref.group(2)}"

    elif "CELESC" in texto or "CENTRAIS ELETRICAS DE SANTA CATARINA" in texto:
        info["sistema"] = "CELESC"
        info["grupo"] = _grupo_por_celesc(texto)

        m_stem = re.match(r"^(\d+)", pdf_path.stem)
        if m_stem:
            info["instalacao"] = m_stem.group(1)
        if not info["instalacao"]:
            for pattern in (
                r"UNIDADE CONSUMIDORA\s+(\d+)",
                r"CLIENTE:\s*(\d+)",
                r"\b(\d{8,})\s+CPF/CNPJ",
                r"CODIGO PARA CADASTRO.*?\b(\d{8,})\b",
            ):
                m_uc = re.search(pattern, texto, flags=re.S)
                if m_uc:
                    info["instalacao"] = m_uc.group(1)
                    break

        m_ref = re.search(r"\b(\d{2})/(\d{4})\s+\d{2}/\d{2}/\d{4}\s+R\$", texto)
        if m_ref:
            info["mes_ref"] = f"{m_ref.group(1)}-{m_ref.group(2)}"

    # -- CEMIG ----------------------------------------------------------------
    elif "CEMIG" in texto or "COMPANHIA ENERGETICA DE MINAS GERAIS" in texto:
        info["sistema"] = "CEMIG"
        info["grupo"] = _grupo_generico(texto)
        for pat in (
            r"INSTALACAO\s*[:\-]?\s*([\d.]+[\d-]+)",
            r"NUMERO DA INSTALACAO\s*[:\-]?\s*([\d.]+[\d-]+)",
            r"COD\.?\s*INSTALACAO\s*[:\-]?\s*([\d.]+[\d-]+)",
            r"\b(\d+(?:\.\d+)+-\d+)\b",
        ):
            m = re.search(pat, texto)
            if m:
                info["instalacao"] = m.group(1); break
        # fallback: stem do arquivo  cobre UCs no formato 11.006.325.018-23
        if not info["instalacao"]:
            m_stem = re.match(r"^([\d.]+[\d-]+)", pdf_path.stem)
            if m_stem:
                info["instalacao"] = m_stem.group(1)
        meses_pt = {"JAN":"01","FEV":"02","MAR":"03","ABR":"04","MAI":"05","JUN":"06",
                    "JUL":"07","AGO":"08","SET":"09","OUT":"10","NOV":"11","DEZ":"12"}
        m = re.search(r"(?:REFERENCIA|REFERENTE\s+A)\s*[:\-]?\s*([A-Z]{3})/(\d{4})", texto)
        if not m:
            m = re.search(r"\b([A-Z]{3})/(20\d{2})\b", texto)
        if m and m.group(1) in meses_pt:
            mm = meses_pt[m.group(1)]
            info["mes_ref"] = f"{mm}-{m.group(2)}"
        else:
            for mm_m in re.finditer(r"\b(0[1-9]|1[0-2])/(20\d{2})\b", texto):
                info["mes_ref"] = f"{mm_m.group(1)}-{mm_m.group(2)}"; break

    # -- COPEL ----------------------------------------------------------------
    elif "COPEL" in texto or "COMPANHIA PARANAENSE DE ENERGIA" in texto:
        info["sistema"] = "COPEL"
        info["grupo"] = _grupo_generico(texto)
        for pat in (
            r"UNIDADE CONSUMIDORA\s*[:\-]?\s*(\d+)",
            r"INSTALACAO\s*[:\-]?\s*(\d{6,})",
            r"COD\.?\s*UC\s*[:\-]?\s*(\d+)",
        ):
            m = re.search(pat, texto)
            if m:
                info["instalacao"] = m.group(1); break
        m = re.search(r"(\d{2})/(\d{4})", texto)
        if m:
            info["mes_ref"] = f"{m.group(1)}-{m.group(2)}"

    # -- ENEL CE --------------------------------------------------------------
    elif "COMPANHIA ENERGETICA DO CEARA" in texto or "COELCE" in texto:
        info["sistema"] = "ENEL CE"
        info["grupo"] = _grupo_generico(texto)
        for pat in (
            r"UNIDADE CONSUMIDORA\s*[:\-]?\s*(\d{6,10})",
            r"INSTALACAO\s*[:\-]?\s*(\d{6,10})",
        ):
            m = re.search(pat, texto)
            if m:
                info["instalacao"] = m.group(1)
                break
        m = re.search(r"(\d{2})/(\d{4})", texto)
        if m:
            info["mes_ref"] = f"{m.group(1)}-{m.group(2)}"

    # -- ENEL SP --------------------------------------------------------------
    elif "ELETROPAULO" in texto or ("ENEL" in texto and ("SAO PAULO" in texto or "ELETROPAULO" in texto)):
        info["sistema"] = "ENEL SP"
        info["grupo"] = _grupo_generico(texto)
        for pat in (
            r"INSTALACAO\s*[:\-]?\s*(\d{7,})",
            r"UNIDADE CONSUMIDORA\s*[:\-]?\s*(\d+)",
        ):
            m = re.search(pat, texto)
            if m:
                info["instalacao"] = m.group(1); break
        m = re.search(r"REFERENCIA\s*[:\-]?\s*(\d{2})/(\d{4})", texto)
        if m:
            info["mes_ref"] = f"{m.group(1)}-{m.group(2)}"
        else:
            m = re.search(r"(\d{2})/(\d{4})", texto)
            if m:
                info["mes_ref"] = f"{m.group(1)}-{m.group(2)}"

    # -- CELPE ----------------------------------------------------------------
    elif "CELPE" in texto or "NEOENERGIA PERNAMBUCO" in texto or "COMPANHIA ENERGETICA DE PERNAMBUCO" in texto:
        info["sistema"] = "CELPE"
        info["grupo"] = _grupo_generico(texto)
        for pat in (
            r"CODIGO DA INSTALACAO\s*[:\-]?\s*(\d+)",
            r"INSTALACAO\s*[:\-]?\s*(\d{6,})",
        ):
            m = re.search(pat, texto)
            if m:
                info["instalacao"] = m.group(1); break
        m = re.search(r"REF:MES/ANO.*?(\d{2})/(\d{4})", texto, flags=re.S)
        if m:
            info["mes_ref"] = f"{m.group(1)}-{m.group(2)}"

    # -- ELEKTRO --------------------------------------------------------------
    elif "ELEKTRO" in texto or "NEOENERGIA ELEKTRO" in texto or sistema_hint == "ELEKTRO":
        info["sistema"] = "ELEKTRO"
        try:
            from core.ocr import ocr_neoenergia as _ocr_neo
            tipo, _tarifa, _subgrupo, _det = _ocr_neo._detectar_tipo_por_pdf(pdf_path, texto)
            info["grupo"] = "A" if tipo == "mt" else "B"
            info["instalacao"] = _ocr_neo._extract_instalacao(texto, [])
        except Exception:
            info["grupo"] = _grupo_generico(texto)
        if not info["instalacao"]:
            for pat in (
                r"CODIGO DA INSTALACAO\s*[:\-]?\s*(\d+)",
                r"INSTALACAO\s*[:\-]?\s*(\d{6,})",
            ):
                m = re.search(pat, texto)
                if m:
                    info["instalacao"] = m.group(1)
                    break
        if not info["instalacao"]:
            m = re.match(r"^(\d[\d.\-]{5,})", pdf_path.stem)
            if m:
                info["instalacao"] = re.sub(r"\D", "", m.group(1))
        info["mes_ref"] = _mes_ref_generico(texto, pdf_path.name)

    # -- COSERN ---------------------------------------------------------------
    elif "COSERN" in texto or "NEOENERGIA RIO GRANDE DO NORTE" in texto:
        info["sistema"] = "COSERN"
        info["grupo"] = _grupo_generico(texto)
        for pat in (
            r"CODIGO DA INSTALACAO\s*[:\-]?\s*(\d+)",
            r"INSTALACAO\s*[:\-]?\s*(\d{6,})",
        ):
            m = re.search(pat, texto)
            if m:
                info["instalacao"] = m.group(1); break
        m = re.search(r"REF:MES/ANO.*?(\d{2})/(\d{4})", texto, flags=re.S)
        if m:
            info["mes_ref"] = f"{m.group(1)}-{m.group(2)}"

    # -- EQUATORIAL (GO + outros estados via CNPJ na chave NFe) ---------------
    elif ("EQUATORIAL GOIAS" in texto or "EQUATORIAL GO" in texto
          or ("CELG" in texto and "GOIAS" in texto)
          or "06840748000189" in re.sub(r"\D", "", texto)   # PI
          or "05965546000109" in re.sub(r"\D", "", texto)   # AP
          or "05914650000166" in re.sub(r"\D", "", texto)   # RO
          or "03467321000199" in re.sub(r"\D", "", texto)   # MT
          or "25086034000171" in re.sub(r"\D", "", texto)   # TO
          or "09198515000197" in re.sub(r"\D", "", texto)   # PA
          or "04895728000180" in re.sub(r"\D", "", texto)   # PA (CNPJ alternativo)
          or "06272793000184" in re.sub(r"\D", "", texto)   # MA (CEMAR antigo)
          or "12272084000100" in re.sub(r"\D", "", texto)   # AL
          or "06844891000108" in re.sub(r"\D", "", texto)   # MA
    ):
        _digits_texto = re.sub(r"\D", "", texto)
        if "06840748000189" in _digits_texto:
            info["sistema"] = "EQUATORIAL PI"
        elif "05965546000109" in _digits_texto:
            info["sistema"] = "EQUATORIAL AP"
        elif "09198515000197" in _digits_texto or "04895728000180" in _digits_texto:
            info["sistema"] = "EQUATORIAL PA"
        elif "06844891000108" in _digits_texto or "06272793000184" in _digits_texto:
            info["sistema"] = "EQUATORIAL MA"
        elif "12272084000100" in _digits_texto:
            info["sistema"] = "EQUATORIAL AL"
        else:
            info["sistema"] = "EQUATORIAL"
        try:
            from core.ocr import ocr_equatorial_go as _ocr_eq
            tipo_eq = _ocr_eq._detectar_tipo_equatorial_go(texto)
            info["grupo"] = "A" if tipo_eq == "mt" else "B"
            info["instalacao"] = _ocr_eq._resolver_instalacao_equatorial_go(pdf_path, texto, tipo_eq)
            info["mes_ref"] = _ocr_eq._eq_mes_ref_texto(texto)
        except Exception:
            info["grupo"] = _grupo_generico(texto)
        if not info["instalacao"]:
            for pat in (
                r"INSTALACAO\s*[:\-]?\s*(\d{6,})",
                r"UNIDADE CONSUMIDORA\s*[:\-]?\s*(\d+)",
                r"CONTRATO\s*[:\-]?\s*(\d+)",
            ):
                m = re.search(pat, texto)
                if m:
                    info["instalacao"] = m.group(1)
                    break
        if not info["mes_ref"]:
            info["mes_ref"] = _mes_ref_generico(texto, pdf_path.name)

    # -- CHESP ------------------------------------------------------------------
    elif "COMPANHIA HIDROELETRICA SAO PATRICIO" in texto or "CHESP" in texto:
        info["sistema"] = "CHESP"
        info["grupo"] = "A"  # CHESP é concessionária MT exclusivamente
        for pat in (
            r"UNIDADE CONSUMIDORA\s+(\d{6,12})",
            r"\b(\d{6,12})\s+NOTA FISCAL N",
        ):
            m = re.search(pat, texto, flags=re.S)
            if m:
                info["instalacao"] = m.group(1)
                break
        m = re.search(r"(?:^|\s)(\d{2})/(\d{4})\s+\d{2}/\d{2}/\d{4}\s+R\$", texto)
        if m:
            info["mes_ref"] = f"{m.group(1)}-{m.group(2)}"

    # -- RGE SUL (antes de CPFL  RGE faz parte do grupo CPFL e pode ter CPFL no rodapé) --
    elif ("RGE SUL DISTRIBUIDORA" in texto or "02016440" in re.sub(r"\D", "", texto)):
        info["sistema"] = "RGE SUL"
        info["grupo"]   = "B"
        m = re.search(r"\b(\d{10})\s+\d{2}/\d{2}/\d{4}", texto)
        if m:
            info["instalacao"] = m.group(1)
        for pat in (r"\b(0[1-9]|1[0-2])/(20\d{2})\b",):
            m = re.search(pat, texto)
            if m: info["mes_ref"] = f"{m.group(1)}-{m.group(2)}"; break

    # -- CPFL -------------------------------------------------------------------
    elif ("COMPANHIA PAULISTA DE FORCA E LUZ" in texto
          or "33050196000188" in re.sub(r"\D", "", texto)
          or "WWW.CPFL.COM.BR" in texto
          or "CPFL.COM.BR" in texto):
        info["sistema"] = "CPFL"
        info["grupo"]   = "B"
        m = re.search(r"\b(\d{5,10})\s+\d{2}/\d{2}/\d{4}\s+\d{2}/\d{2}/\d{4}\s+\d{1,3}\b", texto)
        if m:
            info["instalacao"] = m.group(1)
        if not info["instalacao"]:
            m_cpfl = re.search(r"cpfl\.com\.br\s+(\d{5,10})\b", texto, re.I)
            if m_cpfl:
                info["instalacao"] = m_cpfl.group(1)
        if not info["instalacao"]:
            m_stem = re.match(r"^(\d+)", pdf_path.stem)
            if m_stem:
                info["instalacao"] = m_stem.group(1)
        meses_pt = {"JAN":"01","FEV":"02","MAR":"03","ABR":"04","MAI":"05","JUN":"06",
                    "JUL":"07","AGO":"08","SET":"09","OUT":"10","NOV":"11","DEZ":"12"}
        m = re.search(r"\b([A-Z]{3})/(20\d{2})\b", texto)
        if m and m.group(1) in meses_pt:
            info["mes_ref"] = f"{meses_pt[m.group(1)]}-{m.group(2)}"
        if not info["mes_ref"]:
            for mm in re.finditer(r"\b(0[1-9]|1[0-2])/(20\d{2})\b", texto):
                info["mes_ref"] = f"{mm.group(1)}-{mm.group(2)}"; break

    # -- EDP ES -----------------------------------------------------------------
    elif ("28152650000171" in re.sub(r"\D", "", texto) or "EDP ESPIRITO SANTO" in texto or "ESCELSA" in texto):
        info["sistema"] = "EDP ES"
        info["grupo"] = "B"
        for pat in (r"INSTALACAO\s*[:\-]?\s*(\d{6,})", r"UNIDADE CONSUMIDORA\s*[:\-]?\s*(\d+)"):
            m = re.search(pat, texto)
            if m: info["instalacao"] = m.group(1); break
        if not info["instalacao"]:
            m_stem = re.match(r"^BB_(\d+)", pdf_path.stem, re.I) or re.match(r"^(\d+)", pdf_path.stem)
            if m_stem: info["instalacao"] = m_stem.group(1)
        for pat in (r"(?:REFERENCIA|COMPETENCIA)\s*[:\-]?\s*(\d{2})/(\d{4})", r"\b(0[1-9]|1[0-2])/(20\d{2})\b"):
            m = re.search(pat, texto)
            if m: info["mes_ref"] = f"{m.group(1)}-{m.group(2)}"; break

    # -- EDP SP -----------------------------------------------------------------
    elif ("02302100000106" in re.sub(r"\D", "", texto) or "EDP SAO PAULO" in texto):
        info["sistema"] = "EDP SP"
        info["grupo"] = "B"
        for pat in (r"INSTALACAO\s*[:\-]?\s*(\d{6,})", r"UNIDADE CONSUMIDORA\s*[:\-]?\s*(\d+)"):
            m = re.search(pat, texto)
            if m: info["instalacao"] = m.group(1); break
        if not info["instalacao"]:
            m_stem = re.match(r"^BB_(\d+)", pdf_path.stem, re.I) or re.match(r"^(\d+)", pdf_path.stem)
            if m_stem: info["instalacao"] = m_stem.group(1)
        for pat in (r"(?:REFERENCIA|COMPETENCIA)\s*[:\-]?\s*(\d{2})/(\d{4})", r"\b(0[1-9]|1[0-2])/(20\d{2})\b"):
            m = re.search(pat, texto)
            if m: info["mes_ref"] = f"{m.group(1)}-{m.group(2)}"; break

    # -- ENERGISA ---------------------------------------------------------------
    elif "ENERGISA" in texto:
        info["sistema"] = "ENERGISA"
        info["grupo"] = "B"
        try:
            from core.ocr.ocr_energisa_bt import identificacao_rapida as _id_e
            r2 = _id_e(pdf_path)
            info["instalacao"] = str(r2.get("instalacao", ""))
            info["mes_ref"]    = str(r2.get("mes_ref", ""))
            info["grupo"]      = str(r2.get("grupo", "B"))
        except Exception:
            for pat in (r"INSTALACAO\s*[:\-]?\s*(\d{6,})", r"\b(\d{7,10})\b"):
                m = re.search(pat, texto)
                if m: info["instalacao"] = m.group(1); break
            for pat in (r"\b(0[1-9]|1[0-2])/(20\d{2})\b",):
                m = re.search(pat, texto)
                if m: info["mes_ref"] = f"{m.group(1)}-{m.group(2)}"; break

    # -- LIGHT RJ ---------------------------------------------------------------
    # CNPJ 60.444.437 = Light Serviços de Eletricidade S/A (aparece na chave NF-e)
    elif ("LIGHT SERVICOS DE ELETRICIDADE" in texto or "LIGHT ENERGIA" in texto
          or "60444437" in re.sub(r"\D", "", texto)):
        info["sistema"] = "LIGHT RJ"
        info["grupo"] = "B"
        for pat in (r"INSTALACAO\s*[:\-]?\s*(\d{6,})", r"NUMERO DA INSTALACAO\s*[:\-]?\s*(\d+)"):
            m = re.search(pat, texto)
            if m: info["instalacao"] = m.group(1); break
        for pat in (r"REFERENTE A\s*[:\-]?\s*(\d{2})/(\d{4})", r"\b(0[1-9]|1[0-2])/(20\d{2})\b"):
            m = re.search(pat, texto)
            if m: info["mes_ref"] = f"{m.group(1)}-{m.group(2)}"; break

    # -- CEEE -------------------------------------------------------------------
    elif ("COMPANHIA ESTADUAL DE DISTRIBUICAO DE ENERGIA ELETRICA" in texto
          or "08467115000100" in re.sub(r"\D", "", texto)):
        info["sistema"] = "CEEE"
        info["grupo"] = "B"  # CEEE BT exclusivamente neste fluxo
        # Instalacao: tenta texto da fatura, fallback para inicio do stem
        for pat in (
            r"INSTALACAO:\s*(\d+)",
            r"INSTALACAO\s+(\d+)",
            r"NUMERO\s+DA\s+INSTALACAO\s*[:\-]?\s*(\d+)",
            r"CODIGOCLIENTE\s*[:\-]?\s*(\d+)",
        ):
            m = re.search(pat, texto, flags=re.I)
            if m:
                info["instalacao"] = m.group(1)
                break
        if not info["instalacao"]:
            m_stem = re.match(r"^(\d+)", pdf_path.stem)
            if m_stem:
                info["instalacao"] = m_stem.group(1)
        m = re.search(r"(\d{2})/(\d{4})\s+\d{2}[./]\d{2}[./]\d{4}\s+R\$", texto)
        if not m:
            # fallback: instalacao seguida de MM/AAAA no boleto
            m = re.search(r"\b" + re.escape(info.get("instalacao", "X")) + r"\s+(\d{2})/(20\d{2})\b", texto)
        if not m:
            # fallback generico: primeiro MM/20YY valido (mes 01-12)
            for mm in re.finditer(r"\b(0[1-9]|1[0-2])/(20\d{2})\b", texto):
                m = mm; break
        if m:
            info["mes_ref"] = f"{m.group(1)}-{m.group(2)}"

    return info


def _ler_master_rows(master_file: Path) -> list[dict[str, str]]:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(master_file, newline="", encoding=enc) as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    return []


def _buscar_carimbo_existente(master: MasterIndice, uc: str, mes_ref: str, sistema: str) -> str:
    try:
        for row in _ler_master_rows(master.master_file):
            if (
                str(row.get("SISTEMA", "")).strip().upper() == sistema.upper()
                and str(row.get("UC", "")).strip().lstrip("0") == uc.strip().lstrip("0")
                and str(row.get("MES_REF", "")).strip() == mes_ref.strip()
            ):
                carimbo = _normalizar_carimbo(row.get("INDICE", ""))
                if carimbo.startswith("BB_"):
                    return carimbo
    except Exception:
        pass
    return ""


def _carimbo_existe_no_master(master: MasterIndice, carimbo: str) -> bool:
    carimbo_norm = _normalizar_carimbo(carimbo)
    if not carimbo_norm.startswith("BB_"):
        return False
    try:
        for row in _ler_master_rows(master.master_file):
            if _normalizar_carimbo(row.get("INDICE", "")) == carimbo_norm:
                return True
    except Exception:
        pass
    return False


def _atualizar_arquivo_master(master: MasterIndice, carimbo: str, arquivo: Path) -> None:
    def _fazer():
        rows = _ler_master_rows(master.master_file)
        if not rows:
            return
        encontrado = False
        for row in rows:
            if _normalizar_carimbo(row.get("INDICE", "")) == carimbo:
                row["ARQUIVO"] = str(arquivo)
                encontrado = True
        if not encontrado:
            return
        tmp = master.master_file.with_suffix(".tmp")
        with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=MASTER_FIELDS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        tmp.replace(master.master_file)

    try:
        with master._filelock:
            _fazer()
    except Exception:
        _fazer()


def _registrar_com_carimbo(master: MasterIndice, carimbo: str, sistema: str, uc: str, mes_ref: str, arquivo: Path) -> None:
    master.registrar(
        indice_bb=carimbo,
        sistema=sistema,
        uc=uc,
        mes_ref=mes_ref,
        arquivo=str(arquivo),
        estado=_estado_por_sistema(sistema),
        concessionaria=sistema,
    )


def _carimbar_pdf_na_origem(pdf: Path, carimbo: str, master: MasterIndice) -> Path:
    destino = pdf.with_name(f"{carimbo}.pdf")
    if pdf == destino:
        _atualizar_arquivo_master(master, carimbo, destino)
        return destino
    if destino.exists():
        _atualizar_arquivo_master(master, carimbo, destino)
        return destino
    import time as _time
    import gc as _gc
    _gc.collect()
    for tentativa in range(4):
        try:
            pdf.rename(destino)
            break
        except PermissionError:
            if tentativa < 3:
                _time.sleep(1.2)
                continue
            # Ultimo recurso: copia para o novo nome; tenta apagar original
            shutil.copy2(str(pdf), str(destino))
            try:
                pdf.unlink()
            except PermissionError:
                log.warning(
                    "  Original nao removido (travado por outro processo)  "
                    "apague manualmente: %s", pdf.name
                )
            break
    log.info("  Carimbado na origem: %s -> %s", pdf.name, destino.name)
    _atualizar_arquivo_master(master, carimbo, destino)
    return destino


def etapa_carimbos(pdfs: list[Path], master: MasterIndice, cache_info: dict[Path, dict[str, str]]) -> dict[Path, str]:
    log.info("=" * 64)
    log.info("  ETAPA 1 - Carimbos e registro no master")
    log.info("=" * 64)

    mapa: dict[Path, str] = {}
    novos = reaproveitados = ignorados = 0

    for pdf in pdfs:
        info = cache_info[pdf]
        sistema = info["sistema"]
        uc = info["instalacao"]
        mes_ref = info["mes_ref"]

        if sistema == "DESCONHECIDA" or not uc or not mes_ref or info["grupo"] not in {"A", "B"}:
            log.warning("  IGNORADO: %s | info=%s", pdf.name, info)
            ignorados += 1
            continue

        carimbo_no_nome = _normalizar_carimbo(pdf.stem)
        if carimbo_no_nome.startswith("BB_"):
            if not _carimbo_existe_no_master(master, carimbo_no_nome):
                existente = _buscar_carimbo_existente(master, uc, mes_ref, sistema)
                if existente and existente != carimbo_no_nome:
                    log.warning(
                        "  Conflito de carimbo em %s: nome=%s master=%s. Mantendo nome existente e pulando registro.",
                        pdf.name, carimbo_no_nome, existente
                    )
                else:
                    _registrar_com_carimbo(master, carimbo_no_nome, sistema, uc, mes_ref, pdf)
                    log.info("  CARIMBO REGISTRADO PELO NOME: %s | %s | UC=%s | %s", carimbo_no_nome, sistema, uc, mes_ref)
            pdf_final = _carimbar_pdf_na_origem(pdf, carimbo_no_nome, master)
            mapa[pdf_final] = carimbo_no_nome
            reaproveitados += 1
            continue

        carimbo_existente = _buscar_carimbo_existente(master, uc, mes_ref, sistema)
        if carimbo_existente:
            pdf_final = _carimbar_pdf_na_origem(pdf, carimbo_existente, master)
            mapa[pdf_final] = carimbo_existente
            log.info("  CARIMBO REAPROVEITADO: %s | %s | UC=%s | %s", carimbo_existente, sistema, uc, mes_ref)
            reaproveitados += 1
            continue

        carimbo_novo = master.consumir_carimbo()
        _registrar_com_carimbo(master, carimbo_novo, sistema, uc, mes_ref, pdf)
        pdf_final = _carimbar_pdf_na_origem(pdf, carimbo_novo, master)
        mapa[pdf_final] = carimbo_novo
        log.info("  NOVO: %s | %s | UC=%s | %s", carimbo_novo, sistema, uc, mes_ref)
        novos += 1

    log.info("  Novos          : %d", novos)
    log.info("  Reaproveitados : %d", reaproveitados)
    log.info("  Ignorados      : %d", ignorados)
    log.info("  Proximo BB_    : %s", master.proximo_carimbo)
    return mapa


def etapa_staging(
    mapa: dict[Path, str],
    staging_root: Path,
    cache_info: dict[Path, dict[str, str]],
) -> tuple[dict[tuple[str, str], Path], dict[str, Path], dict[tuple[str, str], list[str]]]:
    log.info("=" * 64)
    log.info("  ETAPA 2 - Staging por concessionaria e tensao")
    log.info("=" * 64)

    dirs: dict[tuple[str, str], Path] = {}
    lotes: dict[tuple[str, str], list[str]] = {}
    mapa_reverso: dict[str, Path] = {}

    for pdf, carimbo in mapa.items():
        info = cache_info[pdf]
        sistema = info["sistema"]
        tensao = "MT" if info["grupo"] == "A" else "BT"
        chave = (sistema, tensao)
        pasta = staging_root / sistema / tensao
        if chave not in dirs:
            dirs[chave] = pasta
            lotes[chave] = []
            _mkdir_seguro(pasta)

        destino = pasta / f"{carimbo}.pdf"
        if not destino.exists():
            shutil.copy2(pdf, destino)
        lotes[chave].append(carimbo)
        mapa_reverso[carimbo] = pdf
        log.info("  Staging: %s -> %s/%s", pdf.name, sistema, tensao)

    for chave, carimbos in sorted(lotes.items()):
        log.info("  Lote %s/%s: %d PDF(s)", chave[0], chave[1], len(carimbos))

    return dirs, mapa_reverso, lotes


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


def _saida_pipeline_coelba(tipo: str, pasta: Path) -> Path:
    slug = _slug_resgate(pasta, [])
    return COELBA_SAIDA_ROOT / tipo.upper() / slug


def _saida_pipeline_elektro(tipo: str, pasta: Path) -> Path:
    slug = _slug_resgate(pasta, [])
    return SERVIDOR / "ARQUIVOS ENZO" / "NEOENERGIA_ELEKTRO_pipeline_saida" / "_resgates" / tipo.upper() / slug


def _saida_pipeline_celesc(tipo: str, pasta: Path) -> Path:
    slug = _slug_resgate(pasta, [])
    return CELESC_SAIDA_ROOT / tipo.upper() / "_resgates" / slug


def _saida_pipeline_chesp(tipo: str, pasta: Path) -> Path:
    slug = _slug_resgate(pasta, [])
    return CHESP_SAIDA_ROOT / tipo.upper() / slug


def _ler_carimbos_moviveis(auditoria: Path) -> set[str]:
    carimbos: set[str] = set()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(auditoria, newline="", encoding=enc) as f:
                reader = csv.DictReader(f, delimiter=";")
                for row in reader:
                    status = str(row.get("status", "")).strip().lower()
                    if status in STATUS_MOVIVEIS:
                        carimbo = _normalizar_carimbo(row.get("carimbo", ""))
                        if carimbo:
                            carimbos.add(carimbo)
            return carimbos
        except UnicodeDecodeError:
            continue
        except Exception as exc:
            log.warning("  Falha ao ler auditoria %s: %s", auditoria, exc)
            return carimbos
    return carimbos


def _remover_originais(carimbos: set[str], mapa_reverso: dict[str, Path]) -> tuple[int, int]:
    apagados = faltantes = 0
    for carimbo in sorted(carimbos):
        original = mapa_reverso.get(carimbo)
        if original and original.exists():
            try:
                original.unlink()
                log.info("  Original removido da ENZO: %s", original.name)
                apagados += 1
            except Exception as exc:
                log.warning("  Falha ao remover %s: %s", original.name, exc)
        else:
            faltantes += 1
    return apagados, faltantes


def _copiar_para_download(pasta_staged: Path, download_dir: Path) -> None:
    """Copia PDFs do staging para pasta de download fixa (pipelines sem --pasta)."""
    _mkdir_seguro(download_dir)
    for pdf in pasta_staged.glob("*.pdf"):
        destino = download_dir / pdf.name
        if not destino.exists():
            shutil.copy2(pdf, destino)
            log.info("  Copiado para download: %s -> %s", pdf.name, download_dir)


def _executar_digitacao_generica(xlsx: Path, pasta_saida: Path, pasta_pdfs: Path, sistema: str = "") -> int:
    if not xlsx.exists():
        log.warning("  XLSX nao encontrado, digitacao pulada: %s", xlsx)
        return 0
    if not DIGITACAO_SCRIPT.exists():
        log.error("Script de digitacao nao encontrado: %s", DIGITACAO_SCRIPT)
        return 1

    _mkdir_seguro(pasta_saida)
    env_extra = {
        "ENEL_EXCEL_PATH": str(xlsx),
        "CONSEN_PIPELINE_SAIDA": str(pasta_saida),
        "ENEL_DIGITACAO_PASTA_PDFS": str(pasta_pdfs),
        "CONSEN_INTERATIVO_FECHAR": "0",
        "CONSEN_INVESTIGAR_ZEROS": "0",
        "DIGITACAO_FATOR_VELOCIDADE":    "0.25",
    }
    label = sistema.upper() if sistema else (xlsx.stem.split("_")[1].upper() if "_" in xlsx.stem else "GENERICO")
    return _rodar(f"DIGITACAO {label} ({xlsx.name})", [PYTHON_EXE, str(DIGITACAO_SCRIPT)], env_extra=env_extra)


def _executar_filtro_generico(auditoria: Path, pasta_pdfs: Path, sistema: str = "") -> int:
    if not auditoria.exists():
        log.warning("  auditoria_resultados.csv nao encontrado para filtro: %s", auditoria)
        return 0
    if not FILTRO_NEO.exists():
        log.error("Script de filtro nao encontrado: %s", FILTRO_NEO)
        return 1

    env_extra = {
        "NEO_FILTRO_CSV": str(auditoria),
        "NEO_FILTRO_ROOT": str(pasta_pdfs),
        "NEO_FILTRO_DESTINO": str(DIGITADAS_DIR),
        "NEO_FILTRO_DESTINO_EXISTENTES": str(PASTA_ENZO / "Ja_existiam_no_Consen"),
    }
    label = sistema.upper() if sistema else "GENERICO"
    return _rodar(f"FILTRO {label}", [PYTHON_EXE, str(FILTRO_NEO)], env_extra=env_extra)


def _executar_lote(sistema: str, tipo: str, pasta: Path) -> tuple[int, Path]:
    hoje = dt.date.today()
    mes = f"{hoje.month:02d}"
    ano = str(hoje.year)
    env_extra = {"PYTHONPATH": str(ROOT_DIR)}
    saida_generica = pasta / "_saida"

    if sistema == "COELBA":
        saida = _saida_pipeline_coelba(tipo, pasta)
        cmd = [PYTHON_EXE, str(PIPELINE_COELBA),
               "--mes", mes, "--ano", ano, "--tipo", tipo.lower(), "--pasta", str(pasta)]
        return _rodar(f"COELBA {tipo}", cmd, env_extra=env_extra), saida

    if sistema == "CELESC":
        saida = _saida_pipeline_celesc(tipo, pasta)
        cmd = [PYTHON_EXE, str(PIPELINE_CELESC),
               "--mes", mes, "--ano", ano, "--pasta", str(pasta),
               "--so-bt" if tipo == "BT" else "--so-mt"]
        return _rodar(f"CELESC {tipo}", cmd, env_extra=env_extra), saida

    if sistema == "COPEL":
        script = PIPELINE_COPEL_BT if tipo == "BT" else PIPELINE_COPEL_MT
        cmd = [PYTHON_EXE, str(script), "--mes", mes, "--ano", ano, "--pasta", str(pasta)]
        return _rodar(f"COPEL {tipo}", cmd, env_extra=env_extra), saida_generica

    if sistema == "CELPE":
        cmd = [PYTHON_EXE, str(PIPELINE_CELPE),
               "--mes", mes, "--ano", ano, "--tipo", tipo.lower(), "--pasta", str(pasta)]
        return _rodar(f"CELPE {tipo}", cmd, env_extra=env_extra), saida_generica

    if sistema == "ELEKTRO":
        saida = _saida_pipeline_elektro(tipo, pasta)
        cmd = [PYTHON_EXE, str(PIPELINE_ELEKTRO),
               "--mes", mes, "--ano", ano, "--tipo", tipo.lower(), "--pasta", str(pasta)]
        return _rodar(f"ELEKTRO {tipo}", cmd, env_extra=env_extra), saida

    if sistema == "COSERN":
        cmd = [PYTHON_EXE, str(PIPELINE_COSERN),
               "--mes", mes, "--ano", ano, "--tipo", tipo.lower(), "--pasta", str(pasta)]
        return _rodar(f"COSERN {tipo}", cmd, env_extra=env_extra), saida_generica

    if sistema == "CEMIG":
        saida = CEMIG_SAIDA_ROOT
        _mkdir_seguro(saida)
        if not OCR_CEMIG.exists():
            log.error("Script OCR CEMIG nao encontrado: %s", OCR_CEMIG)
            return 1, saida

        xlsx = saida / f"ocr_cemig_{tipo}_{mes}{ano}.xlsx"
        cmd_ocr = [
            PYTHON_EXE, str(OCR_CEMIG),
            "--pasta-direta", str(pasta),
            "--saida", str(xlsx),
            "--tipo", tipo.lower(),
        ]
        cod_ocr = _rodar(f"CEMIG {tipo} OCR", cmd_ocr, env_extra=env_extra)
        if cod_ocr != 0:
            return cod_ocr, saida

        cod_dig = _executar_digitacao_generica(xlsx, saida, pasta, sistema="CEMIG")
        if cod_dig != 0:
            return cod_dig, saida

        auditoria = saida / "auditoria_resultados.csv"
        cod_filtro = _executar_filtro_generico(auditoria, pasta, sistema="CEMIG")
        return cod_filtro, saida

    if sistema == "ENEL SP":
        _copiar_para_download(pasta, ENEL_DOWNLOAD_DIR)
        cmd = [PYTHON_EXE, str(PIPELINE_ENEL),
               "--mes", mes, "--ano", ano, "--tipo", tipo.lower()]
        return _rodar(f"ENEL SP {tipo}", cmd, env_extra=env_extra), saida_generica

    if sistema == "ENEL CE":
        cmd = [
            PYTHON_EXE,
            str(PIPELINE_ENEL),
            "--mes",
            mes,
            "--ano",
            ano,
            "--tipo",
            tipo.lower(),
            "--pasta",
            str(pasta),
        ]
        return _rodar(f"ENEL CE {tipo}", cmd, env_extra=env_extra), saida_generica

    if sistema == "EQUATORIAL":
        _copiar_para_download(pasta, EQUATORIAL_DOWNLOAD_DIR)
        cmd = [PYTHON_EXE, str(PIPELINE_EQUATORIAL), "--mes", mes, "--ano", ano]
        return _rodar(f"EQUATORIAL {tipo}", cmd, env_extra=env_extra), saida_generica

    if sistema == "EQUATORIAL MA":
        pipeline_eq_ma = PIPELINE_EQUATORIAL_MA_MT if tipo == "MT" else PIPELINE_EQUATORIAL_MA_BT
        cmd = [PYTHON_EXE, str(pipeline_eq_ma), "--mes", mes, "--ano", ano, "--pasta", str(pasta)]
        return _rodar(f"EQUATORIAL MA {tipo}", cmd, env_extra=env_extra), saida_generica

    if sistema == "EQUATORIAL PA":
        pipeline_eq_pa = PIPELINE_EQUATORIAL_PA_MT if tipo == "MT" else PIPELINE_EQUATORIAL_PA_BT
        cmd = [PYTHON_EXE, str(pipeline_eq_pa), "--mes", mes, "--ano", ano, "--pasta", str(pasta)]
        return _rodar(f"EQUATORIAL PA {tipo}", cmd, env_extra=env_extra), saida_generica

    if sistema == "EQUATORIAL PI":
        pipeline_eq_pi = PIPELINE_EQUATORIAL_PI_MT if tipo == "MT" else PIPELINE_EQUATORIAL_PI_BT
        cmd = [PYTHON_EXE, str(pipeline_eq_pi), "--mes", mes, "--ano", ano, "--pasta", str(pasta)]
        return _rodar(f"EQUATORIAL PI {tipo}", cmd, env_extra=env_extra), saida_generica

    if sistema == "EQUATORIAL AL":
        cmd = [PYTHON_EXE, str(PIPELINE_EQUATORIAL_AL_BT), "--mes", mes, "--ano", ano, "--pasta", str(pasta)]
        return _rodar(f"EQUATORIAL AL {tipo}", cmd, env_extra=env_extra), saida_generica

    if sistema == "EQUATORIAL AP":
        cmd = [PYTHON_EXE, str(PIPELINE_EQUATORIAL_AP_BT), "--mes", mes, "--ano", ano, "--pasta", str(pasta)]
        return _rodar(f"EQUATORIAL AP {tipo}", cmd, env_extra=env_extra), saida_generica

    if sistema == "CHESP":
        saida = _saida_pipeline_chesp(tipo, pasta)
        _mkdir_seguro(saida)
        if tipo != "MT":
            log.warning("  CHESP %s ainda nao possui OCR/digitacao homologados neste fluxo.", tipo)
            return 1, saida
        if not OCR_CHESP.exists():
            log.error("Script OCR CHESP nao encontrado: %s", OCR_CHESP)
            return 1, saida

        xlsx = saida / f"ocr_chesp_{tipo}_{_slug_resgate(pasta, [])}.xlsx"
        cmd_ocr = [
            PYTHON_EXE,
            str(OCR_CHESP),
            "--mes", mes,
            "--ano", ano,
            "--pasta", str(pasta),
            "--saida", str(xlsx),
        ]
        cod_ocr = _rodar(f"CHESP {tipo} OCR", cmd_ocr, env_extra=env_extra)
        if cod_ocr != 0:
            return cod_ocr, saida

        cod_dig = _executar_digitacao_generica(xlsx, saida, pasta, sistema="CHESP")
        if cod_dig != 0:
            return cod_dig, saida

        auditoria = saida / "auditoria_resultados.csv"
        cod_filtro = _executar_filtro_generico(auditoria, pasta, sistema="CHESP")
        return cod_filtro, saida

    if sistema == "CEEE":
        saida = saida_generica
        _mkdir_seguro(saida)
        if tipo != "BT":
            log.warning("  CEEE %s ainda nao possui OCR/digitacao homologados neste fluxo.", tipo)
            return 1, saida
        if not OCR_CEEE_BT.exists():
            log.error("Script OCR CEEE BT nao encontrado: %s", OCR_CEEE_BT)
            return 1, saida

        xlsx = saida / f"ocr_ceee_BT_{mes}{ano}.xlsx"
        cmd_ocr = [
            PYTHON_EXE,
            str(OCR_CEEE_BT),
            "--mes", mes,
            "--ano", ano,
            "--pasta", str(pasta),
            "--saida", str(xlsx),
        ]
        cod_ocr = _rodar("CEEE BT OCR", cmd_ocr, env_extra=env_extra)
        if cod_ocr != 0:
            return cod_ocr, saida

        cod_dig = _executar_digitacao_generica(xlsx, saida, pasta, sistema="CEEE")
        if cod_dig != 0:
            return cod_dig, saida

        auditoria = saida / "auditoria_resultados.csv"
        cod_filtro = _executar_filtro_generico(auditoria, pasta, sistema="CEEE")
        return cod_filtro, saida

    if sistema == "CPFL":
        saida = CPFL_SAIDA_ROOT
        _mkdir_seguro(saida)
        if tipo != "BT":
            log.warning("  CPFL %s ainda nao possui OCR/digitacao homologados neste fluxo.", tipo)
            return 1, saida
        if not OCR_CPFL_BT.exists():
            log.error("Script OCR CPFL BT nao encontrado: %s", OCR_CPFL_BT)
            return 1, saida

        xlsx = saida / f"ocr_cpfl_BT_{mes}{ano}.xlsx"
        cmd_ocr = [
            PYTHON_EXE,
            str(OCR_CPFL_BT),
            "--mes", mes,
            "--ano", ano,
            "--pasta", str(pasta),
            "--saida", str(xlsx),
        ]
        cod_ocr = _rodar("CPFL BT OCR", cmd_ocr, env_extra=env_extra)
        if cod_ocr != 0:
            return cod_ocr, saida

        cod_dig = _executar_digitacao_generica(xlsx, saida, pasta, sistema="CPFL")
        if cod_dig != 0:
            return cod_dig, saida

        auditoria = saida / "auditoria_resultados.csv"
        cod_filtro = _executar_filtro_generico(auditoria, pasta, sistema="CPFL")
        return cod_filtro, saida

    if sistema == "EDP SP":
        cmd = [PYTHON_EXE, str(PIPELINE_EDP_SP_BT), "--mes", mes, "--ano", ano, "--pasta", str(pasta)]
        return _rodar("EDP SP BT", cmd, env_extra=env_extra), saida_generica

    if sistema == "EDP ES":
        cmd = [PYTHON_EXE, str(PIPELINE_EDP_ES_BT), "--mes", mes, "--ano", ano, "--pasta", str(pasta)]
        return _rodar("EDP ES BT", cmd, env_extra=env_extra), saida_generica

    if sistema == "ENERGISA":
        cmd = [PYTHON_EXE, str(PIPELINE_ENERGISA_BT), "--mes", mes, "--ano", ano, "--pasta", str(pasta)]
        return _rodar("ENERGISA BT", cmd, env_extra=env_extra), saida_generica

    if sistema == "RGE SUL":
        cmd = [PYTHON_EXE, str(PIPELINE_RGE_SUL_BT), "--mes", mes, "--ano", ano, "--pasta", str(pasta)]
        return _rodar("RGE SUL BT", cmd, env_extra=env_extra), saida_generica

    if sistema == "LIGHT RJ":
        saida = saida_generica
        _mkdir_seguro(saida)
        if tipo != "BT":
            log.warning("  LIGHT RJ %s ainda nao possui OCR/digitacao homologados neste fluxo.", tipo)
            return 1, saida
        if not OCR_LIGHT_RJ_BT.exists():
            log.error("Script OCR LIGHT RJ BT nao encontrado: %s", OCR_LIGHT_RJ_BT)
            return 1, saida

        xlsx = saida / f"ocr_light_rj_BT_{mes}{ano}.xlsx"
        cmd_ocr = [
            PYTHON_EXE,
            str(OCR_LIGHT_RJ_BT),
            "--mes", mes,
            "--ano", ano,
            "--pasta", str(pasta),
            "--saida", str(xlsx),
        ]
        cod_ocr = _rodar("LIGHT RJ BT OCR", cmd_ocr, env_extra=env_extra)
        if cod_ocr != 0:
            return cod_ocr, saida

        cod_dig = _executar_digitacao_generica(xlsx, saida, pasta, sistema="LIGHT RJ")
        if cod_dig != 0:
            return cod_dig, saida

        auditoria = saida / "auditoria_resultados.csv"
        cod_filtro = _executar_filtro_generico(auditoria, pasta, sistema="LIGHT RJ")
        return cod_filtro, saida

    log.warning("  Sistema desconhecido para dispatch: %s", sistema)
    return 1, saida_generica


def _carregar_ou_criar_slug() -> str:
    if SLUG_FILE.exists():
        slug = SLUG_FILE.read_text(encoding="utf-8").strip()
        if slug:
            return slug
    slug = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    SLUG_FILE.write_text(slug, encoding="utf-8")
    return slug


def _novo_slug() -> str:
    slug = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    SLUG_FILE.write_text(slug, encoding="utf-8")
    return slug


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pipeline de producao ENZO para COELBA e CELESC")
    parser.add_argument("--pasta", type=str, default=str(PASTA_ENZO), help="Pasta plana da producao")
    parser.add_argument("--so-carimbo", action="store_true", help="So identifica e carimba")
    parser.add_argument("--novo-slug", action="store_true", help="Forca novo staging")
    parser.add_argument("--slug", type=str, default="", help="Slug para retomar um staging existente")
    parser.add_argument("--manter-staging", action="store_true", help="Nao apaga o staging ao final")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pasta_pdfs = Path(args.pasta.strip())

    if args.slug.strip():
        slug = args.slug.strip()
        SLUG_FILE.write_text(slug, encoding="utf-8")
    else:
        slug = _novo_slug() if args.novo_slug or not args.so_carimbo else _carregar_ou_criar_slug()

    staging_root = STAGING_SERVER / f"producao_enzo_{slug}"
    log.info("=" * 64)
    log.info("  PIPELINE PRODUCAO ENZO")
    log.info("=" * 64)
    log.info("  Pasta PDFs : %s", pasta_pdfs)
    log.info("  Slug       : %s", slug)
    log.info("  Staging    : %s", staging_root)

    if not pasta_pdfs.exists():
        log.error("Pasta nao encontrada: %s", pasta_pdfs)
        return 1

    pdfs = sorted(p for p in pasta_pdfs.glob("*.pdf") if p.is_file())
    if not pdfs:
        log.warning("Nenhum PDF encontrado em %s", pasta_pdfs)
        return 0

    cache_info: dict[Path, dict[str, str]] = {}
    for pdf in pdfs:
        cache_info[pdf] = _identificar_pdf(pdf)

    log.info("  PDFs encontrados: %d", len(pdfs))
    SISTEMAS_SUPORTADOS = {
        "COELBA", "CELESC", "CEMIG", "COPEL",
        "ENEL CE", "ENEL SP", "CELPE", "ELEKTRO", "COSERN", "EQUATORIAL", "CHESP", "CEEE", "CPFL",
    }
    for sistema in sorted(SISTEMAS_SUPORTADOS) + ["DESCONHECIDA"]:
        subset = [p for p in pdfs if cache_info[p]["sistema"] == sistema]
        if subset:
            log.info("  %s: %d", sistema, len(subset))

    master = MasterIndice()
    mapa = etapa_carimbos(pdfs, master, cache_info)
    if args.so_carimbo:
        return 0
    if not mapa:
        log.warning("Nenhum PDF elegivel para producao.")
        return 0

    cache_carimbado: dict[Path, dict[str, str]] = {}
    for pdf_final in mapa:
        info = cache_info.get(pdf_final)
        if info is None:
            info = _identificar_pdf(pdf_final)
        cache_carimbado[pdf_final] = info

    dirs, mapa_reverso, lotes = etapa_staging(mapa, staging_root, cache_carimbado)

    falhas: list[str] = []
    total_apagados = total_faltantes = 0
    SISTEMAS_SUPORTADOS = {
        "COELBA", "CELESC", "CEMIG", "COPEL",
        "ENEL CE", "ENEL SP", "CELPE", "ELEKTRO", "COSERN", "EQUATORIAL", "CHESP", "CEEE", "CPFL",
        "EDP ES", "EDP SP", "ENERGISA", "RGE SUL", "LIGHT RJ",
    }
    for chave in sorted(dirs.keys()):
        if chave[0] not in SISTEMAS_SUPORTADOS:
            log.warning("  Lote ignorado (sistema sem dispatch): %s/%s", *chave)
            continue
        pasta_lote = dirs.get(chave)
        if not pasta_lote or not lotes.get(chave):
            continue

        code, saida = _executar_lote(chave[0], chave[1], pasta_lote)
        if code != 0:
            falhas.append(f"{chave[0]}_{chave[1]}")
            continue

        auditoria = saida / "auditoria_resultados.csv"
        if not auditoria.exists():
            log.warning("  Auditoria nao encontrada apos %s/%s: %s", chave[0], chave[1], auditoria)
            continue
        carimbos = _ler_carimbos_moviveis(auditoria)
        apagados, faltantes = _remover_originais(carimbos, mapa_reverso)
        total_apagados += apagados
        total_faltantes += faltantes

    log.info("  Originais removidos da ENZO: %d | faltantes: %d", total_apagados, total_faltantes)

    if staging_root.exists() and not args.manter_staging:
        shutil.rmtree(staging_root, ignore_errors=True)

    if falhas:
        log.error("Falhas em: %s", ", ".join(falhas))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
