#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
migrar_master_v2.py
===================
Migração única: converte o indice_master.csv antigo para o novo esquema v2.

O que faz:
  1. Renomeia indice_master.csv  ->  indice_master_old.csv  (backup intocável)
  2. Lê o CSV antigo e converte cada linha para o novo esquema:
       - Adiciona CONCESSIONARIA  (derivada de SISTEMA)
       - Unifica UC / INSTALACAO  em UC
       - Expande estado abreviado (SP -> SÃO PAULO, etc.)
       - Remove coluna INSTALACAO
  3. Grava indice_master.csv novo no mesmo caminho

Uso:
    python migrar_master_v2.py            # migração real
    python migrar_master_v2.py --dry-run  # só mostra o que faria, sem gravar
"""

from __future__ import annotations

import csv
import shutil
import sys
from datetime import datetime
from pathlib import Path

DRY_RUN = "--dry-run" in sys.argv

# =============================================================================
# IMPORTA AS DEFINIÇÕES DO MÓDULO MASTER
# =============================================================================

sys.path.insert(0, str(Path(__file__).resolve().parent))
import indice_master as _m

MASTER_FILE  = _m.MASTER_FILE
OLD_BACKUP   = MASTER_FILE.parent / "indice_master_old.csv"
MASTER_FIELDS_NEW = _m.MASTER_FIELDS  # já contém o novo esquema

# =============================================================================
# HELPERS
# =============================================================================

def _estado(sistema: str, estado_raw: str) -> str:
    e = (estado_raw or "").strip().upper()
    if not e:
        e = _m._ESTADO_FIXO_SISTEMA.get(sistema.strip().upper(), "")
    return _m._ESTADO_ABREV.get(e, e)

def _uc(row: dict) -> str:
    return (row.get("UC") or row.get("INSTALACAO") or "").strip()

def _concessionaria(sistema: str, estado: str) -> str:
    sist = sistema.strip().upper()
    if sist == "NEOENERGIA":
        return _m._NEOENERGIA_POR_ESTADO.get(estado, "Neoenergia")
    return _m._CONCESSIONARIA_SISTEMA.get(sist, sist)

def _converter_linha(row: dict) -> dict:
    sistema = (row.get("SISTEMA") or "").strip().upper()
    estado  = _estado(sistema, row.get("ESTADO", ""))
    return {
        "INDICE":         (row.get("INDICE") or "").strip(),
        "CONCESSIONARIA": _concessionaria(sistema, estado),
        "SISTEMA":        sistema,
        "ESTADO":         estado,
        "UC":             _uc(row),
        "MES_REF":        _m.normalizar_mes_ref(row.get("MES_REF") or ""),
        "FATURA_ID":      (row.get("FATURA_ID") or "").strip(),
        "CNPJ":           (row.get("CNPJ") or "").strip(),
        "DATA_DOWNLOAD":     (row.get("DATA_DOWNLOAD") or "").strip(),
        "ARQUIVO":           (row.get("ARQUIVO") or "").strip(),
        "STATUS_DIGITACAO":  (row.get("STATUS_DIGITACAO") or "").strip(),
        "DATA_DIGITACAO":    (row.get("DATA_DIGITACAO") or "").strip(),
    }

# =============================================================================
# LEITURA DO CSV ANTIGO
# =============================================================================

def _ler_csv_antigo(path: Path) -> list[dict]:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, newline="", encoding=enc) as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"Não foi possível ler {path} com nenhum encoding.")

# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    print("=" * 62)
    print(f"Migração indice_master -> v2  {'[DRY-RUN]' if DRY_RUN else '[REAL]'}")
    print(f"Início  : {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"Fonte   : {MASTER_FILE}")
    print(f"Backup  : {OLD_BACKUP}")
    print("=" * 62)
    print()

    if not MASTER_FILE.exists():
        print(f"ERRO: arquivo não encontrado — {MASTER_FILE}")
        sys.exit(1)

    if OLD_BACKUP.exists() and not DRY_RUN:
        print(f"AVISO: backup ja existe -- {OLD_BACKUP}")
        print("Sobrescrevendo backup existente (use --dry-run para revisar antes).")

    # Lê antes de qualquer alteração
    print("Lendo CSV antigo...")
    linhas_antigas = _ler_csv_antigo(MASTER_FILE)
    print(f"  {len(linhas_antigas)} linha(s) encontrada(s).")
    print()

    # Amostra das primeiras 3 para conferência
    if linhas_antigas:
        print("Amostra (3 primeiras linhas convertidas):")
        for row in linhas_antigas[:3]:
            nova = _converter_linha(row)
            print(f"  {nova['INDICE']:15} | {nova['CONCESSIONARIA']:22} | {nova['SISTEMA']:10} | "
                  f"{nova['ESTADO']:20} | UC={nova['UC']} | {nova['MES_REF']}")
        print()

    if DRY_RUN:
        print("Dry-run concluído — nenhum arquivo foi alterado.")
        return

    # 1. Renomeia para backup
    shutil.copy2(str(MASTER_FILE), str(OLD_BACKUP))
    print(f"Backup criado : {OLD_BACKUP}")

    # 2. Converte e ordena por CONCESSIONARIA -> MES_REF -> UC
    linhas_novas = []
    ignoradas = 0
    for row in linhas_antigas:
        nova = _converter_linha(row)
        if not nova["UC"] and not nova["MES_REF"]:
            ignoradas += 1
            continue
        linhas_novas.append(nova)

    linhas_novas.sort(key=lambda r: (
        r["CONCESSIONARIA"].upper(),
        r["MES_REF"],
        r["UC"],
    ))

    # 3. Grava novo CSV no mesmo caminho
    convertidas = len(linhas_novas)
    with open(MASTER_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=MASTER_FIELDS_NEW)
        writer.writeheader()
        writer.writerows(linhas_novas)

    print(f"Novo master   : {MASTER_FILE}")
    print()
    print("=" * 62)
    print(f"Convertidas : {convertidas}")
    print(f"Ignoradas   : {ignoradas}  (linhas sem UC e sem MES_REF)")
    print("Próx. BB_   : detectado no carregamento do módulo")
    print(f"Fim         : {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print("=" * 62)


if __name__ == "__main__":
    main()
