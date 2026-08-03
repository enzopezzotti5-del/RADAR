#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sincronizar_master.py
=====================
Lê cada índice local dos downloaders, detecta registros ausentes no master
(dedup por SISTEMA + UC/instalação + mês_ref) e os insere.

Uso:
    python sincronizar_master.py            # executa migração real
    python sincronizar_master.py --dry-run  # só mostra o que faria
"""

from __future__ import annotations

import csv
import importlib.util
import sys
from datetime import datetime
from pathlib import Path

DRY_RUN = "--dry-run" in sys.argv

# =============================================================================
# CARREGA O indice_master.py DO SERVIDOR
# =============================================================================

_MASTER_SERVIDOR = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/indice_master.py")
_MASTER_LOCAL    = Path(__file__).resolve().parent / "indice_master.py"

def _carregar_modulo_master():
    for caminho in [_MASTER_SERVIDOR, _MASTER_LOCAL]:
        if not caminho.exists():
            continue
        try:
            spec = importlib.util.spec_from_file_location("indice_master", caminho)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            print(f"[master] Carregado: {caminho}")
            print(f"[master] CSV: {m.MASTER_FILE}")
            return m
        except Exception as e:
            print(f"[master] Falha ao carregar {caminho}: {e}")
    raise RuntimeError("indice_master.py não encontrado em nenhum caminho.")

mod    = _carregar_modulo_master()
master = mod.MasterIndice(mod.MASTER_FILE)
print(f"[master] {len(master._ja_baixados)} registros | próximo: {master.proximo_carimbo}")
print()

_sistemas_master: dict[str, set[str]] = {}
_indices_master: set[str] = set()

def _chave_base(uc: str, mes_ref: str) -> str:
    return f"{str(uc or '').strip().lstrip('0') or '0'}|{_normalizar(mes_ref)}"

def _recarregar_estado_master() -> None:
    _sistemas_master.clear()
    _indices_master.clear()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(master.master_file, newline="", encoding=enc) as f:
                for row in csv.DictReader(f):
                    uc = (row.get("UC") or row.get("INSTALACAO") or "").strip()
                    mes_ref = (row.get("MES_REF") or "").strip()
                    sistema = _normalizar_sistema_dedup((row.get("SISTEMA") or "").strip())
                    indice = (row.get("INDICE") or "").strip()
                    if uc and mes_ref and sistema:
                        _sistemas_master.setdefault(_chave_base(uc, mes_ref), set()).add(sistema)
                    if indice.startswith("BB_"):
                        _indices_master.add(indice)
            return
        except UnicodeDecodeError:
            continue

def _sistemas_ja_registrados(uc: str, mes_ref: str) -> list[str]:
    return sorted(_sistemas_master.get(_chave_base(uc, mes_ref), set()))

def _ja_foi_baixado_sistema(uc: str, mes_ref: str, sistema: str) -> bool:
    return _normalizar_sistema_dedup(sistema) in _sistemas_master.get(_chave_base(uc, mes_ref), set())

def _indice_existe(indice: str) -> bool:
    return (indice or "").strip() in _indices_master

def _registrar_cache_local(uc: str, mes_ref: str, sistema: str, indice: str) -> None:
    _sistemas_master.setdefault(_chave_base(uc, mes_ref), set()).add(_normalizar_sistema_dedup(sistema))
    if (indice or "").startswith("BB_"):
        _indices_master.add(indice)

# =============================================================================
# HELPER: normalização
# =============================================================================

def _normalizar(mes_ref: str) -> str:
    return mod.normalizar_mes_ref(mes_ref)

def _normalizar_sistema_dedup(sistema: str) -> str:
    if hasattr(mod, "normalizar_sistema_dedup"):
        return mod.normalizar_sistema_dedup(sistema)
    sist = str(sistema or "").strip().upper()
    if sist.startswith("ENEL"):
        return "ENEL"
    return sist

_recarregar_estado_master()

# =============================================================================
# FUNÇÃO GENÉRICA DE MIGRAÇÃO
# =============================================================================

def migrar(
    nome_sistema: str,
    caminho_csv: Path,
    col_uc:       str,
    col_mes:      str,
    col_indice:   str  = "INDICE",
    col_fatura:   str  = "",
    col_cnpj:     str  = "",
    col_estado:   str  = "",
    col_instal:   str  = "",
    col_arquivo:  str  = "",
    estado_fixo:  str  = "",
) -> int:
    """
    Lê o CSV local e insere no master os registros ainda ausentes.
    Retorna o número de registros inseridos.
    """
    if not caminho_csv.exists():
        print(f"  [{nome_sistema}] CSV não encontrado: {caminho_csv}")
        return 0

    inseridos = 0
    pulados   = 0
    erros     = 0

    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(caminho_csv, newline="", encoding=enc) as f:
                linhas = list(csv.DictReader(f))
            break
        except UnicodeDecodeError:
            continue
    else:
        print(f"  [{nome_sistema}] Erro de encoding ao ler {caminho_csv}")
        return 0

    for row in linhas:
        uc      = (row.get(col_uc)      or "").strip()
        mes_ref = (row.get(col_mes)     or "").strip()
        indice  = (row.get(col_indice)  or "").strip()
        fatura  = (row.get(col_fatura)  or "").strip() if col_fatura  else ""
        cnpj    = (row.get(col_cnpj)    or "").strip() if col_cnpj    else ""
        estado  = (row.get(col_estado)  or estado_fixo).strip()
        instal  = (row.get(col_instal)  or uc).strip() if col_instal  else uc
        arquivo = (row.get(col_arquivo) or "").strip() if col_arquivo else ""

        if not uc or not mes_ref:
            continue

        mes_norm = _normalizar(mes_ref)

        sistemas_existentes = _sistemas_ja_registrados(uc, mes_norm)

        if _ja_foi_baixado_sistema(uc, mes_norm, nome_sistema):
            pulados += 1
            outros = [s for s in sistemas_existentes if s != _normalizar_sistema_dedup(nome_sistema)]
            if outros:
                print(
                    f"    [SKIP] {nome_sistema} | {uc} | {mes_norm} já existe neste sistema "
                    f"(também presente em: {', '.join(outros)})"
                )
            continue

        if sistemas_existentes and _normalizar_sistema_dedup(nome_sistema) not in sistemas_existentes:
            print(
                f"    [INFO] {nome_sistema} | {uc} | {mes_norm} já existe em outro(s) sistema(s): "
                f"{', '.join(sistemas_existentes)} — inserindo novo registro"
            )

        if DRY_RUN:
            print(f"    [DRY] {nome_sistema} | {uc} | {mes_norm} | {indice}")
            inseridos += 1
            continue

        # Usa carimbo original se existir, senão consome novo
        carimbo = indice if indice.startswith("BB_") else master.consumir_carimbo()
        if carimbo.startswith("BB_") and _indice_existe(carimbo):
            print(
                f"    [INFO] {nome_sistema} | {uc} | {mes_norm} índice {carimbo} já existe "
                f"globalmente — consumindo novo carimbo"
            )
            carimbo = master.consumir_carimbo()

        try:
            master.registrar(
                indice_bb  = carimbo,
                sistema    = nome_sistema,
                uc         = uc,
                mes_ref    = mes_norm,
                fatura_id  = fatura,
                cnpj       = cnpj,
                estado     = estado,
                instalacao = instal,
                arquivo    = arquivo,
            )
            _registrar_cache_local(uc, mes_norm, nome_sistema, carimbo)
            inseridos += 1
        except Exception as e:
            print(f"    [ERR] {nome_sistema} | {uc} | {mes_norm}: {e}")
            erros += 1

    status = "DRY" if DRY_RUN else "OK"
    print(f"  [{nome_sistema}] {status} — inseridos: {inseridos} | já no master: {pulados} | erros: {erros}")
    return inseridos


# =============================================================================
# MIGRAÇÃO DE CADA SISTEMA
# =============================================================================

BASE = Path("//10.10.250.21/Energia/ARQUIVOS ENZO")

print("=" * 60)
print(f"Sincronização master {'[DRY-RUN]' if DRY_RUN else '[REAL]'}")
print(f"Início: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
print("=" * 60)
print()

total = 0

# ── COPEL BT ────────────────────────────────────────────────────────────────
total += migrar(
    nome_sistema = "COPEL",
    caminho_csv  = BASE / "DOWNLOAD COPEL" / "indice_faturas_copel_bt.csv",
    col_uc       = "INSTALACAO",
    col_mes      = "MES_REF",
    col_indice   = "INDICE",
    col_fatura   = "NR_FATURA",
    col_cnpj     = "CNPJ",
    col_arquivo  = "ARQUIVO",
    col_instal   = "INSTALACAO",
    estado_fixo  = "PR",
)

# ── CEMIG ───────────────────────────────────────────────────────────────────
total += migrar(
    nome_sistema = "CEMIG",
    caminho_csv  = BASE / "DOWNLOAD CEMIG" / "indice_faturas_cemig.csv",
    col_uc       = "UC",
    col_mes      = "MES_REF",
    col_indice   = "INDICE",
    col_fatura   = "FATURA_ID",
    col_cnpj     = "CNPJ",
    col_arquivo  = "ARQUIVO",
    estado_fixo  = "MG",
)

# ── ENEL SP ─────────────────────────────────────────────────────────────────
total += migrar(
    nome_sistema = "ENEL_SP",
    caminho_csv  = BASE / "DOWNLOAD ENEL" / "indice_faturas.csv",
    col_uc       = "UC",
    col_mes      = "MES_REF",
    col_indice   = "INDICE",
    col_fatura   = "FATURA_ID",
    estado_fixo  = "SP",
)

# ── ENEL CE ─────────────────────────────────────────────────────────────────
total += migrar(
    nome_sistema = "ENEL_CE",
    caminho_csv  = BASE / "DOWNLOAD ENEL CE" / "indice_faturas.csv",
    col_uc       = "UC",
    col_mes      = "MES_REF",
    col_indice   = "INDICE",
    col_fatura   = "FATURA_ID",
    col_cnpj     = "CNPJ",
    col_instal   = "INSTALACAO",
    col_arquivo  = "ARQUIVO",
    estado_fixo  = "CE",
)

# ── ENEL RJ ─────────────────────────────────────────────────────────────────
total += migrar(
    nome_sistema = "ENEL_RJ",
    caminho_csv  = BASE / "DOWNLOAD ENEL RJ" / "indice_faturas_enel_rj.csv",
    col_uc       = "UC",
    col_mes      = "MES_REF",
    col_indice   = "INDICE",
    col_fatura   = "FATURA_ID",
    col_arquivo  = "CAMINHO",
    estado_fixo  = "RJ",
)

# ── EQUATORIAL GO ────────────────────────────────────────────────────────────
total += migrar(
    nome_sistema = "EQUATORIAL",
    caminho_csv  = BASE / "DOWNLOAD EQUATORIAL" / "indice_downloads_equatorial.csv",
    col_uc       = "INSTALACAO",
    col_mes      = "MES_REF",
    col_indice   = "INDICE",
    col_arquivo  = "ARQUIVO",
    estado_fixo  = "GO",
)

# ── NEOENERGIA ───────────────────────────────────────────────────────────────
total += migrar(
    nome_sistema = "NEOENERGIA",
    caminho_csv  = BASE / "DOWNLOAD NEOENERGIA" / "indice_downloads_neoenergia.csv",
    col_uc       = "instalacao",
    col_mes      = "mes_referencia",
    col_indice   = "id",
    col_fatura   = "chave_unica",
    col_cnpj     = "cnpj",
    col_estado   = "estado",
    col_arquivo  = "arquivo",
    col_instal   = "instalacao",
)

# =============================================================================
# RESUMO FINAL
# =============================================================================

print()
print("=" * 60)
print(f"Total inseridos: {total}")
print(f"Próximo carimbo: {master.proximo_carimbo}")
print(f"Fim: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
print("=" * 60)
