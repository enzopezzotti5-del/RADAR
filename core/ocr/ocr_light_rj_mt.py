#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ocr_light_rj_mt.py
------------------
OCR faturas MT Light RJ (Grupos A4/AS/A3a) -> XLSX schema CEMIG/Consen.

Layout: colunas Componente Fio kW HFP / Componente Encargo kWh HFP / HP
        com ICMS embarcado por linha; total fiscal aparece na linha TOTAL.

Uso:
    python ocr_light_rj_mt.py --pasta "\\\\srv\\...\\05.2026\\BT" --mes 05 --ano 2026
    python ocr_light_rj_mt.py --pasta "..." --saida ocr_light_mt.xlsx
"""

from __future__ import annotations

import argparse
import datetime as _dt
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pdfplumber

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.ocr.OCR_Cemig import HEADERS_REF, salvar_excel

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

PASTA_PDF_DEFAULT = Path(os.environ.get(
    "LIGHT_RJ_MT_PASTA_PDF",
    "//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD LIGHT",
))
PASTA_SAIDA_DEFAULT = Path(os.environ.get(
    "LIGHT_RJ_MT_PASTA_SAIDA",
    "//10.10.250.21/Energia/ARQUIVOS ENZO/OCR LIGHT RJ MT",
))
CONC_COD = "3"
MAX_WORKERS = 4

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# =============================================================================
# UTILITÁRIOS
# =============================================================================

_MESES = {
    "JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12,
}

_SUBGRUPO_MAP = {
    "A4": "A4 [<13,8kV]",
    "AS": "AS [<2,3kV]",
    "A3A": "A3a [<30kV]",
    "A3": "A3 [<69kV]",
    "A2": "A2 [<88kV]",
    "A1": "A1 [>=230kV]",
}

# Alíquotas fixas de retenção para LIGHT (RIR Decreto 9.580/2018 / IN 1.234/12)
_ALIQ_RET = {"IRPJ": 1.20, "PIS": 0.65, "COFINS": 3.00, "CSLL": 1.00}


def _br2f(s: str) -> float:
    try:
        return float(str(s).strip().replace(".", "").replace(",", "."))
    except (ValueError, AttributeError):
        return 0.0


def _texto(pdf_path: Path) -> str:
    with pdfplumber.open(str(pdf_path)) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages[:2])


# =============================================================================
# PARSER PRINCIPAL
# =============================================================================

def processar_pdf(pdf_path: str | Path) -> dict:
    pdf_path = Path(pdf_path)
    filename = pdf_path.name
    try:
        txt = _texto(pdf_path)
        dados: dict = {h: "" for h in HEADERS_REF}
        dados["ARQUIVO"] = filename
        dados["ERRO"] = ""
        dados["fatDataCadastro"] = _dt.date.today()
        dados["concCod"] = CONC_COD
        dados["fatMultasDiversas"] = 0.0
        dados["fatValBandeira"] = 0.0
        dados["fatTributoFederalPerc"] = 5.85

        # ── Carimbo ──────────────────────────────────────────────────────────
        m_car = re.search(r"BB_(\d+)", filename, re.IGNORECASE)
        dados["fatCarimbo"] = int(m_car.group(1)) if m_car else 0

        # ── Subgrupo e tarifa (ex: "Grupo A4 / A4 - Verde / Comercial") ──────
        m_grupo = re.search(r"Grupo\s+([\w]+)\s*/.*?-\s*(Verde|Azul)", txt, re.IGNORECASE)
        if m_grupo:
            sg_raw = m_grupo.group(1).upper()
            dados["cadSubGrupoCod"] = _SUBGRUPO_MAP.get(sg_raw, sg_raw)
            dados["cadTarifaCod"] = "HS - Verde" if m_grupo.group(2).lower() == "verde" else "HS - Azul"
            dados["TARIFA_DETECTADA"] = sg_raw
        else:
            dados["cadSubGrupoCod"] = "A4 [<13,8kV]"
            dados["cadTarifaCod"] = "HS - Verde"
            dados["TARIFA_DETECTADA"] = "A4"

        # ── UC: "1.641.059-69" ou "9.134.059-01" ────────────────────────────
        m_uc = re.search(r"^(\d[\d.]*-\d{2})\s", txt, re.MULTILINE)
        if not m_uc:
            m_uc = re.search(r"(?m)^(\d{1,4}(?:\.\d{3}){1,3}-\d{2})\s*$", txt)
        if m_uc:
            dados["Instalacao"] = m_uc.group(1)
            dados["CODIGOCLIENTE"] = m_uc.group(1)

        # ── CNPJ ─────────────────────────────────────────────────────────────
        m_cnpj = re.search(r"CNPJ\s+([\d./-]+)", txt)
        if m_cnpj:
            dados["CNPJ"] = m_cnpj.group(1).strip()

        # ── Período de leitura: "30/04/2026 31/05/2026 31 30/06/2026" ────────
        m_periodo = re.search(
            r"(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+\d+\s+\d{2}/\d{2}/\d{4}", txt
        )
        if m_periodo:
            try:
                dados["fatDataLeituraAnterior"] = _dt.datetime.strptime(
                    m_periodo.group(1), "%d/%m/%Y"
                ).date()
                dados["fatDataLeituraAtual"] = _dt.datetime.strptime(
                    m_periodo.group(2), "%d/%m/%Y"
                ).date()
            except ValueError:
                pass

        # ── Referência, vencimento e valor: "MAI/2026 15/07/2026 R$792,78" ──
        m_ref_total = re.search(
            r"\b([A-Z]{3}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+R\$([\d.]+,\d{2})", txt
        )
        if m_ref_total:
            dados["fatValorFatura"] = _br2f(m_ref_total.group(3))
            try:
                dados["fatDataVcto"] = _dt.datetime.strptime(
                    m_ref_total.group(2), "%d/%m/%Y"
                ).date()
            except ValueError:
                pass
            m_mes = re.match(r"([A-Z]{3})/(\d{4})", m_ref_total.group(1))
            if m_mes and m_mes.group(1) in _MESES:
                dados["fatDataReferencia"] = _dt.date(
                    int(m_mes.group(2)), _MESES[m_mes.group(1)], 1
                )

        # ── Data de emissão ───────────────────────────────────────────────────
        m_emissao = re.search(r"EMISS[ÃA]O\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})", txt, re.IGNORECASE)
        if m_emissao:
            try:
                dados["fatDataEmissao"] = _dt.datetime.strptime(
                    m_emissao.group(1), "%d/%m/%Y"
                ).date()
            except ValueError:
                pass

        # ── DEMANDA (Componente Fio kW HFP) ──────────────────────────────────
        m_fio = re.search(
            r"^Componente\s+Fio\s+kW\s+HFP\s+kW\s+([\d.]+)\s+[\d.,]+\s+([\d.]+,\d{2})",
            txt, re.MULTILINE | re.IGNORECASE,
        )
        dem_kw = _br2f(m_fio.group(1)) if m_fio else 0.0
        dem_val_bruto = _br2f(m_fio.group(2)) if m_fio else 0.0

        m_desc_fio = re.search(
            r"^Desconto\s+Comp\.\s+Fio\s+HFP\s+(-?[\d.]+,\d{2})",
            txt, re.MULTILINE | re.IGNORECASE,
        )
        m_ajuste_fio = re.search(
            r"^Ajuste\s+de\s+Desconto\s+C\.\s+Fio\s+HFP\s+([\d.]+,\d{2})",
            txt, re.MULTILINE | re.IGNORECASE,
        )
        desc_fio = abs(_br2f(m_desc_fio.group(1))) if m_desc_fio else 0.0
        ajuste_fio = _br2f(m_ajuste_fio.group(1)) if m_ajuste_fio else 0.0

        dados["fatDemFPontaIndRegistrada"] = dem_kw
        dados["fatDemFPontaIndFaturada"] = dem_kw
        dados["fatDemContratadaFPonta"] = dem_kw
        dados["fatDemFPontaIndValorReais"] = dem_val_bruto
        dados["fatDemandasDevolucaoFPtaValorReais"] = -round(desc_fio, 2) if desc_fio > 0 else 0.0
        dados["fatDemFPontaIndUltra"] = 0.0
        dados["fatDemFPontaIndUltraValorReais"] = 0.0

        # ── Desconto Fio ──────────────────────────────────────────────────────
        # "Percentual de Desconto aplicado de 49,5946419% conforme Rel. de Contabilização da CCEE"
        m_perc_fio = re.search(
            r"Percentual\s+de\s+Desconto\s+aplicado\s+de\s+([\d.,]+)\s*%",
            txt, re.IGNORECASE,
        )
        if m_perc_fio:
            dados["fatDescontoFio"] = _br2f(m_perc_fio.group(1))
            dados["fatDescontoFioKWh"] = 40.86

        # ── CONSUMO HFP (Componente Encargo kWh HFP) ─────────────────────────
        m_enc_hfp = re.search(
            r"^Componente\s+Encargo\s+kWh\s+HFP\s+kWh\s+([\d.]+)\s+[\d.,]+\s+([\d.]+,\d{2})",
            txt, re.MULTILINE | re.IGNORECASE,
        )
        kwh_hfp = _br2f(m_enc_hfp.group(1)) if m_enc_hfp else 0.0
        val_hfp = _br2f(m_enc_hfp.group(2)) if m_enc_hfp else 0.0
        dados["fatConFPontaIndRegistrado"] = kwh_hfp
        dados["fatConFPontaIndFaturado"] = kwh_hfp
        dados["fatConFPontaIndValorReais"] = val_hfp

        # ── CONSUMO HP / Ponta (Componente Encargo kWh HP) ───────────────────
        m_enc_hp = re.search(
            r"^Componente\s+Encargo\s+kWh\s+HP\s+kWh\s+([\d.]+)\s+[\d.,]+\s+([\d.]+,\d{2})",
            txt, re.MULTILINE | re.IGNORECASE,
        )
        kwh_hp = _br2f(m_enc_hp.group(1)) if m_enc_hp else 0.0
        val_hp_bruto = _br2f(m_enc_hp.group(2)) if m_enc_hp else 0.0

        m_desc_enc_hp = re.search(
            r"^Desconto\s+Comp\.\s+Encargo\s+HP\s+(-?[\d.]+,\d{2})",
            txt, re.MULTILINE | re.IGNORECASE,
        )
        m_ajuste_enc_hp = re.search(
            r"^Ajuste\s+de\s+Desconto\s+C\.\s+Enc\s+HP\s+([\d.]+,\d{2})",
            txt, re.MULTILINE | re.IGNORECASE,
        )
        desc_enc_hp = abs(_br2f(m_desc_enc_hp.group(1))) if m_desc_enc_hp else 0.0
        ajuste_enc_hp = _br2f(m_ajuste_enc_hp.group(1)) if m_ajuste_enc_hp else 0.0

        dados["fatConPontaRegistrado"] = kwh_hp
        dados["fatConPontaFaturado"] = kwh_hp
        dados["fatConPontaValorReais"] = val_hp_bruto
        dados["fatConCreditoTUSDPontaValorReais"] = -round(desc_enc_hp, 2) if desc_enc_hp > 0 else 0.0

        # ── ICMS ──────────────────────────────────────────────────────────────
        # A linha "TOTAL" exibe 3 valores: [PIS/COFINS sum] [ICMS base] [ICMS valor].
        # Em alguns PDFs o texto OCR mistura a linha TOTAL com a última retenção;
        # procuramos os 3 últimos números antes de "ICMS da subvenção/desconto".
        icms_total = 0.0
        idx_sub = txt.lower().find("icms da subven")
        if idx_sub > 0:
            bloco_pre = txt[:idx_sub].rstrip()
            ultima_linha = bloco_pre.split("\n")[-1]
            m_tot = re.search(
                r"([\d.]+,\d{2})\s+([\d.]+,\d{2})\s+([\d.]+,\d{2})\s*$", ultima_linha
            )
            if m_tot:
                icms_total = _br2f(m_tot.group(3))

        # Fallback: linha "^TOTAL ... N N N"
        if icms_total == 0.0:
            m_total_row = re.search(
                r"^TOTAL\s+[\d.,]+\s+([\d.]+,\d{2})\s+([\d.]+,\d{2})\s*$",
                txt, re.MULTILINE | re.IGNORECASE,
            )
            if m_total_row:
                icms_total = _br2f(m_total_row.group(2))

        # Fallback 2: soma ICMS de cada componente principal (após "24,000")
        if icms_total == 0.0:
            for m_comp in re.finditer(
                r"^Componente\s+(?:Fio|Encargo)[^\n]+24,000\s+([\d.]+,\d{2})",
                txt, re.MULTILINE | re.IGNORECASE,
            ):
                icms_total += _br2f(m_comp.group(1))

        dados["fatICMS"] = round(icms_total, 2)
        dados["fatDesIcmsAliquota"] = 24.0

        # ── PIS / COFINS ──────────────────────────────────────────────────────
        m_pis = re.search(
            r"\bPIS(?:/PASEP)?\s+([\d.]+,\d{2})\s+([\d.,]+)\s*%\s+([\d.]+,\d{2})",
            txt, re.IGNORECASE,
        )
        if m_pis:
            dados["fatValorNotaFiscal"] = _br2f(m_pis.group(1))
            dados["fatDescPisAliquota"] = _br2f(m_pis.group(2))
            dados["fatPIS"] = _br2f(m_pis.group(3))

        m_cof = re.search(
            r"\bCOFINS\s+([\d.]+,\d{2})\s+([\d.,]+)\s*%\s+([\d.]+,\d{2})",
            txt, re.IGNORECASE,
        )
        if m_cof:
            dados["fatDesCofinsAliquota"] = _br2f(m_cof.group(2))
            dados["fatCOFINS"] = _br2f(m_cof.group(3))

        # ── COSIP ─────────────────────────────────────────────────────────────
        m_cosip = re.search(r"[Cc]ontrib\.?\s+[Ii]lum[^\d]+([\d.]+,\d{2})", txt)
        if m_cosip:
            dados["fatIlumPublica"] = _br2f(m_cosip.group(1))
        m_cosip2 = re.search(
            r"^Complemento\s+COSIP\b[^\n]*([\d.]+,\d{2})\s*$", txt, re.MULTILINE | re.IGNORECASE
        )
        if m_cosip2:
            dados["fatIlumPublica"] = round(
                float(dados.get("fatIlumPublica") or 0.0) + _br2f(m_cosip2.group(1)), 2
            )

        # ── Multas / Juros / IPCA ─────────────────────────────────────────────
        # Usar \s antes do capture para pegar o ÚLTIMO número do fim da linha
        # (evita backtracking capturar sufixo de outro número, ex: "8,65" de "308,65")
        multa = sum(
            _br2f(m.group(1))
            for m in re.finditer(r"^Multa\b[^\n]*\s([\d.]+,\d{2})\s*$", txt, re.MULTILINE | re.IGNORECASE)
        )
        juros = sum(
            _br2f(m.group(1))
            for m in re.finditer(r"^Juros\b[^\n]*\s([\d.]+,\d{2})\s*$", txt, re.MULTILINE | re.IGNORECASE)
        )
        # pdfplumber extrai "DÉBITO" com caracter de substituição (D?BITO);
        # usamos "D.BITO" para cobrir tanto D?BITO quanto DÉBITO/DEBITO.
        ipca = sum(
            _br2f(m.group(1))
            for m in re.finditer(
                r"^D.BITO\s+VAR\s+IPCA\b\s+([\d.]+,\d{2})\s*$",
                txt, re.MULTILINE | re.IGNORECASE,
            )
        )
        dados["fatMultas"] = round(multa + juros + ipca, 2)

        # ── Retenções ─────────────────────────────────────────────────────────
        # IRPJ split: Energia → fatDescIrpjValRetImposto (1,2%),
        #             Demanda → fatDescDemandaValRetImposto (4,8%).
        irpj_energia = sum(
            _br2f(m.group(1))
            for m in re.finditer(
                r"(?:Imposto\s+)?Retido\s+IRPJ\s*-\s*Energia[^\d]*([\d.,]+)",
                txt, re.IGNORECASE,
            )
        )
        irpj_demanda = sum(
            _br2f(m.group(1))
            for m in re.finditer(
                r"(?:Imposto\s+)?Retido\s+IRPJ\s*-\s*Demanda[^\d]*([\d.,]+)",
                txt, re.IGNORECASE,
            )
        )
        if irpj_energia == 0.0 and irpj_demanda == 0.0:
            irpj_energia = sum(
                _br2f(m.group(1))
                for m in re.finditer(
                    r"(?:Imposto\s+)?Retido\s+IRPJ[^\d]*([\d.,]+)",
                    txt, re.IGNORECASE,
                )
            )
        if irpj_energia > 0:
            dados["fatDescIrpjValRetImposto"] = -round(irpj_energia, 2)
            dados["fatDescIrpjPercRetImposto"] = _ALIQ_RET["IRPJ"]  # 1.20 %
        if irpj_demanda > 0:
            dados["fatDescDemandaValRetImposto"] = -round(irpj_demanda, 2)
            dados["fatDescDemandaPercRetImposto"] = 4.8

        for cod, campo_val, campo_perc in [
            ("PIS",    "fatDescPisValRetImposto",    "fatDescPisPercRetImposto"),
            ("COFINS", "fatDescCofinsValRetImposto", "fatDescCofinsPercRetImposto"),
            ("CSLL",   "fatDescCsllValRetImposto",   "fatDescCsllPercRetImposto"),
        ]:
            total_ret = sum(
                _br2f(m.group(1))
                for m in re.finditer(
                    r"(?:Imposto\s+)?Retido\s+" + cod + r"[^\d]*([\d.,]+)",
                    txt, re.IGNORECASE,
                )
            )
            if total_ret > 0:
                dados[campo_val] = -round(total_ret, 2)
                dados[campo_perc] = _ALIQ_RET[cod]

        # ── Restituição de Pagamento → OBS 109 ────────────────────────────────
        m_restit = re.search(
            r"^Restitui.{1,2}o\s+de\s+Pagamento\b[^\n]*\s(-?[\d.]+,\d{2})\s*$",
            txt, re.MULTILINE | re.IGNORECASE,
        )
        restituicao = abs(_br2f(m_restit.group(1))) if m_restit else 0.0

        # ── PIS/COFINS da subvenção/desconto → OBS 256 ────────────────────────
        m_pis_sub = re.search(
            r"^PIS/COFINS\s+da\s+subven[^\n]*\s(-?[\d.]+,\d{2})\s*$",
            txt, re.MULTILINE | re.IGNORECASE,
        )
        pis_cofins_subvencao = abs(_br2f(m_pis_sub.group(1))) if m_pis_sub else 0.0

        # ── ICMS da subvenção/desconto → OBS 145 ──────────────────────────────
        m_icms_sub = re.search(
            r"^ICMS\s+da\s+subven[^\n]*\s(-?[\d.]+,\d{2})\s*$",
            txt, re.MULTILINE | re.IGNORECASE,
        )
        icms_subvencao = abs(_br2f(m_icms_sub.group(1))) if m_icms_sub else 0.0

        # ── Observações ───────────────────────────────────────────────────────
        obs_list = []
        if ajuste_fio > 0:
            obs_list.append(("263", round(ajuste_fio, 2)))
        if ajuste_enc_hp > 0:
            obs_list.append(("261", round(ajuste_enc_hp, 2)))
        if restituicao > 0:
            obs_list.append(("109", -round(restituicao, 2)))
        if pis_cofins_subvencao > 0:
            obs_list.append(("256", -round(pis_cofins_subvencao, 2)))
        if icms_subvencao > 0:
            obs_list.append(("145", -round(icms_subvencao, 2)))
        for _i, (_cod, _val) in enumerate(obs_list[:5], start=1):
            dados[f"obsCod_{_i}"] = _cod
            dados[f"obsValor_{_i}"] = _val

        val_fatura = dados.get("fatValorFatura") or 0.0
        log.info(
            "  OK  %s  |  sg=%s  Dem=%.1fkW  FP=%.0fkWh  Pta=%.0fkWh  R$=%.2f",
            filename,
            dados.get("cadSubGrupoCod", "?"),
            dados.get("fatDemFPontaIndFaturada") or 0,
            dados.get("fatConFPontaIndFaturado") or 0,
            dados.get("fatConPontaFaturado") or 0,
            val_fatura,
        )
        return dados

    except Exception as exc:
        log.error("  ERRO  %s: %s", filename, exc, exc_info=True)
        m_car2 = re.search(r"BB_(\d+)", filename, re.IGNORECASE)
        return {
            "fatCarimbo": int(m_car2.group(1)) if m_car2 else 0,
            "Instalacao": "",
            "concCod": CONC_COD,
            "TARIFA_DETECTADA": "ERRO",
            "ARQUIVO": filename,
            "ERRO": str(exc),
        }


# =============================================================================
# PROCESSAMENTO EM LOTE
# =============================================================================

def processar_pasta(pasta: Path, xlsx_saida: Path, carimbos: set[str] | None = None) -> int:
    todos = sorted(pasta.glob("BB_*.pdf"))
    if carimbos:
        c_norm: set[str] = set()
        for c in carimbos:
            c = c.strip().upper()
            c_norm.add(c)
            c_norm.add(f"BB_{c}" if not c.startswith("BB_") else c[3:])
        pdfs = [p for p in todos if p.stem.upper() in c_norm]
    else:
        pdfs = todos

    if not pdfs:
        log.warning("Nenhum PDF encontrado em: %s", pasta)
        return 0

    log.info("Processando %d PDFs em: %s", len(pdfs), pasta)
    registros: list[dict] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(processar_pdf, p): p for p in pdfs}
        for fut in as_completed(futures):
            registros.append(fut.result())

    registros.sort(key=lambda r: int(r.get("fatCarimbo") or 0))
    xlsx_saida.parent.mkdir(parents=True, exist_ok=True)
    salvar_excel(registros, xlsx_saida)
    log.info("Salvo: %s  (%d faturas)", xlsx_saida, len(registros))
    return len(registros)


# =============================================================================
# CLI
# =============================================================================

def _parse_args() -> argparse.Namespace:
    hoje = _dt.date.today()
    p = argparse.ArgumentParser(description="OCR Light RJ MT -> XLSX schema CEMIG/Consen")
    p.add_argument("--pasta", type=str, default="",
                   help="Pasta com os PDFs (ex: \\\\srv\\...\\05.2026\\BT)")
    p.add_argument("--saida", type=str, default="",
                   help="Caminho XLSX de saida")
    p.add_argument("--mes",   type=str, default=f"{hoje.month:02d}")
    p.add_argument("--ano",   type=str, default=str(hoje.year))
    p.add_argument("--carimbo", action="append", default=[],
                   help="Filtrar por carimbo(s) especificos (ex: 2015060 ou BB_2015060)")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    mes = f"{int(args.mes):02d}"
    ano = str(int(args.ano))
    carimbos: set[str] = {str(c).strip().upper() for c in (args.carimbo or []) if str(c).strip()}

    if args.pasta.strip():
        pasta = Path(args.pasta.strip())
    else:
        pasta = PASTA_PDF_DEFAULT / f"{mes}.{ano}" / "BT"

    if args.saida.strip():
        xlsx_saida = Path(args.saida.strip())
    else:
        xlsx_saida = PASTA_SAIDA_DEFAULT / f"ocr_light_rj_MT_{mes}{ano}.xlsx"

    if not pasta.exists():
        log.error("Pasta nao encontrada: %s", pasta)
        return 1

    processar_pasta(pasta, xlsx_saida, carimbos or None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
