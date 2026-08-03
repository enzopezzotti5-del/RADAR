"""
Orquestrador de download de faturas Energisa.

Fluxo:
    Para cada CNPJ do índice bbenergia:
        - Faz login no portal
        - Baixa a 2ª via de cada UC vinculada ao CNPJ
        - Registra resultado em log CSV

Uso:
    .venv\Scripts\python.exe core\downloaders\energisa\downloader_energisa.py
    .venv\Scripts\python.exe core\downloaders\energisa\downloader_energisa.py --cnpj 12345678000100
    .venv\Scripts\python.exe core\downloaders\energisa\downloader_energisa.py --mes 06 --ano 2026
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from core.downloaders.energisa.indice_energisa import (
    UCEnergisa,
    agrupar_por_cnpj,
    carregar_ucs_bbenergia,
    PLANILHA_DEFAULT,
)
from core.downloaders.energisa.portal import PortalEnergisa

# ---------------------------------------------------------------------------
# Configuração de paths
# ---------------------------------------------------------------------------

SERVIDOR = Path("//10.10.250.21/Energia")

# Pasta de download temporário (Selenium despeja aqui)
DOWNLOAD_TMP = Path("D:/downloads/energisa_tmp")

# Destino final dos PDFs baixados (organizado por concessionária)
DESTINO_BASE = SERVIDOR / "ARQUIVOS ENZO" / "DOWNLOAD ENERGISA" / "BAIXADOS"

# Log de execução
LOG_DIR  = Path("c:/Users/Revit/Desktop/ENERGIA/logs")
LOG_FILE = LOG_DIR / f"energisa_download_{dt.date.today():%Y%m%d}.csv"

LOG_CAMPOS = ["cnpj", "instalacao", "concessionaria", "tensao", "status", "arquivo", "erro", "timestamp"]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _log(cnpj: str, uc: UCEnergisa, status: str, arquivo: str = "", erro: str = "") -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    existe = LOG_FILE.exists()
    with LOG_FILE.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=LOG_CAMPOS, delimiter=";")
        if not existe:
            w.writeheader()
        w.writerow({
            "cnpj":          cnpj,
            "instalacao":    uc.instalacao,
            "concessionaria": uc.concessionaria,
            "tensao":        uc.tensao,
            "status":        status,
            "arquivo":       arquivo,
            "erro":          erro,
            "timestamp":     dt.datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        })


# ---------------------------------------------------------------------------
# Destino do PDF
# ---------------------------------------------------------------------------

def _mover_pdf(pdf_tmp: Path, uc: UCEnergisa, mes: str, ano: str) -> Path:
    """Move PDF da pasta temporária para destino organizado."""
    conc = uc.concessionaria.replace(" ", "_").upper()
    destino_dir = DESTINO_BASE / conc / f"{mes}-{ano}"
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / f"{uc.instalacao}_{mes}{ano}_{pdf_tmp.name}"
    shutil.move(str(pdf_tmp), str(destino))
    return destino


# ---------------------------------------------------------------------------
# Orquestrador
# ---------------------------------------------------------------------------

def rodar(
    planilha: Path = PLANILHA_DEFAULT,
    cnpj_filtro: str | None = None,
    mes: str = "",
    ano: str = "",
) -> None:
    hoje = dt.date.today()
    mes  = mes  or f"{hoje.month:02d}"
    ano  = ano  or str(hoje.year)

    ucs = carregar_ucs_bbenergia(planilha)
    if not ucs:
        print("[downloader] Nenhuma UC bbenergia encontrada na planilha.")
        return

    grupos = agrupar_por_cnpj(ucs)

    if cnpj_filtro:
        cnpj_filtro = cnpj_filtro.strip().replace(".", "").replace("/", "").replace("-", "")
        grupos = {k: v for k, v in grupos.items() if k == cnpj_filtro}
        if not grupos:
            print(f"[downloader] CNPJ {cnpj_filtro} não encontrado no índice bbenergia.")
            return

    print(f"[downloader] {len(ucs)} UC(s) em {len(grupos)} CNPJ(s) para processar.")
    print(f"[downloader] Referência: {mes}/{ano}")

    DOWNLOAD_TMP.mkdir(parents=True, exist_ok=True)

    with PortalEnergisa(download_dir=DOWNLOAD_TMP) as portal:
        for cnpj, lista_ucs in sorted(grupos.items()):
            print(f"\n{'='*60}")
            print(f"[downloader] CNPJ: {cnpj} — {lista_ucs[0].concessionaria} ({len(lista_ucs)} UC(s))")
            print(f"{'='*60}")

            # Mapa instalacao → UCEnergisa para lookup rápido
            uc_map = {uc.instalacao: uc for uc in lista_ucs}
            instalacoes = list(uc_map.keys())

            resultados = portal.baixar_faturas_cnpj(cnpj=cnpj, ucs=instalacoes)

            for instalacao, pdf_tmp in resultados.items():
                uc = uc_map[instalacao]
                print(f"\n  UC: {instalacao} [{uc.tensao}] — {uc.responsavel}")

                if pdf_tmp and pdf_tmp.exists():
                    try:
                        destino = _mover_pdf(pdf_tmp, uc, mes, ano)
                        _log(cnpj, uc, "SUCESSO", str(destino))
                        print(f"  OK → {destino.name}")
                    except Exception as exc:
                        _log(cnpj, uc, "ERRO_MOVER", str(pdf_tmp), str(exc))
                        print(f"  ERRO ao mover: {exc}")
                else:
                    _log(cnpj, uc, "ERRO_DOWNLOAD", erro="PDF não gerado")
                    print("  FALHA no download")

            # Pausa entre CNPJs para não sobrecarregar o portal
            time.sleep(3)

    print(f"\n[downloader] Concluído. Log: {LOG_FILE}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Downloader de faturas Energisa (bbenergia)")
    p.add_argument("--planilha", default=str(PLANILHA_DEFAULT), help="Caminho do acessos_energisa.xlsx")
    p.add_argument("--cnpj",    default=None,  help="Processar apenas este CNPJ")
    p.add_argument("--mes",     default="",    help="Mês de referência (ex: 06)")
    p.add_argument("--ano",     default="",    help="Ano de referência (ex: 2026)")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    rodar(
        planilha=Path(args.planilha),
        cnpj_filtro=args.cnpj,
        mes=args.mes,
        ano=args.ano,
    )
