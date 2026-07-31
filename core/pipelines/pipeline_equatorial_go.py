#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pipeline_equatorial_go.py
--------------------------
Pipeline sequencial Equatorial Goiás BT/MT:
  1. OCR       — extrai faturas de DOWNLOAD EQUATORIAL ? OCR EQUATORIAL GO (xlsx)
  2. Digitação — digita xlsx no Consen via Selenium
  3. Filtro    — move PDFs digitados para a pasta Digitadas

Uso:
    python pipeline_equatorial_go.py                    # mês/ano atual, BT+MT
    python pipeline_equatorial_go.py --mes 03 --ano 2026
    python pipeline_equatorial_go.py --tipo bt
    python pipeline_equatorial_go.py --so-ocr
    python pipeline_equatorial_go.py --so-digitacao
    python pipeline_equatorial_go.py --so-filtro
    python pipeline_equatorial_go.py --recriar
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import logging
import os
import re
import subprocess
import shutil
import sys
import threading
from pathlib import Path

try:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from sessao_meta import (
        ler_session_id as _sm_ler_sid,
        atualizar_etapa as _sm_etapa,
        atualizar_status as _sm_status,
        registrar_carimbo_arquivo as _sm_reg_carimbo,
    )
    _SM_OK = True
except ImportError:
    _SM_OK = False
    def _sm_ler_sid(*a, **k): return None  # type: ignore[misc]
    def _sm_etapa(*a, **k): pass  # type: ignore[misc]
    def _sm_status(*a, **k): pass  # type: ignore[misc]
    def _sm_reg_carimbo(*a, **k): pass  # type: ignore[misc]

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

LOCAL_DIR        = Path(__file__).parent.parent   # raiz do projeto

OCR_SCRIPT       = LOCAL_DIR / "ocr"              / "ocr_equatorial_go.py"
DIGITACAO_SCRIPT = LOCAL_DIR / "digitacao_consen" / "digitacao_consen_enel.py"
FILTRO_SCRIPT    = LOCAL_DIR / "digitacao_consen" / "enel_filtro.py"

SERVIDOR         = Path("//10.10.250.21/Energia")
DOWNLOAD_DIR     = SERVIDOR / "ARQUIVOS ENZO" / "DOWNLOAD EQUATORIAL"
OCR_SAIDA_DIR    = SERVIDOR / "ARQUIVOS ENZO" / "OCR EQUATORIAL GO"
PIPELINE_SAIDA   = SERVIDOR / "ARQUIVOS ENZO" / "EQUATORIAL_pipeline_saida"
DIGITADAS_DIR    = SERVIDOR / "CONTASDEENERGIAELETRICA" / "BB" / "ENZO" / "Digitadas"

PYTHON_EXE = str(Path(sys.executable).parent / "python.exe")

# =============================================================================
# LOG
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


# =============================================================================
# ETAPA 0 — CARIMBAR PDFs NO MASTER INDEX
# =============================================================================

_RE_CARIMBO = re.compile(r"^BB_\d+$", re.IGNORECASE)


def etapa_carimbar(mes: str, ano: str, tipo: str, _staging_root: Path | None = None, _session_id: str | None = None) -> bool:
    """
    Primeira etapa do pipeline:
      1. Carrega o MasterIndice
      2. Varre as pastas BT/MT de DOWNLOAD EQUATORIAL/{mes}-{ano}/
      3. Para cada PDF sem prefixo BB_, consome um carimbo, renomeia e registra no master
      4. PDFs que já têm BB_ são ignorados (idempotente)

    Retorna True se ao menos uma pasta existiu (mesmo sem novos PDFs).
    """
    sys.path.insert(0, str(LOCAL_DIR))
    sys.path.insert(0, str(LOCAL_DIR.parent))  # ENERGIA/ root — onde indice_master.py está
    from indice_master import MasterIndice  # noqa

    log.info("=" * 60)
    log.info("  CARIMBAR PDFs — MASTER INDEX")
    log.info("=" * 60)

    master = MasterIndice()

    todos_pdfs = sorted(DOWNLOAD_DIR.glob("*.pdf"))
    ja_carimbados = [p for p in todos_pdfs if _RE_CARIMBO.fullmatch(p.stem)]
    sem_carimbo = [p for p in todos_pdfs if not _RE_CARIMBO.fullmatch(p.stem)]

    log.info(f"  {tipo.upper()}: {len(todos_pdfs)} PDFs flat — {len(ja_carimbados)} ja carimbados, {len(sem_carimbo)} para carimbar")

    for pdf in sem_carimbo:
            indice_bb  = master.consumir_carimbo()
            novo_caminho = DOWNLOAD_DIR / f"{indice_bb}.pdf"
            pdf.rename(novo_caminho)
            master.registrar(
                indice_bb=indice_bb,
                sistema="EQUATORIAL",
                uc="",
                mes_ref=f"{mes}-{ano}",
                fatura_id="",
                cnpj="",
                estado="GO",
                instalacao="",
                arquivo=str(novo_caminho),
            )
            log.info(f"  {pdf.name} -> {indice_bb}.pdf")
            if _staging_root and _session_id:
                _sm_reg_carimbo(_staging_root, _session_id, pdf.name, indice_bb, f"{indice_bb}.pdf")

    return bool(todos_pdfs)


# =============================================================================
# HELPERS
# =============================================================================

def _rodar(descricao: str, cmd: list[str], extra_env: dict[str, str] | None = None) -> int:
    """Executa subprocesso, imprime stdout em tempo real, retorna exit code."""
    log.info("=" * 60)
    log.info(f"  {descricao}")
    log.info("=" * 60)
    log.info(f"  Comando: {' '.join(cmd)}")

    env = os.environ.copy()
    env["PYTHONUTF8"]        = "1"
    env["PYTHONIOENCODING"]  = "utf-8"
    env["PYTHONUNBUFFERED"]  = "1"
    if extra_env:
        env.update(extra_env)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
    )

    for linha in iter(proc.stdout.readline, ""):
        linha = linha.rstrip()
        if linha:
            log.info(f"  [{descricao[:6]}] {linha}")

    proc.wait()
    codigo  = proc.returncode
    simbolo = "?" if codigo == 0 else "?"
    log.info(f"{simbolo}  {descricao} — exit {codigo}")
    return codigo


def _xlsx_bt(mes: str, ano: str) -> Path:
    return OCR_SAIDA_DIR / f"ocr_equatorial_go_BT_{mes}{ano}.xlsx"


def _xlsx_mt(mes: str, ano: str) -> Path:
    return OCR_SAIDA_DIR / f"ocr_equatorial_go_MT_{mes}{ano}.xlsx"


def _pasta_download_bt(mes: str, ano: str) -> Path:
    return DOWNLOAD_DIR / f"{mes}-{ano}" / "BT"


def _pasta_download_mt(mes: str, ano: str) -> Path:
    return DOWNLOAD_DIR / f"{mes}-{ano}" / "MT"


# =============================================================================
# ETAPAS
# =============================================================================

def etapa_ocr(
    mes: str,
    ano: str,
    tipo: str,
    recriar: bool = False,
    pasta_download: Path | None = None,
    xlsx_saida_dir: Path | None = None,
) -> int:
    cmd = [PYTHON_EXE, str(OCR_SCRIPT), "--mes", mes, "--ano", ano, "--tipo", tipo]
    if pasta_download is not None:
        cmd.extend(["--pasta", str(pasta_download)])
    if recriar:
        cmd.append("--recriar")
    extra_env: dict[str, str] = {}
    if pasta_download is not None:
        extra_env["OCR_EQUATORIAL_DOWNLOAD_DIR"] = str(pasta_download)
    if xlsx_saida_dir is not None:
        extra_env["OCR_EQUATORIAL_SAIDA_DIR"] = str(xlsx_saida_dir)

    return _rodar(f"OCR EQUATORIAL {tipo.upper()} {mes}/{ano}", cmd,
                  extra_env=extra_env or None)


def _contar_linhas_xlsx(xlsx: Path) -> int:
    """Retorna numero de linhas de dados (sem cabecalho) no xlsx. Retorna -1 se falhar."""
    try:
        from openpyxl import load_workbook as _lw
        wb = _lw(xlsx, read_only=True, data_only=True)
        ws = wb.active
        count = max(0, ws.max_row - 1)
        wb.close()
        return count
    except Exception as exc:
        log.warning(f"  [validacao] Nao foi possivel contar linhas de {xlsx.name}: {exc}")
        return -1


def etapa_digitacao(xlsx: Path, pasta_pdfs: Path, pipeline_saida: Path) -> int:
    if not xlsx.exists():
        log.error(f"  Planilha nao encontrada: {xlsx}")
        return 1

    # Validacao: numero de linhas no xlsx deve coincidir com PDFs da sessao
    pdfs_sessao = sorted(pasta_pdfs.glob("BB_*.pdf"))
    n_pdfs = len(pdfs_sessao)
    n_xlsx  = _contar_linhas_xlsx(xlsx)
    if n_xlsx >= 0 and n_xlsx != n_pdfs:
        log.error("=" * 60)
        log.error("  [ABORTADO] Divergencia entre xlsx e sessao!")
        log.error(f"  Sessao possui {n_pdfs} PDF(s): {[p.name for p in pdfs_sessao]}")
        log.error(f"  xlsx contem {n_xlsx} linha(s): {xlsx.name}")
        log.error("  O xlsx contem dados de outras sessoes/lotes.")
        log.error("  Digitacao interrompida para evitar processar faturas de outros lotes.")
        log.error("=" * 60)
        return 1

    env = os.environ.copy()
    env["ENEL_EXCEL_PATH"]            = str(xlsx)
    env["ENEL_DIGITACAO_PASTA_PDFS"]            = str(pasta_pdfs)
    env["CONSEN_PIPELINE_SAIDA"]      = str(pipeline_saida)
    env["CONSEN_INTERATIVO_FECHAR"]   = "0"
    env["CONSEN_INVESTIGAR_ZEROS"]    = "0"
    env["DIGITACAO_FATOR_VELOCIDADE"] = "0.25"
    env["CONSEN_SENHA"]               = "Acao2026"
    env["PYTHONUTF8"]                 = "1"
    env["PYTHONIOENCODING"]           = "utf-8"
    env["PYTHONUNBUFFERED"]           = "1"

    log.info("=" * 60)
    log.info(f"  Digitacao EQUATORIAL GO  {xlsx.name}")
    log.info(f"  PDFs da sessao ({n_pdfs}): {pasta_pdfs}")
    log.info("=" * 60)

    proc = subprocess.Popen(
        [PYTHON_EXE, str(DIGITACAO_SCRIPT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )

    def _drenar(stream, prefixo):
        for linha in iter(stream.readline, ""):
            linha = linha.rstrip()
            if linha:
                log.info(f"  [{prefixo}] {linha}")

    t_out = threading.Thread(target=_drenar, args=(proc.stdout, "DIG"),     daemon=True)
    t_err = threading.Thread(target=_drenar, args=(proc.stderr, "DIG-ERR"), daemon=True)
    t_out.start(); t_err.start()
    t_out.join();  t_err.join()

    proc.wait()
    codigo  = proc.returncode
    simbolo = "OK" if codigo == 0 else "FALHA"
    log.info(f"{simbolo}  Digitacao  exit {codigo}")
    return codigo


def _atualizar_master_pos_filtro(auditoria_csv: Path) -> bool:
    try:
        from core.indice_master import atualizar_status_carimbos
        with auditoria_csv.open(encoding="utf-8-sig", newline="") as fh:
            carimbos = [row.get("carimbo", "").strip() for row in csv.DictReader(fh)]
        if not carimbos:
            raise RuntimeError("auditoria sem carimbos")
        changed = atualizar_status_carimbos(carimbos, "DIGITADO")
        log.info(f"  [MASTER] Digitação atualizada: {changed} linha(s)")
        return True
    except Exception as exc:
        log.error(f"  [MASTER] Não foi possível atualizar o índice master: {exc}")
        return False


def _resetar_auditoria(pipeline_saida: Path) -> None:
    auditoria_csv = pipeline_saida / "auditoria_resultados.csv"
    if auditoria_csv.exists():
        auditoria_csv.unlink()
        log.info(f"  [resume] Auditoria reiniciada: {auditoria_csv}")


def etapa_filtro(pasta_pdfs: Path, pasta_destino: Path, pipeline_saida: Path) -> int:
    auditoria_csv = pipeline_saida / "auditoria_resultados.csv"
    if not auditoria_csv.exists():
        log.warning(
            f"  auditoria_resultados.csv nao encontrado em {pipeline_saida}  filtro pulado"
        )
        return 1
    try:
        with auditoria_csv.open(encoding="utf-8-sig", newline="") as fh:
            linhas = list(csv.DictReader(fh))
        esperado = len(list(pasta_pdfs.glob("BB_*.pdf")))
        if not linhas or len(linhas) != esperado:
            log.error(f"  Auditoria incompativel: linhas={len(linhas)} PDFs={esperado}")
            return 1
    except Exception as exc:
        log.error(f"  Auditoria invalida: {exc}")
        return 1

    env = os.environ.copy()
    env["ENEL_FILTRO_CSV"]      = str(auditoria_csv)
    env["ENEL_FILTRO_PDFS"]     = str(pasta_pdfs)
    env["ENEL_FILTRO_DESTINO"]  = str(pasta_destino)
    env["PYTHONUTF8"]           = "1"
    env["PYTHONIOENCODING"]     = "utf-8"
    env["PYTHONUNBUFFERED"]     = "1"

    log.info("=" * 60)
    log.info(f"  Filtro EQUATORIAL GO  {pasta_pdfs.name} -> {pasta_destino.name}")
    log.info("=" * 60)

    proc = subprocess.Popen(
        [PYTHON_EXE, str(FILTRO_SCRIPT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
    )

    for linha in iter(proc.stdout.readline, ""):
        linha = linha.rstrip()
        if linha:
            log.info(f"  [FILTRO] {linha}")

    proc.wait()
    codigo  = proc.returncode
    simbolo = "OK" if codigo == 0 else "FALHA"
    log.info(f"{simbolo}  Filtro  exit {codigo}")
    if codigo == 0:
        if not _atualizar_master_pos_filtro(auditoria_csv):
            return 1
    else:
        log.warning("  [MASTER] Atualizacao do indice pulada porque o filtro falhou.")
    return codigo


# =============================================================================
# MAIN
# =============================================================================

def parse_args():
    p = argparse.ArgumentParser(
        description="Pipeline Equatorial Goias: Carimbar -> OCR(BT+MT) -> Digitacao(BT) -> Filtro(BT)"
    )
    p.add_argument("--mes",           type=str, help="Mes (ex: 03)")
    p.add_argument("--ano",           type=str, help="Ano (ex: 2026)")
    p.add_argument("--so-carimbar",   action="store_true",
                   help="So carimba e registra no master (sem OCR/digitacao/filtro)")
    p.add_argument("--so-ocr",        action="store_true",
                   help="So OCR (BT + MT)")
    p.add_argument("--so-digitacao",  action="store_true",
                   help="So digitacao BT")
    p.add_argument("--so-filtro",     action="store_true",
                   help="So filtro BT")
    p.add_argument("--recriar",       action="store_true",
                   help="Apaga os xlsx de OCR antes de processar (recria do zero)")
    p.add_argument("--pasta",         type=str, default="",
                   help="Pasta alternativa de PDFs (modo lote avulso/watcher, substitui DOWNLOAD_DIR)")
    p.add_argument("--tipo-tensao", type=str, default="ambos",
                   choices=["bt", "mt", "ambos"],
                   help="Restringe carimbo a BT, MT ou ambos (default: ambos)")
    p.add_argument("--preservar-auditoria", action="store_true",
                   help="Mantem auditoria_resultados.csv existente ao retomar um lote")
    p.add_argument("--retomar", action="store_true",
                   help="Processa PDFs BB_* existentes sem consumir novo carimbo")
    p.add_argument("--session-root", type=str, default="",
                   help="Diretorio tecnico da sessao criado pelo Watcher")

    return p.parse_args()


def _limpar_subpastas_vazias(mes: str, ano: str) -> None:
    """Remove BT/, MT/ e mes-ano/ vazias sob DOWNLOAD_DIR após o filtro."""
    mes_ano = DOWNLOAD_DIR / f"{mes}-{ano}"
    for sub in (mes_ano / "BT", mes_ano / "MT"):
        if sub.exists() and not any(sub.iterdir()):
            try:
                sub.rmdir()
                log.info(f"  [limpeza] Removida pasta vazia: {sub.name}/")
            except OSError as exc:
                log.warning(f"  [limpeza] Nao foi possivel remover {sub}: {exc}")
    if mes_ano.exists() and not any(mes_ano.iterdir()):
        try:
            mes_ano.rmdir()
            log.info(f"  [limpeza] Removida pasta vazia: {mes_ano.name}/")
        except OSError as exc:
            log.warning(f"  [limpeza] Nao foi possivel remover {mes_ano}: {exc}")


def main():
    args = parse_args()

    hoje = dt.date.today()
    mes  = args.mes or f"{hoje.month:02d}"
    ano  = args.ano or str(hoje.year)

    if args.pasta:
        global DOWNLOAD_DIR
        DOWNLOAD_DIR = Path(args.pasta.strip())

    # Sessão de progresso: lê session_id deixado pelo pipeline_lote_bt (modo watcher)
    _session_staging = Path(args.session_root) if args.session_root else (DOWNLOAD_DIR if args.pasta else None)
    _session_id: str | None = _sm_ler_sid(_session_staging) if _session_staging else None

    so_algo      = args.so_carimbar or args.so_ocr or args.so_digitacao or args.so_filtro
    tudo         = not so_algo
    fazer_carimb = (tudo or args.so_carimbar) and not args.retomar
    fazer_ocr    = tudo or args.so_ocr
    fazer_dig    = tudo or args.so_digitacao
    fazer_filtro = tudo or args.so_filtro

    # Quando chamado com --pasta (modo watcher/lote avulso), usar pasta de saida
    # isolada por sessao para evitar cruzamento com outros lotes do mesmo mes.
    if args.pasta:
        _id = hashlib.md5(str(DOWNLOAD_DIR).encode()).hexdigest()[:8]
        sessao_saida = PIPELINE_SAIDA / f"_sessao_{_id}"
        xlsx_saida_dir: Path | None = sessao_saida
        log.info(f"  [modo lote] Saida isolada da sessao: {sessao_saida}")
    else:
        sessao_saida = PIPELINE_SAIDA
        xlsx_saida_dir = None

    sessao_saida.mkdir(parents=True, exist_ok=True)
    DIGITADAS_DIR.mkdir(parents=True, exist_ok=True)

    preservar_auditoria = args.preservar_auditoria or (
        os.environ.get("PIPELINE_PRESERVAR_AUDITORIA", "").strip().lower()
        in {"1", "true", "yes", "sim", "on"}
    )

    if not preservar_auditoria:
        _resetar_auditoria(sessao_saida)

    falhou = False

    # -- 0. CARIMBAR  renomeia todos os PDFs (BT + MT) e registra no master --
    if fazer_carimb:
        try:
            etapa_carimbar(mes, ano, getattr(args, "tipo_tensao", "ambos"),
                           _staging_root=_session_staging, _session_id=_session_id)
        except Exception as exc:
            log.error(f"Etapa CARIMBAR falhou: {exc}")
            _sm_etapa(_session_staging, _session_id, "carimbo", "erro", motivo=str(exc))
            sys.exit(1)
        _sm_etapa(_session_staging, _session_id, "carimbo", "ok")

    tipo_tensao = getattr(args, "tipo_tensao", "ambos")
    tipos_execucao = ("bt", "mt") if tipo_tensao == "ambos" else (tipo_tensao,)

    # -- OCR processa somente o tipo solicitado -------------------------------
    if fazer_ocr:
        _sm_etapa(_session_staging, _session_id, "ocr", "em_execucao")
        for t in tipos_execucao:
            cod = etapa_ocr(
                mes,
                ano,
                t,
                recriar=getattr(args, "recriar", False),
                pasta_download=DOWNLOAD_DIR if args.pasta else None,
                xlsx_saida_dir=xlsx_saida_dir,
            )
            if cod != 0:
                log.error(f"OCR {t.upper()} falhou (exit {cod})")
                falhou = True
            else:
                esperado = (xlsx_saida_dir or OCR_SAIDA_DIR) / f"ocr_equatorial_go_{t.upper()}_{mes}{ano}.xlsx"
                if not esperado.exists():
                    log.error(f"OCR {t.upper()} nao gerou XLSX esperado: {esperado}")
                    falhou = True
        _sm_etapa(_session_staging, _session_id, "ocr",
                  "erro" if falhou else "ok")

    # -- Caminhos de XLSX e PDFs flat da sessao --------------------------------
    if xlsx_saida_dir is not None:
        xlsx_bt = xlsx_saida_dir / f"ocr_equatorial_go_BT_{mes}{ano}.xlsx"
        xlsx_mt = xlsx_saida_dir / f"ocr_equatorial_go_MT_{mes}{ano}.xlsx"
    else:
        xlsx_bt = _xlsx_bt(mes, ano)
        xlsx_mt = _xlsx_mt(mes, ano)
    pdfs_bt = DOWNLOAD_DIR
    pdfs_mt = DOWNLOAD_DIR

    # -- 2a. Digitacao BT (apenas quando tipo_tensao inclui BT) ---------------
    if fazer_dig and tipo_tensao in ("bt", "ambos"):
        if falhou:
            log.error("Digitacao BT bloqueada: OCR/XLSX falhou")
        else:
            n_pdfs_bt = len(list(pdfs_bt.glob("BB_*.pdf")))
            _sm_etapa(_session_staging, _session_id, "validacao_lote", "em_execucao", pdfs=n_pdfs_bt)
            _sm_etapa(_session_staging, _session_id, "digitacao", "em_execucao")
            cod = etapa_digitacao(xlsx_bt, pdfs_bt, sessao_saida)
            if cod != 0:
                log.error(f"Digitacao BT falhou (exit {cod})")
                falhou = True
                _sm_etapa(_session_staging, _session_id, "digitacao", "erro", rc=cod)
            else:
                _sm_etapa(_session_staging, _session_id, "digitacao", "ok", rc=cod)

    # -- 2b. Digitacao MT (apenas quando tipo_tensao inclui MT) ---------------
    if fazer_dig and tipo_tensao in ("mt", "ambos"):
        if falhou:
            log.error("Digitacao MT bloqueada: OCR/XLSX falhou")
        else:
            n_pdfs_mt = len(list(pdfs_mt.glob("BB_*.pdf")))
            _sm_etapa(_session_staging, _session_id, "validacao_lote", "em_execucao", pdfs=n_pdfs_mt)
            _sm_etapa(_session_staging, _session_id, "digitacao", "em_execucao")
            cod = etapa_digitacao(xlsx_mt, pdfs_mt, sessao_saida)
            if cod != 0:
                log.error(f"Digitacao MT falhou (exit {cod})")
                falhou = True
                _sm_etapa(_session_staging, _session_id, "digitacao", "erro", rc=cod)
            else:
                _sm_etapa(_session_staging, _session_id, "digitacao", "ok", rc=cod)

    # -- 3a. Filtro BT  SOMENTE PDFs confirmados como digitados ----------------
    if fazer_filtro and not falhou and tipo_tensao in ("bt", "ambos"):
        _sm_etapa(_session_staging, _session_id, "filtro", "em_execucao")
        cod = etapa_filtro(pdfs_bt, DIGITADAS_DIR, sessao_saida)
        if cod != 0:
            log.error(f"Filtro BT falhou (exit {cod})")
            _sm_etapa(_session_staging, _session_id, "filtro", "erro", rc=cod)
            falhou = True
        else:
            _sm_etapa(_session_staging, _session_id, "filtro", "ok", rc=cod)

    # -- 3b. Filtro MT  SOMENTE PDFs confirmados como digitados ----------------
    if fazer_filtro and not falhou and tipo_tensao in ("mt", "ambos"):
        _sm_etapa(_session_staging, _session_id, "filtro", "em_execucao")
        cod = etapa_filtro(pdfs_mt, DIGITADAS_DIR, sessao_saida)
        if cod != 0:
            log.error(f"Filtro MT falhou (exit {cod})")
            _sm_etapa(_session_staging, _session_id, "filtro", "erro", rc=cod)
            falhou = True
        else:
            _sm_etapa(_session_staging, _session_id, "filtro", "ok", rc=cod)

    log.info("")
    log.info("=" * 60)
    if falhou:
        log.info("  Pipeline EQUATORIAL GO finalizado COM FALHAS")
        _sm_status(_session_staging, _session_id, "interrompido",
                   retomavel=True, motivo="pipeline finalizado com falhas")
    else:
        log.info("  Pipeline EQUATORIAL GO finalizado com SUCESSO")
        _sm_status(_session_staging, _session_id, "concluido")
    log.info("=" * 60)

    sys.exit(1 if falhou else 0)


if __name__ == "__main__":
    main()
