import os
import re
import shutil
import time
import pandas as pd
from pathlib import Path

import os as _os

try:
    from digitacao_consen.auditoria_schema import ler_auditoria_csv_flexivel
except ModuleNotFoundError:
    from auditoria_schema import ler_auditoria_csv_flexivel  # type: ignore

# ==============================
# CONFIGURAÇÕES
# Aceita override via variáveis de ambiente (injetadas pelo pipeline_enel.py)
# ==============================
CSV_PATH           = _os.environ.get("ENEL_FILTRO_CSV",          "//10.10.250.21/Energia/ARQUIVOS ENZO/ENEL_pipeline_saida/auditoria_resultados.csv")
PASTA_PDFS         = _os.environ.get("ENEL_FILTRO_PDFS",         "//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD ENEL/03-2026/BT")
PASTA_DESTINO      = _os.environ.get("ENEL_FILTRO_DESTINO",      "//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO/Digitadas")
PASTA_JA_EXISTIAM  = _os.environ.get("ENEL_FILTRO_JA_EXISTIAM",  "//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO/Ja_existiam_no_Consen")

# Nome da coluna que define se deve pular ou não
COLUNA_STATUS = "status"

# PDFs digitados pelo robô vão para Digitadas.
# PDFs que já existiam no Consen vão para Ja_existiam_no_Consen.
# Em ambos os casos saem da pasta de entrada.
STATUSES_MOVER     = {"sucesso_auditoria", "auditoria_sem_valor", "pulado_referencia_existente"}
STATUSES_JA_EXISTIAM = {"pulado_referencia_existente"}

VALOR_IGNORAR = ""  # mantido para compatibilidade

# Nome da coluna do carimbo
COLUNA_CARIMBO = "carimbo"

# Se True, procura ignorando maiúsculas/minúsculas
BUSCA_CASE_INSENSITIVE = True
TENTATIVAS_COPIA = 3
ESPERA_ENTRE_TENTATIVAS = 1  # segundos


def normalizar_texto(valor):
    if pd.isna(valor):
        return ""
    return str(valor).strip()


def ler_csv_flexivel(csv_path: Path) -> pd.DataFrame:
    """
    Lê a auditoria mesmo quando o campo de detalhes vier quebrado com ';'
    extras, preservando ao menos carimbo e status.
    """
    rows_padrao = ler_auditoria_csv_flexivel(csv_path)
    if rows_padrao:
        return pd.DataFrame(rows_padrao)

    linhas = []
    for enc in ("utf-8-sig", "utf-8", "latin1"):
        try:
            linhas = csv_path.read_text(encoding=enc).splitlines()
            break
        except UnicodeDecodeError:
            continue

    if linhas:
        cabecalho = [c.strip() for c in linhas[0].split(";")]
        cabecalho_lower = [c.lower() for c in cabecalho]
        if "carimbo" in cabecalho_lower and "status" in cabecalho_lower:
            idx_carimbo = cabecalho_lower.index("carimbo")
            idx_status = cabecalho_lower.index("status")
            rows = []
            for linha in linhas[1:]:
                if not linha.strip():
                    continue
                partes = linha.split(";")
                row = {}
                for i, coluna in enumerate(cabecalho):
                    if i == idx_status and len(partes) > len(cabecalho):
                        row[coluna] = partes[-1].strip()
                    else:
                        row[coluna] = partes[i].strip() if i < len(partes) else ""
                if idx_carimbo < len(partes):
                    row[cabecalho[idx_carimbo]] = partes[idx_carimbo].strip()
                rows.append(row)
            if rows:
                return pd.DataFrame(rows)

    tentativas = [
        {"sep": ";", "encoding": "utf-8-sig"},
        {"sep": ",", "encoding": "utf-8-sig"},
        {"sep": ";", "encoding": "utf-8"},
        {"sep": ",", "encoding": "utf-8"},
        {"sep": ";", "encoding": "latin1"},
        {"sep": ",", "encoding": "latin1"},
    ]
    ultimo_erro = None
    for tentativa in tentativas:
        try:
            df = pd.read_csv(csv_path, dtype=str, **tentativa)
            if len(df.columns) > 1:
                return df
        except Exception as e:
            ultimo_erro = e
    raise RuntimeError(f"Nao foi possivel ler o CSV {csv_path}: {ultimo_erro}")


def encontrar_pdfs_por_carimbo(carimbo, arquivos_pdf):
    encontrados = []
    carimbo_norm = normalizar_texto(carimbo)
    carimbo_sem_prefixo = re.sub(r"^BB_", "", carimbo_norm, flags=re.IGNORECASE)
    carimbo_digitos = re.sub(r"\D", "", carimbo_sem_prefixo)

    if BUSCA_CASE_INSENSITIVE:
        carimbo_cmp = carimbo_norm.lower()
        carimbo_cmp_sem_prefixo = carimbo_sem_prefixo.lower()
        _pat = re.compile(r'(?<![0-9a-zA-Z])' + re.escape(carimbo_cmp) + r'(?![0-9a-zA-Z])')
        _pat_sem_prefixo = re.compile(r'(?<![0-9a-zA-Z])' + re.escape(carimbo_cmp_sem_prefixo) + r'(?![0-9a-zA-Z])')
        for pdf in arquivos_pdf:
            nome_lower = pdf.name.lower()
            stem_lower = pdf.stem.lower()
            stem_digits = re.sub(r"\D", "", pdf.stem)
            # Critério 1: match exato do stem (sem extensão)
            if stem_lower == carimbo_cmp or stem_lower == carimbo_cmp_sem_prefixo:
                encontrados.append(pdf)
            # Critério 2: carimbo como palavra completa (sem dígitos/letras adjacentes)
            elif _pat.search(nome_lower):
                encontrados.append(pdf)
            elif carimbo_cmp_sem_prefixo and _pat_sem_prefixo.search(nome_lower):
                encontrados.append(pdf)
            # Critério 3: mesmos dígitos, útil quando o PDF avulso ainda não foi renomeado para BB_
            elif carimbo_digitos and stem_digits == carimbo_digitos:
                encontrados.append(pdf)
    else:
        _pat = re.compile(r'(?<![0-9a-zA-Z])' + re.escape(carimbo_norm) + r'(?![0-9a-zA-Z])')
        _pat_sem_prefixo = re.compile(r'(?<![0-9a-zA-Z])' + re.escape(carimbo_sem_prefixo) + r'(?![0-9a-zA-Z])')
        for pdf in arquivos_pdf:
            stem_digits = re.sub(r"\D", "", pdf.stem)
            if pdf.stem == carimbo_norm or pdf.stem == carimbo_sem_prefixo or _pat.search(pdf.name):
                encontrados.append(pdf)
            elif carimbo_sem_prefixo and _pat_sem_prefixo.search(pdf.name):
                encontrados.append(pdf)
            elif carimbo_digitos and stem_digits == carimbo_digitos:
                encontrados.append(pdf)

    return encontrados


def gerar_nome_unico(destino_arquivo: Path) -> Path:
    """
    Se o arquivo já existir, cria um novo nome:
    exemplo.pdf -> exemplo_1.pdf -> exemplo_2.pdf
    """
    if not destino_arquivo.exists():
        return destino_arquivo

    stem = destino_arquivo.stem
    suffix = destino_arquivo.suffix
    parent = destino_arquivo.parent

    contador = 1
    while True:
        novo = parent / f"{stem}_{contador}{suffix}"
        if not novo.exists():
            return novo
        contador += 1


def copiar_com_tentativas(origem: Path, destino: Path):
    """
    Tenta copiar algumas vezes. Se falhar por arquivo em uso, relança no final.
    """
    ultimo_erro = None

    for tentativa in range(1, TENTATIVAS_COPIA + 1):
        try:
            shutil.move(str(origem), destino)
            return True
        except PermissionError as e:
            ultimo_erro = e
            print(f"[TENTATIVA {tentativa}/{TENTATIVAS_COPIA}] Arquivo em uso: {origem.name}")
            time.sleep(ESPERA_ENTRE_TENTATIVAS)
        except Exception as e:
            raise e

    raise ultimo_erro


def _destino_por_status(status: str) -> Path:
    if status in STATUSES_JA_EXISTIAM:
        return Path(PASTA_JA_EXISTIAM)
    return Path(PASTA_DESTINO)


def copiar_pdfs_do_csv():
    pasta_pdfs = Path(PASTA_PDFS)
    pasta_destino = Path(PASTA_DESTINO)
    pasta_destino.mkdir(parents=True, exist_ok=True)
    Path(PASTA_JA_EXISTIAM).mkdir(parents=True, exist_ok=True)

    df = ler_csv_flexivel(Path(CSV_PATH))

    if COLUNA_STATUS not in df.columns:
        raise ValueError(f"Coluna '{COLUNA_STATUS}' não encontrada no CSV.")
    if COLUNA_CARIMBO not in df.columns:
        raise ValueError(f"Coluna '{COLUNA_CARIMBO}' não encontrada no CSV.")

    # Busca recursiva: PDFs podem estar em subpastas por mês/tipo (03-2026/BT/, etc.)
    arquivos_pdf = list(pasta_pdfs.glob("**/*.pdf"))

    if not arquivos_pdf:
        print("Nenhum PDF encontrado na pasta de origem.")
        return

    total_linhas = 0
    total_processadas = 0
    total_ignoradas = 0
    total_sem_carimbo = 0
    total_copiados = 0

    falhas = []
    ja_copiados = set()

    for idx, row in df.iterrows():
        total_linhas += 1

        status = normalizar_texto(row[COLUNA_STATUS])
        carimbo = normalizar_texto(row[COLUNA_CARIMBO])

        if status not in STATUSES_MOVER:
            total_ignoradas += 1
            continue

        total_processadas += 1

        if not carimbo:
            print(f"[LINHA {idx + 2}] Carimbo vazio.")
            total_sem_carimbo += 1
            continue

        encontrados = encontrar_pdfs_por_carimbo(carimbo, arquivos_pdf)

        if not encontrados:
            print(f"[LINHA {idx + 2}] Nenhum PDF encontrado para carimbo: {carimbo}")
            continue

        for pdf in encontrados:
            chave = str(pdf.resolve()).lower()
            if chave in ja_copiados:
                continue

            pasta_destino_status = _destino_por_status(status)
            destino_arquivo = pasta_destino_status / pdf.name
            destino_arquivo = gerar_nome_unico(destino_arquivo)

            try:
                copiar_com_tentativas(pdf, destino_arquivo)
                ja_copiados.add(chave)
                total_copiados += 1
                print(f"[MOVIDO] Carimbo '{carimbo}' -> {pdf.name}")
            except PermissionError as e:
                falhas.append({
                    "linha_csv": idx + 2,
                    "carimbo": carimbo,
                    "arquivo": pdf.name,
                    "erro": str(e)
                })
                print(f"[ERRO] Arquivo em uso, não foi possível copiar: {pdf.name}")
            except Exception as e:
                falhas.append({
                    "linha_csv": idx + 2,
                    "carimbo": carimbo,
                    "arquivo": pdf.name,
                    "erro": str(e)
                })
                print(f"[ERRO] Falha ao copiar {pdf.name}: {e}")

    print("\n===== RESUMO =====")
    print(f"Total de linhas no CSV: {total_linhas}")
    print(f"Linhas ignoradas (status fora de {STATUSES_MOVER}): {total_ignoradas}")
    print(f"Linhas processadas: {total_processadas}")
    print(f"Linhas com carimbo vazio: {total_sem_carimbo}")
    print(f"PDFs copiados: {total_copiados}")
    print(f"Falhas: {len(falhas)}")

    if falhas:
        relatorio_falhas = pasta_destino / "falhas_copia.csv"
        pd.DataFrame(falhas).to_csv(relatorio_falhas, index=False, encoding="utf-8-sig")
        print(f"Relatório de falhas salvo em: {relatorio_falhas}")


if __name__ == "__main__":
    copiar_pdfs_do_csv()
