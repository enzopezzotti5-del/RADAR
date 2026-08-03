#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Parser OCR dedicado para faturas EDP ES e EDP SP (Grupo B / BT).

Layout DANF3E: itens de cobrança em linhas densas (TUSD / TE / Bandeira),
retenções na fonte e CIP na tabela fiscal.
"""
from __future__ import annotations

import re
import datetime as dt
from pathlib import Path

import pdfplumber

MESES = {
    "JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12,
}

_SUBGRUPO_MAP = {
    "B1": "B1 [<1kV Res]",
    "B2": "B2 [<1kV Rural]",
    "B3": "B3 [<2,3kV]",
    "B4": "B4 [>=2,3kV e <13,8kV]",
}

_TARIFA_MAP = {
    "CONVENCIONAL": "Convencional",
    "BRANCA":       "Branca",
    "VERDE":        "Verde THS",
    "AZUL":         "Azul THS",
}


def _br2f(s: str) -> float:
    s = str(s or "").strip().strip("-")
    s = re.sub(r"[^\d,.]", "", s)
    if not s:
        return 0.0
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return abs(float(s))
    except ValueError:
        return 0.0


def _to_date(s: str) -> dt.date | None:
    s = str(s or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _fmt(d: dt.date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


def _fix_num_spaces(txt: str) -> str:
    """Remove espaços espúrios dentro de números no layout antigo EDP ES.

    PDF com colunas estreitas faz pdfplumber inserir espaços entre dígitos:
    "8,3 9" -> "8,39", "7520,0 0 0 0" -> "7520,0000", "4.400, 8 1" -> "4.400,81"
    """
    s = txt
    prev = ""
    while prev != s:
        prev = s
        # Merge dígito-espaço-dígito quando já há vírgula/ponto no grupo
        s = re.sub(r"(\d[\d.]*,\d{1,3}) (\d)", r"\1\2", s)
    # Merge separador decimal seguido de espaço e dígito: "4.400, 8" -> "4.400,8"
    s = re.sub(r"([,.]) (\d)", r"\1\2", s)
    # Segunda rodada após correção do separador
    prev = ""
    while prev != s:
        prev = s
        s = re.sub(r"(\d[\d.]*,\d{1,3}) (\d)", r"\1\2", s)
    return s


def _texto(pdf_path: str, max_pages: int = 3) -> str:
    parts: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages[:max_pages]:
            parts.append(page.extract_text() or "")
    return _fix_num_spaces("\n".join(parts))


def _texto_raw(pdf_path: str, max_pages: int = 3) -> str:
    """Texto sem _fix_num_spaces — usado em extrações que sofreriam com a concatenação."""
    parts: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages[:max_pages]:
            parts.append(page.extract_text() or "")
    return "\n".join(parts)


def _instalacao(txt: str, path: str) -> str:
    # EDP ES: 13 grupos com traço final
    m = re.search(r"\b(\d\.\d{3}\.\d{3}\.\d{3}\.\d{3}-\d{2})\b", txt)
    if m:
        return m.group(1)
    # EDP SP: 10 grupos
    m = re.search(r"\b(\d\.\d{3}\.\d{3}\.\d{3}-\d{2})\b", txt)
    if m:
        return m.group(1)
    return Path(path).stem.split(" - ")[0].strip()


def _cnpj_cliente(txt: str) -> str:
    m = re.search(r"CNPJ:\s*([\d./-]{11,20})\b", txt, re.IGNORECASE)
    if m:
        return re.sub(r"\D", "", m.group(1))
    return ""


def _nf(txt: str) -> str:
    m = re.search(r"NOTA\s+FISCAL\s+N[º°O]?\s*([\d.]+)", txt, re.IGNORECASE)
    if m:
        return re.sub(r"\D", "", m.group(1))
    return ""


def _mes_ref(txt: str) -> dt.date | None:
    for m in re.finditer(r"\b([A-Z]{3})/(20\d{2})\b", txt):
        if m.group(1) in MESES:
            return dt.date(int(m.group(2)), MESES[m.group(1)], 1)
    return None


def _vencimento(txt: str) -> dt.date | None:
    # layout novo: "MAI/2026 11/06/2026 R$ 388,52"
    m = re.search(r"[A-Z]{3}/20\d{2}\s+(\d{2}/\d{2}/\d{4})\s+R\$", txt, re.IGNORECASE)
    if m:
        return _to_date(m.group(1))
    # layout antigo: "MAI/2026 08/06/2026 1.062,10 Protocolo"
    m = re.search(r"[A-Z]{3}/20\d{2}\s+(\d{2}/\d{2}/\d{4})\s+[\d.,]+\s+Protocolo", txt, re.IGNORECASE)
    if m:
        return _to_date(m.group(1))
    return None


def _emissao(txt: str) -> dt.date | None:
    # Tenta regex simples primeiro (funciona quando label e data estão na mesma linha)
    m = re.search(r"DATA\s*DE\s*EMISS.O[:\s]*(\d{2}/\d{2}/\d{4})", txt, re.IGNORECASE)
    if m:
        return _to_date(m.group(1))

    # Layouts EDP com linha CEP entre label e data, ou label quebrada:
    #   "DATA DE EMISSÃO:\nCEP: XXXXX\n22/05/2026"
    #   "DATA DE\nCEP: XXXXX\nEMISSÃO: 22/05/2026"
    lines = txt.splitlines()
    _DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")
    for i, line in enumerate(lines):
        # Linha contém "DATA DE" (com ou sem "EMISS" na mesma linha)
        if not re.search(r"\bDATA\s+DE\b", line, re.IGNORECASE):
            continue
        # Procura data ou "EMISS..." nas próximas 4 linhas
        for j in range(i, min(i + 5, len(lines))):
            dm = _DATE_RE.search(lines[j])
            if dm:
                return _to_date(dm.group(1))
    return None


def _valor_nf(txt: str) -> float:
    # layout novo: "MAI/2026 11/06/2026 R$ 388,52"
    m = re.search(r"[A-Z]{3}/20\d{2}\s+\d{2}/\d{2}/\d{4}\s+R\$\s*([\d.,]+)", txt, re.IGNORECASE)
    if m:
        v = _br2f(m.group(1))
        if v > 5:
            return v
    # layout antigo: "MAI/2026 08/06/2026 1.062,10 Protocolo"
    m = re.search(r"[A-Z]{3}/20\d{2}\s+\d{2}/\d{2}/\d{4}\s+([\d.,]+)\s+Protocolo", txt, re.IGNORECASE)
    if m:
        v = _br2f(m.group(1))
        if v > 5:
            return v
    return 0.0


def _datas_leitura(txt: str) -> tuple[dt.date | None, dt.date | None]:
    m = re.search(
        r"(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+(\d{1,3})\s+\d{2}/\d{2}/\d{4}",
        txt,
    )
    if m:
        d1, d2 = _to_date(m.group(1)), _to_date(m.group(2))
        dias = int(m.group(3))
        if d1 and d2 and 15 <= dias <= 45:
            return (d1, d2) if d1 < d2 else (d2, d1)
    return None, None


def _subgrupo(txt: str) -> str:
    m = re.search(r"Classifica[cç][aã]o[:\s]+B\s*-\s*(B\d)", txt, re.IGNORECASE)
    if m:
        return _SUBGRUPO_MAP.get(m.group(1).upper(), "B3 [<2,3kV]")
    return "B3 [<2,3kV]"


def _tarifa(txt: str) -> str:
    m = re.search(r"Modalidade\s+Tarif[aá]ria[:\s]+([\w]+)", txt, re.IGNORECASE)
    if m:
        return _TARIFA_MAP.get(m.group(1).upper(), "Convencional")
    return "Convencional"


def _consumo_kwh(txt: str) -> float:
    # "Energia Ativa - kWh Único  LANT  LATU  CONST  CONSUMO"
    m = re.search(
        r"Energia\s+Ativa\s*[-–]\s*kWh\s+.nico\s+[\d.,]+\s+[\d.,]+\s+[\d.,]+\s+([\d.]+)",
        txt, re.IGNORECASE,
    )
    if m:
        v = _br2f(m.group(1))
        if 1 <= v <= 999_999:
            return v
    # EDP ES/SP layout DANF3E e antigo: "TUSD - Energia Ativa Fornecida kWh 4056,0000"
    m = re.search(r"TUSD\s*-\s*Energia\s+Ativa\s+Fornecida\s+kWh\s+([\d.,]+)", txt, re.IGNORECASE)
    if m:
        v = _br2f(m.group(1))
        if 1 <= v <= 999_999:
            return v
    # EDP SP: "TUSD - Consumo kWh N"
    m = re.search(r"TUSD\s*-\s*Consumo\s+kWh\s+([\d.,]+)", txt, re.IGNORECASE)
    if m:
        v = _br2f(m.group(1))
        if 1 <= v <= 999_999:
            return v
    return 0.0


def _consumo_kwh_pontafponta(txt_raw: str) -> float:
    """Fallback para layout A4 Verde EDP ES: 'TUSD - Consumo Ativo Ponta/FPonta kWh'.

    Soma apenas linhas TUSD (evita dobrar contagem TUSD+TE para o mesmo período).
    """
    total = 0.0
    for line in txt_raw.splitlines():
        line_up = line.upper()
        if "KWH" not in line_up or "INJ" in line_up or "TUSD" not in line_up:
            continue
        if not ("CONSUMO ATIVO" in line_up or "CONS ATIVO" in line_up):
            continue
        m = re.search(r"KWH\s+([\d.,]+)", line, re.IGNORECASE)
        if m:
            v = _br2f(m.group(1))
            if 1 <= v <= 999_999:
                total += v
    return round(total, 4) if total > 0 else 0.0


def _icms_aliquota(txt: str) -> float:
    # TUSD/TE forward line: "... base_icms 18,000 valor_icms ..."
    for line in txt.splitlines():
        line_up = line.upper()
        if "KWH" not in line_up or "INJ" in line_up:
            continue
        if "TUSD" not in line_up and not re.search(r"\bTE\b", line, re.IGNORECASE):
            continue
        nums = re.findall(r"\d[\d.]*,\d+", line)
        for raw in nums:
            v = _br2f(raw)
            if 5 <= v <= 40 and re.search(r",0{2,3}$", raw):
                return v
    if re.search(r"28152650000171", re.sub(r"\D", "", txt)):
        return 17.0
    if re.search(r"02302100000106", re.sub(r"\D", "", txt)):
        return 18.0
    return 0.0


def _normalizar_total_line(line: str) -> str:
    """Normaliza espaços dentro de números monetários na linha TOTAL.

    Aplica apenas: (1) espaço logo após separador decimal, e (2) merge de
    exatamente 1 dígito decimal seguido de espaço+dígito.  Usando apenas 1 dígito
    decimal evita concatenar valores adjacentes como '1.062,10 2,93'.
    """
    # Remove espaços imediatamente após vírgula/ponto decimal: "755, 23" → "755,23"
    s = re.sub(r"([,.]) *(\d)", r"\1\2", line)
    # Merge iterativo APENAS quando há 1 dígito decimal: "1.062,1 0" → "1.062,10"
    prev = ""
    while prev != s:
        prev = s
        s = re.sub(r"(\d[\d.]*,\d) (\d)", r"\1\2", s)
    return s


def _icms_total(txt: str) -> tuple[float, float]:
    """Returns (icms_valor, icms_base) from TOTAL billing line."""
    for line in txt.splitlines():
        if re.match(r"\s*TOTAL\s", line, re.IGNORECASE):
            norm = _normalizar_total_line(line)
            vals = re.findall(r"\d[\d.]*,\d{2}(?!\d)", norm)
            if len(vals) >= 4:
                return _br2f(vals[3]), _br2f(vals[2])
            if len(vals) >= 2:
                return _br2f(vals[-1]), 0.0
    return 0.0, 0.0


def _pis_cofins(txt: str) -> tuple[float, float, float, float]:
    """Returns (pis_val, pis_pct, cofins_val, cofins_pct) — net de injeção.

    Escaneia todas as linhas com re.finditer para capturar tanto linhas
    standalone quanto valores embutidos nas linhas TUSD/TE (layout DANF3E).
    Espera texto RAW (sem _fix_num_spaces) para não confundir com concatenação.
    """
    pis_pos = pis_neg = pis_pct = 0.0
    cof_pos = cof_neg = cof_pct = 0.0

    # Padrão: LABEL <base>[-] <rate>[-] <value>[-]
    _pat_pis = re.compile(r"\bPIS\s+([\d.,]+)(-?)\s+([\d.,]+)(-?)\s+([\d.,]+)(-?)")
    _pat_cof = re.compile(r"\bCOFINS\s+([\d.,]+)(-?)\s+([\d.,]+)(-?)\s+([\d.,]+)(-?)")

    for line in txt.splitlines():
        for m in _pat_pis.finditer(line):
            val = _br2f(m.group(5))
            pct = _br2f(m.group(3))
            neg = bool(m.group(2) or m.group(4) or m.group(6))
            if neg:
                pis_neg += val
            else:
                pis_pos += val
                if not pis_pct:
                    pis_pct = pct

        for m in _pat_cof.finditer(line):
            val = _br2f(m.group(5))
            pct = _br2f(m.group(3))
            neg = bool(m.group(2) or m.group(4) or m.group(6))
            if neg:
                cof_neg += val
            else:
                cof_pos += val
                if not cof_pct:
                    cof_pct = pct

    return round(pis_pos - pis_neg, 2), pis_pct, round(cof_pos - cof_neg, 2), cof_pct


def _nums_apos_kwh(line: str) -> list[float]:
    """Extrai todos os números decimais após 'kWh' numa linha de item."""
    m = re.search(r"kWh\s+(.*)", line, re.IGNORECASE)
    if not m:
        return []
    return [_br2f(n) for n in re.findall(r"\d[\d.]*,\d+", m.group(1))]


def _consumo_valor(txt_raw: str) -> float:
    """TUSD+TE em R$ forward (gross, sem subtrair injeção GD).

    Usa txt_raw para evitar que _fix_num_spaces concatene valores adjacentes.
    Acumula com += para suportar layout Ponta+FPonta (múltiplas linhas por período).
    """
    tusd_fwd = te_fwd = 0.0
    for line in txt_raw.splitlines():
        line_up = line.upper()
        if "KWH" not in line_up or "INJ" in line_up:
            continue
        nums = _nums_apos_kwh(line)
        if len(nums) < 3:
            continue
        v = nums[2]
        if not v:
            continue
        if "TUSD" in line_up:
            if "FORNECIDA" in line_up or "CONSUMO" in line_up or "ATIVO" in line_up:
                tusd_fwd += v
        elif re.search(r"\bTE\b", line, re.IGNORECASE):
            if "FORNECIDA" in line_up or "CONSUMO" in line_up or "ATIVO" in line_up:
                te_fwd += v
    return round(tusd_fwd + te_fwd, 2)


def _bandeira(txt_raw: str) -> float:
    """Adicional bandeira forward (sem injeção GD). Injeção vai para fatValBandeira2 via _gd_fields."""
    fwd = 0.0
    for line in txt_raw.splitlines():
        line_up = line.upper()
        if "ADICIONAL" not in line_up or "BANDEIRA" not in line_up:
            continue
        if "INJ" in line_up:
            continue
        nums = _nums_apos_kwh(line)
        if len(nums) >= 3 and nums[2]:
            fwd += nums[2]
    return round(fwd, 2)


def _cip(txt: str) -> float:
    """Extrai CIP.

    Layout novo: '... 1,0000 171,92 0,00000000' → valor após 1,0+
    Layout antigo: '... 7.800/2019 272, 7 0 0,00000000 VERDE' → primeira quantia monetária
    """
    for line in txt.splitlines():
        if not re.search(r"[Cc]ontribui.{1,5}\s+de\s+Ilum", line, re.IGNORECASE):
            continue
        # Normaliza espaços dentro de números na linha (ex.: "272, 7 0" → "272,70")
        line_norm = _normalizar_total_line(line)
        for n in re.findall(r"\d[\d.]*,\d{2}(?!\d)", line_norm):
            v = _br2f(n)
            if 5 <= v <= 10_000:
                return v
    return 0.0


def _ret_valor(label_pat: str, txt: str) -> float:
    """Extrai valor de retenção nos dois layouts EDP.

    Layout novo: 'LABEL 1,0000 6,45-'  -> captura 6,45 (2º número)
    Layout antigo: 'LABEL 8,39 -'       -> captura 8,39 (1º número, sem quantidade)
    """
    m = re.search(label_pat + r"\s+[\d.,]+\s+([\d.,]+)", txt, re.IGNORECASE)
    if m:
        v = _br2f(m.group(1))
        if v > 0:
            return v
    # Layout antigo: valor vem direto após o label (sem coluna de quantidade)
    m = re.search(label_pat + r"\s+([\d.,]+)", txt, re.IGNORECASE)
    if m:
        v = _br2f(m.group(1))
        if v > 0:
            return v
    return 0.0


def _retencoes(txt: str) -> dict[str, float]:
    """Extrai retenções na fonte por linha.

    Usa 'RETEN' como filtro primário para tolerar encoding garbled (ç/ã → ?).
    Acumula variantes 'Demanda' no mesmo campo (ex.: CSLL + Demanda CSLL).
    Valor extraído por '(número)-' no final da linha.
    """
    acc: dict[str, float] = {}
    for line in txt.splitlines():
        if "RETEN" not in line.upper():
            continue
        m = re.search(r"([\d.,]+)\s*-", line)
        if not m:
            continue
        v = _br2f(m.group(1))
        if not v:
            continue
        line_up = line.upper()
        if "CSLL" in line_up:
            campo = "fatDescCsllValRetImposto"
        elif "PIS" in line_up:
            campo = "fatDescPisValRetImposto"
        elif "COFINS" in line_up:
            campo = "fatDescCofinsValRetImposto"
        elif "IMPOSTO" in line_up or "RENDA" in line_up or "IRPJ" in line_up or "IRRF" in line_up:
            campo = "fatDescIrpjValRetImposto"
        else:
            continue
        acc[campo] = acc.get(campo, 0.0) - v
    return acc


def _retencoes_perc(ret: dict[str, float]) -> dict[str, float]:
    """EDP SP/ES usa as alíquotas padrão quando há retenções individuais."""
    out: dict[str, float] = {}
    if ret.get("fatDescPisValRetImposto"):
        out["fatDescPisPercRetImposto"] = 0.65
    if ret.get("fatDescCofinsValRetImposto"):
        out["fatDescCofinsPercRetImposto"] = 3.0
    if ret.get("fatDescCsllValRetImposto"):
        out["fatDescCsllPercRetImposto"] = 1.0
    if ret.get("fatDescIrpjValRetImposto"):
        out["fatDescIrpjPercRetImposto"] = 1.2
    return out


def _gd_fields(txt_raw: str) -> dict:
    """Extrai campos GD: injetado kWh/R$, bandeira injetada R$, saldo acumulado.

    Linhas com INJ+KWH: TUSD/TE → inj_val (fatConFPontaInjetadoValorReais);
                        BANDEIRA → band2_val (fatValBandeira2, negativo).
    kWh da injeção: soma da coluna qty das linhas TUSD INJ.
    Saldo: linha 'Saldo Total ... kWh'.
    """
    out: dict = {}
    inj_kwh = 0.0
    inj_val = 0.0
    fwd_val = 0.0
    band2_val = 0.0

    for line in txt_raw.splitlines():
        line_up = line.upper()

        # EDP ESCELSA: "Energia Ativa Fornecida kWh qty tarifa valor"
        # Quando fwd==inj a compensação é integral; capturar fwd evita vc<0.
        if "INJ" not in line_up and "FORNECIDA" in line_up and "KWH" in line_up:
            nums = _nums_apos_kwh(line)
            if len(nums) >= 3:
                fwd_val += nums[2]

        elif "INJ" in line_up and "KWH" in line_up:
            nums = _nums_apos_kwh(line)
            if len(nums) >= 3:
                if "BANDEIRA" in line_up:
                    band2_val += nums[2]
                else:
                    inj_val += nums[2]
                    if "TUSD" in line_up and nums[0] > 0:
                        inj_kwh += nums[0]

        if re.search(r"Saldo\s+Total", line, re.IGNORECASE):
            m = re.search(r"(\d[\d.,]+)\s*kWh", line, re.IGNORECASE)
            if m:
                out["fatConFPontaInjetadoUsinaSaldo"] = _br2f(m.group(1))

    if inj_kwh > 0:
        inj_kwh = round(inj_kwh, 4)
        out["fatConFPontaInjetadoUsina"]      = inj_kwh
        out["fatConFPontaInjetadoRegistrado"] = inj_kwh
        out["fatConFPontaInjetadoFaturado"]   = inj_kwh
    if fwd_val > 0:
        out["fatConFPontaIndValorReais"] = round(fwd_val, 2)
    if inj_val > 0:
        out["fatConFPontaInjetadoValorReais"] = round(inj_val, 2)
    if band2_val > 0:
        out["fatValBandeira2"] = -round(band2_val, 2)

    return out


def _observacoes(txt: str) -> dict:
    """Extrai observações financeiras conhecidas da EDP BT."""
    observacoes: list[tuple[str, float]] = []

    regras = [
        (
            re.compile(
                r"(?:devolu[cç][aã]o|restitui[cç][aã]o).{0,80}"
                r"pagamento.{0,40}indevido.{0,40}?([\d.]+,\d{2})\s*-?",
                re.IGNORECASE,
            ),
            "109",
            True,
        ),
        (
            re.compile(r"\bjuros?\s+(?:de\s+)?mora\b.{0,60}?([\d.]+,\d{2})", re.IGNORECASE),
            "7",
            False,
        ),
        (
            re.compile(r"\bmulta\b.{0,60}?([\d.]+,\d{2})", re.IGNORECASE),
            "6",
            False,
        ),
    ]

    for line in txt.splitlines():
        if len(observacoes) >= 5:
            break
        for pat, codigo, negativo in regras:
            m = pat.search(line)
            if not m:
                continue
            valor = _br2f(m.group(1))
            if valor >= 0.01:
                observacoes.append((codigo, -valor if negativo else valor))
            break

    out: dict[str, str | float] = {}
    for idx, (codigo, valor) in enumerate(observacoes[:5], start=1):
        out[f"obsCod_{idx}"] = codigo
        out[f"obsValor_{idx}"] = round(valor, 2)
    return out


def _pis_cofins_danf3e(txt_raw: str) -> tuple[float, float, float, float]:
    """
    Extrai PIS e COFINS do layout DANF3E (EDP SP/ES).

    Nesse layout os valores estão embutidos ao final das linhas TUSD e TE:
      TUSD: "...0,45667000 PIS 691,81 0 , 7 1 4,91"
        → base=691,81  rate≈0,71%  valor=4,91
      TE:   "...0,33003000 COF I N S 691.81 3, 2 6 0 22.55"
        → base=691.81  rate≈3,26%  valor=22.55

    Estratégia: captura o primeiro e o último número decimal (XX,XX ou XX.XX)
    no fragmento após o label; base=primeiro, valor=último;
    taxa calculada de base+valor para evitar parsing de frações fragmentadas.
    Retorna (pis_val, pis_pct, cofins_val, cofins_pct).
    """
    pis_v = pis_a = cof_v = cof_a = 0.0

    _num2d = re.compile(r"\d[\d.]*[,\.]\d{2}(?!\d)")

    for line in txt_raw.splitlines():
        line_up = line.upper()

        # PIS na linha TUSD
        if "TUSD" in line_up and re.search(r"\bPIS\b", line, re.IGNORECASE):
            m = re.search(r"\bPIS\b\s+(.*)", line, re.IGNORECASE)
            if m:
                nums = [_br2f(n) for n in _num2d.findall(m.group(1)) if _br2f(n) > 0]
                if len(nums) >= 2:
                    pis_v = nums[-1]
                    base  = nums[0]
                    if base > 0:
                        pis_a = round(pis_v / base * 100, 4)

        # COFINS na linha TE ("COF I N S" ou "COFINS")
        if re.search(r"\bTE\b", line, re.IGNORECASE) and re.search(r"COF\s*I?\s*N?\s*S", line, re.IGNORECASE):
            m = re.search(r"COF\s*I?\s*N?\s*S\s+(.*)", line, re.IGNORECASE)
            if m:
                nums = [_br2f(n) for n in _num2d.findall(m.group(1)) if _br2f(n) > 0]
                if len(nums) >= 2:
                    cof_v = nums[-1]
                    base  = nums[0]
                    if base > 0:
                        cof_a = round(cof_v / base * 100, 4)

    return pis_v, pis_a, cof_v, cof_a


def _barcode(txt: str) -> str:
    # Linha digitável EDP: 4 grupos de 12 dígitos
    m = re.search(r"\b(\d{12})\s+(\d{12})\s+(\d{12})\s+(\d{12})\b", txt)
    if m:
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) >= 44:
            return digits[:47]
    m = re.search(r"\d{44,}", txt.replace(" ", ""))
    if m:
        return m.group(0)[:47]
    return ""


def processar_pdf(path: str, src_original: str | None = None) -> dict:
    pdf_path = Path(str(path))
    txt     = _texto(str(pdf_path))       # com _fix_num_spaces (para kWh e consumo)
    txt_raw = _texto_raw(str(pdf_path))   # sem fix (para TOTAL, PIS/COFINS, GD)

    uc       = _instalacao(txt, str(src_original or path))
    mes      = _mes_ref(txt)
    vcto     = _vencimento(txt)
    em       = _emissao(txt)
    ant, atu = _datas_leitura(txt)
    valor    = _valor_nf(txt)
    kwh      = _consumo_kwh(txt)
    if not kwh:
        kwh = _consumo_kwh_pontafponta(txt_raw)
    icms_v, icms_b = _icms_total(txt_raw)
    icms_a   = _icms_aliquota(txt)
    pis_v, pis_a, cof_v, cof_a = _pis_cofins(txt_raw)
    # Fallback DANF3E: PIS/COFINS embutidos nas linhas TUSD/TE (EDP SP/ES)
    if not pis_v or not cof_v:
        danf_p, danf_pa, danf_c, danf_ca = _pis_cofins_danf3e(txt_raw)
        if not pis_v and danf_p:
            pis_v, pis_a = danf_p, danf_pa
        if not cof_v and danf_c:
            cof_v, cof_a = danf_c, danf_ca
    fp_val   = _consumo_valor(txt)    # txt fixado: espaços em números quebrados corrigidos (ex: "177, 3 9" → "177,39")
    band_v   = _bandeira(txt)         # idem: "7, 3 2" → "7,32"; txt_raw dava nums[2] errado
    cip_v    = _cip(txt_raw)  # raw: _fix_num_spaces garble "1,0000 171,92" → CIP perde valor
    subg     = _subgrupo(txt)
    tar      = _tarifa(txt)
    ret      = _retencoes(txt)
    ret_perc = _retencoes_perc(ret)
    gd       = _gd_fields(txt_raw)
    obs      = _observacoes(txt)
    nf       = _nf(txt)
    cnpj     = _cnpj_cliente(txt)
    bar      = _barcode(txt)

    stem = pdf_path.stem.strip()
    carimbo = stem if stem.upper().startswith("BB_") else ""

    return {
        "fatCarimbo":                carimbo,
        "Instalacao":                uc,
        "CODIGOCLIENTE":             uc,
        "NOTAFISCAL":                nf,
        "CNPJ":                      cnpj,
        "fatDataEmissao":            _fmt(em),
        "fatDataVcto":               _fmt(vcto),
        "fatDataLeituraAnterior":    _fmt(ant),
        "fatDataLeituraAtual":       _fmt(atu),
        "fatDataReferencia":         mes.strftime("01/%m/%Y") if mes else "",
        "fatValorFatura":            valor,
        "fatValorNotaFiscal":        valor,
        "fatConFPontaIndRegistrado": kwh,
        "fatConFPontaIndFaturado":   kwh,
        "fatConFPontaIndutivo":      kwh,
        "fatConFPontaIndValorReais": fp_val,
        # GD — injetado kWh, R$ e saldo; fatValBandeira2 também vem daqui
        **gd,
        "fatICMS":                   icms_v,
        "fatICMSBase":               icms_b,
        "fatDesIcmsAliquota":        icms_a,
        "fatPIS":                    pis_v,
        "fatDescPisAliquota":        pis_a,
        "fatCOFINS":                 cof_v,
        "fatDesCofinsAliquota":      cof_a,
        "fatValBandeira":            band_v,
        "fatIlumPublica":            cip_v,
        "cadTarifaCod":              tar,
        "cadSubGrupoCod":            subg,
        "fatCodigoBarras":           bar,
        **ret,
        **ret_perc,
        **obs,
    }
