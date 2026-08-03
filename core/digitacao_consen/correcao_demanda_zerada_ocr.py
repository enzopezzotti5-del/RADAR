#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Correcao de demanda zerada via OCR — extrai valores diretamente do PDF.

Fluxo:
  1. Le _demanda_zerada_robo.xlsx → carimbo, concessionaria, sistema, mes_ref, pdf_path
  2. Le xlsx de Erros de Digitacao → quais campos (Registrada/Contratada/Faturada) estavam zerados
  3. Para cada carimbo: roda OCR pelo parser correto, extrai campos demanda
  4. Aplica no CONSEN somente os campos que estavam zerados e que o OCR extraiu com valor > 0

Uso:
    python correcao_demanda_zerada_ocr.py                        # simula
    python correcao_demanda_zerada_ocr.py --salvar               # efetiva
    python correcao_demanda_zerada_ocr.py --salvar --retomar-apos 2011889
    python correcao_demanda_zerada_ocr.py --salvar --todos        # inclui Davi
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import pandas as pd
from core.project_paths import resolve_indice_master_csv

_ROOT = Path(__file__).resolve().parent.parent.parent
_OCR_DIR = str(_ROOT / "core" / "ocr")
for _p in [str(_ROOT), str(_ROOT / "core"), _OCR_DIR, str(_ROOT / "core" / "digitacao_consen")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _venv_check  # noqa: F401

try:
    from digitacao_consen import correcao_fluxo_base as fluxo_base
    from digitacao_consen.correcao_fluxo_base import log, warn
except ModuleNotFoundError:
    import correcao_fluxo_base as fluxo_base  # type: ignore
    from correcao_fluxo_base import log, warn  # type: ignore

LOGIN_URL = fluxo_base.LOGIN_URL
BASE_URL  = LOGIN_URL.rsplit("login.php", 1)[0]
EDIT_URL  = os.environ.get(
    "CONSEN_EDITA_FATURA_CARIMBO_URL",
    f"{BASE_URL}index.php#bpg/gestao/fatura/editaFaturaCarimbo.php",
)

ERROS_DIR    = Path(r"\\10.10.250.21\Energia\ARQUIVOS ENZO\Erros de Digitacao")
ROBO_XLSX    = Path(r"c:\Users\Revit\Desktop\ENERGIA\_demanda_zerada_robo.xlsx")
SAIDA_DIR    = Path(r"\\10.10.250.21\Energia\ARQUIVOS ENZO\correcoes_demanda_zerada_ocr")
EXECUCAO_CSV = SAIDA_DIR / "correcao_execucao.csv"
FECHAR       = os.environ.get("CONSEN_CORRECAO_FECHAR", "1").strip() not in {"0", "false"}

CAMPOS_CRITICOS = ("btnSalvar", "fatDemFPontaIndRegistrada")

# Aliases de campo: o mesmo campo lógico pode ter IDs diferentes no HTML do CONSEN
# dependendo da concessionária. Testamos em ordem até achar um que exista no DOM.
_CAMPO_ALIASES: dict[str, tuple[str, ...]] = {
    "fatDemContratadaFPonta":        ("txtDemContratadaFPonta",         "fatDemContratadaFPonta"),
    "fatDemFPontaIndFaturada":       ("fatDemFPontaIndutivo",           "fatDemFPontaIndFaturada"),
    "fatDemPontaFaturada":           ("fatDemPonta",                    "fatDemPontaFaturada"),
    "fatDemContratadaPonta":         ("txtDemContratadaPonta",          "fatDemContratadaPonta"),
    "fatDemFPontaIndValorReais":     ("txt-demandas-fpind-valor-reais", "fatDemFPontaIndValorReais"),
    "fatDemPontaValorReais":         ("txt-demandas-pta-valor-reais",   "fatDemPontaValorReais"),
    "fatDemFPontaIndUltraValorReais":("fatDemFPontaIndUltraValorReais",),
}


def _resolver_aliases_payload(driver, payload: dict) -> dict:
    """Resolve aliases de campo: substitui o ID lógico pelo ID real encontrado no DOM."""
    from selenium.webdriver.common.by import By
    resultado = {}
    for campo, valor in payload.items():
        candidatos = _CAMPO_ALIASES.get(campo, (campo,))
        id_real = campo  # fallback
        for cand in candidatos:
            els = driver.find_elements(By.ID, cand)
            if els:
                id_real = cand
                break
        resultado[id_real] = valor
    return resultado


# Campos de demanda que queremos corrigir (em ordem de prioridade)
CAMPOS_DEMANDA = [
    "fatDemFPontaIndRegistrada",
    "fatDemContratadaFPonta",
    "fatDemFPontaIndFaturada",
    "fatDemPontaRegistrada",
    "fatDemContratadaPonta",
    "fatDemPontaFaturada",
    "fatDemFPontaIndValorReais",
    "fatDemPontaValorReais",
    "fatDemFPontaIndUltra",
    "fatDemFPontaIndUltraValorReais",
    "fatDemPontaUltra",
    "fatDemPontaUltraValorReais",
    "fatDemFPontaExcFaturada",
    "fatDemFPontaExcRegistrada",
    "fatDemFPontaExcValorReais",
]

# Mapeamento campo CONSEN → nome na coluna dos xlsx de Erros de Digitacao
_CAMPO_PARA_XLSX = {
    "fatDemFPontaIndRegistrada": "fatDemFPontaIndRegistrada",
    "fatDemContratadaFPonta":    "fatDemContratadaFPonta",
    "fatDemFPontaIndFaturada":   "fatDemFPontaIndFaturada",
}


def _norm_carimbo(v) -> str:
    return str(v).strip().replace("BB_", "").replace("bb_", "").split(".")[0]


def _fmt(v: float) -> str:
    return f"{v:.2f}".replace(".", ",")


def _mes_ano(mes_ref: str) -> tuple[int, int]:
    """Converte '04-2026' → (4, 2026). Retorna (1, 2026) se nao parsear."""
    try:
        partes = str(mes_ref).strip().split("-")
        return int(partes[0]), int(partes[1])
    except Exception:
        return 1, 2026


# ---------------------------------------------------------------------------
# Deteccao automatica de concessionaria pelo conteudo do PDF
# ---------------------------------------------------------------------------

def _detectar_concessionaria(pdf_path: Path) -> tuple[str, str]:
    """Retorna (concessionaria, sistema) lendo o texto da primeira pagina do PDF."""
    try:
        import pdfplumber
        with pdfplumber.open(str(pdf_path)) as pdf:
            txt = ((pdf.pages[0].extract_text() or "") + (pdf.pages[1].extract_text() if len(pdf.pages) > 1 else "")).upper()
    except Exception:
        return "", ""

    if "COPEL" in txt:
        return "COPEL", "COPEL"
    if "CELESC" in txt:
        return "CELESC", "CELESC"
    if "ENERGISA" in txt:
        return "ENERGISA", "ENERGISA"
    if "LIGHT" in txt or ("RIO DE JANEIRO" in txt and "GRUPO A" in txt):
        return "LIGHT", "LIGHT"
    if "CEMIG" in txt:
        return "CEMIG", "CEMIG"
    if "COELBA" in txt or "BAHIA" in txt and "NEOENERGIA" in txt:
        return "COELBA", "NEOENERGIA"
    if "COSERN" in txt:
        return "COSERN", "NEOENERGIA"
    if "ELEKTRO" in txt:
        return "ELEKTRO", "NEOENERGIA"
    if "CELPE" in txt or ("PERNAMBUCO" in txt and "NEOENERGIA" in txt):
        return "CELPE", "NEOENERGIA"
    if "NEOENERGIA" in txt:
        return "COELBA", "NEOENERGIA"
    if "ENEL" in txt and ("SAO PAULO" in txt or "SÃO PAULO" in txt):
        return "Enel São Paulo", "ENEL_SP"
    if "ENEL" in txt and ("RIO DE JANEIRO" in txt or "BANDEIRANTE" in txt):
        return "Enel Rio de Janeiro", "ENEL_RJ"
    if "ENEL" in txt and ("CEAR" in txt):
        return "ENEL CE", "ENEL_CE"
    if "ENEL" in txt:
        return "Enel São Paulo", "ENEL_SP"
    if "EQUATORIAL" in txt and ("GOIA" in txt or " GO " in txt):
        return "EQUATORIAL GO", "EQUATORIAL_GO"
    if "EQUATORIAL" in txt and ("PIAU" in txt or " PI " in txt):
        return "Equatorial Piauí", "EQUATORIAL_PI"
    if "EQUATORIAL" in txt:
        return "EQUATORIAL GO", "EQUATORIAL_GO"
    if "CPFL" in txt or "RGE" in txt:
        return "CPFL", "CPFL"
    if "EDP" in txt and ("ESPIRITO SANTO" in txt or "ESPÍRITO SANTO" in txt or "ES" in txt):
        return "EDP ES", "EDP_ES"
    if "EDP" in txt:
        return "EDP SP", "EDP_SP"
    if "LIGHT" in txt:
        return "LIGHT", "LIGHT"
    # Equatorial / CEEE / distribuidoras DANF3E e layouts "Grupo A4 - Verde":
    # roteia para o parser MT da família Equatorial (lida com A4/AS Verde e DANF3E).
    if ("RORAIMA" in txt or "AMAZONAS" in txt or "CEEE" in txt or "RGE" in txt
            or "COMPANHIA ESTADUAL DE DISTRIBUI" in txt or "BRASILIA" in txt
            or "BRASÍLIA" in txt or "NEOENERGIA DISTRIBUI" in txt
            or "GRUPO A" in txt or "DANF3E" in txt):
        # sis genérico "EQUATORIAL" (sem _PI) para cair no branch com fallback genérico
        return "EQUATORIAL", "EQUATORIAL"
    return "", ""


# ---------------------------------------------------------------------------
# OCR dispatcher
# ---------------------------------------------------------------------------

def _demanda_generica_mt(pdf_path: Path) -> float:
    """Fallback genérico de demanda contratada/registrada para layouts MT diversos.

    Varre padrões comuns em DANF3E / TUSD de várias distribuidoras quando o
    parser específico não extraiu nenhum campo de demanda.
    """
    import pdfplumber
    import re as _re
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            full = "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception:
        return 0.0

    patterns = [
        r"Demanda\s+Todos\s+os\s+Per[ií]odos\s*:?\s*([\d\.]+)\s*kW",
        r"DEMANDA\s+DE\s+TUSD(?:\s+ISENTA\s+ICMS)?\s+kW\s+([\d\.]+)",
        # Roraima/Amazonas: "D.CtdaF.Pta:50" / "D. Ctda F.Pta: 115" (contratada fora ponta)
        r"D\.?\s*Ctda\s*F\.?\s*Pta\s*:?\s*([\d\.]+)",
        r"D\.?\s*Ctda\s*Pta\s*:?\s*([\d\.]+)",
        # Roraima: "Demanda sem ICMS 50 kW"
        r"Demanda\s+sem\s+ICMS\s+([\d\.]+)\s*kW",
        r"Demanda\s+Contratada\s+(?:[ÚU]nica\s+\(kW\)\s*:?\s*)?([\d\.]+(?:,\d+)?)",
        r"Demanda\s*-\s*kW\s+([\d\.]+(?:,\d+)?)",
        r"Demanda\s+Fora\s+Ponta\s*[-–]?\s*kW\s+([\d\.]+(?:,\d+)?)",
    ]
    def _br(raw: str) -> float:
        raw = raw.strip()
        if "," in raw:                 # 1.234,56 → milhar com vírgula decimal
            raw = raw.replace(".", "").replace(",", ".")
        elif raw.count(".") == 1 and len(raw.split(".")[-1]) == 3:
            raw = raw.replace(".", "")  # 1.400 → milhar
        try:
            return float(raw)
        except ValueError:
            return 0.0

    for pat in patterns:
        m = _re.search(pat, full, _re.IGNORECASE)
        if m:
            v = _br(m.group(1))
            if v and v > 0:
                return v
    return 0.0


def _ocr_light_mt(pdf_path: Path) -> dict:
    """Extrai demanda de faturas LIGHT MT (layout 'Grupo A4/AS - Verde').

    Sinais (em ordem de confiabilidade):
      1. 'Demanda Fora Ponta {MES}/{YY} NN ...'  → histórico, 1º valor = mês atual
      2. 'Demanda NN,NN' isolado (próximo a 'Imposto Retido')  → contratada/registrada
      3. 'Demanda Ativa kW ... kW NN ...'  → quantidade faturada
    """
    import pdfplumber
    import re as _re
    rec: dict = {}
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            full = "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception:
        return rec

    dem = None

    # 1. histórico "Demanda Fora Ponta JAN/26 80 DEZ/25 80 ..." → 1º número
    m = _re.search(r"Demanda\s+Fora\s+Ponta\s+(?:[A-Z]{3}/\d{2}\s+)([\d\.]+)", full, _re.IGNORECASE)
    if m:
        dem = float(m.group(1).replace(".", ""))

    # 2. "Demanda 80,00" isolado (linha de imposto retido / grandezas)
    if not dem:
        for ln in full.splitlines():
            mm = _re.match(r"\s*Demanda\s+([\d\.]+,\d{2})\s*$", ln)
            if mm:
                dem = float(mm.group(1).replace(".", "").replace(",", "."))
                break

    # 3. "Demanda Ativa kW HFP/Unico kW 80 ..." → quantidade após 'kW'
    if not dem:
        m3 = _re.search(r"Demanda\s+Ativa.*?\bkW\s+([\d\.]+)\s+\d", full, _re.IGNORECASE)
        if m3:
            dem = float(m3.group(1).replace(".", ""))

    if dem and dem > 0:
        rec["fatDemFPontaIndRegistrada"] = dem
        rec["fatDemContratadaFPonta"]    = dem
        rec["fatDemFPontaIndFaturada"]   = dem
    return rec


def _ocr_pdf(concessionaria: str, sistema: str, pdf_path: Path, mes: int, ano: int) -> dict:
    """Roda o parser OCR correto para a concessionaria e retorna o dict de campos."""
    conc = concessionaria.upper().strip()
    sis  = sistema.upper().strip()

    # Equatorial PI MT
    if "EQUATORIAL PI" in sis or "EQUATORIAL_PI" in sis:
        from core.ocr.ocr_equatorial_pi_mt import processar_pdf
        return processar_pdf(pdf_path)

    # COPEL — sistema pode ser CNPJ numérico; entry point é _build_record
    if conc == "COPEL" or sis == "COPEL":
        from ocr_copel_mt import _build_record
        rec = _build_record(Path(pdf_path))
        if rec.get("ERRO"):
            # Se demanda foi extraida apesar do erro de campos críticos, use-a
            _dem_fields = ("fatDemFPontaIndRegistrada", "fatDemContratadaFPonta", "fatDemFPontaIndFaturada")
            if any(rec.get(f) for f in _dem_fields):
                rec_clean = {k: v for k, v in rec.items() if k != "ERRO"}
                return rec_clean
            # Fallback: pode ser fatura CEMIG gerida pelo sistema COPEL
            from core.ocr.OCR_Cemig import processar_pdf as _cemig_pdf
            rec2 = _cemig_pdf(str(pdf_path), "mt")
            if not rec2.get("ERRO") or any(rec2.get(f) for f in _dem_fields):
                return rec2
        return rec

    # CELESC — entry point é extrair_campos
    if conc == "CELESC":
        from ocr_celesc_mt import extrair_campos
        return extrair_campos(Path(pdf_path))

    # ENEL (SP, RJ, CE)
    if "ENEL" in sis or "ENEL" in conc:
        from core.ocr.ocr_enel import processar_pdf
        return processar_pdf(str(pdf_path), "mt")

    # CEMIG
    if conc == "CEMIG":
        from core.ocr.OCR_Cemig import processar_pdf
        return processar_pdf(str(pdf_path), "mt")

    # Neoenergia (ELEKTRO, CELPE, COSERN, COELBA)
    if sis == "NEOENERGIA" or conc in ("ELEKTRO", "CELPE", "COSERN", "COELBA"):
        from core.ocr.ocr_neoenergia import processar_pdf_direto
        _tipo, rec = processar_pdf_direto(pdf_path, mes, ano)
        return rec

    # CPFL MT
    if conc == "CPFL" or sis == "CPFL":
        from core.ocr.ocr_cpfl_mt import processar_pdf
        return processar_pdf(pdf_path, mes, ano)

    # Energisa MT (DANF3E)
    if "ENERGISA" in conc or "ENERGISA" in sis:
        from core.ocr.ocr_energisa_mt import processar_pdf_mt
        return processar_pdf_mt(Path(pdf_path), mes, ano)

    # Equatorial GO / PA / MA + distribuidoras DANF3E diversas (Roraima, Amazonas,
    # CEEE/Companhia Estadual, Brasília) — layout "GRUPO DE TENSAO" / TUSD.
    if "EQUATORIAL" in conc or "EQUATORIAL" in sis:
        from core.ocr.ocr_equatorial_pi_mt import processar_pdf as _eq_pdf
        try:
            rec = _eq_pdf(Path(pdf_path))
        except Exception:
            rec = {}
        if not isinstance(rec, dict):
            rec = {}
        # Fallback genérico: preenche cada campo de demanda que ficou vazio.
        _dem3 = ("fatDemFPontaIndRegistrada", "fatDemContratadaFPonta", "fatDemFPontaIndFaturada")
        if not all((rec.get(f) or 0) for f in _dem3):
            dem = _demanda_generica_mt(Path(pdf_path))
            if dem:
                for f in _dem3:
                    if not (rec.get(f) or 0):
                        rec[f] = dem
        return rec

    # LIGHT MT (layout "Grupo A4/AS - Verde")
    if "LIGHT" in conc or "LIGHT" in sis:
        return _ocr_light_mt(Path(pdf_path))

    # EDP ES / EDP SP — BT parser; complementa com extração MT inline para demanda
    if "EDP" in conc:
        import pdfplumber
        import re as _re
        rec: dict = {}
        with pdfplumber.open(str(pdf_path)) as _pdf:
            _full = "\n".join(p.extract_text() or "" for p in _pdf.pages)
        # Demanda faturada: "Demanda kW 36,74 ..."
        m = _re.search(r"^Demanda\s+kW\s+([\d\.,]+)", _full, _re.MULTILINE | _re.IGNORECASE)
        if m:
            rec["fatDemFPontaIndFaturada"] = float(m.group(1).replace(",", "."))
            rec["fatDemFPontaIndRegistrada"] = float(m.group(1).replace(",", "."))
        # Contratada: "Demanda Contratual-KW 80"
        m2 = _re.search(r"Demanda\s+Contratual[-\s]*KW\s+([\d\.,]+)", _full, _re.IGNORECASE)
        if m2:
            rec["fatDemContratadaFPonta"] = float(m2.group(1).replace(",", "."))
        # Registrada mais precisa: "Demanda M?xima FPonta ... XX,XX KW" no demonstrativo
        m3 = _re.search(r"Demanda\s+M.{1,5}xima\s+F(?:ora\s+de\s+)?Ponta\s+.*?([\d,\.]+)\s+KW", _full, _re.IGNORECASE)
        if m3:
            rec["fatDemFPontaIndRegistrada"] = float(m3.group(1).replace(",", "."))
        return rec

    raise ValueError(f"Sem dispatcher OCR: concessionaria={concessionaria!r} sistema={sistema!r}")


# ---------------------------------------------------------------------------
# Carregamento de dados
# ---------------------------------------------------------------------------

def _carregar_xlsx_ref(apenas_robo: bool) -> dict[str, dict[str, str]]:
    """Le os xlsx de Erros de Digitacao e retorna {carimbo: {campo: valor_ref_fmt}}.

    O valor_ref é o valor CORRETO que deve entrar no CONSEN (fonte autoritativa).
    """
    resultado: dict[str, dict[str, str]] = {}

    def _aceitar(row) -> bool:
        if not apenas_robo:
            return True
        return "robo" in str(row.get("Digitador", "")).lower()

    mapeamento = [
        (ERROS_DIR / "Demana Registrada Zerada(2).xlsx", "fatDemFPontaIndRegistrada"),
        (ERROS_DIR / "Demanda Contratada Zerada.xlsx",   "fatDemContratadaFPonta"),
        (ERROS_DIR / "Demanda Faturada Zerada.xlsx",     "fatDemFPontaIndFaturada"),
    ]
    for arq, campo in mapeamento:
        if not arq.exists():
            warn(f"Nao encontrado: {arq}")
            continue
        df = pd.read_excel(arq, dtype=str)
        col_valor = df.columns[-1]
        aceitos = 0
        for _, row in df.iterrows():
            if not _aceitar(row):
                continue
            car = _norm_carimbo(row.get("Carimbo", ""))
            if not car:
                continue
            try:
                fval = float(str(row[col_valor]).replace(",", ".").strip())
            except (ValueError, TypeError):
                continue
            if fval <= 0:
                continue
            resultado.setdefault(car, {})[campo] = _fmt(fval)
            aceitos += 1
        log(f"[{campo.replace('fatDem','').replace('FPontaInd','').replace('Contratada','Cont')}] "
            f"{aceitos}/{len(df)} linhas de {arq.name}")

    return resultado


def _campos_zerados_por_carimbo(apenas_robo: bool) -> dict[str, set[str]]:
    """Compatibilidade: retorna apenas o conjunto de campos zerados por carimbo."""
    ref = _carregar_xlsx_ref(apenas_robo)
    return {car: set(campos.keys()) for car, campos in ref.items()}


def _info_carimbos(carimbos_extra: set[str] | None = None) -> dict[str, dict]:
    """Retorna {carimbo: {concessionaria, sistema, mes_ref, pdf_path}}.

    Fonte primaria: _demanda_zerada_robo.xlsx (tem PDF_path direto).
    Fonte secundaria: indice_master.csv (para carimbos nao presentes no xlsx do robo).
    """
    resultado: dict[str, dict] = {}

    # --- fonte primária: robo xlsx ---
    if ROBO_XLSX.exists():
        df = pd.read_excel(ROBO_XLSX, sheet_name="TODOS", dtype=str)
        for _, row in df.iterrows():
            car = _norm_carimbo(row.get("Carimbo", ""))
            if not car:
                continue
            pdf_str = str(row.get("PDF_path", "")).strip()
            resultado[car] = {
                "concessionaria": str(row.get("Concessionaria", "")).strip(),
                "sistema":        str(row.get("Sistema", "")).strip(),
                "mes_ref":        str(row.get("Mes_Ref", "")).strip(),
                "pdf_path":       Path(pdf_str) if pdf_str and pdf_str != "nan" else None,
            }

    # --- fonte secundária: indice_master.csv (para carimbos extras) ---
    extras_pendentes = (carimbos_extra or set()) - resultado.keys()
    if extras_pendentes:
        master = resolve_indice_master_csv(prefer_network=False)
        if master.exists():
            df_m = pd.read_csv(master, dtype=str)
            df_m["_car"] = df_m["INDICE"].str.replace("BB_", "", regex=False).str.strip()
            df_m = df_m[df_m["_car"].isin(extras_pendentes)]
            for _, row in df_m.iterrows():
                car = row["_car"]
                pdf_str = str(row.get("ARQUIVO", "")).strip()
                resultado[car] = {
                    "concessionaria": str(row.get("CONCESSIONARIA", "")).strip(),
                    "sistema":        str(row.get("SISTEMA", "")).strip(),
                    "mes_ref":        str(row.get("MES_REF", "")).strip(),
                    "pdf_path":       Path(pdf_str) if pdf_str and pdf_str != "nan" else None,
                }
            log(f"indice_master: {len(df_m)}/{len(extras_pendentes)} carimbos extras localizados")

    # --- fonte terciária: CSV de PDFs buscados nas pastas de digitados ---
    extras_pendentes = (carimbos_extra or set()) - resultado.keys()
    if extras_pendentes:
        pdfs_csv = Path(__file__).parent.parent.parent / "_demanda_zerada_davi_rodrigo_pdfs.csv"
        if not pdfs_csv.exists():
            pdfs_csv = Path("_demanda_zerada_davi_rodrigo_pdfs.csv")
        if pdfs_csv.exists():
            df_p = pd.read_csv(pdfs_csv, dtype=str)
            df_p["_car"] = df_p["carimbo"].str.replace("BB_", "", regex=False).str.strip()
            df_p = df_p[df_p["_car"].isin(extras_pendentes)]
            for _, row in df_p.iterrows():
                car = row["_car"]
                pdf_path = Path(str(row.get("pdf_path", "")).strip())
                # Concessionaria/sistema/mes_ref serão inferidos pelo OCR
                resultado[car] = {
                    "concessionaria": "",
                    "sistema":        "",
                    "mes_ref":        "",
                    "pdf_path":       pdf_path,
                }
            log(f"pdfs_digitados: {len(df_p)}/{len(extras_pendentes)} carimbos localizados nas pastas de digitados")
            nao_achou = extras_pendentes - resultado.keys()
            if nao_achou:
                warn(f"Nao encontrados em nenhuma fonte: {sorted(nao_achou)}")

    return resultado


# ---------------------------------------------------------------------------
# OCR em lote
# ---------------------------------------------------------------------------

_CAMPOS_ALVO = [
    "fatDemFPontaIndRegistrada",
    "fatDemContratadaFPonta",
    "fatDemFPontaIndFaturada",
    "fatDemFPontaIndValorReais",
    "fatDemPontaRegistrada",
    "fatDemContratadaPonta",
    "fatDemPontaFaturada",
    "fatDemPontaValorReais",
    "fatDemFPontaIndUltraValorReais",
]


def _extrair_ocr_todos(carimbos: list[str], info: dict, xlsx_ref: dict) -> dict[str, dict[str, str]]:
    """Roda OCR em todos os carimbos e retorna {carimbo: payload_consen}.

    Estrategia de mesclagem:
      1. OCR extrai todos os campos de demanda com valor > 0
      2. xlsx_ref sobrescreve os campos que ele cobre (fonte autoritativa)
      3. Resultado: uniao de ambos, xlsx prevalece onde disponivel
    """
    payloads: dict[str, dict[str, str]] = {}

    for carimbo in carimbos:
        meta = info.get(carimbo)
        xlsx_vals = xlsx_ref.get(carimbo, {})

        # Base do payload: valores do xlsx (autoritativos)
        payload: dict[str, str] = dict(xlsx_vals)

        # Tenta OCR para suplementar campos que o xlsx nao cobre
        if meta and meta.get("pdf_path") and meta["pdf_path"].exists():
            conc = meta["concessionaria"]
            sis  = meta["sistema"]
            mes, ano = _mes_ano(meta["mes_ref"])
            # Para carimbos buscados nas pastas de digitados, concessionaria desconhecida
            if not conc:
                conc, sis = _detectar_concessionaria(meta["pdf_path"])
                if conc:
                    log(f"  BB_{carimbo}: detectado {conc} pelo PDF")
            try:
                rec = _ocr_pdf(conc, sis, meta["pdf_path"], mes, ano)
                if not rec.get("ERRO"):
                    for campo in _CAMPOS_ALVO:
                        if campo in payload:
                            continue  # xlsx ja tem — nao sobrescreve
                        val = rec.get(campo)
                        if val is None:
                            continue
                        try:
                            fval = float(val)
                        except (TypeError, ValueError):
                            continue
                        if fval > 0.0:
                            payload[campo] = _fmt(fval)
            except Exception as e:
                warn(f"  BB_{carimbo}: OCR erro — {type(e).__name__}: {e}")
        elif not xlsx_vals:
            warn(f"  BB_{carimbo}: sem PDF e sem valor xlsx")

        if not payload:
            warn(f"  BB_{carimbo}: sem dados para corrigir")
            continue

        payloads[carimbo] = payload
        conc_log = meta["concessionaria"] if meta else "xlsx-only"
        campos_str = "  ".join(f"{c.replace('fatDem','').replace('FPontaInd','')}={v}" for c, v in payload.items())
        log(f"  BB_{carimbo} [{conc_log}]: {campos_str}")

    return payloads


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Correcao demanda zerada via OCR")
    p.add_argument("--salvar",         action="store_true", help="Efetiva no CONSEN (padrao: simula)")
    p.add_argument("--retomar-apos",   type=str, default="", help="Pula ate e inclusive este carimbo")
    p.add_argument("--reprocessar-ok", action="store_true", help="Reprocessa mesmo os ja marcados ok")
    p.add_argument("--todos",          action="store_true", help="Inclui Davi alem do Robo (padrao: so Robo)")
    p.add_argument("--carimbo",        action="append", default=[], help="Filtra carimbo(s) especificos")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    SAIDA_DIR.mkdir(parents=True, exist_ok=True)

    apenas_robo = not args.todos

    log("Carregando xlsx de referencia (valores autoritativos)...")
    xlsx_ref = _carregar_xlsx_ref(apenas_robo)
    if not xlsx_ref:
        warn("Nenhum carimbo/campo zerado encontrado.")
        return 1

    log("Carregando info de carimbos (concessionaria + PDF)...")
    info = _info_carimbos(carimbos_extra=set(xlsx_ref.keys()))

    # Lista de carimbos a processar
    if args.carimbo:
        carimbos = [_norm_carimbo(c) for c in args.carimbo]
    else:
        carimbos = sorted(xlsx_ref.keys())

    if not args.reprocessar_ok:
        status_ok = fluxo_base.carregar_status_execucao(EXECUCAO_CSV)
        carimbos = [c for c in carimbos if status_ok.get(c) != "ok"]

    if args.retomar_apos:
        marcador = _norm_carimbo(args.retomar_apos)
        idx = next((i for i, c in enumerate(carimbos) if c == marcador), None)
        if idx is not None:
            carimbos = carimbos[idx + 1:]

    if not carimbos:
        log("Nenhum carimbo pendente.")
        return 0

    log(f"\n=== OCR + merge xlsx em {len(carimbos)} carimbos ===")
    payloads = _extrair_ocr_todos(carimbos, info, xlsx_ref)

    if not payloads:
        warn("Nenhum payload OCR valido gerado.")
        return 1

    n_reg  = sum(1 for p in payloads.values() if "fatDemFPontaIndRegistrada" in p)
    n_cont = sum(1 for p in payloads.values() if "fatDemContratadaFPonta"    in p)
    n_fat  = sum(1 for p in payloads.values() if "fatDemFPontaIndFaturada"   in p)
    log(f"\n=== Pronto para corrigir: {len(payloads)} carimbos "
        f"(Reg={n_reg}  Cont={n_cont}  Fat={n_fat}) ===")

    if not args.salvar:
        log("MODO SIMULACAO — use --salvar para efetivar.")
        return 0

    driver = None
    try:
        driver, wait = fluxo_base.abrir_driver_logado()

        for carimbo in sorted(payloads):
            payload = payloads[carimbo]
            log(f"--- BB_{carimbo}  {payload} ---")
            fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "iniciado")

            try:
                fluxo_base.abrir_tela_edicao_carimbo(driver, wait, EDIT_URL)
                fluxo_base.carregar_fatura_por_carimbo(driver, wait, carimbo, CAMPOS_CRITICOS)
            except Exception as e:
                warn(f"BB_{carimbo}: falha ao abrir — {e}")
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "erro_navegacao", str(e))
                continue

            time.sleep(0.2)
            payload_real = _resolver_aliases_payload(driver, payload)
            aplicadas, confirmadas, total = fluxo_base.aplicar_correcoes(
                driver, wait, carimbo, payload_real
            )

            # Campos criticos = os 3 do xlsx (FPontaInd). Campos suplementares
            # (PontaRegistrada, etc.) podem nao existir em todos os formularios
            # e nao devem bloquear o save.
            # Usa payload_real (com aliases resolvidos) para a contagem.
            _CRITICOS_LOGICOS = ("fatDemFPontaIndRegistrada", "fatDemContratadaFPonta", "fatDemFPontaIndFaturada")
            # Verifica quantos criticos logicos estavam no payload original
            criticos_no_payload = [c for c in _CRITICOS_LOGICOS if c in payload]
            # Verifica cada critico individualmente: o campo aplicar_correcoes
            # conta como confirmado se foi preenchido OU ja tinha o valor certo.
            # Como nao temos esse detalhe por campo, usamos a heuristica:
            # se confirmadas >= qtd campos criticos, esta bom para salvar.
            if confirmadas < len(criticos_no_payload):
                warn(f"BB_{carimbo}: campos criticos nao confirmados ({confirmadas}/{total})")
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "incompleto",
                                              f"{confirmadas}/{total}")
                continue

            time.sleep(0.5)
            fluxo_base.salvar_auditar_e_avancar(driver, wait, carimbo)
            fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "ok", f"{confirmadas}/{total}")
            log(f"BB_{carimbo}: salvo ({confirmadas}/{total})")
            time.sleep(0.3)

    except KeyboardInterrupt:
        log("Interrompido.")
    finally:
        if driver and FECHAR:
            try:
                driver.quit()
            except Exception:
                pass

    log("Concluido.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
