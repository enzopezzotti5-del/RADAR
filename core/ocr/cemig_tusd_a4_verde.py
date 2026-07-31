# tarifas/a4_verde_tusd.py
# CEMIG - TUSD Livre A4 Verde (VERSÃO FINAL CORRIGIDA)

from __future__ import annotations

import os
import re
import datetime as dt
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

import pdfplumber


# =============================================================================
# HELPERS BR
# =============================================================================

def _header_irpj_perc(headers: list) -> str | None:
    for h in headers:
        hl = str(h).lower()
        if "irpj" in hl and "perc" in hl and "ret" in hl:
            return h
    return None


def _br_money_to_float(s: str) -> Optional[float]:
    if not s:
        return None
    s = str(s).strip()
    s = re.sub(r"[R\$\s]", "", s, flags=re.IGNORECASE)
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    elif s.startswith("-"):
        neg = True
        s = s[1:]
    s = s.replace(".", "").replace(",", ".")
    try:
        val = float(s)
        return -val if neg else val
    except Exception:
        return None


def _br_int_from_thousand_str(s: str) -> int:
    if not s: return 0
    s = s.replace(".", "")
    try:
        return int(s)
    except Exception:
        return 0


def _parse_date_br(s: str) -> Optional[dt.date]:
    if not s:
        return None
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", s)
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except Exception:
        return None


def _date_to_br(d: dt.date | None) -> str | None:
    if not d:
        return None
    return d.strftime("%d/%m/%Y")


def _is_date_candidate(digits: str) -> bool:
    if len(digits) != 8:
        return False
    try:
        d, m, a = int(digits[:2]), int(digits[2:4]), int(digits[4:])
        if 1 <= d <= 31 and 1 <= m <= 12 and 1990 <= a <= 2035:
            return True
    except Exception:
        pass
    return False


# =============================================================================
# LEITURA DE PDF
# =============================================================================

def _get_pages_text(pdf_path: str) -> List[str]:
    pages_text = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            txt = page.extract_text(layout=True) or ""
            if not txt.strip():
                txt = page.extract_text() or ""
            pages_text.append(txt)
    return pages_text


def _full_text(pages: List[str]) -> str:
    return "\n".join(pages)


def _detectar_subgrupo_consen(full: str) -> str:
    """
    Detecta o subgrupo real da fatura MT da CEMIG no formato esperado pelo Consen.
    """
    candidatos = [
        (r"\bAS\b", "AS [<2,3kV]"),
        (r"\bA3A\b", "A3a [30kV a 44kV]"),
        (r"\bA4\b", "A4 [2,3kV a 25kV]"),
        (r"\bA3\b", "A3 [69 kV]"),
        (r"\bA2\b", "A2  [88 kV a 138 kV]"),
        (r"\bA1\b", "A1"),
    ]

    linhas = [ln.strip() for ln in (full or "").splitlines() if ln.strip()]
    pistas = [
        ln for ln in linhas
        if any(chave in ln.upper() for chave in ("SUBGRUPO", "GRUPO", "TENSAO", "TENSAO"))
    ]
    blocos = pistas + [full or ""]

    for bloco in blocos:
        bloco_up = bloco.upper()
        for pattern, texto_consen in candidatos:
            if re.search(pattern, bloco_up):
                return texto_consen

    return "A4 [2,3kV a 25kV]"


# =============================================================================
# EXTRATORES CORRIGIDOS
# =============================================================================

def _get_unidade_consumidora(full: str) -> Optional[str]:
    """
    ✅ CORRIGIDO: Busca especificamente por N.º DA UNIDADE CONSUMIDORA com formatação preservada
    """
    lines = [ln.strip() for ln in full.splitlines()]

    for i, ln in enumerate(lines):
        if re.search(r"N\.\s*[ºo°]?\s*DA\s+UNIDADE\s+CONSUMIDORA", ln, re.IGNORECASE):
            # Verifica as próximas linhas
            for j in range(i, min(i + 5, len(lines))):
                next_ln = lines[j].strip()
                if not next_ln:
                    continue

                # Procura padrões de UC com formatação preservada
                # Formatos: X.XXX.XXX.XXX-XX ou XXX.XXX.XXX-XX ou XX.XXX.XXX-XX
                m_uc = re.search(r"\b(\d{1,3}\.\d{3}\.\d{3}\.\d{3}-\d{2}|\d{1,3}\.\d{3}\.\d{3}-\d{2})\b", next_ln)
                if m_uc:
                    return m_uc.group(1)

    # Fallback: busca no texto completo
    m = re.search(r"N\.\s*[ºo°]?\s*DA\s+UNIDADE\s+CONSUMIDORA[^\d]*(\d{1,3}\.\d{3}\.\d{3}\.\d{3}-\d{2}|\d{1,3}\.\d{3}\.\d{3}-\d{2})",
                  full, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1)

    return None


def _get_instalacao(full: str) -> Optional[str]:
    """
    ✅ CORRIGIDO: Prioriza Unidade Consumidora sobre Instalação
    """
    uc = _get_unidade_consumidora(full)
    if uc:
        return uc

    m = re.search(r"Instalação\s*[:\.]?\s*(\d{7,})", full, re.IGNORECASE)
    if m:
        cand = m.group(1)
        if not _is_date_candidate(cand):
            return cand

    return None


def _get_ref_venc_valor(full: str) -> Tuple[Optional[dt.date], Optional[dt.date], Optional[float]]:
    """
    ✅ CORRIGIDO: Busca melhorada com suporte a múltiplos formatos
    """
    ref_dt, venc_dt, valor_fatura = None, None, None

    # Referência
    m_ref = re.search(r"\b([A-Z]{3}/\d{4})\b", full)
    if m_ref:
        s_ref = m_ref.group(1)
        try:
            parts = s_ref.split('/')
            mes_map = {
                'JAN': 1, 'FEV': 2, 'MAR': 3, 'ABR': 4, 'MAI': 5, 'JUN': 6,
                'JUL': 7, 'AGO': 8, 'SET': 9, 'OUT': 10, 'NOV': 11, 'DEZ': 12
            }
            mm = mes_map.get(parts[0].upper())
            yy = int(parts[1])
            if mm:
                ref_dt = dt.date(yy, mm, 1)
        except Exception:
            pass

    # Vencimento
    m_venc = re.search(r"Vencimento\s+(\d{2}/\d{2}/\d{4})", full, re.IGNORECASE)
    if m_venc:
        venc_dt = _parse_date_br(m_venc.group(1))
    else:
        # Padrão alternativo: data logo após MÊS/ANO
        m_venc2 = re.search(r"[A-Z]{3}/\d{4}\s+(\d{2}/\d{2}/\d{4})", full)
        if m_venc2:
            venc_dt = _parse_date_br(m_venc2.group(1))

    # Valor Total
    m_val = re.search(r"Valor\s+a\s+pagar\s+\(R\$\)\s+([\d\.]+\,\d{2})", full, re.IGNORECASE)
    if not m_val:
        m_val = re.search(r"Total\s+a\s+pagar.*?R\$\s*([\d\.]+\,\d{2})", full, re.IGNORECASE | re.DOTALL)

    if m_val:
        valor_fatura = _br_money_to_float(m_val.group(1))

    return ref_dt, venc_dt, valor_fatura


def _get_dates_leitura(full: str, year_hint: int) -> Tuple[Optional[dt.date], Optional[dt.date]]:
    m = re.search(r"Anterior\s+Atual.*?(\d{2}/\d{2})\s+(\d{2}/\d{2})", full, re.IGNORECASE | re.DOTALL)
    if not m: return None, None
    s_ant, s_atu = m.group(1), m.group(2)

    def parse_ddmm(s, y):
        try:
            d, mo = map(int, s.split('/'))
            return dt.date(y, mo, d)
        except Exception:
            return None

    return parse_ddmm(s_ant, year_hint), parse_ddmm(s_atu, year_hint)


def _get_fisco_data(pdf_path: str) -> Dict[str, float]:
    """
    ✅ CORRIGIDO: Usa x_tolerance=1 para extração correta
    """
    out = {}

    # ✅ ESTRATÉGIA PRINCIPAL: Texto com x_tolerance=1 (solução definitiva)
    with pdfplumber.open(pdf_path) as pdf:
        # Extrair com tolerância reduzida para evitar corrupção de texto
        full_text = ""
        for page in pdf.pages:
            txt = page.extract_text(x_tolerance=1, y_tolerance=1) or ""
            full_text += txt

    # Buscar ICMS
    m_icms = re.search(r"ICMS\s+([\d\.]+,\d{2})\s+([\d\.]+,\d{2})\s+([\d\.]+,\d{2})", full_text, re.IGNORECASE)
    if m_icms:
        out["icms"] = _br_money_to_float(m_icms.group(3))
        out["aliq_icms"] = _br_money_to_float(m_icms.group(2))

    # Buscar PASEP/PIS
    m_pis = re.search(r"(?:PASEP|PIS)\s+([\d\.]+,\d{2})\s+([\d\.]+,\d{2})\s+([\d\.]+,\d{2})",
                      full_text, re.IGNORECASE)
    if m_pis:
        out["pis"] = _br_money_to_float(m_pis.group(3))
        out["aliq_pis"] = _br_money_to_float(m_pis.group(2))

    # Buscar COFINS
    m_cofins = re.search(r"COFINS\s+([\d\.]+,\d{2})\s+([\d\.]+,\d{2})\s+([\d\.]+,\d{2})",
                         full_text, re.IGNORECASE)
    if m_cofins:
        out["cofins"] = _br_money_to_float(m_cofins.group(3))
        out["aliq_cofins"] = _br_money_to_float(m_cofins.group(2))

    # ✅ FALLBACK: Tentar tabelas se algo falhar
    if not all([out.get("icms"), out.get("pis"), out.get("cofins")]):
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    tables = page.extract_tables()
                    for table in tables:
                        for row in table:
                            if row and len(row) >= 4:
                                first_col = str(row[0] or "").upper().strip()

                                if "ICMS" in first_col and not out.get("icms"):
                                    try:
                                        out["icms"] = _br_money_to_float(row[3])
                                        out["aliq_icms"] = _br_money_to_float(row[2])
                                    except Exception:
                                        pass

                                if ("PIS" in first_col or "PASEP" in first_col) and not out.get("pis"):
                                    try:
                                        out["pis"] = _br_money_to_float(row[3])
                                        out["aliq_pis"] = _br_money_to_float(row[2])
                                    except Exception:
                                        pass

                                if "COFINS" in first_col and not out.get("cofins"):
                                    try:
                                        out["cofins"] = _br_money_to_float(row[3])
                                        out["aliq_cofins"] = _br_money_to_float(row[2])
                                    except Exception:
                                        pass
        except Exception:
            pass

    return out


def _get_retidos(full: str) -> Dict[str, float]:
    ret = {}

    def find_ret(label):
        m = re.search(rf"Imposto\s+Retido\s+-\s+{label}.*?(-?[\d\.]+\,\d{{2}})", full, re.IGNORECASE)
        if m: return _br_money_to_float(m.group(1))
        return None

    ret["csll_ret"] = find_ret("CSLL")
    ret["irpj_ret"] = find_ret("IRPJ")
    ret["pis_ret"] = find_ret("PIS(?:/PASEP)?")
    ret["cof_ret"] = find_ret("COFINS")
    return ret


def _extract_componentes(full: str) -> Dict[str, any]:
    """
    Extrai valores dos componentes TUSD Livre A4 Verde.

    Mapeamento de campos:
    - dem_hfp_qty/val        → Componente Fio HFP (kW / R$)
    - con_hfp_qty/val        → Componente Encargo HFP (kWh / R$)
    - con_hp_qty/val         → Componente Encargo HP (kWh / R$)
    - ultrap_fio_hfp_qty/val → Ultrapassagem C. Fio HFP (kW / R$) → fatDemFPontaExcValorReais
    - ultrap_fio_hfp_tarifa  → Tarifa Unit. da Ultrapassagem → fatDescontoFioKWh
    - desc_fio               → Desconto Comp. Fio HFP (R$) → fatDemandasDevolucaoFPtaValorReais (negativo)
    - desc_fio_aliq          → "Aplicado desconto de XX%" nas Info Gerais → fatDescontoFioKW (%)
    - desc_encargo           → Desconto Comp. Encargo HP (R$)
    - ajuste_desc_fio        → Ajuste de Desconto C. Fio HFP (R$) → obs 263
    - ajuste_desc_encargo    → Ajuste de Desconto C. Enc HP (R$) → obs 261
    - escassez_qty/val       → Enc Cta Esc Hídr / REN 1008 (kWh / R$)
    - covid_qty/val          → Encargo Conta Covid (kWh / R$)
    - scee_hfp/hp_qty/val    → Energia SCEE HFP/HP ISENTA
    - reativo_hfp/hp_qty/val → Energia Reativa HFP/HP
    - bandeira               → Bandeira Tarifária (R$)
    - cip                    → Contrib Ilum Publica (R$)
    - credito_tusd_ponta_val → Crédito TUSD Ponta
    - credito_tusd_fponta_val→ Crédito TUSD F.Ponta
    - restituicao            → Restituição de Pagamento
    - dem_reativa_hfp_qty/val→ Demanda Reativa HFP (kW / R$) — UFDR
    - variacao_tensao        → Variação de Tensão DRC (R$) → obs 251
    """
    dados = defaultdict(float)
    lines = full.splitlines()

    _restitui_pending = False  # carry-forward: linha RESTITUI sem valor próprio

    for ln in lines:
        ln_clean = re.sub(r"\s+", " ", ln).strip()
        ln_upper = ln_clean.upper()

        # ASCII-fold para evitar problemas de encoding com acentos
        ln_ascii = ln_upper.encode("ascii", "ignore").decode("ascii")

        valores = re.findall(r"-?[\d\.]+\,\d{2}", ln_clean)

        # Carry-forward: linha seguinte à RESTITUI sem valor
        if _restitui_pending:
            if valores:
                dados["restituicao"] += abs(_br_money_to_float(valores[-1]) or 0)
            _restitui_pending = False

        # ── ULTRAPASSAGEM C. FIO HFP ─────────────────────────────────────────
        # Linha: "Ultrapassagem C. Fio HFP kW 6 58,55609551 351,32 ..."
        # O Preço Unit. tem 8 casas decimais (58,55609551) — excluir com (?!\d)
        # → fatDemFPontaExcValorReais (R$), fatDemFPontaExcFaturada (kW)
        # → fatDescontoFioKWh = Tarifa Unit. (última coluna com muitas casas)
        if ("ULTRAPASSAGEM" in ln_upper or "ULTRAP" in ln_upper) and "FIO" in ln_upper and "HFP" in ln_upper:
            m_qty = re.search(r"\bKW\s+([\d\.]+)", ln_upper)
            if m_qty:
                dados["ultrap_fio_hfp_qty"] += _br_int_from_thousand_str(m_qty.group(1))
            # Valores monetários com exatamente 2 casas decimais (exclui preço unit. com 8 casas)
            vals_mon = re.findall(r"-?[\d\.]+,\d{2}(?!\d)", ln_clean)
            if vals_mon:
                for v in vals_mon:
                    f = _br_money_to_float(v)
                    if f and abs(f) > 10:
                        dados["ultrap_fio_hfp_val"] += abs(f)
                        break
            # Tarifa Unit. = número com 4+ casas decimais no final da linha
            m_tarifa = re.search(r"(\d+,\d{4,})\s*$", ln_clean.rstrip())
            if m_tarifa:
                t = _br_money_to_float(m_tarifa.group(1))
                if t and t > 0:
                    dados["ultrap_fio_hfp_tarifa"] = t
            continue

        # ── COMPONENTE FIO HFP (demanda normal, não ultrapassagem) ───────────
        if "COMPONENTE FIO HFP" in ln_upper and "S/ ICMS" not in ln_upper:
            if not valores:
                continue
            m_qty = re.search(r"\bKW\s+([\d\.]+)", ln_upper)
            if m_qty:
                dados["dem_hfp_qty"] += _br_int_from_thousand_str(m_qty.group(1))
            for v in valores:
                f = _br_money_to_float(v)
                if f and abs(f) > 100:
                    dados["dem_hfp_val"] += f
                    break
            continue

        if "COMPONENTE FIO HFP" in ln_upper and "S/ ICMS" in ln_upper:
            # qty para soma de registrada; valor R$ também entra no total faturado
            m_qty = re.search(r"\bKW\s+([\d\.]+)", ln_upper)
            if m_qty:
                dados["dem_hfp_sem_icms_qty"] += _br_int_from_thousand_str(m_qty.group(1))
            # Captura valor monetário da linha (entra no dem_hfp_val total)
            vals_mon = re.findall(r"-?[\d\.]+,\d{2}(?!\d)", ln_clean)
            if vals_mon:
                for v in vals_mon:
                    f = _br_money_to_float(v)
                    if f and abs(f) > 10:
                        dados["dem_hfp_sem_icms_val"] += abs(f)
                        break
            continue

        # ── COMPONENTE ENCARGO HFP (energia F.Ponta) ────────────────────────
        if "COMPONENTE ENCARGO HFP" in ln_upper:
            if not valores:
                continue
            m_qty = re.search(r"\bKWH\s+([\d\.]+)", ln_upper)
            if m_qty:
                dados["con_hfp_qty"] += _br_int_from_thousand_str(m_qty.group(1))
            for v in valores:
                f = _br_money_to_float(v)
                if f and abs(f) > 100:
                    dados["con_hfp_val"] += f
                    break
            continue

        # ── COMPONENTE ENCARGO HP (energia Ponta) ───────────────────────────
        if "COMPONENTE ENCARGO HP" in ln_upper:
            if not valores:
                continue
            m_qty = re.search(r"\bKWH\s+([\d\.]+)", ln_upper)
            if m_qty:
                dados["con_hp_qty"] += _br_int_from_thousand_str(m_qty.group(1))
            for v in valores:
                f = _br_money_to_float(v)
                if f and abs(f) > 100:
                    dados["con_hp_val"] += f
                    break
            continue

        # ── ENERGIA SCEE HFP / HP ISENTA ──────────────────────────────────────
        # Essas linhas compõem:
        # - consumo principal do mesmo posto
        # - bloco de injetado do mesmo posto
        if "ENERGIA SCEE" in ln_upper and "ISENTA" in ln_upper:
            m_qty = re.search(r"\bKWH\s+([\d\.]+)", ln_upper)
            qty = _br_int_from_thousand_str(m_qty.group(1)) if m_qty else 0
            vals_mon = re.findall(r"\b\d[\d\.]*,\d{2}(?!\d)", ln_clean)
            val = 0.0
            for v in vals_mon:
                f = _br_money_to_float(v)
                if f is not None and abs(f) > 1:
                    val = abs(f)
                    break
            if "HFP" in ln_upper:
                dados["scee_hfp_qty"] += qty
                dados["scee_hfp_val"] += val
            elif "HP" in ln_upper:
                dados["scee_hp_qty"] += qty
                dados["scee_hp_val"] += val
            continue

        # ── DESCONTO COMP. FIO HFP → Devolução F.Ponta ──────────────────────
        # Linha: "Desconto Comp. Fio HFP  -524,38"
        # → fatDescontoFio (R$, positivo internamente)
        # → fatDemandasDevolucaoFPtaValorReais (R$, negativo na saída)
        if "DESCONTO COMP" in ln_upper and "FIO" in ln_upper:
            if valores:
                val = _br_money_to_float(valores[-1])
                if val is not None:
                    dados["desc_fio"] += abs(val)
            continue

        # ── DESCONTO COMP. ENCARGO HP ────────────────────────────────────────
        if "DESCONTO COMP" in ln_upper and "ENCARGO" in ln_upper:
            if valores:
                val = _br_money_to_float(valores[-1])
                if val is not None:
                    dados["desc_encargo"] += abs(val)
            continue

        # ── AJUSTE DESCONTO FIO / ENCARGO (entram como observação própria) ─
        if "AJUSTE" in ln_upper and "DESCONTO" in ln_upper and "FIO" in ln_upper:
            if valores:
                val = _br_money_to_float(valores[-1])
                if val is not None:
                    dados["ajuste_desc_fio"] += abs(val)
            continue

        if "AJUSTE" in ln_upper and "DESCONTO" in ln_upper and "ENC" in ln_upper:
            if valores:
                val = _br_money_to_float(valores[-1])
                if val is not None:
                    dados["ajuste_desc_encargo"] += abs(val)
            continue

        if "AJUSTE" in ln_upper and "DESCONTO" in ln_upper:
            continue

        # ── ENERGIA REATIVA HFP / HP ─────────────────────────────────────────
        if "ENERGIA REATIVA" in ln_upper or "ENC" in ln_upper and "REATIVO" in ln_upper:
            m_qty = re.search(r"\bKWH\s+([\d\.]+)", ln_upper)
            if m_qty and valores:
                qty = _br_int_from_thousand_str(m_qty.group(1))
                # Valor R$ = 3ª coluna numérica da linha (após qty e preço unit.)
                # Estrutura: kWh QTY PREÇO_UNIT(8casas) VALOR_RS PIS BASE ALIQ ICMS TARIFA
                # Preço unit. tem 4+ casas decimais → excluí-lo com (?!\d) não basta pois
                # 0,40346906 vira 0,40 após truncamento — melhor: excluir números com 4+ casas
                vals_2dec = re.findall(r"\b\d[\d\.]*,\d{2}(?!\d)", ln_clean)
                # vals_2dec: [0,39, 0,01, 0,39, 18,00, 0,07]
                # O valor R$ é o PRIMEIRO na posição; PIS é o segundo (menor); base é igual ao R$
                # Pegar o primeiro valor com 2 casas que não seja alíquota ICMS (12,00/18,00)
                ALIQUOTAS = {12.0, 18.0, 0.0, 25.0}
                val = 0.0
                for v in vals_2dec:
                    f = _br_money_to_float(v)
                    if f is not None and f not in ALIQUOTAS:
                        val = abs(f)
                        break
                if "HFP" in ln_upper:
                    dados["reativo_hfp_qty"] += qty
                    dados["reativo_hfp_val"] += val
                elif "HP" in ln_upper:
                    dados["reativo_hp_qty"] += qty
                    dados["reativo_hp_val"] += val
            continue

        # ── ESCASSEZ HÍDRICA ─────────────────────────────────────────────────
        # Linha: "Enc Cta Esc Hídr REN 1008/2022 kWh 11.490 ... 59,56"
        # Casamento robusto: não depende de acento, usa "REN 1008" ou "ESC" + "HIDR"
        ln_ascii_esc = ln_ascii  # já ASCII-fold acima
        if (
            ("ESC" in ln_ascii_esc and "HIDR" in ln_ascii_esc) or
            "REN 1008" in ln_upper or
            "ESC HIDR" in ln_ascii_esc or
            "ESCASSEZ" in ln_upper
        ):
            if not valores:
                continue
            m_qty = re.search(r"\bKWH\s+([\d\.]+)", ln_upper)
            if m_qty:
                dados["escassez_qty"] += _br_int_from_thousand_str(m_qty.group(1))
            # Valor R$: primeiro valor monetário relevante (>1)
            for v in valores:
                f = _br_money_to_float(v)
                if f and abs(f) > 1:
                    dados["escassez_val"] += abs(f)
                    break
            continue

        # ── CONTA COVID ───────────────────────────────────────────────────────
        if "CONTA COVID" in ln_upper or "ENCARGO CTA COVID" in ln_upper or "ENC CTA COVID" in ln_upper:
            m_qty = re.search(r"\bKWH\s+([\d\.]+)", ln_upper)
            if m_qty and valores:
                dados["covid_qty"] += _br_int_from_thousand_str(m_qty.group(1))
            if valores:
                val = _br_money_to_float(valores[-1]) or 0
                dados["covid_val"] += abs(val)
            continue

        # ── CRÉDITO TUSD PONTA / F.PONTA ────────────────────────────────────
        ln_ascii_cred = ln_ascii
        if "CREDITO" in ln_ascii_cred and "TUSD" in ln_upper and "PONTA" in ln_upper:
            if valores:
                val = abs(_br_money_to_float(valores[-1]) or 0)
                is_fponta = ("F." in ln_upper or "F PONTA" in ln_upper or
                             ln_upper.count("PONTA") > 1 and "F" in ln_upper)
                if is_fponta:
                    dados["credito_tusd_fponta_val"] += val
                else:
                    dados["credito_tusd_ponta_val"] += val
            continue

        # ── CIP ───────────────────────────────────────────────────────────────
        if "ILUM" in ln_upper and "PUBLICA" in ln_upper:
            if valores:
                dados["cip"] += abs(_br_money_to_float(valores[-1]) or 0)
            continue

        # ── BANDEIRA TARIFÁRIA ────────────────────────────────────────────────
        if "BANDEIRA" in ln_upper:
            if valores:
                val = _br_money_to_float(valores[-1]) or 0
                dados["bandeira"] += abs(val)
            continue

        # ── DEMANDA REATIVA HFP (UFDR kW) ────────────────────────────────────
        if "DEMANDA REATIVA" in ln_upper and "HFP" in ln_upper and "KW" in ln_upper:
            m_qty = re.search(r"\bKW\s+([\d\.]+)", ln_upper)
            if m_qty:
                dados["dem_reativa_hfp_qty"] += _br_int_from_thousand_str(m_qty.group(1))
            # Exclui preço unitário (8 casas decimais): usa (?!\d) igual à ultrapassagem
            vals_mon = re.findall(r"-?[\d\.]+,\d{2}(?!\d)", ln_clean)
            for v in vals_mon:
                f = _br_money_to_float(v)
                if f and abs(f) > 10:
                    dados["dem_reativa_hfp_val"] += abs(f)
                    break
            continue

        # ── VARIAÇÃO DE TENSÃO (DRC) — obs 251 ──────────────────────────────
        # ln_ascii pode gerar "VARIAO" (ç→§→removido) em vez de "VARIACAO"
        if ("VARIA" in ln_ascii or "VARIACAO" in ln_ascii) and "TENS" in ln_ascii:
            if valores:
                dados["variacao_tensao"] += abs(_br_money_to_float(valores[-1]) or 0)
            continue

        # ── RESTITUIÇÃO DE PAGAMENTO ──────────────────────────────────────────
        if "RESTITUI" in ln_ascii and ("PAGAM" in ln_upper or "PAGAM" in ln_ascii):
            if valores:
                dados["restituicao"] += abs(_br_money_to_float(valores[-1]) or 0)
            else:
                _restitui_pending = True  # valor pode estar na próxima linha
            continue

    # ── Alíquota % do desconto: extraída das Informações Gerais ──────────────
    # Linha: "Aplicado desconto de 49,98 %."
    m_aliq = re.search(r"[Aa]plicado\s+desconto\s+de\s+([\d,\.]+)\s*%", full)
    if m_aliq:
        dados["desc_fio_aliq"] = _br_money_to_float(m_aliq.group(1)) or 0

    return dados


def _get_demonstrativo(pages: List[str]) -> Dict[str, any]:
    """
    Lê a página do Demonstrativo de Grandezas Faturadas (DEMANDA/REATIVO).

    Estrutura real do PDF CEMIG:
        [linha sem label]  Demanda ativa  kW  46  12/12/25 13:45  40  6  46
        HFP                Demanda ativa adicional kW
        [linha sem label]  Energia reativa - UFER kWh  5  5
        [linha sem label]  Demanda ativa  kW  42  12/12/25 19:15
        HP                 Demanda ativa adicional kW
        [linha sem label]  Energia reativa - UFER kWh  59  59

    O label (HFP/HP) aparece na linha SEGUINTE à linha de dados.
    Estratégia: coletar linhas de dados em buffer; ao encontrar o label,
    atribuir os dados do buffer ao segmento correto.
    """
    res: Dict[str, any] = {
        "dem_hfp_registrada":       None,
        "dem_hfp_contratada":       None,
        "dem_hfp_ultrap":           None,
        "dem_hfp_faturada":         None,
        "dem_hp_registrada":        None,
        "reativo_hfp_qty":          None,
        "reativo_hp_qty":           None,
        "nota_ultrap_tarifa":       None,
        "dem_reativa_hfp_registrada": None,
        "dem_reativa_hfp_faturada":   None,
    }

    demo_text = ""
    for pg in pages:
        if "DEMONSTRATIVO" in pg.upper() or "DEMANDA/REATIVO" in pg.upper():
            demo_text = pg
            break
    if not demo_text:
        return res

    def _parse_dem_nums(ln_clean: str):
        """Remove data DD/MM/AA e hora HH:MM, retorna lista de inteiros > 0."""
        s = re.sub(r"\d{2}/\d{2}/\d{2,4}", "", ln_clean)
        s = re.sub(r"\d{2}:\d{2}", "", s)
        return [int(n) for n in re.findall(r"\b(\d+)\b", s) if int(n) > 0]

    def _apply_dem(nums, segmento):
        """Aplica lista de números ao segmento HFP ou HP."""
        if segmento == "HFP" and nums:
            res["dem_hfp_registrada"] = nums[0]
            if len(nums) == 4:
                # REG, CONT, ULTRAP, FAT_NORMAL
                res["dem_hfp_contratada"] = nums[1]
                res["dem_hfp_ultrap"]     = nums[2]
                res["dem_hfp_faturada"]   = nums[3]
            elif len(nums) == 3:
                # REG, CONT, FAT_NORMAL
                res["dem_hfp_contratada"] = nums[1]
                res["dem_hfp_faturada"]   = nums[2]
            elif len(nums) == 2:
                res["dem_hfp_contratada"] = nums[1]
                res["dem_hfp_faturada"]   = nums[1]
        elif segmento == "HP" and nums:
            res["dem_hp_registrada"] = nums[0]

    def _apply_reativo(nums, segmento):
        if segmento == "HFP" and nums:
            res["reativo_hfp_qty"] = nums[0]
        elif segmento == "HP" and nums:
            res["reativo_hp_qty"] = nums[0]

    lines = demo_text.splitlines()

    # Buffer: guarda (tipo, dados) da linha anterior sem label
    # tipo: "dem" | "reativo"
    pending_dem     = None   # nums da Demanda ativa pendente
    pending_reativo = None   # nums do UFER pendente
    segmento        = None   # segmento ATUAL (definido pelo label)
    # Controle: quantos blocos de Demanda ativa já foram atribuídos
    dem_blocks_seen = 0

    for ln in lines:
        ln_clean = re.sub(r"\s{2,}", " ", ln).strip()
        lu = ln_clean.upper()

        # ── Detecta label de segmento ────────────────────────────────────
        # Label aparece em linhas como "    HFP  Demanda ativa adicional kW"
        # ou "    HP   Demanda ativa adicional kW"
        label_match = re.match(r"^(HFP|HP|HR)\b", lu)
        if label_match:
            novo_seg = label_match.group(1)
            # Ao encontrar o label, os dados pendentes pertencem a este segmento
            if pending_dem is not None:
                _apply_dem(pending_dem, novo_seg)
                pending_dem = None
                dem_blocks_seen += 1
            if pending_reativo is not None:
                _apply_reativo(pending_reativo, novo_seg)
                pending_reativo = None
            segmento = novo_seg
            continue

        # ── Linha de Demanda ativa ────────────────────────────────────────
        if "DEMANDA ATIVA" in lu and "KW" in lu:
            nums = _parse_dem_nums(ln_clean)
            if nums:
                pending_dem = nums
            continue

        # ── Linha de Demanda reativa UFDR (kW) ──────────────────────────────
        if ("DEMANDA REATIVA" in lu or "UFDR" in lu) and "KW" in lu:
            raw = re.findall(r"\b\d{1,3}(?:\.\d{3})*\b", ln_clean)
            nums = [_br_int_from_thousand_str(n) for n in raw if _br_int_from_thousand_str(n) > 0]
            if nums and segmento == "HFP":
                res["dem_reativa_hfp_registrada"] = nums[0]
                res["dem_reativa_hfp_faturada"] = nums[-1]  # último = faturado
            continue

        # ── Linha de Energia reativa UFER ────────────────────────────────
        if ("ENERGIA REATIVA" in lu or "UFER" in lu) and "KWH" in lu:
            # Usa padrão N.NNN para capturar números com ponto de milhar (ex: 1.752)
            raw = re.findall(r"\b\d{1,3}(?:\.\d{3})*\b", ln_clean)
            nums = [_br_int_from_thousand_str(n) for n in raw if _br_int_from_thousand_str(n) > 0]
            if nums and segmento:
                # UFER aparece sem label próprio, pertence ao segmento atual
                _apply_reativo(nums, segmento)
            continue

    # Tarifa Ultrap. C. Fio HFP das Notas (formato "45.62" com ponto)
    m_nota = re.search(r"Ultrap\.\s*C\.\s*Fio\s*HFP\s+([\d\.]+)", demo_text, re.IGNORECASE)
    if m_nota:
        try:
            res["nota_ultrap_tarifa"] = float(m_nota.group(1))
        except Exception:
            pass

    return res


def _get_grandezas_contratadas(full: str) -> Optional[int]:
    """Extrai 'Demanda Fora Ponta' das Grandezas Contratadas."""
    m = re.search(r"Demanda\s+Fora\s+Ponta\s+(\d+)", full, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _get_nota_fiscal_tusd(full: str) -> Optional[str]:
    """Número da Nota Fiscal. Ex: 'NOTA FISCAL No. 123456789' → '123456789'"""
    m = re.search(r"NOTA\s+FISCAL\s+N[ºo°]\s*\.?\s*(\d+)", full, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _get_codigo_barras_tusd(full: str) -> Optional[str]:
    """Linha digitável CEMIG: blocos 'XXXXXXXXXXX-X' separados por espaço."""
    parts = re.findall(r"\d{11}-\d", full or "")
    if len(parts) >= 4:
        return " ".join(parts[:4])
    return " ".join(parts) if parts else None


def _get_valor_nota_fiscal_tusd(full: str) -> Optional[float]:
    """
    Valor da Nota Fiscal = Base de Cálculo do ICMS (seção Reservado ao Fisco).
    Ex: "ICMS 6.931,19 18,00 1.247,60" → retorna 6931.19
    """
    # Busca na seção Reservado ao Fisco
    m = re.search(
        r"Reservado\s+ao\s+Fisco.*?ICMS\s+([\d\.]+,\d{2})",
        full, re.IGNORECASE | re.DOTALL
    )
    if m:
        return _br_money_to_float(m.group(1))

    # Fallback: qualquer linha "ICMS <base> <aliq> <val>" com 3 números
    m2 = re.search(
        r"\bICMS\s+([\d\.]+,\d{2})\s+([\d\.]+,\d{2})\s+([\d\.]+,\d{2})",
        full, re.IGNORECASE
    )
    if m2:
        return _br_money_to_float(m2.group(1))

    return None


def get_dados_extras(pdf_path: str) -> dict:
    """
    Retorna campos extras nao presentes nos headers da planilha,
    usados apenas pelo analisar_e_montar_obs.
    Atualmente: restituicao de pagamento.
    """
    pages = _get_pages_text(pdf_path)
    full = _full_text(pages)
    comp = _extract_componentes(full)
    extras = {}
    if comp["restituicao"] > 0:
        extras["_restituicaoPagamento"] = comp["restituicao"]
    return extras


# =============================================================================
# ENTRYPOINT
# =============================================================================

def extrair_linha(pdf_path: str, headers: list, carimbo=None) -> list:
    pages = _get_pages_text(pdf_path)
    full = _full_text(pages)
    data = {}

    # Instalação/UC
    inst = _get_instalacao(full)
    data["Instalação"] = inst
    data["Instalacao"] = inst

    # Emissão
    m_emis = re.search(r"Data\s+de\s+emiss[ãa]o[:\s]*(\d{2}/\d{2}/\d{4})", full, re.IGNORECASE)
    if m_emis:
        data["fatDataEmissao"] = _parse_date_br(m_emis.group(1))

    # Referência, Vencimento, Valor
    ref_dt, venc_dt, valor_fatura = _get_ref_venc_valor(full)
    data["fatDataReferencia"] = ref_dt
    data["fatDataVcto"] = venc_dt
    data["fatValorFatura"] = valor_fatura

    # Datas Leitura
    year_hint = ref_dt.year if ref_dt else dt.date.today().year
    dt_ant, dt_atu = _get_dates_leitura(full, year_hint)
    data["fatDataLeituraAnterior"] = dt_ant
    data["fatDataLeituraAtual"] = dt_atu

    # CNPJ
    cnpjs = re.findall(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", full)
    for c in cnpjs:
        if not c.startswith("06.981.180"):
            data["CNPJ"] = c
            break

    # Nota Fiscal e Código de Barras (linha digitável)
    data["NOTAFISCAL"] = _get_nota_fiscal_tusd(full) or ""
    data["fatCodigoBarras"] = _get_codigo_barras_tusd(full) or ""

    # Códigos Fixos
    data["concCod"] = 22
    data["cadTarifaCod"] = 1
    data["cadSubGrupoCod"] = _detectar_subgrupo_consen(full)
    data["usuCod"] = 93
    if carimbo:
        data["fatCarimbo"] = carimbo
    data["fatDataCadastro"] = dt.date.today().strftime("%d/%m/%Y")

    # Endereço
    m_cep = re.search(r"\d{5}-\d{3}\s+[A-Z\s]+,\s+MG", full)
    if m_cep:
        data["ENDERECO"] = m_cep.group(0).strip()

    # Extrai componentes (itens da fatura)
    comp = _extract_componentes(full)

    # Extrai Demonstrativo de Grandezas (página 3) — separação registrada/contratada/faturada
    demo = _get_demonstrativo(pages)

    # Detecta "caso a parte": sem pagina de demonstrativo (grandezas todas None)
    _sem_demonstrativo = all(
        demo[k] is None
        for k in ("dem_hfp_registrada", "dem_hfp_faturada", "dem_hp_registrada", "reativo_hfp_qty")
    )

    # Grandezas Contratadas (texto da página 1)
    dem_contratada = _get_grandezas_contratadas(full)

    # Energia:
    # consumo principal = Energia Ativa + Energia SCEE ISENTA do mesmo posto
    ponta_qty_total = int(comp["con_hp_qty"] + comp["scee_hp_qty"])
    fponta_qty_total = int(comp["con_hfp_qty"] + comp["scee_hfp_qty"])
    ponta_val_total = round(comp["con_hp_val"] + comp["scee_hp_val"], 2)
    fponta_val_total = round(comp["con_hfp_val"] + comp["scee_hfp_val"], 2)

    data["fatConFPontaIndValorReais"] = fponta_val_total
    data["fatConPontaValorReais"] = ponta_val_total
    data["fatConFPontaIndFaturado"] = fponta_qty_total
    data["fatConPontaFaturado"] = ponta_qty_total
    data["fatConFPontaIndRegistrado"] = fponta_qty_total
    data["fatConPontaRegistrado"] = ponta_qty_total

    # Demanda F.Ponta — separação correta das colunas
    # Valor R$ = soma Componente Fio HFP (normal) + Componente Fio HFP s/ ICMS
    dem_hfp_val_total = round(comp["dem_hfp_val"] + comp["dem_hfp_sem_icms_val"], 2)
    data["fatDemFPontaIndValorReais"]  = dem_hfp_val_total
    # Registrada: vem do demonstrativo (coluna Registrado), fallback qty da fatura
    data["fatDemFPontaIndRegistrada"]  = demo["dem_hfp_registrada"] or comp["dem_hfp_qty"]
    # Contratada: Grandezas Contratadas > demonstrativo > qty fatura
    data["fatDemContratadaFPonta"]     = dem_contratada or demo["dem_hfp_contratada"] or comp["dem_hfp_qty"]
    # Faturada: usa a soma dos componentes normais para não misturar ultrapassagem.
    dem_hfp_fat = int(comp["dem_hfp_qty"] + comp["dem_hfp_sem_icms_qty"])
    if dem_hfp_fat <= 0:
        dem_hfp_fat = demo["dem_hfp_faturada"]
    if dem_hfp_fat is None:
        if _sem_demonstrativo:
            dem_hfp_fat = data["fatDemFPontaIndRegistrada"]
        else:
            dem_hfp_fat = data["fatDemFPontaIndRegistrada"]
    data["fatDemFPontaIndFaturada"]    = dem_hfp_fat

    # Demanda Ponta Registrada (HP)
    if demo["dem_hp_registrada"] is not None:
        data["fatDemPontaRegistrada"] = demo["dem_hp_registrada"]

    # Ultrapassagem C. Fio HFP
    if comp["ultrap_fio_hfp_val"] > 0:
        data["fatDemFPontaIndUltraValorReais"] = comp["ultrap_fio_hfp_val"]
        ultra_fat = demo["dem_hfp_ultrap"] or int(comp["ultrap_fio_hfp_qty"])
        data["fatDemFPontaIndUltra"] = ultra_fat          # nome real da planilha
        data["fatDemFPontaIndUltraFaturada"] = ultra_fat  # alias de segurança

    # Desconto Fio (TUSD kW) usa a aliquota destacada no quadro da fatura.
    # Desconto Fio (TUSD kWh) fica fixo para este caso operacional.
    data["fatDescontoFio"] = comp["desc_fio_aliq"] or 0
    data["fatDemandasDevolucaoFPtaValorReais"] = -round(comp["desc_fio"], 2) if comp["desc_fio"] > 0 else 0
    data["fatConCreditoTUSDPontaValorReais"] = -round(comp["desc_encargo"], 2) if comp["desc_encargo"] > 0 else 0

    # Crédito TUSD F.Ponta (se houver linha explícita na fatura)
    if comp["credito_tusd_fponta_val"] > 0:
        data["fatConCreditoTUSDFPontaValorReais"] = comp["credito_tusd_fponta_val"]

    # Outros
    data["fatValBandeira"] = comp["bandeira"]
    data["fatIlumPublica"] = comp["cip"]
    data["fatDescontoEncargo"] = 0

    # Escassez Hidrica e Conta COVID
    data["fatEscassezHidrica"] = comp["escassez_qty"]
    data["fatEscassezHidricaValorReais"] = comp["escassez_val"]
    data["fatContaCovid"] = comp["covid_qty"]
    data["fatContaCovidValorReais"] = comp["covid_val"]

    # Reativos — qty do Demonstrativo (Registrado e Faturado da pg3), val dos itens
    # HFP: registrada e faturada são iguais (UFER HFP kWh X X na pg3)
    reativo_hfp_qty = demo["reativo_hfp_qty"] if demo["reativo_hfp_qty"] is not None else int(comp["reativo_hfp_qty"])
    data["fatConFPontaIndReativoExcedente"]    = reativo_hfp_qty   # registrada
    data["fatConFPontaIndReativoFaturado"]     = reativo_hfp_qty   # faturada = mesma
    data["fatConFPontaIndExcRegistrado"]       = reativo_hfp_qty   # registrada (alias)
    data["fatConFPontaIndExcFaturado"]         = reativo_hfp_qty   # faturada (alias)
    data["fatConFPontaIndExcValorReais"]       = comp["reativo_hfp_val"]
    # HP: registrada e faturada são iguais
    reativo_hp_qty = demo["reativo_hp_qty"] if demo["reativo_hp_qty"] is not None else int(comp["reativo_hp_qty"])
    data["fatConPontaExcRegistrado"]           = reativo_hp_qty    # registrada
    data["fatConPontaExcFaturado"]             = reativo_hp_qty    # faturada = mesma
    data["fatConPontaReativoExcedente"]        = reativo_hp_qty    # alias legado
    data["fatConPontaReativoFaturado"]         = reativo_hp_qty    # alias legado
    data["fatConPontaExcValorReais"]           = comp["reativo_hp_val"]

    # Impostos
    fisco = _get_fisco_data(pdf_path)
    data["fatICMS"] = fisco.get("icms")
    data["fatPIS"] = fisco.get("pis")
    data["fatCOFINS"] = fisco.get("cofins")
    data["fatDesIcmsAliquota"] = fisco.get("aliq_icms")
    data["fatDescPisAliquota"] = fisco.get("aliq_pis")
    data["fatDesCofinsAliquota"] = fisco.get("aliq_cofins")

    # Retidos
    ret = _get_retidos(full)
    data["fatDescPisPercRetImposto"] = 0.65 if ret.get("pis_ret") is not None else 0
    data["fatDescPisValRetImposto"] = -abs(ret.get("pis_ret")) if ret.get("pis_ret") is not None else 0
    data["fatDescCofinsPercRetImposto"] = 3.00 if ret.get("cof_ret") is not None else 0
    data["fatDescCofinsValRetImposto"] = -abs(ret.get("cof_ret")) if ret.get("cof_ret") is not None else 0
    data["fatDescCsllPercRetImposto"] = 1.00 if ret.get("csll_ret") is not None else 0
    data["fatDescCsllValRetImposto"] = -abs(ret.get("csll_ret")) if ret.get("csll_ret") is not None else 0
    data["fatDescIrpjValRetImposto"] = -abs(ret.get("irpj_ret")) if ret.get("irpj_ret") is not None else 0
    data["fatDescIrrfPercRetImposto"] = 0
    data["fatDescIrrfValRetImposto"] = 0
    data["fatDescConsumoPercRetImposto"] = 0
    data["fatDescConsumoValRetImposto"] = 0
    data["fatDescDemandaPercRetImposto"] = 0
    data["fatDescDemandaValRetImposto"] = 0

    data["fatDescIrpjPercRetImposto"] = -1
    h_irpj_perc = _header_irpj_perc(headers)
    if h_irpj_perc:
        data[h_irpj_perc] = -1

    # Injetado: usa o mesmo bloco de Energia SCEE ISENTA por posto.
    data["fatConPontaInjetadoRegistrado"] = int(comp["scee_hp_qty"])
    data["fatConPontaInjetadoFaturado"] = int(comp["scee_hp_qty"])
    data["fatConPontaInjetadoValorReais"] = round(comp["scee_hp_val"], 2)
    data["fatConPontaInjetadoUsina"] = 0
    data["fatConPontaInjetadoUsinaSaldoAcumulado"] = 0
    data["fatConFPontaInjetadoRegistrado"] = int(comp["scee_hfp_qty"])
    data["fatConFPontaInjetadoFaturado"] = int(comp["scee_hfp_qty"])
    data["fatConFPontaInjetadoValorReais"] = round(comp["scee_hfp_val"], 2)
    data["fatConFPontaInjetadoUsina"] = 0
    data["fatConFPontaInjetadoUsinaSaldoAcumulado"] = 0
    data["fatConIntermedInjetadoRegistrado"] = 0
    data["fatConIntermedInjetadoFaturado"] = 0
    data["fatConIntermedInjetadoValorReais"] = 0

    # Desconto Fio (TUSD kWh): valor operacional fixo para CEMIG TUSD A4 Verde.
    # Atualizado para 45,14 conforme Res ANEEL 3.589/2026.
    DESCONTO_FIO_KWH_FIXO = 45.14
    data["fatDescontoFioKWh"] = DESCONTO_FIO_KWH_FIXO

    obs_list = []
    if comp["ajuste_desc_fio"] > 0:
        obs_list.append(("263", round(comp["ajuste_desc_fio"], 2)))
    if comp["ajuste_desc_encargo"] > 0:
        obs_list.append(("261", round(comp["ajuste_desc_encargo"], 2)))
    if comp["restituicao"] > 0:
        obs_list.append(("109", -round(comp["restituicao"], 2)))
    # A demanda reativa não deve cair nos campos de demanda do Consen neste fluxo.
    # Mantemos a variação de tensão como observação e preservamos a demanda reativa
    # fora das demandas faturadas até existir o código de observação operacional.
    if comp["variacao_tensao"] > 0:
        obs_list.append(("251", -round(comp["variacao_tensao"], 2)))
    for _i, (_cod, _val) in enumerate(obs_list[:5], start=1):
        data[f"obsCod_{_i}"] = _cod
        data[f"obsValor_{_i}"] = _val

    # Nota Fiscal
    # caso a parte (sem demonstrativo): usa diretamente o valor da fatura
    if _sem_demonstrativo:
        data["fatValorNotaFiscal"] = data.get("fatValorFatura")
    else:
        vnf = _get_valor_nota_fiscal_tusd(full)
        data["fatValorNotaFiscal"] = vnf if vnf is not None else data.get("fatValorFatura")

    # Saída — padrão idêntico ao B3
    def _fmt_pt(v: float) -> str:
        return "{:.2f}".format(float(v)).replace(".", ",")

    out = []
    for h in headers:
        hl = str(h).lower()
        if "irpj" in hl and "perc" in hl and "ret" in hl:
            out.append("-1")
            continue

        v = data.get(h)

        if isinstance(v, (dt.date, dt.datetime)):
            v = v.strftime("%d/%m/%Y")
        if isinstance(v, float):
            v = _fmt_pt(round(v, 2))
        if isinstance(v, int):
            v = str(v)
        if v is None or v == "":
            if str(h).startswith("fatData"):
                v = "0"
            else:
                v = 0 if str(h).startswith("fat") else "0"

        out.append(v)

    return out
