# -*- coding: utf-8 -*-
"""Checkpoints duráveis por PDF para recuperação após crash do Chrome.

Cada checkpoint é armazenado como <pasta_saida>/checkpoints/<carimbo>.json.
Gravações são atômicas via .tmp + os.replace.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from pathlib import Path


def _dir_checkpoints(pasta_saida: Path | str) -> Path:
    return Path(pasta_saida) / "checkpoints"


def gravar_salvar_confirmado(
    pasta_saida: Path | str,
    carimbo: str,
    instalacao: str,
    data_ref: str,
    session_id: str = "",
) -> None:
    """Grava checkpoint 'salvar_confirmado' para o carimbo de forma atômica."""
    if not carimbo:
        return
    pasta = _dir_checkpoints(pasta_saida)
    pasta.mkdir(parents=True, exist_ok=True)
    payload = {
        "estado": "salvar_confirmado",
        "carimbo": carimbo,
        "instalacao": instalacao,
        "data_ref": data_ref,
        "session_id": session_id,
        "ts": dt.datetime.now().isoformat(),
    }
    destino = pasta / f"{carimbo}.json"
    fd, tmp_name = tempfile.mkstemp(dir=str(pasta), suffix=".tmp", prefix=f"ck_{carimbo}_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_name, str(destino))
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def salvar_confirmado(pasta_saida: Path | str, carimbo: str) -> bool:
    """Retorna True se existe checkpoint 'salvar_confirmado' para o carimbo."""
    if not carimbo:
        return False
    ck = ler_checkpoint(pasta_saida, carimbo)
    return ck is not None and ck.get("estado") == "salvar_confirmado"


def ler_checkpoint(pasta_saida: Path | str, carimbo: str) -> dict | None:
    """Lê checkpoint do carimbo. Retorna None se não existir ou não puder ler."""
    if not carimbo:
        return None
    caminho = _dir_checkpoints(pasta_saida) / f"{carimbo}.json"
    if not caminho.exists():
        return None
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except Exception:
        return None


def limpar_checkpoints(pasta_saida: Path | str) -> int:
    """Remove todos os checkpoints da pasta. Retorna quantidade removida."""
    pasta = _dir_checkpoints(pasta_saida)
    if not pasta.exists():
        return 0
    removidos = 0
    for arq in pasta.glob("*.json"):
        try:
            arq.unlink()
            removidos += 1
        except OSError:
            pass
    return removidos
