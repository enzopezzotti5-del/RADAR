"""Gera o inventario operacional canonico sem alterar o CSV bruto."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from core.downloaders.cpfl.cpfl_inventory import build_pdf_sha_index, write_canonical_inventory

DEFAULT_ROOT = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD CPFL")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--skip-pdf-index", action="store_true")
    args = parser.parse_args()
    raw = args.root / "cpfl_ucs_inventario.csv"
    canonical = args.root / "cpfl_ucs_canonical.csv"
    metrics = args.root / "cpfl_ucs_canonical_metrics.json"
    _, summary = write_canonical_inventory(raw, canonical, metrics)
    if not args.skip_pdf_index:
        sha_index = build_pdf_sha_index(args.root, args.root / "cpfl_pdf_sha256.csv")
        summary["PDF_SHA256_INDEXED"] = len(sha_index)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
