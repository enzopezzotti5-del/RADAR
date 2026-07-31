# -*- coding: utf-8 -*-
"""
Digitação Neoenergia BT no Consen.

Etapa 2 do pipeline Neoenergia BT:
  1. ocr_neoenergia.py       → gera XLSX BT  (OCR NEOENERGIA/<estado>/)
  2. este script             → digita no Consen
  3. neoenergia_filtro.py    → move PDFs digitados para Digitadas/

Uso:
    python digitacao_consen_neoenergia_bt.py --xlsx "//servidor/OCR NEOENERGIA/BAHIA/ocr_neoenergia_bahia_BT_032026.xlsx"

Todas as variáveis de ambiente do script ENEL também funcionam aqui
(CONSEN_USUARIO, CONSEN_SENHA, CONSEN_INTERATIVO_FECHAR, etc.).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# ── bootstrap venv ──────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _venv_check  # noqa: E402

# ── parse args ANTES do import do módulo ENEL ────────────────────────────────
# O módulo ENEL lê variáveis de ambiente em nível de módulo,
# por isso os env vars precisam estar setados antes do import.

_OCR_ROOT = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/OCR NEOENERGIA")
_PIPELINE_SAIDA = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/NEOENERGIA_pipeline_saida/BT")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Digitação Neoenergia BT → Consen",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument(
        "--xlsx",
        default=os.environ.get("NEO_BT_XLSX", ""),
        help=(
            "Planilha BT gerada pelo ocr_neoenergia.py.\n"
            "Ex: .../OCR NEOENERGIA/BAHIA/ocr_neoenergia_bahia_BT_032026.xlsx\n"
            "Também pode ser passada via env var NEO_BT_XLSX."
        ),
    )
    p.add_argument(
        "--pasta-pdfs",
        default=os.environ.get("NEO_BT_PASTA_PDFS", ""),
        help=(
            "Pasta com os PDFs BT do mês. Se informada, só digita faturas\n"
            "cujo PDF esteja presente na pasta. Opcional."
        ),
    )
    p.add_argument(
        "--saida",
        default=str(_PIPELINE_SAIDA),
        help=f"Pasta de saída dos arquivos de auditoria. Padrão: {_PIPELINE_SAIDA}",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    xlsx = args.xlsx.strip()
    if not xlsx:
        sys.exit(
            "Erro: informe o caminho do XLSX via --xlsx ou env var NEO_BT_XLSX.\n"
            f"Exemplo: --xlsx \"{_OCR_ROOT / 'BAHIA' / 'ocr_neoenergia_bahia_BT_032026.xlsx'}\""
        )

    if not Path(xlsx).exists():
        sys.exit(f"Erro: arquivo não encontrado: {xlsx}")

    # ── injeta env vars que o módulo ENEL lê no import ──────────────────────
    os.environ["ENEL_EXCEL_PATH"] = xlsx
    os.environ["CONSEN_PIPELINE_SAIDA"] = args.saida
    os.environ["ENEL_PIPELINE_SAIDA"] = args.saida
    if args.pasta_pdfs.strip():
        os.environ["ENEL_DIGITACAO_PASTA_PDFS"] = args.pasta_pdfs.strip()

    # ── importa e delega para o módulo ENEL ─────────────────────────────────
    # O módulo é genérico: toda a lógica de Consen, auditoria e retry está lá.
    # A diferença são apenas os paths (acima) e o concCod no XLSX
    # (populado pelo ocr_neoenergia.py → COELBA / CELPE / COSERN / ELEKTRO).
    try:
        from digitacao_consen.digitacao_consen_enel import main as _enel_main
    except ModuleNotFoundError:
        from digitacao_consen_enel import main as _enel_main  # type: ignore

    _enel_main()


if __name__ == "__main__":
    main()
