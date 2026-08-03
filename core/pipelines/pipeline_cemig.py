#!/usr/bin/env python3
"""
pipeline_cemig.py  —  Fluxo automatico CEMIG BT
================================================
Disparado pelo orquestrador.py as 8h, apos os downloads das 6h.

Sequencia:
    1. OCR       -> ocr/OCR_Cemig.py --tipo bt --mes MM --ano AAAA
    2. Digitacao -> digitacao_consen/Digitacao_Consen_CEMIG.py --xlsx <caminho_bt>
    3. Filtro    -> digitacao_consen/cemig_filtro.py --mes MM --ano AAAA

Exit codes:
    0 -> pipeline concluido
    1 -> falha critica em OCR ou digitacao (filtro e best-effort, nao aborta)

Uso:
    python pipeline_cemig.py                    # mes/ano atual
    python pipeline_cemig.py --mes 03 --ano 2026
    python pipeline_cemig.py --so-ocr
    python pipeline_cemig.py --so-digitacao
    python pipeline_cemig.py --so-filtro
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import subprocess
import sys
from pathlib import Path

try:
    from core.pipelines._visual import _p, _info, _ok, _fail, _warn, _sep, _banner, _rodar as _rodar_visual
except ModuleNotFoundError:
    from _visual import _p, _info, _ok, _fail, _warn, _sep, _banner, _rodar as _rodar_visual

# =============================================================================
# CONFIGURACAO
# =============================================================================

RAIZ         = Path(__file__).resolve().parent.parent
SERVIDOR     = Path("//10.10.250.21/Energia")
PASTA_OCR    = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/OCR CEMIG")
PASTA_DOWNLOAD = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD CEMIG")
PASTA_LOGS   = Path(__file__).resolve().parent / "logs"
PIPELINE_SAIDA = SERVIDOR / "ARQUIVOS ENZO" / "CEMIG_pipeline_saida"
FALLBACK_ROOT = RAIZ / "_runtime_fallback" / "pipeline_cemig"

SCRIPT_OCR       = RAIZ / "ocr"              / "OCR_Cemig.py"
SCRIPT_DIGITACAO = RAIZ / "digitacao_consen" / "digitacao_consen_cemig.py"
SCRIPT_FILTRO    = RAIZ / "digitacao_consen" / "cemig_filtro.py"
SCRIPT_DIGITACAO_MT = RAIZ / "digitacao_consen" / "digitacao_consen_cemig.py"
CONSEN_LOGIN_URL   = "https://consen.acaoengenharia.com.br/login.php"
CONSEN_TARGET_HASH = "#bpg/gestao/fatura/cadastroTabFatura.php"
CONSEN_TARGET_URL  = f"{CONSEN_LOGIN_URL.rsplit('/', 1)[0]}/index.php{CONSEN_TARGET_HASH}"
CONSEN_LINK_HREF   = "bpg/gestao/fatura/cadastroTabFatura.php"
CONSEN_LINK_TEXTO  = "Instalacao"
CONSEN_USUARIO     = "Robo Digitador"
CONSEN_SENHA       = "Acao2026"

# =============================================================================
# HELPERS
# =============================================================================

def _mkdir_seguro(pasta):
    """mkdir tolerante ao WinError 1398 (diferenca de relogio com servidor UNC)."""
    try:
        pasta.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # pasta ja existe no servidor — WinError 1398 e falso positivo


def _safe_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _resolver_dir(preferido: Path, fallback: Path, rotulo: str) -> Path:
    try:
        preferido.mkdir(parents=True, exist_ok=True)
        return preferido
    except OSError as e:
        fallback.mkdir(parents=True, exist_ok=True)
        _warn(f"{rotulo} indisponível em {preferido}: {e}. Usando fallback local {fallback}")
        return fallback


def _xlsx_bt(mes: str, ano: str) -> Path:
    return PASTA_OCR / f"ocr_cemig_BT_{mes}{ano}.xlsx"

def _xlsx_mt(mes: str, ano: str) -> Path:
    return PASTA_OCR / f"ocr_cemig_MT_{mes}{ano}.xlsx"


def _pasta_mes_download(mes: str, ano: str) -> Path:
    for sep in [".", "-", "_", " ", ""]:
        p = PASTA_DOWNLOAD / f"{mes}{sep}{ano}"
        if p.is_dir():
            return p
    return PASTA_DOWNLOAD / f"{mes}.{ano}"


def _rodar(descricao: str, cmd: list, env_extra: dict[str, str] | None = None) -> bool:
    """Executa subprocess com saída visual. Retorna True se exit 0."""
    code = _rodar_visual(descricao, cmd, env_extra=env_extra)
    if code == 130:
        return False
    return code == 0
# =============================================================================
# LOGGING UNIFICADO
# =============================================================================

_mkdir_seguro(PASTA_LOGS)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            PASTA_LOGS / "pipeline_cemig.log",
            encoding="utf-8",
            errors="replace",
        ),
    ],
)
log = logging.getLogger("pipeline_cemig")

# =============================================================================
# ETAPAS
# =============================================================================


def etapa_ocr(mes: str, ano: str) -> bool:
    log.info("=" * 60)
    log.info("  ETAPA 1 — OCR CEMIG BT")
    log.info("=" * 60)
    if not SCRIPT_OCR.exists():
        log.error(f"  Script nao encontrado: {SCRIPT_OCR}")
        return False
    cmd = [sys.executable, str(SCRIPT_OCR), "--tipo", "bt", "--mes", mes, "--ano", ano]
    return _rodar("OCR BT", cmd)


def etapa_ocr_mt(mes: str, ano: str) -> bool:
    log.info("=" * 60)
    log.info("  ETAPA 1b — OCR CEMIG MT")
    log.info("=" * 60)
    if not SCRIPT_OCR.exists():
        log.error(f"  Script nao encontrado: {SCRIPT_OCR}")
        return False
    cmd = [sys.executable, str(SCRIPT_OCR), "--tipo", "mt", "--mes", mes, "--ano", ano]
    return _rodar("OCR MT", cmd)


def _script_digitacao_cemig() -> Path:
    """Localiza o script de digitacao CEMIG (aceita nome com ou sem acento)."""
    candidatos = [
        SCRIPT_DIGITACAO,
        SCRIPT_DIGITACAO_MT,
        RAIZ / "digitacao_consen" / "digitacao_consen_cemig.py",
    ]
    candidatos += sorted((RAIZ / "digitacao_consen").glob("*Consen*CEMIG.py"))
    return next((p for p in candidatos if p.exists()), None)


def _resetar_auditoria(pasta_saida: Path) -> None:
    csv = pasta_saida / "auditoria_resultados.csv"
    if _safe_exists(csv):
        csv.unlink()
        log.info("  [reset] auditoria_resultados.csv removido antes da digitacao.")


def etapa_digitacao(xlsx_bt: Path, mes: str, ano: str, sem_filtro_origem: bool = False) -> bool:
    log.info("=" * 60)
    log.info("  ETAPA 2 — DIGITACAO CEMIG BT")
    log.info("=" * 60)
    script = _script_digitacao_cemig()
    if script is None:
        log.error("  Script de digitacao CEMIG nao encontrado.")
        return False
    if not xlsx_bt.exists():
        log.error(f"  xlsx BT nao encontrado apos OCR: {xlsx_bt}")
        return False
    log.info(f"  xlsx: {xlsx_bt}")
    cmd = [
        sys.executable,
        str(script),
        "--xlsx", str(xlsx_bt),
        "--linha-inicio", "2",
    ]
    if not sem_filtro_origem:
        pasta_origem_bt = _pasta_mes_download(mes, ano) / "BT"
        cmd += ["--pasta-origem", str(pasta_origem_bt)]
        log.info(f"  origem: {pasta_origem_bt}")
    else:
        log.info("  origem: (sem filtro — todas as linhas do xlsx)")
    env_extra = {
        "CONSEN_PIPELINE_SAIDA": str(PIPELINE_SAIDA),
        "CONSEN_LOGIN_URL": CONSEN_LOGIN_URL,
        "CONSEN_TARGET_HASH": CONSEN_TARGET_HASH,
        "CONSEN_TARGET_URL": CONSEN_TARGET_URL,
        "CONSEN_LINK_HREF": CONSEN_LINK_HREF,
        "CONSEN_LINK_TEXTO": CONSEN_LINK_TEXTO,
        "CONSEN_USUARIO": CONSEN_USUARIO,
        "CONSEN_SENHA": CONSEN_SENHA,
    }
    return _rodar("Digitacao BT", cmd, env_extra=env_extra)


def etapa_digitacao_mt(xlsx_mt: Path, mes: str, ano: str) -> bool:
    log.info("=" * 60)
    log.info("  ETAPA 2b — DIGITACAO CEMIG MT")
    log.info("=" * 60)
    script = _script_digitacao_cemig()
    if script is None:
        log.error("  Script de digitacao CEMIG nao encontrado.")
        return False
    if not xlsx_mt.exists():
        log.warning(f"  xlsx MT nao encontrado — subpasta MT pode nao existir: {xlsx_mt}")
        return True   # nao e falha critica, pode nao ter MT no mes
    log.info(f"  xlsx: {xlsx_mt}")
    pasta_origem_mt = _pasta_mes_download(mes, ano) / "MT"
    cmd = [
        sys.executable,
        str(script),
        "--xlsx", str(xlsx_mt),
        "--linha-inicio", "2",
        "--pasta-origem", str(pasta_origem_mt),
    ]
    env_extra = {
        "CONSEN_PIPELINE_SAIDA": str(PIPELINE_SAIDA),
        "CONSEN_LOGIN_URL": CONSEN_LOGIN_URL,
        "CONSEN_TARGET_HASH": CONSEN_TARGET_HASH,
        "CONSEN_TARGET_URL": CONSEN_TARGET_URL,
        "CONSEN_LINK_HREF": CONSEN_LINK_HREF,
        "CONSEN_LINK_TEXTO": CONSEN_LINK_TEXTO,
        "CONSEN_USUARIO": CONSEN_USUARIO,
        "CONSEN_SENHA": CONSEN_SENHA,
    }
    return _rodar("Digitacao MT", cmd, env_extra=env_extra)


def _atualizar_master_pos_filtro(auditoria_csv: Path) -> None:
    try:
        from core.indice_master import marcar_digitados_auditoria
        contadores = marcar_digitados_auditoria(auditoria_csv)
        log.info(f"  [MASTER] Digitacao atualizada: {contadores}")
    except Exception as _e:
        log.warning(f"  [MASTER] Nao foi possivel atualizar o indice master: {_e}")


def etapa_filtro(mes: str, ano: str, tipo: str = "bt") -> bool:
    tipo = (tipo or "bt").lower()
    rotulo = tipo.upper()

    log.info("=" * 60)
    log.info(f"  ETAPA 3 - FILTRO / MOVIMENTACAO {rotulo}")
    log.info("=" * 60)
    if not SCRIPT_FILTRO.exists():
        log.error(f"  Script nao encontrado: {SCRIPT_FILTRO}")
        return False

    auditoria_csv = PIPELINE_SAIDA / "auditoria_resultados.csv"
    pasta_origem = _pasta_mes_download(mes, ano) / rotulo
    cmd = [
        sys.executable,
        str(SCRIPT_FILTRO),
        "--mes", mes,
        "--ano", ano,
        "--csv", str(auditoria_csv),
        "--origem", str(pasta_origem),
        "--rotulo-origem", rotulo,
    ]
    ok = _rodar(f"Filtro {rotulo}", cmd)
    if not ok:
        log.warning(f"  Filtro {rotulo} retornou falhas - verifique falhas_mover_cemig.csv")
    return ok

# =============================================================================
# CLI + MAIN
# =============================================================================

def parse_args():
    hoje = dt.date.today()
    p = argparse.ArgumentParser(description="Pipeline CEMIG BT+MT: OCR -> Digitacao -> Filtro")
    p.add_argument("--mes", type=str, default=f"{hoje.month:02d}",
                   help="Mes de referencia (padrao: mes atual)")
    p.add_argument("--ano", type=str, default=str(hoje.year),
                   help="Ano de referencia (padrao: ano atual)")
    p.add_argument("--so-ocr",             action="store_true", help="Roda so OCR BT+MT (debug)")
    p.add_argument("--so-digitacao",       action="store_true", help="Roda so digitacao BT+MT (debug)")
    p.add_argument("--so-filtro",          action="store_true", help="Roda so filtro BT (debug)")
    p.add_argument("--so-mt",              action="store_true", help="Roda so OCR+Digitacao MT (sem BT)")
    p.add_argument("--so-bt",              action="store_true", help="Roda pipeline BT completo (sem MT)")
    p.add_argument("--sem-filtro-origem",  action="store_true",
                   help="Nao passa --pasta-origem para digitacao (digita todas as linhas do xlsx)")
    return p.parse_args()


def main():
    global PIPELINE_SAIDA
    args = parse_args()
    mes, ano = args.mes, args.ano

    # Impede duas instâncias simultâneas (duplicação de carimbo / CSV corrompido)
    _lock_path = Path(os.environ.get("TEMP", "C:/Temp")) / "pipeline_cemig.lock"
    try:
        from filelock import FileLock, Timeout
        _lock = FileLock(str(_lock_path), timeout=0)
        try:
            _lock.acquire()
        except Timeout:
            log.warning(f"pipeline_cemig já está em execução ({_lock_path}). Saindo para evitar duplicação.")
            sys.exit(0)
    except ImportError:
        _lock = None
    xlsx_bt  = _xlsx_bt(mes, ano)
    xlsx_mt  = _xlsx_mt(mes, ano)
    modo_debug = args.so_ocr or args.so_digitacao or args.so_filtro or args.so_mt or args.so_bt

    fazer_bt = not args.so_mt
    fazer_mt = not args.so_bt

    PIPELINE_SAIDA = _resolver_dir(PIPELINE_SAIDA, FALLBACK_ROOT / "saida", "Saída do pipeline CEMIG")

    _banner("PIPELINE CEMIG  BT + MT", [
        f"Referência : {mes}/{ano}",
        f"xlsx BT    : {xlsx_bt}",
        f"xlsx MT    : {xlsx_mt}",
        f"Saída      : {PIPELINE_SAIDA}",
    ])

    falhas_criticas = []

    # ── Etapa 1: OCR BT ──────────────────────────────────────────────────────
    if fazer_bt and not args.so_digitacao and not args.so_filtro:
        if not etapa_ocr(mes, ano):
            falhas_criticas.append("OCR BT")
            if not modo_debug:
                log.error("  OCR BT falhou — abortando pipeline.")
                sys.exit(1)
    elif not fazer_bt:
        log.info("  [skip] OCR BT.")

    # ── Etapa 1b: OCR MT ─────────────────────────────────────────────────────
    if fazer_mt and not args.so_digitacao and not args.so_filtro:
        if not etapa_ocr_mt(mes, ano):
            falhas_criticas.append("OCR MT")
            log.warning("  OCR MT falhou — digitacao MT sera pulada.")
            fazer_mt = False
    elif not fazer_mt:
        log.info("  [skip] OCR MT.")

    # ── Etapa 2: Digitacao BT ────────────────────────────────────────────────
    if fazer_bt and not args.so_ocr and not args.so_filtro:
        _resetar_auditoria(PIPELINE_SAIDA)
        if not etapa_digitacao(xlsx_bt, mes, ano, sem_filtro_origem=getattr(args, "sem_filtro_origem", False)):
            falhas_criticas.append("Digitacao BT")
            log.warning("  Digitacao BT falhou parcialmente — filtro BT roda assim mesmo (best-effort).")
    elif not fazer_bt:
        log.info("  [skip] Digitacao BT.")

    # ── Etapa 3a: Filtro BT (best-effort) ───────────────────────────────────
    # O BT precisa ser filtrado antes da digitacao MT, porque ambas as etapas
    # compartilham o mesmo auditoria_resultados.csv na pasta de saida.
    filtro_bt_ok = True
    if fazer_bt and not args.so_ocr and not args.so_digitacao:
        filtro_bt_ok = etapa_filtro(mes, ano, "bt")
        if filtro_bt_ok:
            _atualizar_master_pos_filtro(PIPELINE_SAIDA / "auditoria_resultados.csv")
    elif args.so_ocr or args.so_digitacao:
        log.info("  [debug] Pulando filtro BT.")
    else:
        log.info("  [skip] Filtro BT.")

    # ── Etapa 2b: Digitacao MT ───────────────────────────────────────────────
    if fazer_mt and not args.so_ocr and not args.so_filtro:
        _resetar_auditoria(PIPELINE_SAIDA)
        if not etapa_digitacao_mt(xlsx_mt, mes, ano):
            falhas_criticas.append("Digitacao MT")
            log.warning("  Digitacao MT falhou — pipeline continua (filtro MT segue disponivel).")
    elif not fazer_mt:
        log.info("  [skip] Digitacao MT.")

    # ── Etapa 3b: Filtro MT (best-effort) ───────────────────────────────────
    filtro_mt_ok = True
    if fazer_mt and not args.so_ocr and not args.so_digitacao:
        filtro_mt_ok = etapa_filtro(mes, ano, "mt")
        if filtro_mt_ok:
            _atualizar_master_pos_filtro(PIPELINE_SAIDA / "auditoria_resultados.csv")
    elif args.so_ocr or args.so_digitacao:
        log.info("  [debug] Pulando filtro MT.")
    else:
        log.info("  [skip] Filtro MT.")

    _p()
    _sep("═")
    if _lock:
        _lock.release()
    if falhas_criticas:
        _fail(f"PIPELINE CEMIG COM FALHAS: {', '.join(falhas_criticas)}")
        _sep("═")
        sys.exit(1)
    _ok("PIPELINE CEMIG CONCLUÍDO COM SUCESSO")
    _sep("═")
    sys.exit(0)


if __name__ == "__main__":
    main()
