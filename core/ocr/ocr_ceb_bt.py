#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ocr_ceb_bt.py
-------------
OCR de faturas BT Neoenergia Brasília (CEB) -> XLSX no schema CEMIG/Consen.

Nome dos PDFs na pasta original: "NNNNNN DD.MM.YY ceb.pdf"
                 no staging (BB): "BB_XXXXXXX.pdf"

Uso:
    python ocr_ceb_bt.py --pasta "\\\\srv\\...\\CEB\\BT" --mes 07 --ano 2026

Env vars:
    NEOENERGIA_CEB_PASTA_PDF   — pasta-raiz com os PDFs
    NEOENERGIA_CEB_PASTA_SAIDA — pasta para o XLSX de saída
    NEOENERGIA_CEB_CONC_COD    — código da concessionária no Consen
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import re
import sys
import unicodedata
from pathlib import Path

import pdfplumber

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.ocr.OCR_Cemig import HEADERS_REF, salvar_excel
from indice_master import MasterIndice

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

PASTA_PDF_DEFAULT = Path(os.environ.get(
    "NEOENERGIA_CEB_PASTA_PDF",
    "//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO/Faturas/NEOENERGIA/CEB/BT",
))
PASTA_SAIDA_DEFAULT = Path(os.environ.get(
    "NEOENERGIA_CEB_PASTA_SAIDA",
    "//10.10.250.21/Energia/ARQUIVOS ENZO/OCR CEB",
))
CONC_COD: str = os.environ.get("NEOENERGIA_CEB_CONC_COD", "NEOENERGIA BRASILIA")
SISTEMA_MASTER = "NEOENERGIA BRASILIA"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# =============================================================================
# HELPERS
# =============================================================================

def _br2f(s: str) -> float:
    try:
        return float(str(s).strip().replace(".", "").replace(",", "."))
    except (ValueError, AttributeError):
        return 0.0

_RE_MONEY = re.compile(r"(?<!\d)-?[\d.]+,\d{2}(?!\d)")


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ASCII", "ignore").decode("ASCII").upper()


def _extrair_texto(pdf_path: str | Path) -> str:
    partes: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages[:2]:
            partes.append(page.extract_text() or "")
    return "\n".join(partes)


def _instalacao_do_nome(filename: str) -> str:
    """Extrai instalacao do nome do arquivo.
    'NNNNNN DD.MM.YY ceb.pdf' -> 'NNNNNN'
    'BB_2017789.pdf'          -> '' (sera buscado no indice_master)
    """
    stem = Path(filename).stem
    if stem.upper().startswith("BB_"):
        return ""
    m = re.match(r"^(\d+)", stem)
    return m.group(1) if m else ""


# =============================================================================
# EXTRAÇÃO
# =============================================================================

def _extrair_ref_total_vcto(text: str) -> tuple[dt.date | None, float, str]:
    """Linha: 'MM/YYYY VALOR VENCIMENTO'"""
    m = re.search(
        r"\b(0[1-9]|1[0-2])/(20\d{2})\s+([\d.]+,\d{2})\s+(\d{2}/\d{2}/20\d{2})",
        text,
    )
    if m:
        try:
            ref = dt.date(int(m.group(2)), int(m.group(1)), 1)
        except ValueError:
            ref = None
        return ref, abs(_br2f(m.group(3))), m.group(4)
    return None, 0.0, ""


def _extrair_emissao(text: str) -> str:
    upper = _norm(text)
    m = re.search(r"DATA\s+DE\s+EMISSAO\s*:\s*(\d{2}/\d{2}/\d{4})", upper)
    if m:
        return m.group(1)
    m2 = re.search(r"EMISSAO\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})", upper)
    if m2:
        return m2.group(1)
    # Fallback: buscar data próxima ao "Protocolo de autorização"
    m3 = re.search(r"Protocolo.*?(\d{2}/\d{2}/20\d{2})", text, re.IGNORECASE)
    if m3:
        return m3.group(1)
    return ""


def _extrair_leituras(text: str) -> tuple[str, str]:
    m = re.search(
        r"LEITURA\s+ANTERIOR\s+(\d{2}/\d{2}/20\d{2}).*?LEITURA\s+ATUAL\s+(\d{2}/\d{2}/20\d{2})",
        text, re.IGNORECASE,
    )
    if m:
        return m.group(1), m.group(2)
    return "", ""


def _extrair_codigo_barras(text: str) -> str:
    for ln in text.splitlines():
        stripped = ln.strip()
        digits = re.sub(r"\D", "", stripped)
        if len(digits) in (44, 47, 48) and len(re.sub(r"[\d.\- ]", "", stripped)) <= 4:
            return digits
    return ""


def _extrair_nota_fiscal(text: str) -> str:
    upper = _norm(text)
    m = re.search(r"NOTA\s+FISCAL\s+N[O°Oº]*\s*:?\s*(\d{6,})", upper)
    if m:
        return m.group(1)
    # Chave de acesso NF-e: 44 digitos
    m2 = re.search(r"\b(\d{44})\b", re.sub(r"\s", "", text))
    if m2:
        return m2.group(1)[:9]  # primeiros dígitos como referência
    return ""


def _extrair_consumo(text: str) -> tuple[float, float, float]:
    """Retorna (kWh, tusd_val, te_val).

    Layout 1 — NF-e padrão CEB:
        'Consumo-TUSD kWh <qtd,2dec> <tarifa> <val,2dec>'
        'Consumo-TE   kWh <qtd,2dec> <tarifa> <val,2dec>'
        → te_val = val da linha TE, tusd_val = val da linha TUSD

    Layout 2 — CEB BT com fora-ponta único (ex: "CONSUMO FORA DE PONTA UMIDO"):
        'CONSUMO FORA DE PONTA <qualificador> KWh <qtd> <tarifa> <val,2dec> ...'
        A quantidade pode ser inteira (9521) ou decimal (9.521,00).
        A linha pode conter impostos PIS/COFINS depois do valor — não filtrar a linha inteira.
        → te_val = val (consumo fora ponta indutivo); tusd_val permanece 0.

    Layout 3 — CEB BT tarifa única (ex: "CONSUMO ENERGIA ATIVA"):
        'CONSUMO ENERGIA ATIVA <kWh> KWH X <tarifa> <val,2dec>'
        → te_val = val; tusd_val = 0.
    """
    kWh = tusd_val = te_val = 0.0

    # Layout 1: NF-e com TUSD e TE separados
    m_tusd = re.search(
        r"Consumo[- ]TUSD\s+kWh\s+([\d.]+,\d{2})\s+[\d.,]+\s+([\d.]+,\d{2})",
        text, re.IGNORECASE,
    )
    if m_tusd:
        kWh = abs(_br2f(m_tusd.group(1)))
        tusd_val = abs(_br2f(m_tusd.group(2)))

    m_te = re.search(
        r"Consumo[- ]TE\s+kWh\s+([\d.]+,\d{2})\s+[\d.,]+\s+([\d.]+,\d{2})",
        text, re.IGNORECASE,
    )
    if m_te:
        te_val = abs(_br2f(m_te.group(2)))
        if not kWh:
            kWh = abs(_br2f(m_te.group(1)))

    if kWh > 0 or tusd_val > 0 or te_val > 0:
        return round(kWh, 2), round(tusd_val, 2), round(te_val, 2)

    # Layout 2: "CONSUMO FORA DE PONTA <UMIDO|SECO|...> KWh <qtd> <tarifa> <valor>"
    # A quantidade pode ser inteiro (9521) ou formato BR (9.521,00 ou 9.521).
    # A tarifa tem muitas casas decimais. O valor tem exatamente 2 (6.342,97).
    # Usa re.search diretamente na linha para não ser filtrado por palavras como PIS
    # que aparecem DEPOIS do valor na mesma linha de faturamento.
    m_umido = re.search(
        r"CONSUMO\s+FORA\s+DE\s+PONTA\b[^\n]*?KWh\s+([\d\.]+(?:,\d+)?)\s+[\d\.,]+\s+([\d\.]+,\d{2})",
        text, re.IGNORECASE,
    )
    if m_umido:
        kWh = abs(_br2f(m_umido.group(1)))
        te_val = abs(_br2f(m_umido.group(2)))
        return round(kWh, 2), round(tusd_val, 2), round(te_val, 2)

    # Layout 3: "CONSUMO ENERGIA ATIVA <kWh> KWH X <tarifa> <valor>"
    m_ativa = re.search(
        r"CONSUMO\s+ENERGIA\s+ATIVA\s+([\d.]+(?:,\d+)?)\s+KWH\s+X\s+[\d.,]+\s+([\d.,]+)",
        text, re.IGNORECASE,
    )
    if m_ativa:
        kWh = abs(_br2f(m_ativa.group(1)))
        te_val = abs(_br2f(m_ativa.group(2)))

    return round(kWh, 2), round(tusd_val, 2), round(te_val, 2)


def _extrair_icms(text: str) -> tuple[float, float, float]:
    """Retorna (base, aliquota, valor)."""
    m = re.search(
        r"\bICMS\b\s+([\d.]+,\d{2})\s+([\d.,]+)\s+([\d.]+,\d{2})",
        text, re.IGNORECASE,
    )
    if m:
        base  = abs(_br2f(m.group(1)))
        aliq  = abs(_br2f(m.group(2)))
        valor = abs(_br2f(m.group(3)))
        if base > 0 and valor < base:
            return round(base, 2), round(aliq, 4), round(valor, 2)
    return 0.0, 0.0, 0.0


def _extrair_pis_cofins(text: str) -> tuple[float, float, float, float, float, float]:
    """Retorna (pis_base, pis_aliq, pis_val, cof_base, cof_aliq, cof_val)."""
    pis_base = pis_aliq = pis_val = 0.0
    cof_base = cof_aliq = cof_val = 0.0

    m = re.search(r"\bPIS\b\s+([\d.]+,\d{2})\s+([\d.,]+)\s+([\d.]+,\d{2})", text, re.IGNORECASE)
    if m:
        pis_base = abs(_br2f(m.group(1)))
        pis_aliq = abs(_br2f(m.group(2)))
        pis_val  = abs(_br2f(m.group(3)))

    m = re.search(r"\bCOFINS\b\s+([\d.]+,\d{2})\s+([\d.,]+)\s+([\d.]+,\d{2})", text, re.IGNORECASE)
    if m:
        cof_base = abs(_br2f(m.group(1)))
        cof_aliq = abs(_br2f(m.group(2)))
        cof_val  = abs(_br2f(m.group(3)))

    return pis_base, pis_aliq, pis_val, cof_base, cof_aliq, cof_val


def _extrair_cip(text: str) -> float:
    m = re.search(r"Ilum\.\s*P[uúü]b\.\s*Distrital\s+([\d.]+,\d{2})", text, re.IGNORECASE)
    if m:
        return abs(_br2f(m.group(1)))
    m2 = re.search(r"COSIP|CIP|Contrib\.?\s+Ilum", text, re.IGNORECASE)
    if m2:
        monies = _RE_MONEY.findall(m2.string[m2.start():m2.start()+60])
        if monies:
            return abs(_br2f(monies[0]))
    return 0.0


def _extrair_bandeira(text: str) -> float:
    m = re.search(r"Acr[eéê]sc?\.?\s+Band\.\s+\w+\s+([\d.]+,\d{2})", text, re.IGNORECASE)
    if m:
        return abs(_br2f(m.group(1)))
    m2 = re.search(r"(?:Bandeira|Adicional)\s+\w+\s+([\d.]+,\d{2})", text, re.IGNORECASE)
    if m2:
        return abs(_br2f(m2.group(1)))
    return 0.0


def _extrair_trib_federal(text: str) -> float:
    """Trib.Federal(X%) val- → retorna como positivo (deduçao na fatura)."""
    m = re.search(r"Trib\.?Federal\s*\([^)]+\)\s*([\d.]+,\d{2})-", text, re.IGNORECASE)
    if m:
        return abs(_br2f(m.group(1)))
    return 0.0


def _extrair_instalacao_do_texto(text: str) -> str:
    upper = _norm(text)
    m = re.search(r"CODIGO\s+D[AO]\s+INSTALACAO\s*:?\s*(\d{4,})", upper)
    if m:
        return m.group(1)
    m2 = re.search(r"CODIGO\s+DO\s+CLIENTE\s*:?\s*(\d{4,})", upper)
    if m2:
        return m2.group(1)
    return ""


# =============================================================================
# CARIMBO
# =============================================================================

_indice_master: MasterIndice | None = None
_carimbo_lookup: dict[tuple[str, str], str] = {}
_lookup_construido = False


def _get_indice() -> MasterIndice:
    global _indice_master
    if _indice_master is None:
        _indice_master = MasterIndice()
    return _indice_master


def _construir_lookup() -> None:
    global _lookup_construido
    if _lookup_construido:
        return
    from indice_master import MASTER_FILE
    import csv as _csv
    if not MASTER_FILE.exists():
        _lookup_construido = True
        return
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(MASTER_FILE, newline="", encoding=enc) as f:
                for row in _csv.DictReader(f):
                    if (row.get("SISTEMA") or "").strip().upper() != SISTEMA_MASTER.upper():
                        continue
                    uc  = (row.get("UC") or "").strip()
                    ref = (row.get("MES_REF") or "").strip()
                    bb  = (row.get("INDICE") or "").strip()
                    if uc and ref and bb.startswith("BB_"):
                        _carimbo_lookup[(uc, ref)] = bb
            break
        except UnicodeDecodeError:
            continue
        except Exception:
            break
    _lookup_construido = True


def _obter_carimbo(instalacao: str, mes_ref: dt.date | None, filename: str) -> str:
    indice = _get_indice()
    ref_str = mes_ref.strftime("%m-%Y") if mes_ref else ""

    # Carimbo ja atribuido (arquivo BB_XXXXXXX.pdf no staging)
    stem = Path(filename).stem
    if stem.upper().startswith("BB_"):
        return stem

    if instalacao and ref_str:
        _construir_lookup()
        existente = _carimbo_lookup.get((instalacao, ref_str))
        if existente:
            return existente

    carimbo = indice.consumir_carimbo()
    if instalacao and ref_str:
        _carimbo_lookup[(instalacao, ref_str)] = carimbo
        try:
            indice.registrar(
                indice_bb=carimbo,
                sistema=SISTEMA_MASTER,
                uc=instalacao,
                mes_ref=ref_str,
                arquivo=filename,
                estado="DISTRITO FEDERAL",
                concessionaria="Neoenergia Brasilia",
            )
        except Exception as exc:
            log.warning(f"  [indice_master] Falha ao registrar {filename}: {exc}")
    return carimbo


# =============================================================================
# PROCESSAMENTO
# =============================================================================

def processar_pdf(pdf_path: str | Path) -> dict:
    filename = Path(pdf_path).name
    try:
        text = _extrair_texto(pdf_path)

        # Instalacao
        instalacao = _instalacao_do_nome(filename) or _extrair_instalacao_do_texto(text)

        # Se arquivo BB_*.pdf e instalacao nao identificada pelo nome, busca no indice_master
        if not instalacao and Path(filename).stem.upper().startswith("BB_"):
            stem_bb = Path(filename).stem
            _construir_lookup()
            for (uc, ref), bb in _carimbo_lookup.items():
                if bb == stem_bb:
                    instalacao = uc
                    break
            if not instalacao:
                from indice_master import MASTER_FILE
                import csv as _csv2
                try:
                    for enc2 in ("utf-8-sig", "utf-8", "latin-1"):
                        try:
                            with open(MASTER_FILE, newline="", encoding=enc2) as _f:
                                for _row in _csv2.DictReader(_f):
                                    if (_row.get("INDICE") or "").strip() == stem_bb:
                                        instalacao = (_row.get("UC") or "").strip()
                                        break
                            break
                        except UnicodeDecodeError:
                            continue
                except Exception:
                    pass

        # Campos básicos
        mes_ref, val_fatura, vencimento = _extrair_ref_total_vcto(text)
        emissao = _extrair_emissao(text)
        leit_ant, leit_at = _extrair_leituras(text)
        cod_barras = _extrair_codigo_barras(text)
        nota_fiscal = _extrair_nota_fiscal(text)

        # Consumo
        kWh, tusd_val, te_val = _extrair_consumo(text)
        consumo_val = round(tusd_val + te_val, 2)

        # Impostos
        icms_base, icms_aliq, icms_val = _extrair_icms(text)
        _, pis_aliq, pis_val, _, cof_aliq, cof_val = _extrair_pis_cofins(text)
        cip = _extrair_cip(text)
        bandeira = _extrair_bandeira(text)
        trib_fed = _extrair_trib_federal(text)

        # Carimbo
        carimbo_bb = _obter_carimbo(instalacao, mes_ref, filename)
        stem = Path(filename).stem
        if stem.upper().startswith("BB_"):
            carimbo_num = int(stem.replace("BB_", "").replace("bb_", "")) if stem[3:].isdigit() else 0
        else:
            carimbo_num = int(carimbo_bb.replace("BB_", "")) if carimbo_bb.startswith("BB_") else 0

        dados = {h: "" for h in HEADERS_REF}

        # Consumo BT: campos fora-ponta (unico posto)
        dados["fatConFPontaIndFaturado"]   = kWh
        dados["fatConFPontaIndRegistrado"] = kWh
        dados["fatConFPontaIndValorReais"] = consumo_val

        # Impostos
        dados["fatICMS"]              = icms_val
        dados["fatDesIcmsAliquota"]   = icms_aliq
        dados["fatPIS"]               = pis_val
        dados["fatDescPisAliquota"]   = pis_aliq
        dados["fatCOFINS"]            = cof_val
        dados["fatDesCofinsAliquota"] = cof_aliq
        dados["fatIlumPublica"]       = cip
        dados["fatValBandeira"]       = bandeira
        dados["fatMultas"]            = 0.0

        # Tributo federal (retencao implicita no valor total)
        dados["fatDescConsumoPercRetImposto"] = 5.85 if trib_fed > 0 else 0.0
        dados["fatDescConsumoValRetImposto"]  = round(-trib_fed, 2) if trib_fed > 0 else 0.0

        # Campos gerais
        dados["fatCarimbo"]         = carimbo_num
        dados["Instalacao"]         = instalacao
        dados["CODIGOCLIENTE"]      = instalacao
        dados["concCod"]            = CONC_COD
        dados["cadTarifaCod"]       = "Convencional"
        dados["cadSubGrupoCod"]     = "B3 [<2,3kV]"
        dados["TARIFA_DETECTADA"]   = "B3"
        dados["NOTAFISCAL"]         = nota_fiscal
        dados["fatCodigoBarras"]    = cod_barras
        dados["fatValorFatura"]     = val_fatura
        dados["fatValorNotaFiscal"] = round(icms_base if icms_base > 0 else val_fatura, 2)

        if mes_ref:
            dados["fatDataReferencia"] = mes_ref.strftime("%d/%m/%Y")
        if emissao:
            dados["fatDataEmissao"] = emissao
        if vencimento:
            dados["fatDataVcto"] = vencimento
        if leit_ant:
            dados["fatDataLeituraAnterior"] = leit_ant
        if leit_at:
            dados["fatDataLeituraAtual"] = leit_at

        dados["ARQUIVO"] = filename
        dados["ERRO"]    = ""

        log.info(
            f"  OK  {filename}"
            f"  | carimbo {carimbo_bb}"
            f"  | kWh={kWh:.0f}"
            f"  | R$={val_fatura:.2f}"
            f"  | ICMS={icms_val:.2f}"
        )
        return dados

    except Exception as exc:
        log.error(f"  ERRO  {filename}: {exc}", exc_info=True)
        return {
            "fatCarimbo": 0,
            "Instalacao": _instalacao_do_nome(filename),
            "concCod":    CONC_COD,
            "TARIFA_DETECTADA": "ERRO",
            "ARQUIVO":    filename,
            "ERRO":       str(exc),
        }


def processar_pasta(pasta: Path, xlsx_saida: Path) -> None:
    pdfs = sorted(pasta.glob("*.pdf"))
    if not pdfs:
        log.warning(f"Nenhum PDF em: {pasta}")
        return
    log.info(f"Processando {len(pdfs)} PDFs em {pasta}")
    registros = [processar_pdf(p) for p in pdfs]
    registros.sort(key=lambda r: int(r.get("fatCarimbo") or 0))
    salvar_excel(registros, xlsx_saida)
    log.info(f"Salvo: {xlsx_saida}  ({len(registros)} faturas)")


def parse_args() -> argparse.Namespace:
    hoje = dt.date.today()
    p = argparse.ArgumentParser(description="OCR Neoenergia Brasilia (CEB) BT -> XLSX")
    p.add_argument("--pasta",  type=str, default=str(PASTA_PDF_DEFAULT))
    p.add_argument("--saida",  type=str, default="")
    p.add_argument("--mes",    type=int, default=hoje.month)
    p.add_argument("--ano",    type=int, default=hoje.year)
    p.add_argument("--carimbo", action="append", default=[])
    return p.parse_args()


def main() -> int:
    args = parse_args()
    pasta = Path(args.pasta)
    mes   = f"{args.mes:02d}"
    ano   = str(args.ano)

    if args.saida:
        xlsx_saida = Path(args.saida)
    else:
        xlsx_saida = PASTA_SAIDA_DEFAULT / f"ocr_ceb_BT_{mes}{ano}.xlsx"

    log.info("  OCR NEOENERGIA BRASILIA (CEB) -- BT".center(60))
    log.info(f"  Pasta : {pasta}")
    log.info(f"  Saida : {xlsx_saida}")

    if not pasta.exists():
        log.error(f"Pasta nao encontrada: {pasta}")
        return 1

    processar_pasta(pasta, xlsx_saida)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
