#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pdfplumber

from core.ocr import ocr_bt_generico
from core.ocr.ocr_bt_cemig_adapter import main_bt_generico
from core.ocr.ocr_neoenergia import _extract_ilum_publica

_SAIDA_ROOT = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/RGE_pipeline_saida/BT")


def _texto_pdf(path: Path) -> str:
    with pdfplumber.open(str(path)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def _to_float_abs(text: str) -> float:
    raw = str(text or "").strip()
    raw = raw.replace("R$", "").replace("%", "").strip()
    raw = raw.lstrip("-").rstrip("-").strip()
    return ocr_bt_generico._br2f(raw)


def _to_float_signed(text: str) -> float:
    raw = str(text or "").strip()
    raw = raw.replace("R$", "").replace("%", "").strip()
    neg = raw.startswith("-") or raw.endswith("-")
    raw = raw.lstrip("-").rstrip("-").strip()
    val = ocr_bt_generico._br2f(raw)
    return -abs(val) if neg else abs(val)


def _nums_after_kwh(line: str, *, signed: bool = False) -> list[float]:
    m = re.search(r"kWh\s+(.*)", line, re.IGNORECASE)
    trecho = m.group(1) if m else line
    conv = _to_float_signed if signed else _to_float_abs
    return [
        conv(token)
        for token in re.findall(r"-?\d[\d.]*(?:,\d+)?-?", trecho)
        if abs(conv(token)) > 0
    ]


def _extract_rge_energia_campos(txt: str) -> dict[str, float]:
    qtd_fwd_tusd = qtd_fwd_alt = 0.0
    qtd_inj_tusd = qtd_inj_alt = 0.0
    val_fwd = val_inj = 0.0
    band_pos = band_neg = 0.0

    for line in txt.splitlines():
        up = line.upper()
        is_bandeira = "BANDEIRA" in up or "CRED ADC BAND" in up
        is_inj = bool(re.search(r"\bINJ\b|INJET", up))
        is_consumo = "CONSUMO" in up
        is_disponibilidade = "CUSTO DISP" in up or "DISP SISTEMA" in up

        if is_bandeira:
            nums = _nums_after_kwh(line, signed=True)
            if nums:
                is_credito = bool(re.search(r"CRED", up))
                if is_credito:
                    valor_bandeira = nums[0]
                else:
                    valor_bandeira = nums[-1] if len(nums) >= 3 and abs(nums[1]) < 1 else nums[0]
                if is_credito or valor_bandeira < 0:
                    band_neg += abs(valor_bandeira)
                else:
                    band_pos += abs(valor_bandeira)
            continue

        if "KWH" not in up:
            continue
        if re.search(r"REATIV|DEMANDA|MULTA|JUROS|ILUM", up):
            continue
        if not (is_consumo or is_inj or is_disponibilidade):
            continue

        nums = _nums_after_kwh(line, signed=True)
        if len(nums) < 3:
            continue

        qtd = abs(nums[0])
        valor = abs(nums[3] if len(nums) >= 4 else nums[-1])
        is_tusd = "TUSD" in up or "USO SISTEMA" in up

        if is_inj:
            val_inj += valor
            if is_tusd:
                qtd_inj_tusd += qtd
            else:
                qtd_inj_alt += qtd
        else:
            val_fwd += valor
            if is_tusd:
                qtd_fwd_tusd += qtd
            else:
                qtd_fwd_alt += qtd

    qtd_fwd = qtd_fwd_tusd or qtd_fwd_alt
    qtd_inj = qtd_inj_tusd or qtd_inj_alt
    return {
        "fatConFPontaIndRegistrado": round(qtd_fwd, 3) if qtd_fwd else 0.0,
        "fatConFPontaIndFaturado": round(qtd_fwd, 3) if qtd_fwd else 0.0,
        "fatConFPontaIndValorReais": round(val_fwd, 2) if val_fwd else 0.0,
        "fatConFPontaInjetadoRegistrado": round(qtd_inj, 3) if qtd_inj else 0.0,
        "fatConFPontaInjetadoFaturado": round(qtd_inj, 3) if qtd_inj else 0.0,
        "fatConFPontaInjetadoValorReais": round(val_inj, 2) if val_inj else 0.0,
        "fatConFPontaInjetadoUsina": round(qtd_inj, 3) if qtd_inj else 0.0,
        "fatValBandeira": round(band_pos, 2) if band_pos else 0.0,
        "fatValBandeira2": -round(band_neg, 2) if band_neg else 0.0,
    }


def _extract_rge_total_distribuidora(txt: str) -> float:
    m = re.search(r"Total\s+Distribuidora\s+([\d.,]+)", txt, re.IGNORECASE)
    return _to_float_abs(m.group(1)) if m else 0.0


def _extract_rge_retencoes(txt: str) -> dict[str, float]:
    result: dict[str, float] = {
        "fatDescPisPercRetImposto": 0.0,
        "fatDescPisValRetImposto": 0.0,
        "fatDescCofinsPercRetImposto": 0.0,
        "fatDescCofinsValRetImposto": 0.0,
        "fatDescCsllPercRetImposto": 0.0,
        "fatDescCsllValRetImposto": 0.0,
        "fatDescIrpjPercRetImposto": 0.0,
        "fatDescIrpjValRetImposto": 0.0,
        "fatDescIrrfPercRetImposto": 0.0,
        "fatDescIrrfValRetImposto": 0.0,
        "fatDescConsumoPercRetImposto": 0.0,
        "fatDescConsumoValRetImposto": 0.0,
    }
    fixos = {
        "PIS": ("fatDescPisPercRetImposto", "fatDescPisValRetImposto", 0.65),
        "COFINS": ("fatDescCofinsPercRetImposto", "fatDescCofinsValRetImposto", 3.00),
        "CSLL": ("fatDescCsllPercRetImposto", "fatDescCsllValRetImposto", 1.00),
        "IRRF": ("fatDescIrpjPercRetImposto", "fatDescIrpjValRetImposto", 1.20),
        "IRPJ": ("fatDescIrpjPercRetImposto", "fatDescIrpjValRetImposto", 1.20),
    }

    def _valor_retencao_linha(line: str, cod: str) -> float:
        # As linhas reais podem vir acompanhadas de histórico de consumo na lateral:
        #   Retencao Consumo COFINS-3,0% 17,44- AGO 25 ... 2096 32
        # Não se deve usar o último número da linha; o valor correto é o primeiro
        # valor monetário imediatamente depois do percentual da retenção.
        m = re.search(
            rf"\b{cod}\b\s*[-–]?\s*[\d.,]+\s*%?\s+(-?\s*[\d.,]+-?)",
            line,
            re.IGNORECASE,
        )
        if m:
            return _to_float_abs(m.group(1))
        m = re.search(rf"\b{cod}\b\s+(-?\s*[\d.,]+-?)", line, re.IGNORECASE)
        return _to_float_abs(m.group(1)) if m else 0.0

    for line in txt.splitlines():
        up = line.upper()
        if "RETENC" not in up:
            continue
        for cod, (campo_perc, campo_val, perc_fixa) in fixos.items():
            if cod not in up:
                continue
            valor = _valor_retencao_linha(line, cod)
            if valor > 0:
                result[campo_perc] = perc_fixa
                result[campo_val] = result.get(campo_val, 0.0) - valor
    return result


def processar_texto_rge(txt: str, src_original: str | None = None) -> dict:
    uc = ocr_bt_generico._extract_instalacao(txt, str(src_original or ""))
    nf = ocr_bt_generico._extract_nf(txt)
    emissao = ocr_bt_generico._extract_emissao(txt)
    mes_ref = ocr_bt_generico._extract_mes_ref(txt)
    vcto = ocr_bt_generico._extract_vencimento(txt)
    ant, atu = ocr_bt_generico._extract_datas_leitura(txt)
    valor = ocr_bt_generico._extract_valor(txt)
    if not valor:
        valor = _extract_rge_total_distribuidora(txt)
    icms = ocr_bt_generico._extract_icms(txt)
    icms_base = ocr_bt_generico._extract_icms_base(txt)
    icms_aliq = ocr_bt_generico._extract_icms_aliquota(txt)
    pis = ocr_bt_generico._extract_pis(txt)
    pis_aliq = ocr_bt_generico._extract_pis_aliquota(txt)
    cofins = ocr_bt_generico._extract_cofins(txt)
    cofins_aliq = ocr_bt_generico._extract_cofins_aliquota(txt)
    barcode = ocr_bt_generico._extract_barcode(txt)
    energia = _extract_rge_energia_campos(txt)
    retencoes = _extract_rge_retencoes(txt)

    def _fmt(data):
        return data.strftime("%d/%m/%Y") if data else ""

    return {
        "fatCarimbo": "",
        "Instalacao": uc,
        "CODIGOCLIENTE": uc,
        "NOTAFISCAL": nf,
        "CNPJ": "",
        "fatDataEmissao": _fmt(emissao),
        "fatDataVcto": _fmt(vcto),
        "fatDataLeituraAnterior": _fmt(ant),
        "fatDataLeituraAtual": _fmt(atu),
        "fatDataReferencia": mes_ref.strftime("01/%m/%Y") if mes_ref else "",
        "fatValorFatura": valor,
        "fatValorNotaFiscal": valor,
        "fatConFPontaIndRegistrado": energia.get("fatConFPontaIndRegistrado", 0),
        "fatConFPontaIndFaturado": energia.get("fatConFPontaIndFaturado", 0),
        "fatConFPontaIndValorReais": energia.get("fatConFPontaIndValorReais", 0),
        "fatConFPontaInjetadoRegistrado": energia.get("fatConFPontaInjetadoRegistrado", 0),
        "fatConFPontaInjetadoFaturado": energia.get("fatConFPontaInjetadoFaturado", 0),
        "fatConFPontaInjetadoValorReais": energia.get("fatConFPontaInjetadoValorReais", 0),
        "fatConFPontaInjetadoUsina": energia.get("fatConFPontaInjetadoUsina", 0),
        "fatICMS": icms,
        "fatICMSBase": icms_base,
        "fatDesIcmsAliquota": icms_aliq,
        "fatPIS": pis,
        "fatDescPisAliquota": pis_aliq,
        "fatCOFINS": cofins,
        "fatDesCofinsAliquota": cofins_aliq,
        "fatCodigoBarras": barcode,
        "fatValBandeira": energia.get("fatValBandeira", 0),
        "fatValBandeira2": energia.get("fatValBandeira2", 0),
        "fatDescIrpjPercRetImposto": retencoes.get("fatDescIrpjPercRetImposto", 0),
        "fatDescIrpjValRetImposto": retencoes.get("fatDescIrpjValRetImposto", 0),
        "fatDescPisPercRetImposto": retencoes.get("fatDescPisPercRetImposto", 0),
        "fatDescPisValRetImposto": retencoes.get("fatDescPisValRetImposto", 0),
        "fatDescCofinsPercRetImposto": retencoes.get("fatDescCofinsPercRetImposto", 0),
        "fatDescCofinsValRetImposto": retencoes.get("fatDescCofinsValRetImposto", 0),
        "fatDescCsllPercRetImposto": retencoes.get("fatDescCsllPercRetImposto", 0),
        "fatDescCsllValRetImposto": retencoes.get("fatDescCsllValRetImposto", 0),
        "fatDescIrrfPercRetImposto": 0,
        "fatDescIrrfValRetImposto": 0,
        "fatDescConsumoPercRetImposto": 0,
        "fatDescConsumoValRetImposto": 0,
        "fatIlumPublica": _extract_ilum_publica(txt),
    }


def processar_pdf(pdf_path: Path) -> dict:
    txt = _texto_pdf(pdf_path)
    rec = processar_texto_rge(txt, str(pdf_path))
    stem = pdf_path.stem.strip()
    rec["fatCarimbo"] = stem if stem.upper().startswith("BB_") else ""
    return rec


if __name__ == "__main__":
    _SAIDA_ROOT.mkdir(parents=True, exist_ok=True)
    raise SystemExit(
        main_bt_generico(
            sistema="RGE SUL",
            default_pasta="",
            default_saida_stem=str(_SAIDA_ROOT / "ocr_rge_sul_bt"),
            description="OCR RGE Sul BT -> XLSX no schema CEMIG",
            parser_func=processar_pdf,
        )
    )
