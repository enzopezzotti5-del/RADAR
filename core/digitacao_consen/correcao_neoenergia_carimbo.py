#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Correcao Neoenergia (COELBA / CELPE / COSERN) por carimbo.

Fluxo:
1. Localiza os PDFs dos carimbos informados em DOWNLOAD NEOENERGIA.
2. Reprocessa com o OCR Neoenergia atualizado.
3. Corrige no Consen: impostos, aliquotas, retencoes, observacoes,
   valor nota fiscal, desconto fio, demanda (MT) e bandeira.

Por seguranca, nao salva por padrao. Use --salvar para efetivar.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from selenium.common.exceptions import NoAlertPresentException
from selenium.webdriver.common.by import By

DEFAULT_SAIDA_DIR = "//10.10.250.21/Energia/ARQUIVOS ENZO/NEOENERGIA_pipeline_saida/correcoes_por_carimbo"
DEFAULT_PDFS_ROOT = "//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD NEOENERGIA"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _venv_check  # noqa: E402,F401

try:
    from digitacao_consen import correcao_fluxo_base as fluxo_base  # noqa: E402
    from digitacao_consen.correcao_fluxo_base import (  # noqa: E402
        CorrecaoFluxoConfig,
        formatar_valor_para_campo,
        log,
        warn,
    )
    from digitacao_consen.digitacao_consen_enel import formatar_numero_br  # noqa: E402
    from ocr.ocr_neoenergia import processar_pdf_direto  # noqa: E402
except ModuleNotFoundError:
    import correcao_fluxo_base as fluxo_base  # type: ignore  # noqa: E402
    from correcao_fluxo_base import (  # type: ignore  # noqa: E402
        CorrecaoFluxoConfig,
        formatar_valor_para_campo,
        log,
        warn,
    )
    from digitacao_consen_enel import formatar_numero_br  # type: ignore  # noqa: E402
    from ocr_neoenergia import processar_pdf_direto  # type: ignore  # noqa: E402

LOGIN_URL = fluxo_base.LOGIN_URL
BASE_URL = LOGIN_URL.rsplit("login.php", 1)[0]
EDIT_HASH = "#bpg/gestao/fatura/editaFaturaCarimbo.php"
EDIT_URL = os.environ.get("CONSEN_EDITA_FATURA_CARIMBO_URL", f"{BASE_URL}index.php{EDIT_HASH}")

SAIDA_DIR = Path(os.environ.get("CONSEN_CORRECAO_SAIDA", DEFAULT_SAIDA_DIR))
PDFS_ROOT = Path(os.environ.get("CONSEN_CORRECAO_NEO_ROOT", DEFAULT_PDFS_ROOT))
FECHAR_AO_FINAL = os.environ.get("CONSEN_CORRECAO_FECHAR", "1").strip().lower() not in {"0", "false", "nao", "não"}
EXECUCAO_CSV = SAIDA_DIR / "correcao_execucao.csv"

CAMPOS_CRITICOS_TELA: tuple[str, ...] = (
    "btnSalvar",
    "instalacao",
)

# (campo_logico, header_xlsx)  — usados para BT e MT
CAMPO_OCR_PARA_LOGICO: tuple[tuple[str, str], ...] = (
    ("cadTarifaCod",                  "cadTarifaCod"),
    # Demanda FP (MT)
    ("fatDemPontaRegistrada",         "fatDemPontaRegistrada"),
    ("fatDemPontaFaturada",           "fatDemPontaFaturada"),
    ("fatDemPontaValorReais",         "fatDemPontaValorReais"),
    ("fatDemFPontaIndRegistrada",     "fatDemFPontaIndRegistrada"),
    ("fatDemFPontaIndFaturada",       "fatDemFPontaIndFaturada"),
    ("fatDemFPontaIndValorReais",     "fatDemFPontaIndValorReais"),
    ("fatDemFPontaIndUltra",          "fatDemFPontaIndUltra"),
    ("fatDemFPontaIndUltraValorReais","fatDemFPontaIndUltraValorReais"),
    ("fatDemFPontaExcFaturada",       "fatDemFPontaExcFaturada"),
    ("fatDemFPontaExcValorReais",     "fatDemFPontaExcValorReais"),
    # Consumo
    ("fatConPontaRegistrado",         "fatConPontaRegistrado"),
    ("fatConPontaFaturado",           "fatConPontaFaturado"),
    ("fatConPontaValorReais",         "fatConPontaValorReais"),
    ("fatConFPontaIndRegistrado",     "fatConFPontaIndRegistrado"),
    ("fatConFPontaIndFaturado",       "fatConFPontaIndFaturado"),
    ("fatConFPontaIndValorReais",     "fatConFPontaIndValorReais"),
    # Impostos e aliquotas
    ("fatICMS",                       "fatICMS"),
    ("fatPIS",                        "fatPIS"),
    ("fatCOFINS",                     "fatCOFINS"),
    ("fatDesIcmsAliquota",            "fatDesIcmsAliquota"),
    ("fatDescPisAliquota",            "fatDescPisAliquota"),
    ("fatDesCofinsAliquota",          "fatDesCofinsAliquota"),
    # Valor NF e bandeira
    ("fatValorNotaFiscal",            "fatValorNotaFiscal"),
    ("fatValBandeira",                "fatValBandeira"),
    ("fatValBandeira2",               "fatValBandeira2"),
    ("fatTributoFederalPerc",         "fatTributoFederalPerc"),
    ("fatTributoFederalVal",          "fatTributoFederalVal"),
    # Desconto fio (MT)
    ("fatDescontoFio",                "fatDescontoFio"),
    ("fatDescontoFioKWh",             "fatDescontoFioKWh"),
    # Retencoes — consumo (BT+MT)
    ("fatDescConsumoPercRetImposto",  "fatDescConsumoPercRetImposto"),
    ("fatDescConsumoValRetImposto",   "fatDescConsumoValRetImposto"),
    # Retencoes — desdobradas (Neoenergia 9.45% MT)
    ("fatDescPisPercRetImposto",      "fatDescPisPercRetImposto"),
    ("fatDescPisValRetImposto",       "fatDescPisValRetImposto"),
    ("fatDescCofinsPercRetImposto",   "fatDescCofinsPercRetImposto"),
    ("fatDescCofinsValRetImposto",    "fatDescCofinsValRetImposto"),
    ("fatDescCsllPercRetImposto",     "fatDescCsllPercRetImposto"),
    ("fatDescCsllValRetImposto",      "fatDescCsllValRetImposto"),
    ("fatDescIrpjPercRetImposto",     "fatDescIrpjPercRetImposto"),
    ("fatDescIrpjValRetImposto",      "fatDescIrpjValRetImposto"),
    # Iluminacao publica e multas
    ("fatIlumPublica",                "fatIlumPublica"),
    ("fatMultas",                     "fatMultas"),
)

# HTML IDs confirmados pelo DOM do Consen (mesmo sistema que Energisa MT)
CAMPO_TELA_ALTERNATIVOS: dict[str, tuple[str, ...]] = {
    "cadTarifaCod":                  ("cb-tarifa",),
    "fatDemPontaRegistrada":         ("fatDemPontaRegistrada",),
    "fatDemPontaFaturada":           ("fatDemPonta", "fatDemPontaFaturada"),
    "fatDemPontaValorReais":         ("fatDemPontaValorReais",),
    "fatDemFPontaIndRegistrada":     ("fatDemFPontaIndRegistrada",),
    "fatDemFPontaIndFaturada":       ("fatDemFPontaIndutivo", "fatDemFPontaIndFaturada"),
    "fatDemFPontaIndValorReais":     ("fatDemFPontaIndValorReais",),
    "fatDemFPontaIndUltra":          ("fatDemFPontaIndUltra",),
    "fatDemFPontaIndUltraValorReais":("fatDemFPontaIndUltraValorReais",),
    "fatDemFPontaExcFaturada":       ("fatDemFPontaIndExc", "fatDemFPontaExcFaturada"),
    "fatDemFPontaExcValorReais":     ("fatDemFPontaIndExcValorReais", "fatDemFPontaExcValorReais"),
    "fatConPontaRegistrado":         ("fatConPontaRegistrado",),
    "fatConPontaFaturado":           ("fatConPonta", "fatConPontaFaturado"),
    "fatConPontaValorReais":         ("fatConPontaValorReais",),
    "fatConFPontaIndRegistrado":     ("fatConFPontaIndRegistrado",),
    "fatConFPontaIndFaturado":       ("fatConFPontaIndutivo", "fatConFPontaIndFaturado"),
    "fatConFPontaIndValorReais":     ("fatConFPontaIndValorReais",),
    "fatICMS":                       ("fatICMS",),
    "fatPIS":                        ("fatPIS",),
    "fatCOFINS":                     ("fatCofins", "fatCOFINS"),
    "fatDesIcmsAliquota":            ("fatDesIcmsAliquota",),
    "fatDescPisAliquota":            ("fatDescPisAliquota",),
    "fatDesCofinsAliquota":          ("fatDesCofinsAliquota", "fatDescCofinsAliquota"),
    "fatValorNotaFiscal":            ("fatValorNFiscal", "fatValorNotaFiscal"),
    "fatValBandeira":                ("fatValBandeira",),
    "fatValBandeira2":               ("fatValBandeira2",),
    "fatTributoFederalPerc":         ("fatTributoFederalPerc",),
    "fatTributoFederalVal":          ("fatTributoFederalVal",),
    "fatDescontoFio":                ("fatDescontoFio",),
    "fatDescontoFioKWh":             ("fatDescontoFioKWh",),
    "fatDescConsumoPercRetImposto":  ("fatDescConsumoPercRetImposto",),
    "fatDescConsumoValRetImposto":   ("fatDescConsumoValRetImposto",),
    "fatDescPisPercRetImposto":      ("fatDescPisPercRetImposto",),
    "fatDescPisValRetImposto":       ("fatDescPisValRetImposto",),
    "fatDescCofinsPercRetImposto":   ("fatDescCofinsPercRetImposto",),
    "fatDescCofinsValRetImposto":    ("fatDescCofinsValRetImposto",),
    "fatDescCsllPercRetImposto":     ("fatDescCsllPercRetImposto",),
    "fatDescCsllValRetImposto":      ("fatDescCsllValRetImposto",),
    "fatDescIrpjPercRetImposto":     ("fatDescIrpjPercRetImposto",),
    "fatDescIrpjValRetImposto":      ("fatDescIrpjValRetImposto",),
    "fatIlumPublica":                ("fatIluminacaoPublica", "fatIlumPublica"),
    "fatMultas":                     ("fatMultas",),
}

ORDEM_CAMPOS_CORRECAO: tuple[str, ...] = tuple(campo for campo, _ in CAMPO_OCR_PARA_LOGICO)
CONFIG = CorrecaoFluxoConfig(
    saida_dir=SAIDA_DIR,
    execucao_csv=EXECUCAO_CSV,
    edit_url=EDIT_URL,
    ordem_campos=ORDEM_CAMPOS_CORRECAO,
    fechar_ao_final=FECHAR_AO_FINAL,
)

_CAMPOS_RETENCOES_E_BANDEIRAS: frozenset[str] = frozenset({
    "fatValBandeira",
    "fatValBandeira2",
    "fatTributoFederalPerc",
    "fatTributoFederalVal",
    "fatDescConsumoPercRetImposto",
    "fatDescConsumoValRetImposto",
    "fatDescPisPercRetImposto",
    "fatDescPisValRetImposto",
    "fatDescCofinsPercRetImposto",
    "fatDescCofinsValRetImposto",
    "fatDescCsllPercRetImposto",
    "fatDescCsllValRetImposto",
    "fatDescIrpjPercRetImposto",
    "fatDescIrpjValRetImposto",
})


def normalizar_carimbo(carimbo: str) -> str:
    return fluxo_base.normalizar_carimbo(carimbo)


def valor_vazio(valor: Any) -> bool:
    return fluxo_base.valor_vazio(valor)


def formatar_valor_correcao(header: str, valor: Any) -> str:
    return formatar_valor_para_campo(header, valor, "text")


def _inferir_mes_ano(pdf: Path) -> tuple[int, int]:
    for parte in (pdf.parent.name, pdf.parent.parent.name, pdf.parent.parent.parent.name):
        m = re.search(r"(\d{4})-(\d{2})", str(parte))
        if m:
            return int(m.group(2)), int(m.group(1))
        m2 = re.fullmatch(r"(\d{2})(\d{2})(\d{4})", str(parte).strip())
        if m2:
            return int(m2.group(2)), int(m2.group(3))
    import datetime
    hoje = datetime.date.today()
    return hoje.month, hoje.year


def _score_data_pasta(pdf: Path) -> tuple[int, int, int]:
    for parte in (pdf.parent.name, pdf.parent.parent.name):
        m = re.search(r"(\d{4})-(\d{2})", str(parte))
        if m:
            return int(m.group(1)), int(m.group(2)), 0
        m2 = re.fullmatch(r"(\d{2})(\d{2})(\d{4})", str(parte).strip())
        if m2:
            return int(m2.group(3)), int(m2.group(2)), int(m2.group(1))
    return (0, 0, 0)


def _observacoes_da_linha_ocr(row: dict[str, Any]) -> list[tuple[str, float]]:
    pares: list[tuple[str, float]] = []
    vistos: set[tuple[str, float]] = set()
    for idx in range(1, 6):
        cod = str(row.get(f"obsCod_{idx}") or "").strip()
        if not cod or cod == "0":
            continue
        try:
            valor = round(float(row.get(f"obsValor_{idx}") or 0.0), 2)
        except Exception:
            continue
        if abs(valor) <= 0.004:
            continue
        par = (cod, valor)
        if par in vistos:
            continue
        vistos.add(par)
        pares.append(par)
    return pares


def _correcoes_da_linha_ocr(row: dict[str, Any]) -> dict[str, Any]:
    correcoes: dict[str, Any] = {}
    for campo_logico, header in CAMPO_OCR_PARA_LOGICO:
        valor = row.get(header)
        if valor_vazio(valor):
            continue
        correcoes[campo_logico] = formatar_valor_correcao(header, valor)

    obs = []
    if str(row.get("concCod") or "").strip().upper() != "CELPE":
        obs = _observacoes_da_linha_ocr(row)
    if obs:
        correcoes["__obs__"] = obs
    return correcoes


def _filtrar_correcoes(payload: dict[str, Any], somente_retencoes_bandeiras: bool) -> dict[str, Any]:
    if not somente_retencoes_bandeiras:
        return payload
    filtrado: dict[str, Any] = {}
    for campo, valor in payload.items():
        if campo.startswith("__"):
            continue
        if campo in _CAMPOS_RETENCOES_E_BANDEIRAS:
            filtrado[campo] = valor
    return filtrado


def localizar_pdfs_por_carimbo(raiz: Path, carimbos_filtro: set[str]) -> dict[str, Path]:
    encontrados: dict[str, Path] = {}
    if not carimbos_filtro:
        return encontrados

    for atual, _, arquivos in os.walk(raiz):
        pasta = Path(atual)
        for nome in arquivos:
            if not nome.lower().endswith(".pdf"):
                continue
            try:
                carimbo = normalizar_carimbo(Path(nome).stem)
            except Exception:
                continue
            if carimbo not in carimbos_filtro:
                continue
            candidato = pasta / nome
            atual_escolhido = encontrados.get(carimbo)
            if atual_escolhido is None or _score_data_pasta(candidato) > _score_data_pasta(atual_escolhido):
                encontrados[carimbo] = candidato

    for carimbo in sorted(encontrados):
        log(f"[LOCALIZADO] BB_{carimbo} -> {encontrados[carimbo]}")
    return encontrados


def carregar_correcoes_de_raiz_pdfs(raiz: Path, carimbos_filtro: set[str]) -> dict[str, dict[str, Any]]:
    encontrados = localizar_pdfs_por_carimbo(raiz, carimbos_filtro)
    correcoes: dict[str, dict[str, Any]] = {}
    linhas_log: list[dict[str, Any]] = []

    for carimbo in sorted(carimbos_filtro):
        pdf = encontrados.get(carimbo)
        if not pdf:
            linhas_log.append({"carimbo": carimbo, "arquivo": "", "status": "nao_encontrado",
                                "tipo": "", "campos": 0, "obs": 0, "erro": ""})
            warn(f"[LOCALIZADO] BB_{carimbo}: PDF nao encontrado em {raiz}")
            continue

        mes, ano = _inferir_mes_ano(pdf)
        try:
            tipo, row = processar_pdf_direto(pdf, mes, ano)
        except Exception as exc:
            linhas_log.append({"carimbo": carimbo, "arquivo": str(pdf),
                                "status": f"erro_ocr:{type(exc).__name__}",
                                "tipo": "", "campos": 0, "obs": 0, "erro": str(exc)})
            warn(f"[OCR] {pdf.name}: {type(exc).__name__}: {exc}")
            continue

        erro = str(row.get("ERRO") or "").strip()
        if erro:
            linhas_log.append({"carimbo": carimbo, "arquivo": str(pdf),
                                "status": "pulado_linha_com_erro", "tipo": tipo,
                                "campos": 0, "obs": 0, "erro": erro})
            continue

        payload = _correcoes_da_linha_ocr(row)
        obs = list(payload.get("__obs__", []))
        campos = len([k for k in payload if not k.startswith("__")])
        if payload:
            correcoes[carimbo] = payload

        linhas_log.append({"carimbo": carimbo, "arquivo": str(pdf),
                            "status": "ok" if payload else "sem_campos", "tipo": tipo,
                            "campos": campos, "obs": len(obs), "erro": ""})

    _salvar_log_preparacao(linhas_log)
    return correcoes


def _salvar_log_preparacao(linhas: list[dict[str, Any]]) -> None:
    SAIDA_DIR.mkdir(parents=True, exist_ok=True)
    path = SAIDA_DIR / "preparacao_correcao_neoenergia.csv"
    campos = ["carimbo", "arquivo", "status", "tipo", "campos", "obs", "erro"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=campos, delimiter=";")
        writer.writeheader()
        writer.writerows(linhas)
    log(f"Log de preparacao salvo: {path}")

    carimbos_ok = sorted({str(l.get("carimbo") or "").strip() for l in linhas if l.get("status") == "ok"})
    txt_path = SAIDA_DIR / "carimbos_preparados_neoenergia.txt"
    txt_path.write_text("\n".join(f"BB_{c}" for c in carimbos_ok if c), encoding="utf-8")
    log(f"Lista de carimbos preparados: {txt_path}")


def registrar_execucao(carimbo: str, status: str, detalhe: str = "") -> None:
    fluxo_base.registrar_execucao(CONFIG.execucao_csv, carimbo, status, detalhe)


def carregar_status_execucao() -> dict[str, str]:
    return fluxo_base.carregar_status_execucao(CONFIG.execucao_csv)


def abrir_driver_logado():
    return fluxo_base.abrir_driver_logado()


def abrir_tela_edicao_carimbo(driver, wait) -> None:
    fluxo_base.abrir_tela_edicao_carimbo(driver, wait, CONFIG.edit_url)


def carregar_fatura_por_carimbo(driver, wait, carimbo: str) -> None:
    fluxo_base.carregar_fatura_por_carimbo(driver, wait, carimbo, CAMPOS_CRITICOS_TELA)


def salvar_snapshot(driver, carimbo: str) -> None:
    fluxo_base.salvar_snapshot(driver, CONFIG.saida_dir, carimbo)


def _localizar_input_sem_wait(driver, campo: str):
    for by, sel in [
        (By.ID, campo),
        (By.NAME, campo),
        (By.CSS_SELECTOR, f"input[id='{campo}'], select[id='{campo}'], textarea[id='{campo}']"),
        (By.CSS_SELECTOR, f"input[name='{campo}'], select[name='{campo}'], textarea[name='{campo}']"),
    ]:
        try:
            encontrados = driver.find_elements(by, sel)
        except Exception:
            continue
        for el in encontrados:
            try:
                if el.is_displayed():
                    return el
            except Exception:
                pass
        if encontrados:
            return encontrados[0]
    return None


def _aceitar_alerta_se_existir(driver) -> None:
    try:
        driver.switch_to.alert.accept()
        time.sleep(0.3)
    except NoAlertPresentException:
        pass
    except Exception:
        pass


def _limpar_observacoes_existentes(driver) -> int:
    script = """
        const root = document.querySelector('#cb-dados-financeiros-obs')?.closest('form, table, div, section') || document;
        const textos = [/excluir/i, /remover/i, /apagar/i, /deletar/i];
        const clicaveis = Array.from(root.querySelectorAll('a, button, input[type="button"], input[type="submit"], i, span'))
            .filter((el) => {
                const txt = ((el.innerText || el.textContent || '') + ' ' + (el.title || '') + ' ' + (el.value || '') + ' ' + (el.getAttribute('onclick') || '') + ' ' + (el.className || '')).trim();
                if (!txt) return false;
                return textos.some((re) => re.test(txt));
            });
        const alvo = clicaveis
            .filter((el) => !el.disabled && (el.offsetWidth || el.offsetHeight || el.getClientRects().length))
            .slice(0, 20)
            .map((el) => {
                if (typeof el.click === 'function') el.click();
                return ((el.innerText || el.textContent || el.title || el.value || el.className || '') + '').trim();
            });
        return alvo.length;
    """
    total = 0
    for _ in range(12):
        try:
            qtd = int(driver.execute_script(script) or 0)
        except Exception:
            qtd = 0
        if qtd <= 0:
            break
        total += qtd
        _aceitar_alerta_se_existir(driver)
        time.sleep(0.6)
    return total


def _preencher_observacoes(driver, wait, pares: list[tuple[str, float]]) -> tuple[int, int]:
    if not pares:
        return 0, 0
    incluidas = 0
    for idx, (cod, valor) in enumerate(pares, start=1):
        el_sel = _localizar_input_sem_wait(driver, "cb-dados-financeiros-obs")
        if el_sel is None:
            warn(f"[OBS] Linha {idx}: select cb-dados-financeiros-obs nao encontrado")
            continue
        try:
            fluxo_base.preencher_elemento_html(driver, el_sel, str(cod))
            log(f"[OBS] Linha {idx}: cod={cod}")
        except Exception as exc:
            warn(f"[OBS] Linha {idx}: erro no select — {type(exc).__name__}")
            continue

        val_fmt = formatar_numero_br(valor) if valor is not None else "0,00"
        el_val = _localizar_input_sem_wait(driver, "fatValorObs")
        if el_val is None:
            el_val = _localizar_input_sem_wait(driver, "txt-dados-financeiros-outros")
        if el_val is None:
            warn(f"[OBS] Linha {idx}: campo de valor nao encontrado")
            continue
        try:
            driver.execute_script(
                "arguments[0].value = '';"
                "arguments[0].dispatchEvent(new Event('focus',{bubbles:true}));",
                el_val,
            )
            el_val.send_keys(val_fmt)
            driver.execute_script(
                "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));"
                "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));",
                el_val,
            )
            log(f"[OBS] Linha {idx}: valor={val_fmt}")
        except Exception as exc:
            warn(f"[OBS] Linha {idx}: erro no campo valor — {type(exc).__name__}")

        el_btn = _localizar_input_sem_wait(driver, "btnIncluiLinha")
        if el_btn is None:
            warn(f"[OBS] Linha {idx}: btnIncluiLinha nao encontrado")
            continue
        try:
            el_btn.click()
            incluidas += 1
            log(f"[OBS] Linha {idx}: incluida")
        except Exception as exc:
            warn(f"[OBS] Linha {idx}: erro ao clicar btnIncluiLinha — {type(exc).__name__}")

    return incluidas, len(pares)


def _aplicar_campo_com_aliases(driver, wait, campo_logico: str, valor: Any) -> tuple[int, int]:
    candidatos = CAMPO_TELA_ALTERNATIVOS.get(campo_logico, (campo_logico,))
    for candidato in candidatos:
        elemento = _localizar_input_sem_wait(driver, candidato)
        if elemento is None:
            continue
        try:
            valor_atual = (elemento.get_attribute("value") or "").strip()
            if valor_atual == str(valor).strip():
                return 0, 1
            ok = fluxo_base.preencher_elemento_html(driver, elemento, valor)
            if ok and campo_logico == "cadTarifaCod":
                fluxo_base._aguardar_sem_spinner(driver, timeout=12, min_wait=1.5)
                log("[TARIFA] AJAX apos mudanca de tarifa estabilizado.")
            return (1, 1) if ok else (0, 0)
        except Exception:
            continue
    warn(f"[CORRECAO] Campo {campo_logico} pulado: nenhum alias localizado ({', '.join(candidatos)})")
    return 0, 0


def aplicar_correcoes(driver, wait, carimbo: str, correcoes: dict[str, Any]) -> tuple[int, int, int]:
    payload = dict(correcoes)
    obs = list(payload.pop("__obs__", []))
    aplicadas = 0
    confirmadas = 0
    ordem = [campo for campo in CONFIG.ordem_campos if campo in payload]
    ordem.extend(campo for campo in payload if campo not in ordem)
    for campo in ordem:
        qtd, ok = _aplicar_campo_com_aliases(driver, wait, campo, payload[campo])
        aplicadas += qtd
        confirmadas += ok
    total_campos = len(ordem)
    if obs:
        removidas = _limpar_observacoes_existentes(driver)
        if removidas:
            log(f"[OBS] {removidas} observacoes existentes removidas antes de reinserir")
    obs_aplicadas, obs_confirmadas = _preencher_observacoes(driver, wait, obs)
    total = total_campos + len(obs)
    return aplicadas + obs_aplicadas, confirmadas + obs_confirmadas, total


def salvar_auditar_e_avancar(driver, wait, carimbo: str) -> None:
    fluxo_base.salvar_auditar_e_avancar(driver, wait, carimbo)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Correcao Neoenergia (COELBA/CELPE/COSERN) por carimbo no Consen")
    p.add_argument("--carimbo", action="append", default=[], help="Ex: --carimbo BB_2010489")
    p.add_argument("--carimbos-arquivo", type=str, default="", help="TXT com um carimbo por linha")
    p.add_argument("--raiz-pdfs", type=str, default=str(PDFS_ROOT), help="Raiz para busca recursiva dos PDFs")
    p.add_argument("--preparar-apenas", action="store_true", help="Apenas OCR + monta correcoes, sem abrir Consen")
    p.add_argument("--retomar-apos", type=str, default="", help="Retoma o lote apos este carimbo")
    p.add_argument("--reprocessar-ok", action="store_true", help="Nao pula carimbos ja concluidos com status ok")
    p.add_argument("--salvar", action="store_true", help="Salva a fatura apos aplicar correcoes")
    p.add_argument("--sem-snapshot", action="store_true", help="Nao salva HTML/JSON da tela carregada")
    p.add_argument("--somente-retencoes-bandeiras", action="store_true", help="Aplica apenas campos de retencoes e bandeiras, sem observacoes")
    p.add_argument("--limite", type=int, default=0, help="Limita a quantidade de carimbos processados")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    carimbos_filtro: set[str] = {normalizar_carimbo(item) for item in list(args.carimbo or [])}
    if args.carimbos_arquivo:
        path = Path(args.carimbos_arquivo)
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                carimbos_filtro.add(normalizar_carimbo(line))

    raiz = Path(args.raiz_pdfs)
    if not raiz.exists():
        print(f"Raiz de PDFs nao encontrada: {raiz}")
        return 2

    correcoes_ocr = carregar_correcoes_de_raiz_pdfs(raiz, carimbos_filtro)
    carimbos = fluxo_base.carregar_lista_carimbos(args)
    carimbos.extend(c for c in sorted(correcoes_ocr) if c not in set(carimbos))

    if args.retomar_apos:
        marcador = normalizar_carimbo(args.retomar_apos)
        if marcador in carimbos:
            carimbos = carimbos[carimbos.index(marcador) + 1:]

    if not args.reprocessar_ok:
        status_execucao = carregar_status_execucao()
        carimbos = [c for c in carimbos if status_execucao.get(normalizar_carimbo(c)) != "ok"]

    if args.limite and args.limite > 0:
        carimbos = carimbos[:args.limite]

    if not carimbos:
        print("Informe ao menos um --carimbo ou --carimbos-arquivo valido.")
        return 2

    if args.preparar_apenas:
        log(f"Preparacao concluida. Carimbos prontos: {len(carimbos)}")
        return 0

    driver = None
    try:
        driver, wait = abrir_driver_logado()
        for carimbo in carimbos:
            registrar_execucao(carimbo, "iniciado")
            abrir_tela_edicao_carimbo(driver, wait)
            carregar_fatura_por_carimbo(driver, wait, carimbo)

            if not args.sem_snapshot:
                salvar_snapshot(driver, carimbo)

            correcoes = correcoes_ocr.get(carimbo)
            if not correcoes:
                warn(f"[CORRECAO] BB_{carimbo}: sem correcoes OCR, pulando")
                registrar_execucao(carimbo, "sem_correcoes")
                continue
            correcoes = _filtrar_correcoes(correcoes, args.somente_retencoes_bandeiras)
            if not correcoes:
                warn(f"[CORRECAO] BB_{carimbo}: sem campos apos filtro, pulando")
                registrar_execucao(carimbo, "sem_campos_filtrados")
                continue

            aplicadas, confirmadas, total = aplicar_correcoes(driver, wait, carimbo, correcoes)
            log(f"[CORRECAO] BB_{carimbo}: {aplicadas}/{total} campos aplicados ({confirmadas} confirmados)")

            if args.salvar and aplicadas > 0:
                salvar_auditar_e_avancar(driver, wait, carimbo)
                registrar_execucao(carimbo, "ok", f"{aplicadas}/{total}")
            else:
                registrar_execucao(carimbo, "preparado_nao_salvo", f"{aplicadas}/{total}")

    except KeyboardInterrupt:
        log("Interrompido pelo usuario.")
    except Exception as exc:
        warn(f"Erro inesperado: {type(exc).__name__}: {exc}")
        return 1
    finally:
        if driver and FECHAR_AO_FINAL:
            try:
                driver.quit()
            except Exception:
                pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
