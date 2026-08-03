#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Parser genérico BT — extrai campos mínimos para digitação no Consen.
Suporta: Light (RJ), EDP SP, EDP ES, RGE Sul, Enel Rio/Ampla, Enel CE.
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


def _digits(s: str) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _compact(region: str, n: int = 5) -> str:
    """Colapsa espaços dentro de números em uma região curta."""
    for _ in range(n):
        prev = region
        region = re.sub(r"(\d)\s+(\d)", r"\1\2", region)
        region = re.sub(r"(\d)\s*,\s*(\d)", r"\1,\2", region)
        if region == prev:
            break
    return region


def _br2f(s: str) -> float:
    s = str(s or "").strip()
    if not s or "*" in s:
        return 0.0
    if "," in s:
        # Formato BR: ponto = milhar, vírgula = decimal
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") == 1:
        # Sem vírgula, ponto único:
        #   "4.228" (1–3 dígitos antes do ponto, 3 após) → BR milhar → 4228
        #   "6681.000" (4+ dígitos antes, 3 após) → US decimal → 6681.0
        #   "3.45"  (1–3 dígitos antes, 1–2 após)  → decimal   → 3.45
        m = re.match(r"^(\d+)\.(\d+)$", s)
        if m:
            before, after = m.group(1), m.group(2)
            if len(after) == 3 and len(before) <= 3:
                # BR milhar: remove ponto
                s = before + after
            # else: tratar como float (decimal US)
    else:
        # Múltiplos pontos → todos são separadores de milhar → remove
        s = s.replace(".", "")
    try:
        return abs(float(s))
    except ValueError:
        return 0.0


def _to_date(s: str) -> dt.date | None:
    s = str(s or "").strip().replace(".", "/")  # normaliza DD.MM.YYYY → DD/MM/YYYY
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _uc_from_filename(path: str) -> str:
    """'1.205.201.059-25 - 28.05.pdf' → '1.205.201.059-25' (mantém pontuação)."""
    nome = Path(path).stem
    return nome.split(" - ")[0].strip()


def _carimbo_from_filename(path: str) -> str:
    nome = Path(path).stem.strip()
    return nome if nome.upper().startswith("BB_") else ""


def _extract_nf(txt: str) -> str:
    # Permissivo: após "NOTA FISCAL" pega o primeiro bloco de dígitos longo
    m = re.search(r"NOTA.{0,8}FISCAL\D{0,20}(\d[\d.]{4,})", txt, re.I)
    if m:
        return _digits(m.group(1))
    return ""


def _extract_instalacao(txt: str, path: str) -> str:
    patterns = [
        r"(\d\.\d{3}\.\d{3}\.\d{3}\.\d{3}-\d{2})",  # EDP ES completo
        r"EQUATORIAL[^\n]+DISTRIB[^\n]+\s(\d{3}\.\d{3}\.\d{3}-\d{2})\b",  # Equatorial PI/PA inline (antes do 1.3.3.3 para nao pegar UC distribuicao)
        r"(\d\.\d{3}\.\d{3}\.\d{3}-\d{2})",  # EDP / Equatorial MA
        r"(?<!\d)(\d{1,3}\.\d{3}\.\d{3}-\d{2})",  # Light RJ (1-3 dígitos antes do ponto)
        r"(?m)^\s*(\d{7,12})\s+\d{2}/\d{2}/\d{4}\s+\d{2}/\d{2}/\d{4}\s+\d{1,3}\s*$",  # RGE
        r"(?:UNIDADE\s+CONSUMIDORA|INSTALACAO|INSTALA[CÇ][AÃ]O)[^\d]{0,20}(\d{7,12})",
        r"\b(\d{7,12})\s*/\s*\d{6,12}\s+R\$\s*[\d.,]+",  # layouts RJ/CE com "UC/cliente"
    ]
    for pattern in patterns:
        m = re.search(pattern, txt, re.I)
        if m:
            return m.group(1).strip()
    return _uc_from_filename(path)


def _extract_emissao(txt: str) -> dt.date | None:
    for pat in [
        # Imediato: emissão logo após a label (Light, ENEL, EDP SP)
        r"DATA\s*DE\s*EMISS.O[:\s]*([\d]{2}[/.][\d]{2}[/.][\d]{4})",
        r"EMISS.O[:\s]*([\d]{2}[/.][\d]{2}[/.][\d]{4})",
        # EDP ES: data separada por outras linhas até 100 chars após a label
        r"DATA\s*DE\s*EMISS.O[\s\S]{0,100}?(\d{2}/\d{2}/\d{4})",
        # Equatorial MA: DATA DOCUMENTO header seguido (até 200 chars) pela data
        r"DATA\s+DOCUMENTO[\s\S]{1,200}?(\d{2}\.\d{2}\.\d{4})",
    ]:
        m = re.search(pat, txt, re.I)
        if m:
            return _to_date(m.group(1))
    return None


def _extract_mes_ref(txt: str) -> dt.date | None:
    for m in re.finditer(r"\b([A-Z]{3})/(20\d{2})\b", txt):
        if m.group(1) in MESES:
            return dt.date(int(m.group(2)), MESES[m.group(1)], 1)
    m = re.search(r"\b(0[1-9]|1[0-2])/(20\d{2})\b", txt)
    if m:
        return dt.date(int(m.group(2)), int(m.group(1)), 1)
    return None


def _extract_vencimento(txt: str) -> dt.date | None:
    # "MES/AAAA DD/MM/YYYY R$ ..." ou "MM/AAAA DD/MM/YYYY R$ ..." (numérico ou abreviação)
    # Equatorial PA usa "07/2026 26/08/2026 R$ 0,00"; outros usam "JUL/2026 26/08/2026 R$ ..."
    # Lookbehind (?<![/\d]) impede que o "05" de "01/05/2026" seja confundido com mês numérico.
    m = re.search(
        r"(?:[A-Z]{3}|(?<![/\d])\d{2})/20\d{2}\s+([\d]{2}/[\d]{2}/[\d]{4})\s+(?:R?\$?|[*\d])",
        txt, re.I,
    )
    if m:
        d = _to_date(m.group(1))
        if d:
            return d
    # "VENCIMENTO ... DD/MM/YYYY" ou "DD.MM.YYYY"
    m = re.search(r"VENCIMENTO[^\d]*([\d]{2}[/.][\d]{2}[/.][\d]{4})", txt, re.I)
    if m:
        return _to_date(m.group(1))
    # Equatorial MA: linha após "PAGÁVEL PREFERENCIALMENTE NO BANCO DO BRASIL DD.MM.YYYY"
    m = re.search(r"BANCO\s+DO\s+BRASIL\s+([\d]{2}\.[\d]{2}\.[\d]{4})\b", txt, re.I)
    if m:
        return _to_date(m.group(1))
    return None


def _extract_valor(txt: str) -> float:
    # "MES/AAAA DD/MM/YYYY R$ X.XXX,XX" ou DD.MM.YYYY
    for m in re.finditer(
        r"[A-Z]{3}/20\d{2}\s+[\d/.]+\s+R?\$?\s*([\d.,]+)", txt
    ):
        v = _br2f(m.group(1))
        if 10.0 <= v <= 9_999_999.0:
            return v
    # Linha de pagamento: "DD/MM/YYYY X.XXX,XX" — requer vírgula (evita capturar dias)
    for m in re.finditer(r"[\d]{2}/[\d]{2}/[\d]{4}\s+([\d.]*,[\d]{2})\b", txt):
        v = _br2f(m.group(1))
        if 10.0 <= v <= 9_999_999.0:
            return v
    # Equatorial MA boleto: "VALOR DOCUMENTO\n17 R$ X.XXX,XX"
    m = re.search(r"VALOR\s+DOCUMENTO[\s\S]{0,60}R\$\s*([\d.]+,[\d]{2})\b", txt, re.I)
    if m:
        v = _br2f(m.group(1))
        if 10.0 <= v <= 9_999_999.0:
            return v
    return 0.0


def _extract_datas_leitura(txt: str) -> tuple[dt.date | None, dt.date | None]:
    # "LEITURA ANTERIOR ... LEITURA ATUAL ... D1 D2 DIAS"
    m = re.search(
        r"LEITURA\s+ANTERIOR.*?LEITURA\s+ATUAL.*?"
        r"([\d]{2}/[\d]{2}/[\d]{4})\s+([\d]{2}/[\d]{2}/[\d]{4})",
        txt, re.I | re.DOTALL,
    )
    if m:
        return _to_date(m.group(1)), _to_date(m.group(2))
    # Par de datas seguido de número de dias (15–45); (?!/) impede capturar "15" de "15/06/2026"
    for m in re.finditer(
        r"([\d]{2}/[\d]{2}/[\d]{4})\s+([\d]{2}/[\d]{2}/[\d]{4})\s+(\d{1,3})\b(?!/)", txt
    ):
        d1, d2 = _to_date(m.group(1)), _to_date(m.group(2))
        dias = int(m.group(3))
        if d1 and d2 and 15 <= dias <= 45:
            return (d1, d2) if d1 < d2 else (d2, d1)
    return None, None


def _extract_consumo(txt: str) -> float:
    """Extrai consumo kWh em ordem de prioridade."""

    # 1. "Energia Elétrica kWh kWh N" — Light/ENEL (billed consumption)
    m = re.search(r"Energia\s+El[eé]trica\s+kWh\s+kWh\s+([\d.,]+)", txt, re.I)
    if m:
        v = _br2f(m.group(1))
        if 1 <= v <= 999_999:
            return v

    # 2. "TUSD - Consumo kWh N" — EDP SP (exclui linhas GD injection)
    for m in re.finditer(r"TUSD\s*-\s*Consumo\s+kWh\s+([\d.,]+)", txt, re.I):
        v = _br2f(m.group(1))
        if 1 <= v <= 999_999:
            return v

    # 3. "Consumo Uso Sistema ... kWh N" — CPFL/RGE Sul (pdfplumber pode remover espaços)
    for m in re.finditer(
        r"Consumo\s*Uso\s*Sistema[^\n]*?kWh\s+([\d.,]+)", txt, re.I
    ):
        v = _br2f(m.group(1))
        if 1 <= v <= 999_999:
            return v

    # 4. "kWh [Úú�]nico ... N_ant N_atu CONST CONSUMO" — EDP ES
    m = re.search(
        r"kWh\s+.nico\s+[\d.,]+\s+[\d.,]+\s+[\d.,]+\s+([\d.]+)", txt, re.I
    )
    if m:
        # _br2f distingue BR milhar ("3.280"→3280) de US decimal ("6681.000"→6681)
        v = _br2f(m.group(1))
        if 1 <= v <= 999_999:
            return v

    # 5. "Consumo (kWh) N ..." — Equatorial MA tarifa convencional
    m = re.search(r"Consumo\s+\(kWh\)\s+([\d.,]+)", txt, re.I)
    if m:
        v = _br2f(m.group(1))
        if 1 <= v <= 999_999:
            return v

    # 5b. Tarifa Branca Equatorial MA: usa Fora Ponta como campo principal
    m = re.search(r"Consumo\s+Fora\s+Ponta\s+\(kWh\)\s+([\d.,]+)", txt, re.I)
    if m:
        v = _br2f(m.group(1))
        if 1 <= v <= 999_999:
            return v

    # 6. "Consumo ATIVO TOTAL N_ant N_atu CONST CONSUMO kWh" — Equatorial MA alternativo
    m = re.search(r"Consumo\s+ATIVO\s+TOTAL\s+[\d.,]+\s+[\d.,]+\s+[\d.,]+\s+([\d.,]+)\s+kWh", txt, re.I)
    if m:
        v = _br2f(m.group(1))
        if 1 <= v <= 999_999:
            return v

    # 7. Custo de Disponibilidade kWh N — Light RJ em consumo mínimo
    m = re.search(r"Custo\s+de\s+Disponibilidade\s+kWh\s+([\d.,]+)", txt, re.I)
    if m:
        v = _br2f(m.group(1))
        if 1 <= v <= 999_999:
            return v

    # 8. Ampla/ENEL RJ — tabela "DADOS DE MEDIÇÃO"
    # Linha: "ENERGIA ATIVA - KWH  HFP  LANT  LATU  CONST  CONSUMO"
    m = re.search(
        r"ENERGIA\s+ATIVA\s*[-–]?\s*KWH\s+HFP\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)",
        txt, re.I
    )
    if m:
        try:
            v = abs(float(m.group(4)))
        except ValueError:
            v = 0.0
        if 1 <= v <= 999_999:
            return v

    return 0.0


def _extract_icms(txt: str) -> float:
    # "ICMS BASE RATE% VALOR" — retorna o VALOR do tributo
    m = re.search(r"\bICMS\b\s+([\d.,]+)\s+([\d.,]+)\s*%?\s+([\d.,]+)", txt, re.I)
    if m:
        v = _br2f(m.group(3))
        if v > 0:
            return v

    # ENEL CE/RJ com "I CMS" separado por espaço
    m = re.search(r"\bI\s*CMS\b\s+([\d.,]+)\s+([\d.,]+)\s*%?\s+([\d.,]+)", txt, re.I)
    if m:
        v = _br2f(m.group(3))
        if v > 0:
            return v

    # EDP SP / ENEL RJ: linha TOTAL com 4 valores, o último é o ICMS total
    for line in txt.splitlines():
        if "TOTAL" in line.upper():
            c = _compact(line)
            vals = re.findall(r"[\d.]+,\d{2}", c)
            if len(vals) >= 2:
                v = _br2f(vals[-1])
                if v > 0:
                    return v

    # EDP ES: "Imposto / Tributos" traz a carga total; deduz PIS/COFINS
    m = re.search(r"IMPOSTO\s*/\s*TRIBUTOS\s+([\d.,]+)", txt, re.I)
    if m:
        total_trib = _br2f(m.group(1))
        if total_trib > 0:
            v = round(max(total_trib - _extract_pis(txt) - _extract_cofins(txt), 0.0), 2)
            if v > 0:
                return v

    return 0.0


def _extract_pis(txt: str) -> float:
    # "PIS/PASEP BASE RATE% VALOR" — retorna o VALOR do tributo
    m = re.search(r"PIS(?:/PASEP)?\s+([\d.,]+)\s+([\d.,]+)\s*%?\s+([\d.,]+)", txt, re.I)
    if m:
        v = _br2f(m.group(3))
        if v > 0:
            return v

    # "PIS/PASEP BASE RATE VALOR" — sem % (EDP ES, RGE Sul)
    m = re.search(r"PIS(?:/PASEP)?\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)", txt, re.I)
    if m:
        v = _br2f(m.group(3))
        if v > 0:
            return v

    # Compacta linha com "PIS" (EDP SP garbled)
    for line in txt.splitlines():
        marker = re.sub(r"\s+", "", line.upper())
        if "PIS" in marker:
            vals = re.findall(r"[\d.]+,\d{2}|\d+\.\d{2}", line)
            if vals:
                v = _br2f(vals[-1])
                if v > 0:
                    return v
            c = _compact(line)
            m = re.search(r"PIS(?:/PASEP)?[^\n]*?([\d.,]+)\s*%?\s+([\d.,]+)$", c, re.I)
            if m:
                v = _br2f(m.group(2))
                if v > 0:
                    return v

    return 0.0


def _extract_cofins(txt: str) -> float:
    # "COFINS BASE RATE% VALOR" — retorna o VALOR do tributo
    m = re.search(r"\bCOFINS\s+([\d.,]+)\s+([\d.,]+)\s*%?\s+([\d.,]+)", txt, re.I)
    if m:
        v = _br2f(m.group(3))
        if v > 0:
            return v

    # "COFINS BASE RATE VALOR" — sem % (EDP ES, RGE Sul)
    m = re.search(r"\bCOFINS\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)", txt, re.I)
    if m:
        v = _br2f(m.group(3))
        if v > 0:
            return v

    # Compacta linha com "COFINS" (EDP SP garbled)
    for line in txt.splitlines():
        marker = re.sub(r"\s+", "", line.upper())
        if "COFINS" in marker:
            vals = re.findall(r"[\d.]+,\d{2}|\d+\.\d{2}", line)
            if vals:
                v = _br2f(vals[-1])
                if v > 0:
                    return v
            c = _compact(line)
            m = re.search(r"COFINS[^\n]*?([\d.,]+)\s*%?\s+([\d.,]+)$", c, re.I)
            if m:
                v = _br2f(m.group(2))
                if v > 0:
                    return v

    return 0.0


def _extract_barcode(txt: str) -> str:
    # CPFL/RGE Sul: 4 grupos exatos de 12 dígitos — concessionária usa 48 dígitos
    m = re.search(r"\b(\d{12})\s+(\d{12})\s+(\d{12})\s+(\d{12})\b", txt)
    if m:
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) == 48:
            return digits

    # Grupos de 9–12 dígitos separados por espaços (linha digitável clássica)
    grupos = re.findall(r"\b\d{9,12}\b", txt)
    if len(grupos) >= 4:
        candidato = "".join(grupos[:5])
        if len(candidato) >= 44:
            return candidato[:47]
    # Sequência contínua ≥ 44 dígitos
    m = re.search(r"\d{44,}", txt.replace(" ", ""))
    if m:
        return m.group(0)[:47]
    # Linha digitável BB: XXXXX.XXXXX XXXXX.XXXXXX XXXXX.XXXXXX X XXXXXXXXXXXXXX (47 dígitos)
    m = re.search(
        r"(\d{5}\.\d{5})\s+(\d{5}\.\d{6})\s+(\d{5}\.\d{6})\s+(\d)\s+(\d{14})",
        txt
    )
    if m:
        return re.sub(r"\D", "", m.group(0))[:47]
    # Genérico: bloco de dígitos com espaços/pontos internos totalizando 44–48 dígitos
    for m in re.finditer(r"\d[\d .\-]{30,}\d", txt):
        digits = re.sub(r"\D", "", m.group(0))
        if 44 <= len(digits) <= 48:
            return digits[:47]
    return ""


def _extract_icms_aliquota(txt: str) -> float:
    # "I CMS BASE ALIQ VALOR" — Ampla/ENEL RJ
    m = re.search(r"\bI\s*CMS\b\s+[\d.,]+\s+([\d.,]+)\s+[\d.,]+", txt, re.I)
    if m:
        v = _br2f(m.group(1))
        if 5 <= v <= 40:
            return v
    # "ICMS BASE ALIQ% VALOR" — padrão com %
    m = re.search(r"\bICMS\b\s+[\d.,]+\s+([\d.,]+)\s*%", txt, re.I)
    if m:
        v = _br2f(m.group(1))
        if 5 <= v <= 40:
            return v
    # "ICMS BASE ALIQ VALOR" — sem %
    m = re.search(r"\bICMS\b\s+[\d.,]+\s+([\d.,]+)\s+[\d.,]+", txt, re.I)
    if m:
        v = _br2f(m.group(1))
        if 5 <= v <= 40:
            return v
    # ENEL GO / Light — alíquota na linha de energia elétrica / TUSD / Custo Disp
    for pat in [
        r"(?:Energia El[eé]trica|TUSD|Custo de Disponibilidade)\s+kWh[\s\S]{0,150}?\s(\d{2})[,.]0{2,3}\b",
    ]:
        m = re.search(pat, txt, re.I)
        if m:
            v = float(m.group(1))
            if 5 <= v <= 40:
                return v
    # Garbled ENEL GO: "18, 0 0 0"
    m = re.search(r"(?<!\d)(\d{2}),\s*0\s+0\s+0\b", txt)
    if m:
        v = float(m.group(1))
        if 5 <= v <= 40:
            return v
    # EDP ES / EDP SP — fallback por identificação
    if re.search(r"EDP\s+ES\b|EDP\s+ESP[IÍ]RITO", txt, re.I):
        return 17.0
    if re.search(r"EDP\s+S[AÃ]O\s+PAULO|EDP\s+SP\b", txt, re.I):
        return 12.0
    return 0.0


def _extract_pis_aliquota(txt: str) -> float:
    m = re.search(r"PIS(?:/PASEP)?\s+[\d.,]+\s+([\d.,]+)\s*%", txt, re.I)
    if m:
        v = _br2f(m.group(1))
        if 0.1 <= v <= 5:
            return v
    m = re.search(r"PIS(?:/PASEP)?\s+[\d.,]+\s+([\d.,]+)\s+[\d.,]+", txt, re.I)
    if m:
        v = _br2f(m.group(1))
        if 0.1 <= v <= 5:
            return v
    return 0.0


def _extract_cofins_aliquota(txt: str) -> float:
    m = re.search(r"COFINS\s+[\d.,]+\s+([\d.,]+)\s*%", txt, re.I)
    if m:
        v = _br2f(m.group(1))
        if 0.5 <= v <= 10:
            return v
    m = re.search(r"COFINS\s+[\d.,]+\s+([\d.,]+)\s+[\d.,]+", txt, re.I)
    if m:
        v = _br2f(m.group(1))
        if 0.5 <= v <= 10:
            return v
    return 0.0


def _extract_icms_base(txt: str) -> float:
    """Extrai base de cálculo do ICMS (o valor R$ sobre o qual incide a alíquota)."""
    # Padrão geral: "ICMS 5.661,71 23,0000 1.302,19" ou "ICMS 5.300,79 24% 1.272,19"
    m = re.search(r'\bICMS\b\s+([\d.,]+)\s+([\d.,]+)\s*%?\s*([\d.,]+)', txt, re.I)
    if m:
        v = _br2f(m.group(1))
        if v > 100:
            return v
    # Ampla/ENEL RJ: "I CMS 3.551,76 24,00 852,39"
    m = re.search(r'\bI\s*CMS\b\s+([\d.,]+)\s+([\d.,]+)\s*%?\s*([\d.,]+)', txt, re.I)
    if m:
        v = _br2f(m.group(1))
        if v > 100:
            return v
    # ENEL GO texto garbled: base do PIS (= base ICMS) aparece nas linhas TUSD/TE
    # Ex: "PIS 4237,96 0 , 7 1 30,09"
    for line in txt.split('\n'):
        if re.search(r'\bTUSD\b', line) or re.search(r'\bTE\b', line):
            m = re.search(r'PIS\s+([\d]{3,6}[.,]\d{2})', line)
            if m:
                v = _br2f(m.group(1))
                if v > 100:
                    return v
    return 0.0


def _extract_consumo_fp_valor(txt: str) -> float:
    """Extrai valor R$ do consumo fora ponta (TUSD+TE combinados)."""

    def _vals_br(linha: str) -> list[float]:
        # (?!\d) evita match parcial em "3.280,0000" (capturaria "3.280,00")
        return [_br2f(m) for m in re.findall(r'\d{1,3}\.\d{3},\d{2}(?!\d)', linha)]

    # Pattern 0: CPFL/RGE Sul — pdfplumber colapsa espaços ("TUSDMAI/26", "Consumo-TEMAI/26")
    # \bTUSD\b não bate em "TUSDMAI"; usamos presença simples com contexto "CONSUMO"
    _total0, _found_tusd, _found_te = 0.0, False, False
    for _line in txt.split('\n'):
        _up = _line.upper()
        if re.search(r'INJET|CR[EÉ]DIT|GD\b', _line, re.I):
            continue
        if not _found_tusd and 'TUSD' in _up and 'CONSUMO' in _up:
            _cands = [v for v in _vals_br(_line) if 100 < v < 100_000]
            if _cands:
                _total0 += _cands[0]
                _found_tusd = True
        elif not _found_te and 'TUSD' not in _up and re.search(r'CONSUMO.{0,5}TE', _up):
            _cands = [v for v in _vals_br(_line) if 100 < v < 100_000]
            if _cands:
                _total0 += _cands[0]
                _found_te = True
    if _total0 > 10:
        return _total0

    # Pattern 1: ENEL GO — soma TUSD e TE (exclui GD/injetado/crédito)
    total = 0.0
    for nome in ['TUSD', 'TE']:
        for line in txt.split('\n'):
            if re.search(r'\b' + nome + r'\b', line) and not re.search(r'GD\b|Injet|Cr[eé]dit', line, re.I):
                candidatos = [v for v in _vals_br(line) if 100 < v < 100_000]
                if candidatos:
                    total += candidatos[0]
                    break
    if total > 10:
        return total

    # Pattern 2: "Energia Elétrica ... X.XXX,XX" (Light RJ, EDP ES, RGE)
    for line in txt.split('\n'):
        if re.search(r'Energia El[eé]trica', line, re.I) and not re.search(r'GD\b|Injet', line, re.I):
            candidatos = [v for v in _vals_br(line) if 100 < v < 100_000]
            if candidatos:
                return candidatos[0]

    # Pattern 3: Equatorial MA — "Consumo (kWh) ..." → maior valor da linha
    for line in txt.split('\n'):
        if re.search(r'^Consumo\s*\(kWh\)', line, re.I):
            candidatos = [v for v in _vals_br(line) if 100 < v < 100_000]
            if candidatos:
                return max(candidatos)

    # Pattern 4: Ampla/ENEL RJ — "Energia Atv Forn"
    for line in txt.split('\n'):
        if re.search(r'Energia Atv Forn', line, re.I):
            candidatos = [v for v in _vals_br(line) if 100 < v < 100_000]
            if candidatos:
                return candidatos[0]

    # Pattern 5: Subtotal Faturamento (fallback)
    m = re.search(r'Subtotal Faturamento\s+([\d.,]+)', txt, re.I)
    if m:
        v = _br2f(m.group(1))
        if v > 100:
            return v

    return 0.0


# Alíquotas fixas BT (Lei 9430 — retenção 5,85%)
_ALIQ_BT_5_85: dict[str, float] = {
    "IRPJ": 1.20, "PIS": 0.65, "COFINS": 3.00, "CSLL": 1.00,
}

_TRIBUTO_RETER_MAP: dict[str, tuple[str, str]] = {
    "IRPJ":   ("fatDescIrpjPercRetImposto",   "fatDescIrpjValRetImposto"),
    "PIS":    ("fatDescPisPercRetImposto",    "fatDescPisValRetImposto"),
    "COFINS": ("fatDescCofinsPercRetImposto", "fatDescCofinsValRetImposto"),
    "CSLL":   ("fatDescCsllPercRetImposto",   "fatDescCsllValRetImposto"),
}


def _extract_retencoes(txt: str) -> dict[str, float]:
    """Captura retenções na fonte e retorna campos do schema Consen (valores negativos)."""
    result: dict[str, float] = {}

    # Formato RGE Sul: "RetencaoConsumoIRRF-1,2% 8,17-" (pdfplumber pode colapsar espaços)
    for cod, (campo_perc, campo_val) in {
        "CSLL": ("fatDescCsllPercRetImposto", "fatDescCsllValRetImposto"),
        "IRRF": ("fatDescIrrfPercRetImposto", "fatDescIrrfValRetImposto"),
    }.items():
        m = re.search(
            r"Retenc[aã]o\s*Consumo\s*" + cod + r"[^\d]*([\d.,]+)%?\s+([\d.,]+)",
            txt, re.I
        )
        if m:
            perc = _br2f(m.group(1))
            val  = _br2f(m.group(2))
            if val > 0:
                result[campo_perc] = perc
                result[campo_val]  = -val

    # PIS + COFINS combinados em fatDescConsumo (RGE Sul)
    perc_consumo, val_consumo = 0.0, 0.0
    for cod in ("PIS", "COFINS"):
        m = re.search(
            r"Retenc[aã]o\s*Consumo\s*" + cod + r"[^\d]*([\d.,]+)%?\s+([\d.,]+)",
            txt, re.I
        )
        if m:
            perc_consumo += _br2f(m.group(1))
            val_consumo  += _br2f(m.group(2))
    if val_consumo > 0:
        result["fatDescConsumoPercRetImposto"] = perc_consumo
        result["fatDescConsumoValRetImposto"]  = -val_consumo

    # Formato Equatorial (CEMAR/CELPA/CEPISA): "Tributo a Reter IRPJ -66,20"
    # Alíquota não consta no PDF — aplica breakdown fixo 5,85% BT
    for cod, (campo_perc, campo_val) in _TRIBUTO_RETER_MAP.items():
        m = re.search(r"Tributo\s+a\s+Reter\s+" + cod + r"\s+(-?[\d.,]+)", txt, re.I)
        if m:
            val = abs(_br2f(m.group(1)))
            if val > 0:
                result[campo_perc] = _ALIQ_BT_5_85[cod]
                result[campo_val]  = -val

    return result


def _extract_consumo_total_ativo(txt: str) -> float:
    """Consumo ATIVO TOTAL leit_ant leit_atu fator N kWh → N (registrado GD Equatorial)."""
    m = re.search(
        r"Consumo\s+ATIVO\s+TOTAL\s+[\d.,]+\s+[\d.,]+\s+[\d.,]+\s+([\d.,]+)\s+kWh",
        txt, re.I,
    )
    if m:
        v = _br2f(m.group(1))
        if 1 <= v <= 999_999:
            return v
    return 0.0


def _extract_gd_equatorial(txt: str) -> dict[str, float]:
    """Campos GD Equatorial BT: consumo compensado e energia injetada de outra UC.

    Detecta linhas típicas do formato Equatorial PA/MA:
      Consumo (kWh) 100 1,320200 0,978300 9,12 25,08 132,02
      Consumo Compensado (kWh) 4.272 0,983237 0,728550 289,97 798,07 4.200,39
      Energia Inj. oUC 05/2026 oPT (kWh) 4.272 ... -289,97 -798,07 -4.200,39
    """
    out: dict[str, float] = {}

    # fatConFPontaIndValorReais: último valor da parte energética da linha "Consumo (kWh)"
    # PDF de duas colunas mescla a tabela de impostos (PIS/COFINS) na mesma linha —
    # cortamos antes do bloco de impostos para não capturar o valor do PIS.
    for line in txt.split("\n"):
        if re.match(r"^\s*Consumo\s*\(kWh\)\s", line, re.I):
            energia_part = re.split(r"\s+(?:PIS|ICMS|COFINS|Cip|Tributo)\b", line, maxsplit=1, flags=re.I)[0]
            nums = re.findall(r"[\d.]+,\d{2}(?!\d)", energia_part)
            if nums:
                out["fatConFPontaIndValorReais"] = _br2f(nums[-1])
            break

    # fatConFPontaInjetadoRegistrado / Faturado: "Consumo Compensado (kWh) N ..."
    m = re.search(r"Consumo\s+Compensado\s*\(kWh\)\s+([\d.,]+)", txt, re.I)
    if m:
        v = _br2f(m.group(1))
        if v > 0:
            out["fatConFPontaInjetadoRegistrado"] = v
            out["fatConFPontaInjetadoFaturado"] = v

    # fatConFPontaInjetadoValorReais: "Energia Inj. oUC ... -X.XXX,XX"
    # Consen armazena como positivo (o crédito é implícito pelo campo "Injetado")
    m = re.search(r"Energia\s+Inj\.?\s*oUC\b[^\n]+-\s*([\d.]+,\d{2})", txt, re.I)
    if m:
        v = _br2f(m.group(1))
        if v > 0:
            out["fatConFPontaInjetadoValorReais"] = v

    return out


def _extract_cip(txt: str) -> float:
    """CIP / Iluminação Pública."""
    for line in txt.split("\n"):
        if re.search(r"Cip[-\s]Ilum|Contrib\.?\s*Ilum|Ilumina[cç][aã]o\s+P[uú]blica|\bCIP\b", line, re.I):
            nums = re.findall(r"[\d.]+,\d{2}", line)
            if not nums:
                # Tenta valor sem separador de milhar (ex: "403,55")
                nums = re.findall(r"\d+,\d{2}", line)
            if nums:
                v = _br2f(nums[-1])
                if v > 0:
                    return v
    return 0.0


def _extract_bandeira(txt: str) -> float:
    """Valor total da bandeira tarifária (Adicional Bandeira / Bandeira Tarifária)."""
    for line in txt.split("\n"):
        if re.search(r"Adicional\s+Bandeira|Bandeira\s+Tarifária|Bandeira\s+(?:Vermelha|Amarela|Verde|Escassez)", line, re.I):
            nums = re.findall(r"[\d.]+,\d{2}", line)
            if not nums:
                nums = re.findall(r"\d+,\d{2}", line)
            if nums:
                v = _br2f(nums[-1])
                if v > 0:
                    return v
    return 0.0


def _detect_conc_cod(txt: str) -> str:
    txt_up = txt.upper()
    if re.search(r"EDP\s+ES\b|EDP\s+ESP[IÍ]RITO", txt_up, re.I):
        return "EDP ES"
    if re.search(r"EDP\s+S[AÃ]O\s+PAULO|EDP\s+SP\b", txt_up, re.I):
        return "EDP SP"
    if "LIGHT" in txt_up:
        return "LIGHT RJ"
    if "AMPLA" in txt_up or "ENEL RIO" in txt_up:
        return "ENEL RJ"
    if "ENEL CEARA" in txt_up or "ENEL CE" in txt_up:
        return "ENEL CE"
    if "RGE SUL" in txt_up:
        return "RGE SUL"
    return ""


def processar_pdf(path: str, src_original: str | None = None) -> dict:
    """Processa um PDF BT e retorna dict com campos para Consen."""
    with pdfplumber.open(str(path)) as pdf:
        txt = "\n".join(p.extract_text() or "" for p in pdf.pages)

    uc       = _extract_instalacao(txt, str(src_original or path))
    carimbo  = _carimbo_from_filename(str(path))
    nf       = _extract_nf(txt)
    emissao  = _extract_emissao(txt)
    mes_ref  = _extract_mes_ref(txt)
    vcto     = _extract_vencimento(txt)
    ant, atu = _extract_datas_leitura(txt)
    valor    = _extract_valor(txt)
    kwh           = _extract_consumo(txt)
    kwh_total     = _extract_consumo_total_ativo(txt)
    gd            = _extract_gd_equatorial(txt)
    icms          = _extract_icms(txt)
    icms_base     = _extract_icms_base(txt)
    icms_aliq     = _extract_icms_aliquota(txt)
    pis           = _extract_pis(txt)
    pis_aliq      = _extract_pis_aliquota(txt)
    cofins        = _extract_cofins(txt)
    cofins_aliq   = _extract_cofins_aliquota(txt)
    fp_val        = _extract_consumo_fp_valor(txt)
    cip           = _extract_cip(txt)
    bandeira      = _extract_bandeira(txt)
    barcode       = _extract_barcode(txt)
    retencoes     = _extract_retencoes(txt)
    conc_cod      = _detect_conc_cod(txt)

    def _fmt(d: dt.date | None) -> str:
        return d.strftime("%d/%m/%Y") if d else ""

    return {
        "fatCarimbo":                  carimbo,
        "concCod":                     conc_cod,
        "Instalacao":                  uc,
        "CODIGOCLIENTE":               uc,
        "NOTAFISCAL":                  nf,
        "CNPJ":                        "00000000000191",
        "fatDataEmissao":              _fmt(emissao),
        "fatDataVcto":                 _fmt(vcto),
        "fatDataLeituraAnterior":      _fmt(ant),
        "fatDataLeituraAtual":         _fmt(atu),
        "fatDataReferencia":           mes_ref.strftime("01/%m/%Y") if mes_ref else "",
        "fatValorFatura":              valor,
        "fatValorNotaFiscal":          valor,
        # Para GD: registrado = total medido, faturado = após compensação
        "fatConFPontaIndRegistrado":   kwh_total or kwh,
        "fatConFPontaIndFaturado":     kwh,
        "fatConFPontaIndValorReais":   gd.get("fatConFPontaIndValorReais") or fp_val,
        # Campos GD (zero para faturas sem geração distribuída)
        "fatConFPontaInjetadoRegistrado": gd.get("fatConFPontaInjetadoRegistrado", 0),
        "fatConFPontaInjetadoFaturado":   gd.get("fatConFPontaInjetadoFaturado", 0),
        "fatConFPontaInjetadoValorReais": gd.get("fatConFPontaInjetadoValorReais", 0),
        "fatIlumPublica":              cip,
        "fatValBandeira":              bandeira,
        "fatICMS":                     icms,
        "fatICMSBase":                 icms_base,
        "fatDesIcmsAliquota":          icms_aliq,
        "fatPIS":                      pis,
        "fatDescPisAliquota":          pis_aliq,
        "fatCOFINS":                   cofins,
        "fatDesCofinsAliquota":        cofins_aliq,
        "fatCodigoBarras":             barcode,
        # Retenções na fonte (valores negativos)
        "fatDescIrpjPercRetImposto":    retencoes.get("fatDescIrpjPercRetImposto", 0),
        "fatDescIrpjValRetImposto":     retencoes.get("fatDescIrpjValRetImposto", 0),
        "fatDescPisPercRetImposto":     retencoes.get("fatDescPisPercRetImposto", 0),
        "fatDescPisValRetImposto":      retencoes.get("fatDescPisValRetImposto", 0),
        "fatDescCofinsPercRetImposto":  retencoes.get("fatDescCofinsPercRetImposto", 0),
        "fatDescCofinsValRetImposto":   retencoes.get("fatDescCofinsValRetImposto", 0),
        "fatDescCsllPercRetImposto":    retencoes.get("fatDescCsllPercRetImposto", 0),
        "fatDescCsllValRetImposto":     retencoes.get("fatDescCsllValRetImposto", 0),
        "fatDescIrrfPercRetImposto":    retencoes.get("fatDescIrrfPercRetImposto", 0),
        "fatDescIrrfValRetImposto":     retencoes.get("fatDescIrrfValRetImposto", 0),
        "fatDescConsumoPercRetImposto": retencoes.get("fatDescConsumoPercRetImposto", 0),
        "fatDescConsumoValRetImposto":  retencoes.get("fatDescConsumoValRetImposto", 0),
    }
