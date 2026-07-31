# tarifas/ths_verde_a4.py
# CEMIG - THS Verde A4

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple

import re
import pdfplumber

# ==========================================
# 1. HELPERS E CONSTANTES
# ==========================================

_RE_MONEY = re.compile(r"-?[\d\.]+,\d{2}")
_RE_DATE_ANY = re.compile(r"(\d{2})[\/\.](\d{2})[\/\.](\d{4})")

_MONTHS_PT = {
    "JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12
}

_STRING_FIELDS = {
    "Instalação", "CNPJ", "usuCod", "cadObsCodigo", "cadTarifaCod", "cadSubGrupoCod",
    "ENDERECO", "NOTAFISCAL", "fatCodigoBarras", "Instalacao Antiga"
}

_DATE_KEYS = ("Data", "Vcto", "Emissao", "Leitura", "Referencia", "Próxima", "Proxima")


def _br_money_to_float(s: str) -> Optional[float]:
    if not s or not isinstance(s, str): return None
    s = s.strip()
    if not s: return None
    neg = s.startswith("-")
    s2 = s.replace("-", "").replace(".", "").replace(",", ".")
    try:
        v = float(s2)
        return -v if neg else v
    except Exception:
        return None


def _br_int_from_thousand_str(s: str) -> Optional[int]:
    if not s or not isinstance(s, str): return None
    s = s.strip()
    if not s: return None
    try:
        return int(s.replace(".", ""))
    except Exception:
        return None


def _parse_date_any(s: str) -> Optional[date]:
    if not s: return None
    m = _RE_DATE_ANY.search(s)
    if not m: return None
    dd, mm, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(yyyy, mm, dd)
    except Exception:
        return None


def _date_to_br(d: Optional[date]) -> Optional[str]:
    if not d: return None
    return d.strftime("%d/%m/%Y")


def _ref_to_date(ref: str) -> Optional[date]:
    if not ref: return None
    ref = ref.strip().upper()
    m = re.search(r"\b([A-Z]{3})\/(\d{4})\b", ref)
    if not m: return None
    mon = _MONTHS_PT.get(m.group(1))
    if not mon: return None
    y = int(m.group(2))
    return date(y, mon, 1)


def _infer_date_ddmm(ddmm: str, year: int) -> Optional[date]:
    if not ddmm: return None
    m = re.search(r"(\d{2})\/(\d{2})", ddmm)
    if not m: return None
    dd, mm = int(m.group(1)), int(m.group(2))
    try:
        return date(year, mm, dd)
    except Exception:
        return None


def _json_safe(v):
    if isinstance(v, datetime): return v.strftime("%d/%m/%Y")
    if isinstance(v, date): return v.strftime("%d/%m/%Y")
    return v


# ✅ ESTA FUNÇÃO FALTAVA OU ESTAVA FORA DE ORDEM
def _norm_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _norm_key(k: str) -> str:
    k = (k or "").strip().lower()
    k = re.sub(r"\s+", "", k)
    k = re.sub(r"[^a-z0-9]", "", k)
    return k


def _fixar_por_padrao(headers_list, includes: list, value):
    inc = [_norm_key(x) for x in includes]
    for h in headers_list:
        hk = _norm_key(h)
        if all(i in hk for i in inc): return h
    return None


def _is_date_field(h: str) -> bool:
    if not isinstance(h, str): return False
    if h.startswith("fatData"): return True
    if "Data" in h or "Vcto" in h: return True
    return False


def _is_numeric_field(h: str) -> bool:
    if not isinstance(h, str) or not h: return False
    if h in _STRING_FIELDS: return False
    if _is_date_field(h): return False
    if h.startswith("fat"): return True
    if h in ("concCod", "obsValor"): return True
    return False


def _is_date_candidate(digits: str) -> bool:
    if len(digits) != 8: return False
    try:
        v = int(digits)
        d, m, a = int(digits[:2]), int(digits[2:4]), int(digits[4:])
        if 1 <= d <= 31 and 1 <= m <= 12 and 1990 <= a <= 2035: return True
    except Exception:
        pass
    return False


# ==========================================
# 2. EXTRAÇÃO DE TEXTO PDF
# ==========================================

def _extract_pages_text(pdf_path: str) -> List[str]:
    with pdfplumber.open(pdf_path) as pdf:
        pages = []
        for p in pdf.pages:
            t = ""
            try:
                t = p.extract_text(x_tolerance=2, y_tolerance=2) or ""
            except Exception:
                t = ""
            if not t:
                try:
                    t = p.extract_text(layout=True) or ""
                except Exception:
                    t = ""
            pages.append(t)
        return pages


def _full_text(pages: List[str]) -> str:
    return "\n".join([p for p in pages if p])


def _detectar_subgrupo_consen(full: str) -> str:
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


# ==========================================
# 3. EXTRATORES ESPECÍFICOS
# ==========================================

def _get_unidade_consumidora(full: str) -> Optional[str]:
    if not full: return None

    def _norm_uc(raw: str) -> Optional[str]:
        if not raw: return None
        s = raw.strip()
        s = re.sub(r"\s+", "", s)
        nd = len(re.sub(r"\D+", "", s))
        # Aceita apenas 10 dígitos (não 12 do código de débito)
        if nd != 10: return None
        if re.fullmatch(r"\d{2}[\/\.]\d{2}[\/\.]\d{4}", raw.strip()): return None
        # Preserva formatação se tiver pontos e hífen
        if '.' in s and '-' in s:
            return s
        return s

    # Padrão 1: Busca padrão completo com formatação
    m = re.search(r"N\.\s*[ºo°]?\s*da\s+Unidade\s+Consumidora[^\d]*(\d{1,3}\.\d{3}\.\d{3}\.\d{3}-\d{2}|\d{1,3}\.\d{3}\.\d{3}-\d{2})",
                  full, re.IGNORECASE)
    if m:
        return m.group(1)

    # Padrão 2: Busca próximo a "N.º da Unidade Consumidora"
    m = re.search(r"N\.\s*[ºo°]?\s*da\s+Unidade\s+Consumidora\s*[:\-]\s*([0-9][0-9\.\-\s]{5,})", full, re.IGNORECASE)
    if m:
        got = _norm_uc(m.group(1))
        if got: return got

    # Padrão 3: Tabela (débito automático | UC | vencimento)
    m = re.search(
        r"C[oó]digo\s+de\s+D[eé]bito\s+Autom[aá]tico\s+N\.\s*[ºo°]?\s*da\s+Unidade\s+Consumidora\s+Vencimento\s+Total\s+a\s+pagar"
        r"\s*[\r\n ]+(\d{6,})\s+([0-9][0-9\.\-\s]{5,})\s+(\d{2}/\d{2}/\d{4})", full, re.IGNORECASE)
    if m:
        got = _norm_uc(m.group(2))
        if got: return got

    # Padrão 4: Busca linha por linha
    lines = [re.sub(r"\s+", " ", (ln or "")).strip() for ln in (full or "").splitlines()]
    lines = [ln for ln in lines if ln]
    for i, ln in enumerate(lines):
        if re.search(r"N\.\s*[ºo°]?\s*DA\s*UNIDADE\s*CONSUMIDORA", ln, re.IGNORECASE):
            # Procura nas próximas linhas
            for j in range(i, min(i + 5, len(lines))):
                # Primeiro tenta encontrar com formatação
                m_fmt = re.search(r"\b(\d{1,3}\.\d{3}\.\d{3}\.\d{3}-\d{2}|\d{1,3}\.\d{3}\.\d{3}-\d{2})\b", lines[j])
                if m_fmt:
                    return m_fmt.group(1)
                # Se não encontrar com formatação, tenta sem
                mm = re.search(r"\b([0-9][0-9\.\-\s]{5,})\b", lines[j])
                if mm:
                    got = _norm_uc(mm.group(1))
                    if got: return got
    return None


def _get_instalacao(full_text: str) -> Optional[str]:
    if not full_text: return None
    uc = _get_unidade_consumidora(full_text)
    if uc: return uc

    m = re.search(r"Unidade\s+Consumidora\s*[:\-]?\s*(\d{7,})", full_text, re.IGNORECASE)
    if m: return m.group(1).strip()

    lines = [re.sub(r"\s+", " ", ln).strip() for ln in full_text.splitlines() if ln.strip()]
    keywords = ["INSTALAÇÃO", "UNIDADE CONSUMIDORA", "Nº DA INSTALAÇÃO", "Nº DO CLIENTE"]

    for i, line in enumerate(lines):
        line_upper = line.upper()
        if any(k in line_upper for k in keywords):
            m_inline = re.search(r"\b(\d{7,})\b", line)
            if m_inline:
                cand = m_inline.group(1)
                if not _is_date_candidate(cand): return cand

            for j in range(1, 7):
                if i + j >= len(lines): break
                next_line = lines[i + j]
                if re.search(r"\d{2}/\d{2}/\d{4}", next_line): continue
                if "VENCIMENTO" in next_line.upper() or "TOTAL" in next_line.upper(): continue

                m_next = re.search(r"\b(\d{7,})\b", next_line)
                if m_next:
                    cand = m_next.group(1)
                    if not _is_date_candidate(cand): return cand
    return None


def _get_emissao(full: str) -> Optional[date]:
    m = re.search(r"Data\s+de\s+emissão:\s*(\d{2}[\/\.]\d{2}[\/\.]\d{4})", full, re.IGNORECASE)
    if m: return _parse_date_any(m.group(1))
    m = re.search(r"Data\s+de\s+emissão:\s*(\d{2}\.\d{2}\.\d{4})", full, re.IGNORECASE)
    if m: return _parse_date_any(m.group(1))
    return None


def _get_referencia_vcto_valor(full: str) -> Tuple[Optional[str], Optional[date], Optional[float]]:
    ref, vcto, valor = None, None, None
    m = re.search(r"\b([A-Z]{3}\/\d{4})\s+(\d{2}[\/\.]\d{2}[\/\.]\d{4})\s+([\d\.]+,\d{2})\b", full)
    if m:
        ref = m.group(1)
        vcto = _parse_date_any(m.group(2))
        valor = _br_money_to_float(m.group(3))
    return ref, vcto, valor


def _get_datas_leitura_from_pdf(pdf_path: str, year_hint: Optional[int]) -> Tuple[
    Optional[date], Optional[date], Optional[int], Optional[date]]:
    ano = year_hint or date.today().year

    def _get_datas_leitura(txt: str, yr: int):
        m = re.search(
            r"Anterior\s+Atual\s+N[ºo]\s+de\s+dias\s+Pr[óo]xima.*?(\d{2}\/\d{2})\s+(\d{2}\/\d{2})\s+(\d+)\s+(\d{2}\/\d{2})",
            txt, re.IGNORECASE | re.DOTALL)
        if not m: return None, None, None, None
        return _infer_date_ddmm(m.group(1), yr), _infer_date_ddmm(m.group(2), yr), int(m.group(3)), _infer_date_ddmm(
            m.group(4), yr)

    try:
        with pdfplumber.open(pdf_path) as pdf:
            p1 = pdf.pages[0]
            t = (p1.crop((60, 175, 580, 210)).extract_text() or "")
    except Exception:
        t = ""

    ant, atu, dias, prox = _get_datas_leitura(t, ano)
    if ant or atu or prox: return ant, atu, dias, prox

    with pdfplumber.open(pdf_path) as pdf:
        full = "\n".join(p.extract_text() or "" for p in pdf.pages)
    return _get_datas_leitura(full, ano)


def _get_ilum_publica(page1: str) -> Optional[float]:
    m = re.search(r"Contrib\s+Ilum\s+Publica.*?\s(-?[\d\.]+,\d{2})\b", page1, re.IGNORECASE)
    return _br_money_to_float(m.group(1)) if m else None


def _get_client_cnpj(full: str) -> Optional[str]:
    cnpjs = re.findall(r"CNPJ\s+([0-9\.\*]+\/[0-9\*]+-[0-9\*]+)", full)
    for c in cnpjs:
        if not c.startswith("06.981.180"): return c
    return cnpjs[0] if cnpjs else None


def _get_codigo_barras(full: str) -> Optional[str]:
    parts = re.findall(r"\d{11}-\d", full or "")
    if parts and len(parts) >= 4: return " ".join(parts[:4])
    if parts: return " ".join(parts)
    return None


def _get_valor_nota_fiscal_ths(page1_text: str, full_text: str = "") -> Optional[str]:
    src = page1_text or ""
    if not src and full_text: src = full_text
    if not src: return None

    ini = re.search(r"Valores\s+Faturados\b", src, re.IGNORECASE)
    if not ini: return None

    sub = src[ini.end():]
    fim = re.search(r"Hist[oó]rico\s+de\s+Consumo\b|Reservado\s+ao\s+Fisco\b", sub, re.IGNORECASE)
    if fim: sub = sub[:fim.start()]

    for ln in sub.splitlines():
        s = (ln or "").strip()
        if not s: continue
        if re.match(r"^TOTAL\b", s, re.IGNORECASE):
            vals = re.findall(r"-?[\d\.]+,\d{2}", s)
            floats = []
            for v in vals:
                f = _br_money_to_float(v)
                if f is not None: floats.append(f)
            if floats:
                max_val = max(floats)
                return f"{max_val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return None


def _get_nota_fiscal(full: str) -> Optional[str]:
    m = re.search(r"NOTA\s+FISCAL\s+N[ºo°]\s*(\d+)", full or "", re.IGNORECASE)
    return m.group(1).strip() if m else None


def _get_endereco_cliente(full: str) -> Optional[str]:
    if not full: return None
    lines = [ln.strip() for ln in (full or "").splitlines() if ln.strip()]

    idx_cnpj = None
    for i, ln in enumerate(lines):
        if re.search(r"\bCNPJ\b", ln, re.IGNORECASE):
            if "06.981.180/0001-16" in ln: continue
            idx_cnpj = i
            break
    if idx_cnpj is None: return None

    block = lines[max(0, idx_cnpj - 3): idx_cnpj]
    cleaned = []
    for ln in block:
        s = ln
        s = re.split(r"\bNOTA\s+FISCAL\b", s, flags=re.IGNORECASE)[0]
        s = re.split(r"\b[A-Z]{3}/\d{4}\b", s.upper())[0]
        s = re.sub(r"\b\d{2}/\d{2}/\d{4}\b", "", s)
        s = re.sub(r"-?[\d\.]+,\d{2}", "", s)
        s = _norm_spaces(s).strip(" -")
        if s: cleaned.append(s)

    cleaned2 = []
    for ln in cleaned:
        ln2 = re.sub(r"^Cliente:\s*", "", ln, flags=re.IGNORECASE).strip()
        if ln2: cleaned2.append(ln2)

    return _norm_spaces(" - ".join(cleaned2)).strip(" -") or None


# ==========================================
# 4. ITENS THS
# ==========================================

@dataclass
class THSItem:
    name: str
    unit: Optional[str]
    qty: Optional[int]
    valor: Optional[float]
    raw: str


def _parse_ths_item_line(ln: str) -> Optional[THSItem]:
    s = (ln or "").strip()
    if not s: return None
    s_low = s.lower()

    if " kwh " in f" {s_low} ":
        m = re.search(r"\bkWh\s+([\d\.]+)\s+[\d\.]+,\d+\s+(-?[\d\.]+,\d{2})\b", s, re.IGNORECASE)
        if m:
            qty = _br_int_from_thousand_str(m.group(1))
            valor = _br_money_to_float(m.group(2))
            name = re.split(r"\bkWh\b", s, flags=re.IGNORECASE)[0].strip()
            return THSItem(name=name, unit="kWh", qty=qty, valor=valor, raw=s)

    if " kvarh " in f" {s_low} ":
        m = re.search(r"\bkVArh\s+([\d\.]+)\s+[\d\.]+,\d+\s+(-?[\d\.]+,\d{2})\b", s, re.IGNORECASE)
        if m:
            qty = _br_int_from_thousand_str(m.group(1))
            valor = _br_money_to_float(m.group(2))
            name = re.split(r"\bkVArh\b", s, flags=re.IGNORECASE)[0].strip()
            return THSItem(name=name, unit="kVArh", qty=qty, valor=valor, raw=s)

    if " kw " in f" {s_low} ":
        m = re.search(r"\bkW\s+([\d\.]+)\s+[\d\.]+,\d+\s+(-?[\d\.]+,\d{2})\b", s, re.IGNORECASE)
        if m:
            qty = _br_int_from_thousand_str(m.group(1))
            valor = _br_money_to_float(m.group(2))
            name = re.split(r"\bkW\b", s, flags=re.IGNORECASE)[0].strip()
            return THSItem(name=name, unit="kW", qty=qty, valor=valor, raw=s)

    return None


def _get_ths_items_page1(page1: str) -> List[THSItem]:
    wanted_prefix = (
        "Demanda Ativa HFP", "Energia Ativa HFP", "Energia Ativa HP", "Demanda Geração",
        "Energia SCEE", "Energia compensada", "Encargo Cta Covid", "Enc Cta Esc Hídr", "Desconto Comp. Fio",
        "Ajuste de Desconto C. Fio", "Energia Reativa Excedente", "Energia Reativa Indutiva", "Reativo Excedente",
        "Energia Reativa HFP", "Restituição de Pagamento",
    )
    out: List[THSItem] = []
    for ln in (page1 or "").splitlines():
        ln = ln.strip()
        if not ln: continue
        it = None
        if any(ln.startswith(p) for p in wanted_prefix):
            it = _parse_ths_item_line(ln)
        if it:
            out.append(it)
            continue

        if "Desconto" in ln and "Fio" in ln:
            vals = re.findall(r"-?[\d\.]+,\d{2}", ln)
            if vals:
                val = _br_money_to_float(vals[-1])
                out.append(THSItem(name=ln.split("  ")[0], unit=None, qty=None, valor=val, raw=ln))
                continue
        if ln.startswith("Multa") or ln.startswith("Juros") or ln.startswith("Correção"):
            vals = re.findall(r"-?[\d\.]+,\d{2}", ln)
            if vals:
                val = _br_money_to_float(vals[-1])
                out.append(THSItem(name=ln.split("  ")[0], unit=None, qty=None, valor=val, raw=ln))
    return out


def _get_bandeira(page1: str) -> Optional[float]:
    m = re.search(r"\bBandeira\b.*?(-?[\d\.]+,\d{2})\b", page1, re.IGNORECASE)
    return _br_money_to_float(m.group(1)) if m else None


def _get_dic_fic(page1: str, full: str) -> Tuple[Optional[float], Optional[float]]:
    dic, fic = None, None
    m_dic = re.search(r"\bDIC\s*[=:]?\s*([\d\.]+,\d{2})", full, re.IGNORECASE)
    if m_dic: dic = _br_money_to_float(m_dic.group(1))
    m_fic = re.search(r"\bFIC\s*[=:]?\s*([\d\.]+,\d{2})", full, re.IGNORECASE)
    if m_fic: fic = _br_money_to_float(m_fic.group(1))
    return dic, fic


def _extract_fisco_from_page1(pdf_path: str) -> Dict[str, Optional[float]]:
    out = {
        "base_icms": None, "aliq_icms": None, "icms_val": None,
        "base_pis": None, "aliq_pis": None, "pis_val": None,
        "base_cofins": None, "aliq_cofins": None, "cofins_val": None,
    }
    try:
        with pdfplumber.open(pdf_path) as pdf:
            p1 = pdf.pages[0]
            cropped = p1.crop((230, 668, 580, 715))
            t = (cropped.extract_text() or "")
    except Exception:
        t = ""

    for ln in t.splitlines():
        s = ln.strip()
        if not s: continue
        up = s.upper()
        nums = re.findall(r"[\d\.]+,\d{2}", s)
        if up.startswith("ICMS") and len(nums) >= 3:
            out["base_icms"] = _br_money_to_float(nums[0])
            out["aliq_icms"] = _br_money_to_float(nums[1])
            out["icms_val"] = _br_money_to_float(nums[2])
        elif up.startswith("PASEP") and len(nums) >= 3:
            out["base_pis"] = _br_money_to_float(nums[0])
            out["aliq_pis"] = _br_money_to_float(nums[1])
            out["pis_val"] = _br_money_to_float(nums[2])
        elif up.startswith("COFINS") and len(nums) >= 3:
            out["base_cofins"] = _br_money_to_float(nums[0])
            out["aliq_cofins"] = _br_money_to_float(nums[1])
            out["cofins_val"] = _br_money_to_float(nums[2])
    return out


def _extract_retidos_from_page1(page1: str) -> Dict[str, Optional[float]]:
    def grab(label: str) -> Optional[float]:
        m = re.search(label + r"\s+(-?[\d\.]+,\d{2})\b", page1, re.IGNORECASE)
        return _br_money_to_float(m.group(1)) if m else None

    return {
        "csll": grab(r"Imposto\s+Retido\s*-\s*CSLL"),
        "cofins": grab(r"Imposto\s+Retido\s*-\s*COFINS"),
        "pis": grab(r"Imposto\s+Retido\s*-\s*PIS\/PASEP"),
        "irpj": grab(r"Imposto\s+Retido\s*-\s*IRPJ"),
    }


def _extract_usina_table_robust(page2: str) -> Dict[str, Optional[float]]:
    out = {"ponta_inj": None, "ponta_saldo": None, "fponta_inj": None, "fponta_saldo": None}
    matches_ponta = list(re.finditer(r"(?:Ponta|PONTA)\s+.*?([\d\.]+,\d{2})\s+.*?([\d\.]+,\d{2})", page2, re.DOTALL))
    if matches_ponta:
        m = matches_ponta[-1]
        out["ponta_inj"] = _br_money_to_float(m.group(1))
        out["ponta_saldo"] = _br_money_to_float(m.group(2))
    matches_fponta = list(
        re.finditer(r"(?:F\.|F)\s*(?:Ponta|PONTA)\s+.*?([\d\.]+,\d{2})\s+.*?([\d\.]+,\d{2})", page2, re.DOTALL))
    if matches_fponta:
        m = matches_fponta[-1]
        out["fponta_inj"] = _br_money_to_float(m.group(1))
        out["fponta_saldo"] = _br_money_to_float(m.group(2))
    return out


def _extract_saldo_text(text: str) -> Dict[str, Optional[float]]:
    out = {"ponta": None, "fponta": None}
    if not text: return out
    m = re.search(
        r"SALDO\s+ATUAL\s+DE\s+GERAÇÃO:.*?\b([\d\.]+,\d{2})\s*kWh\s+PONTA.*?([\d\.]+,\d{2})\s*kWh\s+F\.?\s*PONTA", text,
        re.IGNORECASE | re.DOTALL)
    if m:
        out["ponta"] = _br_money_to_float(m.group(1))
        out["fponta"] = _br_money_to_float(m.group(2))
    return out


def _extract_dem_energia_from_page2(page2: str) -> Dict[str, Optional[int]]:
    out = {
        "dem_hfp_reg": None, "dem_hfp_contr": None, "dem_hfp_fat": None, "dem_hp_reg": None,
        "kwh_hfp_reg": None, "kwh_hfp_fat": None, "kwh_hp_reg": None, "kwh_hp_fat": None,
        "dem_ger_reg": None, "inj_hfp_reg": None, "inj_hfp_fat": None, "inj_hp_reg": None, "inj_hp_fat": None,
        "ufer_hfp_reg": None, "ufer_hp_reg": None,
        "usina_ponta_inj": None, "usina_fponta_inj": None,
    }
    dms = list(re.finditer(r"Demanda\s+ativa\s+(\d+)(?:\s+(\d+))?(?:\s+(\d+))?", page2, re.IGNORECASE))
    if len(dms) >= 1:
        out["dem_hfp_reg"] = int(dms[0].group(1))
        if dms[0].group(2) and dms[0].group(3):
            # 3 números: reg, contr, fat
            out["dem_hfp_contr"] = int(dms[0].group(2))
            out["dem_hfp_fat"]   = int(dms[0].group(3))
        elif dms[0].group(2):
            # 2 números: reg, fat — contratada vem de "Demanda Fora Ponta"
            out["dem_hfp_fat"] = int(dms[0].group(2))
    if len(dms) >= 2: out["dem_hp_reg"] = int(dms[1].group(1))

    # "Demanda Fora Ponta X" — contratada HFP (quando não está na linha "Demanda ativa")
    m_contr_fp = re.search(r"Demanda\s+Fora\s+Ponta\s+(\d+)", page2, re.IGNORECASE)
    if m_contr_fp and out["dem_hfp_contr"] is None:
        val = int(m_contr_fp.group(1))
        if val > 0:
            out["dem_hfp_contr"] = val

    inj = list(re.finditer(r"Demanda\s+injetada\s+([\d\.]+)", page2, re.IGNORECASE))
    if inj: out["dem_ger_reg"] = _br_int_from_thousand_str(inj[0].group(1))

    ens = list(re.finditer(r"Energia\s+ativa\s+([\d\.]+)\s+([\d\.]+)", page2, re.IGNORECASE))
    if len(ens) >= 1:
        out["kwh_hfp_reg"] = _br_int_from_thousand_str(ens[0].group(1))
        out["kwh_hfp_fat"] = _br_int_from_thousand_str(ens[0].group(2))
    if len(ens) >= 2:
        out["kwh_hp_reg"] = _br_int_from_thousand_str(ens[1].group(1))
        out["kwh_hp_fat"] = _br_int_from_thousand_str(ens[1].group(2))

    injs = list(re.finditer(r"Energia\s+Injetada\s+([\d\.]+)(?:.*?\s+([\d\.]+))?", page2, re.IGNORECASE))
    if len(injs) >= 1:
        out["inj_hfp_reg"] = _br_int_from_thousand_str(injs[0].group(1))
        if injs[0].group(2): out["inj_hfp_fat"] = _br_int_from_thousand_str(injs[0].group(2))
    if len(injs) >= 2:
        out["inj_hp_reg"] = _br_int_from_thousand_str(injs[1].group(1))
        if injs[1].group(2): out["inj_hp_fat"] = _br_int_from_thousand_str(injs[1].group(2))

    ufers = list(re.finditer(r"Energia\s+reativa\s*-\s*UFER\s+([\d\.]+)", page2, re.IGNORECASE))
    if len(ufers) >= 1: out["ufer_hfp_reg"] = _br_int_from_thousand_str(ufers[0].group(1))
    if len(ufers) >= 2: out["ufer_hp_reg"] = _br_int_from_thousand_str(ufers[1].group(1))

    matches_ponta = list(re.finditer(r"(?:Ponta|PONTA)\s+.*?([\d\.]+,\d{2})\s+.*?([\d\.]+,\d{2})", page2, re.DOTALL))
    if matches_ponta: out["usina_ponta_inj"] = _br_money_to_float(matches_ponta[-1].group(1))

    matches_fponta = list(
        re.finditer(r"(?:F\.|F)\s*(?:Ponta|PONTA)\s+.*?([\d\.]+,\d{2})\s+.*?([\d\.]+,\d{2})", page2, re.DOTALL))
    if matches_fponta: out["usina_fponta_inj"] = _br_money_to_float(matches_fponta[-1].group(1))
    return out


# ==========================================
# 5. FUNÇÃO PRINCIPAL
# ==========================================

def extrair_linha(pdf_path: str, headers: list, carimbo=None) -> list:
    pages = _extract_pages_text(pdf_path)
    full = _full_text(pages)
    page1 = pages[0] if len(pages) > 0 else full
    page2 = pages[1] if len(pages) > 1 else ""

    data: Dict[str, object] = {}

    inst = _get_instalacao(full)
    data["Instalação"] = inst
    data["Instalacao"] = inst
    data["fatDataEmissao"] = _get_emissao(full)
    ref, vcto, valor = _get_referencia_vcto_valor(full)
    data["fatDataVcto"] = vcto
    data["fatValorFatura"] = valor
    data["concCod"] = 22
    data["fatDataCadastro"] = datetime.now()
    ref_date = _ref_to_date(ref) if ref else None
    data["fatDataReferencia"] = ref_date

    year_hint = None
    if ref_date:
        year_hint = ref_date.year
    elif vcto:
        year_hint = vcto.year
    elif data.get("fatDataEmissao"):
        year_hint = data["fatDataEmissao"].year

    ant, atu, dias, prox = _get_datas_leitura_from_pdf(pdf_path, year_hint)
    data["fatDataLeituraAnterior"] = ant
    data["fatDataLeituraAtual"] = atu

    data["fatIlumPublica"] = _get_ilum_publica(page1)
    data["CNPJ"] = _get_client_cnpj(full)
    data["ENDERECO"] = _get_endereco_cliente(full)
    data["NOTAFISCAL"] = _get_nota_fiscal(full)

    h_end = _fixar_por_padrao(headers, ["enderec"], data["ENDERECO"])
    if h_end: data[h_end] = data["ENDERECO"]
    h_nf = _fixar_por_padrao(headers, ["nota", "fiscal"], data["NOTAFISCAL"])
    if h_nf: data[h_nf] = data["NOTAFISCAL"]
    if "Instalacao Antiga" in headers: data["Instalacao Antiga"] = None

    data["fatCodigoBarras"] = _get_codigo_barras(full)
    if carimbo is not None: data["fatCarimbo"] = carimbo
    if "usuCod" in data and (data["usuCod"] is None or data["usuCod"] == ""): data["usuCod"] = 666

    data["cadTarifaCod"] = 1
    data["cadSubGrupoCod"] = _detectar_subgrupo_consen(full)

    bandeira = _get_bandeira(page1)
    items = _get_ths_items_page1(page1)

    dem_val = 0.0
    dem_ger_val = 0.0
    dem_ger_qty = 0
    comp_hfp_qty = None
    comp_hfp_val = 0.0
    scee_hfp_qty = 0
    scee_hfp_val = 0.0
    scee_hp_qty = 0
    scee_hp_val = 0.0
    val_conta_covid = 0.0
    qty_conta_covid = 0
    val_escassez = 0.0
    qty_escassez = 0
    val_desc_fio = 0.0
    val_multas = 0.0
    val_juros = 0.0
    val_correcao = 0.0
    val_reativo_exc_hfp = 0.0
    val_reativo_exc_hp = 0.0

    qty_reativo_exc_hfp = 0
    qty_reativo_exc_hp = 0

    for it in items:
        if it.name.startswith("Demanda Ativa HFP") and it.valor is not None: dem_val += float(it.valor)
        if it.name.startswith("Demanda Geração"):
            if it.valor is not None: dem_ger_val += float(it.valor)
            if it.qty is not None: dem_ger_qty += int(it.qty)
        if it.name.startswith("Energia SCEE HFP"):
            if it.qty is not None: scee_hfp_qty += int(it.qty)
            if it.valor is not None: scee_hfp_val += abs(float(it.valor))
        if it.name.startswith("Energia SCEE HP"):
            if it.qty is not None: scee_hp_qty += int(it.qty)
            if it.valor is not None: scee_hp_val += abs(float(it.valor))
        if it.name.startswith("Energia compensada"):
            if it.qty is not None: comp_hfp_qty = int(it.qty)
            if it.valor is not None: comp_hfp_val += float(it.valor)

        # Multas (soma ao campo fatMultas)
        if it.name.startswith("Multa") and it.valor:
            val_multas += float(it.valor)
        if it.name.startswith("Juros") and it.valor: val_juros += float(it.valor)
        if it.name.startswith("Correção") and it.valor: val_correcao += float(it.valor)

        # Reativos
        if "Reativa" in it.name and ("Excedente" in it.name or "HFP" in it.name):
            if it.valor is not None: val_reativo_exc_hfp += float(it.valor)
            if it.qty is not None: qty_reativo_exc_hfp += int(it.qty)
        if "Reativa" in it.name and "HP" in it.name and "HFP" not in it.name:
            if it.valor is not None: val_reativo_exc_hp += float(it.valor)
            if it.qty is not None: qty_reativo_exc_hp += int(it.qty)

        if "Conta Covid" in it.name:
            if it.valor: val_conta_covid += float(it.valor)
            if it.qty: qty_conta_covid += int(it.qty)
        if "Esc Hídr" in it.name or "Escassez" in it.name:
            if it.valor: val_escassez += float(it.valor)
            if it.qty: qty_escassez += int(it.qty)
        if "Desconto" in it.name and "Fio" in it.name:
            if it.valor: val_desc_fio += float(it.valor)

    # Captura Restituição de Pagamento (segundo loop separado para clareza)
    val_restituicao = None
    for it in items:
        if "Restitui" in it.name and it.valor is not None:
            val_restituicao = abs(float(it.valor))
            break

    if dem_val != 0.0: data["fatDemFPontaIndValorReais"] = round(dem_val, 2)
    if dem_ger_val != 0.0: data["fatDemFPontaGeracaoValorReais"] = round(dem_ger_val, 2)
    if dem_ger_qty != 0: data["fatDemFPontaGeracao"] = dem_ger_qty

    data["fatContaCovid"] = qty_conta_covid
    data["fatContaCovidValorReais"] = round(val_conta_covid, 2)
    data["fatEscassezHidrica"] = qty_escassez
    data["fatEscassezHidricaValorReais"] = round(val_escassez, 2)
    data["fatDescontoFio"] = round(val_desc_fio, 2)
    data["fatConCreditoTUSDFPontaValorReais"] = round(val_desc_fio, 2)
    data["fatMultas"] = round(val_multas, 2)
    data["fatMultasDiversas"] = round(val_juros + val_correcao, 2)
    data["fatConFPontaIndExcValorReais"] = round(val_reativo_exc_hfp, 2)

    # ✅ Inclusão dos campos de Retenção Percentual (Zerados ou calculados se houver regra futura)
    data["fatDescConsumoPercRetImposto"] = 0
    data["fatDescDemandaPercRetImposto"] = 0

    q_hfp, q_hp, v_hfp_raw, v_hp_raw = None, None, None, None
    for it in items:
        if it.name.startswith("Energia Ativa HFP"):
            q_hfp, v_hfp_raw = it.qty, it.valor
        elif it.name.startswith("Energia Ativa HP"):
            q_hp, v_hp_raw = it.qty, it.valor

    # Os valores de HP e HFP já vêm da fatura sem a bandeira embutida —
    # não aplicar desconto proporcional aqui (causaria duplo desconto).
    if bandeira is not None:
        data["fatValBandeira"] = round(float(bandeira), 2)
        # fatValBandeira2 NAO recebe a bandeira — e campo distinto, nao duplicar

    if v_hp_raw is not None: data["fatConPontaValorReais"] = round(float(v_hp_raw) + scee_hp_val, 2)
    if v_hfp_raw is not None: data["fatConFPontaIndValorReais"] = round(float(v_hfp_raw) + scee_hfp_val, 2)

    vnf_str = _get_valor_nota_fiscal_ths(page1, full)
    if vnf_str:
        vnf_float = _br_money_to_float(vnf_str)
        data["fatValorNotaFiscal"] = vnf_float if vnf_float is not None else vnf_str
    else:
        if data.get("fatValorFatura"): data["fatValorNotaFiscal"] = data["fatValorFatura"]

    d2 = _extract_dem_energia_from_page2(page2)

    # Fallback contratada em page1 (ex: "Demanda Fora Ponta 57" aparece na página 1 em B3/HFP)
    if d2.get("dem_hfp_contr") is None:
        m_fp1 = re.search(r"Demanda\s+Fora\s+Ponta\s+(\d+)", page1, re.IGNORECASE)
        if m_fp1:
            val = int(m_fp1.group(1))
            if val > 0:
                d2["dem_hfp_contr"] = val

    # Fallback registrada/faturada em page1 (ex: "Demanda Ativa HFP kW 57" em B3/HFP)
    if d2.get("dem_hfp_reg") is None:
        m_hfp1 = re.search(r"Demanda\s+Ativa\s+HFP\s+(?:s/\s*ICMS\s+)?kW\s+(\d+)", page1, re.IGNORECASE)
        if m_hfp1:
            d2["dem_hfp_reg"] = int(m_hfp1.group(1))
            if d2.get("dem_hfp_fat") is None:
                d2["dem_hfp_fat"] = d2["dem_hfp_reg"]

    if d2.get("dem_hp_reg") is not None: data["fatDemPontaRegistrada"] = d2["dem_hp_reg"]
    if d2.get("dem_hfp_reg") is not None: data["fatDemFPontaIndRegistrada"] = d2["dem_hfp_reg"]
    if d2.get("dem_hfp_contr") is not None: data["fatDemContratadaFPonta"] = d2["dem_hfp_contr"]
    if d2.get("dem_hfp_fat") is not None: data["fatDemFPontaIndFaturada"] = d2["dem_hfp_fat"]

    # Fallback TUSD: "DEMANDA DE DISTRIBUICAO TUSD kW X" — clientes livre/TUSD sem contratada explícita
    if not data.get("fatDemFPontaIndRegistrada"):
        m_tusd = re.search(r"DEMANDA\s+DE\s+DISTRIBUICAO\s+TUSD\s+kW\s+([\d,\.]+)", full, re.IGNORECASE)
        if m_tusd:
            kw = _br_money_to_float(m_tusd.group(1))
            if kw:
                data["fatDemFPontaIndRegistrada"] = kw
                if not data.get("fatDemContratadaFPonta"):
                    data["fatDemContratadaFPonta"] = kw
                if not data.get("fatDemFPontaIndFaturada"):
                    data["fatDemFPontaIndFaturada"] = kw

    # Fallback formato especial: "Demanda - KW X,XXX" (ex: clientes com regime especial)
    if not data.get("fatDemFPontaIndRegistrada"):
        m_dem_kw = re.search(r"Demanda\s*-\s*KW\s+([\d,\.]+)", full, re.IGNORECASE)
        if m_dem_kw:
            kw = _br_money_to_float(m_dem_kw.group(1))
            if kw:
                data["fatDemFPontaIndRegistrada"] = kw
                if not data.get("fatDemContratadaFPonta"):
                    data["fatDemContratadaFPonta"] = kw
                if not data.get("fatDemFPontaIndFaturada"):
                    data["fatDemFPontaIndFaturada"] = kw

    if d2.get("dem_ger_reg") is not None: data["fatDemFPontaGeracaoRegistrada"] = d2["dem_ger_reg"]

    if d2.get("kwh_hp_reg") is not None:
        data["fatConPontaRegistrado"] = d2["kwh_hp_reg"]
        data["fatConPontaFaturado"] = d2["kwh_hp_fat"]
    if d2.get("kwh_hfp_reg") is not None:
        data["fatConFPontaIndRegistrado"] = d2["kwh_hfp_reg"]
        data["fatConFPontaIndFaturado"] = d2["kwh_hfp_fat"]

    ponta_inj_qty = scee_hp_qty or d2.get("inj_hp_fat") or d2.get("inj_hp_reg") or 0
    fponta_inj_qty = scee_hfp_qty or d2.get("inj_hfp_fat") or d2.get("inj_hfp_reg") or 0
    data["fatConPontaInjetadoRegistrado"] = ponta_inj_qty
    data["fatConPontaInjetadoFaturado"] = ponta_inj_qty
    # SCEE HP ISENTA só representa injeção líquida quando existe "Energia Ativa HP"
    # na fatura. Sem ela, o par SCEE ISENTA + Energia compensada se anula (net=0).
    data["fatConPontaInjetadoValorReais"] = round(scee_hp_val, 2) if (scee_hp_val and v_hp_raw is not None) else 0
    data["fatConFPontaInjetadoRegistrado"] = fponta_inj_qty
    data["fatConFPontaInjetadoFaturado"] = fponta_inj_qty
    data["fatConFPontaInjetadoValorReais"] = round(scee_hfp_val, 2) if (scee_hfp_val and v_hfp_raw is not None) else 0
    data["fatConFPontaInjetadoUsina"] = fponta_inj_qty

    saldo_text = _extract_saldo_text(full)

    if d2.get("usina_ponta_inj") is not None:
        data["fatConPontaInjetadoUsina"] = d2["usina_ponta_inj"]
    else:
        data["fatConPontaInjetadoUsina"] = d2.get("inj_hp_reg") or 0

    if saldo_text["ponta"] is not None:
        data["fatConPontaInjetadoUsinaSaldoAcumulado"] = saldo_text["ponta"]
    else:
        data["fatConPontaInjetadoUsinaSaldoAcumulado"] = 0

    if d2.get("usina_fponta_inj") is not None:
        data["fatConFPontaInjetadoUsina"] = d2["usina_fponta_inj"]
    else:
        data["fatConFPontaInjetadoUsina"] = d2.get("inj_hfp_reg") or 0

    if saldo_text["fponta"] is not None:
        data["fatConFPontaInjetadoUsinaSaldoAcumulado"] = saldo_text["fponta"]
    else:
        data["fatConFPontaInjetadoUsinaSaldoAcumulado"] = 0

    data["fatEnergiaInjetadaUsinaPonta"] = data.get("fatConPontaInjetadoUsina", 0)
    data["fatEnergiaInjetadaUsinaFPonta"] = data.get("fatConFPontaInjetadoUsina", 0)

    if qty_reativo_exc_hfp > 0:
        data["fatConFPontaIndReativoExcedente"] = qty_reativo_exc_hfp
        data["fatConFPontaIndExcRegistrado"] = qty_reativo_exc_hfp
        data["fatConFPontaIndExcFaturado"] = qty_reativo_exc_hfp
    else:
        if d2.get("ufer_hfp_reg") is not None:
            data["fatConFPontaIndReativoExcedente"] = d2["ufer_hfp_reg"]
            data["fatConFPontaIndExcRegistrado"] = d2["ufer_hfp_reg"]
            data["fatConFPontaIndExcFaturado"] = d2["ufer_hfp_reg"]

    if qty_reativo_exc_hp > 0:
        data["fatConPontaReativoExcedente"] = qty_reativo_exc_hp
        data["fatConPontaExcRegistrado"] = qty_reativo_exc_hp
        data["fatConPontaExcFaturado"] = qty_reativo_exc_hp
        data["fatConPontaExcValorReais"] = round(val_reativo_exc_hp, 2)
    elif d2.get("ufer_hp_reg") is not None:
        data["fatConPontaReativoExcedente"] = d2["ufer_hp_reg"]

    data["fatConIntermedInjetadoRegistrado"] = 0
    data["fatConIntermedInjetadoFaturado"] = 0
    data["fatConIntermedInjetadoValorReais"] = 0

    dic, fic = _get_dic_fic(page1, full)
    data["fatDIC"] = dic
    data["fatFIC"] = fic
    dic_val = dic or 0.0
    fic_val = fic or 0.0

    obs_list = []
    if val_restituicao is not None and abs(val_restituicao) > 0.005:
        obs_list.append(("109", -round(abs(val_restituicao), 2)))
    if val_correcao > 0.005:
        obs_list.append(("8", -round(abs(val_correcao), 2)))
    elif val_correcao < -0.005:
        data["fatDemandasDevolucaoFPtaValorReais"] = round(val_correcao, 2)
    if (dic_val + fic_val) > 0.005:
        obs_list.append(("11", -round(abs(dic_val + fic_val), 2)))
    for _i, (_cod, _val) in enumerate(obs_list[:5], start=1):
        data[f"obsCod_{_i}"] = _cod
        data[f"obsValor_{_i}"] = _val

    fisco = _extract_fisco_from_page1(pdf_path)
    data["fatICMS"] = fisco["icms_val"]
    data["fatPIS"] = fisco["pis_val"]
    data["fatCOFINS"] = fisco["cofins_val"]
    data["fatDescPisAliquota"] = fisco["aliq_pis"]
    data["fatDesCofinsAliquota"] = fisco["aliq_cofins"]
    data["fatDesIcmsAliquota"] = fisco["aliq_icms"]

    if fisco["aliq_pis"] and fisco["aliq_cofins"]:
        pass  # fatTributoFederalPerc nao preenchido automaticamente — evita valores incorretos
    if fisco["pis_val"] and fisco["cofins_val"]:
        pass  # fatTributoFederalVal nao preenchido automaticamente

    ret = _extract_retidos_from_page1(page1)
    pis_ret, cof_ret, csll_ret, irpj_ret = ret.get("pis"), ret.get("cofins"), ret.get("csll"), ret.get("irpj")

    data["fatDescPisPercRetImposto"] = 0.65 if pis_ret is not None else 0
    data["fatDescPisValRetImposto"] = -abs(pis_ret) if pis_ret is not None else 0
    data["fatDescCofinsPercRetImposto"] = 3.00 if cof_ret is not None else 0
    data["fatDescCofinsValRetImposto"] = -abs(cof_ret) if cof_ret is not None else 0
    data["fatDescCsllPercRetImposto"] = 1.00 if csll_ret is not None else 0
    data["fatDescCsllValRetImposto"] = -abs(csll_ret) if csll_ret is not None else 0
    data["fatDescIrpjValRetImposto"] = -abs(irpj_ret) if irpj_ret is not None else 0
    data["fatDescIrpjPercRetImposto"] = -1
    data["fatDescIrrfPercRetImposto"] = 0
    data["fatDescIrrfValRetImposto"] = 0
    data["fatDescConsumoPercRetImposto"] = 0
    data["fatDescConsumoValRetImposto"] = 0
    data["fatDescDemandaPercRetImposto"] = 0
    data["fatDescDemandaValRetImposto"] = 0
    data["obsValor"] = 0
    data["fatDescontoFioKWh"] = 0

    h_irpj = _fixar_por_padrao(headers, ["irpj", "perc", "ret"], "-1")
    if h_irpj: data[h_irpj] = "-1"

    FIXOS_PERC_RETIDOS = {"pis": "0,65", "cofins": "3", "csll": "1"}
    for trib, val in FIXOS_PERC_RETIDOS.items():
        h_fix = _fixar_por_padrao(headers, [trib, "perc", "ret"], val)
        if h_fix: data[h_fix] = val

    out: List[object] = []
    for h in headers:
        v = data.get(h)
        if isinstance(v, (datetime, date)):
            # FORÇA TEXTO NO EXCEL
            v = v.strftime('%d/%m/%Y')
        elif isinstance(v, float):
            v = round(v, 2)

        v = _json_safe(v)
        if v is None and _is_numeric_field(h): v = 0
        out.append(v)

    return out


def get_dados_extras(pdf_path: str) -> dict:
    """
    Retorna campos extras nao presentes nos headers da planilha,
    usados apenas pelo analisar_e_montar_obs.
    Atualmente: restituicao de pagamento.
    """
    pages = _extract_pages_text(pdf_path)
    full = _full_text(pages)
    page1 = pages[0] if pages else full
    items = _get_ths_items_page1(page1)
    extras = {}
    for it in items:
        if "Restitui" in it.name and it.valor is not None:
            extras["_restituicaoPagamento"] = abs(float(it.valor))
            break
    return extras
