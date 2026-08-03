#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Correcao CEMIG por carimbo.

Este fluxo:
1. Localiza os PDFs ja digitados pela raiz de carimbos digitados.
2. Reprocessa cada PDF com o OCR CEMIG atualizado.
3. Corrige no Consen campos de vencimento, consumo, demanda, subgrupo, retencoes e observacoes.

Por seguranca, nao salva por padrao. Use --salvar quando quiser efetivar.
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
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoAlertPresentException

DEFAULT_SAIDA_DIR = "//10.10.250.21/Energia/ARQUIVOS ENZO/CEMIG_pipeline_saida/BT/correcoes_por_carimbo"
DEFAULT_PDFS_ROOT = "//10.10.250.21/Energia/CONTROLE BB/DIGITADOS/CARIMBOS DIGITADOS"
DEFAULT_CARIMBOS_ARQUIVO = Path(__file__).with_name("cemig_bt_carimbos_100kwh.txt")
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
    from digitacao_consen.digitacao_consen_cemig import _normalizar_valor_obs  # noqa: E402
    from ocr.OCR_Cemig import processar_pdf  # noqa: E402
    from digitacao_consen.digitacao_consen_cemig import preencher_obs_multiplas  # noqa: E402
except ModuleNotFoundError:
    import correcao_fluxo_base as fluxo_base  # type: ignore  # noqa: E402
    from correcao_fluxo_base import (  # type: ignore  # noqa: E402
        CorrecaoFluxoConfig,
        formatar_valor_para_campo,
        log,
        warn,
    )
    from digitacao_consen_cemig import _normalizar_valor_obs  # type: ignore  # noqa: E402
    from OCR_Cemig import processar_pdf  # type: ignore  # noqa: E402
    from digitacao_consen_cemig import preencher_obs_multiplas  # type: ignore  # noqa: E402

LOGIN_URL = fluxo_base.LOGIN_URL
BASE_URL = LOGIN_URL.rsplit("login.php", 1)[0]
EDIT_HASH = "#bpg/gestao/fatura/editaFaturaCarimbo.php"
EDIT_URL = os.environ.get("CONSEN_EDITA_FATURA_CARIMBO_URL", f"{BASE_URL}index.php{EDIT_HASH}")

SAIDA_DIR = Path(os.environ.get("CONSEN_CORRECAO_SAIDA", DEFAULT_SAIDA_DIR))
PDFS_ROOT = Path(os.environ.get("CONSEN_CORRECAO_CEMIG_ROOT", DEFAULT_PDFS_ROOT))
FECHAR_AO_FINAL = os.environ.get("CONSEN_CORRECAO_FECHAR", "1").strip().lower() not in {"0", "false", "nao", "não"}
EXECUCAO_CSV = SAIDA_DIR / "correcao_execucao.csv"
CAMPOS_CRITICOS_TELA: tuple[str, ...] = (
    "btnSalvar",
    "fatConFPontaIndRegistrado",
    "fatConFPontaIndValorReais",
)

CAMPO_OCR_PARA_TELA: tuple[tuple[str, str], ...] = (
    ("dataVencimento", "fatDataVcto"),
    ("fatConFPontaIndRegistrado", "fatConFPontaIndRegistrado"),
    ("fatConFPontaIndFaturado", "fatConFPontaIndFaturado"),
    ("fatConFPontaIndValorReais", "fatConFPontaIndValorReais"),
    ("fatDemFPontaIndRegistrada", "fatDemFPontaIndRegistrada"),
    ("fatDemFPontaIndFaturada", "fatDemFPontaIndFaturada"),
    ("fatDemFPontaIndValorReais", "fatDemFPontaIndValorReais"),
    ("fatDemFPontaIndUltra", "fatDemFPontaIndUltra"),
    ("fatDemFPontaIndUltraValorReais", "fatDemFPontaIndUltraValorReais"),
    ("fatICMS", "fatICMS"),
    ("fatDescPisPercRetImposto", "fatDescPisPercRetImposto"),
    ("fatDescPisValRetImposto", "fatDescPisValRetImposto"),
    ("fatDescCofinsPercRetImposto", "fatDescCofinsPercRetImposto"),
    ("fatDescCofinsValRetImposto", "fatDescCofinsValRetImposto"),
    ("fatDescCsllPercRetImposto", "fatDescCsllPercRetImposto"),
    ("fatDescCsllValRetImposto", "fatDescCsllValRetImposto"),
    ("fatDescIrpjPercRetImposto", "fatDescIrpjPercRetImposto"),
    ("fatDescIrpjValRetImposto", "fatDescIrpjValRetImposto"),
)
ORDEM_CAMPOS_CORRECAO: tuple[str, ...] = (
    "cb-subgrupo",
    "dataVencimento",
    "fatConFPontaIndRegistrado",
    "fatConFPontaIndFaturado",
    "fatConFPontaIndValorReais",
    "fatDemFPontaIndRegistrada",
    "fatDemFPontaIndFaturada",
    "fatDemFPontaIndValorReais",
    "fatDemFPontaIndUltra",
    "fatDemFPontaIndUltraValorReais",
    "fatICMS",
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
    ordem_campos=ORDEM_CAMPOS_CORRECAO,
    fechar_ao_final=FECHAR_AO_FINAL,
)

CORRECOES_CEMIG_BT: dict[str, dict[str, Any]] = {
    "2007235": {
        "fatDescPisPercRetImposto": "0,65",
        "fatDescCofinsPercRetImposto": "3,00",
        "fatDescCsllPercRetImposto": "1,00",
        "fatDescIrpjPercRetImposto": "-1",
    },
    "2007477": {
        "fatDemFPontaIndUltra": "8,00",
        "fatDemFPontaIndUltraValorReais": "478,56",
    },
    "2007484": {
        "cb-subgrupo": "AS [<2,3kV]",
        "fatDemFPontaIndRegistrada": "302,00",
    },
    "2007485": {
        "fatDemFPontaIndUltra": "3,00",
        "fatDemFPontaIndUltraValorReais": "179,45",
    },
    "2007969": {
        "dataVencimento": "18/05/2026",
    },
}

CARIMBOS_RECRIAR_OBS: set[str] = {
    "2007486",
    "2007494",
    "2007495",
    "2007515",
    "2007518",
    "2007519",
    "2007521",
    "2007528",
    "2007530",
    "2007538",
    "2007540",
    "2007545",
    "2007547",
    "2007558",
    "2007562",
    "2007563",
    "2007564",
    "2007570",
    "2007575",
    "2007579",
    "2007580",
}

CAMPO_TELA_ALTERNATIVOS: dict[str, tuple[str, ...]] = {
    "cb-subgrupo": ("cb-dados-contratuais-fatura-subgrupo", "cb-subgrupo", "cadSubGrupoCod"),
    "dataVencimento": ("dataVencimento", "fatDataVcto"),
    "fatConFPontaIndRegistrado": ("txt-consumo-registrada-fpind", "fatConFPontaIndRegistrado"),
    "fatConFPontaIndFaturado": ("txt-consumo-faturada-fpind", "fatConFPontaIndFaturado", "fatConFPontaIndutivo"),
    "fatConFPontaIndValorReais": ("txt-consumo-fpind-valor-reais", "fatConFPontaIndValorReais"),
    "fatDemFPontaIndRegistrada": ("txt-demandas-registrada-fpind", "fatDemFPontaIndRegistrada"),
    "fatDemFPontaIndFaturada": ("txt-demandas-faturada-fpind", "fatDemFPontaIndFaturada", "fatDemFPontaIndutivo"),
    "fatDemFPontaIndValorReais": ("txt-demandas-fpind-valor-reais", "fatDemFPontaIndValorReais"),
    "fatDemFPontaIndUltra": ("txt-demandas-ultrapassagem-faturada-fpind", "fatDemFPontaIndUltra"),
    "fatDemFPontaIndUltraValorReais": ("txt-demandas-ultrapassagem-fpind-valor-reais", "fatDemFPontaIndUltraValorReais"),
}


def normalizar_carimbo(carimbo: str) -> str:
    return fluxo_base.normalizar_carimbo(carimbo)


def valor_vazio(valor: Any) -> bool:
    return fluxo_base.valor_vazio(valor)


def formatar_valor_correcao(header: str, valor: Any) -> str:
    return formatar_valor_para_campo(header, valor, "text")


def eh_linha_cemig(row: dict[str, Any]) -> bool:
    return not str(row.get("ERRO") or "").strip()


def correcoes_da_linha_ocr(row: dict[str, Any]) -> dict[str, str]:
    correcoes: dict[str, str] = {}
    for campo_tela, header in CAMPO_OCR_PARA_TELA:
        valor = row.get(header)
        if valor_vazio(valor):
            continue
        correcoes[campo_tela] = formatar_valor_correcao(header, valor)
    subgrupo = str(row.get("cadSubGrupoCod") or "").strip().upper()
    if subgrupo == "AS":
        correcoes["cb-subgrupo"] = "AS [<2,3kV]"
    elif subgrupo:
        correcoes["cb-subgrupo"] = subgrupo
    return correcoes


def observacoes_da_linha_ocr(row: dict[str, Any]) -> list[tuple[str, float]]:
    agregados: dict[str, float] = {}
    ordem: list[str] = []
    pares_vistos: set[tuple[str, float]] = set()
    for i in range(1, 6):
        cod = str(row.get(f"obsCod_{i}") or "").strip()
        if not cod or cod == "0":
            continue
        valor = _normalizar_valor_obs(cod, row.get(f"obsValor_{i}"))
        valor = round(valor, 2)
        if abs(valor) <= 0.004:
            continue
        par = (cod, valor)
        if par in pares_vistos:
            continue
        pares_vistos.add(par)
        if cod not in agregados:
            agregados[cod] = 0.0
            ordem.append(cod)
        agregados[cod] = round(agregados[cod] + valor, 2)
    pares: list[tuple[str, float]] = []
    for cod in ordem:
        valor = round(agregados[cod], 2)
        if abs(valor) > 0.004:
            pares.append((cod, valor))
    return pares


def inferir_tipo_pasta(pdf: Path) -> str:
    partes = [pdf.parent.name.upper(), pdf.parent.parent.name.upper()]
    return "mt" if any(parte == "MT" or parte.endswith("\\MT") for parte in partes) else "bt"


def normalizar_correcoes(correcoes: dict[str, Any]) -> dict[str, Any]:
    saida = dict(correcoes)
    for campo in (
        "fatDescPisValRetImposto",
        "fatDescCofinsValRetImposto",
        "fatDescCsllValRetImposto",
        "fatDescIrpjValRetImposto",
    ):
        valor = saida.get(campo)
        if valor_vazio(valor):
            continue
        try:
            num = float(str(valor).replace(".", "").replace(",", "."))
            saida[campo] = formatar_valor_correcao(campo, -abs(num))
        except Exception:
            pass
    return saida


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


def carregar_correcoes_de_raiz_pdfs(raiz: Path, carimbos_filtro: set[str]) -> dict[str, dict[str, Any]]:
    encontrados = localizar_pdfs_por_carimbo(raiz, carimbos_filtro)
    correcoes: dict[str, dict[str, Any]] = {}
    linhas_log: list[dict[str, Any]] = []

    for carimbo in sorted(carimbos_filtro):
        pdf = encontrados.get(carimbo)
        if not pdf:
            linhas_log.append({
                "carimbo": carimbo,
                "arquivo": "",
                "status": "nao_encontrado",
                "tarifa": "",
                "campos": 0,
                "registrado": "",
                "faturado": "",
                "valor_reais": "",
            })
            continue

        try:
            row = processar_pdf(str(pdf), inferir_tipo_pasta(pdf))
        except Exception as exc:
            linhas_log.append({
                "carimbo": carimbo,
                "arquivo": str(pdf),
                "status": f"erro_ocr:{type(exc).__name__}",
                "tarifa": "",
                "campos": 0,
                "registrado": "",
                "faturado": "",
                "valor_reais": "",
            })
            warn(f"[OCR CORRECAO] {pdf.name}: erro {type(exc).__name__}: {exc}")
            continue

        tarifa = str(row.get("TARIFA_DETECTADA") or "").strip().upper()
        if not eh_linha_cemig(row):
            linhas_log.append({
                "carimbo": carimbo,
                "arquivo": str(pdf),
                "status": "pulado_linha_com_erro",
                "tarifa": tarifa,
                "campos": 0,
                "registrado": "",
                "faturado": "",
                "valor_reais": "",
            })
            continue

        mapa = correcoes_da_linha_ocr(row)
        obs = observacoes_da_linha_ocr(row)
        payload: dict[str, Any] = normalizar_correcoes(mapa)
        if obs:
            payload["__obs__"] = obs
        if payload:
            correcoes[carimbo] = payload
        linhas_log.append({
            "carimbo": carimbo,
            "arquivo": str(pdf),
            "status": "ok" if payload else "sem_campos",
            "tarifa": tarifa,
            "campos": len(mapa) + len(obs),
            "registrado": row.get("fatConFPontaIndRegistrado", ""),
            "faturado": row.get("fatConFPontaIndFaturado", ""),
            "valor_reais": row.get("fatConFPontaIndValorReais", ""),
        })

    salvar_log_preparacao(linhas_log)
    return correcoes


def salvar_log_preparacao(linhas: list[dict[str, Any]]) -> None:
    SAIDA_DIR.mkdir(parents=True, exist_ok=True)
    path = SAIDA_DIR / "preparacao_correcao_cemig_bt.csv"
    campos = ["carimbo", "arquivo", "status", "tarifa", "campos", "registrado", "faturado", "valor_reais"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=campos, delimiter=";")
        writer.writeheader()
        writer.writerows(linhas)
    log(f"Log de preparacao salvo: {path}")

    carimbos_ok = sorted({str(l.get("carimbo") or "").strip() for l in linhas if l.get("status") == "ok"})
    txt_path = SAIDA_DIR / "carimbos_preparados_cemig_bt.txt"
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


def _preencher_observacoes(driver, wait, pares: list[tuple[str, float]]) -> tuple[int, int]:
    if not pares:
        return 0, 0
    dados_obs: dict[str, Any] = {}
    for idx, (cod, val) in enumerate(pares, start=1):
        dados_obs[f"obsCod_{idx}"] = str(cod)
        dados_obs[f"obsValor_{idx}"] = val
    try:
        preencher_obs_multiplas(driver, wait, dados_obs)
        return len(pares), len(pares)
    except Exception as exc:
        warn(f"[OBS] Falha ao recriar observacoes: {type(exc).__name__} - {exc}")
        return 0, 0


def _aceitar_alerta_se_existir(driver) -> None:
    try:
        alert = driver.switch_to.alert
        alert.accept()
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


def _aplicar_campo_com_aliases(driver, wait, campo_logico: str, valor: Any) -> tuple[int, int]:
    candidatos = CAMPO_TELA_ALTERNATIVOS.get(campo_logico, (campo_logico,))
    for candidato in candidatos:
        try:
            elemento = fluxo_base.localizar_input_exato(driver, wait, candidato)
            valor_atual = (elemento.get_attribute("value") or "").strip()
            if valor_atual == str(valor).strip():
                return 0, 1
            ok = fluxo_base.preencher_elemento_html(driver, elemento, valor)
            return (1, 1) if ok else (0, 0)
        except Exception:
            continue
    warn(f"[CORRECAO] Campo {campo_logico} pulado: nenhum alias localizado ({', '.join(candidatos)})")
    return 0, 0


def aplicar_correcoes(driver, wait, carimbo: str, correcoes: dict[str, Any]) -> tuple[int, int, int]:
    payload = dict(correcoes)
    obs = list(payload.pop("__obs__", []))
    campos = normalizar_correcoes(payload)
    aplicadas = 0
    confirmadas = 0
    if obs and normalizar_carimbo(carimbo) in CARIMBOS_RECRIAR_OBS:
        removidas = _limpar_observacoes_existentes(driver)
        log(f"[OBS] BB_{normalizar_carimbo(carimbo)}: observacoes removidas antes da recriacao = {removidas}")
    ordem = [campo for campo in CONFIG.ordem_campos if campo in campos]
    ordem.extend(campo for campo in campos if campo not in ordem)
    for campo in ordem:
        qtd, ok = _aplicar_campo_com_aliases(driver, wait, campo, campos[campo])
        aplicadas += qtd
        confirmadas += ok
    total_campos = len(ordem)
    obs_aplicadas, obs_confirmadas = _preencher_observacoes(driver, wait, obs)
    total = total_campos + len(obs)
    return aplicadas + obs_aplicadas, confirmadas + obs_confirmadas, total


def salvar_auditar_e_avancar(driver, wait, carimbo: str) -> None:
    fluxo_base.salvar_auditar_e_avancar(driver, wait, carimbo)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Correcao CEMIG BT por carimbo no Consen")
    p.add_argument("--carimbo", action="append", default=[], help="Carimbo a carregar. Ex: --carimbo BB_2004331")
    p.add_argument(
        "--carimbos-arquivo",
        type=str,
        default=str(DEFAULT_CARIMBOS_ARQUIVO),
        help="TXT com um carimbo por linha. Padrao: lista 100 kWh CEMIG BT.",
    )
    p.add_argument("--raiz-pdfs", type=str, default=str(PDFS_ROOT), help="Raiz dos PDFs ja digitados.")
    p.add_argument("--preparar-apenas", action="store_true", help="So valida o OCR e monta a lista de correcoes.")
    p.add_argument("--retomar-apos", type=str, default="", help="Retoma o lote apos este carimbo.")
    p.add_argument("--reprocessar-ok", action="store_true", help="Nao pula carimbos ja concluidos com status ok.")
    p.add_argument("--salvar", action="store_true", help="Salva a fatura apos aplicar correcoes.")
    p.add_argument("--sem-snapshot", action="store_true", help="Nao salva HTML/JSON da tela carregada.")
    p.add_argument("--limite", type=int, default=0, help="Limita a quantidade de carimbos processados.")
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
        if not path.exists():
            print(f"Arquivo de carimbos nao encontrado: {path}")
            return 2
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                carimbos_filtro.add(normalizar_carimbo(line))

    if not carimbos_filtro:
        print("Informe ao menos um --carimbo ou um --carimbos-arquivo valido.")
        return 2

    raiz_pdfs = Path(args.raiz_pdfs)
    if not raiz_pdfs.exists():
        print(f"Raiz de PDFs nao encontrada: {raiz_pdfs}")
        return 2

    correcoes_ocr = carregar_correcoes_de_raiz_pdfs(raiz_pdfs, carimbos_filtro)
    carimbos = carregar_lista_carimbos(args)
    if not carimbos:
        carimbos = sorted(correcoes_ocr)
    else:
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
        print("Nenhum carimbo pendente para processar.")
        return 0

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
            correcoes = dict(CORRECOES_CEMIG_BT.get(carimbo_norm, {}))
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
