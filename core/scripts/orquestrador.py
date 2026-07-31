#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
orquestrador.py
---------------
Launcher manual de downloaders e pipelines.

Observacao:
- o modo agendado antigo e o dashboard Flask foram descontinuados
- este arquivo permanece apenas como ponto de disparo manual por CLI
"""

import sys
import os
import csv
import re
import html
import time
import atexit
import smtplib
import logging
import threading
import subprocess
import traceback
import importlib
import unicodedata
from pathlib import Path
from datetime import datetime
from multiprocessing import Process, Lock, Queue, freeze_support
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Adiciona a raiz do projeto (ENERGIA) ao sys.path para encontrar o config.py
ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = Path(__file__).parent
if str(CONFIG_DIR) not in sys.path:
    sys.path.insert(0, str(CONFIG_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import config

try:
    from core.pipelines._session_runtime import build_session_command
except Exception:  # pragma: no cover - fallback em inicializacoes antigas
    build_session_command = lambda cmd: cmd  # type: ignore[assignment]

# Dashboard antigo removido. Mantemos no-op helpers para nao quebrar chamadas internas.
def _dashboard_iniciar(*a, **kw): pass
def _dashboard_evento(*a, **kw): pass
def _dashboard_bot(*a, **kw): pass
def _dashboard_agendamentos(*a, **kw): pass

# =============================================================================
# LOG
# =============================================================================

caminho_log = getattr(config, "LOG_DIR", None)
log_dir = Path(caminho_log) if caminho_log else CONFIG_DIR
log_dir.mkdir(parents=True, exist_ok=True)
log_file = log_dir / f"orquestrador_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# =============================================================================
# ESTADO GLOBAL
# =============================================================================

falha_global = threading.Event()
resultados_finais = {}
filtro_exibicao = "TODOS"
_result_lock = threading.Lock()

LOCK_DIR = CONFIG_DIR / "locks"
LOCK_DIR.mkdir(parents=True, exist_ok=True)


# =============================================================================
# UTIL
# =============================================================================

def agora_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def identificar_bot_por_nome(nome: str) -> str:
    nome_u = (nome or "").upper()
    # Pipelines primeiro (evitar match em "ENEL" antes de "PIPELINE_ENEL")
    if "PIPELINE_NEOENERGIA_BAHIA" in nome_u:
        return "PIPELINE_NEOENERGIA_BAHIA"
    if "PIPELINE_NEOENERGIA_PERNAMBUCO" in nome_u:
        return "PIPELINE_NEOENERGIA_PERNAMBUCO"
    if "PIPELINE_NEOENERGIA_ELEKTRO" in nome_u or "PIPELINE_NEOENERGIA" in nome_u or "PIPELINE_NEO" in nome_u:
        return "PIPELINE_NEOENERGIA_ELEKTRO"
    if "PIPELINE_ENEL" in nome_u:
        return "PIPELINE_ENEL"
    if "PIPELINE_CEMIG" in nome_u or "PIPELINE" in nome_u:
        return "PIPELINE_CEMIG"
    if "CEMIG" in nome_u:
        return "CEMIG"
    # ENEL separado por região
    if "ENEL_SP" in nome_u or "ENEL-SP" in nome_u:
        return "ENEL_SP"
    if "ENEL_CE" in nome_u or "ENEL-CE" in nome_u:
        return "ENEL_CE"
    if "ENEL_RJ" in nome_u or "ENEL-RJ" in nome_u:
        return "ENEL_RJ"
    if "ENEL" in nome_u:
        return "ENEL_SP"   # fallback genérico → SP
    if "EQUATORIAL" in nome_u:
        return "EQUATORIAL"
    if "COELBA" in nome_u:
        return "COELBA"
    if "CELPE" in nome_u:
        return "CELPE"
    if "COSERN" in nome_u:
        return "COSERN"
    if "ELEKTRO" in nome_u:
        return "ELEKTRO"
    if "NEOENERGIA" in nome_u:
        return "NEOENERGIA"
    return Path(nome).stem.upper()


def registrar_resultado(nome_bot: str, status: str, detalhe: str = "", **extras):
    payload = {"status": status, "detalhe": detalhe or ""}
    payload.update(extras)
    with _result_lock:
        resultados_finais[nome_bot] = payload
    _dashboard_bot(
        nome_bot, status,
        mensagem=detalhe or "",
        inicio=extras.get("inicio", ""),
        fim=extras.get("fim", ""),
        duracao_s=extras.get("duracao_s", 0),
        total_pdfs=extras.get("total_pdfs", 0),
        cnpjs=extras.get("cnpjs_processados", 0),
    )


def print_filtrado(nome_bot: str, mensagem: str):
    global filtro_exibicao
    if not mensagem:
        return
    _dashboard_evento(nome_bot, mensagem)
    if filtro_exibicao == "TODOS" or filtro_exibicao == nome_bot:
        print(f" ➔ [{nome_bot}] {mensagem}")


FILTRO_MAPA = {
    "1": "CEMIG",
    "2": "ENEL_SP",
    "3": "ENEL_CE",
    "4": "ENEL_RJ",
    "5": "EQUATORIAL",
    "6": "COELBA",
    "7": "CELPE",
    "8": "COSERN",
    "9": "ELEKTRO",
    "c": "PIPELINE_CEMIG",
    "e": "PIPELINE_ENEL",
    "ne": "PIPELINE_NEOENERGIA_ELEKTRO",
    "nb": "PIPELINE_NEOENERGIA_BAHIA",
    "np": "PIPELINE_NEOENERGIA_PERNAMBUCO",
    "0": "TODOS",
}


def escutar_teclado():
    global filtro_exibicao
    time.sleep(2)
    print("\n" + "═" * 70)
    print(" 🎛️  CONTROLE DE VISÃO ATIVADO")
    for k, v in FILTRO_MAPA.items():
        if v == "TODOS":
            print(f" ➔ Digite '{k}' + Enter para ver TODOS")
        else:
            print(f" ➔ Digite '{k}' + Enter para ver só {v}")
    print(" ➔ Digite 'q' + Enter para sair")
    print("═" * 70 + "\n")

    while True:
        try:
            comando = input().strip()
            if comando == "q":
                print("\n🛑 Encerrando orquestrador...")
                os._exit(0)

            if comando in FILTRO_MAPA:
                filtro_exibicao = FILTRO_MAPA[comando]
                if filtro_exibicao == "TODOS":
                    print("\n👀 MUDOU A VISÃO: Mostrando TODOS os robôs...\n")
                else:
                    print(f"\n👀 MUDOU A VISÃO: Mostrando apenas {filtro_exibicao}...\n")
        except EOFError:
            break
        except Exception:
            continue


def _linhas_limpa(stdout_texto: str):
    return [ln.strip() for ln in (stdout_texto or "").splitlines() if ln.strip()]


def _tail_relevante(stdout_texto: str, limite: int = 12):
    linhas = _linhas_limpa(stdout_texto)
    return linhas[-limite:]


def _pick_first_number(patterns, text):
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                continue
    return None


def resumir_stdout(nome_bot: str, stdout_texto: str):
    linhas = _linhas_limpa(stdout_texto)
    txt = "\n".join(linhas)

    pdfs = _pick_first_number([
        r"PDFs?\s+baixados?\s*[:=]\s*(\d+)",
        r"total_pdfs\s*=\s*(\d+)",
        r"pdfs\s*=\s*(\d+)",
        r"baixadas?\s+(\d+)\s+faturas",
    ], txt)

    cnpjs = _pick_first_number([
        r"total\s+CNPJs\s*=\s*(\d+)",
        r"Jobs\s+carregados\s*:\s*(\d+)",
    ], txt)

    erros = [
        ln for ln in linhas
        if any(x in ln.lower() for x in ["erro", "traceback", "falhou", "exception", "crítico", "critico"])
    ]

    resumo = {
        "linhas_finais": _tail_relevante(stdout_texto, 12),
        "linhas_erro": erros[-8:],
        "pdfs": pdfs,
        "cnpjs": cnpjs,
    }

    if nome_bot == "ENEL":
        master = _pick_first_number([r"Master carregado:\s*(\d+)\s+registros"], txt)
        indice = _pick_first_number([r"Índice CSV:\s*(\d+)\s+faturas"], txt)
        if master is not None:
            resumo["master_registros"] = master
        if indice is not None:
            resumo["indice_faturas"] = indice

    return resumo


def formatar_duracao_segundos(segundos):
    if segundos is None:
        return "-"
    try:
        segundos = int(segundos)
    except Exception:
        return str(segundos)
    h, resto = divmod(segundos, 3600)
    m, s = divmod(resto, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


# =============================================================================
# LOCKS
# =============================================================================

class FileLock:
    def __init__(self, nome: str):
        self.path = LOCK_DIR / f"{nome}.lock"
        self.nome = nome
        self.acquired = False

    def acquire(self):
        if self.path.exists():
            try:
                conteudo = self.path.read_text(encoding="utf-8", errors="ignore").strip()
            except Exception:
                conteudo = ""
            raise RuntimeError(
                f"Lock já existe para '{self.nome}'. "
                f"Outra execução pode estar em andamento. {conteudo}"
            )

        dados = (
            f"pid={os.getpid()}\n"
            f"host={os.environ.get('COMPUTERNAME', '')}\n"
            f"started_at={agora_str()}\n"
        )
        self.path.write_text(dados, encoding="utf-8")
        self.acquired = True

    def release(self):
        if self.acquired and self.path.exists():
            try:
                self.path.unlink()
            except Exception:
                pass
        self.acquired = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()


# =============================================================================
# EXECUÇÃO DE BOTS POR SUBPROCESS
# =============================================================================

def executar_bot(caminho_script: str):
    nome_arquivo = Path(caminho_script).name.upper()
    nome_bot = identificar_bot_por_nome(nome_arquivo)
    inicio_dt = datetime.now()
    inicio_str = inicio_dt.strftime("%Y-%m-%d %H:%M:%S")

    print(f"[{nome_bot}] Iniciando automação...")
    log.info(f"[{nome_bot}] Iniciando automação... Script={caminho_script}")

    if not Path(caminho_script).exists():
        msg_erro = f"[{nome_bot}] Arquivo não encontrado: {caminho_script}"
        print(msg_erro)
        log.error(msg_erro)
        registrar_resultado(
            nome_bot, "ERRO", "Arquivo não encontrado.",
            script=caminho_script, origem="subprocess", inicio=inicio_str, fim=agora_str(),
            duracao_s=0, exit_code=None, stdout_texto="", resumo_stdout={},
        )
        falha_global.set()
        return

    try:
        env_utf8 = os.environ.copy()
        env_utf8["PYTHONUTF8"] = "1"
        env_utf8["PYTHONIOENCODING"] = "utf-8"
        env_utf8["PYTHONUNBUFFERED"] = "1"

        cmd = build_session_command([config.PYTHON_EXE, "-u", caminho_script])
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env_utf8,
        )

        saida_capturada = []

        def ler_tela_em_tempo_real():
            for linha in iter(proc.stdout.readline, ''):
                if linha:
                    saida_capturada.append(linha)
                    linha_limpa = linha.strip()
                    if linha_limpa:
                        print_filtrado(nome_bot, linha_limpa)
                        log.info(f"[{nome_bot}] {linha_limpa}")

        thread_leitura = threading.Thread(target=ler_tela_em_tempo_real, daemon=True)
        thread_leitura.start()

        while proc.poll() is None:
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                continue

        thread_leitura.join(timeout=5)

        codigo = proc.returncode
        texto_final = "".join(saida_capturada)
        fim_dt = datetime.now()
        duracao_s = int((fim_dt - inicio_dt).total_seconds())
        resumo_stdout = resumir_stdout(nome_bot, texto_final)

        if codigo == 0:
            print(f"[{nome_bot}] Concluído com sucesso.")
            log.info(f"[{nome_bot}] Concluído com sucesso.")
            registrar_resultado(
                nome_bot, "SUCESSO", texto_final.strip(),
                script=caminho_script, origem="subprocess", inicio=inicio_str,
                fim=fim_dt.strftime("%Y-%m-%d %H:%M:%S"), duracao_s=duracao_s,
                exit_code=codigo, stdout_texto=texto_final, resumo_stdout=resumo_stdout,
                total_pdfs=resumo_stdout.get("pdfs"),
            )
        else:
            print(f"[{nome_bot}] Falhou com código {codigo}.")
            log.error(f"[{nome_bot}] Falhou com código {codigo}.")
            registrar_resultado(
                nome_bot, "FALHA", texto_final.strip(),
                script=caminho_script, origem="subprocess", inicio=inicio_str,
                fim=fim_dt.strftime("%Y-%m-%d %H:%M:%S"), duracao_s=duracao_s,
                exit_code=codigo, stdout_texto=texto_final, resumo_stdout=resumo_stdout,
                total_pdfs=resumo_stdout.get("pdfs"),
            )
            falha_global.set()

    except Exception as e:
        fim_dt = datetime.now()
        print(f"[{nome_bot}] Erro crítico de execução: {e}")
        log.error(f"[{nome_bot}] Erro crítico de execução: {e}")
        log.error(traceback.format_exc())
        registrar_resultado(
            nome_bot, "ERRO_CRITICO", str(e),
            script=caminho_script, origem="subprocess", inicio=inicio_str,
            fim=fim_dt.strftime("%Y-%m-%d %H:%M:%S"),
            duracao_s=int((fim_dt - inicio_dt).total_seconds()),
            exit_code=None, stdout_texto="", resumo_stdout={},
        )
        falha_global.set()


# =============================================================================
# NEOENERGIA
# =============================================================================

ESTADOS_VALIDOS = {
    "Bahia": "COELBA",
    "Pernambuco": "CELPE",
    "Rio Grande do Norte": "COSERN",
    "Mato Grosso do Sul": "ELEKTRO",
    "São Paulo": "ELEKTRO",
}

COLUNAS_CNPJ = ["CNPJ", "cnpj"]
COLUNAS_SENHA = ["SENHA", "senha"]
COLUNA_ESTADOS_AGREGADA = ["ESTADOS", "estados", "ESTADO", "estado"]


def _get_first_existing(row: dict, candidates):
    for c in candidates:
        if c in row and row.get(c) is not None:
            return str(row.get(c) or "").strip()
    return ""


def fmt_doc(valor: str) -> str:
    return "".join(ch for ch in str(valor or "") if ch.isdigit())


def normalize_text(s: str) -> str:
    return " ".join((s or "").split()).strip()


def _fix_mojibake(s: str) -> str:
    txt = str(s or "")
    try:
        return txt.encode("latin1").decode("utf-8")
    except Exception:
        return txt


def _normalizar_estado_nome(s: str) -> str:
    txt = _fix_mojibake(normalize_text(s)).lower()
    txt = unicodedata.normalize("NFKD", txt)
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", txt)


_ESTADO_CANONICO = {
    "bahia": "Bahia",
    "pernambuco": "Pernambuco",
    "riograndedonorte": "Rio Grande do Norte",
    "matogrossodosul": "Mato Grosso do Sul",
    "saopaulo": "São Paulo",
}


def _parse_estados_from_row(row: dict):
    valor_agregado = _get_first_existing(row, COLUNA_ESTADOS_AGREGADA)
    if valor_agregado:
        partes = [normalize_text(x) for x in _fix_mojibake(valor_agregado).replace(";", "|").split("|")]
        estados = []
        for p in partes:
            canon = _ESTADO_CANONICO.get(_normalizar_estado_nome(p))
            if canon and canon in ESTADOS_VALIDOS:
                estados.append(canon)
        if estados:
            return estados

    encontrados = []
    row_norm = {_normalizar_estado_nome(k): v for k, v in row.items()}
    for estado in ESTADOS_VALIDOS:
        chave = _normalizar_estado_nome(estado)
        if chave in row_norm:
            valor = str(row_norm.get(chave) or "").strip().lower()
            if valor in {"1", "x", "sim", "s", "ok", "true", "y"} or (valor and valor not in {"0", "não", "nao", "n", "false"}):
                encontrados.append(estado)
    return encontrados


def carregar_jobs_base(csv_file: Path):
    if not csv_file.exists():
        raise FileNotFoundError(f"Arquivo base Neoenergia não encontrado: {csv_file}")

    jobs_base = []
    with open(csv_file, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cnpj = fmt_doc(_get_first_existing(row, COLUNAS_CNPJ))
            senha = _get_first_existing(row, COLUNAS_SENHA)
            estados = _parse_estados_from_row(row)

            if len(cnpj) != 14 or not senha or not estados:
                continue

            jobs_base.append({
                "cnpj": cnpj,
                "senha": senha,
                "estados_esperados": estados
            })

    return jobs_base


def distribuir_jobs_por_concessionaria(jobs_base):
    buckets = {"COELBA": [], "CELPE": [], "COSERN": [], "ELEKTRO": []}
    vistos = {k: set() for k in buckets}

    for job in jobs_base:
        cnpj = fmt_doc(job.get("cnpj", ""))
        senha = str(job.get("senha", "")).strip()
        estados = job.get("estados_esperados", []) or []

        por_conc = {}
        for estado in estados:
            conc = ESTADOS_VALIDOS.get(estado)
            if conc:
                por_conc.setdefault(conc, []).append(estado)

        for conc, estados_conc in por_conc.items():
            chave = (cnpj, tuple(sorted(estados_conc)))
            if chave in vistos[conc]:
                continue
            vistos[conc].add(chave)
            buckets[conc].append({
                "cnpj": cnpj,
                "senha": senha,
                "estados_esperados": estados_conc
            })

    return buckets


def _neo_worker_wrapper(nome_bot: str, neo_dir_str: str, jobs, lock, resultado_queue, progress_queue):
    try:
        if neo_dir_str not in sys.path:
            sys.path.insert(0, neo_dir_str)

        module_map = {
            "COELBA": ("worker_coelba", "run_worker_coelba"),
            "CELPE": ("worker_celpe", "run_worker_celpe"),
            "COSERN": ("worker_cosern", "run_worker_cosern"),
            "ELEKTRO": ("worker_elektro", "run_worker_elektro"),
        }

        module_name, func_name = module_map[nome_bot]
        mod = importlib.import_module(module_name)
        func = getattr(mod, func_name)

        inicio = agora_str()
        total = func(jobs, lock, progress_queue)

        resultado_queue.put({
            "worker": nome_bot,
            "status": "ok",
            "inicio": inicio,
            "fim": agora_str(),
            "total": total,
            "erro": ""
        })

    except Exception as e:
        resultado_queue.put({
            "worker": nome_bot,
            "status": "erro",
            "inicio": "",
            "fim": agora_str(),
            "total": 0,
            "erro": f"{e}\n{traceback.format_exc()}"
        })

    finally:
        try:
            progress_queue.put({"worker": nome_bot, "tipo": "fim_wrapper"})
        except Exception:
            pass


def executar_neoenergia():
    if not getattr(config, "NEOENERGIA_ATIVAR", True):
        return

    neo_dir = Path(getattr(config, "NEOENERGIA_DIR", CONFIG_DIR / "neoenergia"))
    neo_csv = Path(getattr(config, "NEOENERGIA_CSV", neo_dir / "cnpjs_neoenergia.csv"))

    if not neo_dir.exists():
        msg = f"Diretório Neoenergia não encontrado: {neo_dir}"
        print(f"[NEOENERGIA] {msg}")
        log.error(f"[NEOENERGIA] {msg}")
        registrar_resultado("NEOENERGIA", "ERRO", msg, origem="neoenergia", inicio=agora_str(), fim=agora_str(), duracao_s=0)
        falha_global.set()
        return

    try:
        for nome in ["worker_coelba.py", "worker_celpe.py", "worker_cosern.py", "worker_elektro.py"]:
            path = neo_dir / nome
            if not path.exists():
                raise FileNotFoundError(f"Worker Neoenergia não encontrado: {path}")

        jobs_base = carregar_jobs_base(neo_csv)
        jobs_por_conc = distribuir_jobs_por_concessionaria(jobs_base)

        resumo_progress = {
            nome: {
                "jobs": len(jobs_por_conc.get(nome, [])),
                "cnpjs_processados": 0,
                "total_pdfs": 0,
                "eventos": []
            }
            for nome in ["COELBA", "CELPE", "COSERN", "ELEKTRO"]
        }

        for conc, jobs in jobs_por_conc.items():
            log.info(f"[{conc}] Jobs Neoenergia carregados: {len(jobs)}")
            print_filtrado(conc, f"Jobs carregados: {len(jobs)}")

        shared_lock = Lock()
        resultado_q = Queue()
        progress_q = Queue()
        processos = {}
        nomes = ["COELBA", "CELPE", "COSERN", "ELEKTRO"]
        neo_falhou = False

        for conc in nomes:
            processos[conc] = Process(
                target=_neo_worker_wrapper,
                args=(conc, str(neo_dir), jobs_por_conc[conc], shared_lock, resultado_q, progress_q),
                daemon=False
            )

        def monitor_progress():
            wrappers_encerrados = 0

            while wrappers_encerrados < 4:
                try:
                    ev = progress_q.get(timeout=1)
                except Exception:
                    continue

                worker = str(ev.get("worker", "")).upper()
                tipo = ev.get("tipo", "")

                if tipo == "fim_wrapper":
                    wrappers_encerrados += 1
                    continue

                if not worker:
                    continue

                info = resumo_progress.setdefault(worker, {
                    "jobs": 0,
                    "cnpjs_processados": 0,
                    "total_pdfs": 0,
                    "eventos": []
                })

                if tipo == "inicio":
                    total = ev.get("total", 0)
                    info["jobs"] = total
                    msg = f"▶  Iniciando | {total} CNPJs"

                elif tipo == "cnpj_inicio":
                    i, total = ev.get("i", 0), ev.get("total", 0)
                    cnpj = ev.get("cnpj", "")
                    estados = ev.get("estados", "")
                    pct = int(i / total * 100) if total else 0
                    barra = "█" * (pct // 10) + "░" * (10 - pct // 10)
                    msg = f"[{i}/{total}] {barra} {pct}% | CNPJ {cnpj} | {estados}"
                    info["eventos"].append(msg)

                elif tipo == "cnpj_fim":
                    info["cnpjs_processados"] += 1
                    info["total_pdfs"] = ev.get("total_pdfs", info.get("total_pdfs", 0))
                    pdfs = ev.get("pdfs", 0)
                    total_pdfs = ev.get("total_pdfs", 0)
                    simbolo = "✔" if pdfs > 0 else "─"
                    msg = f"{simbolo} CNPJ {ev.get('cnpj','')[:8]}... | +{pdfs} PDF(s) | acumulado={total_pdfs}"
                    info["eventos"].append(msg)

                elif tipo == "uc_inicio":
                    uc = ev.get("uc", "")
                    estado = ev.get("estado", "")
                    i, total = ev.get("i", 0), ev.get("total", 0)
                    msg = f"  → UC [{i}/{total}] {uc} | {estado}"

                elif tipo == "uc_fim":
                    uc = ev.get("uc", "")
                    pdfs = ev.get("pdfs", 0)
                    status = ev.get("status", "")
                    simbolo = "✔" if pdfs > 0 else "·"
                    msg = f"  {simbolo} UC {uc} | {pdfs} PDF(s) baixado(s) | {status}"
                    info["eventos"].append(msg)

                elif tipo == "fatura":
                    ref = ev.get("referencia", "")
                    sit = ev.get("situacao", "")
                    msg = f"    📄 fatura {ref} | {sit}"

                elif tipo == "download_ok":
                    carimbo = ev.get("carimbo", "")
                    ref = ev.get("referencia", "")
                    msg = f"    ✔ Download OK | {ref} → {carimbo}"
                    info["eventos"].append(msg)

                elif tipo == "download_skip":
                    ref = ev.get("referencia", "")
                    motivo = ev.get("motivo", "")
                    msg = f"    ─ Pulado | {ref} | {motivo}"

                elif tipo == "login_ok":
                    cnpj = ev.get("cnpj", "")
                    msg = f"  🔑 Login OK | {cnpj}"

                elif tipo == "login_falha":
                    cnpj = ev.get("cnpj", "")
                    msg = f"  ✗ Login FALHOU | {cnpj}"
                    info["eventos"].append(msg)

                elif tipo == "estado_inicio":
                    estado = ev.get("estado", "")
                    msg = f"  📍 Estado: {estado}"
                elif tipo == "tela":
                    etapa = ev.get("etapa", "")
                    estado = ev.get("estado", "")
                    uc = ev.get("uc", "")
                    msg = f"  [TELA] {etapa} | estado={estado} | uc={uc}"

                elif tipo == "ucs_pagina":
                    estado = ev.get("estado", "")
                    pagina = ev.get("pagina", 0)
                    total_ucs = ev.get("total_ucs", 0)
                    ligadas = ev.get("ligadas", 0)
                    msg = f"  [UCS] pag={pagina} | estado={estado} | total={total_ucs} | ligadas={ligadas}"

                elif tipo == "fim":
                    info["total_pdfs"] = ev.get("total_pdfs", info.get("total_pdfs", 0))
                    info["duracao"] = ev.get("duracao", "")
                    total_pdfs = ev.get("total_pdfs", 0)
                    duracao = ev.get("duracao", "")
                    msg = f"■ Finalizado | {total_pdfs} PDF(s) | duração {duracao}"

                else:
                    partes = [
                        f"{k}={v}"
                        for k, v in ev.items()
                        if k not in ("worker", "tipo") and v not in (None, "", 0)
                    ]
                    msg = f"[{tipo}] {' | '.join(partes)}" if partes else tipo
                    info["eventos"].append(msg)

                print_filtrado(worker, msg)
                log.info(f"[{worker}] {msg}")

        for nome, p in processos.items():
            log.info(f"[{nome}] startando processo...")
            p.start()
            log.info(f"[{nome}] pid={p.pid} alive={p.is_alive()}")

        t_monitor = threading.Thread(target=monitor_progress, daemon=True)
        t_monitor.start()

        recebidos = 0
        timeout_sem_resultado = 900  # 15 min sem nenhum retorno
        ultimo_recebimento = time.time()

        while recebidos < 4:
            try:
                resultado = resultado_q.get(timeout=15)
                ultimo_recebimento = time.time()
            except Exception:
                vivos = {nome: p.is_alive() for nome, p in processos.items()}
                log.warning(f"[NEOENERGIA] aguardando workers... vivos={vivos}")

                mortos_sem_retorno = [nome for nome, p in processos.items() if not p.is_alive()]
                if mortos_sem_retorno:
                    raise RuntimeError(
                        f"Worker(s) encerrado(s) sem retornar resultado: {mortos_sem_retorno}"
                    )

                if (time.time() - ultimo_recebimento) > timeout_sem_resultado:
                    raise TimeoutError("Neoenergia ficou tempo demais sem retornar progresso/resultado.")

                continue

            recebidos += 1

            nome = str(resultado.get("worker", "")).upper()
            status = resultado.get("status")
            total = resultado.get("total", 0)
            erro = resultado.get("erro", "")
            inicio = resultado.get("inicio", "")
            fim = resultado.get("fim", "")

            duracao_s = None
            try:
                if inicio and fim:
                    duracao_s = int(
                        (
                            datetime.strptime(fim, "%Y-%m-%d %H:%M:%S") -
                            datetime.strptime(inicio, "%Y-%m-%d %H:%M:%S")
                        ).total_seconds()
                    )
            except Exception:
                pass

            info = resumo_progress.get(nome, {})
            eventos_finais = info.get("eventos", [])[-8:]

            detalhe_real = (
                f"Jobs carregados: {info.get('jobs', 0)}\n"
                f"CNPJs concluídos: {info.get('cnpjs_processados', 0)}\n"
                f"PDFs baixados: {total}\n"
            )
            if eventos_finais:
                detalhe_real += "Últimos eventos:\n" + "\n".join(eventos_finais)

            if status == "ok":
                registrar_resultado(
                    nome, "SUCESSO", detalhe_real.strip(),
                    origem="neoenergia_worker", inicio=inicio, fim=fim, duracao_s=duracao_s,
                    total_pdfs=total, jobs=info.get("jobs", 0), cnpjs_processados=info.get("cnpjs_processados", 0),
                    resumo_stdout={
                        "pdfs": total,
                        "cnpjs": info.get("cnpjs_processados", 0),
                        "linhas_finais": eventos_finais
                    },
                    stdout_texto="\n".join(eventos_finais),
                )
                log.info(f"[{nome}] Concluído com sucesso. PDFs={total}")
                print(f"[{nome}] Concluído com sucesso.")
            else:
                registrar_resultado(
                    nome, "FALHA", erro.strip() or detalhe_real.strip(),
                    origem="neoenergia_worker", inicio=inicio, fim=fim, duracao_s=duracao_s,
                    total_pdfs=total, jobs=info.get("jobs", 0), cnpjs_processados=info.get("cnpjs_processados", 0),
                    resumo_stdout={
                        "pdfs": total,
                        "cnpjs": info.get("cnpjs_processados", 0),
                        "linhas_erro": (erro or "").splitlines()[-8:],
                        "linhas_finais": eventos_finais
                    },
                    stdout_texto=(erro or "") + ("\n" + "\n".join(eventos_finais) if eventos_finais else ""),
                )
                log.error(f"[{nome}] Falhou. {erro}")
                print(f"[{nome}] Falhou.")
                neo_falhou = True

        if neo_falhou:
            for p in processos.values():
                if p.is_alive():
                    try:
                        p.terminate()
                    except Exception:
                        pass

        for p in processos.values():
            p.join(timeout=10)

        t_monitor.join(timeout=3)

    except Exception as e:
        msg = f"Erro crítico na integração Neoenergia: {e}"
        print(f"[NEOENERGIA] {msg}")
        log.error(f"[NEOENERGIA] {msg}")
        log.error(traceback.format_exc())
        registrar_resultado(
            "NEOENERGIA", "ERRO_CRITICO",
            f"{e}\n{traceback.format_exc()}",
            origem="neoenergia",
            inicio=agora_str(),
            fim=agora_str(),
            duracao_s=0
        )
        falha_global.set()


# =============================================================================
# E-MAIL
# =============================================================================

def _html_status_badge(status: str):
    cores = {
        "SUCESSO": ("#e8f5e9", "#1b5e20"),
        "FALHA": ("#ffebee", "#b71c1c"),
        "ERRO": ("#ffebee", "#b71c1c"),
        "ERRO_CRITICO": ("#ffebee", "#b71c1c"),
        "ABORTADO": ("#fff8e1", "#8d6e00"),
    }
    fundo, texto = cores.get(status, ("#eceff1", "#37474f"))
    return f'<span style="background:{fundo};color:{texto};padding:4px 8px;border-radius:6px;font-weight:bold;">{html.escape(status)}</span>'


def _gerar_corpo_resumo(tempo_execucao):
    ordem = [
        "CEMIG",
        "ENEL",
        "COELBA",
        "CELPE",
        "COSERN",
        "ELEKTRO",
        "NEOENERGIA",
        "PIPELINE_CEMIG",
        "PIPELINE_ENEL",
        "PIPELINE_NEOENERGIA_ELEKTRO",
        "PIPELINE_NEOENERGIA_BAHIA",
        "PIPELINE_NEOENERGIA_PERNAMBUCO",
    ]
    bots = [b for b in ordem if b in resultados_finais] + [b for b in resultados_finais if b not in ordem]

    total_ok = sum(1 for b in bots if resultados_finais.get(b, {}).get("status") == "SUCESSO")
    total_falha = sum(1 for b in bots if resultados_finais.get(b, {}).get("status") != "SUCESSO")
    total_pdfs = sum(
        int(
            resultados_finais.get(b, {}).get("total_pdfs")
            or resultados_finais.get(b, {}).get("resumo_stdout", {}).get("pdfs")
            or 0
        )
        for b in bots
    )

    texto = [
        "RESUMO REAL DA EXECUÇÃO DIÁRIA - ORQUESTRADOR DE FATURAS",
        "=" * 72,
        f"Duração total: {formatar_duracao_segundos(tempo_execucao)} ({tempo_execucao}s)",
        f"Bots com sucesso: {total_ok}",
        f"Bots com falha: {total_falha}",
        f"Total de PDFs informados: {total_pdfs}",
        "",
    ]

    html_parts = ["<html><body style='font-family:Arial,sans-serif;font-size:14px;color:#222;'>"]
    html_parts.append("<h2 style='margin-bottom:8px;'>Resumo real da execução diária</h2>")
    html_parts.append("<table style='border-collapse:collapse;margin-bottom:16px;'>")

    for rot, val in [
        ("Duração total", f"{formatar_duracao_segundos(tempo_execucao)} ({tempo_execucao}s)"),
        ("Bots com sucesso", str(total_ok)),
        ("Bots com falha", str(total_falha)),
        ("Total de PDFs informados", str(total_pdfs)),
        ("Log", str(log_file)),
    ]:
        html_parts.append(
            f"<tr><td style='padding:6px 10px;border:1px solid #ddd;background:#f7f7f7;'><b>{html.escape(rot)}</b></td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd;'>{html.escape(val)}</td></tr>"
        )

    html_parts.append("</table>")
    html_parts.append("<table style='border-collapse:collapse;width:100%;margin-bottom:18px;'>")
    html_parts.append(
        "<tr>"
        "<th style='border:1px solid #ccc;padding:8px;background:#f2f2f2;'>Bot</th>"
        "<th style='border:1px solid #ccc;padding:8px;background:#f2f2f2;'>Status</th>"
        "<th style='border:1px solid #ccc;padding:8px;background:#f2f2f2;'>Início</th>"
        "<th style='border:1px solid #ccc;padding:8px;background:#f2f2f2;'>Fim</th>"
        "<th style='border:1px solid #ccc;padding:8px;background:#f2f2f2;'>Duração</th>"
        "<th style='border:1px solid #ccc;padding:8px;background:#f2f2f2;'>PDFs</th>"
        "<th style='border:1px solid #ccc;padding:8px;background:#f2f2f2;'>CNPJs/Jobs</th>"
        "</tr>"
    )

    for bot in bots:
        r = resultados_finais.get(bot, {})
        resumo_stdout = r.get("resumo_stdout", {}) or {}
        pdfs = r.get("total_pdfs") if r.get("total_pdfs") is not None else resumo_stdout.get("pdfs")
        cnpjs_v = r.get("cnpjs_processados") if r.get("cnpjs_processados") is not None else resumo_stdout.get("cnpjs")
        jobs_v = r.get("jobs")
        cj_txt = "-" if cnpjs_v is None and jobs_v is None else f"{cnpjs_v if cnpjs_v is not None else '-'} / {jobs_v if jobs_v is not None else '-'}"

        html_parts.append(
            f"<tr>"
            f"<td style='border:1px solid #ddd;padding:8px;'><b>{html.escape(bot)}</b></td>"
            f"<td style='border:1px solid #ddd;padding:8px;'>{_html_status_badge(r.get('status', '-'))}</td>"
            f"<td style='border:1px solid #ddd;padding:8px;'>{html.escape(str(r.get('inicio', '-') or '-'))}</td>"
            f"<td style='border:1px solid #ddd;padding:8px;'>{html.escape(str(r.get('fim', '-') or '-'))}</td>"
            f"<td style='border:1px solid #ddd;padding:8px;'>{html.escape(formatar_duracao_segundos(r.get('duracao_s')))}</td>"
            f"<td style='border:1px solid #ddd;padding:8px;'>{html.escape(str(pdfs if pdfs is not None else '-'))}</td>"
            f"<td style='border:1px solid #ddd;padding:8px;'>{html.escape(cj_txt)}</td>"
            f"</tr>"
        )

    html_parts.append("</table>")

    for bot in bots:
        r = resultados_finais.get(bot, {})
        resumo_stdout = r.get("resumo_stdout", {}) or {}
        detalhe = (r.get("detalhe") or "").strip()
        finais = resumo_stdout.get("linhas_finais") or []
        erros = resumo_stdout.get("linhas_erro") or []
        script = r.get("script") or r.get("origem") or "-"

        texto.append(f"[{bot}] {r.get('status', '-')}")
        texto.append(f"- Início: {r.get('inicio', '-') or '-'}")
        texto.append(f"- Fim: {r.get('fim', '-') or '-'}")
        texto.append(f"- Duração: {formatar_duracao_segundos(r.get('duracao_s'))}")
        texto.append(f"- Origem/script: {script}")

        if r.get("total_pdfs") is not None or resumo_stdout.get("pdfs") is not None:
            texto.append(f"- PDFs: {r.get('total_pdfs') if r.get('total_pdfs') is not None else resumo_stdout.get('pdfs')}")

        if r.get("cnpjs_processados") is not None or resumo_stdout.get("cnpjs") is not None or r.get("jobs") is not None:
            texto.append(f"- CNPJs concluídos: {r.get('cnpjs_processados') if r.get('cnpjs_processados') is not None else resumo_stdout.get('cnpjs', '-')}")
            texto.append(f"- Jobs carregados: {r.get('jobs', '-')}")

        if detalhe:
            texto.append("- Resumo:")
            texto.extend([f"    {ln}" for ln in detalhe.splitlines()[:12]])

        if finais:
            texto.append("- Últimas linhas relevantes:")
            texto.extend([f"    {ln}" for ln in finais])

        if erros:
            texto.append("- Linhas de erro:")
            texto.extend([f"    {ln}" for ln in erros])

        texto.append("")

        html_parts.append(f"<h3 style='margin:18px 0 8px 0;'>{html.escape(bot)} {_html_status_badge(r.get('status', '-'))}</h3>")
        html_parts.append("<table style='border-collapse:collapse;margin-bottom:10px;'>")

        infos = [
            ("Início", str(r.get("inicio", "-") or "-")),
            ("Fim", str(r.get("fim", "-") or "-")),
            ("Duração", formatar_duracao_segundos(r.get("duracao_s"))),
            ("Origem/script", str(script)),
        ]

        if r.get("total_pdfs") is not None or resumo_stdout.get("pdfs") is not None:
            infos.append(("PDFs", str(r.get("total_pdfs") if r.get("total_pdfs") is not None else resumo_stdout.get("pdfs"))))

        if r.get("cnpjs_processados") is not None or resumo_stdout.get("cnpjs") is not None:
            infos.append(("CNPJs concluídos", str(r.get("cnpjs_processados") if r.get("cnpjs_processados") is not None else resumo_stdout.get("cnpjs"))))

        if r.get("jobs") is not None:
            infos.append(("Jobs carregados", str(r.get("jobs"))))

        for rot, val in infos:
            html_parts.append(
                f"<tr><td style='padding:6px 10px;border:1px solid #ddd;background:#f7f7f7;'><b>{html.escape(rot)}</b></td>"
                f"<td style='padding:6px 10px;border:1px solid #ddd;'>{html.escape(val)}</td></tr>"
            )

        html_parts.append("</table>")

        if detalhe:
            html_parts.append("<div style='margin-bottom:8px;'><b>Resumo:</b></div>")
            html_parts.append(
                f"<pre style='background:#fafafa;border:1px solid #ddd;padding:10px;white-space:pre-wrap;'>"
                f"{html.escape(chr(10).join(detalhe.splitlines()[:20]))}</pre>"
            )

        if finais:
            html_parts.append("<div style='margin-bottom:8px;'><b>Últimas linhas relevantes:</b></div>")
            html_parts.append(
                f"<pre style='background:#fafafa;border:1px solid #ddd;padding:10px;white-space:pre-wrap;'>"
                f"{html.escape(chr(10).join(finais))}</pre>"
            )

        if erros:
            html_parts.append("<div style='margin-bottom:8px;'><b>Linhas de erro:</b></div>")
            html_parts.append(
                f"<pre style='background:#fff5f5;border:1px solid #f3c2c2;padding:10px;white-space:pre-wrap;color:#8a1c1c;'>"
                f"{html.escape(chr(10).join(erros))}</pre>"
            )

    texto.append("=" * 72)
    texto.append(f"Log local: {log_file}")
    html_parts.append(f"<hr><div><b>Log local:</b> {html.escape(str(log_file))}</div>")
    html_parts.append("</body></html>")

    return "\n".join(texto), "".join(html_parts)


def enviar_email_resumo(tempo_execucao):
    teve_falha = falha_global.is_set()
    assunto = "🔴 [FALHA] Relatório Diário - Faturas" if teve_falha else "🟢 [SUCESSO] Relatório Diário - Faturas"

    corpo_texto, corpo_html = _gerar_corpo_resumo(tempo_execucao)

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = assunto
        msg["From"] = config.EMAIL_REMETENTE
        msg["To"] = ", ".join(config.EMAIL_DESTINATARIOS)

        msg.attach(MIMEText(corpo_texto, "plain", "utf-8"))
        msg.attach(MIMEText(corpo_html, "html", "utf-8"))

        if getattr(config, "EMAIL_SMTP_TLS", False):
            servidor = smtplib.SMTP(config.EMAIL_SMTP_HOST, config.EMAIL_SMTP_PORT)
            servidor.ehlo()
            servidor.starttls()
        else:
            servidor = smtplib.SMTP_SSL(config.EMAIL_SMTP_HOST, config.EMAIL_SMTP_PORT)

        servidor.login(config.EMAIL_REMETENTE, config.EMAIL_SENHA)
        servidor.sendmail(config.EMAIL_REMETENTE, config.EMAIL_DESTINATARIOS, msg.as_string())
        servidor.quit()

        print("\n📧 E-mail de resumo enviado com sucesso!")
    except Exception as e:
        print(f"\n❌ Falha ao enviar e-mail de resumo: {e}")
        log.error(f"Falha ao enviar e-mail de resumo: {e}")
        log.error(traceback.format_exc())


# =============================================================================
# PIPELINE CEMIG
# =============================================================================

def executar_pipeline_cemig():
    if not getattr(config, "PIPELINE_CEMIG_ATIVAR", False):
        return

    caminho_script = getattr(config, "PIPELINE_CEMIG_SCRIPT", "")
    nome_bot = "PIPELINE_CEMIG"
    inicio_dt = datetime.now()
    inicio_str = inicio_dt.strftime("%Y-%m-%d %H:%M:%S")

    print(f"[{nome_bot}] Iniciando pipeline OCR + Digitação + Filtro...")
    log.info(f"[{nome_bot}] Iniciando. Script={caminho_script}")

    if not Path(caminho_script).exists():
        msg = f"Script não encontrado: {caminho_script}"
        print(f"[{nome_bot}] {msg}")
        log.error(f"[{nome_bot}] {msg}")
        registrar_resultado(
            nome_bot, "ERRO", msg,
            script=caminho_script, origem="subprocess", inicio=inicio_str,
            fim=agora_str(), duracao_s=0, exit_code=None,
            stdout_texto="", resumo_stdout={},
        )
        falha_global.set()
        return

    try:
        env_utf8 = os.environ.copy()
        env_utf8["PYTHONUTF8"] = "1"
        env_utf8["PYTHONIOENCODING"] = "utf-8"
        env_utf8["PYTHONUNBUFFERED"] = "1"

        cmd = build_session_command([config.PYTHON_EXE, "-u", caminho_script])
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env_utf8,
        )

        saida_capturada = []

        def _ler():
            for linha in iter(proc.stdout.readline, ""):
                if linha:
                    saida_capturada.append(linha)
                    linha_limpa = linha.strip()
                    if linha_limpa:
                        print_filtrado(nome_bot, linha_limpa)
                        log.info(f"[{nome_bot}] {linha_limpa}")

        t_leitura = threading.Thread(target=_ler, daemon=True)
        t_leitura.start()
        proc.wait()
        t_leitura.join(timeout=5)

        codigo = proc.returncode
        texto_final = "".join(saida_capturada)
        fim_dt = datetime.now()
        duracao_s = int((fim_dt - inicio_dt).total_seconds())
        resumo = resumir_stdout(nome_bot, texto_final)

        faturas_ok = _pick_first_number([r"movidos\s*:\s*(\d+)", r"PDFs movidos\s*:\s*(\d+)"], texto_final)
        faturas_dig = _pick_first_number([r"auditoria registrada:\s*(\d+)"], texto_final)

        status = "SUCESSO" if codigo == 0 else "FALHA"
        print(f"[{nome_bot}] {status} (exit {codigo}).")
        log.info(f"[{nome_bot}] {status} (exit {codigo}).")

        registrar_resultado(
            nome_bot, status, texto_final.strip(),
            script=caminho_script, origem="subprocess",
            inicio=inicio_str, fim=fim_dt.strftime("%Y-%m-%d %H:%M:%S"),
            duracao_s=duracao_s, exit_code=codigo,
            stdout_texto=texto_final, resumo_stdout=resumo,
            total_pdfs=faturas_ok,
            cnpjs_processados=faturas_dig,
        )

        if codigo != 0:
            falha_global.set()

    except Exception as e:
        fim_dt = datetime.now()
        print(f"[{nome_bot}] Erro crítico: {e}")
        log.error(f"[{nome_bot}] Erro crítico: {e}")
        log.error(traceback.format_exc())
        registrar_resultado(
            nome_bot, "ERRO_CRITICO", str(e),
            script=caminho_script, origem="subprocess", inicio=inicio_str,
            fim=fim_dt.strftime("%Y-%m-%d %H:%M:%S"),
            duracao_s=int((fim_dt - inicio_dt).total_seconds()),
            exit_code=None, stdout_texto="", resumo_stdout={},
        )
        falha_global.set()


def executar_pipeline_enel():
    if not getattr(config, "PIPELINE_ENEL_ATIVAR", False):
        return

    caminho_script = getattr(config, "PIPELINE_ENEL_SCRIPT", "")
    nome_bot = "PIPELINE_ENEL"
    inicio_dt = datetime.now()
    inicio_str = inicio_dt.strftime("%Y-%m-%d %H:%M:%S")

    print(f"[{nome_bot}] Iniciando pipeline OCR + Digitação + Filtro...")
    log.info(f"[{nome_bot}] Iniciando. Script={caminho_script}")

    if not Path(caminho_script).exists():
        msg = f"Script não encontrado: {caminho_script}"
        print(f"[{nome_bot}] {msg}")
        log.error(f"[{nome_bot}] {msg}")
        registrar_resultado(
            nome_bot, "ERRO", msg,
            script=caminho_script, origem="subprocess", inicio=inicio_str,
            fim=agora_str(), duracao_s=0, exit_code=None,
            stdout_texto="", resumo_stdout={},
        )
        falha_global.set()
        return

    try:
        env_utf8 = os.environ.copy()
        env_utf8["PYTHONUTF8"] = "1"
        env_utf8["PYTHONIOENCODING"] = "utf-8"
        env_utf8["PYTHONUNBUFFERED"] = "1"

        cmd = build_session_command([config.PYTHON_EXE, "-u", caminho_script])
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env_utf8,
        )

        saida_capturada = []

        def _ler():
            for linha in iter(proc.stdout.readline, ""):
                if linha:
                    saida_capturada.append(linha)
                    linha_limpa = linha.strip()
                    if linha_limpa:
                        print_filtrado(nome_bot, linha_limpa)
                        log.info(f"[{nome_bot}] {linha_limpa}")

        t_leitura = threading.Thread(target=_ler, daemon=True)
        t_leitura.start()
        proc.wait()
        t_leitura.join(timeout=5)

        codigo = proc.returncode
        texto_final = "".join(saida_capturada)
        fim_dt = datetime.now()
        duracao_s = int((fim_dt - inicio_dt).total_seconds())
        resumo = resumir_stdout(nome_bot, texto_final)

        faturas_ok = _pick_first_number([r"movidos\s*:\s*(\d+)", r"PDFs movidos\s*:\s*(\d+)"], texto_final)
        faturas_dig = _pick_first_number([r"auditoria registrada:\s*(\d+)"], texto_final)

        status = "SUCESSO" if codigo == 0 else "FALHA"
        print(f"[{nome_bot}] {status} (exit {codigo}).")
        log.info(f"[{nome_bot}] {status} (exit {codigo}).")

        registrar_resultado(
            nome_bot, status, texto_final.strip(),
            script=caminho_script, origem="subprocess",
            inicio=inicio_str, fim=fim_dt.strftime("%Y-%m-%d %H:%M:%S"),
            duracao_s=duracao_s, exit_code=codigo,
            stdout_texto=texto_final, resumo_stdout=resumo,
            total_pdfs=faturas_ok,
            cnpjs_processados=faturas_dig,
        )

        if codigo != 0:
            falha_global.set()

    except Exception as e:
        fim_dt = datetime.now()
        print(f"[{nome_bot}] Erro crítico: {e}")
        log.error(f"[{nome_bot}] Erro crítico: {e}")
        log.error(traceback.format_exc())
        registrar_resultado(
            nome_bot, "ERRO_CRITICO", str(e),
            script=caminho_script, origem="subprocess", inicio=inicio_str,
            fim=fim_dt.strftime("%Y-%m-%d %H:%M:%S"),
            duracao_s=int((fim_dt - inicio_dt).total_seconds()),
            exit_code=None, stdout_texto="", resumo_stdout={},
        )
        falha_global.set()


def _executar_pipeline_neoenergia_por_chave(nome_bot: str, ativar_key: str, script_key: str):
    if not getattr(config, ativar_key, False):
        return

    caminho_script = getattr(config, script_key, "")
    inicio_dt = datetime.now()
    inicio_str = inicio_dt.strftime("%Y-%m-%d %H:%M:%S")

    print(f"[{nome_bot}] Iniciando pipeline OCR + Digitacao + Filtro...")
    log.info(f"[{nome_bot}] Iniciando. Script={caminho_script}")

    if not Path(caminho_script).exists():
        msg = f"Script nao encontrado: {caminho_script}"
        print(f"[{nome_bot}] {msg}")
        log.error(f"[{nome_bot}] {msg}")
        registrar_resultado(
            nome_bot, "ERRO", msg,
            script=caminho_script, origem="subprocess", inicio=inicio_str,
            fim=agora_str(), duracao_s=0, exit_code=None,
            stdout_texto="", resumo_stdout={},
        )
        falha_global.set()
        return

    try:
        env_utf8 = os.environ.copy()
        env_utf8["PYTHONUTF8"] = "1"
        env_utf8["PYTHONIOENCODING"] = "utf-8"
        env_utf8["PYTHONUNBUFFERED"] = "1"

        proc = subprocess.Popen(
            [config.PYTHON_EXE, "-u", caminho_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env_utf8,
        )

        saida_capturada = []

        def _ler():
            for linha in iter(proc.stdout.readline, ""):
                if linha:
                    saida_capturada.append(linha)
                    linha_limpa = linha.strip()
                    if linha_limpa:
                        print_filtrado(nome_bot, linha_limpa)
                        log.info(f"[{nome_bot}] {linha_limpa}")

        t_leitura = threading.Thread(target=_ler, daemon=True)
        t_leitura.start()
        proc.wait()
        t_leitura.join(timeout=5)

        codigo = proc.returncode
        texto_final = "".join(saida_capturada)
        fim_dt = datetime.now()
        duracao_s = int((fim_dt - inicio_dt).total_seconds())
        resumo = resumir_stdout(nome_bot, texto_final)

        faturas_ok = _pick_first_number([r"movidos\s*:\s*(\d+)", r"PDFs movidos\s*:\s*(\d+)"], texto_final)
        faturas_dig = _pick_first_number([r"auditoria registrada:\s*(\d+)"], texto_final)

        status = "SUCESSO" if codigo == 0 else "FALHA"
        print(f"[{nome_bot}] {status} (exit {codigo}).")
        log.info(f"[{nome_bot}] {status} (exit {codigo}).")

        registrar_resultado(
            nome_bot, status, texto_final.strip(),
            script=caminho_script, origem="subprocess",
            inicio=inicio_str, fim=fim_dt.strftime("%Y-%m-%d %H:%M:%S"),
            duracao_s=duracao_s, exit_code=codigo,
            stdout_texto=texto_final, resumo_stdout=resumo,
            total_pdfs=faturas_ok,
            cnpjs_processados=faturas_dig,
        )

        if codigo != 0:
            falha_global.set()

    except Exception as e:
        fim_dt = datetime.now()
        print(f"[{nome_bot}] Erro critico: {e}")
        log.error(f"[{nome_bot}] Erro critico: {e}")
        log.error(traceback.format_exc())
        registrar_resultado(
            nome_bot, "ERRO_CRITICO", str(e),
            script=caminho_script, origem="subprocess", inicio=inicio_str,
            fim=fim_dt.strftime("%Y-%m-%d %H:%M:%S"),
            duracao_s=int((fim_dt - inicio_dt).total_seconds()),
            exit_code=None, stdout_texto="", resumo_stdout={},
        )
        falha_global.set()


def executar_pipeline_neoenergia_elektro():
    _executar_pipeline_neoenergia_por_chave(
        "PIPELINE_NEOENERGIA_ELEKTRO",
        "PIPELINE_NEOENERGIA_ELEKTRO_ATIVAR",
        "PIPELINE_NEOENERGIA_ELEKTRO_SCRIPT",
    )


def executar_pipeline_neoenergia_bahia():
    _executar_pipeline_neoenergia_por_chave(
        "PIPELINE_NEOENERGIA_BAHIA",
        "PIPELINE_NEOENERGIA_BAHIA_ATIVAR",
        "PIPELINE_NEOENERGIA_BAHIA_SCRIPT",
    )


def executar_pipeline_neoenergia_pernambuco():
    _executar_pipeline_neoenergia_por_chave(
        "PIPELINE_NEOENERGIA_PERNAMBUCO",
        "PIPELINE_NEOENERGIA_PERNAMBUCO_ATIVAR",
        "PIPELINE_NEOENERGIA_PERNAMBUCO_SCRIPT",
    )


# =============================================================================
# ROTINAS PRINCIPAIS
# =============================================================================

def _resetar_estado_execucao():
    global falha_global, resultados_finais
    falha_global = threading.Event()
    resultados_finais = {}


def _executar_downloaders():
    _resetar_estado_execucao()
    inicio = datetime.now()

    print("=" * 60)
    print(f"🚀 DOWNLOADERS INICIADOS ({inicio.strftime('%H:%M')})")
    print("=" * 60)

    threads = []

    try:
        with FileLock("downloads"):
            for script in getattr(config, "SCRIPTS", []):
                t = threading.Thread(target=executar_bot, args=(script,), daemon=False)
                threads.append(t)
                t.start()

            # Neoenergia roda no fluxo principal desta execução
            # para evitar uma camada extra de thread antes do multiprocessing.
            if getattr(config, "NEOENERGIA_ATIVAR", True):
                executar_neoenergia()

            for t in threads:
                t.join()

    except Exception as e:
        log.error(f"[DOWNLOADS] Erro geral: {e}")
        log.error(traceback.format_exc())
        print(f"[DOWNLOADS] Erro geral: {e}")
        falha_global.set()

    duracao = int((datetime.now() - inicio).total_seconds())

    print("\n" + "=" * 60)
    if falha_global.is_set():
        print(f"❌ DOWNLOADS FINALIZADOS COM FALHAS em {duracao}s.")
    else:
        print(f"✅ DOWNLOADS CONCLUÍDOS COM SUCESSO em {duracao}s.")

    enviar_email_resumo(duracao)


def _executar_pipeline_cemig_agendado():
    _resetar_estado_execucao()
    inicio = datetime.now()

    print("=" * 60)
    print(f"🚀 PIPELINE CEMIG INICIADO ({inicio.strftime('%H:%M')})")
    print("=" * 60)

    try:
        with FileLock("pipeline_cemig"):
            executar_pipeline_cemig()
    except Exception as e:
        log.error(f"[PIPELINE_CEMIG] Erro geral: {e}")
        log.error(traceback.format_exc())
        print(f"[PIPELINE_CEMIG] Erro geral: {e}")
        falha_global.set()

    duracao = int((datetime.now() - inicio).total_seconds())

    print("\n" + "=" * 60)
    if falha_global.is_set():
        print(f"❌ PIPELINE CEMIG FINALIZADO COM FALHAS em {duracao}s.")
    else:
        print(f"✅ PIPELINE CEMIG CONCLUÍDO COM SUCESSO em {duracao}s.")

    enviar_email_resumo(duracao)


def _executar_pipeline_enel_agendado():
    _resetar_estado_execucao()
    inicio = datetime.now()

    print("=" * 60)
    print(f"🚀 PIPELINE ENEL INICIADO ({inicio.strftime('%H:%M')})")
    print("=" * 60)

    try:
        with FileLock("pipeline_enel"):
            executar_pipeline_enel()
    except Exception as e:
        log.error(f"[PIPELINE_ENEL] Erro geral: {e}")
        log.error(traceback.format_exc())
        print(f"[PIPELINE_ENEL] Erro geral: {e}")
        falha_global.set()

    duracao = int((datetime.now() - inicio).total_seconds())

    print("\n" + "=" * 60)
    if falha_global.is_set():
        print(f"❌ PIPELINE ENEL FINALIZADO COM FALHAS em {duracao}s.")
    else:
        print(f"✅ PIPELINE ENEL CONCLUÍDO COM SUCESSO em {duracao}s.")

    enviar_email_resumo(duracao)


def _executar_pipeline_neoenergia_elektro_agendado():
    _resetar_estado_execucao()
    inicio = datetime.now()

    print("=" * 60)
    print(f"PIPELINE NEOENERGIA ELEKTRO INICIADO ({inicio.strftime('%H:%M')})")
    print("=" * 60)

    try:
        with FileLock("pipeline_neoenergia_elektro"):
            executar_pipeline_neoenergia_elektro()
    except Exception as e:
        log.error(f"[PIPELINE_NEOENERGIA_ELEKTRO] Erro geral: {e}")
        log.error(traceback.format_exc())
        print(f"[PIPELINE_NEOENERGIA_ELEKTRO] Erro geral: {e}")
        falha_global.set()

    duracao = int((datetime.now() - inicio).total_seconds())

    print("\n" + "=" * 60)
    if falha_global.is_set():
        print(f"PIPELINE NEOENERGIA ELEKTRO FINALIZADO COM FALHAS em {duracao}s.")
    else:
        print(f"PIPELINE NEOENERGIA ELEKTRO CONCLUIDO COM SUCESSO em {duracao}s.")

    enviar_email_resumo(duracao)


def _executar_pipeline_neoenergia_bahia_agendado():
    _resetar_estado_execucao()
    inicio = datetime.now()

    print("=" * 60)
    print(f"PIPELINE NEOENERGIA BAHIA INICIADO ({inicio.strftime('%H:%M')})")
    print("=" * 60)

    try:
        with FileLock("pipeline_neoenergia_bahia"):
            executar_pipeline_neoenergia_bahia()
    except Exception as e:
        log.error(f"[PIPELINE_NEOENERGIA_BAHIA] Erro geral: {e}")
        log.error(traceback.format_exc())
        print(f"[PIPELINE_NEOENERGIA_BAHIA] Erro geral: {e}")
        falha_global.set()

    duracao = int((datetime.now() - inicio).total_seconds())

    print("\n" + "=" * 60)
    if falha_global.is_set():
        print(f"PIPELINE NEOENERGIA BAHIA FINALIZADO COM FALHAS em {duracao}s.")
    else:
        print(f"PIPELINE NEOENERGIA BAHIA CONCLUIDO COM SUCESSO em {duracao}s.")

    enviar_email_resumo(duracao)


def _executar_pipeline_neoenergia_pernambuco_agendado():
    _resetar_estado_execucao()
    inicio = datetime.now()

    print("=" * 60)
    print(f"PIPELINE NEOENERGIA PERNAMBUCO INICIADO ({inicio.strftime('%H:%M')})")
    print("=" * 60)

    try:
        with FileLock("pipeline_neoenergia_pernambuco"):
            executar_pipeline_neoenergia_pernambuco()
    except Exception as e:
        log.error(f"[PIPELINE_NEOENERGIA_PERNAMBUCO] Erro geral: {e}")
        log.error(traceback.format_exc())
        print(f"[PIPELINE_NEOENERGIA_PERNAMBUCO] Erro geral: {e}")
        falha_global.set()

    duracao = int((datetime.now() - inicio).total_seconds())

    print("\n" + "=" * 60)
    if falha_global.is_set():
        print(f"PIPELINE NEOENERGIA PERNAMBUCO FINALIZADO COM FALHAS em {duracao}s.")
    else:
        print(f"PIPELINE NEOENERGIA PERNAMBUCO CONCLUIDO COM SUCESSO em {duracao}s.")

    enviar_email_resumo(duracao)


# =============================================================================
# LEGADO — SUBPROCESSOS DO ORQUESTRADOR
# =============================================================================

def _disparar_subprocesso_orquestrador(args_extra, nome_execucao):
    cmd = [config.PYTHON_EXE, "-u", str(Path(__file__).resolve())] + list(args_extra)

    log.info(f"[LAUNCHER] Disparando subprocesso {nome_execucao}: {' '.join(cmd)}")
    print(f"[LAUNCHER] Disparando subprocesso {nome_execucao}...")

    env_utf8 = os.environ.copy()
    env_utf8["PYTHONUTF8"] = "1"
    env_utf8["PYTHONIOENCODING"] = "utf-8"
    env_utf8["PYTHONUNBUFFERED"] = "1"

    subprocess.Popen(
        cmd,
        cwd=str(CONFIG_DIR),
        env=env_utf8,
        creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
    )


def _agendar_disparo_downloads():
    try:
        if (LOCK_DIR / "downloads.lock").exists():
            log.warning("[LAUNCHER] Downloads já estão em execução. Pulando novo disparo.")
            print("[LAUNCHER] Downloads já estão em execução. Pulando.")
            return
        _disparar_subprocesso_orquestrador(["--so-downloads"], "downloads")
    except Exception as e:
        log.error(f"[LAUNCHER] Falha ao disparar downloads: {e}")
        log.error(traceback.format_exc())
        print(f"[LAUNCHER] Falha ao disparar downloads: {e}")


def _agendar_disparo_pipeline():
    try:
        if (LOCK_DIR / "pipeline_cemig.lock").exists():
            log.warning("[LAUNCHER] Pipeline CEMIG já está em execução. Pulando novo disparo.")
            print("[LAUNCHER] Pipeline CEMIG já está em execução. Pulando.")
            return
        _disparar_subprocesso_orquestrador(["--so-pipeline"], "pipeline_cemig")
    except Exception as e:
        log.error(f"[LAUNCHER] Falha ao disparar pipeline CEMIG: {e}")
        log.error(traceback.format_exc())
        print(f"[LAUNCHER] Falha ao disparar pipeline CEMIG: {e}")


def _agendar_disparo_pipeline_enel():
    try:
        if (LOCK_DIR / "pipeline_enel.lock").exists():
            log.warning("[LAUNCHER] Pipeline ENEL já está em execução. Pulando novo disparo.")
            print("[LAUNCHER] Pipeline ENEL já está em execução. Pulando.")
            return
        _disparar_subprocesso_orquestrador(["--so-pipeline-enel"], "pipeline_enel")
    except Exception as e:
        log.error(f"[LAUNCHER] Falha ao disparar pipeline ENEL: {e}")
        log.error(traceback.format_exc())
        print(f"[LAUNCHER] Falha ao disparar pipeline ENEL: {e}")


def _agendar_disparo_pipeline_neoenergia_elektro():
    try:
        if (LOCK_DIR / "pipeline_neoenergia_elektro.lock").exists():
            log.warning("[LAUNCHER] Pipeline NEOENERGIA ELEKTRO ja esta em execucao. Pulando novo disparo.")
            print("[LAUNCHER] Pipeline NEOENERGIA ELEKTRO ja esta em execucao. Pulando.")
            return
        _disparar_subprocesso_orquestrador(["--so-pipeline-neoenergia-elektro"], "pipeline_neoenergia_elektro")
    except Exception as e:
        log.error(f"[LAUNCHER] Falha ao disparar pipeline NEOENERGIA ELEKTRO: {e}")
        log.error(traceback.format_exc())
        print(f"[LAUNCHER] Falha ao disparar pipeline NEOENERGIA ELEKTRO: {e}")


def _agendar_disparo_pipeline_neoenergia_bahia():
    try:
        if (LOCK_DIR / "pipeline_neoenergia_bahia.lock").exists():
            log.warning("[LAUNCHER] Pipeline NEOENERGIA BAHIA ja esta em execucao. Pulando novo disparo.")
            print("[LAUNCHER] Pipeline NEOENERGIA BAHIA ja esta em execucao. Pulando.")
            return
        _disparar_subprocesso_orquestrador(["--so-pipeline-neoenergia-bahia"], "pipeline_neoenergia_bahia")
    except Exception as e:
        log.error(f"[LAUNCHER] Falha ao disparar pipeline NEOENERGIA BAHIA: {e}")
        log.error(traceback.format_exc())
        print(f"[LAUNCHER] Falha ao disparar pipeline NEOENERGIA BAHIA: {e}")


def _agendar_disparo_pipeline_neoenergia_pernambuco():
    try:
        if (LOCK_DIR / "pipeline_neoenergia_pernambuco.lock").exists():
            log.warning("[LAUNCHER] Pipeline NEOENERGIA PERNAMBUCO ja esta em execucao. Pulando novo disparo.")
            print("[LAUNCHER] Pipeline NEOENERGIA PERNAMBUCO ja esta em execucao. Pulando.")
            return
        _disparar_subprocesso_orquestrador(["--so-pipeline-neoenergia-pernambuco"], "pipeline_neoenergia_pernambuco")
    except Exception as e:
        log.error(f"[LAUNCHER] Falha ao disparar pipeline NEOENERGIA PERNAMBUCO: {e}")
        log.error(traceback.format_exc())
        print(f"[LAUNCHER] Falha ao disparar pipeline NEOENERGIA PERNAMBUCO: {e}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    freeze_support()

    import argparse
    parser = argparse.ArgumentParser(description="Orquestrador de faturas")
    parser.add_argument("--agora", action="store_true", help="Roda downloads + pipelines agora")
    parser.add_argument("--so-downloads", action="store_true", help="Roda só os downloaders agora")
    parser.add_argument("--so-pipeline", action="store_true", help="Roda só o pipeline CEMIG agora")
    parser.add_argument("--so-pipeline-enel", action="store_true", help="Roda só o pipeline ENEL agora")
    parser.add_argument("--so-pipeline-neoenergia-elektro", action="store_true", help="Roda só o pipeline NEOENERGIA ELEKTRO agora")
    parser.add_argument("--so-pipeline-neoenergia-bahia", action="store_true", help="Roda só o pipeline NEOENERGIA BAHIA agora")
    parser.add_argument("--so-pipeline-neoenergia-pernambuco", action="store_true", help="Roda só o pipeline NEOENERGIA PERNAMBUCO agora")
    parser.add_argument("--sem-teclado", action="store_true", help="Não ativa o listener interativo")
    parser.add_argument(
        "--filtro",
        default="TODOS",
        choices=list(FILTRO_MAPA.values()),
        help="Filtra a saída do terminal para um robô específico (ex: --filtro CEMIG)",
    )
    args = parser.parse_args()

    global filtro_exibicao
    filtro_exibicao = args.filtro

    if args.agora or args.so_downloads or args.so_pipeline or args.so_pipeline_enel or args.so_pipeline_neoenergia_elektro or args.so_pipeline_neoenergia_bahia or args.so_pipeline_neoenergia_pernambuco:
        print("=" * 60)
        print("LAUNCHER MANUAL")
        print("=" * 60)

        if args.agora or args.so_downloads:
            _executar_downloaders()

        if args.agora or args.so_pipeline:
            _executar_pipeline_cemig_agendado()

        if args.agora or args.so_pipeline_enel:
            _executar_pipeline_enel_agendado()

        if args.agora or args.so_pipeline_neoenergia_elektro:
            _executar_pipeline_neoenergia_elektro_agendado()

        if args.agora or args.so_pipeline_neoenergia_bahia:
            _executar_pipeline_neoenergia_bahia_agendado()

        if args.agora or args.so_pipeline_neoenergia_pernambuco:
            _executar_pipeline_neoenergia_pernambuco_agendado()

        return

    return


if __name__ == "__main__":
    main()


