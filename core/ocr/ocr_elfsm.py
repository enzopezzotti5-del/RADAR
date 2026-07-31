#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR Empresa Luz e Força Santa Maria S/A (ELFSM) — BT
=====================================================

Extrai campos das faturas da Luz e Força Santa Maria (Colatina-ES) para
alimentar o fluxo de digitação no Consen.

Uso:
    python ocr_elfsm.py --pasta "//servidor/ENZO" --saida "//servidor/ocr_elfsm_BT.xlsx"
    python ocr_elfsm.py --mes 04 --ano 2026
    python ocr_elfsm.py --carimbo "BB_2004500"

Saída:
    \\\\10.10.250.21\\Energia\\ARQUIVOS ENZO\\OCR ELFSM\\ocr_elfsm_BT_MMYYYY.xlsx
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd
import pdfplumber


# ── Configuração ──────────────────────────────────────────────────────────────

ROOT_DIR = Path("//10.10.250.21/Energia/ARQUIVOS ENZO")
OCR_DIR  = ROOT_DIR / "OCR ELFSM"

# TODO: confirmar o código da ELFSM no sistema CONSEN antes de produção.
# CONSEN > Cadastro > Concessionárias.
CONSEN_CONC_COD: int | None = None

MESES_PT = {
    "JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12,
}

HEADERS = [
    "Instalacao", "fatDataEmissao", "fatDataVcto", "fatValorFatura", "concCod",
    "fatDataCadastro", "fatDataLeituraAnterior", "fatDataLeituraAtual", "fatIlumPublica",
    "cadTarifaCod", "cadSubGrupoCod",
    "fatDemContratadaPonta", "fatDemContratadaFPonta",
    "fatDemPontaRegistrada", "fatDemFPontaIndRegistrada", "fatDemFPontaCapRegistrada",
    "fatDemPontaExcFaturada", "fatDemFPontaExcFaturada",
    "fatDemPontaExcRegistrada", "fatDemFPontaExcRegistrada",
    "fatDemPontaFaturada", "fatDemFPontaIndFaturada",
    "fatDemPontaUltra", "fatDemFPontaIndUltra",
    "fatConPontaRegistrado", "fatConFPontaIndRegistrado",
    "fatConFPontaCapRegistrado", "fatConIntermediarioRegistrado",
    "fatConPontaFaturado", "fatConFPontaIndFaturado",
    "fatConFPontaCapFaturado", "fatConIntermediarioFaturado",
    "fatConPontaExcRegistrado", "fatConFPontaIndExcRegistrado",
    "fatConFPontaCapExcRegistrado", "fatConPontaExcFaturado",
    "fatConFPontaIndExcFaturado", "fatConFPontaCapExcFaturado",
    "fatConPontaValorReais", "fatConFPontaIndValorReais",
    "fatICMS", "fatPIS", "fatCOFINS", "fatTributoFederalPerc", "fatTributoFederalVal",
    "fatValorNotaFiscal",
    "obsValor",
    "CNPJ", "fatDataReferencia",
    "fatConPontaInjetadoRegistrado", "fatConPontaInjetadoFaturado",
    "fatConFPontaInjetadoRegistrado", "fatConFPontaInjetadoFaturado",
    "fatCodigoBarras", "fatCarimbo", "usuCod",
    "fatDescPisAliquota", "fatDescCofinsAliquota", "fatDesIcmsAliquota",
    "fatDescPisValRetImposto", "fatDescCofinsValRetImposto",
    "fatDescCsllValRetImposto", "fatDescIrpjValRetImposto",
    "fatDescConsumoPercRetImposto", "fatDescConsumoValRetImposto",
    "ENDERECO", "NOTAFISCAL", "CODIGOCLIENTE",
    "TARIFA_DETECTADA", "ARQUIVO", "ERRO",
]

DATE_HEADERS = {
    "fatDataEmissao", "fatDataVcto", "fatDataCadastro",
    "fatDataLeituraAnterior", "fatDataLeituraAtual", "fatDataReferencia",
}
TEXT_HEADERS = {
    "Instalacao", "CNPJ", "ENDERECO", "NOTAFISCAL", "CODIGOCLIENTE",
    "fatCodigoBarras", "fatCarimbo", "usuCod",
    "TARIFA_DETECTADA", "ARQUIVO", "ERRO",
}


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ocr_elfsm")


# ── Utilitários ───────────────────────────────────────────────────────────────

def _mkdir_seguro(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def _to_ascii_upper(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(ch)
    ).upper()


def _to_float_br(value: str | float | int | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    txt = str(value).strip()
    if not txt:
        return None
    neg = txt.endswith("-") or txt.startswith("-")
    txt = txt.replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".").replace("%", "")
    txt = txt.lstrip("-").rstrip("-")
    try:
        return -float(txt) if neg else float(txt)
    except ValueError:
        return None


def _extract_pages(path: Path) -> tuple[list[str], list[str], str, str, str]:
    """
    Retorna (lines_original, lines_ascii, text_original, text_ascii, text_ascii_relaxed).

    text_ascii_relaxed usa x_tolerance padrão (~3) para recolher colunas adjacentes
    na mesma linha — útil para campos como TOTAL A PAGAR cujo valor fica numa coluna
    separada mas na mesma linha visual.
    """
    lines_original: list[str] = []
    pages_original: list[str] = []
    pages_relaxed: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=1, y_tolerance=1) or ""
            text_relax = page.extract_text(x_tolerance=5, y_tolerance=3) or ""
            pages_original.append(text)
            pages_relaxed.append(text_relax)
            lines_original.extend(line.strip() for line in text.splitlines() if line.strip())
    text_original = "\n".join(pages_original)
    text_ascii = _to_ascii_upper(text_original)
    text_ascii_relaxed = _to_ascii_upper("\n".join(pages_relaxed))
    lines_ascii = [_to_ascii_upper(line) for line in lines_original]
    return lines_original, lines_ascii, text_original, text_ascii, text_ascii_relaxed


def _limpar_data_com_espacos(raw: str) -> str:
    """
    O pdfplumber às vezes insere espaços dentro de datas.
    Ex: '2 5/02/2026' → '25/02/2026'.
    """
    raw = raw.strip()
    # dígito espaço dígito / → junta os dois dígitos do dia
    raw = re.sub(r"(\d)\s+(\d)(/\d{2}/\d{4})", r"\1\2\3", raw)
    # dígito único /mm/yyyy → adiciona zero
    raw = re.sub(r"^(\d)(/\d{2}/\d{4})$", r"0\1\2", raw)
    return raw.strip()


def _to_date(valor: str) -> dt.date | None:
    if not valor:
        return None
    valor = _limpar_data_com_espacos(valor)
    try:
        d, m, a = valor.strip().split("/")
        return dt.date(int(a), int(m), int(d))
    except Exception:
        return None


# ── Identificação rápida (sem ler o PDF completo) ────────────────────────────

def is_elfsm(text_ascii: str) -> bool:
    """Retorna True se o texto pertence à ELFSM."""
    return (
        "LUZ E FORCA SANTA MARIA" in text_ascii
        or "27.485.069/0001-09" in text_ascii.replace(" ", "")
        or "27485069000109" in re.sub(r"\D", "", text_ascii)
    )


def identificacao_rapida(pdf_path: Path) -> dict:
    """
    Lê apenas a primeira página e extrai:
        sistema, instalacao, mes_ref, grupo
    Usado pelo pipeline para classificar e registrar no master antes do OCR completo.
    """
    resultado = {"sistema": None, "instalacao": "", "mes_ref": "", "grupo": ""}
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = (pdf.pages[0].extract_text(x_tolerance=1, y_tolerance=1) or "") if pdf.pages else ""
        text_ascii = _to_ascii_upper(text)

        # Concessionária
        if is_elfsm(text_ascii):
            resultado["sistema"] = "ELFSM"
        elif "COMPANHIA ENERGETICA DE PERNAMBUCO" in text_ascii or "10.835.932/0001-08" in text_ascii.replace(" ", ""):
            resultado["sistema"] = "CELPE"
        else:
            resultado["sistema"] = "DESCONHECIDA"

        # Instalação / UC
        m = re.search(r"IDENTIFICACAO\s*:\s*(\d+)", text_ascii)
        if m:
            resultado["instalacao"] = m.group(1).zfill(6)

        # Mês/ano de referência → formato MM-YYYY (padrão do master)
        m_ref = re.search(r"MES/ANO\s*:\s*([A-Z]{3})/(\d{4})", text_ascii)
        if m_ref:
            mes_num = MESES_PT.get(m_ref.group(1)[:3])
            if mes_num:
                resultado["mes_ref"] = f"{mes_num:02d}-{m_ref.group(2)}"

        # Grupo (B/A)
        m_grp = re.search(r"GRUPO\s*/\s*SUBGRUPO\s*:\s*([AB])/\s*([A-Z0-9]+)", text_ascii)
        if m_grp:
            resultado["grupo"] = m_grp.group(1)

    except Exception as exc:
        log.warning("  identificacao_rapida %s: %s", pdf_path.name, exc)

    return resultado


# ── Parsers ELFSM ────────────────────────────────────────────────────────────

def _parse_instalacao(text_ascii: str) -> str:
    m = re.search(r"IDENTIFICACAO\s*:\s*(\d+)", text_ascii)
    return m.group(1).zfill(6) if m else ""


def _parse_vencimento(text_ascii: str) -> str:
    m = re.search(r"VENCIMENTO\s*:\s*(\d{2}/\d{2}/\d{4})", text_ascii)
    return m.group(1) if m else ""


def _parse_valor_total(text_ascii: str, text_ascii_relaxed: str = "") -> float | None:
    """
    Extrai o valor total da fatura.

    Tenta em ordem:
    1. text_ascii (x_tolerance=1) — procura "TOTAL A PAGAR : R$ X" na mesma linha
    2. text_ascii_relaxed (x_tolerance=5) — pdfplumber reúne colunas adjacentes,
       fazendo "TOTAL A PAGAR :" e "R$ X" aparecerem na mesma linha
    3. Fallback: linha com "Para pagar pelo PIX"
    4. Fallback: R$ isolado logo após "TOTAL A PAGAR" em janela de 120 chars
    """
    for ta in (text_ascii, text_ascii_relaxed):
        if not ta:
            continue
        m = re.search(r"TOTAL A PAGAR\s*:[\s\n]*R\$\s*([\d\.]+,\d+)", ta)
        if m:
            return _to_float_br(m.group(1))
        # Sem "R$" explícito — valor logo após ":"
        m = re.search(r"TOTAL A PAGAR\s*:\s*([\d\.]+,\d+)", ta)
        if m:
            return _to_float_br(m.group(1))

    # Fallback 3: "Vencimento DD/MM/YYYY  R$ X,XX" → linha do boleto/PIX
    for ta in (text_ascii, text_ascii_relaxed):
        if not ta:
            continue
        m2 = re.search(r"([\d\.]+,\d+)\s+Para pagar pelo PIX", ta, re.IGNORECASE)
        if m2:
            return _to_float_br(m2.group(1))

    # Fallback 4: R$ dentro de janela após "TOTAL A PAGAR"
    for ta in (text_ascii, text_ascii_relaxed):
        if not ta:
            continue
        idx = ta.find("TOTAL A PAGAR")
        if idx != -1:
            janela = ta[idx:idx + 120]
            m3 = re.search(r"R\$\s*([\d\.]+,\d+)", janela)
            if m3:
                return _to_float_br(m3.group(1))
            m3b = re.search(r"([\d\.]+,\d+)", janela)
            if m3b:
                return _to_float_br(m3b.group(1))

    return None


def _parse_nf_emissao(text_ascii: str) -> tuple[str, str]:
    m = re.search(
        r"NF3E\s+N[O°]\s*(\d+)\s*-\s*SERIE\s*\d+\s*-\s*DATA DE EMISSAO\s*:\s*(\d{2}/\d{2}/\d{4})",
        text_ascii,
    )
    if m:
        return m.group(1), m.group(2)
    m2 = re.search(r"DATA DE EMISSAO\s*:\s*(\d{2}/\d{2}/\d{4})", text_ascii)
    m3 = re.search(r"NF3E\s+N[O°]\s*(\d+)", text_ascii)
    return (m3.group(1) if m3 else ""), (m2.group(1) if m2 else "")


def _parse_referencia(text_ascii: str) -> dt.date | None:
    m = re.search(r"MES/ANO\s*:\s*([A-Z]{3})/(\d{4})", text_ascii)
    if not m:
        return None
    mes_num = MESES_PT.get(m.group(1)[:3])
    if not mes_num:
        return None
    return dt.date(int(m.group(2)), mes_num, 1)


def _parse_grupo_subgrupo(text_ascii: str) -> tuple[str, str]:
    m = re.search(r"GRUPO\s*/\s*SUBGRUPO\s*:\s*([AB])/\s*([A-Z0-9]+)", text_ascii)
    return (m.group(1).strip(), m.group(2).strip()) if m else ("", "")


def _parse_tarifa(text_ascii: str) -> str:
    m = re.search(r"MODALIDADE TARIFARIA\s*:\s*(.+?)(?:\s{2,}|$)", text_ascii)
    if m:
        t = m.group(1).strip()
        if "VERDE" in t:
            return "Verde"
        if "AZUL" in t:
            return "Azul"
    return "Convencional"


def _parse_leituras(text_ascii: str) -> tuple[str, str]:
    """Retorna (data_anterior, data_atual)."""
    m_ant = re.search(r"ANTERIOR\s*:\s*(\d{1,2}\s*\d*/\d{2}/\d{4})", text_ascii)
    m_atu = re.search(r"ATUAL\s*:\s*(\d{1,2}\s*\d*/\d{2}/\d{4})", text_ascii)
    ant = _limpar_data_com_espacos(m_ant.group(1)) if m_ant else ""
    atu = _limpar_data_com_espacos(m_atu.group(1)) if m_atu else ""
    return ant, atu


def _parse_consumo_kwh(lines_ascii: list[str]) -> tuple[float | None, float | None]:
    """Consumo BT: primeira linha 'CONSUMO KWH ...' → (quantidade_kwh, valor_r$).

    Layout ELFSM: qty | preco_unit | valor_R$ | ... | icms_base | icms_aliq% | icms_valor
    """
    for line in lines_ascii:
        if re.match(r"^CONSUMO\s+KWH\s", line):
            nums = re.findall(r"[\d\.]+,\d+", line)
            kwh   = _to_float_br(nums[0]) if nums else None
            valor = _to_float_br(nums[2]) if len(nums) >= 3 else (
                    _to_float_br(nums[-1]) if len(nums) >= 2 else None)
            return kwh, valor
    return None, None


def _parse_tributo_federal(lines_ascii: list[str]) -> tuple[float | None, float | None]:
    """Retorna (percentual, valor) da linha 'TRIBUTO FEDERAL'."""
    for line in lines_ascii:
        if "TRIBUTO FEDERAL" in line:
            nums = re.findall(r"-?[\d\.]+,\d+", line)
            if len(nums) >= 2:
                return _to_float_br(nums[0]), _to_float_br(nums[1])
            if len(nums) == 1:
                return None, _to_float_br(nums[0])
    return None, None


def _parse_cosip(lines_ascii: list[str]) -> float | None:
    for line in lines_ascii:
        if "CONTR IL PUB MUNIC" in line:
            nums = re.findall(r"[\d\.]+,\d+", line)
            # estrutura: qty | preço_unit | valor_total
            if len(nums) >= 3:
                return _to_float_br(nums[-1])
            if len(nums) == 2:
                return _to_float_br(nums[-1])
    return None


def _parse_icms(lines_ascii: list[str]) -> tuple[float | None, float | None, float | None]:
    """Retorna (valor_icms, aliquota_icms, base_icms).

    base_icms = base de cálculo do ICMS = fatValorNotaFiscal.
    """
    icms_val = None
    icms_aliq = None
    icms_base = None

    for line in lines_ascii:
        # TOTAIS: último número é ICMS valor
        if re.match(r"^TOTAIS\s", line):
            nums = re.findall(r"[\d\.]+,\d+", line)
            if len(nums) >= 4:
                icms_val = _to_float_br(nums[3])

        # Linha CONSUMO KWH: nums[4]=base_icms, nums[5]=alíquota, nums[6]=valor ICMS
        if re.match(r"^CONSUMO\s+KWH\s", line):
            nums = re.findall(r"[\d\.]+,\d+", line)
            if len(nums) >= 7:
                icms_base = _to_float_br(nums[4])
                icms_aliq = _to_float_br(nums[5])
                if icms_val is None:
                    icms_val = _to_float_br(nums[6])
            elif len(nums) >= 5:
                icms_aliq = _to_float_br(nums[-2])
                if icms_val is None:
                    icms_val = _to_float_br(nums[-1])

    return icms_val, icms_aliq, icms_base


def _parse_pis_cofins(lines_ascii: list[str], text_ascii: str) -> tuple[float | None, float | None, float | None, float | None]:
    """
    Retorna (pis_val, pis_aliq, cofins_val, cofins_aliq).

    Layout ELFSM no PDF: as linhas PIS/COFINS ficam na tabela de Tributos.
    Com x_tolerance=1 o pdfplumber pode mesclar a linha do histórico kWh com COFINS.
    Usamos regex no texto completo como fallback robusto.
    """
    pis_val = pis_aliq = cofins_val = cofins_aliq = None

    # Tentativa 1: linha isolada começando com PIS ou COFINS
    for line in lines_ascii:
        if re.match(r"^PIS\s", line):
            nums = re.findall(r"[\d\.]+,\d+", line)
            if len(nums) >= 3:
                pis_aliq, pis_val = _to_float_br(nums[1]), _to_float_br(nums[2])
            elif len(nums) == 2:
                pis_aliq, pis_val = _to_float_br(nums[0]), _to_float_br(nums[1])
        elif re.match(r"^COFINS\s", line):
            nums = re.findall(r"[\d\.]+,\d+", line)
            if len(nums) >= 3:
                cofins_aliq, cofins_val = _to_float_br(nums[1]), _to_float_br(nums[2])
            elif len(nums) == 2:
                cofins_aliq, cofins_val = _to_float_br(nums[0]), _to_float_br(nums[1])

    # Tentativa 2: regex no texto completo — captura mesmo quando mesclado com outras colunas
    # Padrão: "PIS <base> <aliq> % <valor>" ou "PIS <base> <aliq>% <valor>"
    if pis_val is None:
        m = re.search(r"\bPIS\s+([\d\.]+,\d+)\s+([\d\.]+,\d+)\s*%\s*([\d\.]+,\d+)", text_ascii)
        if m:
            pis_aliq = _to_float_br(m.group(2))
            pis_val  = _to_float_br(m.group(3))

    if cofins_val is None:
        m = re.search(r"\bCOFINS\s+([\d\.]+,\d+)\s+([\d\.]+,\d+)\s*%\s*([\d\.]+,\d+)", text_ascii)
        if m:
            cofins_aliq = _to_float_br(m.group(2))
            cofins_val  = _to_float_br(m.group(3))

    return pis_val, pis_aliq, cofins_val, cofins_aliq


def _parse_codigo_barras(text_ascii: str) -> str:
    """Boleto ELFSM começa com 756xx."""
    m = re.search(r"(756\d{2}\.\d{5}\s+\d{5}\.\d{6}\s+\d{5}\.\d{6}\s+\d\s+\d{14})", text_ascii)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    return ""


def _parse_endereco(lines_original: list[str]) -> str:
    """Linha com CEP no formato XX.XXX-XXX = endereço do cliente."""
    for line in lines_original:
        la = _to_ascii_upper(line)
        if "CEP" in la and re.search(r"\d{2}\.\d{3}-\d{3}", la):
            # Remove tudo a partir de 'Protocolo'
            return line.split("Protocolo")[0].strip()
    return ""


def _extract_retencao_5_85(text_ascii: str) -> tuple[float, float]:
    """
    Localiza o bloco de retenções após PIS/COFINS, soma todos os valores listados
    até encontrar a alíquota 5,85% e retorna (5.85, -total).
    Esses valores vão para fatDescConsumoPercRetImposto / fatDescConsumoValRetImposto.
    """
    total = 0.0
    in_block = False
    for line in text_ascii.splitlines():
        if re.search(r"\bPIS\b|\bCOFINS\b", line):
            in_block = True
            continue
        if not in_block:
            continue
        if "5,85" in line or "5.85" in line:
            nums = re.findall(r"[\d\.]+,\d+", line)
            for n in nums:
                v = _to_float_br(n)
                if v and abs(v) < 99:
                    continue
                if v:
                    total += abs(v)
            break
        nums = re.findall(r"[\d\.]+,\d+", line)
        for n in nums:
            v = _to_float_br(n)
            if v and v > 0:
                total += v
    if total:
        return 5.85, -round(total, 2)
    return 0.0, 0.0


def _parse_observacoes(text_ascii: str) -> str | None:
    """Extrai campo de observações da fatura ELFSM."""
    m = re.search(r"OBSERVA[C\xc7][O\xd5]ES?\s*[:\-]?\s*(.+?)(?:\n|$)", text_ascii, re.IGNORECASE)
    if m:
        return m.group(1).strip() or None
    return None


def _subgrupo_para_cod_consen(grupo: str, subgrupo: str) -> str | int | None:
    grp = (grupo or "").strip().upper()
    sg = (subgrupo or "").strip().upper()
    if grp == "B":
        return "B3 [<2,3kV]"
    mapa = {"B1": 1, "B2": 2, "B3": 3, "B4": 4, "B30": 5, "A4": 5}
    return mapa.get(sg)


# ── Extração principal ────────────────────────────────────────────────────────

def extrair_campos(pdf_path: Path, carimbo_override: str | None = None) -> dict:
    """
    Extrai todos os campos da fatura ELFSM.

    carimbo_override: se informado, usa este carimbo no lugar de derivar do nome do arquivo.
    """
    row: dict = {h: None for h in HEADERS}
    row["ARQUIVO"]    = str(pdf_path)
    row["fatCarimbo"] = carimbo_override if carimbo_override else pdf_path.stem
    row["ERRO"]       = ""

    try:
        lines_original, lines_ascii, text_original, text_ascii, text_ascii_relaxed = _extract_pages(pdf_path)
    except Exception as exc:
        row["ERRO"] = f"Falha ao ler PDF: {exc}"
        log.error("  Erro ao ler %s: %s", pdf_path.name, exc)
        return row

    if not is_elfsm(text_ascii):
        row["ERRO"] = "nao_elfsm"
        log.warning("  %s: não identificado como ELFSM — ignorado", pdf_path.name)
        return row

    try:
        row["Instalacao"]       = _parse_instalacao(text_ascii)
        row["fatDataVcto"]      = _to_date(_parse_vencimento(text_ascii))
        row["fatValorFatura"]   = _parse_valor_total(text_ascii, text_ascii_relaxed)
        nf_num, nf_data         = _parse_nf_emissao(text_ascii)
        row["NOTAFISCAL"]       = nf_num
        row["fatDataEmissao"]   = _to_date(nf_data)
        row["fatDataReferencia"] = _parse_referencia(text_ascii)

        grupo, subgrupo         = _parse_grupo_subgrupo(text_ascii)
        row["cadSubGrupoCod"]   = _subgrupo_para_cod_consen(grupo, subgrupo) if subgrupo else None
        row["TARIFA_DETECTADA"] = f"{grupo}/{subgrupo} {_parse_tarifa(text_ascii)}"

        dt_ant, dt_atu               = _parse_leituras(text_ascii)
        row["fatDataLeituraAnterior"] = _to_date(dt_ant)
        row["fatDataLeituraAtual"]    = _to_date(dt_atu)

        consumo_kwh, consumo_val = _parse_consumo_kwh(lines_ascii)
        row["fatConFPontaIndFaturado"]   = consumo_kwh
        row["fatConFPontaIndRegistrado"] = consumo_kwh
        row["fatConPontaValorReais"]     = None
        row["fatConFPontaIndValorReais"] = consumo_val

        row["fatIlumPublica"] = _parse_cosip(lines_ascii)

        icms_v, icms_a, icms_base = _parse_icms(lines_ascii)
        row["fatICMS"]            = icms_v
        row["fatDesIcmsAliquota"] = icms_a
        row["fatValorNotaFiscal"] = icms_base if icms_base is not None else row["fatValorFatura"]

        pis_v, pis_a, cof_v, cof_a     = _parse_pis_cofins(lines_ascii, text_ascii)
        row["fatPIS"]                   = pis_v
        row["fatDescPisAliquota"]       = pis_a
        row["fatCOFINS"]                = cof_v
        row["fatDescCofinsAliquota"]    = cof_a

        ret_perc, ret_val = _extract_retencao_5_85(text_ascii)
        row["fatDescConsumoPercRetImposto"] = ret_perc if ret_perc else None
        row["fatDescConsumoValRetImposto"]  = ret_val if ret_val else None

        trib_perc, trib_val             = _parse_tributo_federal(lines_ascii)
        row["fatTributoFederalPerc"]    = trib_perc
        row["fatTributoFederalVal"]     = trib_val

        row["obsValor"] = _parse_observacoes(text_ascii)

        row["fatCodigoBarras"] = _parse_codigo_barras(text_ascii)
        row["ENDERECO"]        = _parse_endereco(lines_original)

        row["concCod"]      = CONSEN_CONC_COD
        row["cadTarifaCod"] = "Convencional" if grupo == "B" else 1

    except Exception as exc:
        row["ERRO"] = str(exc)
        log.error("  Erro ao extrair campos de %s: %s", pdf_path.name, exc)

    return row


# ── Geração do XLSX ───────────────────────────────────────────────────────────

def gerar_xlsx(linhas: list[dict], caminho: Path) -> None:
    _mkdir_seguro(caminho.parent)
    df = pd.DataFrame(linhas, columns=HEADERS)

    for col in HEADERS:
        if col in DATE_HEADERS:
            df[col] = pd.to_datetime(df[col], errors="coerce")
        elif col not in TEXT_HEADERS:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    with pd.ExcelWriter(
        caminho, engine="openpyxl",
        date_format="DD/MM/YYYY", datetime_format="DD/MM/YYYY",
    ) as writer:
        df.to_excel(writer, index=False, sheet_name="OCR_ELFSM_BT")
        ws = writer.sheets["OCR_ELFSM_BT"]
        for col_cells in ws.columns:
            max_len = max((len(str(c.value or "")) for c in col_cells), default=8)
            ws.column_dimensions[col_cells[0].column_letter].width = min(max_len + 2, 30)
        date_cols = [i + 1 for i, h in enumerate(HEADERS) if h in DATE_HEADERS]
        for col_num in date_cols:
            for row_num in range(2, len(linhas) + 2):
                cell = ws.cell(row=row_num, column=col_num)
                if cell.value is not None:
                    cell.number_format = "DD/MM/YYYY"

    log.info("XLSX salvo: %s  (%d faturas)", caminho, len(linhas))


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    hoje = dt.date.today()
    p = argparse.ArgumentParser(description="OCR ELFSM BT — Luz e Força Santa Maria")
    p.add_argument("--mes",  type=str, default=f"{hoje.month:02d}")
    p.add_argument("--ano",  type=str, default=str(hoje.year))
    p.add_argument("--pasta", type=str, default="")
    p.add_argument("--recursivo", action="store_true")
    p.add_argument("--carimbo", action="append", default=[],
                   help="Filtra por carimbo(s). Ex: --carimbo BB_2004500")
    p.add_argument("--saida", type=str, default="",
                   help="Caminho completo do XLSX de saída")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    mes = f"{int(args.mes):02d}"
    ano = str(int(args.ano))

    pasta = Path(args.pasta.strip()) if args.pasta.strip() else OCR_DIR / f"{mes}.{ano}"
    xlsx_saida = Path(args.saida.strip()) if args.saida.strip() else OCR_DIR / f"ocr_elfsm_BT_{mes}{ano}.xlsx"

    log.info("=" * 60)
    log.info("  OCR ELFSM BT  %s/%s", mes, ano)
    log.info("=" * 60)
    log.info("  Pasta PDFs : %s", pasta)
    log.info("  XLSX saída : %s", xlsx_saida)

    if not pasta.exists():
        log.error("Pasta não encontrada: %s", pasta)
        return 1

    carimbos_filtro = {c.strip() for c in args.carimbo if c.strip()}
    pdfs = sorted(pasta.rglob("*.pdf")) if args.recursivo else sorted(pasta.glob("*.pdf"))
    if carimbos_filtro:
        pdfs = [p for p in pdfs if p.stem in carimbos_filtro
                or p.stem.upper() in {c.upper() for c in carimbos_filtro}]
    if not pdfs:
        log.warning("Nenhum PDF encontrado em: %s", pasta)
        return 2

    linhas: list[dict] = []
    ignorados = erros = 0

    for pdf in pdfs:
        log.info("  Processando: %s", pdf.name)
        row = extrair_campos(pdf)
        if row.get("ERRO") == "nao_elfsm":
            ignorados += 1
            continue
        if row.get("ERRO"):
            erros += 1
        linhas.append(row)

    log.info("-" * 60)
    log.info("  Total PDFs   : %d", len(pdfs))
    log.info("  Processados  : %d", len(linhas))
    log.info("  Ignorados    : %d  (não ELFSM)", ignorados)
    log.info("  Com erros    : %d", erros)

    if not linhas:
        log.warning("Nenhuma fatura ELFSM extraída.")
        return 2

    if CONSEN_CONC_COD is None:
        log.warning("ATENÇÃO: CONSEN_CONC_COD não configurado em ocr_elfsm.py")

    gerar_xlsx(linhas, xlsx_saida)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
