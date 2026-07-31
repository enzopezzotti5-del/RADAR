#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Correcao Equatorial GO MT por carimbo.

Este fluxo:
1. Localiza os PDFs ja digitados pela raiz de carimbos digitados.
2. Reprocessa cada PDF com o OCR Equatorial GO MT atualizado.
3. Corrige no Consen os campos que mais derrubam a auditoria:
   consumos e demandas em ponta/fora ponta, impostos e retencoes,
   escassez hidrica, valor da fatura/nota fiscal e beneficios.

Por seguranca, nao salva por padrao. Use --salvar quando quiser efetivar.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path
from typing import Any

from selenium.webdriver.common.by import By

DEFAULT_SAIDA_DIR = "//10.10.250.21/Energia/ARQUIVOS ENZO/EQUATORIAL_GO_producao_saida/correcoes_por_carimbo"
DEFAULT_PDFS_ROOT = "//10.10.250.21/Energia/CONTROLE BB/DIGITADOS/CARIMBOS DIGITADOS"
os.environ.setdefault("CONSEN_PIPELINE_SAIDA", DEFAULT_SAIDA_DIR)

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
    from ocr import ocr_equatorial_go as ocr_eq  # noqa: E402
except ModuleNotFoundError:
    import correcao_fluxo_base as fluxo_base  # type: ignore  # noqa: E402
    from correcao_fluxo_base import (  # type: ignore  # noqa: E402
        CorrecaoFluxoConfig,
        formatar_valor_para_campo,
        log,
        warn,
    )
    import ocr_equatorial_go as ocr_eq  # type: ignore  # noqa: E402

LOGIN_URL = fluxo_base.LOGIN_URL
BASE_URL = LOGIN_URL.rsplit("login.php", 1)[0]
EDIT_HASH = "#bpg/gestao/fatura/editaFaturaCarimbo.php"
EDIT_URL = os.environ.get("CONSEN_EDITA_FATURA_CARIMBO_URL", f"{BASE_URL}index.php{EDIT_HASH}")

SAIDA_DIR = Path(os.environ.get("CONSEN_CORRECAO_SAIDA", DEFAULT_SAIDA_DIR))
PDFS_ROOT = Path(os.environ.get("CONSEN_CORRECAO_EQUATORIAL_GO_MT_ROOT", DEFAULT_PDFS_ROOT))
FECHAR_AO_FINAL = os.environ.get("CONSEN_CORRECAO_FECHAR", "1").strip().lower() not in {"0", "false", "nao", "não"}
EXECUCAO_CSV = SAIDA_DIR / "correcao_execucao.csv"

CAMPOS_CRITICOS_TELA: tuple[str, ...] = (
    "btnSalvar",
    "instalacao",
)

# (campo_logico, header_no_xlsx_ocr)
CAMPO_OCR_PARA_LOGICO: tuple[tuple[str, str], ...] = (
    ("cadTarifaCod",                         "cadTarifaCod"),
    ("cadSubGrupoCod",                       "cadSubGrupoCod"),
    ("fatDemContratadaFPonta",               "fatDemContratadaFPonta"),
    ("fatConFPontaIndRegistrado",            "fatConFPontaIndRegistrado"),
    ("fatConFPontaIndFaturado",              "fatConFPontaIndFaturado"),
    ("fatConFPontaIndValorReais",            "fatConFPontaIndValorReais"),
    ("fatConPontaRegistrado",                "fatConPontaRegistrado"),
    ("fatConPontaFaturado",                  "fatConPontaFaturado"),
    ("fatConPontaValorReais",                "fatConPontaValorReais"),
    ("fatConFPontaIndExcRegistrado",         "fatConFPontaIndExcRegistrado"),
    ("fatConFPontaIndExcFaturado",           "fatConFPontaIndExcFaturado"),
    ("fatConFPontaIndExcValorReais",         "fatConFPontaIndExcValorReais"),
    ("fatConPontaExcRegistrado",             "fatConPontaExcRegistrado"),
    ("fatConPontaExcFaturado",               "fatConPontaExcFaturado"),
    ("fatConPontaExcValorReais",             "fatConPontaExcValorReais"),
    ("fatDemFPontaIndRegistrada",            "fatDemFPontaIndRegistrada"),
    ("fatDemFPontaIndFaturada",              "fatDemFPontaIndFaturada"),
    ("fatDemFPontaIndValorReais",            "fatDemFPontaIndValorReais"),
    ("fatDemPontaRegistrada",                "fatDemPontaRegistrada"),
    ("fatDemPontaFaturada",                  "fatDemPontaFaturada"),
    ("fatDemPontaValorReais",                "fatDemPontaValorReais"),
    ("fatICMS",                              "fatICMS"),
    ("fatDesIcmsAliquota",                   "fatDesIcmsAliquota"),
    ("fatPIS",                               "fatPIS"),
    ("fatDescPisAliquota",                   "fatDescPisAliquota"),
    ("fatCOFINS",                            "fatCOFINS"),
    ("fatDesCofinsAliquota",                 "fatDesCofinsAliquota"),
    ("fatDescPisPercRetImposto",             "fatDescPisPercRetImposto"),
    ("fatDescPisValRetImposto",              "fatDescPisValRetImposto"),
    ("fatDescCofinsPercRetImposto",          "fatDescCofinsPercRetImposto"),
    ("fatDescCofinsValRetImposto",           "fatDescCofinsValRetImposto"),
    ("fatDescCsllPercRetImposto",            "fatDescCsllPercRetImposto"),
    ("fatDescCsllValRetImposto",             "fatDescCsllValRetImposto"),
    ("fatDescIrpjValRetImposto",             "fatDescIrpjValRetImposto"),
    ("fatDescontoFio",                       "fatDescontoFio"),
    ("fatDescontoFioKWh",                    "fatDescontoFioKWh"),
    ("fatEscassezHidrica",                   "fatEscassezHidrica"),
    ("fatEscassezHidricaValorReais",         "fatEscassezHidricaValorReais"),
    ("fatMultas",                            "fatMultas"),
    ("fatValorFatura",                       "fatValorFatura"),
    ("fatValorNotaFiscal",                   "fatValorNotaFiscal"),
    ("fatBeneficioTarifarioBrutoValorReais", "fatBeneficioTarifarioBrutoValorReais"),
    ("fatBeneficioLiquidoValorReais",        "fatBeneficioLiquidoValorReais"),
)

# IDs HTML reais confirmados pelo mapeamento CSV da tela Equatorial GO MT.
CAMPO_TELA_ALTERNATIVOS: dict[str, tuple[str, ...]] = {
    "cadTarifaCod":                         ("cb-tarifa",),
    "cadSubGrupoCod":                       ("cb-subgrupo",),
    "fatDemContratadaFPonta":               ("txtDemContratadaFPonta", "fatDemContratadaFPonta"),
    "fatConFPontaIndRegistrado":            ("txt-consumo-registrada-fpind",    "fatConFPontaIndRegistrado"),
    "fatConFPontaIndFaturado":              ("txt-consumo-faturada-fpind",       "fatConFPontaIndutivo", "fatConFPontaIndFaturado"),
    "fatConFPontaIndValorReais":            ("txt-consumo-fpind-valor-reais",    "fatConFPontaIndValorReais"),
    "fatConPontaRegistrado":                ("txt-consumo-registrada-pta",       "fatConPontaRegistrado"),
    "fatConPontaFaturado":                  ("txt-consumo-faturada-pta",         "fatConPonta", "fatConPontaFaturado"),
    "fatConPontaValorReais":                ("txt-consumo-pta-valor-reais",      "fatConPontaValorReais"),
    "fatConFPontaIndExcRegistrado":         ("fatConFPontaIndExcRegistrado",),
    "fatConFPontaIndExcFaturado":           ("fatConFPontaIndExc", "fatConFPontaIndExcFaturado"),
    "fatConFPontaIndExcValorReais":         ("fatConFPontaIndExcValorReais",),
    "fatConPontaExcRegistrado":             ("fatConPontaExcRegistrado",),
    "fatConPontaExcFaturado":               ("fatConPontaExc", "fatConPontaExcFaturado"),
    "fatConPontaExcValorReais":             ("fatConPontaExcValorReais",),
    "fatDemFPontaIndRegistrada":            ("txt-demandas-registrada-fpind",    "fatDemFPontaIndRegistrada"),
    "fatDemFPontaIndFaturada":              ("txt-demandas-faturada-fpind",      "fatDemFPontaIndutivo", "fatDemFPontaIndFaturada"),
    "fatDemFPontaIndValorReais":            ("txt-demandas-fpind-valor-reais",   "fatDemFPontaIndValorReais"),
    "fatDemPontaRegistrada":                ("txt-demandas-registrada-pta",      "fatDemPontaRegistrada"),
    "fatDemPontaFaturada":                  ("txt-demandas-faturada-pta",        "fatDemPonta", "fatDemPontaFaturada"),
    "fatDemPontaValorReais":                ("txt-demandas-pta-valor-reais",     "fatDemPontaValorReais"),
    "fatICMS":                              ("camposFinanICMS",                   "fatICMS"),
    "fatDesIcmsAliquota":                   ("fatDesIcmsAliquota",),
    "fatPIS":                               ("txt-dados-financeiros-pis-pasep",  "fatPIS"),
    "fatDescPisAliquota":                   ("fatDescPisAliquota",),
    "fatCOFINS":                            ("txt-dados-financeiros-cofins",     "fatCofins", "fatCOFINS"),
    "fatDesCofinsAliquota":                 ("fatDesCofinsAliquota",),
    "fatDescPisPercRetImposto":             ("fatDescPisPercRetImposto",),
    "fatDescPisValRetImposto":              ("fatDescPisValRetImposto",),
    "fatDescCofinsPercRetImposto":          ("fatDescCofinsPercRetImposto",),
    "fatDescCofinsValRetImposto":           ("fatDescCofinsValRetImposto",),
    "fatDescCsllPercRetImposto":            ("fatDescCsllPercRetImposto",),
    "fatDescCsllValRetImposto":             ("fatDescCsllValRetImposto",),
    "fatDescIrpjValRetImposto":             ("fatDescIrpjValRetImposto",),
    "fatDescontoFio":                       ("fatDescontoFio",),
    "fatDescontoFioKWh":                    ("fatDescontoFioKWh",),
    "fatEscassezHidrica":                   ("fatEscassezHidrica",),
    "fatEscassezHidricaValorReais":         ("fatEscassezHidricaValorReais",),
    "fatMultas":                            ("fatMultas",),
    "fatValorFatura":                       ("txt-dados-financeiros-valor-fatura-a-pagar", "fatValorFatura"),
    "fatValorNotaFiscal":                   ("txt-dados-financeiros-valor-nota-fiscal", "fatValorNFiscal", "fatValorNotaFiscal"),
    "fatBeneficioTarifarioBrutoValorReais": ("fatBeneficioTarifarioBrutoValorReais",),
    "fatBeneficioLiquidoValorReais":        ("fatBeneficioLiquidoValorReais",),
}

ORDEM_CAMPOS_CORRECAO: tuple[str, ...] = tuple(campo for campo, _ in CAMPO_OCR_PARA_LOGICO)
CONFIG = CorrecaoFluxoConfig(
    saida_dir=SAIDA_DIR,
    execucao_csv=EXECUCAO_CSV,
    edit_url=EDIT_URL,
    ordem_campos=ORDEM_CAMPOS_CORRECAO,
    fechar_ao_final=FECHAR_AO_FINAL,
)


def normalizar_carimbo(carimbo: str) -> str:
    return fluxo_base.normalizar_carimbo(carimbo)


def valor_vazio(valor: Any) -> bool:
    return fluxo_base.valor_vazio(valor)


def formatar_valor_correcao(header: str, valor: Any) -> str:
    return formatar_valor_para_campo(header, valor, "text")


def _score_data_pasta(pdf: Path) -> tuple[int, int, int]:
    for parte in (pdf.parent.name, pdf.parent.parent.name):
        m = re.fullmatch(r"(\d{2})(\d{2})(\d{4})", str(parte).strip())
        if m:
            return int(m.group(3)), int(m.group(2)), int(m.group(1))
    return (0, 0, 0)


_VALORES_FIXOS: dict[str, str] = {
    "fatDescontoFio":    "50",
    "fatDescontoFioKWh": "45,48",
    "cadTarifaCod":      "HS - Verde",
    "cadSubGrupoCod":    "A4",
}

def _correcoes_da_linha_ocr(row: dict[str, Any]) -> dict[str, Any]:
    correcoes: dict[str, Any] = {}
    for campo_logico, header in CAMPO_OCR_PARA_LOGICO:
        if campo_logico in _VALORES_FIXOS:
            correcoes[campo_logico] = _VALORES_FIXOS[campo_logico]
            continue
        valor = row.get(header)
        if valor_vazio(valor):
            continue
        try:
            if float(valor) == 0.0:
                continue
        except (TypeError, ValueError):
            pass
        correcoes[campo_logico] = formatar_valor_correcao(header, valor)
    return correcoes


def localizar_pdfs_por_carimbo(raiz: Path, carimbos_filtro: set[str]) -> dict[str, Path]:
    encontrados: dict[str, Path] = {}
    if not carimbos_filtro:
        return encontrados

    import os as _os
    for atual, _, arquivos in _os.walk(raiz):
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
            linhas_log.append({"carimbo": carimbo, "arquivo": "", "status": "nao_encontrado", "campos": 0, "erro": ""})
            continue

        try:
            row = ocr_eq.processar_pdf(str(pdf), "mt")
        except Exception as exc:
            linhas_log.append({"carimbo": carimbo, "arquivo": str(pdf), "status": f"erro_ocr:{type(exc).__name__}", "campos": 0, "erro": str(exc)})
            warn(f"[OCR CORRECAO] {pdf.name}: erro {type(exc).__name__}: {exc}")
            continue

        erro = str(row.get("ERRO") or "").strip()
        if erro:
            linhas_log.append({"carimbo": carimbo, "arquivo": str(pdf), "status": "pulado_linha_com_erro", "campos": 0, "erro": erro})
            continue

        payload = _correcoes_da_linha_ocr(row)
        campos = len(payload)
        if payload:
            correcoes[carimbo] = payload

        linhas_log.append({"carimbo": carimbo, "arquivo": str(pdf), "status": "ok" if payload else "sem_campos", "campos": campos, "erro": ""})

    salvar_log_preparacao(linhas_log)
    return correcoes


def salvar_log_preparacao(linhas: list[dict[str, Any]]) -> None:
    SAIDA_DIR.mkdir(parents=True, exist_ok=True)
    path = SAIDA_DIR / "preparacao_correcao_equatorial_go_mt.csv"
    campos = ["carimbo", "arquivo", "status", "campos", "erro"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=campos, delimiter=";")
        writer.writeheader()
        writer.writerows(linhas)
    log(f"Log de preparacao salvo: {path}")

    carimbos_ok = sorted({str(l.get("carimbo") or "").strip() for l in linhas if l.get("status") == "ok"})
    txt_path = SAIDA_DIR / "carimbos_preparados_equatorial_go_mt.txt"
    txt_path.write_text("\n".join(f"BB_{c}" for c in carimbos_ok if c), encoding="utf-8")
    log(f"Lista de carimbos preparados salva: {txt_path}")


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
            return (1, 1) if ok else (0, 0)
        except Exception:
            continue
    warn(f"[CORRECAO] Campo {campo_logico} nao encontrado na tela ({', '.join(candidatos)}) — ignorado")
    return 0, 1  # campo ausente na tela de edicao nao bloqueia o save


def aplicar_correcoes(driver, wait, carimbo: str, correcoes: dict[str, Any]) -> tuple[int, int, int]:
    if not correcoes:
        log(f"Sem correcoes cadastradas para BB_{normalizar_carimbo(carimbo)}. Nada sera alterado.")
        return 0, 0, 0

    aplicadas = 0
    confirmadas = 0
    ordem = [campo for campo in CONFIG.ordem_campos if campo in correcoes]
    ordem.extend(campo for campo in correcoes if campo not in ordem)

    for campo in ordem:
        qtd, ok = _aplicar_campo_com_aliases(driver, wait, campo, correcoes[campo])
        aplicadas += qtd
        confirmadas += ok

    return aplicadas, confirmadas, len(ordem)


def salvar_auditar_e_avancar(driver, wait, carimbo: str) -> None:
    fluxo_base.salvar_auditar_e_avancar(driver, wait, carimbo)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Correcao Equatorial GO MT por carimbo no Consen")
    p.add_argument("--carimbo", action="append", default=[], help="Carimbo a corrigir. Ex: --carimbo BB_2008315")
    p.add_argument("--carimbos-arquivo", type=str, default="", help="TXT com um carimbo por linha")
    p.add_argument("--raiz-pdfs", type=str, default=str(PDFS_ROOT), help="Raiz dos PDFs ja digitados")
    p.add_argument("--preparar-apenas", action="store_true", help="So valida o OCR e monta a lista de correcoes")
    p.add_argument("--retomar-apos", type=str, default="", help="Retoma o lote apos este carimbo")
    p.add_argument("--reprocessar-ok", action="store_true", help="Nao pula carimbos ja concluidos com status ok")
    p.add_argument("--salvar", action="store_true", help="Salva a fatura apos aplicar correcoes")
    p.add_argument("--sem-snapshot", action="store_true", help="Nao salva HTML/JSON da tela carregada")
    p.add_argument("--limite", type=int, default=0, help="Limita a quantidade de carimbos processados")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    carimbos_filtro: set[str] = set()
    for item in (args.carimbo or []):
        carimbos_filtro.add(normalizar_carimbo(item))
    if args.carimbos_arquivo:
        path = Path(args.carimbos_arquivo)
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                carimbos_filtro.add(normalizar_carimbo(line))

    if not carimbos_filtro:
        print("Informe ao menos um --carimbo ou --carimbos-arquivo para correcao.")
        return 2

    raiz = Path(args.raiz_pdfs)
    if not raiz.exists():
        print(f"Raiz de PDFs nao encontrada: {raiz}")
        return 2

    correcoes_ocr = carregar_correcoes_de_raiz_pdfs(raiz, carimbos_filtro)

    carimbos = sorted(carimbos_filtro)
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
        print("Nenhum carimbo para processar apos filtros.")
        return 0

    if args.preparar_apenas:
        log(f"Preparacao concluida. Carimbos com correcoes: {len(correcoes_ocr)}/{len(carimbos)}")
        for c, payload in sorted(correcoes_ocr.items()):
            campos = [k for k in payload if not k.startswith("__")]
            log(f"  BB_{c}: {len(campos)} campos -> {', '.join(campos)}")
        return 0

    import time as _time

    driver = None
    try:
        driver, wait = abrir_driver_logado()
        _time.sleep(3.0)  # aguarda SPA estabilizar apos login

        for carimbo in carimbos:
            registrar_execucao(carimbo, "iniciado")
            abrir_tela_edicao_carimbo(driver, wait)
            _time.sleep(1.5)  # aguarda tela de edicao carregar completamente
            carregar_fatura_por_carimbo(driver, wait, carimbo)
            _time.sleep(1.0)  # aguarda campos da fatura renderizarem

            if not args.sem_snapshot:
                salvar_snapshot(driver, carimbo)

            carimbo_norm = normalizar_carimbo(carimbo)
            correcoes = dict(correcoes_ocr.get(carimbo_norm, {}))
            qtd, confirmadas, total = aplicar_correcoes(driver, wait, carimbo, correcoes)

            if args.salvar:
                if total <= 0:
                    warn(f"--salvar ativo, mas sem correcoes para BB_{carimbo_norm}. Salvamento pulado.")
                    registrar_execucao(carimbo, "sem_correcoes")
                elif confirmadas < total:
                    warn(
                        f"BB_{carimbo_norm}: correcoes incompletas ({confirmadas}/{total}). "
                        "Salvamento bloqueado para evitar ajuste parcial."
                    )
                    registrar_execucao(carimbo, "bloqueado_incompleto", f"{confirmadas}/{total}")
                else:
                    _time.sleep(1.0)  # aguarda antes de salvar
                    salvar_auditar_e_avancar(driver, wait, carimbo)
                    registrar_execucao(carimbo, "ok", f"{confirmadas}/{total}")
                    log(f"BB_{carimbo_norm}: salvo, auditado e avancado.")
            else:
                registrar_execucao(carimbo, "validado_sem_salvar", f"{confirmadas}/{total}")
                log(f"BB_{carimbo_norm}: modo seguro, sem salvar. ({confirmadas}/{total} campos prontos)")

            _time.sleep(1.0)  # pausa entre carimbos
        return 0
    finally:
        if driver and CONFIG.fechar_ao_final:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
