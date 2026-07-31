#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR Energisa Rondonia BT -> XLSX para digitacao no Consen.

Primeira base de producao para faturas BT da Energisa na pasta mista ENZO.
Reaproveita o layout de saida do OCR Neoenergia para manter compatibilidade
com a digitacao existente.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path

LOCAL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LOCAL_DIR))

import pdfplumber

from ocr.ocr_neoenergia import (
    MAX_WORKERS,
    OUTPUT_DIR as NEO_OUTPUT_DIR,
    _carimbo_do_nome,
    _empty_record,
    _extract_codigo_barras,
    _extract_debitos_anteriores,
    _extract_ilum_publica,
    _extract_imposto,
    _extract_instalacao,
    _extract_notafiscal,
    _extract_pdf_data,
    _extract_total,
    _extract_tributo_federal,
    _extract_valor_nota_fiscal,
    _norm,
    _texto_normalizado,
    _to_date,
    _to_float_br,
    salvar_excel,
)


OUTPUT_DIR = NEO_OUTPUT_DIR.parent / "OCR ENERGISA"
DEFAULT_PASTA = Path("//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO")

MESES_PT = {
    "JAN": 1,
    "FEV": 2,
    "MAR": 3,
    "ABR": 4,
    "MAI": 5,
    "JUN": 6,
    "JUL": 7,
    "AGO": 8,
    "SET": 9,
    "OUT": 10,
    "NOV": 11,
    "DEZ": 12,
    "JANEIRO": 1,
    "FEVEREIRO": 2,
    "MARCO": 3,
    "ABRIL": 4,
    "MAIO": 5,
    "JUNHO": 6,
    "JULHO": 7,
    "AGOSTO": 8,
    "SETEMBRO": 9,
    "OUTUBRO": 10,
    "NOVEMBRO": 11,
    "DEZEMBRO": 12,
}

RETENCOES_PERC = {
    "PIS": 0.65,
    "COFINS": 3.0,
    "CSLL": 1.0,
    "IRPJ": 1.2,
}


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ocr_energisa_bt")


def _digits(text: str) -> str:
    return re.sub(r"\D", "", str(text or ""))


def _normalizar_codigo_energisa(valor: str) -> str:
    txt = _norm(str(valor or ""))
    if not txt:
        return ""
    txt = txt.replace(" ", "")
    return txt


def _is_layout_contingencia_compacto(text: str) -> bool:
    txt = _texto_normalizado(text)
    return (
        "EMITIDO EM CONTINGENCIA" in txt
        and "PENDENTE DE AUTORIZACAO" in txt
        and "CADASTRE SUA FATURA EM DEBITO AUTOMATICO" in txt
    )


def _extract_codigo_auto_debito_energisa(text: str) -> str:
    text_norm = _texto_normalizado(text)
    patterns = [
        r"UTILIZANDO\s+O\s+CODIGO:?\s*([0-9./-]+)",
        r"UTILIZE\s+O\s+CODIGO:?\s*([0-9./-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text_norm, flags=re.IGNORECASE)
        if match:
            numero = _normalizar_codigo_energisa(match.group(1))
            if numero:
                return numero
    return ""


def _extract_codigo_pontuado_energisa(text: str) -> str:
    match = re.search(r"\b(\d{1,3}(?:\.\d{3}){2,3}-\d{2})\b", text)
    if not match:
        return ""
    return _normalizar_codigo_energisa(match.group(1))


def _codigo_20_derivado_debito_energisa(text: str) -> str:
    codigo_auto = _extract_codigo_auto_debito_energisa(text)
    if not codigo_auto:
        return ""

    match = re.search(r"^0*(\d{6,7})-?(\d)$", codigo_auto)
    if not match:
        return ""
    return f"20/{match.group(1)}-{match.group(2)}"


def _extract_identificador_principal_energisa(text: str) -> str:
    patterns = [
        r"^\s*(\d{1,2}/\d{6,7}-\d)\s*$",
        r"NOTA\s+FISCAL.*?\n\s*(\d{1,2}/\d{6,7}-\d)\b",
        r"\b(\d{1,2}/\d{6,7}-\d)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE | re.DOTALL)
        if match:
            numero = _normalizar_codigo_energisa(match.group(1))
            if numero:
                return numero

    codigo_pontuado = _extract_codigo_pontuado_energisa(text)
    if codigo_pontuado:
        return codigo_pontuado

    codigo_20 = _codigo_20_derivado_debito_energisa(text)
    if codigo_20:
        return codigo_20

    return ""


def _extrair_aliquota_de_segmento(segmento: str) -> float:
    numeros = re.findall(r"[-\d\.,]+", segmento)
    valores = [abs(_to_float_br(numero)) for numero in numeros[:4] if numero.strip()]
    if len(valores) >= 3 and valores[0] > 50 and valores[1] <= 100:
        return valores[1]
    # Formato com % na linha (ex: "COFINS 84,08 4,17% 3,51" → regex captura "84,08 4,17"):
    # base > 30 e segundo valor <= 30 → segundo valor é a alíquota
    if len(valores) == 2 and valores[0] > 30 and 0 < valores[1] <= 30:
        return valores[1]
    if len(valores) >= 2:
        return valores[0]
    return 0.0


def _ucs_do_nome(path: Path | None) -> tuple[str, str]:
    if path is None:
        return "", ""
    nome = path.stem
    antiga = ""
    nova = ""
    match_antiga = re.search(r"UC\s+ANTIGA\s+([0-9.\-]+)", nome, flags=re.IGNORECASE)
    match_nova = re.search(r"UC\s+NOVA\s+([0-9.\-]+)", nome, flags=re.IGNORECASE)
    if match_antiga:
        antiga = _digits(match_antiga.group(1))
    if match_nova:
        nova = _digits(match_nova.group(1))
    return antiga, nova


@lru_cache(maxsize=256)
def _arquivo_original_por_carimbo(carimbo: str) -> Path | None:
    carimbo_norm = str(carimbo or "").strip().upper()
    if not carimbo_norm.startswith("BB_"):
        return None
    repo_root = Path(__file__).resolve().parents[2]
    scripts_infra = repo_root / "scripts" / "infra"
    for extra in (str(repo_root), str(scripts_infra)):
        if extra not in sys.path:
            sys.path.insert(0, extra)
    try:
        from scripts.infra.indice_master import MASTER_FILE  # noqa: PLC0415
    except ModuleNotFoundError:
        from indice_master import MASTER_FILE  # noqa: PLC0415
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(MASTER_FILE, newline="", encoding=enc) as f:
                for row in csv.DictReader(f):
                    if str(row.get("INDICE", "")).strip().upper() != carimbo_norm:
                        continue
                    arquivo = str(row.get("ARQUIVO", "")).strip()
                    return Path(arquivo) if arquivo else None
            break
        except UnicodeDecodeError:
            continue
        except Exception:
            return None
    return None


def _is_energisa(text: str) -> bool:
    txt = _texto_normalizado(text)
    digits = _digits(txt)
    if "ENERGISA" not in txt:
        return False
    # Aceita tanto com espaços quanto texto colado (PDF sem espaços)
    if "DISTRIBUIDORA DE ENERGIA" in txt or "DISTRIBUIDORADEENERGIA" in txt:
        return True
    if "DISTRIB.ENERGIA" in txt or "DISTRIBENERGIA" in txt:
        return True
    return any(
        cnpj in digits
        for cnpj in (
            "05914650000166",  # RO
            "03467321000199",  # MT
            "25086034000171",  # TO
            "07282377",        # Sul-Sudeste (MG/SP) — base CNPJ, cobre filiais
            "13017462000163",  # SE (Sergipe)
            "19527639000158",  # MG/RJ (Minas Rio)
        )
    )


def _first_page_text(pdf_path: Path) -> str:
    with pdfplumber.open(str(pdf_path)) as pdf:
        if not pdf.pages:
            return ""
        return pdf.pages[0].extract_text(x_tolerance=1, y_tolerance=1) or ""


def _extract_instalacao_energisa(text: str, pdf_path: Path | None = None) -> str:
    identificador = _extract_identificador_principal_energisa(text)
    if identificador:
        return identificador

    match_uc_nova = re.search(r"\b([A-Z]\d{10})\s+kWh\s+Total\b", text, flags=re.IGNORECASE)
    if not match_uc_nova:
        match_uc_nova = re.search(r"\b([A-Z]\d{10})\b", text, flags=re.IGNORECASE)
    if match_uc_nova:
        return match_uc_nova.group(1)

    patterns = [
        r"UTILIZ(?:E|ANDO)\s+O\s+CODIGO:\s*([0-9./-]+)",
        r"\b(\d{1,3}(?:\.\d{3}){2,3}-\d{2})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            numero = _normalizar_codigo_energisa(match.group(1))
            if numero:
                return numero

    for line in text.splitlines():
        if "MATRICULA" in _texto_normalizado(line):
            numeros = re.findall(r"\d+", line)
            if numeros:
                return "".join(numeros[:2]) if len(numeros) > 1 else numeros[0]

    if pdf_path is not None:
        antiga, nova = _ucs_do_nome(pdf_path)
        if nova:
            return _normalizar_codigo_energisa(nova)
        if antiga:
            return _normalizar_codigo_energisa(antiga)

    fallback = _extract_instalacao(text, [])
    return _normalizar_codigo_energisa(fallback)


def _extract_codigo_cliente_energisa(text: str, instalacao: str) -> str:
    # Em alguns layouts MT o "CODIGO DO CLIENTE" aparece logo abaixo do bloco
    # da nota fiscal; esse valor deve prevalecer sobre derivacoes de debito automatico.
    for pattern in (
        r"NOTA\s+FISCAL.*?\n\s*([0-9]{1,2}/\d{6,7}-\d)\b",
        r"NOTA\s+FISCAL.*?\n\s*([0-9]{1,2}/\d{6,7}-\d)\s*\n",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            codigo_cliente = _normalizar_codigo_energisa(match.group(1))
            if codigo_cliente:
                return codigo_cliente

    identificador = _extract_identificador_principal_energisa(text)
    if identificador:
        return identificador

    codigo_auto = _extract_codigo_auto_debito_energisa(text)
    if codigo_auto:
        return codigo_auto

    return _normalizar_codigo_energisa(instalacao)


def _extract_referencia(text: str, mes_padrao: int, ano_padrao: int) -> dt.date:
    texto = _texto_normalizado(text)
    match = re.search(r"\b([A-ZÇ]+)\s*/\s*(\d{4})\b", texto)
    if match:
        mes = MESES_PT.get(match.group(1))
        ano = int(match.group(2))
        if mes:
            return dt.date(ano, mes, 1)
    return dt.date(ano_padrao, mes_padrao, 1)


def _extract_vencimento_energisa(text: str) -> dt.date | None:
    linhas = [_texto_normalizado(ln).strip() for ln in str(text or "").splitlines() if _norm(ln)]
    texto = "\n".join(linhas)

    def _linha_bloqueada(linha: str) -> bool:
        norm = _norm(linha)
        return any(
            termo in norm
            for termo in (
                "DATAS DE LEITURAS",
                "DATA DE LEITURA",
                "LEITURA ANTERIOR",
                "LEITURA ATUAL",
                "PROXIMA LEITURA",
                "PRÓXIMA LEITURA",
                "PROXIMA LEITURA PREVISTA",
                "PRÓXIMA LEITURA PREVISTA",
            )
        )

    def _datas_linha(linha: str) -> list[dt.date]:
        datas: list[dt.date] = []
        for raw in re.findall(r"\d{2}/\d{2}/\d{4}", linha):
            data = _to_date(raw)
            if data:
                datas.append(data)
        return datas

    def _linha_do_match(pos: int) -> str:
        inicio = texto.rfind("\n", 0, pos) + 1
        fim = texto.find("\n", pos)
        if fim == -1:
            fim = len(texto)
        return texto[inicio:fim]

    patterns = [
        r"\bVENCIMENTO\b\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})",
        r"\bDATA\s+DE\s+VENCIMENTO\b\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})",
        r"\bVENCE\s+EM\b\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})",
        r"\bVCTO\b\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})",
        r"\bVENCTO\b\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})",
        r"\bVENC\.?\b\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})",
        r"PAGAR\s+PREFERENCIALMENTE(?:\s+NO\s+[A-Z0-9./-]+)?\s+(\d{2}/\d{2}/\d{4})",
        r"LOCAL\s+DE\s+PAGAMENTO\s+VENCIMENTO\s+PIX[\s\S]{0,80}?(\d{2}/\d{2}/\d{4})",
        r"MATR[ÍI]CULA\s+VENCIMENTO[\s\S]{0,80}?(\d{2}/\d{2}/\d{4})\s+R\$",
    ]
    for pattern in patterns:
        match = re.search(pattern, texto, flags=re.IGNORECASE)
        if match:
            if _linha_bloqueada(_linha_do_match(match.start())):
                continue
            vcto = _to_date(match.group(1))
            if vcto:
                log.info(
                    "Energisa vencimento: %s | rotulo=VENCIMENTO | metodo=regex_explicit",
                    vcto.strftime("%d/%m/%Y"),
                )
                return vcto

    for idx, linha in enumerate(linhas):
        norm = _norm(linha)
        if "VENCIMENTO" not in norm or _linha_bloqueada(linha):
            continue

        datas_mesma = _datas_linha(linha)
        if len(datas_mesma) == 1:
            log.info(
                "Energisa vencimento: %s | rotulo=VENCIMENTO | metodo=mesma_linha",
                datas_mesma[0].strftime("%d/%m/%Y"),
            )
            return datas_mesma[0]
        if len(datas_mesma) > 1:
            log.warning("Energisa vencimento ambiguo na linha com VENCIMENTO: %s", linha)
            return None

        for prox in linhas[idx + 1 : idx + 5]:
            if _linha_bloqueada(prox):
                break
            datas = _datas_linha(prox)
            if len(datas) == 1:
                log.info(
                    "Energisa vencimento: %s | rotulo=VENCIMENTO | metodo=linha_proxima",
                    datas[0].strftime("%d/%m/%Y"),
                )
                return datas[0]
            if len(datas) > 1:
                match_valor = re.search(r"(\d{2}/\d{2}/\d{4})\s+R\$\s*[-\d\.,]+", prox)
                if match_valor:
                    vcto = _to_date(match_valor.group(1))
                    if vcto:
                        log.info(
                            "Energisa vencimento: %s | rotulo=VENCIMENTO | metodo=linha_proxima_antes_valor",
                            vcto.strftime("%d/%m/%Y"),
                        )
                        return vcto
                log.warning("Energisa vencimento ambiguo apos linha VENCIMENTO: %s", prox)
                return None
    return None


def _extract_vencimento_resumo_energisa(text: str) -> dt.date | None:
    """Aceita apenas o resumo que parece pertencer ao bloco de vencimento."""
    texto = _texto_normalizado(text)
    linhas = [ln for ln in texto.splitlines() if _norm(ln)]
    meses_hints = tuple(MESES_PT.keys())
    pattern = re.compile(
        r"(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+\d+\s+(\d{2}/\d{2}/\d{4})"
    )
    for line in linhas:
        match = pattern.search(line)
        if not match:
            continue
        line_norm = _texto_normalizado(line)
        if not (
            "R$" in line_norm
            or "VENCIMENTO" in line_norm
            or "PAGAR" in line_norm
            or any(mes in line_norm for mes in meses_hints)
        ):
            continue
        vcto = _to_date(match.group(3))
        if vcto:
            return vcto
    return None


def _extract_datas(text: str) -> tuple[dt.date | None, dt.date | None, dt.date | None]:
    match_resumo = re.search(
        r"(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+\d+\s+(\d{2}/\d{2}/\d{4})",
        text,
    )
    match_leituras = re.search(
        r"LEITURA\s+ANTERIOR:\s*(\d{2}/\d{2}/\d{4})\s+LEITURA\s+ATUAL:\s*(\d{2}/\d{2}/\d{4})"
        r".*?DIAS:\s*\d+\s*(?:\b|$)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match_leituras:
        ant = _to_date(match_leituras.group(1))
        atu = _to_date(match_leituras.group(2))
    else:
        ant = _to_date(match_resumo.group(1)) if match_resumo else None
        atu = _to_date(match_resumo.group(2)) if match_resumo else None
    vcto_resumo = _extract_vencimento_resumo_energisa(text)
    vcto_explicit = _extract_vencimento_energisa(text)

    match_vcto = re.search(
        r"\b([A-ZÇ]+)\s*/\s*\d{4}\s+(\d{2}/\d{2}/\d{4})\s+R\$\s*[-\d\.,]+",
        _texto_normalizado(text),
    )
    vcto = vcto_explicit or (_to_date(match_vcto.group(2)) if match_vcto else None)
    if _is_layout_contingencia_compacto(text) and not vcto and vcto_resumo:
        vcto = vcto_resumo
    elif not vcto and vcto_resumo:
        vcto = vcto_resumo

    return ant, atu, vcto


def _extract_data_emissao(text: str) -> dt.date | None:
    patterns = [
        r"DATA\s*DE\s*EMISSAO\s*:\s*(\d{2}/\d{2}/\d{4})",
        r"DATA\s*EMISSAO\s*/\s*APRESENTACAO\s*:\s*(\d{2}/\d{2}/\d{4})",
        r"DATA\s*DE\s*APRESENTACAO\s*:\s*(\d{2}/\d{2}/\d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, _texto_normalizado(text))
        if match:
            return _to_date(match.group(1))
    return None


def _extract_endereco_energisa(text: str) -> str:
    linhas = [_norm(line) for line in text.splitlines() if _norm(line)]
    inicio = -1
    for idx, line in enumerate(linhas):
        if re.search(r"\d{2}/\d{2}/\d{4}\s+\d{2}/\d{2}/\d{4}\s+\d+\s+\d{2}/\d{2}/\d{4}", line):
            inicio = idx + 1
            break
    if inicio < 0:
        return ""

    partes: list[str] = []
    for line in linhas[inicio:inicio + 6]:
        line_norm = _texto_normalizado(line)
        if any(token in line_norm for token in ("NOTA FISCAL", "DATA DE EMISSAO", "CONSULTE", "CNPJ/CPF/RANI")):
            break
        if re.fullmatch(r"[0-9./-]{8,}", line):
            continue
        partes.append(line)
    return _norm(" ".join(partes))


def _extract_notafiscal_energisa(text: str) -> str:
    match = re.search(r"NOTA\s+FISCAL\s+N[Oº°]*[:\s]*([0-9.]+)", _texto_normalizado(text))
    if match:
        return _digits(match.group(1))
    return _extract_notafiscal(text)


def _extract_total_energisa(text: str) -> float:
    match_total = re.search(r"\bTOTAL:\s*([-\d\.,]+)", text, flags=re.IGNORECASE)
    if match_total:
        return abs(_to_float_br(match_total.group(1)))

    match_resumo = re.search(
        r"\b[A-ZÇ]+\s*/\s*\d{4}\s+\d{2}/\d{2}/\d{4}\s+R\$\s*([-\d\.,]+)",
        _texto_normalizado(text),
    )
    if match_resumo:
        return abs(_to_float_br(match_resumo.group(1)))

    return abs(_extract_total(text))


def _is_energisa_sergipe(text: str) -> bool:
    txt = _texto_normalizado(text)
    digits = _digits(txt)
    return "SERGIPE" in txt or "13017462000163" in digits


def _parse_tributo_linha(line: str, rotulo: str) -> tuple[float, float, float] | None:
    line_norm = _texto_normalizado(line)
    if not line_norm or rotulo not in line_norm:
        return None
    if any(bloqueio in line_norm for bloqueio in ("IMP.RET.", "COBR.", "COBRANCA", "ESCASSEZ HIDRICA")):
        return None
    if rotulo == "ICMS" and line_norm.startswith("ICMS FCP"):
        return None

    m = re.search(rf"\b{re.escape(rotulo)}\b", line_norm)
    if not m:
        return None
    segmento = line[m.end():]
    numeros_txt = [n for n in re.findall(r"-?[\d\.,]+", segmento) if n.strip()]
    numeros = [abs(_to_float_br(n)) for n in numeros_txt]
    if not numeros:
        return None
    m_pct = re.search(r"([-\d\.,]+)\s*%", segmento)
    pct_val = abs(_to_float_br(m_pct.group(1))) if m_pct else 0.0

    if len(numeros) >= 3:
        if pct_val and abs(numeros[1] - pct_val) < 0.01:
            return numeros[0], numeros[1], numeros[2]
        if pct_val and abs(numeros[0] - pct_val) < 0.01:
            return numeros[1], numeros[0], numeros[2]
        return numeros[0], numeros[1], numeros[2]
    if len(numeros) == 2:
        if pct_val and abs(numeros[0] - pct_val) < 0.01:
            return 0.0, numeros[0], numeros[1]
        if pct_val and abs(numeros[1] - pct_val) < 0.01:
            return 0.0, numeros[1], numeros[0]
        return 0.0, numeros[0], numeros[1]
    return 0.0, 0.0, numeros[0]


def _extract_impostos_energisa(text: str) -> dict[str, float]:
    out = {
        "fatICMS": 0.0,
        "fatPIS": 0.0,
        "fatCOFINS": 0.0,
        "fatDesIcmsAliquota": 0.0,
        "fatDescPisAliquota": 0.0,
        "fatDesCofinsAliquota": 0.0,
        "fatICMSFCP": 0.0,
        "fatDesIcmsFcpAliquota": 0.0,
    }

    linhas = text.splitlines()
    for rotulo, campo_val, campo_aliq in (
        ("PIS/PASEP", "fatPIS", "fatDescPisAliquota"),
        ("PIS", "fatPIS", "fatDescPisAliquota"),
        ("COFINS", "fatCOFINS", "fatDesCofinsAliquota"),
        ("ICMS FCP", "fatICMSFCP", "fatDesIcmsFcpAliquota"),
        ("ICMS", "fatICMS", "fatDesIcmsAliquota"),
    ):
        for line in linhas:
            parsed = _parse_tributo_linha(line, rotulo)
            if not parsed:
                continue
            _base, aliquota, valor = parsed
            if campo_val == "fatICMSFCP":
                if valor > 0 and not out[campo_val]:
                    out[campo_val] = valor
                if aliquota > 0 and not out[campo_aliq]:
                    out[campo_aliq] = aliquota
                break
            if valor > 0 and not out[campo_val]:
                out[campo_val] = valor
            if aliquota > 0 and not out[campo_aliq]:
                out[campo_aliq] = aliquota
            break

    return out


def _extract_aliquotas_energisa(text: str) -> dict[str, float]:
    out = {
        "fatDescPisAliquota": 0.0,
        "fatDesCofinsAliquota": 0.0,
        "fatDesIcmsAliquota": 0.0,
    }
    text_norm = _texto_normalizado(text)
    labels = {
        "PIS/PASEP": "fatDescPisAliquota",
        "PIS": "fatDescPisAliquota",
        "COFINS": "fatDesCofinsAliquota",
        "ICMS": "fatDesIcmsAliquota",
    }

    for label, campo in labels.items():
        match = re.search(rf"{re.escape(label)}\s+([-\d\.,]+(?:\s+[-\d\.,]+){{1,3}})", text_norm)
        if not match or out[campo]:
            continue
        aliquota = _extrair_aliquota_de_segmento(match.group(1))
        if aliquota:
            out[campo] = aliquota

    m_pis = re.search(r"\bPIS(?:/PASEP)?\s+([-\d\.,]+)\s+([-\d\.,]+)\b", text_norm)
    if m_pis and not out["fatDescPisAliquota"]:
        out["fatDescPisAliquota"] = abs(_to_float_br(m_pis.group(1)))

    m_cof = re.search(r"\bCOFINS\s+([-\d\.,]+)\s+([-\d\.,]+)\b", text_norm)
    if m_cof and not out["fatDesCofinsAliquota"]:
        out["fatDesCofinsAliquota"] = abs(_to_float_br(m_cof.group(1)))

    m_icms = re.search(r"\bICMS\s+([-\d\.,]+)\s+([-\d\.,]+)\b", text_norm)
    if m_icms and not out["fatDesIcmsAliquota"]:
        out["fatDesIcmsAliquota"] = abs(_to_float_br(m_icms.group(1)))

    for line in text.splitlines():
        line_norm = _texto_normalizado(line)
        if "( - )" in line_norm:
            continue

        for label, campo in labels.items():
            if label not in line_norm or out[campo]:
                continue
            parte = line_norm.split(label, 1)[1].strip()
            aliquota = _extrair_aliquota_de_segmento(parte)
            if aliquota:
                out[campo] = aliquota

        m_pis_2 = re.match(r"^PIS(?:/PASEP)?\s+([-\d\.,]+)\s+([-\d\.,]+)\s*$", line_norm)
        m_pis_3 = re.match(r"^PIS(?:/PASEP)?\s+([-\d\.,]+)\s+([-\d\.,]+)\s+([-\d\.,]+)\s*$", line_norm)
        m_cof_2 = re.match(r"^COFINS\s+([-\d\.,]+)\s+([-\d\.,]+)\s*$", line_norm)
        m_cof_3 = re.match(r"^COFINS\s+([-\d\.,]+)\s+([-\d\.,]+)\s+([-\d\.,]+)\s*$", line_norm)
        m_icms_2 = re.match(r"^ICMS\s+([-\d\.,]+)\s+([-\d\.,]+)\s*$", line_norm)
        m_icms_3 = re.match(r"^ICMS\s+([-\d\.,]+)\s+([-\d\.,]+)\s+([-\d\.,]+)\s*$", line_norm)

        if m_pis_2 and not out["fatDescPisAliquota"]:
            out["fatDescPisAliquota"] = abs(_to_float_br(m_pis_2.group(1)))
        elif m_pis_3 and not out["fatDescPisAliquota"]:
            out["fatDescPisAliquota"] = abs(_to_float_br(m_pis_3.group(2)))
        elif m_cof_2 and not out["fatDesCofinsAliquota"]:
            out["fatDesCofinsAliquota"] = abs(_to_float_br(m_cof_2.group(1)))
        elif m_cof_3 and not out["fatDesCofinsAliquota"]:
            out["fatDesCofinsAliquota"] = abs(_to_float_br(m_cof_3.group(2)))
        elif m_icms_2 and not out["fatDesIcmsAliquota"]:
            out["fatDesIcmsAliquota"] = abs(_to_float_br(m_icms_2.group(1)))
        elif m_icms_3 and not out["fatDesIcmsAliquota"]:
            out["fatDesIcmsAliquota"] = abs(_to_float_br(m_icms_3.group(2)))

    # DANF3E: ICMS aliquota como inteiro isolado na linha de consumo
    # Ex.: "...5.782,76 259,91 5.782,76 17 983,07 0,982849..."
    if not out["fatDesIcmsAliquota"]:
        for line in text.splitlines():
            if "CONSUMO EM KWH" not in _texto_normalizado(line):
                continue
            m = re.search(r"[\d.,]+\s+[\d.,]+\s+[\d.,]+\s+(\d{2})\s+[\d.,]+", line)
            if m:
                aliq = float(m.group(1))
                if 10 <= aliq <= 30:
                    out["fatDesIcmsAliquota"] = aliq
                    break

    return out


def _extract_aliquotas_energisa_words(words: list[dict]) -> dict[str, float]:
    out = {
        "fatDescPisAliquota": 0.0,
        "fatDesCofinsAliquota": 0.0,
        "fatDesIcmsAliquota": 0.0,
        "fatPIS": 0.0,
        "fatCOFINS": 0.0,
    }
    if not words:
        return out

    grouped: dict[int, list[dict]] = {}
    for word in words:
        y_key = int(round(float(word.get("top", 0.0)) / 4.0) * 4)
        grouped.setdefault(y_key, []).append(word)

    for y_key in sorted(grouped):
        tokens = [str(w.get("text") or "").strip() for w in sorted(grouped[y_key], key=lambda w: float(w.get("x0", 0.0)))]
        labels = {
            "PIS": "fatDescPisAliquota",
            "PIS/PASEP": "fatDescPisAliquota",
            "COFINS": "fatDesCofinsAliquota",
            "ICMS": "fatDesIcmsAliquota",
        }
        for label, field in labels.items():
            if label not in tokens:
                continue
            idx = tokens.index(label)
            nums = [_to_float_br(tok) for tok in tokens[idx + 1 :] if re.fullmatch(r"[-\d\.,]+", tok)]
            nums = [abs(v) for v in nums if v]
            if len(nums) >= 2 and not out[field]:
                out[field] = nums[1]
            if label.startswith("PIS") and len(nums) >= 3 and not out["fatPIS"]:
                out["fatPIS"] = nums[2]
            if label == "COFINS" and len(nums) >= 3 and not out["fatCOFINS"]:
                out["fatCOFINS"] = nums[2]
            if label == "ICMS" and len(nums) >= 2 and not out["fatDesIcmsAliquota"]:
                out["fatDesIcmsAliquota"] = nums[1]
    return out


def _extract_retencoes_energisa(text: str) -> dict[str, float]:
    out = {
        "fatDescPisPercRetImposto": 0.0,
        "fatDescPisValRetImposto": 0.0,
        "fatDescCofinsPercRetImposto": 0.0,
        "fatDescCofinsValRetImposto": 0.0,
        "fatDescCsllPercRetImposto": 0.0,
        "fatDescCsllValRetImposto": 0.0,
        "fatDescIrpjPercRetImposto": 0.0,
        "fatDescIrpjValRetImposto": 0.0,
    }
    mapa = {
        "PIS/PASEP": ("fatDescPisPercRetImposto", "fatDescPisValRetImposto", RETENCOES_PERC["PIS"]),
        "COFINS": ("fatDescCofinsPercRetImposto", "fatDescCofinsValRetImposto", RETENCOES_PERC["COFINS"]),
        "CONT. SOCIAL": ("fatDescCsllPercRetImposto", "fatDescCsllValRetImposto", RETENCOES_PERC["CSLL"]),
        "IMPOSTO RENDA": ("fatDescIrpjPercRetImposto", "fatDescIrpjValRetImposto", RETENCOES_PERC["IRPJ"]),
    }
    text_norm = _texto_normalizado(text)
    for rotulo, (campo_perc, campo_val, perc) in mapa.items():
        match = re.search(rf"{re.escape(rotulo)}\s*\(\s*-\s*\).*?(-\d[\d\.,]*)", text_norm)
        if match:
            out[campo_perc] = perc
            out[campo_val] = -abs(_to_float_br(match.group(1)))

    for line in text.splitlines():
        line_norm = _texto_normalizado(line)
        for rotulo, (campo_perc, campo_val, perc) in mapa.items():
            if rotulo not in line_norm or "( - )" not in line_norm or out[campo_val]:
                continue
            m_valor = re.search(r"(-\d[\d\.,]*)", line)
            if not m_valor:
                continue
            out[campo_perc] = perc
            out[campo_val] = -abs(_to_float_br(m_valor.group(1)))
    return out


def _extract_consumo_bt(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    branca_posta: dict[str, tuple[float, float]] = {}

    for line in text.splitlines():
        line_norm = _texto_normalizado(line)

        # Tarifa Branca Energisa: "CONSUMOEMKWH-INTERMEDIARIA", "CONSUMOEMKWH-PONTA", "CONSUMOEMKWH-FPONTA"
        if "CONSUMOEMKWH-" in line_norm:
            match = re.search(r"KWH\s+([-\d\.,]+)\s+[-\d\.,]+\s+([-\d\.,]+)", line, flags=re.IGNORECASE)
            if match:
                qtd = abs(_to_float_br(match.group(1)))
                val = abs(_to_float_br(match.group(2)))
                if "INTERMEDIAR" in line_norm:
                    branca_posta["intermediario"] = (qtd, val)
                elif "FPONTA" in line_norm or "F.PONTA" in line_norm or "FORA" in line_norm:
                    branca_posta["fora_ponta"] = (qtd, val)
                else:
                    branca_posta["ponta"] = (qtd, val)
            continue

        # Convencional: "CONSUMO EM KWH"
        if "CONSUMO EM KWH" not in line_norm:
            continue
        match = re.search(r"KWH\s+([-\d\.,]+)\s+[-\d\.,]+\s+([-\d\.,]+)", line, flags=re.IGNORECASE)
        if not match:
            continue
        qtd = abs(_to_float_br(match.group(1)))
        val = abs(_to_float_br(match.group(2)))
        out["fatConFPontaIndRegistrado"] = qtd
        out["fatConFPontaIndFaturado"] = qtd
        out["fatConFPontaIndValorReais"] = val
        return out

    if branca_posta:
        if "intermediario" in branca_posta:
            qtd, val = branca_posta["intermediario"]
            out["fatConIntermediarioRegistrado"] = qtd
            out["fatConIntermediarioFaturado"] = qtd
            out["fatConIntermediarioValorReais"] = val
        if "ponta" in branca_posta:
            qtd, val = branca_posta["ponta"]
            out["fatConPontaRegistrado"] = qtd
            out["fatConPontaFaturado"] = qtd
            out["fatConPontaValorReais"] = val
        if "fora_ponta" in branca_posta:
            qtd, val = branca_posta["fora_ponta"]
            out["fatConFPontaIndRegistrado"] = qtd
            out["fatConFPontaIndFaturado"] = qtd
            out["fatConFPontaIndValorReais"] = val

    return out


def _extract_gdi_energisa(text: str) -> dict[str, float]:
    """Extrai injeção GDI DANF3E Energisa (kWh e R$ separados).

    Layout KWH: 'Energia Atv Injetada GDI oUC MM/YYYY mPT KWH QTD PRECO VALOR ...'
    Layout sem KWH (DANF3E MS/SE): 'Energia Atv Injetada - Fora Ponta val tarifa total ...'
    kWh: 1º número após KWH. R$: 3º número após KWH (qty, price, value) ou 1º float da linha sem KWH.
    """
    kwh_total = val_total = 0.0
    _INJ_TOKENS = ("INJETADA", "INJET.", "ENERGIA INJ", "SCEE INJET", "GDI")
    _SKIP_TOKENS = ("SALDO", "ACUMULADO", "CREDITO", "CONSUMO")
    for line in text.splitlines():
        line_norm = _texto_normalizado(line)
        if not any(t in line_norm for t in _INJ_TOKENS):
            continue
        if any(t in line_norm for t in _SKIP_TOKENS):
            continue
        if "KWH" in line_norm:
            m_kwh = re.search(r"KWH\s+([\d.,]+)", line, re.IGNORECASE)
            if m_kwh:
                kwh_total += abs(_to_float_br(m_kwh.group(1)) or 0.0)
            m_val = re.search(r"KWH\s+[\d.,]+\s+[\d.,]+\s+(-?[\d.,]+)", line, re.IGNORECASE)
            if m_val:
                val_total += abs(_to_float_br(m_val.group(1)) or 0.0)
        else:
            # DANF3E sem KWH: "Energia Atv Injetada - Fora Ponta <val> <tarifa> ..."
            # 1º número na linha = R$ valor; 2º = tarifa unitária.
            floats = re.findall(r"-?[\d]+(?:[.,]\d+)+", line)
            if floats:
                val_total += abs(_to_float_br(floats[0]) or 0.0)

    out: dict[str, float] = {}
    if kwh_total > 0:
        out["fatConFPontaInjetadoUsina"]      = kwh_total
        out["fatConFPontaInjetadoRegistrado"] = kwh_total
        out["fatConFPontaInjetadoFaturado"]   = kwh_total
    if val_total > 0:
        out["fatConFPontaInjetadoValorReais"] = val_total
    return out


def _extract_saldo_gdi_energisa(text: str) -> float:
    """Extrai saldo acumulado de GD.

    Suporta dois layouts:
    - Rondônia/outros: "250,00 kWh" (valor antes da unidade)
    - MS DANF3E      : "kWh 250" ou "kWh 250,00" (unidade antes do valor)

    Tokens aceitos para identificar linha de saldo GD: inclui "ACUMULADO" e
    "COMP" (abreviação de "Compensar" usado pelo DANF3E da Energisa MS) além
    dos tokens já existentes.
    """
    _GD_TOKENS = ("GDI", "INJET", "CRED", "COMPENS", "COMP", "SCEE", "ACUMULADO")
    _TAX_TOKENS = ("ICMS", "PIS", "COFINS", "BASE DE CALCULO", "BASE CALC")
    candidatos: list[float] = []
    for line in text.splitlines():
        line_norm = _texto_normalizado(line)
        if "SALDO" not in line_norm:
            continue
        if not any(token in line_norm for token in _GD_TOKENS):
            continue
        if any(t in line_norm for t in _TAX_TOKENS):
            continue
        if "KWH" in line_norm:
            # Valor antes do KWH: "250,00 kWh" (layouts Rondônia)
            m_antes = re.search(r"([\d.]+,\d+)\s*KWH", line, re.IGNORECASE)
            # Valor após KWH: "kWh 250" ou "kWh 250,00" (DANF3E MS)
            m_depois = re.search(r"KWH\s+([\d.,]+)", line, re.IGNORECASE)
            for m in filter(None, (m_antes, m_depois)):
                valor = abs(_to_float_br(m.group(1)) or 0.0)
                if valor > 0:
                    candidatos.append(valor)
                    break
        if not candidatos:
            # Sem KWH explícito — qualquer número decimal na linha
            for numero in re.findall(r"\d[\d.]*,\d+", line):
                valor = abs(_to_float_br(numero) or 0.0)
                if valor > 0:
                    candidatos.append(valor)
    return round(max(candidatos), 2) if candidatos else 0.0


def _extract_dic_fic_energisa(text: str) -> dict[str, float]:
    """Extrai compensações de qualidade (DIC/FIC) de forma conservadora."""
    out = {"fatDIC": 0.0, "fatFIC": 0.0}
    for line in text.splitlines():
        line_norm = _texto_normalizado(line)
        if "DIC" in line_norm and any(token in line_norm for token in ("COMPENS", "CRED", "DEVOL", "RESSARC")):
            for numero in re.findall(r"\d[\d.]*,\d+", line):
                valor = abs(_to_float_br(numero) or 0.0)
                if valor >= 0.01:
                    out["fatDIC"] += valor
                    break
        if "FIC" in line_norm and any(token in line_norm for token in ("COMPENS", "CRED", "DEVOL", "RESSARC")):
            for numero in re.findall(r"\d[\d.]*,\d+", line):
                valor = abs(_to_float_br(numero) or 0.0)
                if valor >= 0.01:
                    out["fatFIC"] += valor
                    break
    out["fatDIC"] = round(out["fatDIC"], 2)
    out["fatFIC"] = round(out["fatFIC"], 2)
    return out


def _extract_bandeira_energisa(text: str) -> float:
    """Extrai adicional de bandeira.

    Suporta tanto 'ADICIONAL BANDEIRA' (Energisa Rondonia) quanto
    'Adic. B. Amarela/Verde' (Energisa MS DANF3E).
    Exclui linhas de cidade (ex.: 'BANDEIRANTES') e cabeçalhos.
    O valor correto é o primeiro número decimal da linha (coluna Valor R$),
    não o último (que é o valor do ICMS na coluna final).
    """
    for line in text.splitlines():
        line_norm = _texto_normalizado(line)
        is_adic = bool(re.search(r"\bADIC\b", line_norm) and re.search(r"\bB\b", line_norm))
        is_explicit = any(t in line_norm for t in (
            "BANDEIRA TARIFARIA", "ADICIONAL BANDEIRA", "ADICIONAL DE BANDEIRA",
            "ADIC. BAND", "ADIC BAND", "ESCASSEZ HIDRICA", "ADIC. ESC",
        ))
        if not is_adic and not is_explicit:
            continue
        # Exclui cidade/cabeçalho: "BANDEIRANTES", "DATA DE EMISSAO", etc.
        if re.search(r"\bAG\b|\bDATA\s+DE\b|\bNOTA\s+FISCAL\b|\bEMISS", line_norm):
            continue
        # Se há KWH na linha (DANF3E com detalhamento), o valor está como 3º número após KWH
        if "KWH" in line_norm:
            m_val = re.search(r"KWH\s+[\d.,]+\s+[\d.,]+\s+(-?[\d.,]+)", line, re.IGNORECASE)
            if m_val:
                v = abs(_to_float_br(m_val.group(1)) or 0.0)
                if v >= 0.01:
                    return v
        # Formato simples: primeiro número decimal da linha (coluna Valor R$)
        for n in re.findall(r"\d[\d.]*,\d+", line):
            v = abs(_to_float_br(n) or 0.0)
            if 0.01 <= v < 10_000:
                return v
    return 0.0


def _extract_valor_nota_fiscal_energisa(text: str) -> float:
    for line in text.splitlines():
        line_norm = _texto_normalizado(line)
        if "CONSUMO EM KWH" not in line_norm:
            continue
        match = re.search(r"KWH\s+([-\d\.,]+)\s+[-\d\.,]+\s+([-\d\.,]+)", line, flags=re.IGNORECASE)
        if match:
            return abs(_to_float_br(match.group(2)))
    return abs(_extract_valor_nota_fiscal(text))


def _extract_multas_diversas_energisa(text: str) -> float:
    """Extrai multas, juros e atualização monetária.

    No formato DANF3E as colunas após o valor são PIS/COFINS base e ICMS (zeros
    ou valores pequenos), então pegar o último número captura lixo.
    Solução: primeiro número decimal (com vírgula) ≥ 0.5 na linha.
    """
    total = 0.0
    for line in text.splitlines():
        line_norm = _texto_normalizado(line)
        if not any(token in line_norm for token in (
            "JUROS DE MORA", "MULTA ", "ATUALIZACAO MONETARIA", "AJUSTE GDIII", "AJUSTE GD III",
        )):
            continue
        # Pula linhas informativas e ajustes de GD (TRF Reduzida Lei 14.300/22 — crédito GD, não multa)
        if any(skip in line_norm for skip in ("SERA COBRAD", "SERAO COBRAD", "PRIMEIRA FATURA", "APENAS", "TRF REDUZIDA", "LEI 14.300")):
            continue
        # Primeiro decimal ≥ 0,5 = valor monetário real
        for n in re.findall(r"\d[\d.]*,\d+", line):
            v = abs(_to_float_br(n) or 0.0)
            if v >= 0.5:
                total += v
                break
    return round(total, 2)


def _extract_icms_danf3e(text: str) -> float:
    """Extrai ICMS total da linha TOTAL do DANF3E Energisa.

    Formato: 'TOTAL: <valor_fatura> <base_pis_cofins> <base_icms> <icms_valor>'
    O último número é o ICMS.
    """
    for line in text.splitlines():
        line_norm = _texto_normalizado(line)
        if not line_norm.startswith("TOTAL:") and not line_norm.startswith("TOTAL "):
            continue
        nums = re.findall(r"\d[\d.]*,\d+", line)
        if len(nums) >= 4:
            return abs(_to_float_br(nums[-1]) or 0.0)
    return 0.0


def _detectar_tarifa(text: str) -> tuple[str, str]:
    txt = _texto_normalizado(text)
    if re.search(r"CLASSIFICACAO:.*BRANCA", txt) or "TARIFA BRANCA" in txt:
        return "Branca", "B3_BRANCA"
    return "Convencional", "B3"


def identificacao_rapida(pdf_path: Path) -> dict:
    resultado = {"sistema": "DESCONHECIDA", "instalacao": "", "mes_ref": "", "grupo": ""}
    try:
        text = _first_page_text(pdf_path)
        if not text or not _is_energisa(text):
            return resultado
        resultado["sistema"] = "ENERGISA"
        resultado["instalacao"] = _normalizar_codigo_energisa(_extract_instalacao_energisa(text, pdf_path))
        ref = _extract_referencia(text, dt.date.today().month, dt.date.today().year)
        resultado["mes_ref"] = ref.strftime("%m-%Y")
        txt = _texto_normalizado(text)
        if "BAIXA TENSAO" in txt or "/ B3" in txt or " B3" in txt:
            resultado["grupo"] = "B"
        elif "GRUPO A" in txt or "A4" in txt or "MEDIA TENSAO" in txt:
            resultado["grupo"] = "A"
    except Exception as exc:
        log.warning("  identificacao_rapida %s: %s", pdf_path.name, exc)
    return resultado


def processar_pdf_direto(pdf_path: Path, mes_padrao: int, ano_padrao: int) -> dict:
    rec = _empty_record()
    rec["ARQUIVO"] = pdf_path.name
    rec["fatCarimbo"] = _carimbo_do_nome(pdf_path)
    rec["fatDataCadastro"] = dt.date.today()
    rec["concCod"] = "ENERGISA"

    try:
        text, first_page_words = _extract_pdf_data(pdf_path)
    except Exception as exc:
        rec["ERRO"] = f"{type(exc).__name__}: {exc}"
        return rec

    if not text.strip():
        rec["ERRO"] = "PDF sem texto extraivel"
        return rec
    if not _is_energisa(text):
        rec["ERRO"] = "Nao identificado como Energisa"
        return rec

    uc_antiga_nome, uc_nova_nome = _ucs_do_nome(pdf_path)
    original_pdf = None
    if not (uc_antiga_nome or uc_nova_nome) and str(pdf_path.stem).upper().startswith("BB_"):
        carimbo_norm = f"BB_{rec['fatCarimbo']}" if str(rec["fatCarimbo"]).isdigit() else str(rec["fatCarimbo"])
        original_pdf = _arquivo_original_por_carimbo(carimbo_norm)
        uc_antiga_nome, uc_nova_nome = _ucs_do_nome(original_pdf)

    tarifa, detectada = _detectar_tarifa(text)
    rec["cadTarifaCod"] = tarifa
    rec["cadSubGrupoCod"] = "B3 [<2,3kV]"
    rec["TARIFA_DETECTADA"] = detectada

    instalacao = _extract_instalacao_energisa(text, original_pdf or pdf_path)
    if not instalacao:
        instalacao = _normalizar_codigo_energisa(_extract_instalacao(text, first_page_words))
    rec["Instalacao"] = instalacao
    codigo_cliente = _extract_codigo_cliente_energisa(text, instalacao)
    rec["CODIGOCLIENTE"] = codigo_cliente
    rec["ENDERECO"] = _extract_endereco_energisa(text)
    rec["NOTAFISCAL"] = _extract_notafiscal_energisa(text)
    rec["CNPJ"] = ""

    rec["fatDataEmissao"] = _extract_data_emissao(text)
    leitura_ant, leitura_atu, vencimento = _extract_datas(text)
    rec["fatDataLeituraAnterior"] = leitura_ant
    rec["fatDataLeituraAtual"] = leitura_atu
    rec["fatDataVcto"] = vencimento
    vencimento_explicit = _extract_vencimento_energisa(text)
    if vencimento_explicit:
        rec["fatDataVcto"] = vencimento_explicit
    rec["fatDataReferencia"] = _extract_referencia(text, mes_padrao, ano_padrao)

    rec["fatValorFatura"] = _extract_total_energisa(text)
    rec["fatIlumPublica"] = _extract_ilum_publica(text)
    impostos = _extract_impostos_energisa(text)
    rec["fatICMS"] = impostos["fatICMS"] or abs(_extract_imposto(text, "ICMS")) or _extract_icms_danf3e(text)
    rec["fatPIS"] = impostos["fatPIS"] or abs(_extract_imposto(text, "PIS"))
    rec["fatCOFINS"] = impostos["fatCOFINS"] or abs(_extract_imposto(text, "COFINS"))
    rec.update(_extract_aliquotas_energisa(text))
    aliq_words = _extract_aliquotas_energisa_words(first_page_words)
    for key, value in aliq_words.items():
        if value and not rec.get(key):
            rec[key] = value
    if _is_energisa_sergipe(text) and (impostos["fatICMSFCP"] > 0 or impostos["fatDesIcmsFcpAliquota"] > 0):
        rec["fatICMS"] = round(float(rec.get("fatICMS") or 0.0) + float(impostos["fatICMSFCP"] or 0.0), 2)
        rec["fatDesIcmsAliquota"] = round(
            float(rec.get("fatDesIcmsAliquota") or 0.0) + float(impostos["fatDesIcmsFcpAliquota"] or 0.0),
            2,
        )
    rec["fatTributoFederalPerc"], rec["fatTributoFederalVal"] = _extract_tributo_federal(text)
    rec["Debitos anteriores"] = _extract_debitos_anteriores(text)

    retencoes = _extract_retencoes_energisa(text)
    rec.setdefault("fatDescPisPercRetImposto", 0.0)
    rec.setdefault("fatDescPisValRetImposto", 0.0)
    rec.setdefault("fatDescCofinsPercRetImposto", 0.0)
    rec.setdefault("fatDescCofinsValRetImposto", 0.0)
    rec.setdefault("fatDescCsllPercRetImposto", 0.0)
    rec.setdefault("fatDescCsllValRetImposto", 0.0)
    rec.setdefault("fatDescIrpjPercRetImposto", 0.0)
    rec.setdefault("fatDescIrpjValRetImposto", 0.0)
    rec.update(retencoes)

    rec.update(_extract_consumo_bt(text))

    gdi = _extract_gdi_energisa(text)
    rec.update(gdi)
    saldo_gdi = _extract_saldo_gdi_energisa(text)
    if saldo_gdi > 0:
        rec["fatConFPontaInjetadoUsinaSaldoAcumulado"] = saldo_gdi
    rec.update(_extract_dic_fic_energisa(text))

    bandeira_val = _extract_bandeira_energisa(text)
    rec["fatValBandeira"] = bandeira_val

    consumo_val = rec.get("fatConFPontaIndValorReais", 0.0) or 0.0
    inj_val = gdi.get("fatConFPontaInjetadoValorReais", 0.0) or 0.0
    base_calc = round(consumo_val + bandeira_val - inj_val, 2)
    valor_nf_pdf = _extract_valor_nota_fiscal_energisa(text)
    rec["fatValorNotaFiscal"] = round(valor_nf_pdf or base_calc, 2)

    rec["fatMultasDiversas"] = _extract_multas_diversas_energisa(text)

    codigo_barras = _extract_codigo_barras(text)
    if len(_digits(codigo_barras)) >= 44:
        rec["fatCodigoBarras"] = _digits(codigo_barras)
    else:
        rec["fatCodigoBarras"] = ""

    rec["ERRO"] = ""
    return rec


def _listar_pdfs(pasta: Path, carimbos: set[str]) -> list[Path]:
    pdfs = sorted(p for p in pasta.glob("*.pdf") if p.is_file())
    if carimbos:
        norm = {str(c).strip().upper() for c in carimbos}
        # aceita tanto "2011459" quanto "BB_2011459" no filtro
        norm_sem_bb = {c[3:] if c.startswith("BB_") else c for c in norm}
        def _bate(p: Path) -> bool:
            s = p.stem.upper()
            s_sem = s[3:] if s.startswith("BB_") else s
            return s in norm or s_sem in norm_sem_bb
        pdfs = [p for p in pdfs if _bate(p)]
    return pdfs


def _xlsx_saida(mes: int, ano: int) -> Path:
    return OUTPUT_DIR / f"ocr_energisa_BT_{mes:02d}{ano}.xlsx"


def parse_args() -> argparse.Namespace:
    hoje = dt.date.today()
    parser = argparse.ArgumentParser(description="OCR Energisa Rondonia BT -> XLSX")
    parser.add_argument("--mes", type=int, default=hoje.month, help="Mes de referencia padrao")
    parser.add_argument("--ano", type=int, default=hoje.year, help="Ano de referencia padrao")
    parser.add_argument("--pasta", type=str, default=str(DEFAULT_PASTA), help="Pasta com PDFs")
    parser.add_argument("--saida", type=str, default="", help="XLSX de saida")
    parser.add_argument("--carimbo", action="append", default=[], help="Carimbo(s) BB_XXXXXXX")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pasta = Path(str(args.pasta).strip())
    carimbos = {str(c).strip().upper() for c in args.carimbo or [] if str(c).strip()}

    if not pasta.exists():
        log.error("Pasta nao encontrada: %s", pasta)
        return 1

    pdfs = _listar_pdfs(pasta, carimbos)
    if not pdfs:
        log.warning("Nenhum PDF encontrado para o filtro informado.")
        return 0

    log.info("=" * 64)
    log.info("OCR ENERGISA BT")
    log.info("=" * 64)
    log.info("Pasta : %s", pasta)
    log.info("PDFs candidatos: %d", len(pdfs))

    registros: list[dict] = []
    ignorados = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futuros = [executor.submit(processar_pdf_direto, pdf, int(args.mes), int(args.ano)) for pdf in pdfs]
        for futuro in as_completed(futuros):
            rec = futuro.result()
            if rec.get("ERRO") == "Nao identificado como Energisa":
                ignorados += 1
                continue
            registros.append(rec)

    registros.sort(key=lambda row: str(row.get("fatCarimbo", "")))
    if not registros:
        log.warning("Nenhuma fatura Energisa BT extraida.")
        return 0

    destino = Path(str(args.saida).strip()) if str(args.saida).strip() else _xlsx_saida(int(args.mes), int(args.ano))
    try:
        salvar_excel(registros, destino, titulo="OCR_ENERGISA_BT")
    except Exception as exc:
        log.error("Falha ao salvar XLSX: %s", exc)
        return 1

    ok = sum(1 for rec in registros if not rec.get("ERRO"))
    erro = len(registros) - ok
    log.info("XLSX salvo: %s", destino)
    log.info("Resumo: total=%d ok=%d erro=%d ignorados=%d", len(registros), ok, erro, ignorados)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
