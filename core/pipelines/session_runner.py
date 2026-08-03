from __future__ import annotations

import argparse
import sys
from pathlib import Path

LOCAL_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = LOCAL_DIR.parent
if str(LOCAL_DIR) not in sys.path:
    sys.path.insert(0, str(LOCAL_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from pipelines._session_runtime import executar_pipeline_com_sessao
except ModuleNotFoundError:  # pragma: no cover - fallback para execucoes diretas
    from _session_runtime import executar_pipeline_com_sessao  # type: ignore[no-redef]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Executa pipeline com sessao centralizada")
    p.add_argument("--script", required=True, help="Caminho do pipeline a executar")
    p.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    extra = list(args.rest or [])
    if extra and extra[0] == "--":
        extra = extra[1:]
    return executar_pipeline_com_sessao(args.script, extra)


if __name__ == "__main__":
    raise SystemExit(main())
