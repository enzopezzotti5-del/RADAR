#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fluxo de correcao CELESC MT por carimbo.

Este robo nao redigita faturas. Ele:
  1. Faz login no Consen usando o mesmo fluxo do digitador.
  2. Abre a tela de edicao por carimbo.
  3. Busca/carrega a fatura pelo carimbo.
  4. Aplica correcoes explicitamente cadastradas.

Por seguranca, o script nao salva por padrao. Use --salvar somente depois de
validar as correcoes configuradas.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path
from typing import Any

DEFAULT_SAIDA_DIR = "//10.10.250.21/Energia/ARQUIVOS ENZO/CELESC_pipeline_saida/MT/correcoes_por_carimbo"
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
except ModuleNotFoundError:
    import correcao_fluxo_base as fluxo_base  # type: ignore  # noqa: E402
    from correcao_fluxo_base import (  # type: ignore  # noqa: E402
        CorrecaoFluxoConfig,
        formatar_valor_para_campo,
        log,
        warn,
    )

LOGIN_URL = fluxo_base.LOGIN_URL


BASE_URL = LOGIN_URL.rsplit("login.php", 1)[0]
EDIT_HASH = "#bpg/gestao/fatura/editaFaturaCarimbo.php"
EDIT_URL = os.environ.get("CONSEN_EDITA_FATURA_CARIMBO_URL", f"{BASE_URL}index.php{EDIT_HASH}")

SAIDA_DIR = Path(
    os.environ.get(
        "CONSEN_CORRECAO_SAIDA",
        DEFAULT_SAIDA_DIR,
    )
)
FECHAR_AO_FINAL = os.environ.get("CONSEN_CORRECAO_FECHAR", "1").strip().lower() not in {"0", "false", "nao", "não"}
EXECUCAO_CSV = SAIDA_DIR / "correcao_execucao.csv"
CAMPOS_CRITICOS_TELA: tuple[str, ...] = ("txtDemContratadaFPonta", "fatConFPontaIndRegistrado")


# Preencheremos este mapa quando voce orientar os campos.
# Formato:
# CORRECOES_CELESC_MT = {
#     "2005069": {
#         "txt-consumo-fpind-valor-reais": "123,45",
#         "cb-dados-contratuais-fatura-subgrupo": "A4 [2,3kV a 25kV]",
#     },
# }
CORRECOES_CELESC_MT: dict[str, dict[str, Any]] = {}


CAMPO_OCR_PARA_TELA: tuple[tuple[str, str], ...] = (
    ("txtDemContratadaFPonta", "fatDemContratadaFPonta"),
    ("fatDemFPontaIndRegistrada", "fatDemFPontaIndRegistrada"),
    ("fatDemFPontaIndutivo", "fatDemFPontaIndFaturada"),
    ("fatDemFPontaIndValorReais", "fatDemFPontaIndValorReais"),
    ("fatConPontaRegistrado", "fatConPontaRegistrado"),
    ("fatConPonta", "fatConPontaFaturado"),
    ("fatConPontaValorReais", "fatConPontaValorReais"),
    ("fatConFPontaIndRegistrado", "fatConFPontaIndRegistrado"),
    ("fatConFPontaIndutivo", "fatConFPontaIndFaturado"),
    ("fatConFPontaIndValorReais", "fatConFPontaIndValorReais"),
    ("fatICMS", "fatICMS"),
    ("fatPIS", "fatPIS"),
    ("fatCofins", "fatCOFINS"),
    ("fatValorNFiscal", "fatValorNotaFiscal"),
    ("fatDesIcmsAliquota", "fatDesIcmsAliquota"),
    ("fatDescPisAliquota", "fatDescPisAliquota"),
    ("fatDesCofinsAliquota", "fatDescCofinsAliquota"),
    ("fatEscassezHidrica", "fatEscassezHidrica"),
    ("fatEscassezHidricaValorReais", "fatEscassezHidricaValorReais"),
    ("fatBeneficioTarifarioBrutoValorReais", "fatBeneficioTarifarioBrutoValorReais"),
    ("fatBeneficioLiquidoValorReais", "fatBeneficioLiquidoValorReais"),
    ("fatDescontoFio", "fatDescontoFio"),
    ("fatMultasDiversas", "obsValor"),
    ("fatDescPisPercRetImposto", "fatDescPisPercRetImposto"),
    ("fatDescPisValRetImposto", "fatDescPisValRetImposto"),
    ("fatDescCofinsPercRetImposto", "fatDescCofinsPercRetImposto"),
    ("fatDescCofinsValRetImposto", "fatDescCofinsValRetImposto"),
    ("fatDescCsllPercRetImposto", "fatDescCsllPercRetImposto"),
    ("fatDescCsllValRetImposto", "fatDescCsllValRetImposto"),
    ("fatDescConsumoPercRetImposto", "fatDescConsumoPercRetImposto"),
    ("fatDescConsumoValRetImposto", "fatDescConsumoValRetImposto"),
    ("fatDescDemandaPercRetImposto", "fatDescDemandaPercRetImposto"),
    ("fatDescDemandaValRetImposto", "fatDescDemandaValRetImposto"),
)
# Campos zerados explicitamente para CELESC MT ACL Verde (não existem ou são inválidos)
_CAMPOS_REATIVO_ZERO_MT: tuple[str, ...] = (
    # Demanda contratada Ponta: Verde só tem FP
    "txtDemContratadaPonta",
    # Ultrapassagem: não cobrada separadamente em ACL
    "fatDemFPontaIndUltra", "fatDemFPontaIndUltraValorReais",
    # Demanda reativa excedente (ponta e FP)
    "fatDemPontaExcRegistrada", "fatDemPontaExc", "fatDemPontaExcValorReais",
    "fatDemFPontaExcRegistrada", "fatDemFPontaExc", "fatDemFPontaExcValorReais",
    # Reativo excedente consumo (ponta e FP)
    "fatConPontaExcRegistrado", "fatConPontaExc", "fatConPontaExcValorReais",
    "fatConFPontaIndExcRegistrado", "fatConFPontaIndExc", "fatConFPontaIndExcValorReais",
)
ORDEM_CAMPOS_CORRECAO: tuple[str, ...] = (
    ("cb-tarifa", "cb-subgrupo")
    + tuple(campo for campo, _ in CAMPO_OCR_PARA_TELA)
    + _CAMPOS_REATIVO_ZERO_MT
    + ("fatDescontoFioKWh",)
)
CONFIG = CorrecaoFluxoConfig(
    saida_dir=SAIDA_DIR,
    execucao_csv=EXECUCAO_CSV,
    edit_url=EDIT_URL,
    ordem_campos=ORDEM_CAMPOS_CORRECAO,
    fechar_ao_final=FECHAR_AO_FINAL,
)
REGISTROS_OCR: dict[str, dict[str, Any]] = {}


def normalizar_carimbo(carimbo: str) -> str:
    return fluxo_base.normalizar_carimbo(carimbo)


def valor_vazio(valor: Any) -> bool:
    return fluxo_base.valor_vazio(valor)


def formatar_valor_correcao(header: str, valor: Any) -> str:
    return formatar_valor_para_campo(header, valor, "text")


def eh_linha_mt_celesc(row: dict[str, Any]) -> bool:
    if str(row.get("ERRO") or "").strip():
        return False
    subgrupo = str(row.get("cadSubGrupoCod") or "").strip().upper()
    tarifa = str(row.get("cadTarifaCod") or row.get("TARIFA_DETECTADA") or "").strip().upper()
    tem_mt = subgrupo.startswith("A") or "VERDE" in tarifa or "AZUL" in tarifa
    tem_celesc = not valor_vazio(row.get("Instalacao")) and not valor_vazio(row.get("fatCarimbo"))
    return tem_mt and tem_celesc


def correcoes_da_linha_ocr(row: dict[str, Any]) -> dict[str, str]:
    correcoes: dict[str, str] = {}
    subgrupo = str(row.get("cadSubGrupoCod") or row.get("SUBGRUPO_DETECTADO") or "").strip().upper()
    tarifa = str(row.get("cadTarifaCod") or row.get("TARIFA_DETECTADA") or "").strip().upper()
    if "VERDE" in tarifa:
        correcoes["cb-tarifa"] = "HS - Verde"
    elif "AZUL" in tarifa:
        correcoes["cb-tarifa"] = "HS - Azul"
    if subgrupo.startswith("A4") or subgrupo == "10":
        correcoes["cb-subgrupo"] = "A4 [2,3kV a 25kV]"
    elif subgrupo.startswith("A3A") or subgrupo == "12":
        correcoes["cb-subgrupo"] = "A3a [30kV a 44kV]"

    for campo_tela, header in CAMPO_OCR_PARA_TELA:
        if header not in row:
            continue
        valor = row.get(header)
        if valor_vazio(valor):
            continue
        correcoes[campo_tela] = formatar_valor_correcao(header, valor)
    if eh_mercado_cativo_mt(row):
        correcoes["fatDescontoFio"] = "0,00"
        correcoes["fatDescontoFioKWh"] = "0,00"
    else:
        correcoes["fatDescontoFio"] = "50,00"
        correcoes["fatDescontoFioKWh"] = "43,25"
    # Reativo Ponta e demanda reativa: sempre zero para CELESC MT
    for campo_zero in _CAMPOS_REATIVO_ZERO_MT:
        correcoes[campo_zero] = "0,00"
    return correcoes


def eh_mercado_cativo_mt(row: dict[str, Any]) -> bool:
    campos_livre = (
        "fatBeneficioTarifarioBrutoValorReais",
        "fatBeneficioLiquidoValorReais",
        "fatMultasDiversas",
        "obsValor",
        "fatDescontoFio",
    )
    return all(valor_vazio(row.get(campo)) for campo in campos_livre)


def carregar_correcoes_de_pasta_pdfs(pasta: Path, carimbos_filtro: set[str]) -> dict[str, dict[str, str]]:
    from ocr.ocr_celesc import extrair_campos

    pdfs_dict = {str(p.resolve()).lower(): p for p in list(pasta.rglob("*.pdf")) + list(pasta.rglob("*.PDF"))}
    pdfs = sorted(pdfs_dict.values())
    if carimbos_filtro:
        def _norm_seguro(stem):
            try:
                return normalizar_carimbo(stem)
            except ValueError:
                return ""
        pdfs = [p for p in pdfs if _norm_seguro(p.stem) in carimbos_filtro]

    correcoes: dict[str, dict[str, str]] = {}
    linhas_log: list[dict[str, Any]] = []
    for idx, pdf in enumerate(pdfs, start=1):
        log(f"[OCR CORRECAO] {idx}/{len(pdfs)} {pdf.name}")
        try:
            row = extrair_campos(pdf)
        except Exception as exc:
            warn(f"[OCR CORRECAO] {pdf.name}: erro {type(exc).__name__}: {exc}")
            continue

        carimbo = normalizar_carimbo(str(row.get("fatCarimbo") or pdf.stem))
        if not eh_linha_mt_celesc(row):
            linhas_log.append({
                "carimbo": carimbo,
                "arquivo": str(pdf),
                "status": "pulado_nao_mt_celesc_ou_erro",
                "erro": row.get("ERRO", ""),
                "subgrupo": row.get("cadSubGrupoCod", ""),
                "tarifa": row.get("cadTarifaCod", ""),
                "campos": 0,
            })
            continue

        mapa = correcoes_da_linha_ocr(row)
        if mapa:
            correcoes[carimbo] = mapa
            REGISTROS_OCR[carimbo] = {
                "linha_excel": idx,
                "instalacao": row.get("Instalacao", ""),
                "dataReferenciaEsperada": row.get("fatDataReferencia", ""),
                "fatCarimbo": f"BB_{carimbo}",
            }
        linhas_log.append({
            "carimbo": carimbo,
            "arquivo": str(pdf),
            "status": "ok" if mapa else "sem_campos",
            "erro": row.get("ERRO", ""),
            "subgrupo": row.get("cadSubGrupoCod", ""),
            "tarifa": row.get("cadTarifaCod", ""),
            "campos": len(mapa),
        })

    salvar_log_preparacao(linhas_log)
    return correcoes


def salvar_log_preparacao(linhas: list[dict[str, Any]]) -> None:
    SAIDA_DIR.mkdir(parents=True, exist_ok=True)
    path = SAIDA_DIR / "preparacao_correcao_celesc_mt.csv"
    campos = ["carimbo", "arquivo", "status", "erro", "subgrupo", "tarifa", "campos"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=campos, delimiter=";")
        writer.writeheader()
        writer.writerows(linhas)
    log(f"Log de preparacao salvo: {path}")

    carimbos_ok = sorted({str(l.get("carimbo") or "").strip() for l in linhas if l.get("status") == "ok"})
    txt_path = SAIDA_DIR / "carimbos_preparados_celesc_mt.txt"
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


def coletar_campos_visiveis(driver) -> list[dict[str, str]]:
    return fluxo_base.coletar_campos_visiveis(driver)


def salvar_snapshot(driver, carimbo: str) -> None:
    fluxo_base.salvar_snapshot(driver, CONFIG.saida_dir, carimbo)


def aplicar_correcoes(driver, wait, carimbo: str, correcoes: dict[str, Any]) -> tuple[int, int, int]:
    return fluxo_base.aplicar_correcoes(driver, wait, carimbo, correcoes, CONFIG.ordem_campos)


def localizar_input_exato(driver, wait, campo: str):
    return fluxo_base.localizar_input_exato(driver, wait, campo)


def salvar_auditar_e_avancar(driver, wait, carimbo: str) -> None:
    fluxo_base.salvar_auditar_e_avancar(driver, wait, carimbo)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Correcao CELESC MT por carimbo no Consen")
    p.add_argument("--carimbo", action="append", default=[], help="Carimbo a carregar. Ex: --carimbo BB_2005069")
    p.add_argument("--carimbos-arquivo", type=str, default="", help="TXT com um carimbo por linha")
    p.add_argument("--pasta-pdfs", type=str, default="", help="Pasta com PDFs CELESC MT para extrair correcoes via OCR")
    p.add_argument("--preparar-apenas", action="store_true", help="So valida o OCR e monta a lista de correcoes, sem abrir o Consen")
    p.add_argument("--retomar-apos", type=str, default="", help="Retoma o lote apos este carimbo. Ex: --retomar-apos BB_2003466")
    p.add_argument("--reprocessar-ok", action="store_true", help="Nao pula carimbos ja concluidos com status ok no log de execucao")
    p.add_argument("--salvar", action="store_true", help="Salva a fatura apos aplicar correcoes cadastradas")
    p.add_argument("--sem-snapshot", action="store_true", help="Nao salva HTML/JSON da tela carregada")
    p.add_argument("--limite", type=int, default=0, help="Limita a quantidade de carimbos processados")
    return p.parse_args()


def carregar_lista_carimbos(args: argparse.Namespace) -> list[str]:
    return fluxo_base.carregar_lista_carimbos(args)


def main() -> int:
    args = parse_args()
    carimbos_filtro = set()
    for item in list(args.carimbo or []):
        carimbos_filtro.add(normalizar_carimbo(item))
    if args.carimbos_arquivo:
        path = Path(args.carimbos_arquivo)
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                carimbos_filtro.add(normalizar_carimbo(line))

    correcoes_ocr: dict[str, dict[str, Any]] = {}
    if args.pasta_pdfs:
        pasta_pdfs = Path(args.pasta_pdfs)
        if not pasta_pdfs.exists():
            print(f"Pasta de PDFs nao encontrada: {pasta_pdfs}")
            return 2
        correcoes_ocr = carregar_correcoes_de_pasta_pdfs(pasta_pdfs, carimbos_filtro)

    carimbos = carregar_lista_carimbos(args)
    if correcoes_ocr:
        carimbos.extend(c for c in sorted(correcoes_ocr) if c not in set(carimbos))
    if args.retomar_apos:
        marcador = normalizar_carimbo(args.retomar_apos)
        if marcador in carimbos:
            carimbos = carimbos[carimbos.index(marcador) + 1 :]
    if not args.reprocessar_ok:
        status_execucao = carregar_status_execucao()
        carimbos = [c for c in carimbos if status_execucao.get(normalizar_carimbo(c)) != "ok"]
    if args.limite and args.limite > 0:
        carimbos = carimbos[: args.limite]
    if not carimbos:
        print("Informe ao menos um --carimbo/--carimbos-arquivo ou use --pasta-pdfs com PDFs CELESC MT.")
        return 2

    if args.preparar_apenas:
        log(f"Preparacao concluida. Carimbos prontos para correcao: {len(carimbos)}")
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

            carimbo_norm = normalizar_carimbo(carimbo)
            correcoes = dict(CORRECOES_CELESC_MT.get(carimbo_norm, {}))
            correcoes.update(correcoes_ocr.get(carimbo_norm, {}))
            qtd, confirmadas, total = aplicar_correcoes(driver, wait, carimbo, correcoes)

            if args.salvar:
                if total <= 0:
                    warn(f"--salvar foi informado, mas nao ha correcoes para BB_{carimbo}. Salvamento pulado.")
                    registrar_execucao(carimbo, "sem_correcoes")
                elif confirmadas < total:
                    warn(
                        f"BB_{carimbo}: correcoes incompletas ({confirmadas}/{total}). "
                        "Salvamento bloqueado para evitar ajuste parcial."
                    )
                    registrar_execucao(carimbo, "bloqueado_incompleto", f"{confirmadas}/{total}")
                else:
                    salvar_auditar_e_avancar(driver, wait, carimbo)
                    registrar_execucao(carimbo, "ok", f"{confirmadas}/{total}")
                    log(f"BB_{carimbo}: salvo, auditado e avancado.")
            else:
                registrar_execucao(carimbo, "validado_sem_salvar", f"{confirmadas}/{total}")
                log(f"BB_{carimbo}: modo seguro, sem salvar.")
        return 0
    finally:
        if driver and CONFIG.fechar_ao_final:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
