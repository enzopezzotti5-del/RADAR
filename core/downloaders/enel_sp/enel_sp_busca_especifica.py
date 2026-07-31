#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from enel_sp import EnelDownloaderArquivista


EMAIL = "bbenergia@acaoenge.com.br"
PASS = "Acao*2024"
KEY = "AIzaSyCMiS_wFzups9BdHwwc-x0TinW02rG1peg"
ROOT_DIR = "//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD ENEL"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    default_csv = script_dir / "enel_sp_busca_especifica_ucs.csv"

    parser = argparse.ArgumentParser(
        description="Executa busca especifica da ENEL SP usando uma lista dedicada de UCs."
    )
    parser.add_argument(
        "--csv",
        default=str(default_csv),
        help="CSV com coluna 'instalacao' contendo as UCs alvo.",
    )
    parser.add_argument(
        "--mes-ref",
        default="03-2026",
        help="Mes de referencia alvo no formato MM-AAAA.",
    )
    parser.add_argument(
        "--ignorar-indice",
        action="store_true",
        help="Forca redownload mesmo quando a referencia ja consta no indice.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    csv_path = Path(args.csv)

    if not csv_path.exists():
        print(f"Erro: Arquivo nao encontrado: {csv_path}")
        return 1

    bot = EnelDownloaderArquivista(
        EMAIL,
        PASS,
        KEY,
        "",
        USER_AGENT,
        ROOT_DIR,
    )

    ok = bot.baixar_lote(
        str(csv_path),
        refs_alvo=[args.mes_ref],
        salvar_relatorio=True,
        relatorio_prefixo=(
            "relatorio_enel_sp_redownload_forcado"
            if args.ignorar_indice
            else "relatorio_enel_sp_busca_especifica"
        ),
        ignorar_indice=args.ignorar_indice,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
