#!/usr/bin/env python3
# tarifas/b3_convencional.py
# CEMIG - B3 Convencional (BT) — EXTRAÇÃO 100% TEXTO/REGEX (SEM COORDENADAS)

from __future__ import annotations

import re
import datetime as dt
from typing import Dict, List, Optional, Tuple

import pdfplumber

# =============================================================================
# 1) Regex base / normalização
# =============================================================================

RE_DATE = re.compile(r"\b(\d{2})[\/\.](\d{2})[\/\.](\d{4})\b")
RE_REF = re.compile(r"\b([A-Z]{3})/(\d{4})\b", re.IGNORECASE)

_PT_MONTHS = {
    "janeiro": "JAN", "fevereiro": "FEV", "março": "MAR", "marco": "MAR", "abril": "ABR",
    "maio": "MAI", "junho": "JUN", "julho": "JUL", "agosto": "AGO", "setembro": "SET",
    "outubro": "OUT", "novembro": "NOV", "dezembro": "DEZ",
}


def _norm_ref_to_mmm_aaaa(ref_raw: str) -> Optional[str]:
    if not ref_raw:
        return None
    s = _norm_line(ref_raw).strip()
    m = re.search(r"\b([A-Za-zçÇ]{3,12})/(\d{4})\b", s)
    if m:
        mes = m.group(1).lower()
        ano = m.group(2)
        if len(mes) == 3 and mes.isalpha():
            return mes.upper() + "/" + ano
        mm = _PT_MONTHS.get(mes)
        return (mm + "/" + ano) if mm else (mes[:3].upper() + "/" + ano)

    m = re.search(r"\b(\d{1,2})/(\d{4})\b", s)
    if m:
        mo = int(m.group(1))
        ano = m.group(2)
        mm_list = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]
        if 1 <= mo <= 12:
            return mm_list[mo - 1] + "/" + ano
    return None


RE_CEP = re.compile(r"\b\d{5}-\d{3}\b")
RE_LINHA_DIGITAVEL_PART = re.compile(r"\b(\d{11}-\d)\b")
RE_MONEY = re.compile(r"(\(?\s*-?\s*(?:R\$\s*)?[\d\.]+,\d{2}\s*\)?)", re.IGNORECASE)


def _only_digits(s: str) -> str:
    return re.sub(r"\D+", "", s or "")


def _uc_keep_format(token: str) -> Optional[str]:
    if not token:
        return None
    t = (token or "").strip()
    t = t.strip(".,;:|")
    t = re.sub(r"\s+", "", t)
    t = t.replace("‑", "-").replace("–", "-").replace("—", "-")

    # Formatos válidos com formatação completa
    if re.fullmatch(r"\d{1}\.\d{3}\.\d{3}-\d{2}", t):
        return t
    if re.fullmatch(r"\d{3}\.\d{3}\.\d{3}-\d{2}", t):
        return t
    if re.fullmatch(r"\d{1,3}\.\d{3}\.\d{3}\.\d{3}-\d{2}", t):
        return t
    if re.fullmatch(r"\d{2}\.\d{3}\.\d{3}-\d{2}", t):
        return t
    if re.fullmatch(r"\d{9,}-\d{2}", t):
        digits = _only_digits(t)
        if not _is_compact_date(digits):
            return t

    # Para números sem formatação, aceita apenas 10 dígitos (não 12 do código de débito)
    digits = _only_digits(t)
    if len(digits) == 10 and not _is_compact_date(digits):
        return digits

    return None


def _parse_date_any(s: str) -> Optional[dt.date]:
    if not s:
        return None
    m = RE_DATE.search(s)
    if not m:
        return None
    dd, mm, yy = map(int, m.groups())
    try:
        return dt.date(yy, mm, dd)
    except Exception:
        return None


def _date_to_br(d: Optional[dt.date]) -> Optional[str]:
    return d.strftime("%d/%m/%Y") if d else None


def _br_money_to_float(s: str) -> Optional[float]:
    if not s:
        return None
    s = s.strip()
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1].strip()

    s = s.replace("R$", "").replace(" ", "")
    s = s.replace(".", "").replace(",", ".")
    try:
        v = float(s)
        return -v if neg else v
    except Exception:
        return None


def _fmt_pt(v: float) -> str:
    return "{:.2f}".format(float(v)).replace(".", ",")


def _norm_line(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _fixar_header_por_padrao(headers: List[str], must_have: List[str]) -> Optional[str]:
    must = [str(m).lower() for m in must_have]
    for h in headers:
        hl = str(h).lower()
        if all(m in hl for m in must):
            return h
    return None


def _is_compact_date(digits: str) -> bool:
    if not digits or len(digits) != 8:
        return False
    try:
        dd = int(digits[0:2])
        mm = int(digits[2:4])
        yy = int(digits[4:8])
    except Exception:
        return False
    return (1 <= dd <= 31) and (1 <= mm <= 12) and (1990 <= yy <= 2099)


def _ref_mmm_aaaa_to_br_first_day(ref: Optional[str]) -> Optional[str]:
    if not ref:
        return None
    m = RE_REF.search(ref.strip().upper())
    if not m:
        return None
    mes, ano = m.group(1).upper(), int(m.group(2))
    mapa = {
        "JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6,
        "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12
    }
    mm = mapa.get(mes)
    if not mm:
        return None
    return f"01/{mm:02d}/{ano}"


# =============================================================================
# 2) Leitura do PDF (texto)
# =============================================================================

def _extract_pages_text(pdf_path: str) -> List[str]:
    pages: List[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for p in pdf.pages:
            txt = ""
            try:
                txt = p.extract_text(layout=True) or ""
            except Exception:
                txt = ""
            if not txt:
                try:
                    txt = p.extract_text(x_tolerance=2, y_tolerance=2) or ""
                except Exception:
                    txt = ""
            pages.append(txt)
    return pages


def _full_text(pages: List[str]) -> str:
    return "\n".join([t for t in pages if t])


def _lines(full_text: str) -> List[str]:
    ls = [_norm_line(ln) for ln in (full_text or "").splitlines()]
    return [ln for ln in ls if ln]


# =============================================================================
# 3) Extrações por regex (com fallbacks por linha)
# =============================================================================

def _get_instalacao(full_text: str) -> Optional[str]:
    if not full_text:
        return None
    lines = [ln.strip() for ln in (full_text or "").splitlines() if (ln or "").strip()]
    num_re = re.compile(r"([\d\.\-]{9,}|\d{9,})")
    uc_header_re = re.compile(r"UNIDADE\s+CONSUMIDORA|INSTALA[C???][A??]O", re.IGNORECASE)

    # 1) Tabela do topo
    hdr_tbl = re.compile(r"C[o??]digo\s+de\s+D[e??]bito\s+Autom[a??]tico", re.IGNORECASE)
    has_uc = re.compile(r"(Unidade\s+Consumidora|Instala[??c][??a]o)", re.IGNORECASE)
    has_venc = re.compile(r"Vencimento", re.IGNORECASE)
    for i, ln in enumerate(lines):
        if hdr_tbl.search(ln) and has_uc.search(ln) and has_venc.search(ln):
            for j in range(1, 4):
                if i + j >= len(lines):
                    break
                ln2 = lines[i + j]
                m = re.search(r"\b(\d{6,})\b\s+([\d\.\-]{9,}|\d{9,})\s+\b(\d{2}/\d{2}/\d{4})\b", ln2, re.IGNORECASE)
                if m:
                    cand = _uc_keep_format(m.group(2))
                    if cand and len(_only_digits(cand)) == 10:
                        return cand

    # 2) Bloco vertical
    m_vertical = re.search(r"Unidade\s+Consumidora\s*([\d\.\-]{9,})", full_text, re.IGNORECASE | re.DOTALL)
    if m_vertical:
        cand = _uc_keep_format(m_vertical.group(1))
        if cand:
            return cand

    m_vert_2 = re.search(
        r"(?:Unidade\s+Consumidora|Instala[c??][a??]o)[^\d\n]*\n\s*([\d\.\-]{9,})",
        full_text,
        re.IGNORECASE,
    )
    if m_vert_2:
        cand = _uc_keep_format(m_vert_2.group(1))
        if cand:
            return cand

    # 3) Cabe?alho da unidade consumidora com data/hora na mesma linha
    header_re = re.compile(
        r"N\.?\s*[???o??]?\s*d[ae]\s*(UNIDADE\s*CONSUMIDORA|INSTALA[C???][A??]O)",
        re.IGNORECASE,
    )
    for i, ln in enumerate(lines):
        if header_re.search(ln):
            mm = num_re.search(ln)
            if mm:
                cand = _uc_keep_format(mm.group(1))
                if cand:
                    return cand
            for j in range(1, 7):
                if i + j >= len(lines):
                    break
                ln2 = lines[i + j]
                if re.search(r"\b\d{2}[\./]\d{2}[\./]\d{2,4}\b", ln2) and not _uc_keep_format(ln2):
                    continue
                if re.search(r"\b\d{2}:\d{2}:\d{2}\b", ln2):
                    continue
                mm2 = num_re.search(ln2)
                if mm2:
                    cand = _uc_keep_format(mm2.group(1))
                    if cand:
                        return cand

    # 4) Fallback por proximidade ao cabe?alho da UC/instala??o
    for i, ln in enumerate(lines):
        if uc_header_re.search(ln):
            for j in range(1, 5):
                if i + j >= len(lines):
                    break
                mm2 = num_re.search(lines[i + j])
                if not mm2:
                    continue
                cand = _uc_keep_format(mm2.group(1))
                if cand:
                    return cand

    # 5) Fallbacks finais
    for pat in [
        r"N\.?\s*[??o??]?\s*DA\s*UNIDADE\s*CONSUMIDORA\s*[:\-]?\s*([\d\.\-]{9,}|\d{9,})",
        r"N[??o??\.]?\s*DA\s*INSTALA[??C][??A]O\s*[:\-]?\s*([\d\.\-]{5,}|\d{5,})",
        r"\bInstala[??c][??a]o\s*[:\-]?\s*([\d\.\-]{5,}|\d{5,})\b",
    ]:
        m = re.search(pat, full_text, re.IGNORECASE)
        if m:
            cand = _uc_keep_format(m.group(1))
            if cand:
                return cand
    return None
def _get_emissao(full_text: str) -> Optional[dt.date]:
    m = re.search(r"Data\s+de\s+emiss[ãa]o:\s*([0-9]{2}[\/\.][0-9]{2}[\/\.][0-9]{4})", full_text, re.IGNORECASE)
    return _parse_date_any(m.group(1)) if m else None


def _get_referente_vcto_valor(full_text: str) -> Tuple[Optional[str], Optional[str], Optional[float]]:
    if not full_text: return None, None, None
    ls = _lines(full_text)
    header_re = re.compile(r"\bReferente\s+a\b.*\bVencimento\b.*\bValor\s+a\s+pagar\b", re.IGNORECASE)
    trio_re = re.compile(
        r"\b((?:[A-Z]{3}|[A-Za-zçÇ]{3,12}|\d{1,2})/\d{4})\b.*?\b(\d{2}/\d{2}/\d{4})\b.*?\b([\d\.]+,\d{2})\b",
        re.IGNORECASE)

    for i, ln in enumerate(ls):
        if header_re.search(ln):
            for j in range(i, min(i + 7, len(ls))):
                m = trio_re.search(ls[j])
                if m:
                    return _norm_ref_to_mmm_aaaa(m.group(1)), m.group(2), _br_money_to_float(m.group(3))

    for i, ln in enumerate(ls):
        if re.search(r"C[oó]digo\s+de\s+D[eé]bito\s+Autom[aá]tico", ln, re.IGNORECASE) and re.search(r"Vencimento", ln,
                                                                                                     re.IGNORECASE):
            for j in range(i, min(i + 4, len(ls))):
                m = re.search(r"\b\d{6,}\b\s+[\d\.\-]{9,}\s+(\d{2}/\d{2}/\d{4})\s+R\$\s*([\d\.]+,\d{2})", ls[j],
                              re.IGNORECASE)
                if m: return None, m.group(1), _br_money_to_float(m.group(2))

    m = trio_re.search(full_text)
    if m: return _norm_ref_to_mmm_aaaa(m.group(1)), m.group(2), _br_money_to_float(m.group(3))
    return None, None, None


def _get_datas_leitura(full_text: str, year_hint: int) -> Tuple[Optional[dt.date], Optional[dt.date]]:
    m = re.search(
        r"Anterior\s+Atual\s+N[ºo]\s+de\s+dias\s+Pr[óo]xima(?:[^\d]{0,260})(\d{2}/\d{2})\s+(\d{2}/\d{2})\s+\d+\s+\d{2}/\d{2}",
        full_text, re.IGNORECASE | re.DOTALL)
    if not m: return None, None

    def ddmm_to_date(ddmm: str) -> Optional[dt.date]:
        mm = re.search(r"(\d{2})/(\d{2})", ddmm or "")
        if not mm: return None
        dd, mo = int(mm.group(1)), int(mm.group(2))
        try:
            return dt.date(year_hint, mo, dd)
        except Exception:
            return None

    return ddmm_to_date(m.group(1)), ddmm_to_date(m.group(2))


def _get_modalidade_b3(full_text: str) -> bool:
    if re.search(r"\bConvencional\s+B3\b", full_text, re.IGNORECASE):
        return True

    normalized = full_text or ""
    has_bt_invoice_markers = (
        bool(re.search(r"\bClasse\s+Subclasse\b", normalized, re.IGNORECASE))
        and bool(re.search(r"\bEnergia\s+El[ée]trica\b", normalized, re.IGNORECASE))
        and bool(re.search(r"\bDescri[cç][aã]o\s+Quantidade\s+Tarifa/Pre[cç]o\b", normalized, re.IGNORECASE))
    )
    if not has_bt_invoice_markers:
        return False

    # Exclui residenciais B1 e layouts MT que precisam de outros extratores.
    if re.search(r"\bResidencial\b|\bB1\b", normalized, re.IGNORECASE):
        return False
    if re.search(
        r"\bTHS\b|\bHOROSAZON\b|\bDEMANDA\s+ATIVA\s+HFP\b|\bCOMPONENTE\s+FIO\b|\bCOMPONENTE\s+ENCARGO\b|\bTUSD\s+LIVRE\b",
        normalized,
        re.IGNORECASE,
    ):
        return False

    # Layout BT simples da CEMIG em 2023 pode vir sem o literal "Convencional B3"
    # e sem texto legível da classe/subclasse; ainda assim mantém o mesmo bloco
    # de descrição "Energia Elétrica" e não exibe marcadores residenciais ou MT.
    return True


def _get_consumo_kwh(full_text: str) -> Optional[int]:
    # Padrão 1: Consumo kWh direto
    m = re.search(r"\bConsumo\s+kWh\s+([\d\.]+)\b", full_text, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1).replace(".", ""))
        except Exception:
            return None

    # Padrão 2: Energia Elétrica kWh
    m = re.search(r"\bEnergia\s+El[ée]trica\s+kWh\s+([\d\.]+)\b", full_text, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1).replace(".", ""))
        except Exception:
            return None

    # Padrão 3: Tabela técnica - "Energia kWh" seguido de medição e consumo no final
    # Formato: Energia kWh [MEDIDOR] [LEIT_ANT] [LEIT_ATU] [CONST] [CONSUMO]
    m = re.search(r"\bEnergia\s+kWh\s+\S+\s+[\d\.]+\s+[\d\.]+\s+\d+\s+([\d\.]+)\b", full_text, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1).replace(".", ""))
        except Exception:
            return None

    return None


def _parse_kwh_int(s: str) -> Optional[int]:
    if not s: return None
    try:
        return int(str(s).replace(".", "").strip())
    except Exception:
        return None


def _get_consumo_kwh_historico(full_text: str, ref_mmm_aaaa: Optional[str]) -> Optional[int]:
    if not full_text or not ref_mmm_aaaa: return None
    m = RE_REF.search(ref_mmm_aaaa.strip().upper())
    if not m: return None
    mes = m.group(1).upper()
    ano2 = m.group(2)[-2:]
    pat = re.compile(rf"\b{re.escape(mes)}/{re.escape(ano2)}\b\s+([\d\.]+)\b", re.IGNORECASE)
    mm = pat.search(full_text)
    if not mm: return None
    return _parse_kwh_int(mm.group(1))


def _get_valores_faturados(full_text: str) -> Dict[str, Optional[float]]:
    out = {
        "kwh_energia_eletrica": None, "val_energia_eletrica": None,
        "kwh_scee_isenta": None, "val_scee_isenta": None,
        "kwh_comp_gd1": None, "val_comp_gd1": None,
        "kwh_faturado_sum": None, "val_faturado_sum": None,
    }
    ls = _lines(full_text)

    def parse_item(line: str) -> Tuple[Optional[int], Optional[float]]:
        toks = (line or "").split()
        kwh_idx = None
        for i, t in enumerate(toks):
            if t.lower() == "kwh":
                kwh_idx = i
                break
        if kwh_idx is None or kwh_idx + 3 >= len(toks):
            mq = re.search(r"\bkWh\b\s+([\d\.]+)\b", line, re.IGNORECASE)
            qtd = _parse_kwh_int(mq.group(1)) if mq else None
            mv = RE_MONEY.search(line)
            val = _br_money_to_float(mv.group(1)) if mv else None
            if qtd is None and len(toks) >= 3:
                qtd = _parse_kwh_int(toks[-3])
            if len(toks) >= 1 and re.fullmatch(r"\(?\s*-?[\d\.]+,\d{2}\s*\)?", toks[-1]):
                val = _br_money_to_float(toks[-1])
            return qtd, val
        qtd = _parse_kwh_int(toks[kwh_idx + 1])
        if qtd is None and kwh_idx + 2 < len(toks):
            qtd = _parse_kwh_int(toks[kwh_idx + 2])
        val = None
        for token in toks[kwh_idx + 2 :]:
            token_clean = token.strip()
            if not re.fullmatch(r"\(?\s*-?[\d\.]+,\d+\s*\)?", token_clean):
                continue
            decimal_part = token_clean.replace("(", "").replace(")", "").split(",")[-1]
            if len(decimal_part) == 2:
                val = _br_money_to_float(token_clean)
                break
        if val is None and kwh_idx + 3 < len(toks):
            val = _br_money_to_float(toks[kwh_idx + 3])
        if val is None and toks:
            val = _br_money_to_float(toks[-1])
        return qtd, val

    for ln in ls:
        lnl = ln.lower()
        if "energia el" in lnl and out["val_energia_eletrica"] is None:
            qtd, val = parse_item(ln)
            out["kwh_energia_eletrica"] = float(qtd) if qtd is not None else None
            out["val_energia_eletrica"] = val
            continue
        if (
            (
                "energia scee isenta" in lnl
                or "en comp. isenta" in lnl
                or "en comp isenta" in lnl
                or "en comp. kwh isenta" in lnl   # fix: variante real "En comp. kWh ISENTA"
                or "en comp kwh isenta" in lnl
            )
            and out["val_scee_isenta"] is None
        ):
            qtd, val = parse_item(ln)
            out["kwh_scee_isenta"] = float(qtd) if qtd is not None else None
            out["val_scee_isenta"] = val
            continue
        if (
            ("energia compensada gd i" in lnl or "energia injetada" in lnl)
            and out["val_comp_gd1"] is None
        ):
            qtd, val = parse_item(ln)
            out["kwh_comp_gd1"] = float(qtd) if qtd is not None else None
            out["val_comp_gd1"] = val
            continue

    if out["kwh_energia_eletrica"] is not None or out["kwh_scee_isenta"] is not None:
        out["kwh_faturado_sum"] = float(out["kwh_energia_eletrica"] or 0.0) + float(out["kwh_scee_isenta"] or 0.0)
    if out["val_energia_eletrica"] is not None or out["val_scee_isenta"] is not None:
        out["val_faturado_sum"] = float(out["val_energia_eletrica"] or 0.0) + float(out["val_scee_isenta"] or 0.0)
    return out


def _scee_is_mirrored_gd(valores_fat: Dict[str, Optional[float]]) -> bool:
    kwh_is = valores_fat.get("kwh_scee_isenta")
    val_is = valores_fat.get("val_scee_isenta")
    kwh_gd = valores_fat.get("kwh_comp_gd1")
    val_gd = valores_fat.get("val_comp_gd1")
    if None in (kwh_is, val_is, kwh_gd, val_gd):
        return False
    return abs(float(kwh_is) - float(kwh_gd)) <= 1 and abs(float(val_is) - abs(float(val_gd))) <= 0.05


def _get_nota_fiscal(full_text: str) -> Optional[str]:
    m = re.search(r"NOTA\s+FISCAL\s+N[ºo]\s+(\d+)", full_text, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _get_linha_digitavel(full_text: str) -> Optional[str]:
    parts = RE_LINHA_DIGITAVEL_PART.findall(full_text or "")
    if len(parts) >= 4: return " ".join(parts[:4])
    return " ".join(parts) if parts else None


def _get_debito_automatico(full_text: str) -> Optional[str]:
    m = re.search(r"C[oó]digo\s+de\s+D[eé]bito\s+Autom[aá]tico.*?[\r\n ]+(\d{6,})\s+\d{6,}\s+\d{2}/\d{2}/\d{4}",
                  full_text, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else None


def _get_endereco_cliente(full_text: str) -> Optional[str]:
    ls = _lines(full_text)
    bb_idx = None
    for i, ln in enumerate(ls):
        if ln.startswith("BB "):
            bb_idx = i
            break
    if bb_idx is None: return None
    blk: List[str] = []
    for j in range(bb_idx + 1, min(bb_idx + 16, len(ls))):
        if re.search(r"\bCNPJ\b", ls[j], re.IGNORECASE): break
        blk.append(ls[j])
    if not any(RE_CEP.search(x) for x in blk): return None
    return ", ".join(blk).strip(" ,;:-") if blk else None


# =============================================================================
# 4) Itens/Encargos: multa/juros/correção/saldo, DIC/FIC, bandeiras, CIP
# =============================================================================

def _amount_on_or_next_line(ls: List[str], i: int) -> Optional[float]:
    for j in (i, i + 1):
        if 0 <= j < len(ls):
            m = RE_MONEY.search(ls[j])
            if m: return _br_money_to_float(m.group(1))
    return None


def _find_item_values(full_text: str) -> Dict[str, float]:
    """
    CORREÇÃO REFORÇADA:
    Ignora linhas de aviso legal no rodapé de forma agressiva.
    Previne que valores da coluna de Histórico de Consumo (que ficam na mesma linha do rodapé)
    sejam capturados como multa ou juros.
    """
    ls = _lines(full_text)
    out: Dict[str, float] = {}

    pat_multa = re.compile(r"\bmulta\b", re.IGNORECASE)
    pat_juros = re.compile(r"\bjuro", re.IGNORECASE)
    pat_cor = re.compile(r"(corre[cç][aã]o|ipca|igpm|atualiza(ç|c)[aã]o)", re.IGNORECASE)
    pat_saldo = re.compile(r"saldo\s+para\s+o\s+pr[oó]ximo\s+m[eê]s", re.IGNORECASE)
    pat_dic = re.compile(r"compensa(ç|c)[aã]o\s+dic", re.IGNORECASE)
    pat_fic = re.compile(r"compensa(ç|c)[aã]o\s+fic", re.IGNORECASE)
    pat_devolucao = re.compile(r"\bdevolu[cç][aã]o\b", re.IGNORECASE)
    pat_ren376 = re.compile(r"cobran[cç]a\s+adicional\s+ren\s*376", re.IGNORECASE)
    pat_visita_tecnica = re.compile(
        r"visita\s+t[ée]cnica|taxa\s+de\s+visita\s+t[ée]cnica",
        re.IGNORECASE,
    )
    pat_dmic = re.compile(r"compensa.{0,4}o\s+dmic|dmic\s+mens", re.IGNORECASE)
    pat_b_am = re.compile(r"\bBandeira\s+Amarela\b", re.IGNORECASE)
    pat_b_vm = re.compile(r"\bBandeira\s+Vermelha\b", re.IGNORECASE)
    pat_b_gen = re.compile(r"\bBandeira\s+(?:Tarif[aá]ria|Verde|Escassez|Hídrica)\b", re.IGNORECASE)
    pat_cip = re.compile(r"Contrib.*Ilum.*Publica", re.IGNORECASE)
    pat_ret_csll = re.compile(r"Imposto\s+Retido\s*-\s*CSLL", re.IGNORECASE)
    pat_ret_cof = re.compile(r"Imposto\s+Retido\s*-\s*COFINS", re.IGNORECASE)
    pat_ret_pis = re.compile(r"Imposto\s+Retido\s*-\s*PIS/PASEP|Imposto\s+Retido\s*-\s*PASEP", re.IGNORECASE)
    pat_ret_irpj = re.compile(r"Imposto\s+Retido\s*-\s*IRPJ", re.IGNORECASE)
    pat_restituicao = re.compile(r"Restitui[cç][aã]o\s+de\s+Pagamento", re.IGNORECASE)

    multa = 0.0
    juros = 0.0
    cor = 0.0
    saldo = 0.0
    dic = 0.0
    fic = 0.0
    b1 = 0.0   # Bandeira Amarela acumulada
    b2 = 0.0   # Bandeira Vermelha acumulada (P1, P2, etc.)
    b_gen = 0.0  # Bandeiras genéricas acumuladas
    cip = 0.0
    devolucao = 0.0
    multas_diversas_extra = 0.0
    csll = None
    cof = None
    pis = None
    irpj = None
    restituicao = 0.0

    for i, ln in enumerate(ls):
        ln_lower = ln.lower()

        # [TRAVA DE SEGURANÇA MÁXIMA PARA AVISO LEGAL]
        # Pula qualquer linha que contenha palavras típicas do rodapé jurídico.
        # "sujeitas penalidades", "(multas)", "(juros)" com parênteses, "atualização financeira"
        if "penalidades" in ln_lower: continue
        if "(multas)" in ln_lower or "(juros)" in ln_lower: continue
        if "sujeitas" in ln_lower and "vigentes" in ln_lower: continue
        if "atualiza" in ln_lower and "financeira" in ln_lower: continue

        if pat_multa.search(ln):
            v = _amount_on_or_next_line(ls, i)
            if v is not None: multa += abs(v)
        if pat_juros.search(ln):
            v = _amount_on_or_next_line(ls, i)
            if v is not None: juros += abs(v)
        if pat_cor.search(ln):
            v = _amount_on_or_next_line(ls, i)
            if v is not None: cor += float(v)
        if pat_saldo.search(ln):
            v = _amount_on_or_next_line(ls, i)
            if v is not None: saldo += abs(v)
        if pat_dic.search(ln):
            v = _amount_on_or_next_line(ls, i)
            if v is not None: dic += abs(v)
        if pat_fic.search(ln):
            v = _amount_on_or_next_line(ls, i)
            if v is not None: fic += abs(v)
        if pat_devolucao.search(ln):
            v = _amount_on_or_next_line(ls, i)
            if v is not None: devolucao += abs(v)
        if pat_b_am.search(ln):
            v = _amount_on_or_next_line(ls, i)
            if v is not None: b1 += abs(v)
        if pat_b_vm.search(ln):
            v = _amount_on_or_next_line(ls, i)
            if v is not None: b2 += abs(v)
        if pat_b_gen.search(ln):
            v = _amount_on_or_next_line(ls, i)
            if v is not None: b_gen += abs(v)
        if pat_cip.search(ln):
            v = _amount_on_or_next_line(ls, i)
            if v is not None: cip = v
        if pat_ren376.search(ln):
            v = _amount_on_or_next_line(ls, i)
            if v is not None: multas_diversas_extra += abs(v)
        if pat_visita_tecnica.search(ln):
            v = _amount_on_or_next_line(ls, i)
            if v is not None: multas_diversas_extra += abs(v)
        if pat_dmic.search(ln):
            v = _amount_on_or_next_line(ls, i)
            if v is not None: multas_diversas_extra += abs(v)
        if pat_ret_csll.search(ln):
            v = _amount_on_or_next_line(ls, i)
            if v is not None: csll = v
        if pat_ret_cof.search(ln):
            v = _amount_on_or_next_line(ls, i)
            if v is not None: cof = v
        if pat_ret_pis.search(ln):
            v = _amount_on_or_next_line(ls, i)
            if v is not None: pis = v
        if pat_ret_irpj.search(ln):
            v = _amount_on_or_next_line(ls, i)
            if v is not None: irpj = v
        if pat_restituicao.search(ln):
            v = _amount_on_or_next_line(ls, i)
            if v is not None: restituicao += abs(v)

    out["multa"] = multa
    out["juros"] = juros
    out["correcao"] = cor
    out["saldo"] = saldo
    out["dic"] = dic
    out["fic"] = fic
    out["cip"] = cip
    out["devolucao"] = devolucao
    out["multas_diversas_extra"] = multas_diversas_extra
    # bandeira1 = total de todas as bandeiras somadas (Amarela + Vermelha P1/P2 + genéricas)
    total_bandeira = b1 + b2 + b_gen
    if total_bandeira > 0:
        out["bandeira1"] = total_bandeira
    # bandeira2 fica reservado para uso futuro separado se necessário
    if csll is not None: out["ret_csll"] = csll
    if cof is not None: out["ret_cofins"] = cof
    if pis is not None: out["ret_pis"] = pis
    if irpj is not None: out["ret_irpj"] = irpj
    if restituicao > 0: out["restituicao"] = restituicao
    return out


# =============================================================================
# 5) Quadro fiscal (Reservado ao Fisco)
# =============================================================================

def _get_fisco(full_text: str) -> Dict[str, Optional[float]]:
    out = {
        "base_nf": None, "icms_val": None, "pis_val": None, "cofins_val": None,
        "aliq_icms": None, "aliq_pis": None, "aliq_cofins": None,
    }
    m = re.search(r"\bICMS\s+([\d\.]+,\d{2})\s+([\d\.]+,\d{2})\s+([\d\.]+,\d{2})", full_text, re.IGNORECASE)
    if m:
        out["base_nf"] = _br_money_to_float(m.group(1))
        out["aliq_icms"] = _br_money_to_float(m.group(2))
        out["icms_val"] = _br_money_to_float(m.group(3))
    m = re.search(r"\bPASEP\s+([\d\.]+,\d{2})\s+([\d\.]+,\d{2})\s+([\d\.]+,\d{2})", full_text, re.IGNORECASE)
    if m:
        out["aliq_pis"] = _br_money_to_float(m.group(2))
        out["pis_val"] = _br_money_to_float(m.group(3))
    m = re.search(r"\bCOFINS\s+([\d\.]+,\d{2})\s+([\d\.]+,\d{2})\s+([\d\.]+,\d{2})", full_text, re.IGNORECASE)
    if m:
        out["aliq_cofins"] = _br_money_to_float(m.group(2))
        out["cofins_val"] = _br_money_to_float(m.group(3))
    return out


# =============================================================================
# 6) Conteúdo específico (saldo GD)
# =============================================================================

def _extrair_saldo_geracao(full_text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    txt = full_text or ""

    # Padrão 1: saldo separado por período — "X kWh FP/Único, Y kWh ponta"
    m = re.search(
        r"SALDO\s+ATUAL\s+DE\s+GERA[ÇC][AÃ]O:\s*([\d\.\,]+)\s*kWh\s*FP/[UÚ]nico[,\s]+([\d\.\,]+)\s*kWh\s*[Pp]onta",
        txt, re.IGNORECASE)
    if m:
        out["fatConFPontaInjetadoUsinaSaldoAcumulado"] = m.group(1).strip()
        out["fatConPontaInjetadoUsinaSaldoAcumulado"]  = m.group(2).strip()
        return out

    # Padrão 2: saldo único sem separação ponta/FP — "SALDO ATUAL DE GERAÇÃO: 4.960,00 kWh"
    # Neste caso o saldo vai integralmente para FPonta (B3 convencional não tem ponta)
    m2 = re.search(
        r"SALDO\s+ATUAL\s+DE\s+GERA[ÇC][AÃ]O:\s*([\d\.\,]+)\s*kWh",
        txt, re.IGNORECASE)
    if m2:
        out["fatConFPontaInjetadoUsinaSaldoAcumulado"] = m2.group(1).strip()
        out["fatConPontaInjetadoUsinaSaldoAcumulado"]  = "0"

    return out


# =============================================================================
# 7) Mapa de texto → value do select cb-dados-financeiros-obs (Consen)
# Cada chave é um fragmento normalizado (minúsculo, sem acentos) que pode aparecer
# no texto extraído do PDF.  O value é a string do <option value="..."> do HTML.
# =============================================================================

_OBS_MAPA: list = [
    (re.compile(r"restitui[cç][aã]o\s+de\s+pagamento", re.IGNORECASE), "109", True),
    (re.compile(r"corre[cç][aã]o\s+monet[aá]ria|atualiza[cç][aã]o\s+monet[aá]ria|ipca|igpm", re.IGNORECASE), "8", True),
    (re.compile(r"saldo\s+para\s+o\s+pr[oó]ximo\s+m[eê]s", re.IGNORECASE), "110", False),
    (re.compile(r"devolu[cç][aã]o\s+pagamento\s+indevido", re.IGNORECASE), "59", True),
    (re.compile(r"\bdevolu[cç][aã]o\b", re.IGNORECASE), "23", True),
    # Compensação DIC Mensal (cod 58)
    (re.compile(r"compensa[cç][aã]o\s+dic\s+mensal|dic\s+mensal", re.IGNORECASE), "58", True),
    # Compensação DIC/FIC genérica (cod 11)
    (re.compile(r"compensa[cç][aã]o\s+dic|compensa[cç][aã]o\s+fic", re.IGNORECASE), "11", True),
    # Penalidade DIC/DMIC/FIC/DICRI (cod 149)
    (re.compile(r"penal(?:idade)?\s+(?:dic|dmic|fic|dicri)|penali[zs]a[cç][aã]o\s+(?:dic|dmic|fic|dicri)", re.IGNORECASE), "149", True),
]


def _resolver_obs(full_text: str, itens: dict) -> list:
    """
    Retorna lista de pares [(obsCod, obsValor), ...] — todas as obs encontradas,
    ordenadas por prioridade do _OBS_MAPA.
    obsCod  = value do <select> cb-dados-financeiros-obs (ex: "109")
    obsValor = valor R$ (negativo = crédito ao cliente, positivo = débito)
    """
    linhas = [_norm_line(ln) for ln in (full_text or "").splitlines() if ln.strip()]

    # cod -> (valor, prioridade) — acumula valores por código preservando a menor prioridade
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

        for prio, (pat, cod, eh_credito) in enumerate(_OBS_MAPA):
            if pat.search(ln):
                v = None
                monies = RE_MONEY.findall(ln)
                if monies:
                    v = _br_money_to_float(monies[-1])
                elif i + 1 < len(linhas):
                    monies2 = RE_MONEY.findall(linhas[i + 1])
                    if monies2:
                        v = _br_money_to_float(monies2[-1])
                if v is not None and abs(v) > 0.005:
                    valor = -abs(v) if eh_credito else abs(v)
                    if cod in encontrados:
                        atual, prio_atual = encontrados[cod]
                        encontrados[cod] = (round(atual + valor, 2), min(prio_atual, prio))
                    else:
                        encontrados[cod] = (round(valor, 2), prio)
                # Uma linha deve gerar no máximo uma observação financeira.
                break

    # Fallbacks dos campos já calculados
    if "109" not in encontrados and itens.get("restituicao", 0.0) > 0.005:
        encontrados["109"] = (-abs(itens["restituicao"]), 999)
    if "8" not in encontrados and itens.get("correcao", 0.0) > 0.005:
        encontrados["8"] = (-abs(itens["correcao"]), 999)
    if "23" not in encontrados and itens.get("devolucao", 0.0) > 0.005:
        encontrados["23"] = (-abs(itens["devolucao"]), 999)
    if "110" not in encontrados and itens.get("saldo", 0.0) > 0.005:
        encontrados["110"] = (abs(itens["saldo"]), 999)
    # DIC: usa cod 11 como fallback genérico (58 só via regex no texto)
    if "11" not in encontrados and "58" not in encontrados and (itens.get("dic", 0.0) + itens.get("fic", 0.0)) > 0.005:
        encontrados["11"] = (-abs(itens.get("dic", 0.0) + itens.get("fic", 0.0)), 999)
    if "6" not in encontrados and itens.get("multa", 0.0) > 0.005:
        encontrados["6"] = (itens["multa"], 999)

    # Ordenar por prioridade e retornar lista de pares
    return [(cod, val) for cod, (val, _prio)
            in sorted(encontrados.items(), key=lambda x: x[1][1])]


# =============================================================================
# 8) Entrypoint
# =============================================================================

def extrair_linha(pdf_path: str, headers: list, carimbo=None) -> list:
    data: Dict[str, object] = {h: None for h in headers}
    pages = _extract_pages_text(pdf_path)
    full = _full_text(pages)

    if not _get_modalidade_b3(full):
        raise ValueError("Conta não parece ser Convencional B3 (não encontrou 'Convencional B3').")

    if "fatDescPisPercRetImposto" in data: data["fatDescPisPercRetImposto"] = "0,65"
    if "fatDescCofinsPercRetImposto" in data: data["fatDescCofinsPercRetImposto"] = "3,00"
    if "fatDescCsllPercRetImposto" in data: data["fatDescCsllPercRetImposto"] = "1,00"
    if "fatDescIrpjPercRetImposto" in data: data["fatDescIrpjPercRetImposto"] = "-1"

    inst = _get_instalacao(full)
    ref, vcto_str, val_total = _get_referente_vcto_valor(full)
    emiss = _get_emissao(full)

    # Grava instalação na chave presente em headers (com ou sem acento)
    if "Instalacao" in data: data["Instalacao"] = inst
    if "Instalação" in data: data["Instalação"] = inst
    if "fatDataEmissao" in data: data["fatDataEmissao"] = _date_to_br(emiss)
    if "fatDataVcto" in data: data["fatDataVcto"] = vcto_str
    if "fatValorFatura" in data: data["fatValorFatura"] = val_total
    if "fatDataReferencia" in data: data["fatDataReferencia"] = _ref_mmm_aaaa_to_br_first_day(ref)
    if "concCod" in data: data["concCod"] = "22"
    if "cadTarifaCod" in data: data["cadTarifaCod"] = "1"
    if "cadSubGrupoCod" in data: data["cadSubGrupoCod"] = "5"
    if "fatCarimbo" in data and carimbo is not None: data["fatCarimbo"] = str(carimbo)
    if "fatDataCadastro" in data: data["fatDataCadastro"] = dt.date.today().strftime("%d/%m/%Y")

    year_hint = emiss.year if emiss else (_parse_date_any(vcto_str).year if vcto_str else dt.date.today().year)
    ant, atu = _get_datas_leitura(full, int(year_hint))
    if "fatDataLeituraAnterior" in data: data["fatDataLeituraAnterior"] = _date_to_br(ant)
    if "fatDataLeituraAtual" in data: data["fatDataLeituraAtual"] = _date_to_br(atu)

    valores_fat = _get_valores_faturados(full)
    consumo_sum_tbl = None
    if valores_fat.get("kwh_faturado_sum") is not None:
        try:
            consumo_sum_tbl = int(round(float(valores_fat["kwh_faturado_sum"])))
        except Exception:
            consumo_sum_tbl = None
    consumo = _get_consumo_kwh(full)
    consumo_hist = _get_consumo_kwh_historico(full, ref)

    scee_mirrored_gd     = _scee_is_mirrored_gd(valores_fat)
    kwh_energia_eletrica = float(valores_fat.get("kwh_energia_eletrica") or 0.0)
    kwh_scee_isenta      = 0.0 if scee_mirrored_gd else float(valores_fat.get("kwh_scee_isenta") or 0.0)
    kwh_gd               = valores_fat.get("kwh_comp_gd1")
    kwh_gd_out           = float(kwh_gd) if kwh_gd is not None else 0.0
    m_disp = re.search(
        r"custo\s+de\s+disponibilidade[^\n]*?([\d\.]+,\d{2})",
        full,
        re.IGNORECASE,
    )
    v_disp = float(_br_money_to_float(m_disp.group(1)) or 0.0) if m_disp else 0.0

    # FPontaInd = Energia Elétrica + SCEE Isenta (todo kWh efetivamente consumido/faturado)
    # GD compensada NÃO entra aqui — vai apenas para FPontaInjetado
    tem_gd  = kwh_gd_out > 0
    tem_ele = kwh_energia_eletrica > 0
    tem_ise = kwh_scee_isenta > 0
    soma_100_kwh_disponibilidade = (not tem_ele) and tem_ise and v_disp > 0

    if tem_ele or tem_ise:
        # Fatura com itens explícitos: soma Energia + Isenta
        consumo_registrado = int(round(kwh_energia_eletrica + kwh_scee_isenta))
        consumo_faturado = consumo_registrado
        if soma_100_kwh_disponibilidade:
            consumo_registrado += 100
            consumo_faturado += 100
    elif tem_gd:
        # Apenas GD sem consumo próprio — usa fallbacks do histórico/tabela
        consumo_registrado = consumo or consumo_hist or consumo_sum_tbl or 0
        consumo_faturado = consumo_registrado
    elif v_disp > 0:
        # Sem consumo registrado, mas com mínimo faturável de disponibilidade.
        consumo_registrado = 0
        consumo_faturado = 100
    else:
        consumo_registrado = consumo_sum_tbl or consumo or consumo_hist
        consumo_faturado = consumo_registrado

    consumo_ind_registrado = int(round(consumo_registrado)) if consumo_registrado is not None else None
    consumo_ind_faturado = int(round(consumo_faturado)) if consumo_faturado is not None else None

    if "fatConFPontaIndFaturado"   in data: data["fatConFPontaIndFaturado"]   = consumo_ind_faturado
    if "fatConFPontaIndRegistrado" in data: data["fatConFPontaIndRegistrado"] = consumo_ind_registrado
    if "fatConFPontaIndFaturada"   in data: data["fatConFPontaIndFaturada"]   = consumo_ind_faturado

    if "ENDERECO" in data: data["ENDERECO"] = _get_endereco_cliente(full)
    if "NOTAFISCAL" in data: data["NOTAFISCAL"] = _get_nota_fiscal(full)
    if "fatCodigoBarras" in data: data["fatCodigoBarras"] = _get_linha_digitavel(full)
    h_deb = _fixar_header_por_padrao(headers, ["debito", "automatico"])
    if h_deb: data[h_deb] = _get_debito_automatico(full)

    itens = _find_item_values(full)
    multa = float(itens.get("multa", 0.0) or 0.0)
    juros = float(itens.get("juros", 0.0) or 0.0)
    cor = float(itens.get("correcao", 0.0) or 0.0)
    dic = float(itens.get("dic", 0.0) or 0.0)
    fic = float(itens.get("fic", 0.0) or 0.0)
    cip = float(itens.get("cip", 0.0) or 0.0)

    if "fatMultas" in data: data["fatMultas"] = (multa if abs(multa) > 0 else juros)
    if "fatMultasDiversas" in data: data["fatMultasDiversas"] = round(cor + float(itens.get("multas_diversas_extra", 0.0) or 0.0), 2)
    if "fatDIC" in data: data["fatDIC"] = dic
    if "fatFIC" in data: data["fatFIC"] = fic
    if "fatIlumPublica" in data: data["fatIlumPublica"] = cip
    if "fatValBandeira" in data: data["fatValBandeira"] = itens.get("bandeira1", 0.0)
    if "fatValBandeira2" in data: data["fatValBandeira2"] = itens.get("bandeira2", 0.0)

    if "fatConFPontaIndValorReais" in data:
        v_ele  = float(valores_fat.get("val_energia_eletrica") or 0.0)
        v_is   = float(valores_fat.get("val_scee_isenta") or 0.0)  # fix: sempre incluir SCEE Isenta; val_comp_gd1 subtrai em FPontaInjetado
        # Custo de disponibilidade: só entra quando não há consumo real nem isenta
        if v_ele > 0.0:
            v_disp = 0.0
        # fatConFPontaIndValorReais = Energia Elétrica + SCEE Isenta + Disponibilidade
        data["fatConFPontaIndValorReais"] = round(v_ele + v_is + v_disp, 2)

    if "fatConFPontaInjetadoValorReais" in data:
        v_gd = valores_fat.get("val_comp_gd1")
        # Injetado R$ = apenas compensação GD (valor absoluto)
        # SCEE Isenta NÃO entra aqui — já foi somada ao consumo FPontaInd
        data["fatConFPontaInjetadoValorReais"] = round(
            abs(float(v_gd)) if v_gd is not None else 0.0, 2
        )

    kwh_gd     = valores_fat.get("kwh_comp_gd1")
    kwh_gd_out = float(kwh_gd) if kwh_gd is not None else 0.0

    # Injetado registrado/faturado = apenas kWh da GD compensada
    # SCEE Isenta NÃO entra — já somou ao FPontaInd
    kwh_injetado = round(kwh_gd_out)
    if "fatConPontaInjetadoFaturado"    in data: data["fatConPontaInjetadoFaturado"]    = 0
    if "fatConFPontaInjetadoRegistrado" in data: data["fatConFPontaInjetadoRegistrado"] = kwh_injetado
    if "fatConFPontaInjetadoFaturado"   in data: data["fatConFPontaInjetadoFaturado"]   = kwh_injetado

    # Usina = kWh efetivamente gerados/injetados pela usina (pode divergir do compensado)
    # Usina = kWh injetados pela usina. Para B3 sem linha explícita, usa kwh_gd.
    _m_usina = re.search(
        r"energia\s+injetada\s+(?:usina\s+)?(?:fp/[uú]nico[,\s]+)?([0-9\.,]+)\s*kwh",
        full, re.IGNORECASE)
    kwh_usina_fp = int(round(float(_br_money_to_float(_m_usina.group(1)) or kwh_gd_out))) \
        if _m_usina else int(round(kwh_gd_out))
    if "fatConFPontaInjetadoUsina" in data: data["fatConFPontaInjetadoUsina"] = kwh_usina_fp

    # fatConPontaInjetadoUsina = 0 para B3 (sem período de ponta)
    if "fatConPontaInjetadoUsina" in data: data["fatConPontaInjetadoUsina"] = 0

    if "fatDescCsllValRetImposto" in data and "ret_csll" in itens: data["fatDescCsllValRetImposto"] = -abs(float(itens["ret_csll"] or 0))
    if "fatDescCofinsValRetImposto" in data and "ret_cofins" in itens: data["fatDescCofinsValRetImposto"] = -abs(float(itens["ret_cofins"] or 0))
    if "fatDescPisValRetImposto" in data and "ret_pis" in itens: data["fatDescPisValRetImposto"] = -abs(float(itens["ret_pis"] or 0))
    if "fatDescIrpjValRetImposto" in data and "ret_irpj" in itens: data["fatDescIrpjValRetImposto"] = -abs(float(itens["ret_irpj"] or 0))

    fisco = _get_fisco(full)
    base_nf = fisco.get("base_nf")
    if not base_nf and m_disp:
        base_nf = _br_money_to_float(m_disp.group(1))
    if "fatValorNotaFiscal" in data:
        data["fatValorNotaFiscal"] = base_nf
    else:
        h_base = _fixar_header_por_padrao(headers, ["base", "nf"])
        if h_base:
            data[h_base] = base_nf
    if "fatICMS" in data: data["fatICMS"] = fisco.get("icms_val")
    if "fatPIS" in data: data["fatPIS"] = fisco.get("pis_val")
    if "fatCOFINS" in data: data["fatCOFINS"] = fisco.get("cofins_val")
    if "fatDesIcmsAliquota" in data: data["fatDesIcmsAliquota"] = fisco.get("aliq_icms")
    if "fatDescPisAliquota" in data: data["fatDescPisAliquota"] = fisco.get("aliq_pis")
    if "fatDesCofinsAliquota" in data: data["fatDesCofinsAliquota"] = fisco.get("aliq_cofins")

    saldo_gd = _extrair_saldo_geracao(full)
    for k, v in saldo_gd.items():
        if k in data: data[k] = v

    # fatTributoFederal* não se aplica a BT CEMIG — retenções individuais são usadas.

    if "usuCod" in data and (data["usuCod"] is None or data["usuCod"] == ""): data["usuCod"] = 666

    # ── Observações (OBS) — lista para distribuição pelo OCR orquestrador ─────
    data["_obs_list"] = _resolver_obs(full, itens)
    # Preenche pares obsCod_N / obsValor_N diretamente (caso extrair_linha seja
    # chamado sem passar pelo OCR_Cemig.py)
    for _i, (_c, _v) in enumerate(data["_obs_list"][:5], start=1):
        if f"obsCod_{_i}"   in data: data[f"obsCod_{_i}"]   = _c
        if f"obsValor_{_i}" in data: data[f"obsValor_{_i}"] = round(float(_v), 2)

    if "fatDescIrpjPercRetImposto" in data: data["fatDescIrpjPercRetImposto"] = "-1"
    h_irpj = _fixar_header_por_padrao(headers, ["irpj", "perc", "ret"])
    if h_irpj: data[h_irpj] = "-1"
    if "fatDataReferencia" in data and not data["fatDataReferencia"]:
        data["fatDataReferencia"] = _ref_mmm_aaaa_to_br_first_day(ref) or "0"

    out: List[object] = []
    for h in headers:
        hl = str(h).lower()
        if ("irpj" in hl) and ("perc" in hl) and ("ret" in hl):
            out.append("-1")
            continue
        v = data.get(h)
        if isinstance(v, dt.date): v = _date_to_br(v)
        if isinstance(v, float): v = _fmt_pt(round(v, 2))
        if isinstance(v, int): v = str(v)
        if v is None or v == "":
            if str(h).startswith("fatData"):
                v = "0"
            else:
                v = 0 if str(h).startswith("fat") else "0"
        out.append(v)
    return out


def get_dados_extras(pdf_path: str) -> dict:
    """
    Retorna campos extras extraídos do PDF que não fazem parte dos headers
    da planilha, mas são necessários para a análise de OBS.
    Atualmente: restituição de pagamento.
    """
    pages = _extract_pages_text(pdf_path)
    full = _full_text(pages)
    itens = _find_item_values(full)

    extras = {}
    if itens.get("restituicao") is not None:
        extras["_restituicaoPagamento"] = itens["restituicao"]
    return extras
