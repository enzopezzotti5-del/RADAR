#!/usr/bin/env python3
# tarifas/b1_convencional.py
# CEMIG - B1 Convencional (Residencial/Baixa Tensão) - CORRIGIDO

from __future__ import annotations

import re
import datetime as dt
from typing import Dict, List, Optional, Tuple, Any

import pdfplumber

# =============================================================================
# 1. REGEX E UTILITÁRIOS
# =============================================================================

RE_DATE = re.compile(r"\b(\d{2})[\/\.](\d{2})[\/\.](\d{4})\b")
RE_REF = re.compile(r"\b([A-Z]{3})/(\d{4})\b", re.IGNORECASE)
RE_MONEY = re.compile(r"-?[\d\.]+\,\d{2}")

_STRING_FIELDS = {
    "Instalação", "CNPJ", "usuCod", "cadObsCodigo", "cadTarifaCod", "cadSubGrupoCod",
    "ENDERECO", "NOTAFISCAL", "fatCodigoBarras", "Instalacao Antiga"
}

_DATE_KEYS = ("Data", "Vcto", "Emissao", "Leitura", "Referencia")


def _br_money_to_float(s: str) -> float:
    """Converte string R$ 1.000,00 para float 1000.00"""
    if not s: return 0.0
    s = s.strip().replace('"', '').replace("'", "")

    neg = False
    if s.startswith("-") or (s.startswith("(") and s.endswith(")")):
        neg = True
        s = s.replace("-", "").replace("(", "").replace(")", "")

    s = re.sub(r"[R\$\s]", "", s)
    s = s.replace(".", "").replace(",", ".")

    try:
        val = float(s)
        return -val if neg else val
    except Exception:
        return 0.0


def _fmt_pt(v: Any) -> str:
    """Formata float para string Excel PT-BR (1.000,00)"""
    if v is None: return "0,00"
    if isinstance(v, str): return v
    return f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _parse_date_br(s: str) -> Optional[dt.date]:
    if not s: return None
    m = RE_DATE.search(s)
    if m:
        d, m_val, y = map(int, m.groups())
        try:
            return dt.date(y, m_val, d)
        except Exception:
            return None
    return None


def _ref_to_date(ref_str: str) -> Optional[dt.date]:
    if not ref_str: return None
    m = RE_REF.search(ref_str.upper())
    if m:
        mes_str, ano_str = m.groups()
        meses = {
            "JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6,
            "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12
        }
        return dt.date(int(ano_str), meses.get(mes_str, 1), 1)
    return None


def _is_numeric_field(h: str) -> bool:
    if h in _STRING_FIELDS: return False
    if any(k in h for k in _DATE_KEYS): return False
    if h.startswith("fat") or h in ("concCod", "obsValor"): return True
    return False


def _extract_text(pdf_path: str) -> str:
    full_text = []
    with pdfplumber.open(pdf_path) as pdf:
        for p in pdf.pages:
            text = p.extract_text(layout=True)
            if not text:
                text = p.extract_text()
            if text:
                full_text.append(text)
    return "\n".join(full_text)


def _get_instalacao(text: str) -> Optional[str]:
    """
    Extrai número da instalação com formatação (pontos e hífens)
    CORRIGIDO: Busca especificamente "N.º DA UNIDADE CONSUMIDORA" e mantém formatação
    """
    lines = text.splitlines()

    # Padrão 1: Procura pela linha "N.º DA UNIDADE CONSUMIDORA" seguida pelo número
    for i, ln in enumerate(lines):
        if "N.º DA UNIDADE CONSUMIDORA" in ln.upper() or "Nº DA UNIDADE CONSUMIDORA" in ln.upper():
            # Verifica as próximas 3 linhas
            for j in range(i, min(i + 4, len(lines))):
                next_line = lines[j]
                # Procura número com formatação (mantém pontos e hífens)
                # Formatos: X.XXX.XXX.XXX-XX ou XXX.XXX.XXX-XX ou XX.XXX.XXX-XX
                m = re.search(r"\b(\d{1,3}\.\d{3}\.\d{3}\.\d{3}-\d{2}|\d{1,3}\.\d{3}\.\d{3}-\d{2})\b", next_line)
                if m:
                    return m.group(1)

    # Padrão 2: Busca próximo a "UNIDADE CONSUMIDORA" (fallback)
    m = re.search(r"UNIDADE CONSUMIDORA[^\d]*(\d{1,3}\.\d{3}\.\d{3}\.\d{3}-\d{2}|\d{1,3}\.\d{3}\.\d{3}-\d{2})",
                  text, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1)

    # Padrão 3: Fallback - busca apenas números sem formatação específica, mas com pontos/hífens
    for ln in lines:
        if "UNIDADE CONSUMIDORA" in ln.upper():
            m = re.search(r"\b(\d{1,3}\.\d{3}\.\d{3}\.\d{3}-\d{2}|\d{1,3}\.\d{3}\.\d{3}-\d{2})\b", ln)
            if m:
                return m.group(1)

    return None


def _get_datas_e_total(text: str) -> Dict[str, Any]:
    """Extrai emissão, vencimento, referência, total e datas de leitura — cascata robusta."""
    out = {"emissao": None, "vcto": None, "ref": None, "total": 0.0, "ant": None, "atu": None}
    lines = text.splitlines()

    # ── Emissão ──────────────────────────────────────────────────────────────
    for pat in [
        r"Data\s+de\s+emiss[aã]o[:\s]*(\d{2}/\d{2}/\d{4})",
        r"Emiss[aã]o[:\s]*(\d{2}/\d{2}/\d{4})",
        r"(\d{2}\.\d{2}\.\d{4})\s+às",          # formato "11.03.2026 às 23:01:58"
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            raw = m.group(1).replace(".", "/")
            out["emissao"] = _parse_date_br(raw)
            break

    # ── Trio Referência + Vencimento + Total (padrão CEMIG B1) ───────────────
    # Linha: "MAR/2026  27/03/2026  107,44"  (pode estar mesclada com endereço)
    trio_re = re.compile(
        r"\b([A-Z]{3}/\d{4})\b.*?(\d{2}/\d{2}/\d{4}).*?([\d\.]+,\d{2})",
        re.IGNORECASE)

    # E1: cabeçalho "Referente a ... Vencimento ... Valor a pagar" + linha seguinte
    hdr = re.compile(r"Referente\s+a.*?Vencimento.*?Valor\s+a\s+pagar", re.IGNORECASE)
    for i, ln in enumerate(lines):
        if hdr.search(ln):
            for j in range(i, min(i + 5, len(lines))):
                m = trio_re.search(lines[j])
                if m:
                    out["ref"]   = _ref_to_date(m.group(1))
                    out["vcto"]  = _parse_date_br(m.group(2))
                    out["total"] = _br_money_to_float(m.group(3))
                    break
            if out["vcto"]: break

    # E2: trio direto em qualquer linha (cabeçalho pode estar mesclado com endereço)
    if not out["vcto"]:
        for ln in lines:
            m = trio_re.search(ln)
            if m:
                out["ref"]   = _ref_to_date(m.group(1))
                out["vcto"]  = _parse_date_br(m.group(2))
                out["total"] = _br_money_to_float(m.group(3))
                break

    # E3: VENCIMENTO isolado na linha ou seguinte
    if not out["vcto"]:
        for i, ln in enumerate(lines):
            if re.search(r"\bVENCIMENTO\b", ln, re.IGNORECASE):
                for src in (ln, lines[i+1] if i+1 < len(lines) else ""):
                    m = re.search(r"(\d{2}/\d{2}/\d{4})", src)
                    if m: out["vcto"] = _parse_date_br(m.group(1)); break
                if out["vcto"]: break

    # E4: linha "Código de Débito ... UC ... vcto ... R$ total"
    if not out["vcto"]:
        m = re.search(
            r"\d{12}\s+[\d\.\-]+\s+(\d{2}/\d{2}/\d{4})\s+R\$\s*([\d\.]+,\d{2})",
            text)
        if m:
            out["vcto"]  = _parse_date_br(m.group(1))
            out["total"] = _br_money_to_float(m.group(2))

    # ── Total complementar ────────────────────────────────────────────────────
    if not out["total"]:
        m = re.search(r"^TOTAL\s+([\d\.]+,\d{2})", text, re.IGNORECASE | re.MULTILINE)
        if m: out["total"] = _br_money_to_float(m.group(1))

    # ── Referência complementar ───────────────────────────────────────────────
    if not out["ref"]:
        m = re.search(r"Referente\s+a\s+([A-Z]{3}/\d{4})", text, re.IGNORECASE)
        if m: out["ref"] = _ref_to_date(m.group(1))
        if not out["ref"]:
            m = re.search(r"\b(JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)/(\d{4})\b",
                          text, re.IGNORECASE)
            if m: out["ref"] = _ref_to_date(m.group(0))

    # ── Datas de Leitura ─────────────────────────────────────────────────────
    ano_ref = (out["emissao"].year if out["emissao"]
               else (out["vcto"].year if out["vcto"] else dt.date.today().year))

    def ddmm(s):
        m2 = re.search(r"(\d{2})/(\d{2})", s or "")
        if not m2: return None
        dd, mo = int(m2.group(1)), int(m2.group(2))
        for a in (ano_ref, ano_ref - 1):
            try: return dt.date(a, mo, dd)
            except Exception: pass
        return None

    # L1: "Anterior Atual Nº de dias Próxima  dd/mm  dd/mm  N  dd/mm"
    m = re.search(
        r"Anterior\s+Atual[^\d]{0,300}?(\d{2}/\d{2})\s+(\d{2}/\d{2})\s+\d+",
        text, re.IGNORECASE | re.DOTALL)
    if m:
        out["ant"] = ddmm(m.group(1))
        out["atu"] = ddmm(m.group(2))

    # L2: LEITURA ANTERIOR/ATUAL explícitas
    if not out["ant"]:
        m = re.search(
            r"LEITURA\s+ANTERIOR[^\d]{0,60}(\d{2}/\d{2}(?:/\d{2,4})?)"
            r"[^\d]{0,120}LEITURA\s+ATUAL[^\d]{0,60}(\d{2}/\d{2}(?:/\d{2,4})?)",
            text, re.IGNORECASE | re.DOTALL)
        if m:
            out["ant"] = ddmm(m.group(1)) or _parse_date_br(m.group(1))
            out["atu"] = ddmm(m.group(2)) or _parse_date_br(m.group(2))

    # L3: par dd/mm consecutivos gap 5-45 dias
    if not out["ant"]:
        pares = re.findall(r"\b(\d{2}/\d{2})\b", text)
        ds = [d for d in (ddmm(p) for p in pares) if d]
        for i in range(len(ds) - 1):
            if 5 <= (ds[i+1] - ds[i]).days <= 45:
                out["ant"], out["atu"] = ds[i], ds[i+1]; break

    return out


def _processar_itens_fatura(text: str) -> Dict[str, float]:
    """
    Processa itens de fatura CEMIG B1.
    Cobre: Energia kWh, SCEE Isenta, GD compensada, Custo de Disponibilidade,
    CIP/COSIP, multas, juros, correção monetária, bandeira, DIC/FIC,
    impostos retidos (CSLL/COFINS/PIS/IRPJ) e tributo federal Art.64.
    """
    out = {
        "energia_kwh": 0.0, "energia_val": 0.0,
        "scee_kwh": 0.0,    "scee_val": 0.0,
        "gd_kwh": 0.0,      "gd_val": 0.0,
        "custo_disp": 0.0,
        "ilum": 0.0,  "multa": 0.0,  "juros": 0.0,  "correcao": 0.0,
        "multas_diversas_extra": 0.0,
        "bandeira": 0.0, "dic": 0.0, "fic": 0.0,
        "ret_csll": 0.0, "ret_cofins": 0.0, "ret_pis": 0.0, "ret_irpj": 0.0,
        "tributo_fed_perc": 0.0, "tributo_fed_val": 0.0,
    }

    # Layout BT micro-geração antigo: "Energia injetada kWh HFP" + "En comp. ISENTA"
    # formam par que se anula (net=0). Quando ambos aparecem juntos, ignoramos ambos.
    _lines_up = [l.upper() for l in text.splitlines()]
    has_injetada = any("INJET" in u and "ENERGIA" in u for u in _lines_up)
    has_isenta   = any("ISENTA" in u for u in _lines_up)
    gd_cancels   = has_injetada and has_isenta

    for ln in text.splitlines():
        up = ln.upper()
        monies = RE_MONEY.findall(ln)
        if not monies: continue
        v0   = _br_money_to_float(monies[0])    # 1º valor
        vlast = _br_money_to_float(monies[-1])   # último valor

        # ── Energia kWh (TE) ─────────────────────────────────────────────────
        # Cobre "Energia Elétrica" sem keyword "kWh" (layout BT micro-geração).
        # "COMPENSA" exclui "Energia compensada" (GD) de ser tratada como forward.
        if "ENERGIA" in up and "SCEE" not in up and "INJET" not in up and "COMPENSA" not in up and (
            "KWH" in up or re.search(r"EL.TRIC", up)
        ):
            # Consumo kWh = último inteiro da linha (após leituras anterior/atual)
            ints = re.findall(r"\b(\d{1,5})\b", ln)
            kwh = int(ints[-1]) if ints else 0
            if kwh > 0: out["energia_kwh"] += kwh
            out["energia_val"] += vlast

        # ── SCEE Isenta ──────────────────────────────────────────────────────
        elif "SCEE" in up:
            ints = re.findall(r"\b(\d{1,5})\b", ln)
            kwh = int(ints[-1]) if ints else 0
            if kwh > 0: out["scee_kwh"] += kwh
            out["scee_val"] += vlast

        # ── GD Compensada / Injetada ──────────────────────────────────────────
        # Par injetada+ISENTA se anula (net=0); pular ambas as linhas.
        elif gd_cancels and ("INJET" in up or "ISENTA" in up):
            pass

        elif "COMPENSADA" in up or ("INJET" in up and "ENERGIA" in up):
            ints = re.findall(r"\b(\d{1,5})\b", ln)
            kwh = int(ints[-1]) if ints else 0
            if kwh > 0: out["gd_kwh"] += kwh
            out["gd_val"] += abs(vlast)

        # ── Custo de Disponibilidade (B1 sem consumo real) ───────────────────
        elif "CUSTO" in up and "DISPONIB" in up:
            out["custo_disp"] = v0   # 1º valor = Valor(R$) na tabela CEMIG

        # ── CIP / COSIP / Iluminação Pública ─────────────────────────────────
        elif ("ILUM" in up or "CIP" in up or "COSIP" in up) and "COMPENSA" not in up:
            out["ilum"] += vlast

        # ── Multa ─────────────────────────────────────────────────────────────
        elif "MULTA" in up and "SUJEITAS" not in up and "PENALID" not in up:
            out["multa"] += abs(vlast)

        # ── Juros / Mora ─────────────────────────────────────────────────────
        elif re.search(r"\bJUROS\b|\bMORA\b", up):
            out["juros"] += abs(vlast)

        # ── Correção Monetária (IPCA/IGPM) ───────────────────────────────────
        elif "CORRE" in up and ("IPCA" in up or "IGPM" in up):
            out["correcao"] += float(vlast)

        # ── Cobrança Adicional REN 376 → multas diversas ────────────────────
        elif "COBRAN" in up and "REN" in up and "376" in up:
            out["multas_diversas_extra"] += abs(vlast)

        # ── Visita Técnica / Taxa → multas diversas ─────────────────────────
        elif re.search(r"VISITA\s+T[ÉE]CNICA|TAXA\s+DE\s+VISITA\s+T[ÉE]CNICA", ln, re.IGNORECASE):
            out["multas_diversas_extra"] += abs(vlast)

        # ── Bandeira Tarifária ────────────────────────────────────────────────
        elif "BANDEIRA" in up and "ADICIONAL" in up:
            out["bandeira"] += abs(vlast)

        # ── DIC / FIC compensação ─────────────────────────────────────────────
        elif "DIC" in up and "COMPENSA" in up:
            out["dic"] += abs(vlast)
        elif "FIC" in up and "COMPENSA" in up:
            out["fic"] += abs(vlast)

        # ── Impostos Retidos ─────────────────────────────────────────────────
        elif "IMPOSTO" in up and "RETIDO" in up:
            v = abs(vlast)
            if   "CSLL"  in up:               out["ret_csll"]   = v
            elif "COFINS" in up:              out["ret_cofins"] = v
            elif "PIS" in up or "PASEP" in up: out["ret_pis"]   = v
            elif "IRPJ"  in up:               out["ret_irpj"]   = v

    # ── Consumo kWh da seção Informações Técnicas ──────────────────────────────
    # Linha: "Energia kWh MEDIDOR leit_ant leit_atu constante CONSUMO"
    # Consumo = último inteiro; não tem valor monetário então é tratado aqui
    if out["energia_kwh"] == 0.0:
        for ln in text.splitlines():
            up = ln.upper()
            if "ENERGIA" in up and "KWH" in up and not RE_MONEY.findall(ln):
                ints = re.findall(r"\b(\d{1,6})\b", ln)
                if ints:
                    kwh = int(ints[-1])
                    if 0 < kwh < 99999:
                        out["energia_kwh"] = float(kwh)
                        break

    # ── Tributo Federal Art.64 Lei 9430 (linha de texto geral) ───────────────
    m = re.search(
        r"Reten[cç][aã]o\s+de\s+([\d,\.]+)\s*%.*?valor\s+R\$\s*([\d,\.]+)",
        text, re.IGNORECASE | re.DOTALL)
    if m:
        perc = m.group(1).replace(".", "").replace(",", ".")
        out["tributo_fed_perc"] = float(perc)
        out["tributo_fed_val"]  = _br_money_to_float(m.group(2))

    return out


def _get_fisco(text: str) -> Dict[str, float]:
    """
    Extrai dados da seção 'Reservado ao Fisco'.
    Linhas: 'ICMS base aliq val' | 'PASEP base aliq val' | 'COFINS base aliq val'
    Obs: PASEP e COFINS podem estar mescladas com o Histórico de Consumo (layout PDF).
    """
    out = {
        "icms": 0.0, "pis": 0.0, "cofins": 0.0,
        "aliq_icms": 0.0, "aliq_pis": 0.0, "aliq_cofins": 0.0,
        "base_nf": 0.0,
    }

    for ln in text.splitlines():
        vals = RE_MONEY.findall(ln)
        if len(vals) < 2: continue
        # Identifica o tributo pelo token na linha (pode estar no meio da linha)
        up = ln.upper()
        if re.search(r"\bICMS\b", up) and len(vals) >= 3:
            out["base_nf"]   = _br_money_to_float(vals[-3])
            out["aliq_icms"] = _br_money_to_float(vals[-2])
            out["icms"]      = _br_money_to_float(vals[-1])
        elif re.search(r"\bPASEP\b", up) and len(vals) >= 3:
            out["aliq_pis"] = _br_money_to_float(vals[-2])
            out["pis"]      = _br_money_to_float(vals[-1])
        elif re.search(r"\bCOFINS\b", up) and len(vals) >= 3:
            out["aliq_cofins"] = _br_money_to_float(vals[-2])
            out["cofins"]      = _br_money_to_float(vals[-1])

    # Fallback ICMS via linha TOTAL
    if not out["icms"]:
        m = re.search(r"^TOTAL\s+[\d\.,]+\s+[\d\.,]+\s+[\d\.,]+\s+([\d\.,]+)",
                      text, re.IGNORECASE | re.MULTILINE)
        if m: out["icms"] = _br_money_to_float(m.group(1))

    return out


def _get_endereco(text: str) -> Optional[str]:
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if re.search(r"\d{5}-\d{3}", ln):
            prev = lines[i - 1] if i > 0 else ""
            return f"{prev} {ln}".strip()
    return None


_OBS_MAPA_COMPILED: list = [
    # Compensação DIC Mensal (cod 58)
    (re.compile(r"compensa[cç][aã]o\s+dic\s+mensal|dic\s+mensal", re.IGNORECASE), "58", True),
    (re.compile(r"compensa[cç][aã]o\s+dic|compensa[cç][aã]o\s+fic", re.IGNORECASE), "11",  True),
    # Penalidade DIC/DMIC/FIC/DICRI (cod 149)
    (re.compile(r"penal(?:idade)?\s+(?:dic|dmic|fic|dicri)|penali[zs]a[cç][aã]o\s+(?:dic|dmic|fic|dicri)", re.IGNORECASE), "149", True),
    (re.compile(r"restitui[cç][aã]o\s+de\s+pagamento", re.IGNORECASE),               "109", True),
    (re.compile(r"\bmulta\b", re.IGNORECASE),                                          "6",   False),
    (re.compile(r"\bjuros?\s+(mora|por\s+atraso)\b|\bjuros\b", re.IGNORECASE),        "7",   False),
    (re.compile(r"corre[cç][aã]o\s+monet[aá]ria|atualiza[cç][aã]o\s+monet[aá]ria|ipca|igpm", re.IGNORECASE), "8", True),
    (re.compile(r"acerto\s+de\s+faturamento", re.IGNORECASE),                         "259", False),
    (re.compile(r"bandeira\s+amarela", re.IGNORECASE),                                "281", False),
    (re.compile(r"bandeira\s+vermelha\s+ii", re.IGNORECASE),                          "282", False),
    (re.compile(r"bandeira\s+vermelha", re.IGNORECASE),                               "271", False),
    (re.compile(r"saldo\s+para\s+o\s+pr[oó]ximo", re.IGNORECASE),                    "110", False),
    (re.compile(r"devolu[cç][aã]o\s+pagamento\s+indevido", re.IGNORECASE),            "59",  True),
    (re.compile(r"\bdevolução\b|\bdevolucao\b", re.IGNORECASE),                       "23",  True),
    (re.compile(r"\bparcelamento\b", re.IGNORECASE),                                  "100", False),
]


def _resolver_obs_b1(text: str, itens: dict) -> list:
    """Retorna lista de pares [(obsCod, obsValor), ...] ordenados por prioridade."""
    linhas = [re.sub(r"\s+", " ", ln).strip() for ln in (text or "").splitlines() if ln.strip()]
    encontrados: dict = {}
    for i, ln in enumerate(linhas):
        ln_lower = ln.lower()
        if "penalidades" in ln_lower:
            continue
        if "(multas)" in ln_lower or "(juros)" in ln_lower:
            continue
        if "sujeitas" in ln_lower and "vigentes" in ln_lower:
            continue
        if "atualiza" in ln_lower and "financeira" in ln_lower:
            continue
        if "@conselhoconsumidorescemig" in ln_lower:
            continue
        if "mês/ano" in ln_lower and "dias" in ln_lower:
            continue
        for prio, (pat, cod, eh_credito) in enumerate(_OBS_MAPA_COMPILED):
            if pat.search(ln):
                monies = RE_MONEY.findall(ln)
                v = _br_money_to_float(monies[-1]) if monies else None
                if v is None and i + 1 < len(linhas):
                    m2 = RE_MONEY.findall(linhas[i + 1])
                    if m2: v = _br_money_to_float(m2[-1])
                if v is not None and abs(v) > 0.005:
                    valor = -abs(v) if eh_credito else abs(v)
                    if cod in encontrados:
                        atual, prio_atual = encontrados[cod]
                        encontrados[cod] = (round(atual + valor, 2), min(prio_atual, prio))
                    else:
                        encontrados[cod] = (round(valor, 2), prio)
                # Uma linha deve gerar no máximo uma observação financeira.
                break
    if "11" not in encontrados and "58" not in encontrados and itens.get("dic", 0.0) + itens.get("fic", 0.0) > 0.005:
        encontrados["11"] = (-abs(itens.get("dic", 0.0) + itens.get("fic", 0.0)), 999)
    if "8" not in encontrados and itens.get("correcao", 0.0) > 0.005:
        encontrados["8"] = (-abs(itens["correcao"]), 999)
    if "6" not in encontrados and itens.get("multa", 0.0) > 0.005:
        encontrados["6"] = (itens["multa"], 999)
    return [(cod, val) for cod, (val, _p) in sorted(encontrados.items(), key=lambda x: x[1][1])]


def extrair_linha(pdf_path: str, headers: list, carimbo=None) -> list:
    """
    Extrator B1 CORRIGIDO com filtro de 10 dígitos
    """
    full_text = _extract_text(pdf_path)

    inst = _get_instalacao(full_text)
    dados_datas = _get_datas_e_total(full_text)
    itens = _processar_itens_fatura(full_text)
    fisco = _get_fisco(full_text)

    data = {}

    # Grava instalação na chave presente em headers (com ou sem acento)
    if "Instalacao" in headers: data["Instalacao"] = inst
    data["Instalação"] = inst
    data["Instalação"] = inst
    data["fatDataEmissao"] = dados_datas["emissao"]
    data["fatDataReferencia"] = dados_datas["ref"]
    data["fatDataVcto"] = dados_datas["vcto"]
    data["fatValorFatura"] = dados_datas["total"]

    data["fatDataLeituraAnterior"] = dados_datas["ant"]
    data["fatDataLeituraAtual"] = dados_datas["atu"]

    # ── Consumo kWh e valor faturado ────────────────────────────────────────
    kwh_registrado = int(itens["energia_kwh"] + itens["scee_kwh"])
    kwh_faturado = kwh_registrado
    if itens["energia_kwh"] == 0 and itens["scee_kwh"] > 0 and itens["custo_disp"] > 0:
        kwh_registrado += 100
        kwh_faturado += 100
    elif itens["energia_kwh"] == 0 and itens["scee_kwh"] == 0 and itens["custo_disp"] > 0:
        kwh_registrado = 0
        kwh_faturado = 100
    valor_energia = itens["energia_val"] + itens["scee_val"]

    # Custo de Disponibilidade (B1 sem consumo) → vai para fatConFPontaIndValorReais
    if itens["energia_val"] == 0 and itens["custo_disp"] > 0:
        valor_bt_total = valor_energia + itens["custo_disp"]
    elif valor_energia == 0 and dados_datas["total"] > 0:
        valor_bt_total = dados_datas["total"]
    else:
        valor_bt_total = valor_energia

    data["fatConFPontaIndValorReais"] = valor_bt_total
    data["fatConFPontaIndRegistrado"] = kwh_registrado
    data["fatConFPontaIndFaturado"]   = kwh_faturado

    # ── Outros itens ─────────────────────────────────────────────────────────
    data["fatIlumPublica"]    = itens["ilum"]
    data["fatMultas"]         = itens["multa"]
    # Juros + Correção + cobranças adicionais específicas → fatMultasDiversas
    data["fatMultasDiversas"] = round(
        itens["juros"] + itens["correcao"] + itens.get("multas_diversas_extra", 0.0), 2
    )
    data["fatValBandeira"]    = itens["bandeira"]
    data["fatDIC"]            = itens["dic"]
    data["fatFIC"]            = itens["fic"]

    # ── GD / Injetado ─────────────────────────────────────────────────────────
    data["fatConFPontaInjetadoValorReais"] = itens["gd_val"]
    data["fatConFPontaInjetadoRegistrado"] = int(itens["gd_kwh"])
    data["fatConFPontaInjetadoFaturado"]   = int(itens["gd_kwh"])
    data["fatConFPontaInjetadoUsina"]      = int(itens["gd_kwh"])
    data["fatConPontaInjetadoFaturado"]    = 0

    # ── Tributos fiscais ──────────────────────────────────────────────────────
    data["fatICMS"]             = fisco["icms"]
    data["fatDesIcmsAliquota"]  = fisco["aliq_icms"]
    if fisco["base_nf"] > 0:
        data["fatValorNotaFiscal"] = fisco["base_nf"]
    elif itens["custo_disp"] > 0:
        data["fatValorNotaFiscal"] = itens["custo_disp"]
    else:
        data["fatValorNotaFiscal"] = 0.0
    data["fatPIS"]              = fisco["pis"]
    data["fatDescPisAliquota"]  = fisco["aliq_pis"]
    data["fatCOFINS"]           = fisco["cofins"]
    data["fatDesCofinsAliquota"]= fisco["aliq_cofins"]

    # ── Impostos Retidos ──────────────────────────────────────────────────────
    data["fatDescPisPercRetImposto"]    = 0.65 if itens["ret_pis"] else 0
    data["fatDescCofinsPercRetImposto"] = 3.00 if itens["ret_cofins"] else 0
    data["fatDescCsllPercRetImposto"]   = 1.00 if itens["ret_csll"] else 0
    data["fatDescCsllValRetImposto"]    = -abs(itens["ret_csll"]) if itens["ret_csll"] else 0
    data["fatDescCofinsValRetImposto"]  = -abs(itens["ret_cofins"]) if itens["ret_cofins"] else 0
    data["fatDescPisValRetImposto"]     = -abs(itens["ret_pis"]) if itens["ret_pis"] else 0
    data["fatDescIrpjValRetImposto"]    = -abs(itens["ret_irpj"]) if itens["ret_irpj"] else 0
    data["fatDescIrpjPercRetImposto"]   = -1

    # ── Tributo Federal Art.64 ────────────────────────────────────────────────
    data["fatTributoFederalPerc"] = itens["tributo_fed_perc"]
    data["fatTributoFederalVal"]  = itens["tributo_fed_val"]

    data["concCod"] = "22"
    data["cadTarifaCod"] = "1"
    data["cadSubGrupoCod"] = "1"
    data["fatDataCadastro"] = dt.date.today()
    if carimbo: data["fatCarimbo"] = str(carimbo)

    # ── Observações (OBS) ─────────────────────────────────────────────────────
    data["_obs_list"] = _resolver_obs_b1(full_text, itens)
    for _i, (_c, _v) in enumerate(data["_obs_list"][:5], start=1):
        data[f"obsCod_{_i}"]   = _c
        data[f"obsValor_{_i}"] = round(float(_v), 2)

    data["ENDERECO"] = _get_endereco(full_text)

    m_doc = re.search(r"\d{3}\.\d{3}\.\d{3}-\d{2}|\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", full_text)
    if m_doc: data["CNPJ"] = m_doc.group(0)

    out = []
    for h in headers:
        v = data.get(h)

        if isinstance(v, (dt.date, dt.datetime)):
            v = f"'{v.strftime('%d/%m/%Y')}"
        elif isinstance(v, float):
            v = _fmt_pt(v)
        elif isinstance(v, int):
            v = str(v)

        if v is None or v == "":
            if _is_numeric_field(str(h)):
                v = "0"
            else:
                v = ""

        out.append(v)

    return out
