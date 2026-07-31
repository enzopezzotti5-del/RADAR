#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
staging_enel_mt_lote.py
-----------------------
Carimba no indice_master e copia PDFs de um lote avulso ENEL MT para a
estrutura de DOWNLOAD ENEL, prontos para OCR/digitação/filtro.

Padrão do nome de arquivo esperado:
    {UC} - DD.MM.pdf      ex: "1150844 - 20.06.pdf"

Uso:
    python staging_enel_mt_lote.py                    # execução real
    python staging_enel_mt_lote.py --dry-run          # só simula
    python staging_enel_mt_lote.py --fonte "\\\\srv\\Energia\\..." --lote lote_enel_mt_062026
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

_RAIZ = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_RAIZ))
sys.path.insert(0, str(_RAIZ / "core"))
import _venv_check  # noqa  -- garante venv correto

try:
    import pdfplumber
except ImportError:
    pdfplumber = None  # type: ignore

from indice_master import MasterIndice

# =============================================================================
# CONFIGURAÇÃO PADRÃO
# =============================================================================

SOURCE_DIR = Path("//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO/Baixa Tensão/ENEL/MT")
DOWNLOAD_ENEL = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD ENEL")

# Nome da subpasta de lote dentro de DOWNLOAD ENEL
LOTE_NOME = "lote_enel_mt_062026"

# Mês de referência das faturas (extraído de "DD.06" no nome do arquivo → 06-2026)
MES_REF_PADRAO = "06-2026"

# CNPJ prefixo identificador por distribuidora
_CNPJ_ENEL_SP = "61.695.227"   # Enel Distribuição São Paulo
_CNPJ_ENEL_RJ = "28.150.884"   # Enel Distribuição Rio de Janeiro


# =============================================================================
# HELPERS
# =============================================================================

def _uc_do_nome(nome: str) -> str | None:
    """'1150844 - 20.06.pdf' → '1150844'
       'MTE0001004 - 20.07.pdf' → 'MTE0001004'
    """
    m = re.match(r"^\s*([A-Za-z]*\d+)", nome)
    return m.group(1) if m else None


def _mes_do_nome(nome: str, ano_default: int = 2026) -> str:
    """'101 - 20.06.pdf' → '06-2026'  (DD.MM → MM-YYYY)"""
    m = re.search(r"\d+\s*-\s*\d{2}\.(\d{2})", nome)
    if m:
        return f"{m.group(1)}-{ano_default}"
    return MES_REF_PADRAO


def _detectar_sistema_estado(pdf_path: Path) -> tuple[str, str]:
    """
    Lê a primeira página do PDF e detecta a distribuidora pelo CNPJ.
    Retorna (sistema, estado).
    """
    if pdfplumber is None:
        return "ENEL", ""
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            texto = pdf.pages[0].extract_text() or ""
    except Exception:
        return "ENEL", ""

    if _CNPJ_ENEL_SP in texto:
        return "ENEL_SP", "SÃO PAULO"
    if _CNPJ_ENEL_RJ in texto:
        return "ENEL_RJ", "RIO DE JANEIRO"
    # Fallback por menção geográfica
    upper = texto.upper()
    if "SÃO PAULO" in upper or "SAO PAULO" in upper or "/SP" in upper:
        return "ENEL_SP", "SÃO PAULO"
    if "RIO DE JANEIRO" in upper or "/RJ" in upper:
        return "ENEL_RJ", "RIO DE JANEIRO"
    return "ENEL", ""


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Staging ENEL MT — lote avulso")
    parser.add_argument("--fonte",   default=str(SOURCE_DIR), help="Pasta com os PDFs originais")
    parser.add_argument("--lote",    default=LOTE_NOME,       help="Nome da subpasta de lote em DOWNLOAD ENEL")
    parser.add_argument("--mes",     default="",              help="Forçar mes_ref (ex: 06-2026)")
    parser.add_argument("--dry-run", action="store_true",     help="Simula sem copiar nem registrar")
    args = parser.parse_args()

    fonte   = Path(args.fonte)
    staging = DOWNLOAD_ENEL / args.lote / "MT"

    if not fonte.exists():
        print(f"[ERRO] Pasta fonte não encontrada: {fonte}")
        sys.exit(1)

    pdfs = sorted(fonte.glob("*.pdf"))
    if not pdfs:
        print(f"[ERRO] Nenhum PDF em: {fonte}")
        sys.exit(1)

    if not args.dry_run:
        try:
            staging.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print(f"[ERRO] Não foi possível criar staging: {staging}  —  {e}")
            sys.exit(1)

    master = MasterIndice()

    print(f"\n{'DRY-RUN  ' if args.dry_run else ''}STAGING ENEL MT LOTE AVULSO")
    print(f"  Fonte   : {fonte}")
    print(f"  Staging : {staging}")
    print(f"  PDFs    : {len(pdfs)}\n")

    copiados = pulados = erros = 0

    for pdf in pdfs:
        uc = _uc_do_nome(pdf.name)
        if not uc:
            print(f"  [SKIP UC] {pdf.name}")
            erros += 1
            continue

        mes_ref = args.mes or _mes_do_nome(pdf.name)
        sistema, estado = _detectar_sistema_estado(pdf)

        if master.ja_foi_baixado(uc, mes_ref, "ENEL"):
            print(f"  [JÁ NO MASTER] UC={uc}  ref={mes_ref}  ({pdf.name})")
            pulados += 1
            continue

        if args.dry_run:
            carimbo_preview = master.proximo_carimbo
            print(f"  [DRY] {pdf.name:35s} -> {carimbo_preview}  UC={uc}  {sistema}  {mes_ref}")
            continue

        carimbo = master.consumir_carimbo()
        destino = staging / f"{carimbo}.pdf"

        try:
            shutil.copy2(str(pdf), str(destino))
        except OSError as e:
            print(f"  [ERRO CÓPIA] {pdf.name}: {e}")
            erros += 1
            continue

        master.registrar(
            indice_bb=carimbo,
            sistema=sistema,
            uc=uc,
            mes_ref=mes_ref,
            estado=estado,
            arquivo=pdf.name,
        )
        print(f"  [OK] {pdf.name:35s} -> {destino.name}  UC={uc}  {sistema}  {mes_ref}")
        copiados += 1

    print(f"\n{'-'*60}")
    print(f"  Copiados : {copiados}")
    print(f"  Pulados  : {pulados} (ja no master)")
    print(f"  Erros    : {erros}")
    print(f"{'-'*60}")

    if not args.dry_run and copiados > 0:
        lote = args.lote
        mes_n, ano_n = (args.mes or MES_REF_PADRAO).split("-")
        print("\n[PRÓXIMO PASSO] Execute o pipeline completo:")
        print(f"  cd {_RAIZ / 'core'}")
        print(f"  python pipelines/pipeline_enel_mt_lote.py --lote {lote} --mes {mes_n} --ano {ano_n}")


if __name__ == "__main__":
    main()
