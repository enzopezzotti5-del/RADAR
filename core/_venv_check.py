# -*- coding: utf-8 -*-
"""
_venv_check.py
--------------
Garante que o script esta rodando no venv correto do projeto.
Se nao estiver, relanca automaticamente com o Python do venv.

Uso (primeira linha apos os imports padrao):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))  # ou parent.parent etc
    import _venv_check  # noqa
"""

import os
import sys
from pathlib import Path


def _descobrir_root_projeto() -> Path:
    atual = Path(__file__).resolve().parent
    for pasta in (atual, *atual.parents):
        if (pasta / ".venv" / "Scripts" / "python.exe").exists():
            return pasta
    return atual


_PROJETO_ROOT = _descobrir_root_projeto()
_VENV_PYTHON = _PROJETO_ROOT / ".venv" / "Scripts" / "python.exe"
_LIBS_OBRIGATORIAS = [
    "curl_cffi",
    "selenium",
    "openpyxl",
    "pdfplumber",
    "pandas",
    "bs4",
    "filelock",
    "dotenv",
]


def _no_venv_correto() -> bool:
    """Retorna True se o Python atual e o do venv do projeto."""
    python_atual = Path(sys.executable).resolve()
    return python_atual == _VENV_PYTHON.resolve()


def _validar_libs():
    """Verifica se todas as libs obrigatorias estao instaladas."""
    faltando = []
    for lib in _LIBS_OBRIGATORIAS:
        try:
            __import__(lib)
        except ImportError:
            faltando.append(lib)
    if faltando:
        print(f"[venv] AVISO: bibliotecas nao instaladas: {', '.join(faltando)}")
        print(f"[venv] Execute: pip install {' '.join(faltando)}")


def garantir_venv():
    """
    Se nao estiver no venv correto, relanca o script com o Python do venv.
    Chame no inicio de qualquer script que precise do ambiente correto.
    """
    if not _VENV_PYTHON.exists():
        print(f"[venv] AVISO: venv nao encontrado em {_VENV_PYTHON}")
        print("[venv] Execute: python -m venv .venv && .venv\\Scripts\\pip install -r requirements.txt")
        return

    if not _no_venv_correto():
        print(f"[venv] Relancando com: {_VENV_PYTHON}")
        # os.execv no Windows cria um processo filho e sai do processo pai
        # imediatamente, sem aguardar o filho — o pai fica órfão.
        # subprocess.run aguarda corretamente e propaga o exit code.
        import subprocess
        resultado = subprocess.run([str(_VENV_PYTHON)] + sys.argv)
        sys.exit(resultado.returncode)

    _validar_libs()


garantir_venv()
