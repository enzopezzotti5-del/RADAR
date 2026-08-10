#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cemig_selenium.py  —  V23
==========================
Download de faturas CEMIG via Selenium puro.

Fluxo por UC:
  1. Login manual (você resolve CAPTCHA uma vez)
  2. Lê planilha Senhas_CEMIG.xlsx → lista de (CNPJ, UC)
  3. Para cada UC:
       a. Seleciona CNPJ no <select id="ddCliente">
       b. Aguarda confirmação do CNPJ na tela
       c. Clica em "Trocar Unidade Consumidora" se necessário
       d. Digita UC dígito a dígito em <input id="limparInst">
       e. Clica em <span id="submitPesquisa">
       f. Clica em "Histórico de Contas"
       g. Aguarda tabela #tblGrid
       h. BS4 lê hdnData → lista de faturas com status
       i. Baixa PDF da fatura mais recente não paga
       j. Grava no indice_master + indice local CEMIG

Dependências:
    pip install selenium webdriver-manager pandas openpyxl beautifulsoup4 lxml
"""

from __future__ import annotations

import sys
import ctypes as _ctypes
from pathlib import Path
# Isola do CTRL_C_EVENT do Windows (evita KeyboardInterrupt em SSL/ChromeDriverManager)
if sys.platform == "win32":
    try:
        _ctypes.windll.kernel32.SetConsoleCtrlHandler(None, True)
    except Exception:
        pass
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import _venv_check  # noqa

import argparse
import csv
import inspect
import importlib.util
import json
import os
import re
import tempfile
import time
import traceback
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set, Tuple

import pandas as pd
from bs4 import BeautifulSoup
from urllib3.exceptions import MaxRetryError
from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait
from core.project_paths import resolve_indice_master_csv

try:
    from core.metrics.radar_metrics import emit_outcome as _emit_cemig_outcome
    def _emit(outcome: str, *, uc: str, mes_ref: str, carimbo: str = "") -> None:
        _emit_cemig_outcome(outcome, utility="CEMIG", account_id=uc,
                            competence=mes_ref, invoice_id=carimbo or mes_ref)
except Exception:
    def _emit(outcome: str, **_: str) -> None:  # type: ignore[misc]
        pass

try:
    import pdfplumber
    _PDFPLUMBER_OK = True
except ImportError:
    _PDFPLUMBER_OK = False

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

LOGIN_CEMIG  = "MT7000037579"
SENHA_CEMIG  = "BBCemig0101@"

ROOT_DIR     = Path("//10.10.250.21/Energia/ARQUIVOS ENZO")
CEMIG_DIR    = ROOT_DIR / "DOWNLOAD CEMIG"
INDEX_LOCAL  = CEMIG_DIR / "indice_faturas_cemig.csv"
DEBUG_DIR    = CEMIG_DIR / "_debug_cemig"
INDEX_LOCAL_FALLBACK = Path(__file__).resolve().parent / "indice_faturas_cemig_local_fallback.csv"

MASTER_PY_LOCAL = Path(__file__).resolve().parent.parent.parent.parent / "indice_master.py"  # raiz ENERGIA
MASTER_PY_SERVER = ROOT_DIR / "indice_master.py"
_COUNTER_FILE   = Path(r"\\10.10.250.21\Energia\ARQUIVOS ENZO\indice_master_next.txt")
MASTER_PY_ATIVO: Optional[Path] = None

BASE_URL        = "https://atende.cemig.com.br"
CAPTCHA_API_KEY = "3ea89b196b365e9db9d0fd245c628e4f"
RECAPTCHA_KEY   = "6Lel5yQTAAAAAL3DDXn2lm6J31ke4awM587E001a"
ANO_MINIMO      = 2026

# Tempos (segundos)
T_LOGIN      = 120   # espera máxima para login manual
T_EL         = 15    # WebDriverWait padrão
T_SPINNER    = 12    # aguardar spinner sumir
T_DOWNLOAD   = 60    # aguardar PDF na pasta temp

# Campos do índice local CEMIG
INDEX_FIELDS = ["INDICE", "UC", "MES_REF", "FATURA_ID",
                "DATA_DOWNLOAD", "STATUS", "CNPJ", "ARQUIVO"]


# =============================================================================
# DATACLASSES
# =============================================================================

@dataclass
class UnidadeConsumidora:
    cnpj_valor: str   # value da <option>, ex: "7000037579"
    cnpj_texto: str   # texto formatado, ex: "00.000.000/5435-60"
    cnpj_digitos: str # só dígitos, ex: "00000000543560"
    uc: str           # número da instalação, ex: "3009008558"


@dataclass
class Fatura:
    documento_impressao: str
    mes_ano: str            # "03/2026"
    mes_ref: str            # ""03-2026"  (formato master)
    pasta: str              # "03.2026"  (subpasta do mês)
    status: str             # "Pago", "Pendente", etc.
    vencimento: str
    valor: str
    url_pdf: str
    classificacao: str = "NAO_IDENTIFICADA"   # "BT" | "MT" | "NAO_IDENTIFICADA"


# =============================================================================
# LOGGING
# =============================================================================

def _mkdir_seguro(pasta):
    """mkdir tolerante ao WinError 1398 (diferenca de relogio com servidor UNC)."""
    try:
        pasta.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def _is_dir_unc(p) -> bool:
    """is_dir() tolerante ao WinError 1398 em caminhos UNC."""
    try:
        return p.is_dir()
    except OSError:
        return True


def _exists_unc(p) -> bool:
    """exists() tolerante ao WinError 1398 em caminhos UNC."""
    try:
        return p.exists()
    except OSError:
        return True



def _ler_arquivo_unc(caminho, encoding="utf-8-sig", timeout=10, tentativas=3):
    """
    Lê arquivo UNC com timeout. Evita travar indefinidamente
    quando o servidor está inacessível.
    Retorna conteúdo como string ou None se timeout/erro.
    """
    import threading

    ultimo_erro = None
    for tentativa in range(1, max(1, tentativas) + 1):
        resultado = [None]
        erro = [None]

        def _ler():
            try:
                with open(caminho, encoding=encoding, newline="") as f:
                    resultado[0] = f.read()
            except Exception as e:
                erro[0] = e

        t = threading.Thread(target=_ler, daemon=True)
        t.start()
        t.join(timeout=timeout)
        if t.is_alive():
            ultimo_erro = TimeoutError(f"timeout apos {timeout}s")
        elif erro[0]:
            ultimo_erro = erro[0]
        else:
            return resultado[0]

        if tentativa < tentativas:
            time.sleep(min(1.5, 0.5 * tentativa))

    return None, ultimo_erro


def _consumir_carimbo_fallback() -> str:
    """
    Quando o master não carrega, lê e incrementa indice_master_next.txt
    com filelock para evitar colisão com outros processos.
    Nunca usa IndiceLocal.proximo (que só conhece downloads CEMIG anteriores).
    """
    import csv as _csv
    try:
        from filelock import FileLock
        lock = FileLock(str(_COUNTER_FILE) + ".lock", timeout=30)
        with lock:
            txt = _COUNTER_FILE.read_text(encoding="utf-8").strip() if _COUNTER_FILE.exists() else "2000000"
            num = int(txt)
            # Garante que não colide com carimbos já registrados no master CSV.
            master_csv = resolve_indice_master_csv(prefer_network=False)
            try:
                max_n = 0
                with open(master_csv, newline="", encoding="utf-8-sig") as _f:
                    for row in _csv.DictReader(_f):
                        s = (row.get("INDICE") or "").strip()
                        if s.startswith("BB_"):
                            try:
                                max_n = max(max_n, int(s[3:]))
                            except ValueError:
                                pass
                num = max(num, max_n + 1)
            except Exception:
                pass
            _COUNTER_FILE.write_text(str(num + 1), encoding="utf-8")
            return f"BB_{num:07d}"
    except Exception as e:
        log(f"Fallback counter falhou ({e}) — abortando download para evitar carimbo duplicado", "ERR")
        raise RuntimeError("Counter file inacessível — não é seguro prosseguir sem master nem counter") from e


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")

def log(msg: str, level: str = "INFO") -> None:
    sym = {"INFO": "→", "OK": "✓", "ERR": "✗", "WARN": "⚠",
           "DBG": "◌", "DL": "📥", "FAT": "📄"}
    print(f"[{_ts()}] {sym.get(level,'•')} [{level}] {msg}")


# =============================================================================
# INDICE MASTER
# =============================================================================

def _carregar_master() -> Optional[object]:
    """
    Carrega MasterIndice a partir do modulo local da raiz do projeto.
    Usa filelock para acesso seguro entre processos concorrentes.
    """
    global MASTER_PY_ATIVO
    candidatos = [
        MASTER_PY_LOCAL,
        MASTER_PY_SERVER,
    ]
    for caminho in candidatos:
        if not _exists_unc(caminho):
            continue
        try:
            import threading as _thr
            _mod_result = [None]
            _mod_err = [None]

            def _carregar_mod():
                try:
                    spec = importlib.util.spec_from_file_location("indice_master", caminho)
                    m = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(m)
                    _mod_result[0] = m
                except Exception as e:
                    _mod_err[0] = e

            _t = _thr.Thread(target=_carregar_mod, daemon=True)
            _t.start()
            _t.join(timeout=30)
            if _t.is_alive() or _mod_result[0] is None:
                raise TimeoutError(f"Timeout ao carregar {caminho.name}")

            mod = _mod_result[0]

            if hasattr(mod, "_FILELOCK_OK") and not mod._FILELOCK_OK:
                log("filelock nao instalado - execute: pip install filelock", "WARN")
                log("Operando sem lock de arquivo entre processos.", "WARN")

            master_kwargs = {}
            try:
                assinatura = inspect.signature(mod.MasterIndice)
                if "scan_individual_indexes" in assinatura.parameters:
                    master_kwargs["scan_individual_indexes"] = False
            except (TypeError, ValueError):
                # Mantem compatibilidade com implementacoes antigas/atipicas.
                pass

            master = mod.MasterIndice(mod.MASTER_FILE, **master_kwargs)
            MASTER_PY_ATIVO = caminho
            log(
                f"Master: {len(master._ja_baixados)} registros | "
                f"proximo: {master.proximo_carimbo} | "
                f"lock: {'filelock' if getattr(mod, '_FILELOCK_OK', False) else 'desabilitado'} | "
                f"fonte: {caminho}",
                "OK",
            )
            return master
        except Exception as e:
            log(f"Falha ao carregar master ({caminho.name}): {e}", "WARN")

    log("indice_master.py nao encontrado - usando indice local apenas", "WARN")
    return None



# =============================================================================
# INDICE LOCAL CEMIG
# =============================================================================

class IndiceLocal:
    """Índice local de faturas CEMIG (compatibilidade + fallback sem master)."""

    def __init__(self):
        self.memoria: Set[Tuple[str, str]] = set()  # (uc, mes_ref)
        self.faturas_memoria: Set[Tuple[str, str]] = set()  # (mes_ref, fatura_id)
        self.proximo: int = 2000570                  # inicia após ENEL
        self.destino_gravacao = INDEX_LOCAL
        self._carregar()

    def _escrever_header(self, caminho: Path) -> None:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        with open(caminho, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerow(INDEX_FIELDS)

    def _carregar_de_conteudo(self, conteudo: str) -> None:
        import io
        for row in csv.DictReader(io.StringIO(conteudo)):
            uc  = row.get("UC", "").strip()
            ref = row.get("MES_REF", "").strip()
            fid = row.get("FATURA_ID", "").strip()
            if uc and ref:
                self.memoria.add((uc, ref))
            if ref and fid:
                self.faturas_memoria.add((ref, fid))
            m = re.search(r"(\d+)$", row.get("INDICE", ""))
            if m:
                self.proximo = max(self.proximo, int(m.group(1)) + 1)

    def _carregar(self) -> None:
        if not _exists_unc(INDEX_LOCAL):
            _mkdir_seguro(CEMIG_DIR)
            self._escrever_header(INDEX_LOCAL)
            self.destino_gravacao = INDEX_LOCAL
            return

        leitura = _ler_arquivo_unc(INDEX_LOCAL, timeout=8, tentativas=3)
        if isinstance(leitura, tuple):
            conteudo, erro = leitura
        else:
            conteudo, erro = leitura, None

        if conteudo is not None:
            self._carregar_de_conteudo(conteudo)
            try:
                INDEX_LOCAL_FALLBACK.parent.mkdir(parents=True, exist_ok=True)
                INDEX_LOCAL_FALLBACK.write_text(conteudo, encoding="utf-8-sig")
            except Exception:
                pass
            self.destino_gravacao = INDEX_LOCAL
            log(f"Índice local: {len(self.memoria)} registros, próximo={self.proximo}", "OK")
            return

        detalhe = f": {erro}" if erro else ""
        if INDEX_LOCAL_FALLBACK.exists():
            try:
                conteudo_fb = INDEX_LOCAL_FALLBACK.read_text(encoding="utf-8-sig")
                self._carregar_de_conteudo(conteudo_fb)
                self.destino_gravacao = INDEX_LOCAL_FALLBACK
                log(
                    f"Índice local do servidor inacessível{detalhe}. "
                    f"Usando cache local {INDEX_LOCAL_FALLBACK}",
                    "WARN",
                )
                log(f"Índice local (cache): {len(self.memoria)} registros, próximo={self.proximo}", "OK")
                return
            except Exception as fb_err:
                detalhe += f" | fallback local falhou: {fb_err}"

        self.destino_gravacao = INDEX_LOCAL_FALLBACK
        self._escrever_header(INDEX_LOCAL_FALLBACK)
        log(f"Índice local inacessível — iniciando fallback vazio{detalhe}", "WARN")

    def ja_baixado(self, uc: str, mes_ref: str) -> bool:
        return (uc, mes_ref) in self.memoria

    def ja_baixado_por_fatura(self, mes_ref: str, fatura_id: str) -> bool:
        return (mes_ref, fatura_id) in self.faturas_memoria

    def gravar(self, indice_bb: str, uc: str, mes_ref: str,
               fatura_id: str, cnpj: str, arquivo: str) -> None:
        self.destino_gravacao.parent.mkdir(parents=True, exist_ok=True)
        with open(self.destino_gravacao, "a", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerow([
                indice_bb, uc, mes_ref, fatura_id,
                datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                "Pendente", cnpj, arquivo,
            ])
        self.memoria.add((uc, mes_ref))
        if mes_ref and fatura_id:
            self.faturas_memoria.add((mes_ref, fatura_id))
        m = re.search(r"(\d+)$", indice_bb)
        if m:
            self.proximo = max(self.proximo, int(m.group(1)) + 1)


# =============================================================================
# SELENIUM — DRIVER
# =============================================================================

def _find_cached_chromedriver() -> str | None:
    """Encontra chromedriver compativel no cache do Selenium sem precisar de rede."""
    import subprocess as _sp
    from pathlib import Path as _P

    def _versao_tuple(raw: str) -> tuple[int, ...]:
        try:
            return tuple(int(p) for p in raw.split("."))
        except Exception:
            return (0,)

    cache = _P.home() / ".cache" / "selenium" / "chromedriver" / "win64"
    if not cache.exists():
        return None
    try:
        r = _sp.run(
            ["powershell", "-c",
             "(gi 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe').VersionInfo.ProductVersion"],
            capture_output=True, text=True, timeout=5, creationflags=0x08000000)
        chrome_ver = r.stdout.strip()
        major = chrome_ver.split(".")[0]
    except Exception:
        chrome_ver = ""
        major = None

    candidatos = sorted(cache.iterdir(), key=lambda p: _versao_tuple(p.name), reverse=True)
    if chrome_ver:
        for p in candidatos:
            exe = p / "chromedriver.exe"
            if exe.exists() and p.name == chrome_ver:
                return str(exe)
    for p in candidatos:
        exe = p / "chromedriver.exe"
        if exe.exists() and (not major or p.name.startswith(major + ".")):
            return str(exe)
    return None


class CemigSeleniumSessionEncerrada(RuntimeError):
    """Falha operacional curta para o Radar quando o ChromeDriver desaparece."""


def _sessao_encerrada(exc: BaseException) -> bool:
    texto = str(exc).lower()
    return isinstance(exc, (WebDriverException, MaxRetryError, ConnectionRefusedError)) or any(token in texto for token in (
        "maxretryerror", "connection refused", "failed to establish a new connection",
        "invalid session id", "chrome not reachable",
    ))


def _erro_sessao_encerrada(driver, etapa: str, exc: BaseException, tentativa: int = 1) -> CemigSeleniumSessionEncerrada:
    url = "indisponivel"
    try:
        url = driver.current_url or url
    except Exception:
        pass
    return CemigSeleniumSessionEncerrada(
        "CEMIG_SELENIUM_SESSION_ENCERRADA: "
        f"etapa={etapa}; tentativa={tentativa}; url={url}; "
        f"ChromeDriver deixou de responder ({type(exc).__name__}: {exc})"
    )


def _page_source_seguro(driver, etapa: str, tentativa: int = 1) -> str:
    try:
        return driver.page_source
    except Exception as exc:
        if _sessao_encerrada(exc):
            raise _erro_sessao_encerrada(driver, etapa, exc, tentativa) from exc
        raise


def encerrar_driver_seguro(driver) -> None:
    """Encerra uma sessao no maximo uma vez, sem esconder a excecao em curso."""
    if driver is None or getattr(driver, "_cemig_encerrado", False):
        return
    try:
        driver.quit()
    except Exception as exc:
        log(f"Falha ao encerrar ChromeDriver (ignorada): {exc}", "WARN")
    finally:
        try:
            setattr(driver, "_cemig_encerrado", True)
        except Exception:
            pass


def build_driver() -> webdriver.Chrome:
    log("Preparando ChromeDriver CEMIG...", "INFO")
    opts = Options()
    temp_dl = CEMIG_DIR / "_temp"
    _mkdir_seguro(temp_dl)
    profiles_root = Path(tempfile.gettempdir()) / "energia_chrome_profiles" / "cemig"
    profiles_root.mkdir(parents=True, exist_ok=True)
    profile_dir = Path(tempfile.mkdtemp(prefix="cemig_", dir=str(profiles_root)))
    log(f"Perfil Chrome temporario: {profile_dir}", "DBG")

    opts.add_experimental_option("prefs", {
        "download.default_directory":        str(temp_dl.resolve()),
        "download.prompt_for_download":       False,
        "download.directory_upgrade":         True,
        "plugins.always_open_pdf_externally": True,
    })
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--no-proxy-server")
    opts.add_argument("--remote-debugging-port=0")
    opts.add_argument(f"--user-data-dir={profile_dir}")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    _cd = _find_cached_chromedriver()
    if _cd:
        log(f"ChromeDriver em uso: {_cd}", "DBG")
        driver = webdriver.Chrome(service=Service(_cd), options=opts)
    else:
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            import os as _os
            _proxy_keys = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
            _proxy_bak = {k: _os.environ.pop(k, None) for k in _proxy_keys}
            try:
                driver = webdriver.Chrome(
                    service=Service(ChromeDriverManager().install()), options=opts)
            finally:
                for k, v in _proxy_bak.items():
                    if v is not None:
                        _os.environ[k] = v
        except BaseException:
            driver = webdriver.Chrome(options=opts)

    driver.set_page_load_timeout(30)
    log("ChromeDriver pronto.", "OK")
    return driver


def inicializar_driver_com_retry() -> webdriver.Chrome:
    """Uma unica repeticao, restrita a criacao do navegador sem efeitos no portal."""
    ultimo_erro = None
    for tentativa in (1, 2):
        try:
            log(f"Inicializando ChromeDriver (tentativa {tentativa}/2)", "INFO")
            return build_driver()
        except Exception as exc:
            ultimo_erro = exc
            log(f"Falha ao iniciar ChromeDriver (tentativa {tentativa}/2): {exc}", "WARN")
            if tentativa == 1:
                time.sleep(2)
    raise RuntimeError("CEMIG_FALHA_INICIALIZAR_NAVEGADOR apos uma retentativa segura") from ultimo_erro


# =============================================================================
# HELPERS
# =============================================================================

def W(driver, by, sel, t=T_EL):
    """WebDriverWait simplificado."""
    return WebDriverWait(driver, t).until(
        EC.element_to_be_clickable((by, sel))
    )


def clicar(driver, el, label="") -> bool:
    """Clique com scrollIntoView + fallback JS."""
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        try:
            el.click()
        except ElementClickInterceptedException:
            driver.execute_script("arguments[0].click();", el)
        return True
    except Exception as e:
        log(f"Clique falhou [{label}]: {e}", "DBG")
        return False


def spinner(driver, timeout=T_SPINNER) -> None:
    """Aguarda spinners/overlays sumirem."""
    sels = [".ajax-loader", ".loading-overlay", "#overlay",
            ".blockUI", "#divCarregando", "[class*='loading']"]
    prazo = time.time() + timeout
    while time.time() < prazo:
        if not any(
            e.is_displayed()
            for s in sels
            for e in driver.find_elements(By.CSS_SELECTOR, s)
        ):
            return
        time.sleep(0.25)


def salvar_debug(driver, nome: str) -> None:
    _mkdir_seguro(DEBUG_DIR)
    path = DEBUG_DIR / f"{datetime.now().strftime('%H%M%S')}_{nome}.html"
    path.write_text(_page_source_seguro(driver, f"salvar_debug:{nome}"), encoding="utf-8", errors="replace")
    log(f"Debug → {path.name}", "DBG")


def aguardar_pdf(pasta: Path, antes: set, timeout=T_DOWNLOAD) -> Optional[Path]:
    """Aguarda novo PDF aparecer na pasta."""
    prazo = time.time() + timeout
    while time.time() < prazo:
        novos = {p.name for p in pasta.glob("*.pdf")} - antes
        if novos:
            p = pasta / sorted(novos)[-1]
            time.sleep(1.0)
            if p.exists() and p.stat().st_size > 1024:
                return p
        time.sleep(0.5)
    return None


# =============================================================================
# PASSO 1 — LOGIN
# =============================================================================

def aceitar_cookies_cemig(driver):
    """
    Espera até 5 segundos pelo botão do OneTrust e clica nele.
    Se não aparecer, ignora e segue o fluxo silenciosamente.
    """
    print("[INFO] Verificando banner de cookies...")
    try:
        # Espera no máximo 5 segundos para o botão aparecer e ficar clicável
        wait = WebDriverWait(driver, 5)
        botao_cookies = wait.until(EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler")))

        # Clica via JavaScript (é mais seguro pois o banner costuma ficar "flutuando" na tela)
        driver.execute_script("arguments[0].click();", botao_cookies)
        print("[INFO] Botão 'Aceitar cookies' clicado com sucesso.")

    except Exception:
        # Se der timeout ou erro, significa que o banner não apareceu.
        print("[INFO] Banner de cookies não apareceu. Seguindo o fluxo...")
        pass

def _resolver_captcha_2captcha() -> Optional[str]:
    """
    Resolve o reCAPTCHA v2 via 2captcha API.
    Retorna o token g-recaptcha-response ou None se falhar.
    """
    import urllib.request as _req
    import json as _json

    log("Enviando reCAPTCHA para 2captcha...", "DBG")
    try:
        # Cria tarefa
        payload = _json.dumps({
            "clientKey": CAPTCHA_API_KEY,
            "task": {
                "type":       "RecaptchaV2TaskProxyless",
                "websiteURL": f"{BASE_URL}/Login/Index",
                "websiteKey": RECAPTCHA_KEY,
            }
        }).encode()
        req  = _req.Request("https://api.2captcha.com/createTask",
                            data=payload,
                            headers={"Content-Type": "application/json"})
        res  = _json.loads(_req.urlopen(req, timeout=30).read())
        if res.get("errorId") != 0:
            log(f"2captcha createTask erro: {res}", "WARN")
            return None
        tid = res["taskId"]
        log(f"2captcha taskId={tid} — aguardando solução...", "DBG")

        # Aguarda resultado (máx 120s)
        payload_get = _json.dumps({
            "clientKey": CAPTCHA_API_KEY,
            "taskId":    tid,
        }).encode()
        for _ in range(40):
            time.sleep(3)
            req2 = _req.Request("https://api.2captcha.com/getTaskResult",
                                data=payload_get,
                                headers={"Content-Type": "application/json"})
            res2 = _json.loads(_req.urlopen(req2, timeout=30).read())
            if res2.get("status") == "ready":
                token = res2.get("solution", {}).get("gRecaptchaResponse", "")
                log("reCAPTCHA resolvido pelo 2captcha", "OK")
                return token
        log("Timeout 2captcha — sem solução em 120s", "WARN")
        return None
    except Exception as e:
        log(f"Erro ao chamar 2captcha: {e}", "WARN")
        return None


def _injetar_captcha(driver, token: str) -> None:
    """Injeta o token do captcha no campo oculto e dispara o callback."""
    driver.execute_script(f"""
        document.getElementById('g-recaptcha-response').innerHTML = '{token}';
        try {{
            var cb = document.querySelector('[data-callback]');
            if (cb) {{
                var fn = cb.getAttribute('data-callback');
                if (fn && window[fn]) window[fn]('{token}');
            }}
        }} catch(e) {{}}
        try {{
            if (window.grecaptcha && window.grecaptcha.getResponse) {{}}
        }} catch(e) {{}}
    """)


def _credenciais_preservadas(campo_usuario, campo_senha, usuario: str, senha: str) -> bool:
    """Confere o DOM sem registrar segredos; CAPTCHA pode rerenderizar o form."""
    usuario_atual = campo_usuario.get_attribute("value") or ""
    senha_atual = campo_senha.get_attribute("value") or ""
    ok = usuario_atual == usuario and senha_atual == senha
    log(
        "Credenciais pré-submit: "
        f"usuario_presente={bool(usuario_atual)} usuario_len={len(usuario_atual)} "
        f"senha_presente={bool(senha_atual)} senha_len={len(senha_atual)}",
        "DBG",
    )
    return ok


def _resultado_login(driver) -> str | None:
    """Classifica somente evidência apresentada pelo portal, nunca o clique."""
    url = driver.current_url or ""
    url_lower = url.lower()
    if "/home" in url_lower or "/entrarpoderpublico" in url_lower or "selecionar" in url_lower:
        return "LOGIN_OK"
    mensagens = driver.find_elements(
        By.CSS_SELECTOR,
        ".validation-summary-errors, .field-validation-error, .alert-danger, .alert-warning, .text-danger, [role='alert']",
    )
    texto = " ".join((m.text or "").strip() for m in mensagens if m.is_displayed()).lower()
    if "captcha" in texto or "recaptcha" in texto:
        return "CEMIG_CAPTCHA_NAO_ACEITO"
    if texto:
        return "LOGIN_REJEITADO"
    return None


def _diagnosticar_login_nao_confirmado(driver, codigo: str) -> None:
    log(f"{codigo}: url={driver.current_url} formulario_presente={bool(driver.find_elements(By.CSS_SELECTOR, 'form'))}", "ERR")
    try:
        salvar_debug(driver, codigo.lower())
    except Exception:
        pass


def fazer_login(driver, usuario: str, senha: str) -> bool:
    log("Abrindo portal CEMIG...")
    driver.get(f"{BASE_URL}/Login/Index")
    time.sleep(1.5)

    aceitar_cookies_cemig(driver)   # <-- ADICIONE ESTA LINHA AQUI
    time.sleep(0.8)

    # Preenche usuário
    try:
        campo = W(driver, By.CSS_SELECTOR,
                  "#userId, input[name='userId'], #Acesso, input[name='Acesso']")
        campo.clear()
        campo.send_keys(usuario)
    except TimeoutException:
        salvar_debug(driver, "erro_campo_usuario")
        log("Campo de usuário não encontrado", "ERR")
        return False

    # Preenche senha
    try:
        campo_s = W(driver, By.CSS_SELECTOR, "input[type='password']", 10)
        campo_s.clear()
        campo_s.send_keys(senha)
    except TimeoutException:
        salvar_debug(driver, "erro_campo_senha")
        log("Campo de senha não encontrado", "ERR")
        return False

    # Resolve reCAPTCHA automaticamente via 2captcha
    token = _resolver_captcha_2captcha()
    if token:
        _injetar_captcha(driver, token)
        time.sleep(0.5)
        try:
            campo = W(driver, By.CSS_SELECTOR,
                      "#userId, input[name='userId'], #Acesso, input[name='Acesso']")
            campo_s = W(driver, By.CSS_SELECTOR, "input[type='password']", 10)
        except (TimeoutException, StaleElementReferenceException):
            _diagnosticar_login_nao_confirmado(driver, "CEMIG_LOGIN_NAO_CONFIRMADO")
            return False
        if not _credenciais_preservadas(campo, campo_s, usuario, senha):
            _diagnosticar_login_nao_confirmado(driver, "CEMIG_LOGIN_NAO_CONFIRMADO")
            log("Formulário foi recarregado/rerenderizado antes do submit; login não acionado.", "ERR")
            return False
        # Clica em Entrar automaticamente
        for by, sel in [
            (By.CSS_SELECTOR, "button[type='submit']"),
            (By.XPATH,        "//button[contains(normalize-space(.), 'Entrar')]"),
            (By.XPATH,        "//input[@type='submit']"),
        ]:
            try:
                btn = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((by, sel)))
                clicar(driver, btn, "btn_entrar")
                log("Botão Entrar acionado — aguardando confirmação do portal.", "DBG")
                break
            except Exception:
                continue
    else:
        log("─" * 60)
        log("2captcha falhou — resolva o CAPTCHA manualmente e clique Entrar.", "WARN")
        log("─" * 60)

    limite = time.monotonic() + T_LOGIN
    resultado = None
    while time.monotonic() < limite:
        resultado = _resultado_login(driver)
        if resultado:
            break
        time.sleep(0.25)
    if resultado != "LOGIN_OK":
        codigo = resultado or "CEMIG_LOGIN_NAO_CONFIRMADO"
        _diagnosticar_login_nao_confirmado(driver, codigo)
        return False

    spinner(driver)
    log(f"Login OK — {driver.current_url}", "OK")
    return True


# =============================================================================
# PASSO 2 — SELECIONAR CNPJ
# =============================================================================

def selecionar_cnpj(driver, cnpj_valor: str, cnpj_texto: str) -> bool:
    """
    Seleciona o CNPJ no <select id="ddCliente"> e aguarda confirmação
    visual na tela antes de prosseguir.
    """
    log(f"Selecionando CNPJ: {cnpj_texto} (value={cnpj_valor})")
    try:
        el  = W(driver, By.ID, "ddCliente")
        sel = Select(el)
        sel.select_by_value(cnpj_valor)
    except (TimeoutException, NoSuchElementException):
        salvar_debug(driver, f"erro_select_cnpj_{cnpj_valor}")
        log("Select #ddCliente não encontrado", "ERR")
        return False
    except Exception:
        # Tenta por texto parcial como fallback
        try:
            sel = Select(driver.find_element(By.ID, "ddCliente"))
            for opt in sel.options:
                if cnpj_valor in (opt.get_attribute("value") or ""):
                    sel.select_by_value(opt.get_attribute("value"))
                    break
        except Exception as e:
            log(f"Falha ao selecionar CNPJ: {e}", "ERR")
            return False

    # Aguarda confirmação visual: o CNPJ formatado deve aparecer na tela
    cnpj_parte = cnpj_texto.split("/")[-1].strip() if "/" in cnpj_texto else cnpj_texto[-6:]
    try:
        WebDriverWait(driver, 8).until(
            lambda d: cnpj_parte in _page_source_seguro(d, "confirmar_cnpj")
        )
        log(f"CNPJ confirmado na tela ({cnpj_parte})", "OK")
    except TimeoutException:
        log("CNPJ não confirmado visualmente — continuando mesmo assim", "WARN")

    # Aguarda o DOM estabilizar após troca de CNPJ.
    # O portal reconstrói o campo #limparInst — sem essa espera o elemento
    # fica stale logo em seguida quando digitar_uc_e_pesquisar tentar usá-lo.
    try:
        WebDriverWait(driver, 6).until(
            EC.presence_of_element_located((By.ID, "limparInst"))
        )
        time.sleep(0.4)   # pequena pausa extra para o JS do portal terminar
    except TimeoutException:
        time.sleep(1.0)   # fallback se o campo demorar mais

    spinner(driver, 8)
    return True


# =============================================================================
# PASSO 3 — TROCAR UNIDADE CONSUMIDORA (quando já há UC ativa)
# =============================================================================

def clicar_trocar_uc(driver) -> bool:
    """
    Clica em "Trocar Unidade Consumidora" para revelar o #ddCliente e #limparInst.

    Fluxo confirmado:
      - Home mostra UC atual com dropdown
      - Precisa clicar "Trocar Unidade Consumidora" ANTES de mudar CNPJ ou UC
      - Após clicar, o #ddCliente e #limparInst ficam disponíveis
      - Se não houver UC ativa (campo já visível), retorna True sem fazer nada
    """
    # Se o campo de UC já está disponível, não precisa trocar
    try:
        campo = driver.find_element(By.ID, "limparInst")
        if campo.is_displayed() and campo.is_enabled():
            log("Campo UC já visível — sem necessidade de trocar", "DBG")
            return True
    except Exception:
        pass

    # Tenta clicar direto no botão (pode já estar visível)
    try:
        btn = WebDriverWait(driver, 4).until(
            EC.element_to_be_clickable((By.ID, "btnSelecionarOutraIN"))
        )
        clicar(driver, btn, "btnSelecionarOutraIN")
        log("'Trocar Unidade Consumidora' clicado", "DBG")
        time.sleep(0.6)
        spinner(driver, 8)
        return True
    except TimeoutException:
        pass

    # Botão não visível — clica na UC atual para abrir o dropdown
    seletores_uc_atual = [
        (By.XPATH, "//*[contains(text(),'Nº') and contains(text(),'/')]"),
        (By.CSS_SELECTOR, ".uc-selecionada, #ucAtual, .instalacao-numero"),
        (By.XPATH, "//div[contains(@class,'instalacao')]"),
        (By.XPATH, "//span[contains(@class,'uc')]"),
    ]
    for by, sel in seletores_uc_atual:
        try:
            el = WebDriverWait(driver, 2).until(EC.element_to_be_clickable((by, sel)))
            clicar(driver, el, f"uc_atual ({sel})")
            time.sleep(0.4)
            btn = WebDriverWait(driver, 4).until(
                EC.element_to_be_clickable((By.ID, "btnSelecionarOutraIN"))
            )
            clicar(driver, btn, "btnSelecionarOutraIN")
            log("'Trocar UC' clicado via dropdown", "DBG")
            time.sleep(0.6)
            spinner(driver, 8)
            return True
        except Exception:
            continue

    # Último recurso: navega para home e tenta de novo
    try:
        driver.get(f"{BASE_URL}/Home/Index/")
        spinner(driver, 8)
        btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.ID, "btnSelecionarOutraIN"))
        )
        clicar(driver, btn, "btnSelecionarOutraIN após reload")
        time.sleep(0.6)
        spinner(driver, 8)
        return True
    except Exception:
        pass

    log("Botão 'Trocar UC' não encontrado — continuando mesmo assim", "WARN")
    return False


# =============================================================================
# PASSO 4 — DIGITAR UC E PESQUISAR
# =============================================================================

def _get_campo_uc(driver):
    """
    Re-busca #limparInst a cada chamada — evita StaleElementReferenceException
    que ocorre quando o DOM reconstruiu após troca de CNPJ.
    """
    for by, sel in [(By.ID, "limparInst"), (By.NAME, "NumeroInstalacao")]:
        try:
            return W(driver, by, sel, 8)
        except TimeoutException:
            continue
    return None


def digitar_uc_e_pesquisar(driver, uc: str) -> bool:
    """
    Digita o número da UC dígito a dígito em <input id="limparInst">
    (respeita onkeyup="mascaraUC()") e clica em <span id="submitPesquisa">.

    Re-busca o elemento antes de cada operação para evitar StaleElementReferenceException
    que acontece quando o DOM reconstruiu após seleção de CNPJ.
    """
    log(f"Digitando UC: {uc}")
    digitos = re.sub(r"\D", "", uc)

    # Re-busca campo (nunca guarda referência entre operações)
    campo = _get_campo_uc(driver)
    if not campo:
        salvar_debug(driver, f"erro_campo_uc_{uc}")
        log("Campo #limparInst não encontrado", "ERR")
        return False

    # Limpa via JS primeiro (não precisa do elemento para isso)
    try:
        driver.execute_script(
            "var c = document.getElementById('limparInst') || "
            "document.querySelector('[name=NumeroInstalacao]'); "
            "if(c) c.value = '';")
    except Exception:
        pass

    # Re-busca após JS (DOM pode ter reagido)
    campo = _get_campo_uc(driver)
    if not campo:
        log("Campo sumiu após limpeza JS", "ERR")
        return False

    # Clica e confirma foco — re-busca se stale
    for tentativa in range(3):
        try:
            campo.click()
            break
        except StaleElementReferenceException:
            time.sleep(0.3)
            campo = _get_campo_uc(driver)
            if not campo:
                return False

    time.sleep(0.15)

    # Digita dígito a dígito — re-busca se stale entre dígitos
    for ch in digitos:
        for tentativa in range(3):
            try:
                campo.send_keys(ch)
                break
            except StaleElementReferenceException:
                time.sleep(0.2)
                campo = _get_campo_uc(driver)
                if not campo:
                    log("Campo sumiu durante digitação", "ERR")
                    return False
        time.sleep(0.06)

    time.sleep(0.4)
    log(f"UC digitada: {digitos}", "DBG")

    # Clica em submitPesquisa — re-busca campo para Enter como fallback
    for by, sel in [
        (By.ID,           "submitPesquisa"),
        (By.CSS_SELECTOR, "#submitPesquisa"),
        (By.XPATH,        "//span[@id='submitPesquisa']"),
        (By.CSS_SELECTOR, "button[onclick*='Pesquisar']"),
        (By.XPATH,        "//button[normalize-space(.)='Pesquisar']"),
        (By.XPATH,        "//button[@type='submit']"),
    ]:
        try:
            btn = WebDriverWait(driver, 4).until(EC.element_to_be_clickable((by, sel)))
            clicar(driver, btn, "submitPesquisa")
            time.sleep(1.0)
            spinner(driver, T_SPINNER)
            return True
        except Exception:
            continue

    # Fallback: Enter no campo (re-busca para evitar stale)
    campo = _get_campo_uc(driver)
    if campo:
        try:
            campo.send_keys(Keys.RETURN)
        except StaleElementReferenceException:
            pass
    time.sleep(1.0)
    spinner(driver, T_SPINNER)
    log("Pesquisa via Enter (fallback)", "DBG")
    return True


# =============================================================================
# PASSO 5 — CLICAR EM HISTÓRICO DE CONTAS
# =============================================================================

def clicar_historico(driver) -> bool:
    """
    Clica no card "Histórico de Contas".
    Seletor confirmado: <h4 class="name" data-index="title">Histórico de Contas</h4>
    """
    log("Abrindo Histórico de Contas...")
    for by, sel in [
        (By.XPATH, "//h4[@data-index='title' and normalize-space(text())='Histórico de Contas']"),
        (By.XPATH, "//*[normalize-space(text())='Histórico de Contas']"),
        (By.XPATH, "//a[contains(normalize-space(.),'Histórico')]"),
        (By.CSS_SELECTOR, "a[href*='Historico']"),
    ]:
        try:
            el = WebDriverWait(driver, T_EL).until(EC.element_to_be_clickable((by, sel)))
            clicar(driver, el, "historico")
            time.sleep(1.0)
            spinner(driver, T_SPINNER)
            return True
        except Exception:
            continue

    salvar_debug(driver, "erro_historico")
    log("'Histórico de Contas' não encontrado", "ERR")
    return False


# =============================================================================
# PASSO 6 — LER FATURAS COM BS4
# =============================================================================

def ler_faturas(driver, uc: str) -> List[Fatura]:
    """
    Lê faturas da tabela #tblGrid usando BeautifulSoup.
    Estratégia 1: <input id="hdnData"> (JSON completo, sem paginação).
    Estratégia 2: linhas <tr class="grid-item"> + paginação via #tblGrid_next.
    """
    # Aguarda tabela
    try:
        WebDriverWait(driver, T_EL).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#tblGrid tbody tr"))
        )
        time.sleep(0.3)
    except TimeoutException:
        salvar_debug(driver, f"tabela_nao_carregou_{uc}")
        log(f"Tabela não carregou para UC {uc}", "WARN")
        return []

    faturas: List[Fatura] = []
    pagina  = 1

    while True:
        soup = BeautifulSoup(_page_source_seguro(driver, "ler_faturas"), "lxml")

        # ── Estratégia 1: hdnData (JSON) ────────────────────────────────────
        hdn = soup.find("input", {"id": "hdnData"})
        if hdn and hdn.get("value"):
            try:
                dados = json.loads(hdn["value"])
                for item in dados:
                    fat = _parsear_item_json(item)
                    if fat:
                        faturas.append(fat)
                break  # hdnData tem tudo — não paginar
            except json.JSONDecodeError:
                pass  # cai para estratégia 2

        # ── Estratégia 2: linhas HTML ────────────────────────────────────────
        linhas = soup.select("tr.grid-item")
        if not linhas:
            salvar_debug(driver, f"sem_linhas_{uc}_p{pagina}")
            break

        for tr in linhas:
            fat = _parsear_linha_html(tr)
            if fat:
                faturas.append(fat)

        # Próxima página
        try:
            btn_next = driver.find_element(By.ID, "tblGrid_next")
            if "disabled" in (btn_next.get_attribute("class") or ""):
                break
            driver.execute_script("arguments[0].click();", btn_next)
            time.sleep(0.8)
            pagina += 1
        except NoSuchElementException:
            break

    return faturas


def _parsear_item_json(item: dict) -> Optional[Fatura]:
    mes_ano = item.get("MesAno", "")          # "03/2026"
    doc     = item.get("DocumentoImpressao", "")
    status  = item.get("Status", "")
    venc    = item.get("DataVencimento", "")
    valor   = item.get("ValorFormatado", str(item.get("ValorTotal", "")))

    if not mes_ano or not doc:
        return None
    try:
        m, a = mes_ano.split("/")
        if int(a) < ANO_MINIMO:
            return None
        return Fatura(
            documento_impressao=doc,
            mes_ano=mes_ano,
            mes_ref=f"{m}-{a}",
            pasta=f"{m}.{a}",   # ex: "03.2026"
            status=status,
            vencimento=venc,
            valor=str(valor),
            url_pdf=(f"{BASE_URL}/ComponenteConsultarDebitos/BaixarPDF"
                     f"?documentoImpressao={urllib.parse.quote(doc)}"
                     f"&servico=HistoricoConta"),
        )
    except Exception:
        return None


def _parsear_linha_html(tr) -> Optional[Fatura]:
    tds = tr.find_all("td")
    if len(tds) < 5:
        return None

    mes_ano = tds[0].get_text(strip=True)   # "03/2026"
    valor   = tds[2].get_text(strip=True)
    venc    = tds[3].get_text(strip=True)
    status  = tds[4].get_text(strip=True)

    btn = tr.select_one("a[onclick*='BaixarPDF']")
    doc, url_pdf = "", ""
    if btn:
        m = re.search(r"BaixarPDF\('([^']+)'", btn.get("onclick", ""))
        if m:
            url_pdf = BASE_URL + m.group(1).replace("&amp;", "&")
            md = re.search(r"documentoImpressao=([^&]+)", url_pdf)
            doc = urllib.parse.unquote(md.group(1)) if md else ""

    if not mes_ano:
        return None
    try:
        m_str, a_str = mes_ano.split("/")
        if int(a_str) < ANO_MINIMO:
            return None
        return Fatura(
            documento_impressao=doc,
            mes_ano=mes_ano,
            mes_ref=f"{m_str}-{a_str}",
            pasta=f"{m_str}.{a_str}",   # ex: "03.2026"
            status=status,
            vencimento=venc,
            valor=valor,
            url_pdf=url_pdf,
        )
    except Exception:
        return None


def logar_faturas(faturas: List[Fatura], uc: str) -> None:
    if not faturas:
        log(f"  Sem faturas para UC {uc}", "WARN")
        return
    nao_pagas = sum(1 for f in faturas if "pag" not in f.status.lower())
    log(f"  UC {uc} — {len(faturas)} fatura(s) | {nao_pagas} pendente(s):", "FAT")
    for f in faturas:
        icone = "✓" if "pag" in f.status.lower() else "⚠"
        log(f"    {icone} {f.mes_ano:8s} | {f.status:12s} | "
            f"Venc {f.vencimento:12s} | {f.valor}", "FAT")


# =============================================================================
# CLASSIFICAÇÃO BT / MT
# =============================================================================

_PADROES_MT = [
    r"\bA4\b", r"\bA3\b", r"\bA3A\b", r"\bAS\b",
    r"M[ÉE]DIA\s*TENS[ÃA]O", r"GRUPO\s*A", r"SUBGRUPO\s*A",
    r"TARIFA\s*A", r"MÉDIA TENSÃO", r"MEDIA TENSAO",
]
_PADROES_BT = [
    r"\bB1\b", r"\bB2\b", r"\bB3\b", r"\bB4\b",
    r"BAIXA\s*TENS[ÃA]O", r"GRUPO\s*B", r"SUBGRUPO\s*B",
    r"TARIFA\s*B",
]


def classificar_pdf(pdf_path: Path) -> str:
    """
    Classifica o PDF como BT, MT ou NAO_IDENTIFICADA lendo as primeiras 2 páginas.
    Requer pdfplumber (pip install pdfplumber).
    """
    if not _PDFPLUMBER_OK:
        return "NAO_IDENTIFICADA"
    try:
        texto = ""
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages[:2]:
                texto += (page.extract_text() or "").upper() + "\n"
        if not texto.strip():
            return "NAO_IDENTIFICADA"
        for padrao in _PADROES_MT:
            if re.search(padrao, texto, re.IGNORECASE):
                return "MT"
        for padrao in _PADROES_BT:
            if re.search(padrao, texto, re.IGNORECASE):
                return "BT"
        return "NAO_IDENTIFICADA"
    except Exception:
        return "NAO_IDENTIFICADA"


# =============================================================================
# PASSO 7 — BAIXAR PDF
# =============================================================================

def baixar_pdf(driver, fat: Fatura, uc: str, indice_bb: str) -> Optional[Path]:
    """Clica no botão 'Baixar PDF' da linha correta e aguarda o arquivo."""
    log(f"  Baixando {fat.mes_ano} ({fat.status})")

    temp_dir = CEMIG_DIR / "_temp"
    _mkdir_seguro(temp_dir)
    antes = {p.name for p in temp_dir.glob("*.pdf")}

    # Clica no botão da linha correta pelo documentoImpressao
    doc_q = urllib.parse.quote(fat.documento_impressao)
    clicou = False
    for xp in [
        f"//a[contains(@onclick,'{doc_q}')][.//span[normalize-space()='Baixar PDF']]",
        f"//td[normalize-space()='{fat.mes_ano}']/following-sibling::td"
        f"//a[contains(@onclick,'BaixarPDF')]",
        f"//a[contains(@onclick,'BaixarPDF')][contains(@onclick,'{fat.documento_impressao[:15]}')]",
    ]:
        try:
            el = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, xp)))
            clicar(driver, el, "btn_baixar_pdf")
            clicou = True
            break
        except Exception:
            continue

    # Fallback: URL direta
    if not clicou and fat.url_pdf:
        driver.get(fat.url_pdf)
        clicou = True

    if not clicou:
        log(f"  Botão de download não encontrado para {fat.mes_ano}", "WARN")
        return None

    pdf = aguardar_pdf(temp_dir, antes)
    if not pdf:
        log(f"  Timeout — PDF não chegou para {fat.mes_ano}", "WARN")
        return None

    # Classificar BT/MT antes de mover
    classificacao = classificar_pdf(pdf)
    fat.classificacao = classificacao

    # Estrutura: DOWNLOAD CEMIG / MM.YYYY / BT|MT|NAO_IDENTIFICADA /
    pasta_final = CEMIG_DIR / fat.pasta / classificacao
    try:
        pasta_final.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log(f"  Erro ao criar pasta de destino {pasta_final}: {e}", "ERR")
        try:
            pdf.unlink(missing_ok=True)
        except Exception:
            pass
        return None

    destino = pasta_final / f"{indice_bb}.pdf"
    try:
        pdf.rename(destino)
    except Exception:
        import shutil
        shutil.copy2(pdf, destino)
        pdf.unlink(missing_ok=True)

    log(f"  Salvo: {fat.pasta}/{classificacao}/{destino.name} "
        f"({destino.stat().st_size:,}b)", "DL")
    return destino


# =============================================================================
# CARREGAR PLANILHA
# =============================================================================

def _digitos(s: str) -> str:
    """Remove sufixo .0 de float e retira não-dígitos."""
    s = re.sub(r"\.0+$", "", str(s).strip())
    return re.sub(r"\D", "", s)


def _buscar_pn(cnpj_d: str, mapa: dict) -> str:
    """Busca value do PN no mapa por match exato → sufixo → sufixo 8 dígitos."""
    if cnpj_d in mapa:
        return mapa[cnpj_d]
    if len(cnpj_d) >= 6:
        for chave, val in mapa.items():
            if chave.endswith(cnpj_d):
                return val
    suf = cnpj_d[-8:] if len(cnpj_d) >= 8 else cnpj_d
    for chave, val in mapa.items():
        if chave.endswith(suf) and len(suf) >= 6:
            return val
    return ""


def _ler_select(driver) -> dict:
    """
    Lê <select id="ddCliente"> e retorna:
        {cnpj_14_digitos: value_pn, value_pn: value_pn}
    """
    mapa = {}
    try:
        el  = WebDriverWait(driver, T_EL).until(
            EC.presence_of_element_located((By.ID, "ddCliente"))
        )
        sel = Select(el)
        n   = 0
        for opt in sel.options:
            val = (opt.get_attribute("value") or "").strip()
            txt = opt.text.strip()
            if not val:
                continue
            n += 1
            txt_d = re.sub(r"\D", "", txt)
            if txt_d:
                mapa[txt_d] = val
            mapa[val] = val
        log(f"Select #ddCliente: {n} CNPJs", "DBG")
    except Exception as e:
        log(f"Erro ao ler #ddCliente: {e}", "WARN")
    return mapa


def carregar_planilha(driver) -> List[UnidadeConsumidora]:
    candidatos = [
        ROOT_DIR / "DOWNLOAD CEMIG" / "Senhas_CEMIG.xlsx",
        ROOT_DIR / "DOWNLOAD CEMIG" / "Senhas CEMIG.xlsx",
        ROOT_DIR / "Senhas_CEMIG.xlsx",
        ROOT_DIR / "Senhas CEMIG.xlsx",
        Path("//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD CEMIG/Senhas_CEMIG.xlsx"),
        Path("//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD CEMIG/Senhas CEMIG.xlsx"),
    ]
    planilha = next((p for p in candidatos if p.exists()), None)
    if not planilha:
        log(f"Planilha não encontrada. Tentei: {[str(c) for c in candidatos]}", "ERR")
        return []

    log(f"Planilha: {planilha.name}")
    try:
        df = pd.read_excel(planilha, engine="openpyxl", dtype=str)
        df.columns = [str(c).strip().lower() for c in df.columns]
        log(f"Colunas detectadas: {df.columns.tolist()}", "DBG")

        # Coluna UC: "instalacao" mas NÃO "instalacao antiga"
        col_uc   = next((c for c in df.columns
                         if ("instal" in c or c == "uc") and "antiga" not in c), None)
        col_cnpj = next((c for c in df.columns if "cnpj" in c), None)

        if not col_uc or not col_cnpj:
            log(f"Colunas UC/CNPJ não encontradas. Disponíveis: {df.columns.tolist()}", "ERR")
            return []

        log(f"Colunas usadas → UC: '{col_uc}' | CNPJ: '{col_cnpj}'", "DBG")

        mapa_select = _ler_select(driver)
        inv_mapa    = {v: k for k, v in mapa_select.items() if len(k) > 8}

        ucs: List[UnidadeConsumidora] = []
        vistos: Set[Tuple[str, str]]  = set()
        ignoradas_cnpj: List[str]     = []

        for idx, row in df.iterrows():
            raw_uc   = str(row[col_uc]).strip()
            raw_cnpj = str(row[col_cnpj]).strip()

            uc      = re.sub(r"\D", "", re.sub(r"\.0+$", "", raw_uc))
            cnpj_d  = _digitos(raw_cnpj)

            if not uc or len(uc) < 10 or uc == "nan" or not cnpj_d:
                log(f"Linha {idx+2}: ignorada (uc={raw_uc!r} cnpj={raw_cnpj!r})", "DBG")
                continue

            pn_val  = _buscar_pn(cnpj_d, mapa_select)
            pn_txt  = inv_mapa.get(pn_val, cnpj_d)

            if not pn_val:
                ignoradas_cnpj.append(cnpj_d)
                log(f"Linha {idx+2}: CNPJ '{cnpj_d}' não encontrado no select "
                    f"— UC {uc} ignorada", "WARN")
                continue

            chave = (pn_val, uc)
            if chave not in vistos:
                vistos.add(chave)
                ucs.append(UnidadeConsumidora(
                    cnpj_valor=pn_val,
                    cnpj_texto=pn_txt,
                    cnpj_digitos=cnpj_d,
                    uc=uc,
                ))

        cnpjs_distintos = len(set(u.cnpj_valor for u in ucs))
        log(f"{len(ucs)} UCs carregadas ({cnpjs_distintos} CNPJs distintos)", "OK")
        return ucs

    except Exception as e:
        log(f"Erro ao ler planilha: {e}", "ERR")
        traceback.print_exc()
        return []


# =============================================================================
# EXECUÇÃO PRINCIPAL
# =============================================================================

def executar(limite: Optional[int] = None, um_por_cnpj: bool = False) -> int:
    print("\n" + "=" * 72)
    print("  CEMIG DOWNLOADER  —  V23")
    print("=" * 72)

    _mkdir_seguro(CEMIG_DIR)
    _mkdir_seguro(DEBUG_DIR)

    log("Carregando master...", "INFO")
    master = _carregar_master()
    log("Carregando índice local CEMIG...", "INFO")
    indice = IndiceLocal()
    log("Inicializando navegador CEMIG...", "INFO")
    driver = None
    try:
        driver = inicializar_driver_com_retry()
        # ── 1. Login ──────────────────────────────────────────────────────────
        log("Abrindo fluxo de login CEMIG...", "INFO")
        if not fazer_login(driver, LOGIN_CEMIG, SENHA_CEMIG):
            log("Abortando — login falhou.", "ERR")
            return 1

        # ── 2. Carregar planilha (lê o select que já está na tela) ────────────
        log("Carregando planilha de UCs...", "INFO")
        ucs_lista = carregar_planilha(driver)
        if not ucs_lista:
            log("Abortando — sem UCs.", "ERR")
            return 1

        # Modo um_por_cnpj: pega a primeira UC de cada CNPJ para testar troca
        if um_por_cnpj:
            vistos_cnpj: Set[str] = set()
            filtradas = []
            for u in ucs_lista:
                if u.cnpj_valor not in vistos_cnpj:
                    vistos_cnpj.add(u.cnpj_valor)
                    filtradas.append(u)
            ucs_lista = filtradas
            log(f"Modo um_por_cnpj: {len(ucs_lista)} CNPJs distintos selecionados", "OK")

        itens = ucs_lista if limite is None else ucs_lista[:limite]
        log(f"Processando {len(itens)} UC(s)...\n")

        baixadas = erros = puladas = 0
        cnpj_atual = ""   # rastreia o CNPJ ativo na sessão

        for i, item in enumerate(itens, start=1):
            print(f"\n{'─' * 72}")
            log(f"[{i}/{len(itens)}] {item.cnpj_texto} | UC={item.uc}")

            try:
                # ── Passo A: Trocar UC SEMPRE primeiro ────────────────────────
                # Isso revela o #ddCliente e o #limparInst na tela.
                # Se o campo já estiver visível (1ª UC), retorna imediatamente.
                clicar_trocar_uc(driver)

                # ── Passo B: Selecionar CNPJ (quando mudou) ───────────────────
                if item.cnpj_valor != cnpj_atual:
                    if not selecionar_cnpj(driver, item.cnpj_valor, item.cnpj_texto):
                        log(f"Pulando UC {item.uc} — falha no CNPJ", "WARN")
                        erros += 1
                        _voltar_home(driver)
                        continue
                    cnpj_atual = item.cnpj_valor

                # ── Passo C: Digitar UC e pesquisar ───────────────────────────
                if not digitar_uc_e_pesquisar(driver, item.uc):
                    log(f"Pulando UC {item.uc} — falha ao digitar", "WARN")
                    erros += 1
                    _voltar_home(driver)
                    continue

                # ── 4. Abrir Histórico de Contas ───────────────────────────────
                if not clicar_historico(driver):
                    log(f"Pulando UC {item.uc} — histórico não encontrado", "WARN")
                    erros += 1
                    _voltar_home(driver)
                    continue

                # ── 5. Ler faturas ─────────────────────────────────────────────
                faturas = ler_faturas(driver, item.uc)
                logar_faturas(faturas, item.uc)

                # ── 6. Baixar PDF da fatura mais recente não paga ──────────────
                for fat in faturas:
                    if "pag" in fat.status.lower():
                        continue

                    # Verifica no master E no índice local
                    ja_master = (master.ja_foi_baixado(item.uc, fat.mes_ref, "CEMIG")
                                 if master else False)
                    ja_local  = indice.ja_baixado(item.uc, fat.mes_ref)
                    ja_fatura_master = (
                        master.ja_foi_baixado_por_fatura(fat.documento_impressao, "CEMIG")
                        if master else False
                    )
                    ja_fatura_local = indice.ja_baixado_por_fatura(fat.mes_ref, fat.documento_impressao)

                    if ja_master or ja_local or ja_fatura_master or ja_fatura_local:
                        motivos = []
                        if ja_master:
                            motivos.append("master uc+mes")
                        if ja_local:
                            motivos.append("indice local uc+mes")
                        if ja_fatura_master:
                            motivos.append("master fatura_id")
                        if ja_fatura_local:
                            motivos.append("indice local fatura_id")
                        log(f"  {fat.mes_ano} — já baixado, ignorando ({', '.join(motivos)})", "DBG")
                        _emit("skipped_existing", uc=item.uc, mes_ref=fat.mes_ref)
                        puladas += 1
                        continue

                    # Baixa sem carimbo ainda — carimbo só consumido após
                    # confirmar que o PDF chegou e o diretório foi criado
                    destino = baixar_pdf(driver, fat, item.uc, "_tmp_sem_carimbo")
                    if destino:
                        # Só agora consome o carimbo e renomeia
                        if master:
                            carimbo = master.consumir_carimbo()
                        else:
                            carimbo = _consumir_carimbo_fallback()

                        destino_final = destino.parent / f"{carimbo}.pdf"
                        try:
                            destino.rename(destino_final)
                        except Exception:
                            import shutil as _sh
                            _sh.copy2(destino, destino_final)
                            destino.unlink(missing_ok=True)
                        destino = destino_final

                        # Gravar no master
                        if master:
                            master.registrar(
                                indice_bb=carimbo,
                                sistema="CEMIG",
                                uc=item.uc,
                                mes_ref=fat.mes_ref,
                                fatura_id=fat.documento_impressao,
                                cnpj=item.cnpj_digitos,
                                estado="MINAS GERAIS",
                                arquivo=str(destino),
                            )
                        # Gravar no índice local
                        indice.gravar(
                            indice_bb=carimbo,
                            uc=item.uc,
                            mes_ref=fat.mes_ref,
                            fatura_id=fat.documento_impressao,
                            cnpj=item.cnpj_digitos,
                            arquivo=str(destino),
                        )
                        _emit("downloaded", uc=item.uc, mes_ref=fat.mes_ref, carimbo=carimbo)
                        baixadas += 1
                        log(f"  ✓ {fat.mes_ano} → {carimbo}.pdf", "OK")
                    else:
                        erros += 1

                # ── Volta para Home — sem delay desnecessário ──────────────────
                _voltar_home(driver)

            except CemigSeleniumSessionEncerrada:
                # Nao tente debug/navegacao apos o ChromeDriver desaparecer.
                raise
            except Exception as e:
                log(f"Erro inesperado UC {item.uc}: {e}", "ERR")
                traceback.print_exc()
                salvar_debug(driver, f"erro_uc_{item.uc}")
                erros += 1
                _voltar_home(driver)

        # ── Resumo ─────────────────────────────────────────────────────────────
        print("\n" + "=" * 72)
        print(f"  Baixadas   : {baixadas}")
        print(f"  Já tinham  : {puladas}")
        print(f"  Erros      : {erros}")
        print(f"  Índice     : {INDEX_LOCAL}")
        if master:
            print(f"  Master     : {MASTER_PY_ATIVO or MASTER_PY_LOCAL}")
            print(f"  Próx. BB_  : {master.proximo_carimbo}")
        print("=" * 72)
        return 0

    except KeyboardInterrupt:
        log("Interrompido.", "WARN")
        return 130
    except CemigSeleniumSessionEncerrada as e:
        log(str(e), "ERR")
        # A causa permanece encadeada em ``e``; nao envie o traceback urllib3
        # ao Radar, que deve exibir apenas o resumo operacional acima.
        return 1
    except Exception as e:
        log(f"Erro fatal: {e}", "ERR")
        traceback.print_exc()
        if driver is not None:
            try:
                salvar_debug(driver, "erro_fatal")
            except CemigSeleniumSessionEncerrada as session_error:
                log(str(session_error), "ERR")
        return 1
    finally:
        encerrar_driver_seguro(driver)
        if driver is not None:
            print("\n[CEMIG] Navegador fechado.")
        print("\n[CEMIG] Processo finalizado.")


def _voltar_home(driver) -> None:
    """Navega para Home sem delay excessivo."""
    try:
        driver.get(f"{BASE_URL}/Home/Index/")
        spinner(driver, 8)
    except Exception as e:
        log(f"Erro ao voltar Home: {e}", "WARN")


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Downloader de faturas CEMIG via Selenium.")
    parser.add_argument("--limite", type=int, help="Limita UCs; usar somente em validacao controlada.")
    parser.add_argument("--um-por-cnpj", action="store_true", help="Processa uma UC por CNPJ.")
    args = parser.parse_args()
    # Modos de execução:
    #   executar(um_por_cnpj=True)   → 1 UC por CNPJ (testa troca de CNPJ)
    #   executar(limite=5)           → primeiras 5 UCs
    #   executar()                   → lote completo (produção)
    raise SystemExit(executar(limite=args.limite, um_por_cnpj=args.um_por_cnpj))
