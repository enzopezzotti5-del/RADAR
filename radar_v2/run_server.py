"""
Entrada principal do Radar V2.

Uso:
    .venv/Scripts/python.exe radar_v2/run_server.py [--port 5001]

O scheduler roda em thread daemon dentro do mesmo processo.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar_v2.app.api.server import run

if __name__ == "__main__":
    raise SystemExit(run())
