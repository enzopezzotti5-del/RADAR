#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pipeline Produção BT  ENZO
===========================

Processa as faturas BT que chegam na pasta:
    \\\\10.10.250.21\\Energia\\CONTASDEENERGIAELETRICA\\BB\\ENZO

Fluxo:
    1) Varre os PDFs da pasta raiz (sem subpastas)
    2) Identifica a concessionária de cada PDF (CELPE ou ELFSM)
    3) Para cada PDF novo (não registrado no master):
           - Consome um carimbo BB_XXXXXXX via indice_master (filelock)
           - Registra no master.csv
           - Copia o PDF para pasta de staging com o nome BB_XXXXXXX.pdf
    4) Executa OCR em cada grupo (CELPE ? ocr_neoenergia.py, ELFSM ? ocr_elfsm.py)
    5) Executa digitação no CONSEN (digitacao_consen_enel.py)
    6) Executa filtro ? move faturas digitadas para "Digitadas/"
    7) Limpa a pasta de staging

Uso:
    python pipeline_producao_bt.py
    python pipeline_producao_bt.py --pasta "//servidor/ENZO"
    python pipeline_producao_bt.py --so-carimbo    # só atribui carimbos, sem OCR
    python pipeline_producao_bt.py --so-ocr
    python pipeline_producao_bt.py --so-digitacao
    python pipeline_producao_bt.py --so-filtro
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unicodedata
from pathlib import Path

import pdfplumber

LOCAL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LOCAL_DIR))

from indice_master import MASTER_FIELDS, MasterIndice


# -- Caminhos ------------------------------------------------------------------

SERVIDOR      = Path("//10.10.250.21/Energia")
PASTA_ENZO    = SERVIDOR / "CONTASDEENERGIAELETRICA" / "BB" / "ENZO"
DIGITADAS_DIR = PASTA_ENZO / "Digitadas"

OCR_NEOENERGIA  = LOCAL_DIR / "ocr" / "ocr_neoenergia.py"
OCR_ELFSM       = LOCAL_DIR / "ocr" / "ocr_elfsm.py"
OCR_ENERGISA    = LOCAL_DIR / "ocr" / "ocr_energisa_bt.py"
DIGITACAO_SCRIPT = LOCAL_DIR / "digitacao_consen" / "digitacao_consen_enel.py"
FILTRO_NEO      = LOCAL_DIR / "digitacao_consen" / "neoenergia_filtro.py"

OCR_SAIDA_DIR   = SERVIDOR / "ARQUIVOS ENZO" / "OCR PRODUCAO BT"

PYTHON_EXE = str(Path(sys.executable).parent / "python.exe")

# -- CONSEN --------------------------------------------------------------------

CONSEN_LOGIN_URL   = "https://consen.acaoengenharia.com.br/login.php"
CONSEN_TARGET_HASH = "#bpg/gestao/fatura/cadastroTabFatura.php"
CONSEN_TARGET_URL  = f"{CONSEN_LOGIN_URL.rsplit('/', 1)[0]}/index.php{CONSEN_TARGET_HASH}"
CONSEN_LINK_HREF   = "bpg/gestao/fatura/cadastroTabFatura.php"
CONSEN_LINK_TEXTO  = "Instalacao"
CONSEN_USUARIO     = "Robo Digitador"
CONSEN_SENHA       = "Acao2026"

# -- Logging -------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("pipeline_producao_bt")

try:
    from pipelines._session_runtime import build_session_command
except ModuleNotFoundError:  # pragma: no cover - fallback para execucoes diretas
    from _session_runtime import build_session_command  # type: ignore[no-redef]


# -- Utilitários ---------------------------------------------------------------

def _mkdir_seguro(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def _to_ascii_upper(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(ch)
    ).upper()


MESES_PT = {
    "JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12,
}


def _rodar(descricao: str, cmd: list[str], env_extra: dict | None = None) -> int:
    cmd = build_session_command(cmd)
    log.info("=" * 60)
    log.info("  %s", descricao)
    log.info("=" * 60)
    log.info("  Comando: %s", " ".join(cmd))

    env = os.environ.copy()
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
    if env_extra:
        env.update({k: str(v) for k, v in env_extra.items()})

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        env=env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )

    def _drenar(stream, prefixo):
        for linha in iter(stream.readline, ""):
            linha = linha.rstrip()
            if linha:
                log.info("  [%s] %s", prefixo, linha)

    t_out = threading.Thread(target=_drenar, args=(proc.stdout, "OUT"), daemon=True)
    t_err = threading.Thread(target=_drenar, args=(proc.stderr, "ERR"), daemon=True)
    t_out.start(); t_err.start()

    interrupted = False
    try:
        while True:
            try:
                proc.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                continue
            except KeyboardInterrupt:
                if proc.poll() is not None:
                    break
                interrupted = True
                log.error("Interrompido. Encerrando subprocesso...")
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait()
                break
    finally:
        t_out.join(timeout=5); t_err.join(timeout=5)

    if interrupted:
        return 130
    code = int(proc.returncode or 0)
    log.info("%s  %s -> exit %d", "OK" if code == 0 else "FALHA", descricao, code)
    return code


def _normalizar_carimbo(valor: str) -> str:
    txt = str(valor or "").strip().upper()
    if not txt:
        return ""
    if txt.endswith(".0"):
        txt = txt[:-2]
    if txt.startswith("BB_"):
        return txt
    if txt.isdigit():
        return f"BB_{txt}"
    return txt


def _estado_por_sistema(sistema: str) -> str:
    sistema_up = str(sistema or "").strip().upper()
    if sistema_up == "CELPE":
        return "PERNAMBUCO"
    if sistema_up == "ELFSM":
        return "ESPIRITO SANTO"
    if sistema_up == "ENERGISA":
        return "RONDONIA"
    return ""


def _atualizar_arquivo_master(master: MasterIndice, carimbo: str, arquivo: Path) -> None:
    """
    Atualiza o campo ARQUIVO do índice master para refletir o caminho físico atual
    do PDF carimbado na pasta de produção.
    """
    linhas: list[dict] = []
    encontrado = False
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(master.master_file, newline="", encoding=enc) as f:
                linhas = list(csv.DictReader(f))
            break
        except UnicodeDecodeError:
            continue
    if not linhas:
        return

    for row in linhas:
        if _normalizar_carimbo(row.get("INDICE", "")) == carimbo:
            row["ARQUIVO"] = str(arquivo)
            encontrado = True

    if not encontrado:
        return

    tmp = master.master_file.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=MASTER_FIELDS, extrasaction="ignore")
        w.writeheader()
        for row in linhas:
            w.writerow(row)
    tmp.replace(master.master_file)


def _carimbar_pdf_na_origem(pdf: Path, carimbo: str, master: MasterIndice) -> Path:
    """
    Garante que o PDF da pasta de produção passe a usar o nome BB_XXXXXXX.pdf na
    própria pasta de origem. O master é sincronizado com o caminho final.
    """
    destino = pdf.with_name(f"{carimbo}.pdf")
    if pdf == destino:
        _atualizar_arquivo_master(master, carimbo, destino)
        return destino

    if destino.exists():
        log.warning("  Arquivo carimbado já existe na origem: %s", destino.name)
        _atualizar_arquivo_master(master, carimbo, destino)
        return destino

    pdf.rename(destino)
    log.info("  Carimbado na origem: %s -> %s", pdf.name, destino.name)
    _atualizar_arquivo_master(master, carimbo, destino)
    return destino


# -- Identificação de concessionária ------------------------------------------

def _identificar_pdf(pdf_path: Path) -> dict:
    """
    Lê a primeira página do PDF e retorna:
        sistema   : 'CELPE' | 'ELFSM' | 'DESCONHECIDA'
        instalacao: número da UC/instalação
        mes_ref   : 'MM-YYYY' (formato master)
        grupo     : 'A' | 'B' | ''
    """
    resultado = {"sistema": "DESCONHECIDA", "instalacao": "", "mes_ref": "", "grupo": ""}
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if not pdf.pages:
                return resultado
            text = pdf.pages[0].extract_text(x_tolerance=1, y_tolerance=1) or ""
        ta = _to_ascii_upper(text)

        # -- Concessionária ----------------------------------------------------
        if "LUZ E FORCA SANTA MARIA" in ta or "27485069000109" in re.sub(r"\D", "", ta):
            resultado["sistema"] = "ELFSM"
        elif "COMPANHIA ENERGETICA DE PERNAMBUCO" in ta or "10835932000108" in re.sub(r"\D", "", ta):
            resultado["sistema"] = "CELPE"
        elif "ENERGISA RONDONIA" in ta or "05914650000166" in re.sub(r"\D", "", ta):
            resultado["sistema"] = "ENERGISA"

        # -- Instalação --------------------------------------------------------
        # ELFSM: campo "IDENTIFICACAO : NNNNN"
        m = re.search(r"IDENTIFICACAO\s*:\s*(\d+)", ta)
        if m:
            resultado["instalacao"] = m.group(1)

        # CELPE: texto sai fragmentado letra por letra  usar o número do filename.
        # Padrão do arquivo: "<numero> - DD.MM.pdf"  ex: "3344896 - 29.04.pdf"
        if not resultado["instalacao"] and resultado["sistema"] == "CELPE":
            m_stem = re.match(r"^(\d+)", pdf_path.stem)
            if m_stem:
                resultado["instalacao"] = m_stem.group(1)
        if not resultado["instalacao"] and resultado["sistema"] == "ENERGISA":
            energisa_patterns = [
                r"\b(\d{1,3}/\d{7}-\d)\b",
                r"UTILIZE\s+O\s+CODIGO:\s*([0-9./-]+)",
                r"\b(\d{1,3}(?:\.\d{3}){2}-\d{2})\b",
            ]
            for pattern in energisa_patterns:
                m_ene = re.search(pattern, ta)
                if m_ene:
                    resultado["instalacao"] = re.sub(r"\D", "", m_ene.group(1))
                    break
            if not resultado["instalacao"]:
                m_nome = re.search(r"UC\s+ANTIGA\s+([0-9.\-]+)", pdf_path.stem, flags=re.IGNORECASE)
                if m_nome:
                    resultado["instalacao"] = re.sub(r"\D", "", m_nome.group(1))

        # -- Mês/ano -----------------------------------------------------------
        # ELFSM: "MES/ANO : MAR/2026"
        m_ref = re.search(r"MES/ANO\s*:\s*([A-Z]{3})/(\d{4})", ta)
        if m_ref:
            mes_num = MESES_PT.get(m_ref.group(1)[:3])
            if mes_num:
                resultado["mes_ref"] = f"{mes_num:02d}-{m_ref.group(2)}"

        # CELPE: "REF:MES/ANO ... \n03/2026 719,72 29/04/2026"
        # A referência fica na linha SEGUINTE ao cabeçalho REF:MES/ANO
        if not resultado["mes_ref"] and resultado["sistema"] == "ENERGISA":
            m_ref3 = re.search(r"\b([A-ZÃ]+)\s*/\s*(\d{4})\b", ta)
            if m_ref3:
                mes_num = MESES_PT.get(m_ref3.group(1)[:3])
                if mes_num:
                    resultado["mes_ref"] = f"{mes_num:02d}-{m_ref3.group(2)}"
        if not resultado["mes_ref"]:
            m_ref2 = re.search(
                r"(\d{2}/\d{4})\s+[\d\.]+,\d{2}\s+\d{2}/\d{2}/\d{4}",
                ta,
            )
            if m_ref2:
                mm, yyyy = m_ref2.group(1).split("/")
                resultado["mes_ref"] = f"{mm}-{yyyy}"
        if not resultado["mes_ref"] and resultado["sistema"] == "ENERGISA":
            m_ref3 = re.search(r"\b([A-ZÇ]+)\s*/\s*(\d{4})\b", ta)
            if m_ref3:
                mes_num = MESES_PT.get(m_ref3.group(1)[:3])
                if mes_num:
                    resultado["mes_ref"] = f"{mes_num:02d}-{m_ref3.group(2)}"

        # -- Grupo tarifário ---------------------------------------------------
        m_grp = re.search(r"GRUPO\s*/\s*SUBGRUPO\s*:\s*([AB])", ta)
        if m_grp:
            resultado["grupo"] = m_grp.group(1)
        if not resultado["grupo"]:
            m_cls = re.search(r"CLASSIFICACAO\s*:\s*([AB])", ta)
            if m_cls:
                resultado["grupo"] = m_cls.group(1)
        if not resultado["grupo"] and resultado["sistema"] == "ENERGISA":
            if "BAIXA TENSAO" in ta or "/ B3" in ta or " B3" in ta:
                resultado["grupo"] = "B"
            elif "GRUPO A" in ta or "A4" in ta or "MEDIA TENSAO" in ta:
                resultado["grupo"] = "A"

    except Exception as exc:
        log.warning("  _identificar_pdf %s: %s", pdf_path.name, exc)

    return resultado


# -- Etapa 1: Atribuição de carimbos via master --------------------------------

def etapa_carimbos(
    pdfs: list[Path],
    master: MasterIndice,
    cache_info: dict[Path, dict] | None = None,
) -> dict[Path, str]:
    """
    Para cada PDF, verifica se já tem carimbo no master.
    Se não tiver, consome um novo BB_XXXXXXX e registra.

    Retorna {pdf_path: carimbo_bb}.
    """
    log.info("=" * 60)
    log.info("  ETAPA 1  Atribuição de carimbos (master index)")
    log.info("=" * 60)

    mapa: dict[Path, str] = {}
    novos = ja_existentes = desconhecidos = 0

    for pdf in pdfs:
        info = (cache_info.get(pdf) if cache_info else None) or _identificar_pdf(pdf)
        sistema = info["sistema"]
        instalacao = info["instalacao"]
        mes_ref = info["mes_ref"]

        carimbo_existente_no_nome = _carimbo_do_nome_se_registrado(master, pdf)
        if carimbo_existente_no_nome:
            pdf_carimbado = _carimbar_pdf_na_origem(pdf, carimbo_existente_no_nome, master)
            mapa[pdf_carimbado] = carimbo_existente_no_nome
            if cache_info is not None:
                cache_info[pdf_carimbado] = info
            log.info("  CARIMBO REAPROVEITADO PELO NOME: %s | UC=%s | %s", carimbo_existente_no_nome, instalacao, mes_ref)
            continue

        if sistema == "DESCONHECIDA":
            log.warning("  IGNORADO (concessionária desconhecida): %s", pdf.name)
            desconhecidos += 1
            continue

        if not instalacao or not mes_ref:
            log.warning("  IGNORADO (sem UC ou mês/ano): %s  |  info=%s", pdf.name, info)
            desconhecidos += 1
            continue

        # Verificar se já existe no master para este sistema
        if master.ja_foi_baixado(instalacao, mes_ref, sistema):
            log.info("  JÁ REGISTRADO %s | UC=%s | %s", sistema, instalacao, mes_ref)
            ja_existentes += 1
            # Mesmo assim precisa do carimbo para o mapa  tenta buscar no CSV
            # (se não achar, gera um novo para não travar o pipeline)
            carimbo = _buscar_carimbo_existente(master, instalacao, mes_ref, sistema)
            if carimbo:
                pdf_carimbado = _carimbar_pdf_na_origem(pdf, carimbo, master)
                mapa[pdf_carimbado] = carimbo
                if cache_info is not None:
                    cache_info[pdf_carimbado] = info
            else:
                carimbo = master.consumir_carimbo()
                master.registrar(
                    indice_bb=carimbo, sistema=sistema,
                    uc=instalacao, mes_ref=mes_ref,
                    arquivo=str(pdf),
                    estado=_estado_por_sistema(sistema),
                    concessionaria=sistema,
                )
                pdf_carimbado = _carimbar_pdf_na_origem(pdf, carimbo, master)
                mapa[pdf_carimbado] = carimbo
                if cache_info is not None:
                    cache_info[pdf_carimbado] = info
                log.info("  NOVO (reregistrado) %s -> %s | UC=%s | %s", sistema, carimbo, instalacao, mes_ref)
            continue

        # Novo: consumir carimbo com filelock
        carimbo = master.consumir_carimbo()
        master.registrar(
            indice_bb=carimbo,
            sistema=sistema,
            uc=instalacao,
            mes_ref=mes_ref,
            arquivo=str(pdf),
            estado=_estado_por_sistema(sistema),
            concessionaria=sistema,
        )
        pdf_carimbado = _carimbar_pdf_na_origem(pdf, carimbo, master)
        mapa[pdf_carimbado] = carimbo
        if cache_info is not None:
            cache_info[pdf_carimbado] = info
        log.info("  NOVO %s -> %s | UC=%s | %s", sistema, carimbo, instalacao, mes_ref)
        novos += 1

    log.info("-" * 60)
    log.info("  Total PDFs        : %d", len(pdfs))
    log.info("  Novos registrados : %d", novos)
    log.info("  Já existentes     : %d", ja_existentes)
    log.info("  Ignorados         : %d  (sem UC/mês ou concessionária desconhecida)", desconhecidos)
    log.info("  Próximo carimbo   : %s", master.proximo_carimbo)
    log.info("  Nota: grupo A (MT) também recebe carimbo  OCR/digitação serão pulados para eles")

    return mapa


def _buscar_carimbo_existente(master: MasterIndice, uc: str, mes_ref: str, sistema: str) -> str:
    """Tenta recuperar o carimbo já registrado no master.csv para esta UC/mês/sistema."""
    import csv
    try:
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                with open(master.master_file, newline="", encoding=enc) as f:
                    for row in csv.DictReader(f):
                        if (
                            row.get("SISTEMA", "").strip().upper() == sistema.upper()
                            and row.get("UC", "").strip().lstrip("0") == uc.strip().lstrip("0")
                            and row.get("MES_REF", "").strip() == mes_ref.strip()
                        ):
                            ind = row.get("INDICE", "").strip()
                            if ind.startswith("BB_"):
                                return ind
                break
            except UnicodeDecodeError:
                continue
    except Exception:
        pass
    return ""


def _carimbo_do_nome_se_registrado(master: MasterIndice, pdf: Path) -> str:
    """Se o arquivo já estiver nomeado como BB_XXXXXXX e esse índice existir no master, reaproveita-o."""
    import csv

    carimbo = _normalizar_carimbo(pdf.stem)
    if not carimbo.startswith("BB_"):
        return ""

    try:
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                with open(master.master_file, newline="", encoding=enc) as f:
                    for row in csv.DictReader(f):
                        if _normalizar_carimbo(row.get("INDICE", "")) == carimbo:
                            return carimbo
                break
            except UnicodeDecodeError:
                continue
    except Exception:
        pass
    return ""


# -- Etapa 2: Copiar PDFs para staging com nomes BB_XXXXXXX -------------------

def etapa_staging(
    mapa: dict[Path, str],
    staging_root: Path,
    cache_info: dict[Path, dict] | None = None,
) -> tuple[Path, Path, Path, dict[str, Path]]:
    """
    Copia PDFs para pastas de staging separadas por sistema:
        staging_root/CELPE/BB_XXXXXXX.pdf
        staging_root/ELFSM/BB_XXXXXXX.pdf
        staging_root/ENERGISA/BB_XXXXXXX.pdf

    Retorna (pasta_staging_celpe, pasta_staging_elfsm, pasta_staging_energisa, mapa_reverso).
    mapa_reverso: {carimbo: original_pdf_path}  usado no pós-filtro para apagar originais.
    """
    log.info("=" * 60)
    log.info("  ETAPA 2  Staging (renomeação temporária com carimbo)")
    log.info("=" * 60)

    pasta_celpe = staging_root / "CELPE"
    pasta_elfsm = staging_root / "ELFSM"
    pasta_energisa = staging_root / "ENERGISA"
    _mkdir_seguro(pasta_celpe)
    _mkdir_seguro(pasta_elfsm)
    _mkdir_seguro(pasta_energisa)

    mapa_reverso: dict[str, Path] = {}
    celpe_count = elfsm_count = energisa_count = 0

    for pdf, carimbo in mapa.items():
        info = cache_info.get(pdf) if cache_info else None
        if info is None:
            info = _identificar_pdf(pdf)
        sistema = info["sistema"]

        if sistema == "CELPE":
            destino = pasta_celpe / f"{carimbo}.pdf"
            celpe_count += 1
        elif sistema == "ENERGISA":
            destino = pasta_energisa / f"{carimbo}.pdf"
            energisa_count += 1
        else:
            destino = pasta_elfsm / f"{carimbo}.pdf"
            elfsm_count += 1

        if not destino.exists():
            shutil.copy2(pdf, destino)
            log.info("  Staging: %s -> %s/%s.pdf", pdf.name, destino.parent.name, carimbo)
        else:
            log.info("  Staging já existe: %s", destino.name)

        mapa_reverso[carimbo] = pdf

    log.info("  CELPE staging: %d PDFs", celpe_count)
    log.info("  ELFSM staging: %d PDFs", elfsm_count)
    log.info("  ENERGISA staging: %d PDFs", energisa_count)

    return pasta_celpe, pasta_elfsm, pasta_energisa, mapa_reverso


# -- Etapa 3: OCR -------------------------------------------------------------

def etapa_ocr_celpe(pasta_staging: Path, xlsx_saida: Path, carimbos_bt: list[str]) -> int:
    """
    carimbos_bt: lista dos carimbos BT (grupo B)  só esses serão processados pelo OCR.
    Os PDFs MT estão no staging mas não são passados ao script.
    """
    if not carimbos_bt:
        log.info("  Nenhum PDF CELPE BT para OCR.")
        return 0
    if not OCR_NEOENERGIA.exists():
        log.error("Script OCR neoenergia não encontrado: %s", OCR_NEOENERGIA)
        return 1

    _mkdir_seguro(xlsx_saida.parent)
    cmd = [
        PYTHON_EXE, str(OCR_NEOENERGIA),
        "--pasta", str(pasta_staging),
        "--tipo", "bt",
        "--saida-bt", str(xlsx_saida),
    ]
    for c in carimbos_bt:
        cmd.extend(["--carimbo", c])

    log.info("  CELPE BT a processar: %d carimbos", len(carimbos_bt))
    return _rodar("OCR CELPE (Neoenergia Pernambuco) BT", cmd)


def etapa_ocr_elfsm(pasta_staging: Path, xlsx_saida: Path, carimbos_bt: list[str]) -> int:
    """
    carimbos_bt: lista dos carimbos BT (grupo B)  só esses serão processados.
    """
    if not carimbos_bt:
        log.info("  Nenhum PDF ELFSM BT para OCR.")
        return 0
    if not OCR_ELFSM.exists():
        log.error("Script OCR ELFSM não encontrado: %s", OCR_ELFSM)
        return 1

    _mkdir_seguro(xlsx_saida.parent)
    cmd = [
        PYTHON_EXE, str(OCR_ELFSM),
        "--pasta", str(pasta_staging),
        "--saida", str(xlsx_saida),
    ]
    for c in carimbos_bt:
        cmd.extend(["--carimbo", c])

    log.info("  ELFSM BT a processar: %d carimbos", len(carimbos_bt))
    return _rodar("OCR ELFSM (Luz e Força Santa Maria) BT", cmd)


# -- Etapa 4: Digitação -------------------------------------------------------

def etapa_ocr_energisa(pasta_staging: Path, xlsx_saida: Path, carimbos_bt: list[str]) -> int:
    if not carimbos_bt:
        log.info("  Nenhum PDF ENERGISA BT para OCR.")
        return 0
    if not OCR_ENERGISA.exists():
        log.error("Script OCR Energisa nÃ£o encontrado: %s", OCR_ENERGISA)
        return 1

    _mkdir_seguro(xlsx_saida.parent)
    cmd = [
        PYTHON_EXE, str(OCR_ENERGISA),
        "--pasta", str(pasta_staging),
        "--saida", str(xlsx_saida),
    ]
    for c in carimbos_bt:
        cmd.extend(["--carimbo", c])

    log.info("  ENERGISA BT a processar: %d carimbos", len(carimbos_bt))
    return _rodar("OCR ENERGISA (Rondonia) BT", cmd)


def etapa_digitacao(xlsx: Path, label: str, pasta_saida: Path) -> int:
    if not xlsx.exists():
        log.warning("  XLSX não encontrado, digitação pulada: %s", xlsx)
        return 0
    if not DIGITACAO_SCRIPT.exists():
        log.error("Script de digitação não encontrado: %s", DIGITACAO_SCRIPT)
        return 1

    _mkdir_seguro(pasta_saida)
    env_extra = {
        "ENEL_EXCEL_PATH":        str(xlsx),
        "CONSEN_PIPELINE_SAIDA":  str(pasta_saida),
        "CONSEN_INTERATIVO_FECHAR": "0",
        "DIGITACAO_FATOR_VELOCIDADE":    "0.25",
        "CONSEN_PERMITIR_LOTE_COMPLETO": "1",
        "CONSEN_LOGIN_URL":       CONSEN_LOGIN_URL,
        "CONSEN_TARGET_HASH":     CONSEN_TARGET_HASH,
        "CONSEN_TARGET_URL":      CONSEN_TARGET_URL,
        "CONSEN_LINK_HREF":       CONSEN_LINK_HREF,
        "CONSEN_LINK_TEXTO":      CONSEN_LINK_TEXTO,
        "CONSEN_USUARIO":         CONSEN_USUARIO,
        "CONSEN_SENHA":           CONSEN_SENHA,
    }
    return _rodar(f"DIGITAÇÃO {label} ({xlsx.name})", [PYTHON_EXE, str(DIGITACAO_SCRIPT)], env_extra=env_extra)


# -- Etapa 5: Filtro / mover Digitadas ----------------------------------------

def _ler_carimbos_digitados(auditoria: Path) -> set[str]:
    """
    Lê o auditoria_resultados.csv e retorna o conjunto de carimbos cujo
    status permite mover o PDF (digitado ou pulado por já existir).
    """
    STATUS_MOVIVEIS = {
        "sucesso_auditoria",
        "auditoria_sem_valor",
        "pulado_carimbo_existente",
        "pulado_referencia_existente",
    }
    carimbos: set[str] = set()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(auditoria, newline="", encoding=enc) as f:
                reader = csv.DictReader(f, delimiter=";")
                for row in reader:
                    status = str(row.get("status", "")).strip().lower()
                    if status in STATUS_MOVIVEIS:
                        carimbo = _normalizar_carimbo(row.get("carimbo", ""))
                        if carimbo:
                            carimbos.add(carimbo)
            return carimbos
        except UnicodeDecodeError:
            continue
        except Exception as exc:
            log.warning("  _ler_carimbos_digitados: %s", exc)
            return carimbos
    return carimbos


def _carregar_arquivo_original_por_carimbo(master: MasterIndice, carimbos: set[str]) -> dict[str, Path]:
    """
    Consulta o master.csv e reconstrói {BB_XXXXXXX: arquivo_original} para um
    rerun de filtro quando o staging temporário já não existe mais.
    """
    if not carimbos:
        return {}

    mapa: dict[str, Path] = {}
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(master.master_file, newline="", encoding=enc) as f:
                for row in csv.DictReader(f):
                    carimbo = _normalizar_carimbo(row.get("INDICE", ""))
                    if carimbo not in carimbos:
                        continue
                    arquivo = str(row.get("ARQUIVO", "")).strip()
                    if not arquivo:
                        continue
                    original = Path(arquivo)
                    if original.exists():
                        mapa[carimbo] = original
            return mapa
        except UnicodeDecodeError:
            continue
        except Exception as exc:
            log.warning("  Falha ao ler master para reconstruir staging: %s", exc)
            return mapa
    return mapa


def _reconstruir_staging_para_filtro(
    label: str,
    auditoria: Path,
    pasta_staging: Path,
    master: MasterIndice,
) -> dict[str, Path]:
    """
    Recria o staging mínimo necessário para o filtro final, copiando apenas os
    PDFs cujo status permite movimento para Digitadas.
    """
    carimbos = _ler_carimbos_digitados(auditoria)
    if not carimbos:
        log.info("  [%s] Nenhum carimbo elegível para reconstrução de staging.", label)
        return {}

    mapa_reverso = _carregar_arquivo_original_por_carimbo(master, carimbos)
    if not mapa_reverso:
        log.warning("  [%s] Não foi possível reconstruir staging via master.", label)
        return {}

    _mkdir_seguro(pasta_staging)
    recriados = faltantes = erros = 0
    for carimbo in sorted(carimbos):
        original = mapa_reverso.get(carimbo)
        if not original:
            faltantes += 1
            log.warning("  [%s] Original ausente no master para %s", label, carimbo)
            continue
        destino = pasta_staging / f"{carimbo}.pdf"
        if not destino.exists():
            try:
                shutil.copy2(original, destino)
                recriados += 1
            except Exception as exc:
                erros += 1
                log.warning(
                    "  [%s] Falha ao copiar %s -> %s: %s",
                    label, original.name, destino.name, exc
                )

    log.info(
        "  [%s] Staging reconstruído: %d copiado(s) | %d faltante(s) | %d erro(s)",
        label, recriados, faltantes, erros
    )
    return mapa_reverso


def etapa_filtro(
    label: str,
    pasta_saida: Path,
    pasta_staging: Path,
    mapa_reverso: dict[str, Path],
) -> int:
    """
    1) Lê auditoria_resultados.csv
    2) Move BB_XXXXXXX.pdf do staging ? Digitadas/ (via neoenergia_filtro.py)
    3) Apaga os originais da pasta ENZO para os carimbos movidos com sucesso
    4) Atualiza o master
    """
    auditoria = pasta_saida / "auditoria_resultados.csv"
    if not auditoria.exists():
        log.warning("  auditoria_resultados.csv não encontrado em %s  filtro pulado", pasta_saida)
        return 0

    if not FILTRO_NEO.exists():
        log.warning("  Filtro script não encontrado: %s  filtro pulado", FILTRO_NEO)
        return 0

    _mkdir_seguro(DIGITADAS_DIR)

    # NEO_FILTRO_ROOT aponta para o staging, onde os arquivos já têm nome BB_XXXXXXX.pdf
    env_extra = {
        "NEO_FILTRO_CSV":     str(auditoria),
        "NEO_FILTRO_ROOT":    str(pasta_staging),
        "NEO_FILTRO_DESTINO": str(DIGITADAS_DIR),
    }
    code = _rodar(f"FILTRO {label}", [PYTHON_EXE, str(FILTRO_NEO)], env_extra=env_extra)

    # Apagar os originais da ENZO para os carimbos que foram movidos
    carimbos_digitados = _ler_carimbos_digitados(auditoria)
    apagados = nao_encontrados = 0
    for carimbo in carimbos_digitados:
        original = mapa_reverso.get(carimbo)
        if original and original.exists():
            try:
                original.unlink()
                log.info("  Original removido da ENZO: %s", original.name)
                apagados += 1
            except Exception as exc:
                log.warning("  Falha ao remover original %s: %s", original.name, exc)
        else:
            nao_encontrados += 1

    log.info("  Originais removidos da ENZO: %d  |  não encontrados: %d", apagados, nao_encontrados)

    # Atualiza master
    try:
        from indice_master import marcar_digitados_do_auditoria
        contadores = marcar_digitados_do_auditoria(auditoria, MasterIndice())
        log.info("  [MASTER] %s", contadores)
    except Exception as exc:
        log.warning("  [MASTER] Não foi possível atualizar: %s", exc)

    return code


# -- Slug persistente ---------------------------------------------------------

_SLUG_FILE = LOCAL_DIR / "pipelines" / ".producao_bt_slug"


def _carregar_ou_criar_slug() -> str:
    """
    Retorna o slug da última execução (para reaproveitamento com --so-*).
    Se não existir, cria um novo baseado no timestamp atual.
    """
    if _SLUG_FILE.exists():
        slug = _SLUG_FILE.read_text(encoding="utf-8").strip()
        if slug:
            return slug
    slug = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    _SLUG_FILE.write_text(slug, encoding="utf-8")
    return slug


def _novo_slug() -> str:
    slug = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    _SLUG_FILE.write_text(slug, encoding="utf-8")
    return slug


# -- CLI -----------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Pipeline Produção BT  ENZO")
    p.add_argument("--pasta", type=str, default=str(PASTA_ENZO),
                   help="Pasta com os PDFs BT (default: pasta ENZO na rede)")
    p.add_argument("--so-carimbo",    action="store_true", help="Só atribui carimbos via master")
    p.add_argument("--so-ocr",        action="store_true", help="Só OCR (carimbos e staging já feitos)")
    p.add_argument("--so-digitacao",  action="store_true", help="Só digitação (XLSXs já existem)")
    p.add_argument("--so-filtro",     action="store_true", help="Só filtro/mover Digitadas")
    p.add_argument("--novo-slug",     action="store_true", help="Força novo slug (nova execução do zero)")
    p.add_argument("--manter-staging", action="store_true",
                   help="Não apaga pasta de staging ao final")
    p.add_argument("--slug", type=str, default="",
                   help="Slug de uma execucao anterior para retomar OCR/digitacao/filtro")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    pasta_pdfs = Path(args.pasta.strip())

    # Slug: persistente entre execuções parciais (--so-ocr, --so-digitacao, etc.)
    # --novo-slug força recomeçar do zero
    if args.slug:
        slug = args.slug.strip()
        _SLUG_FILE.write_text(slug, encoding="utf-8")
    else:
        slug = _novo_slug() if args.novo_slug or not (
            args.so_ocr or args.so_digitacao or args.so_filtro
        ) else _carregar_ou_criar_slug()

    staging_root = Path(tempfile.gettempdir()) / f"producao_bt_staging_{slug}"
    xlsx_celpe   = OCR_SAIDA_DIR / f"ocr_celpe_BT_{slug}.xlsx"
    xlsx_elfsm   = OCR_SAIDA_DIR / f"ocr_elfsm_BT_{slug}.xlsx"
    xlsx_energisa = OCR_SAIDA_DIR / f"ocr_energisa_BT_{slug}.xlsx"
    saida_celpe  = OCR_SAIDA_DIR / f"digitacao_CELPE_{slug}"
    saida_elfsm  = OCR_SAIDA_DIR / f"digitacao_ELFSM_{slug}"
    saida_energisa = OCR_SAIDA_DIR / f"digitacao_ENERGISA_{slug}"
    pasta_staging_celpe = staging_root / "CELPE"
    pasta_staging_elfsm = staging_root / "ELFSM"
    pasta_staging_energisa = staging_root / "ENERGISA"

    log.info("=" * 60)
    log.info("  PIPELINE PRODUÇÃO BT")
    log.info("=" * 60)
    log.info("  Slug        : %s", slug)
    log.info("  Pasta PDFs  : %s", pasta_pdfs)
    log.info("  Staging     : %s", staging_root)
    log.info("  Digitadas   : %s", DIGITADAS_DIR)

    if not pasta_pdfs.exists():
        log.error("Pasta não encontrada: %s", pasta_pdfs)
        return 1

    # Listar PDFs (apenas raiz, sem subpastas)
    pdfs = sorted(p for p in pasta_pdfs.glob("*.pdf") if p.is_file())
    if not pdfs:
        log.warning("Nenhum PDF encontrado em: %s", pasta_pdfs)
        return 0
    log.info("  PDFs encontrados: %d", len(pdfs))

    modo_debug = args.so_carimbo or args.so_ocr or args.so_digitacao or args.so_filtro
    falhas: list[str] = []

    # Cache de identificação para não ler cada PDF duas vezes (carimbos + staging)
    cache_info: dict[Path, dict] = {}

    # -- 1) Carimbos ----------------------------------------------------------
    mapa_carimbo: dict[Path, str] = {}
    mapa_reverso: dict[str, Path] = {}

    if not args.so_ocr and not args.so_digitacao and not args.so_filtro:
        # Pre-identificar todos os PDFs (1 leitura por PDF)
        for pdf in pdfs:
            cache_info[pdf] = _identificar_pdf(pdf)

        master = MasterIndice()
        mapa_carimbo = etapa_carimbos(pdfs, master, cache_info=cache_info)
        if not mapa_carimbo:
            log.warning("Nenhum PDF mapeado  encerrando.")
            return 0
        if args.so_carimbo:
            log.info("  --so-carimbo: encerrado após atribuição de carimbos.")
            return 0
    else:
        log.info("[debug] Pulando etapa de carimbos.")

    # -- 2) Staging -----------------------------------------------------------
    if not args.so_digitacao and not args.so_filtro and mapa_carimbo:
        pasta_staging_celpe, pasta_staging_elfsm, pasta_staging_energisa, mapa_reverso = etapa_staging(
            mapa_carimbo, staging_root, cache_info=cache_info
        )
    else:
        log.info("[debug] Pulando staging.")

    # -- 2b) Separar BT/MT por concessionária (para OCR/digitação seletivos) --
    bt_carimbos_celpe: list[str] = []
    bt_carimbos_elfsm: list[str] = []
    bt_carimbos_energisa: list[str] = []
    mt_carimbos: list[str] = []

    if mapa_reverso:
        for carimbo, original in mapa_reverso.items():
            info = cache_info.get(original) or {}
            sistema = info.get("sistema", "")
            grupo   = info.get("grupo", "")
            if grupo == "A":
                mt_carimbos.append(carimbo)
                log.info("  MT (grupo A)  carimbo atribuído, OCR/digitação pulados: %s | UC=%s",
                         carimbo, info.get("instalacao", ""))
            elif sistema == "CELPE":
                bt_carimbos_celpe.append(carimbo)
            elif sistema == "ELFSM":
                bt_carimbos_elfsm.append(carimbo)
            elif sistema == "ENERGISA":
                bt_carimbos_energisa.append(carimbo)

        log.info(
            "  BT CELPE : %d | BT ELFSM : %d | BT ENERGISA : %d | MT (pulado): %d",
            len(bt_carimbos_celpe), len(bt_carimbos_elfsm), len(bt_carimbos_energisa), len(mt_carimbos)
        )

    # -- 3) OCR ---------------------------------------------------------------
    if not args.so_digitacao and not args.so_filtro:
        if bt_carimbos_celpe:
            cod = etapa_ocr_celpe(pasta_staging_celpe, xlsx_celpe, bt_carimbos_celpe)
            if cod != 0:
                falhas.append("OCR_CELPE")
                if not modo_debug:
                    return 1
        if bt_carimbos_elfsm:
            cod = etapa_ocr_elfsm(pasta_staging_elfsm, xlsx_elfsm, bt_carimbos_elfsm)
            if cod != 0:
                falhas.append("OCR_ELFSM")
                if not modo_debug:
                    return 1
        if bt_carimbos_energisa:
            cod = etapa_ocr_energisa(pasta_staging_energisa, xlsx_energisa, bt_carimbos_energisa)
            if cod != 0:
                falhas.append("OCR_ENERGISA")
                if not modo_debug:
                    return 1
    else:
        log.info("[debug] Pulando OCR.")

    # -- 4) Digitação ---------------------------------------------------------
    if not args.so_ocr and not args.so_filtro:
        if xlsx_celpe.exists():
            cod = etapa_digitacao(xlsx_celpe, "CELPE", saida_celpe)
            if cod != 0:
                falhas.append("DIGITACAO_CELPE")
        if xlsx_elfsm.exists():
            cod = etapa_digitacao(xlsx_elfsm, "ELFSM", saida_elfsm)
            if cod != 0:
                falhas.append("DIGITACAO_ELFSM")
        if xlsx_energisa.exists():
            cod = etapa_digitacao(xlsx_energisa, "ENERGISA", saida_energisa)
            if cod != 0:
                falhas.append("DIGITACAO_ENERGISA")
    else:
        log.info("[debug] Pulando digitação.")

    # -- 5) Filtro ------------------------------------------------------------
    if not args.so_ocr and not args.so_digitacao:
        # mapa_reverso_bt: apenas os originais BT para apagar da ENZO após digitação
        if args.so_filtro and (
            not pasta_staging_celpe.exists() or not any(pasta_staging_celpe.glob("*.pdf"))
        ):
            master = MasterIndice()
            mapa_reverso.update(
                _reconstruir_staging_para_filtro(
                    "CELPE",
                    saida_celpe / "auditoria_resultados.csv",
                    pasta_staging_celpe,
                    master,
                )
            )
            mapa_reverso.update(
                _reconstruir_staging_para_filtro(
                    "ELFSM",
                    saida_elfsm / "auditoria_resultados.csv",
                    pasta_staging_elfsm,
                    master,
                )
            )
            mapa_reverso.update(
                _reconstruir_staging_para_filtro(
                    "ENERGISA",
                    saida_energisa / "auditoria_resultados.csv",
                    pasta_staging_energisa,
                    master,
                )
            )

        mapa_reverso_bt = {
            c: p for c, p in mapa_reverso.items()
            if c in bt_carimbos_celpe or c in bt_carimbos_elfsm or c in bt_carimbos_energisa
        }
        if args.so_filtro and not mapa_reverso_bt:
            mapa_reverso_bt = dict(mapa_reverso)
        etapa_filtro("CELPE", saida_celpe, pasta_staging_celpe, mapa_reverso_bt)
        etapa_filtro("ELFSM", saida_elfsm, pasta_staging_elfsm, mapa_reverso_bt)
        etapa_filtro("ENERGISA", saida_energisa, pasta_staging_energisa, mapa_reverso_bt)
    else:
        log.info("[debug] Pulando filtro.")

    # -- Limpeza do staging ---------------------------------------------------
    if staging_root.exists() and not args.manter_staging:
        try:
            shutil.rmtree(staging_root)
            log.info("  Staging removido: %s", staging_root)
        except Exception as exc:
            log.warning("  Falha ao remover staging: %s", exc)

    # Apaga o slug ao concluir com sucesso (próxima execução será nova)
    if not falhas:
        try:
            _SLUG_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    # -- Resumo ---------------------------------------------------------------
    if falhas:
        log.error("PIPELINE COM FALHAS: %s", ", ".join(falhas))
        return 1

    log.info("PIPELINE PRODUÇÃO BT CONCLUÍDO COM SUCESSO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
