#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Correcao EDP SP BT por carimbo.

Fluxo:
1. Localiza os PDFs dos carimbos informados.
2. Reprocessa com o OCR EDP SP BT atualizado.
3. Corrige no Consen os campos de ICMS/PIS/COFINS e retencoes individuais.
4. Salva (requer --salvar).
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from pathlib import Path
from typing import Any

DEFAULT_SAIDA_DIR = "//10.10.250.21/Energia/ARQUIVOS ENZO/EDP_SP_pipeline_saida/BT/correcao_por_carimbo"
PDF_ROOTS_DEFAULT = (
    Path(r"\\10.10.250.21\Energia\CONTASDEENERGIAELETRICA\BB\ENZO\Digitadas"),
    Path(r"\\10.10.250.21\Energia\CONTROLE BB\DIGITADOS\CARIMBOS DIGITADOS"),
)
CARIMBOS_PADRAO = ("2013189", "2013215", "2013216", "2013217", "2013218", "2013220")

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
    from ocr.ocr_edp_bt import processar_pdf
except ModuleNotFoundError:
    import correcao_fluxo_base as fluxo_base  # type: ignore
    from correcao_fluxo_base import (  # type: ignore
        CorrecaoFluxoConfig,
        formatar_valor_para_campo,
        log,
        warn,
    )
    from ocr_edp_bt import processar_pdf  # type: ignore

LOGIN_URL = fluxo_base.LOGIN_URL
BASE_URL = LOGIN_URL.rsplit("login.php", 1)[0]
EDIT_URL = os.environ.get(
    "CONSEN_EDITA_FATURA_CARIMBO_URL",
    f"{BASE_URL}index.php#bpg/gestao/fatura/editaFaturaCarimbo.php",
)

SAIDA_DIR = Path(os.environ.get("CONSEN_CORRECAO_SAIDA", DEFAULT_SAIDA_DIR))
FECHAR_AO_FINAL = os.environ.get("CONSEN_CORRECAO_FECHAR", "1").strip().lower() not in {"0", "false", "nao", "não"}
EXECUCAO_CSV = SAIDA_DIR / "correcao_execucao.csv"

CAMPOS_CRITICOS_TELA: tuple[str, ...] = ("btnSalvar", "fatDesIcmsAliquota")
ORDEM_CAMPOS: tuple[str, ...] = (
    "fatDesIcmsAliquota",
    "fatICMS",
    "fatDescPisAliquota",
    "fatPIS",
    "fatDesCofinsAliquota",
    "fatCofins",
    "fatTributoFederalPerc",
    "fatTributoFederalVal",
    "fatDescPisPercRetImposto",
    "fatDescPisValRetImposto",
    "fatDescCofinsPercRetImposto",
    "fatDescCofinsValRetImposto",
    "fatDescCsllPercRetImposto",
    "fatDescCsllValRetImposto",
    "fatDescIrpjPercRetImposto",
    "fatDescIrpjValRetImposto",
)

CONFIG = CorrecaoFluxoConfig(
    saida_dir=SAIDA_DIR,
    execucao_csv=EXECUCAO_CSV,
    edit_url=EDIT_URL,
    ordem_campos=ORDEM_CAMPOS,
    fechar_ao_final=FECHAR_AO_FINAL,
)


def normalizar_carimbo(carimbo: str) -> str:
    return fluxo_base.normalizar_carimbo(carimbo)


def valor_vazio(valor: Any) -> bool:
    return fluxo_base.valor_vazio(valor)


def _listar_carimbos(raw: str) -> list[str]:
    if not raw.strip():
        return list(CARIMBOS_PADRAO)
    out: list[str] = []
    for part in raw.replace(";", ",").split(","):
        txt = part.strip()
        if not txt:
            continue
        out.append(normalizar_carimbo(txt))
    return out


def _localizar_pdf(carimbo: str) -> Path | None:
    target = f"BB_{normalizar_carimbo(carimbo)}.pdf"
    for root in PDF_ROOTS_DEFAULT:
        if not root.exists():
            continue
        try:
            for path in root.rglob(target):
                return path
        except OSError:
            continue
    return None


def _correcoes_do_ocr(rec: dict[str, Any]) -> dict[str, str]:
    correcoes: dict[str, str] = {}

    mapa = {
        "fatDesIcmsAliquota": rec.get("fatDesIcmsAliquota"),
        "fatICMS": rec.get("fatICMS"),
        "fatDescPisAliquota": rec.get("fatDescPisAliquota"),
        "fatPIS": rec.get("fatPIS"),
        "fatDesCofinsAliquota": rec.get("fatDesCofinsAliquota"),
        "fatCofins": rec.get("fatCOFINS"),
        "fatDescPisPercRetImposto": rec.get("fatDescPisPercRetImposto"),
        "fatDescPisValRetImposto": rec.get("fatDescPisValRetImposto"),
        "fatDescCofinsPercRetImposto": rec.get("fatDescCofinsPercRetImposto"),
        "fatDescCofinsValRetImposto": rec.get("fatDescCofinsValRetImposto"),
        "fatDescCsllPercRetImposto": rec.get("fatDescCsllPercRetImposto"),
        "fatDescCsllValRetImposto": rec.get("fatDescCsllValRetImposto"),
        "fatDescIrpjPercRetImposto": rec.get("fatDescIrpjPercRetImposto"),
        "fatDescIrpjValRetImposto": rec.get("fatDescIrpjValRetImposto"),
    }

    tem_ret_individual = any(
        not valor_vazio(rec.get(campo))
        for campo in (
            "fatDescPisValRetImposto",
            "fatDescCofinsValRetImposto",
            "fatDescCsllValRetImposto",
            "fatDescIrpjValRetImposto",
        )
    )
    if tem_ret_individual:
        mapa["fatTributoFederalPerc"] = 0
        mapa["fatTributoFederalVal"] = 0

    for campo_tela, valor in mapa.items():
        if valor_vazio(valor):
            continue
        correcoes[campo_tela] = formatar_valor_para_campo(campo_tela, valor, "text")
    return correcoes


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Correcao EDP SP BT por carimbo")
    p.add_argument("--carimbos", default=",".join(CARIMBOS_PADRAO), help="Lista separada por virgula")
    p.add_argument("--salvar", action="store_true", help="Salva apos preencher")
    p.add_argument("--sem-snapshot", action="store_true", help="Nao salva HTML/JSON da tela")
    p.add_argument("--reprocessar-ok", action="store_true", help="Reprocessa itens ja marcados como ok no CSV")
    return p.parse_args()


def _salvar_preparacao(linhas: list[dict[str, Any]]) -> None:
    SAIDA_DIR.mkdir(parents=True, exist_ok=True)
    path = SAIDA_DIR / "preparacao_correcao_edp_sp_bt.csv"
    campos = ["carimbo", "arquivo", "status", "campos", "icms_aliq", "pis_aliq", "cofins_aliq", "pis_ret", "cof_ret", "csll_ret", "irpj_ret", "erro"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore", delimiter=";")
        writer.writeheader()
        writer.writerows(linhas)
    log(f"Log de preparacao salvo: {path}")


def main() -> int:
    args = parse_args()
    carimbos = _listar_carimbos(args.carimbos)

    if not args.reprocessar_ok:
        status_ok = fluxo_base.carregar_status_execucao(EXECUCAO_CSV)
        carimbos = [c for c in carimbos if status_ok.get(c) != "ok"]

    if not carimbos:
        log("Nenhum carimbo pendente.")
        return 0

    registros: list[tuple[str, Path, dict[str, str]]] = []
    linhas_log: list[dict[str, Any]] = []
    for carimbo in carimbos:
        pdf = _localizar_pdf(carimbo)
        if not pdf:
            warn(f"BB_{carimbo}: PDF nao encontrado.")
            linhas_log.append({"carimbo": carimbo, "arquivo": "", "status": "nao_encontrado", "campos": 0})
            continue
        try:
            rec = processar_pdf(str(pdf))
            correcoes = _correcoes_do_ocr(rec)
            registros.append((carimbo, pdf, correcoes))
            linhas_log.append(
                {
                    "carimbo": carimbo,
                    "arquivo": str(pdf),
                    "status": "ok" if correcoes else "sem_campos",
                    "campos": len(correcoes),
                    "icms_aliq": rec.get("fatDesIcmsAliquota"),
                    "pis_aliq": rec.get("fatDescPisAliquota"),
                    "cofins_aliq": rec.get("fatDesCofinsAliquota"),
                    "pis_ret": rec.get("fatDescPisValRetImposto"),
                    "cof_ret": rec.get("fatDescCofinsValRetImposto"),
                    "csll_ret": rec.get("fatDescCsllValRetImposto"),
                    "irpj_ret": rec.get("fatDescIrpjValRetImposto"),
                }
            )
        except Exception as exc:
            warn(f"BB_{carimbo}: erro no OCR - {type(exc).__name__}: {exc}")
            linhas_log.append({"carimbo": carimbo, "arquivo": str(pdf), "status": f"erro_ocr:{type(exc).__name__}", "campos": 0, "erro": str(exc)})

    _salvar_preparacao(linhas_log)
    registros = [item for item in registros if item[2]]
    if not registros:
        warn("Nenhum carimbo com correcoes prontas.")
        return 1

    driver = None
    try:
        driver, wait = fluxo_base.abrir_driver_logado()
        time.sleep(1.5)

        for carimbo, pdf, correcoes in registros:
            log(f"--- BB_{carimbo} ---")
            fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "iniciado")
            try:
                fluxo_base.abrir_tela_edicao_carimbo(driver, wait, CONFIG.edit_url)
                time.sleep(0.8)
                fluxo_base.carregar_fatura_por_carimbo(driver, wait, carimbo, CAMPOS_CRITICOS_TELA)
                time.sleep(0.8)
                if not args.sem_snapshot:
                    fluxo_base.salvar_snapshot(driver, SAIDA_DIR, carimbo)

                qtd, confirmadas, total = fluxo_base.aplicar_correcoes(
                    driver, wait, carimbo, correcoes, CONFIG.ordem_campos
                )
                if args.salvar:
                    if confirmadas < total:
                        warn(f"BB_{carimbo}: {confirmadas}/{total} confirmados - salvamento bloqueado.")
                        fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "bloqueado_incompleto", f"{confirmadas}/{total}")
                    else:
                        time.sleep(0.8)
                        fluxo_base.salvar_auditar_e_avancar(driver, wait, carimbo)
                        fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "ok", f"{confirmadas}/{total}")
                        log(f"BB_{carimbo}: salvo com sucesso.")
                else:
                    fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "validado_sem_salvar", f"{confirmadas}/{total}")
                    log(f"BB_{carimbo}: validado sem salvar ({confirmadas}/{total}).")
            except Exception as exc:
                warn(f"BB_{carimbo}: falha - {type(exc).__name__}: {exc}")
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, f"erro_{type(exc).__name__}", str(exc))

        return 0
    finally:
        if driver and CONFIG.fechar_ao_final:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
