#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Correcao COPEL MT por carimbo.

Fluxo:
1. Le um XLSX de OCR COPEL MT ja validado/corrigido.
2. Monta as correcoes por carimbo para os campos relevantes do CONSEN.
3. Abre a tela de edicao por carimbo e aplica as alteracoes.

Por seguranca, nao salva por padrao. Use --salvar quando quiser efetivar.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd

DEFAULT_SAIDA_DIR = "//10.10.250.21/Energia/ARQUIVOS ENZO/COPEL_pipeline_saida/MT/correcoes_por_carimbo"
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

SAIDA_DIR = Path(os.environ.get("CONSEN_CORRECAO_SAIDA", DEFAULT_SAIDA_DIR))
DEFAULT_XLSX = os.environ.get("CONSEN_CORRECAO_COPEL_XLSX", "").strip()
FECHAR_AO_FINAL = os.environ.get("CONSEN_CORRECAO_FECHAR", "1").strip().lower() not in {"0", "false", "nao", "não"}
EXECUCAO_CSV = SAIDA_DIR / "correcao_execucao.csv"
CAMPOS_CRITICOS_TELA: tuple[str, ...] = (
    "btnSalvar",
    "instalacao",
)

# Campo da tela -> header do XLSX
CAMPO_XLSX_PARA_TELA: tuple[tuple[str, str], ...] = (
    ("fatDemContratadaFPonta", "fatDemContratadaFPonta"),
    ("fatDemPontaRegistrada", "fatDemPontaRegistrada"),
    ("fatDemPontaFaturada", "fatDemPontaFaturada"),
    ("fatDemPontaValorReais", "fatDemPontaValorReais"),
    ("fatDemFPontaIndRegistrada", "fatDemFPontaIndRegistrada"),
    ("fatDemFPontaIndFaturada", "fatDemFPontaIndFaturada"),
    ("fatDemFPontaIndValorReais", "fatDemFPontaIndValorReais"),
    ("fatDemFPontaIndUltra", "fatDemFPontaIndUltra"),
    ("fatDemFPontaIndUltraValorReais", "fatDemFPontaIndUltraValorReais"),
    ("fatConPontaRegistrado", "fatConPontaRegistrado"),
    ("fatConPontaFaturado", "fatConPontaFaturado"),
    ("fatConPontaValorReais", "fatConPontaValorReais"),
    ("fatConFPontaIndRegistrado", "fatConFPontaIndRegistrado"),
    ("fatConFPontaIndFaturado", "fatConFPontaIndFaturado"),
    ("fatConFPontaIndValorReais", "fatConFPontaIndValorReais"),
    ("fatConPontaExcRegistrado", "fatConPontaExcRegistrado"),
    ("fatConPontaExcFaturado", "fatConPontaExcFaturado"),
    ("fatConPontaExcValorReais", "fatConPontaExcValorReais"),
    ("fatConFPontaIndExcRegistrado", "fatConFPontaIndExcRegistrado"),
    ("fatConFPontaIndExcFaturado", "fatConFPontaIndExcFaturado"),
    ("fatConFPontaIndExcValorReais", "fatConFPontaIndExcValorReais"),
    ("fatBeneficioTarifarioBrutoValorReais", "fatBeneficioTarifarioBrutoValorReais"),
    ("fatBeneficioLiquidoValorReais", "fatBeneficioLiquidoValorReais"),
    ("fatICMS", "fatICMS"),
    ("fatMultasDiversas", "fatMultasDiversas"),
    ("fatMultas", "fatMultas"),
    ("fatDescontoFio", "fatDescontoFio"),
    ("fatDescontoFioKWh", "fatDescontoFioKWh"),
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
ORDEM_CAMPOS_CORRECAO: tuple[str, ...] = tuple(campo for campo, _ in CAMPO_XLSX_PARA_TELA)
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


def carregar_correcoes_de_xlsx(xlsx: Path, carimbos_filtro: set[str]) -> dict[str, dict[str, str]]:
    if not xlsx.exists():
        raise FileNotFoundError(f"XLSX nao encontrado: {xlsx}")

    df = pd.read_excel(xlsx)
    correcoes: dict[str, dict[str, str]] = {}
    linhas_log: list[dict[str, Any]] = []

    for idx, row in enumerate(df.to_dict(orient="records"), start=2):
        carimbo_bruto = row.get("fatCarimbo")
        if valor_vazio(carimbo_bruto):
            continue
        carimbo = normalizar_carimbo(str(carimbo_bruto))
        if carimbos_filtro and carimbo not in carimbos_filtro:
            continue

        erro = str(row.get("ERRO") or "").strip()
        if erro:
            linhas_log.append({
                "carimbo": carimbo,
                "status": "pulado_com_erro",
                "erro": erro,
                "campos": 0,
                "linha_excel": idx,
            })
            continue

        mapa: dict[str, str] = {}
        for campo_tela, header in CAMPO_XLSX_PARA_TELA:
            if header not in row:
                continue
            valor = row.get(header)
            if valor_vazio(valor):
                continue
            mapa[campo_tela] = formatar_valor_correcao(header, valor)

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
            "status": "ok" if mapa else "sem_campos",
            "erro": erro,
            "campos": len(mapa),
            "linha_excel": idx,
        })

    salvar_log_preparacao(linhas_log)
    return correcoes


def salvar_log_preparacao(linhas: list[dict[str, Any]]) -> None:
    SAIDA_DIR.mkdir(parents=True, exist_ok=True)
    path = SAIDA_DIR / "preparacao_correcao_copel_mt.csv"
    campos = ["carimbo", "status", "erro", "campos", "linha_excel"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=campos, delimiter=";")
        writer.writeheader()
        writer.writerows(linhas)
    log(f"Log de preparacao salvo: {path}")

    carimbos_ok = sorted({str(l.get("carimbo") or "").strip() for l in linhas if l.get("status") == "ok"})
    txt_path = SAIDA_DIR / "carimbos_preparados_copel_mt.txt"
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


def aplicar_correcoes(driver, wait, carimbo: str, correcoes: dict[str, Any]) -> tuple[int, int, int]:
    return fluxo_base.aplicar_correcoes(driver, wait, carimbo, correcoes, CONFIG.ordem_campos)


def salvar_auditar_e_avancar(driver, wait, carimbo: str) -> None:
    fluxo_base.salvar_auditar_e_avancar(driver, wait, carimbo)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Correcao COPEL MT por carimbo no Consen")
    p.add_argument("--carimbo", action="append", default=[], help="Carimbo a carregar. Ex: --carimbo BB_2007424")
    p.add_argument("--carimbos-arquivo", type=str, default="", help="TXT com um carimbo por linha")
    p.add_argument("--xlsx", type=str, default=DEFAULT_XLSX, help="XLSX de OCR COPEL MT com os valores corrigidos")
    p.add_argument("--preparar-apenas", action="store_true", help="So valida o XLSX e monta a lista de correcoes, sem abrir o Consen")
    p.add_argument("--retomar-apos", type=str, default="", help="Retoma o lote apos este carimbo")
    p.add_argument("--reprocessar-ok", action="store_true", help="Nao pula carimbos ja concluidos com status ok no log de execucao")
    p.add_argument("--salvar", action="store_true", help="Salva a fatura apos aplicar correcoes cadastradas")
    p.add_argument("--sem-snapshot", action="store_true", help="Nao salva HTML/JSON da tela carregada")
    p.add_argument("--limite", type=int, default=0, help="Limita a quantidade de carimbos processados")
    return p.parse_args()


def carregar_lista_carimbos(args: argparse.Namespace) -> list[str]:
    return fluxo_base.carregar_lista_carimbos(args)


def main() -> int:
    args = parse_args()
    carimbos_filtro = {normalizar_carimbo(item) for item in list(args.carimbo or [])}
    if args.carimbos_arquivo:
        path = Path(args.carimbos_arquivo)
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                carimbos_filtro.add(normalizar_carimbo(line))

    if not args.xlsx:
        print("Informe --xlsx com a planilha corrigida da COPEL MT.")
        return 2

    xlsx = Path(args.xlsx)
    correcoes_xlsx = carregar_correcoes_de_xlsx(xlsx, carimbos_filtro)

    carimbos = carregar_lista_carimbos(args)
    if correcoes_xlsx:
        carimbos.extend(c for c in sorted(correcoes_xlsx) if c not in set(carimbos))
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
        print("Nenhum carimbo elegivel para correcao.")
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
            correcoes = dict(correcoes_xlsx.get(carimbo_norm, {}))
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
