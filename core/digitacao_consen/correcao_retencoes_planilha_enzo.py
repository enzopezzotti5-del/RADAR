#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Correcao das retencoes listadas em RETENCAO.xlsx (planilha Enzo).

Para cada carimbo: busca o PDF, roda OCR, e aplica APENAS os campos de
retencao no Consen (fatTributoFederalPerc/Val + IRPJ/PIS/COFINS/CSLL
perc e val). Campos com valor 0 sao gravados como 0 (zeram o Consen).

Uso:
    python correcao_retencoes_planilha_enzo.py --salvar
    python correcao_retencoes_planilha_enzo.py --salvar --conc COELCE
    python correcao_retencoes_planilha_enzo.py --salvar --retomar-apos 2011921
    python correcao_retencoes_planilha_enzo.py --salvar --reprocessar-ok
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _venv_check  # noqa: F401

try:
    from digitacao_consen import correcao_fluxo_base as fluxo_base
    from digitacao_consen.correcao_fluxo_base import log, warn
    from digitacao_consen.digitacao_consen_enel import clicar_botao_salvar, _aguardar_sem_spinner
except ModuleNotFoundError:
    import correcao_fluxo_base as fluxo_base  # type: ignore
    from correcao_fluxo_base import log, warn  # type: ignore
    from digitacao_consen_enel import clicar_botao_salvar, _aguardar_sem_spinner  # type: ignore

import core.ocr.ocr_neoenergia as _neo
from core.ocr.ocr_enel import extrair_bt as _enel_bt
from core.ocr.ocr_light_rj_bt import _parser_light_bt
from core.ocr import ocr_bt_generico

LOGIN_URL = fluxo_base.LOGIN_URL
BASE_URL  = LOGIN_URL.rsplit("login.php", 1)[0]
EDIT_URL  = os.environ.get(
    "CONSEN_EDITA_FATURA_CARIMBO_URL",
    f"{BASE_URL}index.php#bpg/gestao/fatura/editaFaturaCarimbo.php",
)

PDF_BASE     = Path(r"\\10.10.250.21\Energia\CONTROLE BB\DIGITADOS\CARIMBOS DIGITADOS")
SAIDA_DIR    = Path(r"\\10.10.250.21\Energia\ARQUIVOS ENZO\correcoes_retencao_planilha_enzo")
EXECUCAO_CSV = SAIDA_DIR / "correcao_execucao.csv"
CAMPOS_CRITICOS: tuple[str, ...] = ("btnSalvar", "instalacao")

# ---------------------------------------------------------------------------
# Catalogo de carimbos: (conc, pasta_DDMMYYYY, parser, mes, ano)
#   parser: "generico" | "enel_bt" | "light_bt" | "neo"
# ---------------------------------------------------------------------------
CATALOGO: list[tuple[str, str, str, str, int, int]] = [
    # conc      carimbo    pasta        parser      mes  ano
    ("CEMAR",  "2011850", "02062026", "generico",   0,   0),
    ("CEMAR",  "2011851", "02062026", "generico",   0,   0),
    ("CELPA",  "2011791", "01062026", "generico",   0,   0),
    ("CELPA",  "2011796", "01062026", "generico",   0,   0),
    ("CELPA",  "2011797", "01062026", "generico",   0,   0),
    ("CELPA",  "2011798", "01062026", "generico",   0,   0),
    ("CELPA",  "2010802", "22052026", "generico",   0,   0),
    ("CELPA",  "2011799", "01062026", "generico",   0,   0),
    ("CELPA",  "2011800", "01062026", "generico",   0,   0),
    ("CELPA",  "2011804", "01062026", "generico",   0,   0),
    ("CEPISA", "2010800", "22052026", "generico",   0,   0),
    ("CEPISA", "2011731", "01062026", "generico",   0,   0),
    ("LIGHT",  "2010026", "19052026", "light_bt",   0,   0),
    ("COELCE", "2011921", "03062026", "enel_bt",    0,   0),
    ("COELCE", "2011922", "03062026", "enel_bt",    0,   0),
    ("COELCE", "2011931", "03062026", "enel_bt",    0,   0),
    ("COELCE", "2011932", "03062026", "enel_bt",    0,   0),
    ("COELCE", "2011933", "03062026", "enel_bt",    0,   0),
    ("COELCE", "2011934", "03062026", "enel_bt",    0,   0),
    ("COELCE", "2011935", "03062026", "enel_bt",    0,   0),
    ("COELCE", "2011936", "03062026", "enel_bt",    0,   0),
    ("COELCE", "2011937", "03062026", "enel_bt",    0,   0),
    ("COELCE", "2011940", "03062026", "enel_bt",    0,   0),
    ("COELCE", "2011941", "03062026", "enel_bt",    0,   0),
    ("COELCE", "2011942", "03062026", "enel_bt",    0,   0),
    ("COELCE", "2011943", "03062026", "enel_bt",    0,   0),
    ("COELCE", "2011944", "03062026", "enel_bt",    0,   0),
    ("COELCE", "2011945", "03062026", "enel_bt",    0,   0),
    ("COELCE", "2011946", "03062026", "enel_bt",    0,   0),
    ("COELCE", "2011947", "03062026", "enel_bt",    0,   0),
    ("COELCE", "2011950", "03062026", "enel_bt",    0,   0),
    ("COELCE", "2011951", "03062026", "enel_bt",    0,   0),
    ("COELCE", "2011952", "03062026", "enel_bt",    0,   0),
    ("COELCE", "2011954", "03062026", "enel_bt",    0,   0),
    ("COELCE", "2011955", "03062026", "enel_bt",    0,   0),
    ("COELCE", "2011956", "03062026", "enel_bt",    0,   0),
    ("COELCE", "2011957", "03062026", "enel_bt",    0,   0),
    ("COELCE", "981658",  "02062026", "enel_bt",    0,   0),
    ("COELBA", "982113",  "15012026", "neo",         1, 2026),
    ("COELBA", "988482",  "19022026", "neo",         2, 2026),
    ("COELBA", "991217",  "05032026", "neo",         3, 2026),
    ("COELBA", "2000071", "02042026", "neo",         4, 2026),
    ("COELBA", "2003647", "14042026", "neo",         4, 2026),
    ("COELBA", "2003688", "14042026", "neo",         4, 2026),
    ("COELBA", "2003743", "14042026", "neo",         4, 2026),
    ("COELBA", "2003769", "14042026", "neo",         4, 2026),
    ("COELBA", "2003887", "14042026", "neo",         4, 2026),
    ("COELBA", "2003893", "14042026", "neo",         4, 2026),
    ("COELBA", "2003895", "14042026", "neo",         4, 2026),
    ("COELBA", "2004378", "22042026", "neo",         4, 2026),
    ("COELBA", "2010662", "21052026", "neo",         5, 2026),
    ("COELBA", "2011838", "02062026", "neo",         6, 2026),
    ("COSERN", "2007087", "07052026", "neo",         5, 2026),
]

_CAMPOS_RET = [
    "fatDescIrpjPercRetImposto",   "fatDescIrpjValRetImposto",
    "fatDescPisPercRetImposto",    "fatDescPisValRetImposto",
    "fatDescCofinsPercRetImposto", "fatDescCofinsValRetImposto",
    "fatDescCsllPercRetImposto",   "fatDescCsllValRetImposto",
    "fatTributoFederalPerc",       "fatTributoFederalVal",
]


def _ocr(pdf_path: Path, parser: str, mes: int, ano: int) -> dict:
    if parser == "generico":
        return ocr_bt_generico.processar_pdf(str(pdf_path))
    if parser == "enel_bt":
        return _enel_bt(str(pdf_path))
    if parser == "light_bt":
        return _parser_light_bt(pdf_path)
    if parser == "neo":
        _, rec = _neo.processar_pdf_direto(pdf_path, mes, ano)
        return rec
    raise ValueError(f"Parser desconhecido: {parser}")


def _extrair_retencoes(rec: dict) -> dict[str, float]:
    return {c: float(rec.get(c) or 0.0) for c in _CAMPOS_RET}


def _fmt(valor: float) -> str:
    return f"{valor:.2f}".replace(".", ",")


def _aplicar_correcao(driver, carimbo: str, valores: dict[str, float]) -> int:
    pares = [[campo, _fmt(valores[campo])] for campo in _CAMPOS_RET]

    resultado = driver.execute_script(
        """
        var pares = arguments[0];
        var ok = 0;
        pares.forEach(function(par) {
            var id = par[0], val = par[1];
            var el = document.getElementById(id) || document.getElementsByName(id)[0];
            if (el) {
                el.value = val;
                el.dispatchEvent(new Event('input',  {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                el.dispatchEvent(new Event('blur',   {bubbles:true}));
                ok++;
            }
        });
        return ok;
        """,
        pares,
    )

    encontrados = int(resultado or 0)
    esperados   = len(pares)
    resumo = (
        f"IRPJ={valores['fatDescIrpjValRetImposto']:.2f}  "
        f"PIS={valores['fatDescPisValRetImposto']:.2f}  "
        f"COF={valores['fatDescCofinsValRetImposto']:.2f}  "
        f"CSLL={valores['fatDescCsllValRetImposto']:.2f}  "
        f"TF={valores['fatTributoFederalVal']:.2f}"
    )
    log(f"[CAMPO] BB_{carimbo}: {resumo} | {encontrados}/{esperados} encontrados")
    return encontrados


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Correcao de retencoes — planilha RETENCAO.xlsx Enzo (53 carimbos)"
    )
    p.add_argument("--salvar", action="store_true",
                   help="Salva cada fatura (padrao: modo leitura)")
    p.add_argument("--retomar-apos", type=str, default="",
                   help="Pula carimbos ate e inclusive este")
    p.add_argument("--reprocessar-ok", action="store_true",
                   help="Reprocessa mesmo os ja marcados ok")
    p.add_argument("--conc", type=str, default="",
                   choices=["CEMAR", "CELPA", "CEPISA", "LIGHT", "COELCE", "COELBA", "COSERN", ""],
                   help="Filtra por concessionaria")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    SAIDA_DIR.mkdir(parents=True, exist_ok=True)

    entradas = [(conc, car, pasta, parser, mes, ano)
                for conc, car, pasta, parser, mes, ano in CATALOGO
                if not args.conc or conc == args.conc]

    if args.retomar_apos:
        marcador = str(args.retomar_apos).replace("BB_", "").strip()
        idx = next((i for i, (_, c, *_) in enumerate(entradas) if c == marcador), None)
        if idx is not None:
            entradas = entradas[idx + 1:]

    if not args.reprocessar_ok:
        status_ok = fluxo_base.carregar_status_execucao(EXECUCAO_CSV)
        entradas = [e for e in entradas if status_ok.get(e[1]) != "ok"]

    if not entradas:
        log("Nenhum carimbo pendente.")
        return 0

    log(f"Carimbos a corrigir: {len(entradas)}")
    if not args.salvar:
        log("MODO LEITURA — use --salvar para gravar.")

    # Pre-OCR: extrai valores de todos os PDFs antes de abrir o browser
    log("Extraindo valores via OCR...")
    ocr_cache: dict[str, dict[str, float]] = {}
    ocr_erros: list[str] = []
    for conc, carimbo, pasta, parser, mes, ano in entradas:
        pdf = PDF_BASE / pasta / f"BB_{carimbo}.pdf"
        try:
            rec = _ocr(pdf, parser, mes, ano)
            vals = _extrair_retencoes(rec)
            ocr_cache[carimbo] = vals
            log(
                f"  OCR {conc} BB_{carimbo}: "
                f"IRPJ={vals['fatDescIrpjValRetImposto']:.2f}  "
                f"PIS={vals['fatDescPisValRetImposto']:.2f}  "
                f"COF={vals['fatDescCofinsValRetImposto']:.2f}  "
                f"CSLL={vals['fatDescCsllValRetImposto']:.2f}  "
                f"TF={vals['fatTributoFederalVal']:.2f}"
            )
        except Exception as e:
            warn(f"  OCR ERRO {conc} BB_{carimbo}: {e}")
            ocr_erros.append(carimbo)

    if ocr_erros:
        warn(f"OCR falhou em {len(ocr_erros)} carimbos: {ocr_erros}")
        warn("Corrija os erros acima antes de continuar.")
        return 1

    driver = None
    try:
        driver, wait = fluxo_base.abrir_driver_logado()

        for conc, carimbo, pasta, parser, mes, ano in entradas:
            log(f"--- {conc} BB_{carimbo} ---")
            fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "iniciado", conc)

            try:
                fluxo_base.abrir_tela_edicao_carimbo(driver, wait, EDIT_URL)
                fluxo_base.carregar_fatura_por_carimbo(driver, wait, carimbo, CAMPOS_CRITICOS)
            except Exception as e:
                warn(f"BB_{carimbo}: falha ao abrir — {type(e).__name__}: {e}")
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "erro_navegacao", str(e))
                continue

            time.sleep(0.4)
            encontrados = _aplicar_correcao(driver, carimbo, ocr_cache[carimbo])

            if encontrados == 0:
                warn(f"BB_{carimbo}: nenhum campo encontrado na tela")
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "campos_nao_encontrados", conc)
                continue

            if args.salvar:
                try:
                    clicar_botao_salvar(driver, wait)
                    _aguardar_sem_spinner(driver, timeout=12, min_wait=0.5)
                    fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "ok", conc)
                    log(f"BB_{carimbo}: salvo.")
                except Exception as e:
                    warn(f"BB_{carimbo}: falha ao salvar — {type(e).__name__}: {e}")
                    fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "erro_salvar", str(e))
            else:
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "simulado", conc)
                log(f"BB_{carimbo}: simulado (sem --salvar).")

            time.sleep(0.3)

    except KeyboardInterrupt:
        log("Interrompido pelo usuario.")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    log("Concluido.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
