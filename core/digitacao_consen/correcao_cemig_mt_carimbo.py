#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Correcao CEMIG MT por carimbo.

Fluxo:
1. Localiza os PDFs na raiz de carimbos (CONTROLE BB/DIGITADOS ou DOWNLOAD CEMIG).
2. Reprocessa cada PDF com OCR_Cemig.processar_pdf(..., "mt").
3. Corrige no Consen os campos de demanda registrada/faturada, consumo e retencoes.

Por seguranca, nao salva por padrao. Use --salvar quando quiser efetivar.

Uso:
    python correcao_cemig_mt_carimbo.py --carimbo BB_2002673 --preparar-apenas
    python correcao_cemig_mt_carimbo.py --carimbos-arquivo cemig_mt_dem_zerada.txt --salvar
    python correcao_cemig_mt_carimbo.py --carimbo BB_2007576 --raiz-pdfs "\\\\10.10.250.21\\Energia\\ARQUIVOS ENZO\\DOWNLOAD CEMIG" --salvar
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from pathlib import Path
from typing import Any

from selenium.webdriver.common.by import By

DEFAULT_SAIDA_DIR = "//10.10.250.21/Energia/ARQUIVOS ENZO/CEMIG_pipeline_saida/MT/correcoes_por_carimbo"
DEFAULT_PDFS_ROOTS = [
    "//10.10.250.21/Energia/CONTROLE BB/DIGITADOS/CARIMBOS DIGITADOS",
    "//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD CEMIG",
    "//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO",
]
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
    from ocr.OCR_Cemig import processar_pdf  # noqa: E402
except ModuleNotFoundError:
    import correcao_fluxo_base as fluxo_base  # type: ignore  # noqa: E402
    from correcao_fluxo_base import (  # type: ignore  # noqa: E402
        CorrecaoFluxoConfig,
        formatar_valor_para_campo,
        log,
        warn,
    )
    from OCR_Cemig import processar_pdf  # type: ignore  # noqa: E402

LOGIN_URL = fluxo_base.LOGIN_URL
BASE_URL = LOGIN_URL.rsplit("login.php", 1)[0]
EDIT_HASH = "#bpg/gestao/fatura/editaFaturaCarimbo.php"
EDIT_URL = os.environ.get("CONSEN_EDITA_FATURA_CARIMBO_URL", f"{BASE_URL}index.php{EDIT_HASH}")

SAIDA_DIR = Path(os.environ.get("CONSEN_CORRECAO_SAIDA", DEFAULT_SAIDA_DIR))
FECHAR_AO_FINAL = os.environ.get("CONSEN_CORRECAO_FECHAR", "1").strip().lower() not in {"0", "false", "nao", "não"}
EXECUCAO_CSV = SAIDA_DIR / "correcao_execucao.csv"

CAMPOS_CRITICOS_TELA: tuple[str, ...] = (
    "btnSalvar",
    "fatDemFPontaIndRegistrada",
)

CAMPO_OCR_PARA_TELA: tuple[tuple[str, str], ...] = (
    ("fatICMS", "fatICMS"),
    ("fatDemFPontaIndRegistrada", "fatDemFPontaIndRegistrada"),
    ("fatDemPontaRegistrada",     "fatDemPontaRegistrada"),
)

ORDEM_CAMPOS_CORRECAO: tuple[str, ...] = tuple(campo for campo, _ in CAMPO_OCR_PARA_TELA)

CAMPO_TELA_ALTERNATIVOS: dict[str, tuple[str, ...]] = {
    "fatDemFPontaIndRegistrada": ("txt-demandas-registrada-fpind", "fatDemFPontaIndRegistrada"),
    "fatDemPontaRegistrada":     ("txt-demandas-registrada-pta",   "fatDemPontaRegistrada"),
}

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


def correcoes_da_linha_ocr(row: dict[str, Any]) -> dict[str, str]:
    correcoes: dict[str, str] = {}
    for campo_tela, header in CAMPO_OCR_PARA_TELA:
        valor = row.get(header)
        if valor_vazio(valor):
            continue
        try:
            if float(str(valor).replace(",", ".")) == 0.0:
                continue
        except (TypeError, ValueError):
            pass
        correcoes[campo_tela] = formatar_valor_correcao(header, valor)
    return correcoes


def localizar_pdfs_por_carimbo(raizes: list[Path], carimbos_filtro: set[str]) -> dict[str, Path]:
    encontrados: dict[str, Path] = {}
    pendentes = set(carimbos_filtro)

    for raiz in raizes:
        if not raiz.exists():
            warn(f"[LOCALIZAR] Pasta nao acessivel: {raiz}")
            continue
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
                if carimbo in pendentes:
                    encontrados[carimbo] = pasta / nome
                    pendentes.discard(carimbo)
                    log(f"[LOCALIZADO] BB_{carimbo} -> {pasta / nome}")
        if not pendentes:
            break

    if pendentes:
        warn(f"[LOCALIZAR] Nao encontrados: {', '.join(f'BB_{c}' for c in sorted(pendentes))}")
    return encontrados


def carregar_correcoes_de_raizes(raizes: list[Path], carimbos_filtro: set[str]) -> dict[str, dict[str, Any]]:
    encontrados = localizar_pdfs_por_carimbo(raizes, carimbos_filtro)
    correcoes: dict[str, dict[str, Any]] = {}
    linhas_log: list[dict[str, Any]] = []

    for carimbo in sorted(carimbos_filtro):
        pdf = encontrados.get(carimbo)
        if not pdf:
            linhas_log.append({"carimbo": carimbo, "arquivo": "", "status": "nao_encontrado", "campos": 0, "erro": ""})
            continue

        try:
            row = processar_pdf(str(pdf), "mt")
        except Exception as exc:
            linhas_log.append({
                "carimbo": carimbo, "arquivo": str(pdf),
                "status": f"erro_ocr:{type(exc).__name__}", "campos": 0, "erro": str(exc),
            })
            warn(f"[OCR] {pdf.name}: {type(exc).__name__}: {exc}")
            continue

        erro = str(row.get("ERRO") or "").strip()
        if erro:
            linhas_log.append({
                "carimbo": carimbo, "arquivo": str(pdf),
                "status": "pulado_erro_ocr", "campos": 0, "erro": erro,
            })
            warn(f"[OCR] {pdf.name}: ERRO OCR = {erro}")
            continue

        mapa = correcoes_da_linha_ocr(row)
        if mapa:
            correcoes[carimbo] = mapa
        linhas_log.append({
            "carimbo": carimbo, "arquivo": str(pdf),
            "status": "ok" if mapa else "sem_campos", "campos": len(mapa), "erro": "",
        })
        log(f"[OCR] BB_{carimbo}: {row.get('TARIFA_DETECTADA')} | "
            f"FP_reg={row.get('fatDemFPontaIndRegistrada')} "
            f"PT_reg={row.get('fatDemPontaRegistrada')}")

    _salvar_log(linhas_log)
    return correcoes


def _salvar_log(linhas: list[dict[str, Any]]) -> None:
    SAIDA_DIR.mkdir(parents=True, exist_ok=True)
    path = SAIDA_DIR / "preparacao_correcao_cemig_mt.csv"
    campos = ["carimbo", "arquivo", "status", "campos", "erro"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=campos, delimiter=";")
        writer.writeheader()
        writer.writerows(linhas)
    log(f"Log de preparacao salvo: {path}")
    carimbos_ok = sorted({str(l["carimbo"]).strip() for l in linhas if l.get("status") == "ok"})
    txt = SAIDA_DIR / "carimbos_preparados_cemig_mt.txt"
    txt.write_text("\n".join(f"BB_{c}" for c in carimbos_ok if c), encoding="utf-8")
    log(f"Carimbos preparados: {txt}")


def registrar_execucao(carimbo: str, status: str, detalhe: str = "") -> None:
    fluxo_base.registrar_execucao(CONFIG.execucao_csv, carimbo, status, detalhe)


def carregar_status_execucao() -> dict[str, str]:
    return fluxo_base.carregar_status_execucao(CONFIG.execucao_csv)


def _localizar_input(driver, campo: str):
    for by, sel in [
        (By.ID, campo),
        (By.NAME, campo),
        (By.CSS_SELECTOR, f"input[id='{campo}'],select[id='{campo}'],textarea[id='{campo}']"),
        (By.CSS_SELECTOR, f"input[name='{campo}'],select[name='{campo}'],textarea[name='{campo}']"),
    ]:
        try:
            els = driver.find_elements(by, sel)
        except Exception:
            continue
        for el in els:
            try:
                if el.is_displayed():
                    return el
            except Exception:
                pass
        if els:
            return els[0]
    return None


def _aplicar_campo(driver, wait, campo_logico: str, valor: Any) -> tuple[int, int]:
    candidatos = CAMPO_TELA_ALTERNATIVOS.get(campo_logico, (campo_logico,))
    for candidato in candidatos:
        el = _localizar_input(driver, candidato)
        if el is None:
            continue
        try:
            atual = (el.get_attribute("value") or "").strip()
            if atual == str(valor).strip():
                return 0, 1
            ok = fluxo_base.preencher_elemento_html(driver, el, valor)
            return (1, 1) if ok else (0, 0)
        except Exception:
            continue
    warn(f"[CORRECAO] Campo {campo_logico} nao encontrado ({', '.join(candidatos)})")
    return 0, 1


def aplicar_correcoes(driver, wait, carimbo: str, correcoes: dict[str, Any]) -> tuple[int, int, int]:
    if not correcoes:
        log(f"Sem correcoes para BB_{normalizar_carimbo(carimbo)}.")
        return 0, 0, 0
    aplicadas = confirmadas = 0
    ordem = [c for c in CONFIG.ordem_campos if c in correcoes]
    ordem += [c for c in correcoes if c not in ordem]
    for campo in ordem:
        qtd, ok = _aplicar_campo(driver, wait, campo, correcoes[campo])
        aplicadas += qtd
        confirmadas += ok
    return aplicadas, confirmadas, len(ordem)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Correcao CEMIG MT por carimbo no Consen")
    p.add_argument("--carimbo", action="append", default=[], help="Carimbo. Ex: --carimbo BB_2002673")
    p.add_argument("--carimbos-arquivo", type=str, default="", help="TXT com um carimbo por linha")
    p.add_argument("--raiz-pdfs", action="append", default=[], help="Pasta raiz de PDFs (pode repetir)")
    p.add_argument("--preparar-apenas", action="store_true", help="So roda OCR e imprime correcoes, sem abrir Consen")
    p.add_argument("--retomar-apos", type=str, default="", help="Pula ate este carimbo (exclusive)")
    p.add_argument("--reprocessar-ok", action="store_true", help="Reprocessa mesmo carimbos ja com status ok")
    p.add_argument("--salvar", action="store_true", help="Salva no Consen apos aplicar correcoes")
    p.add_argument("--sem-snapshot", action="store_true", help="Nao salva snapshot HTML")
    p.add_argument("--limite", type=int, default=0)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    carimbos_filtro: set[str] = {normalizar_carimbo(c) for c in (args.carimbo or [])}
    if args.carimbos_arquivo:
        for line in Path(args.carimbos_arquivo).read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                carimbos_filtro.add(normalizar_carimbo(line))

    if not carimbos_filtro:
        print("Informe --carimbo ou --carimbos-arquivo.")
        return 2

    raizes = [Path(r) for r in (args.raiz_pdfs or [])] or [Path(r) for r in DEFAULT_PDFS_ROOTS]
    correcoes_ocr = carregar_correcoes_de_raizes(raizes, carimbos_filtro)

    carimbos = sorted(carimbos_filtro)
    if args.retomar_apos:
        marcador = normalizar_carimbo(args.retomar_apos)
        if marcador in carimbos:
            carimbos = carimbos[carimbos.index(marcador) + 1:]
    if not args.reprocessar_ok:
        status_ex = carregar_status_execucao()
        carimbos = [c for c in carimbos if status_ex.get(c) != "ok"]
    if args.limite > 0:
        carimbos = carimbos[:args.limite]

    if not carimbos:
        print("Nenhum carimbo para processar.")
        return 0

    if args.preparar_apenas:
        log(f"Preparacao concluida. {len(correcoes_ocr)}/{len(carimbos)} carimbos com correcoes.")
        for c, payload in sorted(correcoes_ocr.items()):
            log(f"  BB_{c}: {', '.join(payload.keys())}")
        return 0

    driver = None
    try:
        driver, wait = fluxo_base.abrir_driver_logado()
        time.sleep(3.0)

        for carimbo in carimbos:
            registrar_execucao(carimbo, "iniciado")
            fluxo_base.abrir_tela_edicao_carimbo(driver, wait, CONFIG.edit_url)
            time.sleep(1.5)
            fluxo_base.carregar_fatura_por_carimbo(driver, wait, carimbo, CAMPOS_CRITICOS_TELA)
            time.sleep(1.0)

            if not args.sem_snapshot:
                fluxo_base.salvar_snapshot(driver, CONFIG.saida_dir, carimbo)

            correcoes = dict(correcoes_ocr.get(normalizar_carimbo(carimbo), {}))
            qtd, confirmadas, total = aplicar_correcoes(driver, wait, carimbo, correcoes)

            if args.salvar:
                if total <= 0:
                    warn(f"--salvar ativo mas sem correcoes para BB_{normalizar_carimbo(carimbo)}. Pulado.")
                    registrar_execucao(carimbo, "sem_correcoes")
                elif confirmadas < total:
                    warn(f"BB_{normalizar_carimbo(carimbo)}: incompleto ({confirmadas}/{total}). Salvamento bloqueado.")
                    registrar_execucao(carimbo, "bloqueado_incompleto", f"{confirmadas}/{total}")
                else:
                    time.sleep(1.0)
                    fluxo_base.salvar_auditar_e_avancar(driver, wait, carimbo)
                    registrar_execucao(carimbo, "ok", f"{confirmadas}/{total}")
                    log(f"BB_{normalizar_carimbo(carimbo)}: salvo ({confirmadas}/{total})")
            else:
                registrar_execucao(carimbo, "validado_sem_salvar", f"{confirmadas}/{total}")
                log(f"BB_{normalizar_carimbo(carimbo)}: modo seguro ({confirmadas}/{total})")

            time.sleep(1.0)
        return 0
    finally:
        if driver and CONFIG.fechar_ao_final:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
