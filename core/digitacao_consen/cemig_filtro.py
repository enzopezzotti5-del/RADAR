#!/usr/bin/env python3
"""
cemig_filtro.py  —  Etapa 3 do pipeline CEMIG
=============================================
Le o auditoria_resultados.csv gerado pela digitacao e move os PDFs
das faturas processadas para a pasta de destino final.

Regra de movimentacao:
    MOVE     -> sucesso_auditoria, auditoria_sem_valor, erro_no_fluxo:*
                e qualquer outro status nao reconhecido (comportamento seguro)
    NAO MOVE -> pulado_referencia_existente
                (fatura ja estava no Consen — PDF fica na pasta de origem)

Caminhos dinamicos (mes/ano calculados em runtime ou passados via CLI):
    CSV      : <PASTA_OCR>/saida_importacao/auditoria_resultados.csv
    Origem   : DOWNLOAD CEMIG / MM.AAAA / BT|MT
    Destino  : CONTASDEENERGIAELETRICA/BB/ENZO/Digitadas

Uso:
    python cemig_filtro.py                    # mes/ano atual
    python cemig_filtro.py --mes 03 --ano 2026
    python cemig_filtro.py --csv C:\\caminho\\alternativo\\auditoria.csv
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import logging
import shutil
import sys
import time
from pathlib import Path

import pandas as pd

try:
    from digitacao_consen.auditoria_schema import ler_auditoria_csv_flexivel
except ModuleNotFoundError:
    from auditoria_schema import ler_auditoria_csv_flexivel  # type: ignore

# =============================================================================
# CONFIGURACAO
# =============================================================================

PASTA_OCR      = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/OCR CEMIG")
PASTA_PIPELINE = Path(r"C:\Users\Revit\Desktop\ENERGIA\pipelines")
PASTA_DOWNLOAD = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD CEMIG")
PASTA_DESTINO  = Path("//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO/Digitadas")
PASTA_EXISTENTES = Path("//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO/Ja_existiam_no_Consen")

COLUNA_LINHA_EXCEL = "linha_excel"
COLUNA_CARIMBO     = "carimbo"
COLUNA_STATUS      = "status"

# Status considerados resolvidos para movimentacao
STATUS_JA_EXISTIAM = {"pulado_referencia_existente"}
STATUS_MOVER = {
    "sucesso_auditoria",
    "auditoria_sem_valor",
} | STATUS_JA_EXISTIAM

BUSCA_CASE_INSENSITIVE      = True
EVITAR_COPIA_DUPLICADA      = True
GERAR_NOME_UNICO_SE_EXISTIR = True

TENTATIVAS_MOVER        = 3
ESPERA_ENTRE_TENTATIVAS = 1  # segundos

# =============================================================================
# LOGGING
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("cemig_filtro")


# =============================================================================
# UTILITARIOS
# =============================================================================

def _normalizar_texto(valor) -> str:
    if pd.isna(valor):
        return ""
    return str(valor).strip()


def _normalizar_status(valor) -> str:
    return _normalizar_texto(valor).lower()


def _normalizar_carimbo(valor) -> str:
    texto = _normalizar_texto(valor)
    if not texto:
        return ""
    # remove .0 quando vier de numero lido como float
    if texto.endswith(".0"):
        texto = texto[:-2]
    return texto.strip()


def _ler_csv_flexivel(csv_path: Path) -> pd.DataFrame:
    """Le o CSV da auditoria, inclusive quando ele vier com colunas excedentes."""
    rows_padrao = ler_auditoria_csv_flexivel(csv_path)
    if rows_padrao:
        return pd.DataFrame(rows_padrao)

    try:
        linhas = csv_path.read_text(encoding="utf-8-sig").splitlines()
    except UnicodeDecodeError:
        linhas = csv_path.read_text(encoding="latin1").splitlines()

    if linhas:
        cab = [c.strip() for c in linhas[0].split(";")]
        if cab[:4] == [
            "linha_excel",
            "instalacao",
            "data_referencia_esperada",
            "carimbo",
        ]:
            rows = []
            for linha in linhas[1:]:
                if not linha.strip():
                    continue
                partes = linha.split(";")
                if len(partes) < 5:
                    continue
                row = {
                    "linha_excel": partes[0].strip(),
                    "instalacao": partes[1].strip() if len(partes) > 1 else "",
                    "data_referencia_esperada": partes[2].strip() if len(partes) > 2 else "",
                    "carimbo": partes[3].strip() if len(partes) > 3 else "",
                    "valor_auditoria": partes[4].strip() if len(partes) > 4 else "",
                    "status": partes[-1].strip() if len(partes) > 5 else "",
                }
                if len(partes) > 6:
                    row["detalhes"] = ";".join(partes[6:-1]).strip()
                elif len(partes) == 6:
                    row["detalhes"] = ""
                else:
                    row["detalhes"] = ""
                rows.append(row)
            if rows:
                return pd.DataFrame(rows)

    """Tenta abrir o CSV com separadores e encodings comuns."""
    tentativas = [
        {"sep": ";", "encoding": "utf-8-sig"},
        {"sep": ",", "encoding": "utf-8-sig"},
        {"sep": ";", "encoding": "utf-8"},
        {"sep": ",", "encoding": "utf-8"},
        {"sep": ";", "encoding": "latin1"},
        {"sep": ",", "encoding": "latin1"},
    ]
    ultimo_erro = None
    for t in tentativas:
        try:
            df = pd.read_csv(csv_path, sep=t["sep"], encoding=t["encoding"], dtype=str)
            if len(df.columns) > 1:
                return df
        except Exception as e:
            ultimo_erro = e
    raise RuntimeError(
        f"Nao foi possivel ler o CSV {csv_path}. Ultimo erro: {ultimo_erro}"
    )


def _encontrar_pdfs_por_carimbo(carimbo: str, arquivos_pdf: list) -> list:
    if BUSCA_CASE_INSENSITIVE:
        c = carimbo.lower()
        return [p for p in arquivos_pdf if c in p.name.lower()]
    return [p for p in arquivos_pdf if carimbo in p.name]


def _gerar_nome_unico(destino: Path) -> Path:
    if not destino.exists():
        return destino
    stem, suffix, parent = destino.stem, destino.suffix, destino.parent
    i = 1
    while True:
        novo = parent / f"{stem}_{i}{suffix}"
        if not novo.exists():
            return novo
        i += 1


def _mover_com_tentativas(origem: Path, destino: Path) -> bool:
    ultimo_erro = None
    for tentativa in range(1, TENTATIVAS_MOVER + 1):
        try:
            shutil.move(str(origem), str(destino))
            return True
        except PermissionError as e:
            ultimo_erro = e
            log.warning(
                f"  [TENTATIVA {tentativa}/{TENTATIVAS_MOVER}] Arquivo em uso: {origem.name}"
            )
            time.sleep(ESPERA_ENTRE_TENTATIVAS)
        except Exception as e:
            raise e
    raise ultimo_erro


def _pasta_mes(mes: str, ano: str) -> Path:
    """Localiza a subpasta MM.AAAA dentro de DOWNLOAD CEMIG."""
    for sep in [".", "-", "_", " ", ""]:
        p = PASTA_DOWNLOAD / f"{mes}{sep}{ano}"
        if p.is_dir():
            return p
    raise FileNotFoundError(
        f"Pasta de download nao encontrada para {mes}/{ano} em {PASTA_DOWNLOAD}"
    )


# =============================================================================
# LOGICA PRINCIPAL
# =============================================================================

def mover_pdfs(
    mes: str,
    ano: str,
    csv_path: Path | None = None,
    pasta_origem_bt_override: Path | None = None,
    rotulo_origem: str = "BT",
) -> bool:
    """
    Executa a movimentacao dos PDFs com base no auditoria_resultados.csv.
    Retorna True se concluiu sem falhas de movimentacao.
    """
    # Caminhos
    if csv_path is None:
        csv_path = PASTA_PIPELINE / "saida_importacao" / "auditoria_resultados.csv"

    try:
        pasta_mes = _pasta_mes(mes, ano)
    except FileNotFoundError as e:
        log.error(f"  {e}")
        return False

    pasta_origem_bt = pasta_origem_bt_override or (pasta_mes / rotulo_origem.upper())
    PASTA_DESTINO.mkdir(parents=True, exist_ok=True)
    PASTA_EXISTENTES.mkdir(parents=True, exist_ok=True)

    # Validacoes
    if not csv_path.exists():
        log.error(f"  CSV nao encontrado: {csv_path}")
        return False

    if not pasta_origem_bt.exists():
        log.error(f"  Pasta {rotulo_origem.upper()} nao encontrada: {pasta_origem_bt}")
        return False

    log.info(f"  CSV       : {csv_path}")
    log.info(f"  Origem {rotulo_origem.upper()} : {pasta_origem_bt}")
    log.info(f"  Destino   : {PASTA_DESTINO}")

    # Leitura do CSV
    df = _ler_csv_flexivel(csv_path)
    df.columns = [str(c).strip() for c in df.columns]

    for col in [COLUNA_LINHA_EXCEL, COLUNA_CARIMBO, COLUNA_STATUS]:
        if col not in df.columns:
            log.error(
                f"  Coluna '{col}' nao encontrada no CSV. "
                f"Colunas presentes: {list(df.columns)}"
            )
            return False

    arquivos_pdf = list(pasta_origem_bt.rglob("*.pdf"))
    if not arquivos_pdf:
        log.warning(f"  Nenhum PDF encontrado na pasta {rotulo_origem.upper()} de origem.")
        return True

    log.info(f"  PDFs na origem : {len(arquivos_pdf)}")
    log.info(f"  Linhas no CSV  : {len(df)}")

    # Processamento linha a linha
    cnt = {
        "lidas":           0,
        "nao_mover":       0,
        "sem_carimbo":     0,
        "nao_encontrados": 0,
        "movidos":         0,
        "falhas":          0,
    }
    falhas: list[dict] = []
    ja_movidos: set[str] = set()

    for _, row in df.iterrows():
        cnt["lidas"] += 1

        linha_excel = _normalizar_texto(row.get(COLUNA_LINHA_EXCEL, ""))
        status      = _normalizar_status(row.get(COLUNA_STATUS, ""))
        carimbo     = _normalizar_carimbo(row.get(COLUNA_CARIMBO, ""))

        if status not in STATUS_MOVER:
            cnt["nao_mover"] += 1
            log.info(f"  [NAO MOVE] linha={linha_excel} | carimbo={carimbo} | status={status}")
            continue

        if not carimbo:
            cnt["sem_carimbo"] += 1
            log.warning(f"  [SEM CARIMBO] linha={linha_excel}")
            continue

        encontrados = _encontrar_pdfs_por_carimbo(carimbo, arquivos_pdf)

        if not encontrados:
            cnt["nao_encontrados"] += 1
            log.warning(
                f"  [NAO ENCONTRADO] linha={linha_excel} | carimbo={carimbo}"
            )
            continue

        pasta_dst = PASTA_EXISTENTES if status in STATUS_JA_EXISTIAM else PASTA_DESTINO
        for pdf in encontrados:
            chave = str(pdf.resolve()).lower()
            if EVITAR_COPIA_DUPLICADA and chave in ja_movidos:
                continue

            destino_arquivo = pasta_dst / pdf.name
            if GERAR_NOME_UNICO_SE_EXISTIR:
                destino_arquivo = _gerar_nome_unico(destino_arquivo)

            try:
                _mover_com_tentativas(pdf, destino_arquivo)
                ja_movidos.add(chave)
                cnt["movidos"] += 1
                log.info(
                    f"  [MOVIDO] linha={linha_excel} | carimbo={carimbo} "
                    f"-> {destino_arquivo.name}"
                )
            except Exception as e:
                cnt["falhas"] += 1
                falhas.append({
                    "linha_excel": linha_excel,
                    "status":      status,
                    "carimbo":     carimbo,
                    "arquivo":     pdf.name,
                    "erro":        str(e),
                })
                log.error(
                    f"  [ERRO] linha={linha_excel} | carimbo={carimbo} | {e}"
                )

    # Resumo
    log.info("")
    log.info("  ── RESUMO FILTRO ─────────────────────────────────")
    log.info(f"  Linhas no CSV              : {cnt['lidas']}")
    log.info(f"  Ja existia no Consen (skip): {cnt['nao_mover']}")
    log.info(f"  Sem carimbo                : {cnt['sem_carimbo']}")
    log.info(f"  Carimbo sem PDF na origem  : {cnt['nao_encontrados']}")
    log.info(f"  PDFs movidos               : {cnt['movidos']}")
    log.info(f"  Falhas na movimentacao     : {cnt['falhas']}")
    log.info("  ──────────────────────────────────────────────────")

    if falhas:
        relatorio = PASTA_DESTINO / "falhas_mover_cemig.csv"
        pd.DataFrame(falhas).to_csv(relatorio, index=False, encoding="utf-8-sig")
        log.warning(f"  Relatorio de falhas salvo em: {relatorio}")

    return cnt["falhas"] == 0


# =============================================================================
# CLI
# =============================================================================

def _parse_args():
    hoje = dt.date.today()
    p = argparse.ArgumentParser(
        description="Filtro/movimentacao de PDFs CEMIG pos-digitacao"
    )
    p.add_argument(
        "--mes", type=str, default=f"{hoje.month:02d}",
        help="Mes de referencia (padrao: mes atual)",
    )
    p.add_argument(
        "--ano", type=str, default=str(hoje.year),
        help="Ano de referencia (padrao: ano atual)",
    )
    p.add_argument(
        "--csv", type=str, default=None,
        help="Caminho alternativo para o auditoria_resultados.csv",
    )
    p.add_argument(
        "--origem-bt", type=str, default=None,
        help="Caminho alternativo para a pasta de origem dos PDFs BT",
    )
    p.add_argument(
        "--origem", type=str, default=None,
        help="Caminho alternativo generico para a pasta de origem dos PDFs",
    )
    p.add_argument(
        "--rotulo-origem", type=str, default="BT",
        help="Rotulo exibido no log para a origem informada (ex: BT, MT)",
    )
    return p.parse_args()


def main():
    args = _parse_args()
    pasta_origem_arg = args.origem or args.origem_bt
    rotulo_origem = (args.rotulo_origem or "BT").strip().upper() or "BT"

    log.info("=" * 60)
    log.info(f"  CEMIG FILTRO - movimentacao pos-digitacao {rotulo_origem}".center(60))
    log.info("=" * 60)
    log.info(f"  Referencia : {args.mes}/{args.ano}")

    csv_path = Path(args.csv) if args.csv else None
    pasta_origem_bt = Path(pasta_origem_arg) if pasta_origem_arg else None
    ok = mover_pdfs(
        args.mes,
        args.ano,
        csv_path,
        pasta_origem_bt,
        rotulo_origem=rotulo_origem,
    )

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
