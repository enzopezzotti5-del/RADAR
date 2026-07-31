#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Orquestrador Neoenergia
- Executa 4 workers em paralelo: COELBA, CELPE, COSERN, ELEKTRO
- Lê a base de entrada cnpjs_neoenergia.csv
- Distribui CNPJs por estado para cada worker
- Cria trava global para gravação de índice/master

Workers e estados:
  COELBA  → Bahia
  CELPE   → Pernambuco
  COSERN  → Rio Grande do Norte
  ELEKTRO → São Paulo, Mato Grosso do Sul

Base esperada:
- cnpjs_neoenergia.csv

Formatos aceitos da base:
1) Coluna agregada:
   CNPJ,SENHA,ESTADOS
   00000000000191,senha,Bahia|Pernambuco

2) Colunas por estado:
   CNPJ,SENHA,Bahia,Pernambuco,Rio Grande do Norte,Mato Grosso do Sul,São Paulo
   00000000000191,senha,1,1,,,
"""

from __future__ import annotations

import sys
import ctypes as _ctypes
# Isola do CTRL_C_EVENT do Windows (evita KeyboardInterrupt em Selenium/SSL)
if sys.platform == "win32":
    try:
        _ctypes.windll.kernel32.SetConsoleCtrlHandler(None, True)
    except Exception:
        pass
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import csv
import io
import traceback
import contextlib
from pathlib import Path
from datetime import datetime
from multiprocessing import Process, Lock, Queue
from threading import Thread
from typing import List, Dict, Tuple

from core.downloaders.neoenergia.classificacao_ocr import organizar_downloads_neoenergia, sanear_sufixos_neoenergia, restaurar_nomes_pelo_indice
from core.downloaders.neoenergia.worker_coelba  import run_worker_coelba
from core.downloaders.neoenergia.worker_celpe   import run_worker_celpe
from core.downloaders.neoenergia.worker_cosern  import run_worker_cosern
from core.downloaders.neoenergia.worker_elektro import run_worker_elektro

# ── Índice master ──────────────────────────────────────────────────────────────
import importlib.util as _ilu

_MASTER_PY_SERVER = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/indice_master.py")
_MASTER_PY_LOCAL  = Path(__file__).resolve().parent.parent.parent / "indice_master.py"

def _carregar_master_mod():
    for caminho in [_MASTER_PY_SERVER, _MASTER_PY_LOCAL]:
        try:
            if caminho.exists():
                spec = _ilu.spec_from_file_location("indice_master", str(caminho))
                mod  = _ilu.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod
        except Exception:
            continue
    return None


# ============================================================
# CONFIG
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR  = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

CSV_FILE = BASE_DIR / "cnpjs_neoenergia.csv"
COELBA_CORRECAO_DIR = Path(r"\\10.10.250.21\Energia\ARQUIVOS ENZO\DOWNLOAD NEOENERGIA\coelba acesso correcao")

ESTADOS_VALIDOS = {
    "Bahia",
    "Pernambuco",
    "Rio Grande do Norte",
    "Mato Grosso do Sul",
    "São Paulo",
}

# Mapeamento estado → worker
ESTADO_PARA_WORKER = {
    "Bahia":               "coelba",
    "Pernambuco":          "celpe",
    "Rio Grande do Norte": "cosern",
    "Mato Grosso do Sul":  "elektro",
    "São Paulo":           "elektro",
}

WORKERS = ["coelba", "celpe", "cosern", "elektro"]
WORKER_ICONES = {
    "coelba":  "🔵",
    "celpe":   "🟡",
    "cosern":  "🟢",
    "elektro": "🔴",
}
WORKER_FUNCS = {
    "coelba":  run_worker_coelba,
    "celpe":   run_worker_celpe,
    "cosern":  run_worker_cosern,
    "elektro": run_worker_elektro,
}

FINAL_DOWNLOAD_ROOT = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD NEOENERGIA")
INDEX_FILE = FINAL_DOWNLOAD_ROOT / "indice_downloads_neoenergia.csv"

COLUNAS_CNPJ           = ["CNPJ", "cnpj"]
COLUNAS_SENHA          = ["SENHA", "senha"]
COLUNA_ESTADOS_AGREGADA = ["ESTADOS", "estados", "ESTADO", "estado"]


def _resolver_csv_coelba_correcao() -> Path | None:
    candidatos = [
        COELBA_CORRECAO_DIR / "cnpjs_neoenergia.csv",
        COELBA_CORRECAO_DIR / "cnpjs_coelba.csv",
        COELBA_CORRECAO_DIR / "acessos_coelba.csv",
        COELBA_CORRECAO_DIR / "coelba_acesso_correcao.csv",
    ]
    for caminho in candidatos:
        try:
            if caminho.exists() and caminho.is_file():
                return caminho
        except Exception:
            continue

    try:
        if COELBA_CORRECAO_DIR.exists() and COELBA_CORRECAO_DIR.is_dir():
            for caminho in sorted(COELBA_CORRECAO_DIR.glob("*.csv")):
                if caminho.is_file():
                    return caminho
    except Exception:
        pass
    return None


def _aplicar_override_coelba(jobs_base: List[Dict]) -> tuple[List[Dict], Path | None, int]:
    csv_override = _resolver_csv_coelba_correcao()
    if csv_override is None:
        return jobs_base, None, 0

    try:
        jobs_override = carregar_base(csv_override)
    except Exception:
        return jobs_base, None, 0

    override_por_cnpj = {
        fmt_doc(job.get("cnpj", "")): job
        for job in jobs_override
        if "Bahia" in (job.get("estados_esperados") or [])
    }
    if not override_por_cnpj:
        return jobs_base, csv_override, 0

    aplicados = 0
    atualizados: List[Dict] = []
    for job in jobs_base:
        cnpj = fmt_doc(job.get("cnpj", ""))
        estados = list(job.get("estados_esperados", []) or [])
        override = override_por_cnpj.get(cnpj)
        if override and "Bahia" in estados:
            novo_job = dict(job)
            novo_job["senha"] = override.get("senha", job.get("senha", ""))
            aplicados += 1
            atualizados.append(novo_job)
        else:
            atualizados.append(job)
    return atualizados, csv_override, aplicados


# ============================================================
# TERMINAL
# ============================================================

_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_CYAN   = "\033[36m"
_RED    = "\033[31m"
_USAR_ANSI = bool(getattr(sys.stdout, "isatty", lambda: False)())

if not _USAR_ANSI:
    _RESET = ""
    _BOLD = ""
    _DIM = ""
    _GREEN = ""
    _YELLOW = ""
    _CYAN = ""
    _RED = ""

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")

def _linha(char: str = "─", largura: int = 72) -> str:
    return char * largura

def orq_print(msg: str, cor: str = "", bold: bool = False) -> None:
    prefixo = f"{_DIM}[{_ts()}]{_RESET} "
    estilo  = ((_BOLD if bold else "") + cor) if cor else ""
    reset   = _RESET if estilo else ""
    print(f"{prefixo}{estilo}{msg}{reset}", flush=True)

def orq_header(titulo: str) -> None:
    print(f"\n{_BOLD}{_CYAN}{_linha('═')}{_RESET}", flush=True)
    print(f"{_BOLD}{_CYAN}  {titulo}{_RESET}", flush=True)
    print(f"{_BOLD}{_CYAN}{_linha('═')}{_RESET}\n", flush=True)

def orq_section(titulo: str) -> None:
    print(f"\n{_DIM}{_linha('─')}{_RESET}", flush=True)
    print(f"{_BOLD}  {titulo}{_RESET}", flush=True)
    print(f"{_DIM}{_linha('─')}{_RESET}", flush=True)

def _barra(atual: int, total: int, largura: int = 20) -> str:
    if total == 0:
        return f"[{'─' * largura}]"
    preenchido = int(largura * atual / total)
    barra = "█" * preenchido + "░" * (largura - preenchido)
    return f"[{barra}] {int(100 * atual / total):3d}%"


# ============================================================
# MONITOR DE PROGRESSO
# ============================================================

_estado_workers: Dict[str, dict] = {
    w: {"i": 0, "total": 0, "cnpj": "─", "estados": "─", "pdfs": 0, "status": "aguardando"}
    for w in WORKERS
}
_total_pdfs_geral = 0
_ultimo_status_workers = ""


def _processar_evento(ev: dict) -> None:
    global _total_pdfs_geral
    w    = ev.get("worker", "")
    tipo = ev.get("tipo", "")
    if w not in _estado_workers:
        return
    est = _estado_workers[w]

    if tipo == "inicio":
        est["total"]  = ev.get("total", 0)
        est["status"] = "rodando"
    elif tipo == "cnpj_inicio":
        est["i"]       = ev.get("i", est["i"])
        est["total"]   = ev.get("total", est["total"])
        est["cnpj"]    = ev.get("cnpj", "─")
        est["estados"] = ev.get("estados", "─")
        est["status"]  = "rodando"
    elif tipo == "cnpj_fim":
        est["i"]    = ev.get("i", est["i"])
        pdfs_novos  = ev.get("pdfs", 0)
        est["pdfs"] = ev.get("total_pdfs", est["pdfs"])
        _total_pdfs_geral += pdfs_novos
        if pdfs_novos > 0:
            icone = WORKER_ICONES.get(w, "•")
            print(
                f"  {icone} {_BOLD}{_GREEN}+{pdfs_novos} PDF{'s' if pdfs_novos > 1 else ''}{_RESET}"
                f"  {_DIM}CNPJ {est['cnpj']} | {est['estados']}{_RESET}",
                flush=True
            )
    elif tipo == "fim":
        est["status"] = "concluído"
        est["pdfs"]   = ev.get("total_pdfs", est["pdfs"])
        _imprimir_status_workers()


def _imprimir_status_workers(force: bool = False) -> None:
    global _ultimo_status_workers
    linhas = [""]
    for nome in WORKERS:
        est = _estado_workers[nome]
        icone = WORKER_ICONES.get(nome, "•")
        barra = _barra(est["i"], est["total"])
        status = est["status"]
        cor = _GREEN if status == "concluído" else (_YELLOW if status == "rodando" else _DIM)
        linhas.append(
            f"  {icone} {_BOLD}{nome.upper():<8}{_RESET}"
            f" {_CYAN}{barra}{_RESET}"
            f"  {est['i']:>3}/{est['total']:<3}"
            f"  PDFs: {_BOLD}{est['pdfs']:>3}{_RESET}"
            f"  {cor}{status}{_RESET}"
        )
        if status == "rodando":
            linhas.append(f"           {_DIM}↳ {est['cnpj']} | {est['estados']}{_RESET}")
    linhas.append("")
    snapshot = "\n".join(linhas)
    if not force and snapshot == _ultimo_status_workers:
        return
    _ultimo_status_workers = snapshot
    print(snapshot, flush=True)


def _monitor_thread(progress_queue: Queue, n_workers: int) -> None:
    recebidos = 0
    while True:
        try:
            item = progress_queue.get(timeout=15)
        except Exception:
            if recebidos < n_workers:
                _imprimir_status_workers()
            continue
        if item is None:
            recebidos += 1
            if recebidos >= n_workers:
                break
            continue
        _processar_evento(item)


# ============================================================
# HELPERS
# ============================================================

def fmt_doc(valor: str) -> str:
    return "".join(ch for ch in str(valor or "") if ch.isdigit())

def normalize_text(s: str) -> str:
    return " ".join((s or "").split()).strip()

def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _get_first_existing(row: dict, candidates: List[str]) -> str:
    for c in candidates:
        if c in row and row.get(c) is not None:
            return str(row.get(c) or "").strip()
    return ""

def _parse_estados_from_row(row: dict) -> List[str]:
    valor_agregado = _get_first_existing(row, COLUNA_ESTADOS_AGREGADA)
    if valor_agregado:
        partes  = [normalize_text(x) for x in valor_agregado.replace(";", "|").split("|")]
        estados = [p for p in partes if p in ESTADOS_VALIDOS]
        if estados:
            return estados
    encontrados = []
    for estado in ESTADOS_VALIDOS:
        if estado in row:
            valor = str(row.get(estado) or "").strip().lower()
            if valor in {"1", "x", "sim", "s", "ok", "true", "y"} or (
                valor and valor not in {"0", "não", "nao", "n", "false"}
            ):
                encontrados.append(estado)
    return encontrados


# ============================================================
# CARGA DA BASE
# ============================================================

def carregar_base(csv_file: Path) -> List[Dict]:
    if not csv_file.exists():
        raise FileNotFoundError(f"Arquivo base não encontrado: {csv_file}")
    jobs = []
    with open(csv_file, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            cnpj   = fmt_doc(_get_first_existing(row, COLUNAS_CNPJ))
            senha  = _get_first_existing(row, COLUNAS_SENHA)
            estados = _parse_estados_from_row(row)
            if len(cnpj) != 14 or not senha or not estados:
                continue
            jobs.append({"cnpj": cnpj, "senha": senha, "estados_esperados": estados})
    # Dedup
    vistos, saida = set(), []
    for job in jobs:
        chave = (fmt_doc(job["cnpj"]), tuple(sorted(job["estados_esperados"])))
        if chave not in vistos:
            vistos.add(chave)
            saida.append(job)
    return saida


# ============================================================
# DISTRIBUIÇÃO
# ============================================================

def distribuir_jobs(jobs_base: List[Dict]) -> Dict[str, List[Dict]]:
    """Distribui cada job para o worker responsável pelo estado.
    CNPJs multiestado são distribuídos por toggle entre os workers envolvidos.
    """
    buckets: Dict[str, List[Dict]] = {w: [] for w in WORKERS}
    toggle: Dict[str, int] = {}

    for job in jobs_base:
        estados = job.get("estados_esperados", [])

        # Descobre quais workers são necessários para este CNPJ
        workers_necessarios = list(dict.fromkeys(
            ESTADO_PARA_WORKER[e] for e in estados if e in ESTADO_PARA_WORKER
        ))

        if not workers_necessarios:
            continue

        if len(workers_necessarios) == 1:
            buckets[workers_necessarios[0]].append(job)
        else:
            # Multiestado: toggle round-robin entre os workers envolvidos
            chave = tuple(workers_necessarios)
            idx   = toggle.get(chave, 0)
            worker_escolhido = workers_necessarios[idx % len(workers_necessarios)]
            toggle[chave]    = idx + 1
            buckets[worker_escolhido].append(job)

    return buckets


# ============================================================
# WRAPPER DOS PROCESSOS
# ============================================================

def _worker_wrapper(nome, func, jobs, lock, resultado_queue, progress_queue) -> None:
    try:
        inicio = now_str()
        total  = func(jobs, lock, progress_queue)
        resultado_queue.put({"worker": nome, "status": "ok", "inicio": inicio,
                             "fim": now_str(), "total": total, "erro": ""})
    except Exception as e:
        resultado_queue.put({"worker": nome, "status": "erro", "inicio": "",
                             "fim": now_str(), "total": 0,
                             "erro": f"{e}\n{traceback.format_exc()}"})
    finally:
        progress_queue.put(None)


# ============================================================
# RESUMO
# ============================================================

def salvar_resumo(resultados: Dict[str, dict], buckets: Dict[str, List], sem_grupo: List) -> Path:
    out = LOG_DIR / f"orchestrator_neo_resumo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    linhas = ["=" * 80, "ORQUESTRADOR NEOENERGIA - RESUMO", "=" * 80, f"Geração: {now_str()}", ""]
    for w in WORKERS:
        linhas.append(f"{w.upper():<10} jobs: {len(buckets.get(w, []))}")
    linhas += ["", f"Sem grupo: {len(sem_grupo)}", ""]
    for w in WORKERS:
        r = resultados.get(w, {})
        linhas += ["-" * 80, f"WORKER: {w.upper()}",
                   f"Status : {r.get('status')}", f"Total  : {r.get('total')}",
                   f"Início : {r.get('inicio')}", f"Fim    : {r.get('fim')}"]
        if r.get("erro"):
            linhas += ["Erro:", r["erro"]]
        linhas.append("")
    out.write_text("\n".join(linhas), encoding="utf-8")
    return out


# ============================================================
# MAIN
# ============================================================

def main():
    _mod = _carregar_master_mod()

    orq_header("ORQUESTRADOR NEOENERGIA  —  COELBA | CELPE | COSERN | ELEKTRO")

    if _mod is not None:
        try:
            _buf = io.StringIO()
            with contextlib.redirect_stdout(_buf):
                _mt = _mod.MasterIndice(_mod.MASTER_FILE)
            lock_tipo = "filelock" if getattr(_mod, "_FILELOCK_OK", False) else "sem lock"
            orq_print(
                f"Master  {_GREEN}OK{_RESET}  "
                f"{len(_mt._ja_baixados)} registros  "
                f"próximo {_BOLD}{_mt.proximo_carimbo}{_RESET}  ({lock_tipo})"
            )
            del _mt
        except Exception as e:
            orq_print(f"Master  {_YELLOW}AVISO{_RESET}  {e}", cor=_YELLOW)
    else:
        orq_print("Master  não encontrado — workers usarão índice local", cor=_YELLOW)

    jobs_base = carregar_base(CSV_FILE)
    jobs_base, csv_coelba_override, n_overrides = _aplicar_override_coelba(jobs_base)
    buckets   = distribuir_jobs(jobs_base)

    # Jobs sem worker mapeado
    mapeados = sum(len(v) for v in buckets.values())
    sem_grupo = [j for j in jobs_base
                 if not any(ESTADO_PARA_WORKER.get(e) for e in j.get("estados_esperados", []))]

    summary = "  |  ".join(
        f"{WORKER_ICONES[w]} {w.upper()} {len(buckets[w])}" for w in WORKERS
    )
    orq_print(f"Base    {_GREEN}OK{_RESET}  {len(jobs_base)} CNPJs  →  {summary}"
              + (f"  |  {_YELLOW}sem grupo {len(sem_grupo)}{_RESET}" if sem_grupo else ""))
    if csv_coelba_override is not None:
        orq_print(
            f"COELBA  acessos de correção: {_BOLD}{csv_coelba_override}{_RESET}  "
            f"overrides aplicados: {_BOLD}{n_overrides}{_RESET}"
        )

    orq_section("EXECUÇÃO PARALELA")

    shared_lock = Lock()
    resultado_q = Queue()
    progress_q  = Queue()

    processos = []
    for w in WORKERS:
        p = Process(
            target=_worker_wrapper,
            args=(w, WORKER_FUNCS[w], buckets[w], shared_lock, resultado_q, progress_q),
            daemon=False,
        )
        processos.append((w, p))

    monitor = Thread(
        target=_monitor_thread,
        args=(progress_q, len(WORKERS)),
        daemon=True,
    )

    inicio_exec = datetime.now()
    orq_print(f"Iniciando {len(WORKERS)} workers às {_BOLD}{inicio_exec.strftime('%H:%M:%S')}{_RESET} ...")
    print()

    for _, p in processos:
        p.start()
    monitor.start()

    resultados: Dict[str, dict] = {}
    for _ in range(len(WORKERS)):
        r = resultado_q.get()
        resultados[r["worker"]] = r

    for _, p in processos:
        p.join()
    monitor.join(timeout=3)

    # ── Resumo final ────────────────────────────────────────
    duracao    = str(datetime.now() - inicio_exec).split(".")[0]
    total_pdfs = sum(r.get("total", 0) for r in resultados.values())

    orq_section("RESULTADO FINAL")

    for w in WORKERS:
        r      = resultados.get(w, {"status": "sem retorno", "total": 0, "inicio": "", "fim": now_str()})
        status = r.get("status", "?")
        cor    = _GREEN if status == "ok" else _RED
        orq_print(
            f"  {WORKER_ICONES[w]} {_BOLD}{w.upper():<8}{_RESET}"
            f"  status: {cor}{status}{_RESET}"
            f"  PDFs: {_BOLD}{r.get('total', 0)}{_RESET}"
            f"  {_DIM}{r.get('inicio', '')} → {r.get('fim', '')}{_RESET}"
        )
        if r.get("erro"):
            for linha in r["erro"].strip().splitlines()[:5]:
                orq_print(f"           {_RED}{linha}{_RESET}")

    print()
    orq_print(f"  Total PDFs baixados : {_BOLD}{_GREEN}{total_pdfs}{_RESET}", bold=True)
    orq_print(f"  Duração total       : {_BOLD}{duracao}{_RESET}")

    if _mod is not None:
        try:
            _buf = io.StringIO()
            with contextlib.redirect_stdout(_buf):
                _mf = _mod.MasterIndice(_mod.MASTER_FILE)
            orq_print(f"  Master final        : {len(_mf._ja_baixados)} registros  "
                      f"próximo {_BOLD}{_mf.proximo_carimbo}{_RESET}")
        except Exception:
            pass

    orq_section("ORGANIZAÇÃO OCR")
    try:
        master_file = getattr(_mod, "MASTER_FILE", None) if _mod is not None else None
        organizacao = organizar_downloads_neoenergia(
            FINAL_DOWNLOAD_ROOT,
            index_file=INDEX_FILE,
            master_file=master_file,
        )
        orq_print(
            "  PDFs organizados     : "
            f"{_BOLD}{organizacao.movidos_bt + organizacao.movidos_mt}{_RESET}"
        )
        orq_print(f"  Pasta BT             : {organizacao.movidos_bt}")
        orq_print(f"  Pasta MT             : {organizacao.movidos_mt}")
        orq_print(f"  Referencias corrigidas: {organizacao.referencias_corrigidas}")
        orq_print(f"  Sem classificação    : {organizacao.nao_classificados}")
        orq_print(f"  Índice atualizado    : {organizacao.indice_atualizado}")
        orq_print(f"  Master atualizado    : {organizacao.master_atualizado}")
    except Exception as e:
        orq_print(f"  Falha na organização OCR: {e}", cor=_RED)

    orq_section("SANEAMENTO SUFIXOS")
    try:
        master_file_san = getattr(_mod, "MASTER_FILE", None) if _mod is not None else None
        if master_file_san and INDEX_FILE.exists() and Path(master_file_san).exists():
            san = sanear_sufixos_neoenergia(
                FINAL_DOWNLOAD_ROOT,
                index_file=INDEX_FILE,
                master_file=Path(master_file_san),
            )
            orq_print(f"  Sufixos simples removidos : {san.renomeados_simples}")
            orq_print(f"  Duplicatas exatas removidas: {san.duplicatas_exatas_removidas}")
            orq_print(f"  Conflitos resolvidos       : {san.conflitos_resolvidos}")
        else:
            orq_print("  Master nao disponivel — saneamento pulado", cor=_RED)
    except Exception as e:
        orq_print(f"  Falha no saneamento de sufixos: {e}", cor=_RED)

    orq_section("RESTAURAÇÃO DE NOMES PELO ÍNDICE")
    try:
        master_file_rest = getattr(_mod, "MASTER_FILE", None) if _mod is not None else None
        if INDEX_FILE.exists():
            rest = restaurar_nomes_pelo_indice(
                index_file=INDEX_FILE,
                master_file=Path(master_file_rest) if master_file_rest else None,
            )
            orq_print(f"  Renomeados (nome correto)  : {rest.renomeados}")
            orq_print(f"  Já corretos                : {rest.ja_corretos}")
            orq_print(f"  Não encontrados            : {rest.nao_encontrados}")
            orq_print(f"  Conflitos pulados          : {rest.conflitos}")
        else:
            orq_print("  Índice não encontrado — restauração pulada", cor=_RED)
    except Exception as e:
        orq_print(f"  Falha na restauração de nomes: {e}", cor=_RED)

    resumo = salvar_resumo(resultados, buckets, sem_grupo)
    orq_print(f"  Resumo salvo em     : {_DIM}{resumo}{_RESET}")
    print(f"\n{_BOLD}{_CYAN}{_linha('═')}{_RESET}\n")


if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    main()
