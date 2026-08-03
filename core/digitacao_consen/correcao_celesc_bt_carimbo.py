#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Correcao CELESC BT por carimbo.

Este fluxo:
1. Localiza os PDFs dos carimbos informados.
2. Reprocessa cada PDF com o OCR CELESC BT atualizado.
3. Corrige apenas os campos combinados para BT:
   - tarifa/subgrupo, quando o PDF confirmar BT B3;
   - limpa ponta e regrava fora ponta;
   - cofins aliquota;
   - valor nota fiscal;
   - multas diversas;
   - energia injetada usina F. ponta;
   - valor em R$ da usina injetada F. ponta;
   - saldo acumulado de usina F. ponta;
   - percentuais/valores de retencao.

Por seguranca, nao salva por padrao. Use --salvar quando quiser efetivar.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path
from typing import Any

from selenium.common.exceptions import InvalidSessionIdException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

DEFAULT_SAIDA_DIR = "//10.10.250.21/Energia/ARQUIVOS ENZO/CELESC_BT_pipeline_saida/correcoes_por_carimbo"
DEFAULT_CARIMBOS_ARQUIVO = Path(__file__).with_name("celesc_bt_carimbos_correcao.txt")
LOCAL_PDFS_ROOT = Path(__file__).resolve().parent.parent / "downloaders" / "celesc" / "downloads_bt"
NETWORK_PDFS_ROOT = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD CELESC")
DEFAULT_PDFS_ROOT = LOCAL_PDFS_ROOT if LOCAL_PDFS_ROOT.exists() else NETWORK_PDFS_ROOT
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
    from ocr.ocr_celesc_bt import _classificar_pdf_bt, extrair_campos  # noqa: E402
except ModuleNotFoundError:
    import correcao_fluxo_base as fluxo_base  # type: ignore  # noqa: E402
    from correcao_fluxo_base import (  # type: ignore  # noqa: E402
        CorrecaoFluxoConfig,
        formatar_valor_para_campo,
        log,
        warn,
    )
    from ocr_celesc_bt import _classificar_pdf_bt, extrair_campos  # type: ignore  # noqa: E402

LOGIN_URL = fluxo_base.LOGIN_URL
BASE_URL = LOGIN_URL.rsplit("login.php", 1)[0]
EDIT_HASH = "#bpg/gestao/fatura/editaFaturaCarimbo.php"
EDIT_URL = os.environ.get("CONSEN_EDITA_FATURA_CARIMBO_URL", f"{BASE_URL}index.php{EDIT_HASH}")
EDITA_TAB_URL_BASE = f"{BASE_URL}index.php#bpg/gestao/fatura/editaTabFatura.php?carimbo="

SAIDA_DIR = Path(os.environ.get("CONSEN_CORRECAO_SAIDA", DEFAULT_SAIDA_DIR))
PDFS_ROOT = Path(os.environ.get("CONSEN_CORRECAO_CELESC_BT_ROOT", str(DEFAULT_PDFS_ROOT)))
FECHAR_AO_FINAL = os.environ.get("CONSEN_CORRECAO_FECHAR", "1").strip().lower() not in {"0", "false", "nao", "não"}
EXECUCAO_CSV = SAIDA_DIR / "correcao_execucao.csv"
CAMPOS_CRITICOS_TELA: tuple[str, ...] = (
    "cb-tarifa",
    "fatConFPontaIndRegistrado",
    "fatValorNFiscal",
)

CAMPO_OCR_PARA_TELA: tuple[tuple[str, str], ...] = (
    ("fatConFPontaIndRegistrado", "fatConFPontaIndRegistrado"),
    ("fatConFPontaIndutivo", "fatConFPontaIndFaturado"),
    ("fatConFPontaIndValorReais", "fatConFPontaIndValorReais"),
    ("fatICMS", "fatICMS"),
    ("fatDesIcmsAliquota", "fatDesIcmsAliquota"),
    ("fatDescPisAliquota", "fatDescPisAliquota"),
    ("fatDesCofinsAliquota", "fatDescCofinsAliquota"),
    ("fatValorNFiscal", "fatValorNotaFiscal"),
    ("fatMultasDiversas", "fatMultasDiversas"),
    ("fatConFPontaInjetadoUsina", "fatConFPontaInjetadoUsina"),
    ("fatConFPontaInjetadoValorReais", "fatConFPontaInjetadoValorReais"),
    ("fatConFPontaInjetadoUsinaSaldo", "fatConFPontaInjetadoUsinaSaldoAcumulado"),
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
    "cb-tarifa",
    "cb-subgrupo",
    # Demanda MT — zerar antes de preencher campos BT
    "txtDemContratadaPonta",
    "txtDemContratadaFPonta",
    "fatDemFPontaIndRegistrada",
    "fatDemFPontaIndutivo",
    "fatDemFPontaIndValorReais",
    "fatDemFPontaIndUltra",
    "fatDemFPontaIndUltraValorReais",
    "fatDemPontaExcRegistrada",
    "fatDemPontaExc",
    "fatDemPontaExcValorReais",
    "fatDemFPontaExcRegistrada",
    "fatDemFPontaExc",
    "fatDemFPontaExcValorReais",
    "fatConPontaExcRegistrado",
    "fatConPontaExc",
    "fatConPontaExcValorReais",
    # Consumo BT
    "fatConPontaRegistrado",
    "fatConPonta",
    "fatConPontaValorReais",
    "fatConFPontaIndRegistrado",
    "fatConFPontaIndutivo",
    "fatConFPontaIndValorReais",
    "fatConFPontaIndExcRegistrado",
    "fatConFPontaIndExc",
    "fatConFPontaIndExcValorReais",
    "fatConFPontaCapExcRegistrado",
    "fatConFPontaCapExc",
    "fatConFPontaCapExcValorReais",
    "fatICMS",
    "fatDesIcmsAliquota",
    "fatDescPisAliquota",
    "fatDesCofinsAliquota",
    "fatValorNFiscal",
    "fatMultasDiversas",
    "fatConFPontaInjetadoUsina",
    "fatConFPontaInjetadoValorReais",
    "fatConFPontaInjetadoUsinaSaldo",
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

CORRECOES_CELESC_BT: dict[str, dict[str, Any]] = {}


def normalizar_carimbo(carimbo: str) -> str:
    return fluxo_base.normalizar_carimbo(carimbo)


def valor_vazio(valor: Any) -> bool:
    return fluxo_base.valor_vazio(valor)


def formatar_valor_correcao(header: str, valor: Any) -> str:
    return formatar_valor_para_campo(header, valor, "text")


def eh_linha_bt_celesc(row: dict[str, Any]) -> bool:
    if str(row.get("ERRO") or "").strip():
        return False
    instalacao = str(row.get("Instalacao") or "").strip()
    carimbo = str(row.get("fatCarimbo") or "").strip()
    return bool(instalacao and carimbo)


def correcoes_da_linha_ocr(row: dict[str, Any], bt_confirmado: bool) -> dict[str, str]:
    correcoes: dict[str, str] = {}

    if bt_confirmado:
        zero = formatar_valor_correcao("fatConPontaRegistrado", 0)
        correcoes.update({
            "cb-tarifa": "Convencional",
            "cb-subgrupo": "B3 [<2,3kV]",
            # BT nao usa ponta: qualquer valor indevido nessa familia deve voltar zerado.
            "fatConPontaRegistrado": zero,
            "fatConPonta": zero,
            "fatConPontaValorReais": zero,
            # Demanda MT — campos preenchidos pelo pipeline de MT incorretamente.
            "txtDemContratadaPonta": zero,
            "txtDemContratadaFPonta": zero,
            "fatDemFPontaIndRegistrada": zero,
            "fatDemFPontaIndutivo": zero,
            "fatDemFPontaIndValorReais": zero,
            "fatDemFPontaIndUltra": zero,
            "fatDemFPontaIndUltraValorReais": zero,
            "fatDemPontaExcRegistrada": zero,
            "fatDemPontaExc": zero,
            "fatDemPontaExcValorReais": zero,
            "fatDemFPontaExcRegistrada": zero,
            "fatDemFPontaExc": zero,
            "fatDemFPontaExcValorReais": zero,
            # Reativo excedente consumo MT — zerar ao corrigir para BT.
            "fatConPontaExcRegistrado": zero,
            "fatConPontaExc": zero,
            "fatConPontaExcValorReais": zero,
            "fatConFPontaIndExcRegistrado": zero,
            "fatConFPontaIndExc": zero,
            "fatConFPontaIndExcValorReais": zero,
            "fatConFPontaCapExcRegistrado": zero,
            "fatConFPontaCapExc": zero,
            "fatConFPontaCapExcValorReais": zero,
        })

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
            linhas_log.append({
                "carimbo": carimbo,
                "arquivo": "",
                "status": "nao_encontrado",
                "campos": 0,
                "fponta_reg": "",
                "fponta_fat": "",
                "fponta_valor": "",
                "valor_nf": "",
                "cofins_aliq": "",
                "multas_diversas": "",
                "qtd_usina_fp": "",
                "valor_usina_fp_reais": "",
                "saldo_usina_fp": "",
                "classificacao_bt": "",
            })
            continue

        bt_confirmado, classificacao_bt = _classificar_pdf_bt(pdf)

        try:
            row = extrair_campos(pdf)
        except Exception as exc:
            linhas_log.append({
                "carimbo": carimbo,
                "arquivo": str(pdf),
                "status": f"erro_ocr:{type(exc).__name__}",
                "campos": 0,
                "fponta_reg": "",
                "fponta_fat": "",
                "fponta_valor": "",
                "valor_nf": "",
                "cofins_aliq": "",
                "multas_diversas": "",
                "qtd_usina_fp": "",
                "valor_usina_fp_reais": "",
                "saldo_usina_fp": "",
                "classificacao_bt": classificacao_bt,
            })
            warn(f"[OCR CORRECAO] {pdf.name}: erro {type(exc).__name__}: {exc}")
            continue

        if not eh_linha_bt_celesc(row):
            linhas_log.append({
                "carimbo": carimbo,
                "arquivo": str(pdf),
                "status": "pulado_nao_bt_celesc_ou_erro",
                "campos": 0,
                "fponta_reg": "",
                "fponta_fat": "",
                "fponta_valor": "",
                "valor_nf": "",
                "cofins_aliq": "",
                "multas_diversas": "",
                "qtd_usina_fp": "",
                "valor_usina_fp_reais": "",
                "saldo_usina_fp": "",
                "classificacao_bt": classificacao_bt,
            })
            continue

        mapa = correcoes_da_linha_ocr(row, bt_confirmado=bt_confirmado)
        if mapa:
            correcoes[carimbo] = mapa
        linhas_log.append({
            "carimbo": carimbo,
            "arquivo": str(pdf),
            "status": "ok" if mapa else "sem_campos",
            "campos": len(mapa),
            "fponta_reg": row.get("fatConFPontaIndRegistrado", ""),
            "fponta_fat": row.get("fatConFPontaIndFaturado", ""),
            "fponta_valor": row.get("fatConFPontaIndValorReais", ""),
            "valor_nf": row.get("fatValorNotaFiscal", ""),
            "cofins_aliq": row.get("fatDescCofinsAliquota", ""),
            "multas_diversas": row.get("fatMultasDiversas", ""),
            "qtd_usina_fp": row.get("fatConFPontaInjetadoUsina", ""),
            "valor_usina_fp_reais": row.get("fatConFPontaInjetadoValorReais", ""),
            "saldo_usina_fp": row.get("fatConFPontaInjetadoUsinaSaldoAcumulado", ""),
            "classificacao_bt": classificacao_bt,
        })

    salvar_log_preparacao(linhas_log)
    return correcoes


def salvar_log_preparacao(linhas: list[dict[str, Any]]) -> None:
    SAIDA_DIR.mkdir(parents=True, exist_ok=True)
    path = SAIDA_DIR / "preparacao_correcao_celesc_bt.csv"
    campos = [
        "carimbo",
        "arquivo",
        "status",
        "campos",
        "fponta_reg",
        "fponta_fat",
        "fponta_valor",
        "valor_nf",
        "cofins_aliq",
        "multas_diversas",
        "qtd_usina_fp",
        "valor_usina_fp_reais",
        "saldo_usina_fp",
        "classificacao_bt",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=campos, delimiter=";")
        writer.writeheader()
        writer.writerows(linhas)
    log(f"Log de preparacao salvo: {path}")

    carimbos_ok = sorted({str(l.get("carimbo") or "").strip() for l in linhas if l.get("status") == "ok"})
    txt_path = SAIDA_DIR / "carimbos_preparados_celesc_bt.txt"
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
    carimbo_norm = normalizar_carimbo(carimbo)
    log(f"Carregando fatura pelo carimbo BB_{carimbo_norm}...")

    fluxo_base.preencher_input_texto(driver, wait, "carimbo", carimbo_norm, pausa_antes=0.2)
    fluxo_base.clicar_botao(
        driver,
        wait,
        [
            (By.ID, "botaoCarregar"),
            (By.NAME, "botaoCarregar"),
            (By.CSS_SELECTOR, "button#botaoCarregar"),
            (By.CSS_SELECTOR, "input#botaoCarregar"),
            (By.CSS_SELECTOR, "button[type='submit']"),
            (By.CSS_SELECTOR, "input[type='submit']"),
        ],
        "Carregar por carimbo",
    )

    fluxo_base._aguardar_sem_spinner(driver, timeout=12, min_wait=0.5)

    try:
        WebDriverWait(driver, 12).until(lambda d: "editaTabFatura" in (d.current_url or ""))
    except Exception:
        pass

    url = driver.current_url or ""
    if "editaTabFatura" not in url:
        url = f"{EDITA_TAB_URL_BASE}{carimbo_norm}"
        log(f"Forcando navegacao direta para a rota de edicao: {url}")
        driver.get(url)
        fluxo_base._aguardar_sem_spinner(driver, timeout=15, min_wait=0.6)
    elif f"carimbo={carimbo_norm}" in url:
        log("Recarregando a URL final da edicao para estabilizar a tela do Consen...")
        driver.get(url)
        fluxo_base._aguardar_sem_spinner(driver, timeout=15, min_wait=0.6)

    seletores_prontos = (
        (By.ID, "btnSalvar"),
        (By.ID, "cb-tarifa"),
        (By.ID, "fatConFPontaIndRegistrado"),
        (By.ID, "fatValorNFiscal"),
    )
    for by, sel in seletores_prontos:
        try:
            WebDriverWait(driver, 15).until(EC.presence_of_element_located((by, sel)))
            log(f"Fatura carregada para edicao (marcador: {sel}).")
            return
        except Exception:
            continue

    warn(f"Nao confirmei a tela de edicao apos carregar. URL atual: {driver.current_url or ''}")
    raise TimeoutError(f"Fatura BB_{carimbo_norm} nao abriu a tela de edicao.")


def salvar_snapshot(driver, carimbo: str) -> None:
    fluxo_base.salvar_snapshot(driver, CONFIG.saida_dir, carimbo)


def aplicar_correcoes(driver, wait, carimbo: str, correcoes: dict[str, Any]) -> tuple[int, int, int]:
    return fluxo_base.aplicar_correcoes(driver, wait, carimbo, correcoes, CONFIG.ordem_campos)


def salvar_auditar_e_avancar(driver, wait, carimbo: str) -> None:
    fluxo_base.salvar_auditar_e_avancar(driver, wait, carimbo)


def fechar_driver_seguro(driver) -> None:
    if not driver:
        return
    try:
        driver.quit()
    except Exception:
        pass


def eh_erro_sessao(exc: Exception) -> bool:
    if isinstance(exc, InvalidSessionIdException):
        return True
    if isinstance(exc, WebDriverException):
        msg = str(exc).lower()
        return "invalid session id" in msg or "not connected to devtools" in msg
    return False


def eh_erro_recuperavel(exc: Exception) -> bool:
    if eh_erro_sessao(exc):
        return True
    if isinstance(exc, TimeoutError):
        msg = str(exc).lower()
        return (
            "tela de edicao por carimbo nao carregou" in msg
            or "nao abriu a tela de edicao" in msg
        )
    return False


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Correcao CELESC BT por carimbo no Consen")
    p.add_argument("--carimbo", action="append", default=[], help="Carimbo a carregar. Ex: --carimbo BB_2004259")
    p.add_argument(
        "--carimbos-arquivo",
        type=str,
        default="",
        help=(
            "TXT com um carimbo por linha. Quando omitido, somente os --carimbo "
            "informados serao processados."
        ),
    )
    p.add_argument("--raiz-pdfs", type=str, default=str(PDFS_ROOT), help="Raiz dos PDFs para reprocessar no OCR.")
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
            sucesso = False
            for tentativa in range(1, 4):
                try:
                    registrar_execucao(carimbo, "iniciado", f"tentativa {tentativa}")
                    abrir_tela_edicao_carimbo(driver, wait)
                    carregar_fatura_por_carimbo(driver, wait, carimbo)

                    if not args.sem_snapshot:
                        salvar_snapshot(driver, carimbo)

                    carimbo_norm = normalizar_carimbo(carimbo)
                    correcoes = dict(CORRECOES_CELESC_BT.get(carimbo_norm, {}))
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

                    sucesso = True
                    break
                except Exception as exc:
                    if tentativa < 3 and eh_erro_recuperavel(exc):
                        warn(
                            f"BB_{carimbo}: erro recuperavel na tentativa {tentativa}/3 "
                            f"({type(exc).__name__}). Reabrindo o navegador para tentar novamente..."
                        )
                        registrar_execucao(carimbo, "erro_recuperavel", f"tentativa {tentativa}: {type(exc).__name__}")
                        fechar_driver_seguro(driver)
                        driver, wait = abrir_driver_logado()
                        continue
                    registrar_execucao(carimbo, "erro", f"{type(exc).__name__}: {exc}")
                    raise
            if not sucesso:
                raise RuntimeError(f"Falha ao processar BB_{carimbo}.")
        return 0
    finally:
        if driver and CONFIG.fechar_ao_final:
            fechar_driver_seguro(driver)


if __name__ == "__main__":
    raise SystemExit(main())
