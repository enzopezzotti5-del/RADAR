"""Leitura centralizada de segredos locais, nunca versionados."""
from __future__ import annotations

import os


def secret_env(name: str) -> str:
    """Obtém uma credencial obrigatória sem expor o valor em código ou logs."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Configure a variável de ambiente obrigatória: {name}")
    return value
