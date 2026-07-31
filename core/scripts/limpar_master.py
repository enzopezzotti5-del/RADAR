#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
limpar_master.py
================
1. Lê indice_master.csv do servidor
2. Remove linhas com parsing corrompido (linha inteira em uma coluna)
3. Remove duplicatas de carimbo BB_ (mantém primeira ocorrência)
4. Remove duplicatas de UC+MES_REF (mantém primeira ocorrência)
5. Grava CSV limpo (faz backup antes)

Uso:
    python limpar_master.py --dry-run   # só mostra, não grava
    python limpar_master.py             # grava de verdade
"""

from __future__ import annotations

import csv
import shutil
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

DRY_RUN = "--dry-run" in sys.argv

MASTER_CSV = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/indice_master.csv")

MASTER_FIELDS = [
    "INDICE", "SISTEMA", "UC", "MES_REF", "FATURA_ID",
    "CNPJ", "ESTADO", "INSTALACAO", "DATA_DOWNLOAD", "ARQUIVO",
]

# =============================================================================
# LEITURA
# =============================================================================

print(f"Lendo: {MASTER_CSV}")
linhas_raw = []
for enc in ("utf-8-sig", "utf-8", "latin-1"):
    try:
        with open(MASTER_CSV, newline="", encoding=enc) as f:
            linhas_raw = list(csv.DictReader(f))
        print(f"Encoding: {enc} | Linhas brutas: {len(linhas_raw)}")
        break
    except UnicodeDecodeError:
        continue

# =============================================================================
# LIMPEZA
# =============================================================================

removidos_corrompidos = 0
removidos_dup_carimbo = 0
removidos_dup_chave   = 0
linhas_limpas = []

carimbos_vistos: set = set()
chaves_vistas:   set = set()

for row in linhas_raw:
    indice  = (row.get("INDICE")     or "").strip()
    sistema = (row.get("SISTEMA")    or "").strip()
    uc      = (row.get("UC")         or "").strip()
    mes_ref = (row.get("MES_REF")    or "").strip()

    # ── Detecta linha corrompida (toda a linha numa coluna) ──────────────────
    # Sinal: campo INDICE contém vírgulas (CSV mal parseado)
    if "," in indice or len(indice) > 20:
        print(f"  [CORROMPIDA] INDICE='{indice[:80]}'")
        removidos_corrompidos += 1
        continue

    # ── Normaliza UC para dedup (remove zeros à esquerda) ───────────────────
    uc_norm = uc.lstrip("0") or "0"

    # ── Dedup por carimbo BB_ ────────────────────────────────────────────────
    if indice.startswith("BB_") and indice in carimbos_vistos:
        print(f"  [DUP CARIMBO] {indice} | {sistema} | {uc} | {mes_ref}")
        removidos_dup_carimbo += 1
        continue

    # ── Dedup por UC + MES_REF ───────────────────────────────────────────────
    chave = f"{uc_norm}|{mes_ref}"
    if uc_norm and mes_ref and chave in chaves_vistas:
        print(f"  [DUP CHAVE]   {indice} | {sistema} | {uc} | {mes_ref}")
        removidos_dup_chave += 1
        continue

    # Registro válido
    if indice.startswith("BB_"):
        carimbos_vistos.add(indice)
    if uc_norm and mes_ref:
        chaves_vistas.add(chave)

    linhas_limpas.append(row)

print()
print(f"Removidos corrompidos : {removidos_corrompidos}")
print(f"Removidos dup carimbo : {removidos_dup_carimbo}")
print(f"Removidos dup chave   : {removidos_dup_chave}")
print(f"Linhas restantes      : {len(linhas_limpas)}")
print()

if DRY_RUN:
    print("[DRY-RUN] Nenhuma alteração gravada.")
    sys.exit(0)

# =============================================================================
# BACKUP + GRAVAÇÃO
# =============================================================================

ts = datetime.now().strftime("%Y%m%d_%H%M%S")
backup = MASTER_CSV.parent / f"indice_master_backup_{ts}.csv"
shutil.copy2(MASTER_CSV, backup)
print(f"Backup: {backup}")

with open(MASTER_CSV, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=MASTER_FIELDS, extrasaction="ignore")
    w.writeheader()
    w.writerows(linhas_limpas)

print(f"CSV limpo gravado: {MASTER_CSV}")
print(f"Total final: {len(linhas_limpas)} registros")
