#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Correcao COELBA MT por carimbo.

Corrige no Consen os campos ausentes/errados nas faturas MT COELBA
processadas antes das fixes do OCR (2026-06-19):
  - fatDescontoFio       = 50.0  (fio kW)
  - fatDescontoFioKWh    = 47.48 (fio kWh)
  - fatTributoFederalPerc/Val quando ha duas linhas (5,85% + 9,45%) ->
    mantém 9,45% e soma os valores negativos
  - componentes IRPJ/PIS/COFINS/CSLL redistribuídos a partir do total

Por seguranca, nao salva por padrao. Use --salvar para efetivar.
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

DEFAULT_SAIDA_DIR = "//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD NEOENERGIA/COELBA/correcoes_mt"
DEFAULT_PDFS_ROOT = "//10.10.250.21/Energia/CONTROLE BB/DIGITADOS/CARIMBOS DIGITADOS"
DEFAULT_PDFS_COELBA = "//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD NEOENERGIA/COELBA"
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
    from ocr import ocr_neoenergia as ocr_neo
except ModuleNotFoundError:
    import correcao_fluxo_base as fluxo_base  # type: ignore
    from correcao_fluxo_base import (  # type: ignore
        CorrecaoFluxoConfig,
        formatar_valor_para_campo,
        log,
        warn,
    )
    import ocr_neoenergia as ocr_neo  # type: ignore

LOGIN_URL = fluxo_base.LOGIN_URL
BASE_URL = LOGIN_URL.rsplit("login.php", 1)[0]
EDIT_HASH = "#bpg/gestao/fatura/editaFaturaCarimbo.php"
EDIT_URL = os.environ.get("CONSEN_EDITA_FATURA_CARIMBO_URL", f"{BASE_URL}index.php{EDIT_HASH}")

SAIDA_DIR = Path(os.environ.get("CONSEN_CORRECAO_SAIDA", DEFAULT_SAIDA_DIR))
PDFS_ROOT = Path(os.environ.get("CONSEN_CORRECAO_COELBA_MT_ROOT", DEFAULT_PDFS_ROOT))
FECHAR_AO_FINAL = os.environ.get("CONSEN_CORRECAO_FECHAR", "1").strip().lower() not in {"0", "false", "nao", "não"}
EXECUCAO_CSV = SAIDA_DIR / "correcao_execucao.csv"

CAMPOS_CRITICOS_TELA: tuple[str, ...] = (
    "btnSalvar",
    "instalacao",
)

# (campo_logico, header_no_ocr)
# COELBA MT: tributo federal agregado + desconto fio; individuais zerados
CAMPO_OCR_PARA_LOGICO: tuple[tuple[str, str], ...] = (
    ("fatICMS",                    "fatICMS"),
    ("fatDescontoFio",              "fatDescontoFio"),
    ("fatDescontoFioKWh",           "fatDescontoFioKWh"),
    ("fatTributoFederalPerc",       "fatTributoFederalPerc"),
    ("fatTributoFederalVal",        "fatTributoFederalVal"),
    # individuais — sempre zerados em COELBA MT
    ("fatDescPisPercRetImposto",    "_zero"),
    ("fatDescPisValRetImposto",     "_zero"),
    ("fatDescCofinsPercRetImposto", "_zero"),
    ("fatDescCofinsValRetImposto",  "_zero"),
    ("fatDescCsllPercRetImposto",   "_zero"),
    ("fatDescCsllValRetImposto",    "_zero"),
    ("fatDescIrpjPercRetImposto",   "_zero"),
    ("fatDescIrpjValRetImposto",    "_zero"),
)

CAMPO_TELA_ALTERNATIVOS: dict[str, tuple[str, ...]] = {
    "fatICMS":                     ("fatICMS",),
    "fatDescontoFio":              ("fatDescontoFio",),
    "fatDescontoFioKWh":           ("fatDescontoFioKWh",),
    "fatTributoFederalPerc":       ("fatTributoFederalPerc",),
    "fatTributoFederalVal":        ("fatTributoFederalVal",),
    "fatDescPisPercRetImposto":    ("fatDescPisPercRetImposto",),
    "fatDescPisValRetImposto":     ("fatDescPisValRetImposto",),
    "fatDescCofinsPercRetImposto": ("fatDescCofinsPercRetImposto",),
    "fatDescCofinsValRetImposto":  ("fatDescCofinsValRetImposto",),
    "fatDescCsllPercRetImposto":   ("fatDescCsllPercRetImposto",),
    "fatDescCsllValRetImposto":    ("fatDescCsllValRetImposto",),
    "fatDescIrpjPercRetImposto":   ("fatDescIrpjPercRetImposto",),
    "fatDescIrpjValRetImposto":    ("fatDescIrpjValRetImposto",),
}

_ZERO = "0,00"

ORDEM_CAMPOS_CORRECAO: tuple[str, ...] = tuple(c for c, _ in CAMPO_OCR_PARA_LOGICO)
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
        m = re.fullmatch(r"(\d{4})-(\d{2})", str(parte).strip())
        if m:
            return int(m.group(1)), int(m.group(2)), 0
    return (0, 0, 0)


def _correcoes_da_linha_ocr(rec: dict[str, Any]) -> dict[str, Any]:
    correcoes: dict[str, Any] = {}
    for campo_logico, header in CAMPO_OCR_PARA_LOGICO:
        if header == "_zero":
            correcoes[campo_logico] = _ZERO
            continue
        valor = rec.get(header)
        if valor_vazio(valor):
            continue
        try:
            if float(valor) == 0.0:
                continue
        except (TypeError, ValueError):
            pass
        correcoes[campo_logico] = formatar_valor_correcao(header, valor)
    return correcoes


def localizar_pdfs_por_carimbo(raizes: list[Path], carimbos_filtro: set[str]) -> dict[str, Path]:
    encontrados: dict[str, Path] = {}
    if not carimbos_filtro:
        return encontrados

    import os as _os
    for raiz in raizes:
        for atual, _, arquivos in _os.walk(str(raiz)):
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


def _mes_ano_do_caminho(pdf: Path) -> tuple[int, int]:
    for parte in (pdf.parent.name, pdf.parent.parent.name, pdf.parent.parent.parent.name):
        m = re.fullmatch(r"(\d{4})-(\d{2})", str(parte).strip())
        if m:
            return int(m.group(2)), int(m.group(1))
    return 5, 2026  # fallback


def carregar_correcoes_de_raiz_pdfs(raizes: list[Path], carimbos_filtro: set[str]) -> dict[str, dict[str, Any]]:
    encontrados = localizar_pdfs_por_carimbo(raizes, carimbos_filtro)
    correcoes: dict[str, dict[str, Any]] = {}
    linhas_log: list[dict[str, Any]] = []

    for carimbo in sorted(carimbos_filtro):
        pdf = encontrados.get(carimbo)
        if not pdf:
            linhas_log.append({"carimbo": carimbo, "arquivo": "", "status": "nao_encontrado", "campos": 0, "erro": ""})
            warn(f"[LOCALIZAR] BB_{carimbo}: PDF nao encontrado nas raizes")
            continue

        mes, ano = _mes_ano_do_caminho(pdf)
        try:
            tipo, rec = ocr_neo.processar_pdf_direto(pdf, mes, ano)
        except Exception as exc:
            linhas_log.append({"carimbo": carimbo, "arquivo": str(pdf), "status": f"erro_ocr:{type(exc).__name__}", "campos": 0, "erro": str(exc)})
            warn(f"[OCR] {pdf.name}: erro {type(exc).__name__}: {exc}")
            continue

        erro = str(rec.get("ERRO") or "").strip()
        if erro:
            linhas_log.append({"carimbo": carimbo, "arquivo": str(pdf), "status": "pulado_com_erro", "campos": 0, "erro": erro})
            continue

        payload = _correcoes_da_linha_ocr(rec)
        campos = len(payload)
        if payload:
            correcoes[carimbo] = payload
            log(f"[OCR OK] BB_{carimbo}: {campos} campos -> {payload}")

        linhas_log.append({"carimbo": carimbo, "arquivo": str(pdf), "status": "ok" if payload else "sem_campos", "campos": campos, "erro": ""})

    _salvar_log_preparacao(linhas_log)
    return correcoes


def _salvar_log_preparacao(linhas: list[dict[str, Any]]) -> None:
    SAIDA_DIR.mkdir(parents=True, exist_ok=True)
    path = SAIDA_DIR / "preparacao_correcao_coelba_mt.csv"
    campos = ["carimbo", "arquivo", "status", "campos", "erro"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=campos, delimiter=";")
        writer.writeheader()
        writer.writerows(linhas)
    log(f"Log de preparacao salvo: {path}")

    carimbos_ok = sorted({str(l.get("carimbo") or "").strip() for l in linhas if l.get("status") == "ok"})
    txt_path = SAIDA_DIR / "carimbos_preparados_coelba_mt.txt"
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
    return 0, 1


def aplicar_correcoes(driver, wait, carimbo: str, correcoes: dict[str, Any]) -> tuple[int, int, int]:
    if not correcoes:
        log(f"Sem correcoes para BB_{normalizar_carimbo(carimbo)}.")
        return 0, 0, 0

    aplicadas = 0
    confirmadas = 0
    ordem = [c for c in CONFIG.ordem_campos if c in correcoes]
    ordem.extend(c for c in correcoes if c not in ordem)

    for campo in ordem:
        qtd, ok = _aplicar_campo_com_aliases(driver, wait, campo, correcoes[campo])
        aplicadas += qtd
        confirmadas += ok

    return aplicadas, confirmadas, len(ordem)


def salvar_auditar_e_avancar(driver, wait, carimbo: str) -> None:
    fluxo_base.salvar_auditar_e_avancar(driver, wait, carimbo)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Correcao COELBA MT por carimbo no Consen")
    p.add_argument("--carimbo", action="append", default=[], help="Carimbo a corrigir. Ex: --carimbo BB_2013140")
    p.add_argument("--carimbos-arquivo", type=str, default="", help="TXT com um carimbo por linha")
    p.add_argument("--raiz-pdfs", action="append", default=[], help="Raiz adicional de PDFs (pode repetir)")
    p.add_argument("--preparar-apenas", action="store_true", help="So valida OCR e monta lista de correcoes, sem abrir CONSEN")
    p.add_argument("--retomar-apos", type=str, default="", help="Retoma o lote apos este carimbo")
    p.add_argument("--reprocessar-ok", action="store_true", help="Nao pula carimbos ja concluidos com status ok")
    p.add_argument("--salvar", action="store_true", help="Salva a fatura apos aplicar correcoes")
    p.add_argument("--sem-snapshot", action="store_true", help="Nao salva HTML/JSON da tela")
    p.add_argument("--limite", type=int, default=0, help="Limita quantidade de carimbos processados")
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
        print("Informe ao menos um --carimbo ou --carimbos-arquivo.")
        return 2

    raizes: list[Path] = [PDFS_ROOT, Path(DEFAULT_PDFS_COELBA)]
    for r in (args.raiz_pdfs or []):
        raizes.append(Path(r))

    for raiz in raizes:
        if not raiz.exists():
            warn(f"[AVISO] Raiz nao encontrada: {raiz}")

    log(f"Carimbos a corrigir: {len(carimbos_filtro)}")
    correcoes = carregar_correcoes_de_raiz_pdfs(raizes, carimbos_filtro)
    log(f"Carimbos com correcoes preparadas: {len(correcoes)}")

    if args.preparar_apenas:
        log("--preparar-apenas: encerrando sem abrir CONSEN.")
        return 0

    if not correcoes:
        log("Nenhuma correcao a aplicar.")
        return 0

    driver, wait = abrir_driver_logado()
    abrir_tela_edicao_carimbo(driver, wait)

    status_anterior = {} if args.reprocessar_ok else carregar_status_execucao()
    retomar_apos = normalizar_carimbo(args.retomar_apos) if args.retomar_apos else ""
    pulando = bool(retomar_apos)

    lista = sorted(correcoes.keys())
    if args.limite > 0:
        lista = lista[: args.limite]

    ok_total = err_total = pulados = 0
    for carimbo in lista:
        if pulando:
            if carimbo == retomar_apos:
                pulando = False
            else:
                pulados += 1
                continue

        status_prev = status_anterior.get(carimbo, "")
        if status_prev == "ok" and not args.reprocessar_ok:
            log(f"[PULADO] BB_{carimbo}: ja corrigido anteriormente")
            continue

        try:
            carregar_fatura_por_carimbo(driver, wait, carimbo)
        except Exception as exc:
            warn(f"[ERRO] BB_{carimbo}: nao foi possivel carregar fatura: {exc}")
            registrar_execucao(carimbo, "erro_carregar", str(exc))
            err_total += 1
            continue

        if not args.sem_snapshot:
            salvar_snapshot(driver, carimbo)

        aplicadas, confirmadas, total = aplicar_correcoes(driver, wait, carimbo, correcoes[carimbo])
        log(f"[CORRECAO] BB_{carimbo}: {aplicadas}/{total} campos alterados, {confirmadas}/{total} confirmados")

        if args.salvar:
            try:
                salvar_auditar_e_avancar(driver, wait, carimbo)
                registrar_execucao(carimbo, "ok", f"{aplicadas}/{total} campos")
                ok_total += 1
            except Exception as exc:
                warn(f"[ERRO] BB_{carimbo}: falha ao salvar: {exc}")
                registrar_execucao(carimbo, "erro_salvar", str(exc))
                err_total += 1
        else:
            log(f"[SIM] BB_{carimbo}: correcoes aplicadas na tela mas NAO salvas (use --salvar)")
            registrar_execucao(carimbo, "simulado", f"{aplicadas}/{total} campos")
            ok_total += 1

    log(f"Concluido: {ok_total} ok, {err_total} erros, {pulados} pulados")
    if FECHAR_AO_FINAL:
        try:
            driver.quit()
        except Exception:
            pass
    return 0 if err_total == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
