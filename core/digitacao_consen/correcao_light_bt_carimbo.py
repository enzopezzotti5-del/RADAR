#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Correcao Light RJ BT por carimbo.

Localiza cada PDF em CARIMBOS_DIGITADOS ou Digitadas, roda OCR com o
parser Light corrigido e aplica os campos no Consen via edicao por carimbo.

Uso:
    python correcao_light_bt_carimbo.py --salvar
    python correcao_light_bt_carimbo.py --salvar --retomar-apos 2013670
    python correcao_light_bt_carimbo.py --salvar --reprocessar-ok
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
    from ocr.ocr_light_rj_bt import _parser_light_bt
except ModuleNotFoundError:
    import correcao_fluxo_base as fluxo_base  # type: ignore
    from correcao_fluxo_base import log, warn  # type: ignore
    from digitacao_consen_enel import clicar_botao_salvar, _aguardar_sem_spinner  # type: ignore
    from ocr_light_rj_bt import _parser_light_bt  # type: ignore

LOGIN_URL = fluxo_base.LOGIN_URL
BASE_URL  = LOGIN_URL.rsplit("login.php", 1)[0]
EDIT_URL  = os.environ.get(
    "CONSEN_EDITA_FATURA_CARIMBO_URL",
    f"{BASE_URL}index.php#bpg/gestao/fatura/editaFaturaCarimbo.php",
)

SAIDA_DIR    = Path(r"\\10.10.250.21\Energia\ARQUIVOS ENZO\LIGHT_pipeline_saida\correcoes")
EXECUCAO_CSV = SAIDA_DIR / "correcao_execucao.csv"
CAMPOS_CRITICOS: tuple[str, ...] = ("btnSalvar", "instalacao")

# Pastas onde os PDFs Light podem estar, em ordem de preferência
PDF_ROOTS: list[Path] = [
    Path(r"\\10.10.250.21\Energia\CONTASDEENERGIAELETRICA\BB\ENZO\Digitadas"),
    Path(r"\\10.10.250.21\Energia\CONTROLE BB\DIGITADOS\CARIMBOS DIGITADOS"),
]

# Todos os carimbos Light com dados incompletos (ICMS, PIS, COFINS, consumo)
CARIMBOS: list[str] = [
    # Run anterior — ICMS faltando
    "2013642", "2013645", "2013647", "2013655", "2013657",
    # Small UC (formato tabular) — PIS/COF ilegíveis, ICMS agora OK
    "2013659", "2013660", "2013661", "2013662",
    "2013681", "2013682", "2013683", "2013684", "2013685",
    "2013686", "2013687", "2013688", "2013689", "2013690", "2013691",
    # ICMS, PIS e COF todos faltando
    "2013677", "2013678", "2013679", "2013680",
    # Run light_bcdceb10 — ICMS, PIS, COF zerados + consumo zerado em 2 carimbos
    "2013693", "2013694", "2013695", "2013696", "2013697",
    "2013698", "2013699", "2013700", "2013701", "2013702",
]

# Campos aplicados em cada correcao
_CAMPOS = [
    "fatConFPontaIndRegistrado",
    "fatConFPontaIndFaturado",
    "fatConFPontaIndValorReais",
    "fatDesIcmsAliquota",
    "fatICMS",
    "fatDescPisAliquota",
    "fatPIS",
    "fatDesCofinsAliquota",
    "fatCOFINS",
    "fatValorNotaFiscal",
    "fatDataVcto",
]


def _localizar_pdf(carimbo: str) -> Path | None:
    nome = f"BB_{carimbo}.pdf"
    for raiz in PDF_ROOTS:
        # busca direta
        p = raiz / nome
        if p.exists():
            return p
        # busca em subpastas de data (DDMMYYYY)
        for sub in raiz.iterdir() if raiz.exists() else []:
            if sub.is_dir():
                p = sub / nome
                if p.exists():
                    return p
    return None


def _fmt(valor) -> str:
    if valor is None:
        return ""
    import datetime as _dt
    if isinstance(valor, (_dt.date, _dt.datetime)):
        return valor.strftime("%d/%m/%Y") if not isinstance(valor, _dt.datetime) else valor.strftime("%d/%m/%Y")
    try:
        return f"{float(valor):.2f}".replace(".", ",")
    except (TypeError, ValueError):
        return str(valor)


def _aplicar_campos(driver, carimbo: str, rec: dict) -> int:
    pares = []
    for campo in _CAMPOS:
        val = rec.get(campo)
        if val is None:
            continue
        # nao sobrescrever com zero campos que nao foram extraidos
        import datetime as _dt
        if not isinstance(val, (_dt.date, _dt.datetime)) and float(val or 0) == 0.0:
            continue
        pares.append([campo, _fmt(val)])

    if not pares:
        warn(f"BB_{carimbo}: nenhum campo com valor para aplicar")
        return 0

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
    resumo = "  ".join(f"{p[0].replace('fat','').replace('Des','').replace('Desc','')}={p[1]}" for p in pares[:6])
    log(f"[CAMPO] BB_{carimbo}: {resumo} | {encontrados}/{len(pares)} encontrados")
    return encontrados


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Correcao Light RJ BT por carimbo")
    p.add_argument("--salvar", action="store_true", help="Efetiva o save (padrao: apenas leitura)")
    p.add_argument("--retomar-apos", type=str, default="", help="Pula ate e inclusive este carimbo")
    p.add_argument("--reprocessar-ok", action="store_true", help="Reprocessa mesmo os ja marcados ok")
    p.add_argument("--carimbo", action="append", default=[], help="Filtra carimbo(s) especificos")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    SAIDA_DIR.mkdir(parents=True, exist_ok=True)

    entradas = [str(c).replace("BB_", "").strip() for c in (args.carimbo or CARIMBOS)]

    if args.retomar_apos:
        marcador = str(args.retomar_apos).replace("BB_", "").strip()
        idx = next((i for i, c in enumerate(entradas) if c == marcador), None)
        if idx is not None:
            entradas = entradas[idx + 1:]

    if not args.reprocessar_ok:
        status_ok = fluxo_base.carregar_status_execucao(EXECUCAO_CSV)
        entradas = [c for c in entradas if status_ok.get(c) != "ok"]

    if not entradas:
        log("Nenhum carimbo pendente.")
        return 0

    log(f"Carimbos a corrigir: {len(entradas)}")
    if not args.salvar:
        log("MODO LEITURA — use --salvar para gravar.")

    # Pre-OCR
    log("Extraindo dados via OCR (parser Light corrigido)...")
    ocr_cache: dict[str, dict] = {}
    ocr_erros: list[str] = []

    for carimbo in entradas:
        pdf = _localizar_pdf(carimbo)
        if pdf is None:
            warn(f"  PDF nao encontrado: BB_{carimbo}")
            ocr_erros.append(carimbo)
            continue
        try:
            rec = _parser_light_bt(pdf)
            if rec.get("ERRO"):
                warn(f"  OCR ERRO BB_{carimbo}: {rec['ERRO']}")
                ocr_erros.append(carimbo)
                continue
            ocr_cache[carimbo] = rec
            log(
                f"  BB_{carimbo}: cons={rec.get('fatConFPontaIndRegistrado',0)}  "
                f"PIS%={rec.get('fatDescPisAliquota',0)}  "
                f"COF%={rec.get('fatDesCofinsAliquota',0)}  "
                f"ICMS%={rec.get('fatDesIcmsAliquota',0)}  "
                f"ICMS={rec.get('fatICMS',0)}"
            )
        except Exception as e:
            warn(f"  OCR ERRO BB_{carimbo}: {e}")
            ocr_erros.append(carimbo)

    if ocr_erros:
        warn(f"OCR falhou em {len(ocr_erros)} PDFs: {ocr_erros}")

    pendentes = [c for c in entradas if c in ocr_cache]
    if not pendentes:
        warn("Nenhum carimbo com OCR valido.")
        return 1

    driver = None
    try:
        driver, wait = fluxo_base.abrir_driver_logado()

        for carimbo in pendentes:
            log(f"--- BB_{carimbo} ---")
            fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "iniciado", "LIGHT")

            try:
                fluxo_base.abrir_tela_edicao_carimbo(driver, wait, EDIT_URL)
                fluxo_base.carregar_fatura_por_carimbo(driver, wait, carimbo, CAMPOS_CRITICOS)
            except Exception as e:
                warn(f"BB_{carimbo}: falha ao abrir — {type(e).__name__}: {e}")
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "erro_navegacao", str(e))
                continue

            time.sleep(0.4)
            encontrados = _aplicar_campos(driver, carimbo, ocr_cache[carimbo])

            if encontrados == 0:
                warn(f"BB_{carimbo}: nenhum campo encontrado na tela")
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "campos_nao_encontrados", "LIGHT")
                continue

            if args.salvar:
                try:
                    clicar_botao_salvar(driver, wait)
                    _aguardar_sem_spinner(driver, timeout=12, min_wait=0.5)
                    fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "ok", "LIGHT")
                    log(f"BB_{carimbo}: salvo.")
                except Exception as e:
                    warn(f"BB_{carimbo}: falha ao salvar — {type(e).__name__}: {e}")
                    fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "erro_salvar", str(e))
            else:
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "simulado", "LIGHT")
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
