#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Correcao COPEL BT por carimbo.

Fluxo:
1. Localiza os PDFs dos carimbos informados.
2. Reprocessa com o OCR COPEL BT atualizado.
3. Sobrepoe no Consen todos os campos extraidos pelo novo OCR:
   bandeira tarifaria, observacoes (obsCod/obsValor), retencoes PIS=0,65%
   e COFINS=3,00%, base de calculo, ICMS/PIS/COFINS, consumo.

Por seguranca, nao salva por padrao. Use --salvar para efetivar.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path
from typing import Any

from selenium.common.exceptions import InvalidSessionIdException, WebDriverException

DEFAULT_SAIDA_DIR = "//10.10.250.21/Energia/ARQUIVOS ENZO/COPEL_pipeline_saida/BT/correcoes_por_carimbo"
NETWORK_PDFS_ROOT = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD COPEL")
os.environ.setdefault("CONSEN_PIPELINE_SAIDA", DEFAULT_SAIDA_DIR)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _venv_check  # noqa: E402,F401

try:
    from digitacao_consen import correcao_fluxo_base as fluxo_base
    from digitacao_consen.correcao_fluxo_base import (
        CorrecaoFluxoConfig,
        formatar_valor_para_campo,
        log,
        warn,
    )
    from ocr.ocr_copel_bt import _build_record
except ModuleNotFoundError:
    import correcao_fluxo_base as fluxo_base  # type: ignore
    from correcao_fluxo_base import (  # type: ignore
        CorrecaoFluxoConfig,
        formatar_valor_para_campo,
        log,
        warn,
    )
    from ocr_copel_bt import _build_record  # type: ignore

LOGIN_URL = fluxo_base.LOGIN_URL
BASE_URL = LOGIN_URL.rsplit("login.php", 1)[0]
EDIT_HASH = "#bpg/gestao/fatura/editaFaturaCarimbo.php"
EDIT_URL = os.environ.get("CONSEN_EDITA_FATURA_CARIMBO_URL", f"{BASE_URL}index.php{EDIT_HASH}")

SAIDA_DIR = Path(os.environ.get("CONSEN_CORRECAO_SAIDA", DEFAULT_SAIDA_DIR))
PDFS_ROOT = Path(os.environ.get("CONSEN_CORRECAO_COPEL_BT_ROOT", str(NETWORK_PDFS_ROOT)))
FECHAR_AO_FINAL = os.environ.get("CONSEN_CORRECAO_FECHAR", "1").strip().lower() not in {"0", "false", "nao", "não"}
EXECUCAO_CSV = SAIDA_DIR / "correcao_execucao.csv"

CAMPOS_CRITICOS_TELA: tuple[str, ...] = (
    "fatConFPontaIndRegistrado",
    "fatValorNFiscal",
)

# (HTML field id, XLSX column header)
CAMPO_OCR_PARA_TELA: tuple[tuple[str, str], ...] = (
    ("fatConFPontaIndRegistrado",   "fatConFPontaIndRegistrado"),
    ("fatConFPontaIndutivo",        "fatConFPontaIndFaturado"),
    ("fatConFPontaIndValorReais",   "fatConFPontaIndValorReais"),
    ("fatICMS",                     "fatICMS"),
    ("fatPIS",                      "fatPIS"),
    ("fatCofins",                   "fatCOFINS"),
    ("fatDesIcmsAliquota",          "fatDesIcmsAliquota"),
    ("fatDescPisAliquota",          "fatDescPisAliquota"),
    ("fatDesCofinsAliquota",        "fatDesCofinsAliquota"),
    ("fatValorNFiscal",             "fatValorNotaFiscal"),
    ("fatValBandeira",              "fatValBandeira"),
    ("fatValBandeira2",             "fatValBandeira2"),
    ("obsCod_1",                    "obsCod_1"),
    ("obsValor_1",                  "obsValor_1"),
    ("obsCod_2",                    "obsCod_2"),
    ("obsValor_2",                  "obsValor_2"),
    ("obsCod_3",                    "obsCod_3"),
    ("obsValor_3",                  "obsValor_3"),
    ("obsCod_4",                    "obsCod_4"),
    ("obsValor_4",                  "obsValor_4"),
    ("obsCod_5",                    "obsCod_5"),
    ("obsValor_5",                  "obsValor_5"),
    ("fatDescPisPercRetImposto",    "fatDescPisPercRetImposto"),
    ("fatDescPisValRetImposto",     "fatDescPisValRetImposto"),
    ("fatDescCofinsPercRetImposto", "fatDescCofinsPercRetImposto"),
    ("fatDescCofinsValRetImposto",  "fatDescCofinsValRetImposto"),
    ("fatIlumPublica",              "fatIlumPublica"),
    ("fatMultasDiversas",           "fatMultasDiversas"),
)

ORDEM_CAMPOS_CORRECAO: tuple[str, ...] = (
    "fatConFPontaIndRegistrado",
    "fatConFPontaIndutivo",
    "fatConFPontaIndValorReais",
    # Excedente reativo FP Ind — deve ser zerado em BT GD (sem reativa excedente)
    "fatConFPontaIndExcRegistrado",
    "fatConFPontaIndExc",
    "fatConFPontaIndExcValorReais",
    "fatICMS",
    "fatPIS",
    "fatCofins",
    "fatDesIcmsAliquota",
    "fatDescPisAliquota",
    "fatDesCofinsAliquota",
    "fatValorNFiscal",
    "fatValBandeira",
    "fatValBandeira2",
    "obsCod_1", "obsValor_1",
    "obsCod_2", "obsValor_2",
    "obsCod_3", "obsValor_3",
    "obsCod_4", "obsValor_4",
    "obsCod_5", "obsValor_5",
    "fatDescPisPercRetImposto",
    "fatDescPisValRetImposto",
    "fatDescCofinsPercRetImposto",
    "fatDescCofinsValRetImposto",
    "fatIlumPublica",
    "fatMultasDiversas",
)

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


def eh_linha_copel_bt(row: dict[str, Any]) -> bool:
    if str(row.get("ERRO") or "").strip():
        return False
    conc = str(row.get("concCod") or "").strip().upper()
    return bool(row.get("Instalacao") and row.get("fatCarimbo") and conc == "COPEL")


def correcoes_da_linha_ocr(row: dict[str, Any]) -> dict[str, str]:
    correcoes: dict[str, str] = {}
    for campo_tela, header in CAMPO_OCR_PARA_TELA:
        valor = row.get(header)
        if valor_vazio(valor):
            continue
        correcoes[campo_tela] = formatar_valor_correcao(header, valor)
    return correcoes


def localizar_pdfs_por_carimbo(raiz: Path, carimbos_filtro: set[str]) -> dict[str, Path]:
    encontrados: dict[str, Path] = {}
    pendentes = set(carimbos_filtro)
    if not pendentes:
        return encontrados
    for atual, _, arquivos in os.walk(raiz):
        if not pendentes:
            break
        pasta = Path(atual)
        for nome in arquivos:
            if not nome.lower().endswith(".pdf"):
                continue
            try:
                carimbo = normalizar_carimbo(Path(nome).stem)
            except Exception:
                continue
            if carimbo in pendentes and carimbo not in encontrados:
                encontrados[carimbo] = pasta / nome
                pendentes.remove(carimbo)
                log(f"[LOCALIZADO] BB_{carimbo} -> {encontrados[carimbo]}")
                if not pendentes:
                    break
    return encontrados


def carregar_correcoes_de_raiz_pdfs(raiz: Path, carimbos_filtro: set[str]) -> dict[str, dict[str, str]]:
    encontrados = localizar_pdfs_por_carimbo(raiz, carimbos_filtro)
    correcoes: dict[str, dict[str, str]] = {}
    linhas_log: list[dict[str, Any]] = []

    for carimbo in sorted(carimbos_filtro):
        pdf = encontrados.get(carimbo)
        if not pdf:
            linhas_log.append({"carimbo": carimbo, "arquivo": "", "status": "nao_encontrado", "campos": 0})
            warn(f"[LOCALIZADO] BB_{carimbo}: PDF nao encontrado em {raiz}")
            continue

        try:
            row = _build_record(pdf)
        except Exception as exc:
            linhas_log.append({"carimbo": carimbo, "arquivo": str(pdf), "status": f"erro_ocr:{type(exc).__name__}", "campos": 0})
            warn(f"[OCR] {pdf.name}: {type(exc).__name__}: {exc}")
            continue

        if not eh_linha_copel_bt(row):
            linhas_log.append({
                "carimbo": carimbo, "arquivo": str(pdf),
                "status": "pulado_nao_copel_bt_ou_erro", "campos": 0,
                "erro": row.get("ERRO", ""),
            })
            continue

        mapa = correcoes_da_linha_ocr(row)
        # Em BT GD (injetado > 0) sem reativa excedente, zera os campos de excedente
        # que o fallback CSV preenchia erroneamente com o consumo FP normal.
        _inj = float(row.get("fatConFPontaInjetadoRegistrado") or 0)
        _exc = row.get("fatConFPontaIndExcRegistrado")
        if _inj > 0 and (_exc is None or float(_exc or 0) == 0):
            mapa["fatConFPontaIndExcRegistrado"] = "0,00"
            mapa["fatConFPontaIndExc"] = "0,00"
            mapa["fatConFPontaIndExcValorReais"] = "0,00"
        if mapa:
            correcoes[carimbo] = mapa
        linhas_log.append({
            "carimbo": carimbo,
            "arquivo": str(pdf),
            "status": "ok" if mapa else "sem_campos",
            "campos": len(mapa),
            "bandeira": row.get("fatValBandeira", ""),
            "pis_ret": row.get("fatDescPisPercRetImposto", ""),
            "cofins_ret": row.get("fatDescCofinsPercRetImposto", ""),
            "obs1": row.get("obsCod_1", ""),
        })

    _salvar_log_preparacao(linhas_log)
    return correcoes


def _salvar_log_preparacao(linhas: list[dict[str, Any]]) -> None:
    SAIDA_DIR.mkdir(parents=True, exist_ok=True)
    path = SAIDA_DIR / "preparacao_correcao_copel_bt.csv"
    campos = ["carimbo", "arquivo", "status", "campos", "bandeira", "pis_ret", "cofins_ret", "obs1", "erro"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore", delimiter=";")
        writer.writeheader()
        writer.writerows(linhas)
    log(f"Log de preparacao salvo: {path}")

    carimbos_ok = sorted({str(l.get("carimbo") or "") for l in linhas if l.get("status") == "ok"})
    txt = SAIDA_DIR / "carimbos_preparados_copel_bt.txt"
    txt.write_text("\n".join(f"BB_{c}" for c in carimbos_ok if c), encoding="utf-8")
    log(f"Lista de carimbos preparados: {txt}")


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


def aplicar_correcoes(driver, wait, carimbo: str, correcoes: dict[str, Any]) -> tuple[int, int, int]:
    return fluxo_base.aplicar_correcoes(driver, wait, carimbo, correcoes, CONFIG.ordem_campos)


def salvar_auditar_e_avancar(driver, wait, carimbo: str) -> None:
    fluxo_base.salvar_auditar_e_avancar(driver, wait, carimbo)


def salvar_snapshot(driver, carimbo: str) -> None:
    fluxo_base.salvar_snapshot(driver, CONFIG.saida_dir, carimbo)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Correcao COPEL BT por carimbo no Consen")
    p.add_argument("--carimbo", action="append", default=[], help="Ex: --carimbo BB_2011234")
    p.add_argument("--carimbos-arquivo", type=str, default="", help="TXT com um carimbo por linha")
    p.add_argument("--pasta-pdfs", type=str, default=str(PDFS_ROOT), help="Raiz para busca recursiva dos PDFs")
    p.add_argument("--preparar-apenas", action="store_true", help="Apenas OCR + monta correcoes, sem abrir Consen")
    p.add_argument("--retomar-apos", type=str, default="", help="Retoma o lote apos este carimbo")
    p.add_argument("--reprocessar-ok", action="store_true", help="Nao pula carimbos ja concluidos com status ok")
    p.add_argument("--salvar", action="store_true", help="Salva a fatura apos aplicar correcoes")
    p.add_argument("--sem-snapshot", action="store_true", help="Nao salva HTML/JSON da tela")
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--somente-bandeira", action="store_true", help="Aplica apenas fatValBandeira e fatValBandeira2")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    carimbos_filtro: set[str] = set()
    for item in list(args.carimbo or []):
        carimbos_filtro.add(normalizar_carimbo(item))
    if args.carimbos_arquivo:
        path = Path(args.carimbos_arquivo)
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                carimbos_filtro.add(normalizar_carimbo(line))

    raiz_pdfs = Path(args.pasta_pdfs)
    log("=== Correcao COPEL BT ===")
    log(f"Pasta PDFs : {raiz_pdfs}")
    log(f"Carimbos   : {len(carimbos_filtro) if carimbos_filtro else 'todos'}")

    correcoes = carregar_correcoes_de_raiz_pdfs(raiz_pdfs, carimbos_filtro)

    if args.somente_bandeira:
        _BANDEIRA_CAMPOS = {"fatValBandeira", "fatValBandeira2"}
        correcoes = {c: {k: v for k, v in m.items() if k in _BANDEIRA_CAMPOS} for c, m in correcoes.items()}
        correcoes = {c: m for c, m in correcoes.items() if m}
        log(f"[somente-bandeira] {len(correcoes)} carimbos com bandeira a corrigir.")

    if args.preparar_apenas:
        log(f"[preparar-apenas] {len(correcoes)} carimbos preparados. Encerrando sem abrir Consen.")
        return 0

    if not correcoes:
        log("Nenhuma correcao encontrada. Encerrando.")
        return 0

    carimbos_ordenados = sorted(correcoes)
    if args.retomar_apos:
        ancora = normalizar_carimbo(args.retomar_apos)
        if ancora in carimbos_ordenados:
            idx = carimbos_ordenados.index(ancora) + 1
            carimbos_ordenados = carimbos_ordenados[idx:]
            log(f"Retomando apos BB_{ancora}: {len(carimbos_ordenados)} restantes.")

    status_execucao = carregar_status_execucao()
    if not args.reprocessar_ok:
        antes = len(carimbos_ordenados)
        carimbos_ordenados = [c for c in carimbos_ordenados if status_execucao.get(c) != "ok"]
        log(f"Pulados (ok anteriores): {antes - len(carimbos_ordenados)}")

    if args.limite > 0:
        carimbos_ordenados = carimbos_ordenados[: args.limite]

    log(f"Processando {len(carimbos_ordenados)} carimbos.")
    if not carimbos_ordenados:
        log("Nenhum carimbo para processar.")
        return 0

    driver, wait = abrir_driver_logado()
    try:
        abrir_tela_edicao_carimbo(driver, wait)

        for i, carimbo in enumerate(carimbos_ordenados, start=1):
            log(f"--- [{i}/{len(carimbos_ordenados)}] BB_{carimbo} ---")
            try:
                carregar_fatura_por_carimbo(driver, wait, carimbo)
                if not args.sem_snapshot:
                    salvar_snapshot(driver, carimbo)
                apl, conf, total = aplicar_correcoes(driver, wait, carimbo, correcoes[carimbo])
                if args.salvar and apl > 0:
                    salvar_auditar_e_avancar(driver, wait, carimbo)
                    registrar_execucao(carimbo, "ok", f"apl={apl} conf={conf}/{total}")
                elif not args.salvar:
                    log(f"BB_{carimbo}: --salvar nao informado. Correcoes NAO salvas.")
                    registrar_execucao(carimbo, "simulado", f"apl={apl} conf={conf}/{total}")
                else:
                    log(f"BB_{carimbo}: nenhum campo alterado.")
                    registrar_execucao(carimbo, "sem_alteracao")
            except (InvalidSessionIdException, WebDriverException) as exc:
                warn(f"BB_{carimbo}: sessao perdida ({type(exc).__name__}). Tentando relogin...")
                registrar_execucao(carimbo, "erro_sessao", str(exc))
                try:
                    driver.quit()
                except Exception:
                    pass
                driver, wait = abrir_driver_logado()
                abrir_tela_edicao_carimbo(driver, wait)
            except Exception as exc:
                warn(f"BB_{carimbo}: {type(exc).__name__}: {exc}")
                registrar_execucao(carimbo, "erro", str(exc))

    finally:
        if FECHAR_AO_FINAL:
            try:
                driver.quit()
            except Exception:
                pass

    log("=== Correcao COPEL BT concluida ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
