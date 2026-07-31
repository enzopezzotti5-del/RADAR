#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
copel_mt.py  —  Downloader COPEL Media Tensao via Selenium
===========================================================
Fluxo por UC (login individual por UC):
  1. Abre portal COPEL e clica "Acessar utilizando a Unidade Consumidora"
  2. Preenche UC + CNPJ + Senha e faz login
  3. Clica "ACESSAR TODOS OS SERVICOS +"
  4. Clica "Historico de pagamento"
  5. Para cada fatura de 2026 nao baixada:
       a. Clica "2 via"
       b. Modal → "Fazer download da 2a via"
       c. Aguarda PDF na pasta temp
       d. Move para servidor, grava no indice local + master
"""

from __future__ import annotations

import sys
import ctypes as _ctypes
from pathlib import Path

if sys.platform == "win32":
    try:
        _ctypes.windll.kernel32.SetConsoleCtrlHandler(None, True)
    except Exception:
        pass

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import _venv_check  # noqa

import csv
import importlib.util
import os
import re
import shutil
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set, Tuple

import pandas as pd
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from core.project_paths import resolve_copel_accessos_xls

# =============================================================================
# CONFIGURACAO
# =============================================================================

ANO_MINIMO  = 2026

# API key do 2captcha.com para resolucao automatica de reCAPTCHA.
# Deixe vazio ("") para resolver manualmente.
TWOCAPTCHA_API_KEY = os.environ.get("TWOCAPTCHA_API_KEY", "3ea89b196b365e9db9d0fd245c628e4f")

ROOT_DIR    = Path("//10.10.250.21/Energia/ARQUIVOS ENZO")
COPEL_DIR   = ROOT_DIR / "DOWNLOAD COPEL"
ACESSOS_XLS = COPEL_DIR / "acessos_copel.xlsx"
INDEX_LOCAL = COPEL_DIR / "indice_faturas_copel_mt.csv"

_MASTER_SERVIDOR = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/indice_master.py")
MASTER_PY_LOCAL  = Path(__file__).resolve().parent.parent.parent / "indice_master.py"

URL_LOGIN    = "https://www.copel.com/avaweb/paginaLogin/login.jsf"
URL_LOGIN_UC = "https://www.copel.com/avaweb/avaweb/../paginaLogin/loginUc.jsf"
URL_DEPARA   = "https://app.copel.com/xuwweb/"

# Cache UC-antiga → UC-ANEEL (populado por _buscar_uc_nova, persiste durante a sessão)
_uc_nova_cache: dict[str, str] = {}

# Tempos (segundos)
T_LOGIN    = 60
T_EL       = 15
T_DOWNLOAD = 90

INDEX_FIELDS = ["INDICE", "INSTALACAO", "MES_REF", "NR_FATURA",
                "DATA_DOWNLOAD", "STATUS", "CNPJ", "ARQUIVO"]


# =============================================================================
# DATACLASSES
# =============================================================================

@dataclass
class Instalacao:
    medidor:    str
    prefixo:    str
    instalacao: str
    cnpj:       str
    senha:      str
    uc_nova:    str = ""   # UC ANEEL resolvida via De/Para (de_para_copel_mt.csv)


@dataclass
class FaturaHistorico:
    data_ri:          int
    mes_ref:          str
    mes_ref_raw:      str
    nr_fatura:        str
    situacao:         str
    data_vencimento:  str
    valor:            str
    link_via_id:      str


# =============================================================================
# LOGGING
# =============================================================================

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")

def log(msg: str, level: str = "INFO") -> None:
    sym = {"INFO": "→", "OK": "✔", "ERR": "✖", "WARN": "⚠",
           "DBG": "⟡", "DL": "📥", "FAT": "📁", "SKIP": "⋅"}
    print(f"[{_ts()}] {sym.get(level, '•')} [{level}] {msg}")


# =============================================================================
# HELPERS UNC
# =============================================================================

def _mkdir_seguro(pasta: Path) -> None:
    try:
        pasta.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

def _exists_unc(p: Path) -> bool:
    try:
        return p.exists()
    except OSError:
        return True


# =============================================================================
# INDICE MASTER
# =============================================================================

def _carregar_master() -> Optional[object]:
    candidatos = [MASTER_PY_LOCAL, _MASTER_SERVIDOR]
    for caminho in candidatos:
        if not _exists_unc(caminho):
            continue
        try:
            import threading as _thr
            _res = [None]; _err = [None]
            def _load():
                try:
                    spec = importlib.util.spec_from_file_location("indice_master", caminho)
                    m = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(m)
                    _res[0] = m
                except Exception as e:
                    _err[0] = e
            t = _thr.Thread(target=_load, daemon=True)
            t.start(); t.join(timeout=10)
            if t.is_alive() or _res[0] is None:
                raise TimeoutError(f"Timeout ao carregar {caminho.name}")
            mod = _res[0]
            if hasattr(mod, "_FILELOCK_OK") and not mod._FILELOCK_OK:
                log("filelock nao instalado — pip install filelock", "WARN")
            master = mod.MasterIndice(mod.MASTER_FILE)
            origem = "local" if Path(caminho).resolve() == MASTER_PY_LOCAL.resolve() else "servidor"
            log(
                f"Master: {len(master._ja_baixados)} registros | proximo: {master.proximo_carimbo} | fonte: {origem}",
                "OK",
            )
            return master
        except Exception as e:
            log(f"Falha ao carregar master: {e}", "WARN")
    log("indice_master.py nao encontrado — usando indice local apenas", "WARN")
    return None


def _master_ja_baixado(master, instalacao: str, mes_ref: str, sistema: str) -> bool:
    if not master:
        return False
    try:
        return bool(master.ja_foi_baixado(instalacao, mes_ref, sistema))
    except TypeError:
        log("Master antigo detectado — fallback sem SISTEMA na deduplicacao", "WARN")
        return bool(master.ja_foi_baixado(instalacao, mes_ref))


# =============================================================================
# INDICE LOCAL COPEL MT
# =============================================================================

class IndiceLocal:
    def __init__(self):
        self.memoria: Set[Tuple[str, str]] = set()
        self.proximo: int = 0
        self._carregar()

    def _carregar(self) -> None:
        if not _exists_unc(INDEX_LOCAL):
            _mkdir_seguro(COPEL_DIR)
            with open(INDEX_LOCAL, "w", encoding="utf-8-sig", newline="") as f:
                csv.writer(f).writerow(INDEX_FIELDS)
            log("Indice local MT criado (vazio)", "INFO")
            return

        try:
            with open(INDEX_LOCAL, encoding="utf-8-sig", newline="") as f:
                conteudo = f.read()
        except Exception as e:
            log(f"Indice local inacessivel: {e}", "WARN")
            return

        import io
        for row in csv.DictReader(io.StringIO(conteudo)):
            inst = row.get("INSTALACAO", "").strip()
            ref  = row.get("MES_REF", "").strip()
            if inst and ref:
                self.memoria.add((inst, ref))
            m = re.search(r"(\d+)$", row.get("INDICE", ""))
            if m:
                self.proximo = max(self.proximo, int(m.group(1)) + 1)

        log(f"Indice local MT: {len(self.memoria)} registros | proximo local={self.proximo or '(do master)'}", "OK")

    def ja_baixado(self, instalacao: str, mes_ref: str) -> bool:
        return (instalacao, mes_ref) in self.memoria

    def gravar(self, indice_bb: str, instalacao: str, mes_ref: str,
               nr_fatura: str, cnpj: str, arquivo: str) -> None:
        with open(INDEX_LOCAL, "a", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerow([
                indice_bb, instalacao, mes_ref, nr_fatura,
                datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                "Pendente", cnpj, arquivo,
            ])
        self.memoria.add((instalacao, mes_ref))
        m = re.search(r"(\d+)$", indice_bb)
        if m:
            num = int(m.group(1))
            if num >= self.proximo:
                self.proximo = num + 1


# =============================================================================
# LEITURA DE ACESSOS
# =============================================================================

def _ascii_fold(s: str) -> str:
    """Remove acentos e converte para ASCII maiusculo para comparacoes robustas."""
    import unicodedata
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").upper()


def carregar_instalacoes() -> List[Instalacao]:
    """Le acessos_copel.xlsx → filtra COPEL + Media Tensao."""
    planilha = resolve_copel_accessos_xls(COPEL_DIR)
    if not _exists_unc(planilha):
        log(f"Planilha nao encontrada: {planilha}", "ERR")
        return []
    try:
        df = pd.read_excel(planilha, dtype=str)
        df.columns = [c.strip() for c in df.columns]
    except Exception as e:
        log(f"Erro ao ler planilha: {e}", "ERR")
        return []

    col_conc   = next((c for c in df.columns if "concess"  in c.lower()), None)
    col_tensao = next((c for c in df.columns if "tens"     in c.lower()), None)
    col_inst   = next((c for c in df.columns if "instalac" in c.lower()), None)
    col_cnpj   = next((c for c in df.columns if c.upper() == "CNPJ"),     None)
    col_medi   = next((c for c in df.columns if "medidor"  in c.lower()), None)
    col_pref   = next((c for c in df.columns if "prefixo"  in c.lower()), None)
    col_senha  = next((c for c in df.columns if "senha"    in c.lower()), None)
    col_aneel  = next((c for c in df.columns if c.upper() == "UC_ANEEL"), None)

    faltando = [n for n, c in [("Concessionaria", col_conc), ("Tensao", col_tensao),
                                ("Instalacao", col_inst), ("CNPJ", col_cnpj),
                                ("Senha", col_senha)] if c is None]
    if faltando:
        log(f"Colunas nao encontradas: {faltando}", "ERR")
        return []

    # Usa ascii_fold para tolerar acentos e variações de grafia (ex: "MédiaTensão")
    mask = (df[col_conc].fillna("").apply(_ascii_fold).str.contains("COPEL", na=False) &
            df[col_tensao].fillna("").apply(_ascii_fold).str.contains("MEDIA", na=False))
    df_mt = df[mask].copy()
    log(f"Instalacoes COPEL MT: {len(df_mt)}", "OK")

    resultado = []
    for _, row in df_mt.iterrows():
        inst  = str(row.get(col_inst,  "") or "").strip()
        senha = str(row.get(col_senha, "") or "").strip()
        if not inst:
            continue
        if not senha:
            log(f"UC {inst}: sem senha — pulando", "WARN")
            continue
        uc_nova = str(row.get(col_aneel, "") or "").strip() if col_aneel else ""
        resultado.append(Instalacao(
            medidor    = str(row.get(col_medi, "") or "").strip(),
            prefixo    = str(row.get(col_pref, "") or "").strip(),
            instalacao = inst,
            cnpj       = str(row.get(col_cnpj, "") or "").strip(),
            senha      = senha,
            uc_nova    = uc_nova,
        ))
    return resultado


# =============================================================================
# SELENIUM — DRIVER
# =============================================================================

def _find_cached_chromedriver() -> str | None:
    """Encontra chromedriver compativel no cache do Selenium sem precisar de rede."""
    import subprocess as _sp
    from pathlib import Path as _P
    cache = _P.home() / ".cache" / "selenium" / "chromedriver" / "win64"
    if not cache.exists():
        return None
    try:
        r = _sp.run(
            ["powershell", "-c",
             "(gi 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe').VersionInfo.ProductVersion"],
            capture_output=True, text=True, timeout=5, creationflags=0x08000000)
        major = r.stdout.strip().split(".")[0]
    except Exception:
        major = None
    for p in sorted(cache.iterdir()):
        exe = p / "chromedriver.exe"
        if exe.exists() and (not major or p.name.startswith(major + ".")):
            return str(exe)
    return None


def _chrome_user_data_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"


def build_driver(temp_dl: Path) -> webdriver.Chrome:
    """
    Tenta iniciar primeiro com o perfil real do Chrome do usuario ("Default"),
    como no uso manual. Se falhar por bloqueio do perfil ou sessao criada,
    cai para o perfil isolado de automacao.
    """
    _mkdir_seguro(temp_dl)

    perfil_real = _chrome_user_data_dir()
    perfil_isolado = Path(os.environ.get("LOCALAPPDATA", "")) / "copel_selenium_profile_mt"
    _mkdir_seguro(perfil_isolado)

    def _build_options(user_data_dir: Path, profile_directory: str = "Default") -> Options:
        opts = Options()
        opts.add_argument(f"--user-data-dir={user_data_dir}")
        opts.add_argument(f"--profile-directory={profile_directory}")
        opts.add_experimental_option("prefs", {
            "download.default_directory":                    str(temp_dl.resolve()),
            "download.prompt_for_download":                  False,
            "download.directory_upgrade":                    True,
            "plugins.always_open_pdf_externally":            True,
            "profile.default_content_setting_values.automatic_downloads": 1,
        })
        opts.add_argument("--start-maximized")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1400,900")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--no-proxy-server")
        opts.add_argument("--no-restore-last-session")
        opts.add_argument("--no-first-run")
        opts.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
        opts.add_experimental_option("useAutomationExtension", False)
        return opts

    opts = _build_options(perfil_real if perfil_real.exists() else perfil_isolado)

    _cd = _find_cached_chromedriver()
    if _cd:
        service = Service(_cd)
    else:
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            import os as _os
            _proxy_keys = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
            _proxy_bak = {k: _os.environ.pop(k, None) for k in _proxy_keys}
            try:
                service = Service(ChromeDriverManager().install())
            finally:
                for k, v in _proxy_bak.items():
                    if v is not None:
                        _os.environ[k] = v
        except BaseException:
            service = Service()

    try:
        if perfil_real.exists():
            log(f"Chrome MT usando perfil real: {perfil_real}", "INFO")
        driver = webdriver.Chrome(service=service, options=opts)
    except Exception as exc:
        msg = str(exc).lower()
        if perfil_real.exists() and ("session not created" in msg or "user data directory is already in use" in msg or "chrome not reachable" in msg):
            log("Perfil real do Chrome indisponivel — tentando perfil isolado de automacao", "WARN")
            opts = _build_options(perfil_isolado)
            driver = webdriver.Chrome(service=service, options=opts)
        else:
            raise

    driver.set_page_load_timeout(30)
    driver.execute_cdp_cmd("Page.setDownloadBehavior", {
        "behavior":     "allow",
        "downloadPath": str(temp_dl.resolve()),
    })
    driver.get(URL_LOGIN)
    return driver


def _resolver_captcha_2captcha(sitekey: str, page_url: str) -> Optional[str]:
    """Resolve o reCAPTCHA v2 via API 2captcha e retorna o token."""
    import json as _json
    import urllib.request as _req
    import urllib.error as _uerr

    if not TWOCAPTCHA_API_KEY or not sitekey:
        return None

    def _post_json(url: str, payload: bytes, tentativas: int = 3):
        ultimo_erro = None
        for tentativa in range(1, tentativas + 1):
            try:
                req = _req.Request(
                    url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                return _json.loads(_req.urlopen(req, timeout=30).read())
            except (_uerr.HTTPError, _uerr.URLError, TimeoutError) as e:
                ultimo_erro = e
                if tentativa < tentativas:
                    log(f"2captcha HTTP/rede falhou (t{tentativa}/{tentativas}): {e}", "WARN")
                    time.sleep(2 * tentativa)
                else:
                    raise
            except Exception as e:
                ultimo_erro = e
                if tentativa < tentativas:
                    log(f"2captcha chamada falhou (t{tentativa}/{tentativas}): {e}", "WARN")
                    time.sleep(2 * tentativa)
                else:
                    raise
        if ultimo_erro:
            raise ultimo_erro

    log("Enviando reCAPTCHA para 2captcha...", "DBG")
    try:
        payload = _json.dumps({
            "clientKey": TWOCAPTCHA_API_KEY,
            "task": {
                "type": "RecaptchaV2TaskProxyless",
                "websiteURL": page_url,
                "websiteKey": sitekey,
            }
        }).encode()
        res = _post_json("https://api.2captcha.com/createTask", payload)
        if res.get("errorId") != 0:
            log(f"2captcha createTask erro: {res}", "WARN")
            return None

        task_id = res["taskId"]
        log(f"2captcha taskId={task_id} ? aguardando solu??o...", "DBG")

        payload_get = _json.dumps({
            "clientKey": TWOCAPTCHA_API_KEY,
            "taskId": task_id,
        }).encode()
        for _ in range(40):
            time.sleep(3)
            try:
                res2 = _post_json("https://api.2captcha.com/getTaskResult", payload_get, tentativas=2)
            except Exception as e:
                log(f"2captcha getTaskResult falhou temporariamente: {e}", "WARN")
                continue
            if res2.get("status") == "ready":
                token = res2.get("solution", {}).get("gRecaptchaResponse", "")
                if token:
                    log("reCAPTCHA resolvido pelo 2captcha", "OK")
                    return token

        log("Timeout 2captcha ? sem solu??o em 120s", "WARN")
        return None
    except Exception as e:
        log(f"Erro ao chamar 2captcha: {e}", "WARN")
        return None


def _injetar_captcha(driver: webdriver.Chrome, token: str) -> None:
    """Injeta o token do captcha e tenta disparar o callback do reCAPTCHA."""
    driver.execute_script(
        """
        const token = arguments[0];
        const targets = Array.from(document.querySelectorAll(
          '#g-recaptcha-response, textarea[name="g-recaptcha-response"]'
        ));
        if (!targets.length) {
          const ta = document.createElement('textarea');
          ta.id = 'g-recaptcha-response';
          ta.name = 'g-recaptcha-response';
          ta.style.display = 'none';
          document.body.appendChild(ta);
          targets.push(ta);
        }
        targets.forEach(el => {
          el.value = token;
          el.innerHTML = token;
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
        });

        function findCallback(obj, seen) {
          if (!obj || typeof obj !== 'object' || seen.has(obj)) return null;
          seen.add(obj);
          for (const key of Object.keys(obj)) {
            const val = obj[key];
            if (key === 'callback' && typeof val === 'function') return val;
            const nested = findCallback(val, seen);
            if (nested) return nested;
          }
          return null;
        }

        try {
          if (window.___grecaptcha_cfg && ___grecaptcha_cfg.clients) {
            for (const client of Object.values(___grecaptcha_cfg.clients)) {
              const cb = findCallback(client, new Set());
              if (cb) {
                cb(token);
                break;
              }
            }
          }
        } catch (e) {}
        """,
        token,
    )


def _resolver_captcha(driver: webdriver.Chrome, timeout: int = 120,
                       wait_aparece: int = 8) -> None:
    """
    Aguarda até wait_aparece segundos para o reCAPTCHA aparecer.
    Se detectado: resolve via 2captcha ou aguarda resolução manual.
    """
    try:
        WebDriverWait(driver, wait_aparece).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "iframe[src*='recaptcha']"))
        )
    except Exception:
        return  # Captcha não apareceu — segue normalmente
    iframes = driver.find_elements(By.CSS_SELECTOR, "iframe[src*='recaptcha']")
    if not iframes:
        return

    sitekey = None
    try:
        src = iframes[0].get_attribute("src") or ""
        m = re.search(r"[?&]k=([^&]+)", src)
        if m:
            sitekey = m.group(1)
    except Exception:
        pass

    page_url = driver.current_url

    if TWOCAPTCHA_API_KEY and sitekey:
        log(f"reCAPTCHA detectado (sitekey={sitekey[:12]}...) ? resolvendo via 2captcha...", "WARN")
        try:
            token = _resolver_captcha_2captcha(sitekey, page_url)
            if not token:
                raise RuntimeError("2captcha nao retornou token")
            log("Token 2captcha obtido ? injetando na pagina...", "DBG")
            _injetar_captcha(driver, token)
            time.sleep(1)
            log("Captcha resolvido automaticamente.", "OK")
            return
        except Exception as e:
            log(f"2captcha falhou ({e}) ? aguardando resolucao manual.", "WARN")

    log("reCAPTCHA detectado ? resolva manualmente no navegador e aguarde.", "WARN")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2)
        try:
            marcado = driver.find_elements(By.CSS_SELECTOR, "#recaptcha-anchor[aria-checked='true']")
            presente = driver.find_elements(By.CSS_SELECTOR, "iframe[src*='recaptcha']")
            if marcado or not presente:
                log("Captcha resolvido ? continuando.", "OK")
                return
        except Exception:
            return
    log("Timeout aguardando captcha ? tentando prosseguir mesmo assim.", "WARN")


# =============================================================================
# HELPERS SELENIUM
# =============================================================================

def W(driver, by, sel, t=T_EL):
    return WebDriverWait(driver, t).until(EC.element_to_be_clickable((by, sel)))

def W_vis(driver, by, sel, t=T_EL):
    return WebDriverWait(driver, t).until(EC.visibility_of_element_located((by, sel)))

def W_pres(driver, by, sel, t=T_EL):
    return WebDriverWait(driver, t).until(EC.presence_of_element_located((by, sel)))


def preencher_input_login(driver, by, sel, valor: str, t=T_EL) -> object:
    """
    Preenche inputs do login de forma resiliente para componentes com mascara/JS.
    """
    el = W(driver, by, sel, t)
    valor = str(valor or "").strip()
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    try:
        el.click()
    except Exception:
        pass
    try:
        el.clear()
    except Exception:
        pass
    try:
        el.send_keys(Keys.CONTROL + "a")
        el.send_keys(Keys.DELETE)
    except Exception:
        pass
    try:
        el.send_keys(valor)
    except Exception:
        pass

    atual = str(el.get_attribute("value") or "").strip()
    if atual != valor:
        driver.execute_script(
            """
            const el = arguments[0];
            const value = arguments[1];
            el.focus();
            el.value = value;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('blur', { bubbles: true }));
            """,
            el,
            valor,
        )
        atual = str(el.get_attribute("value") or "").strip()

    if atual != valor:
        raise RuntimeError(f"Falha ao preencher {sel}: esperado={valor!r} atual={atual!r}")
    return el

def clicar(driver, el_ou_sel, by=None, label="") -> bool:
    try:
        el = driver.find_element(by, el_ou_sel) if by else el_ou_sel
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        time.sleep(0.15)
        el.click()
        return True
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", el)
            return True
        except Exception as e:
            log(f"Falha ao clicar '{label}': {e}", "ERR")
            return False

def _aguardar_spinner(driver, t=10) -> None:
    try:
        WebDriverWait(driver, t).until_not(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".ui-blockui, .ui-widget-overlay"))
        )
    except Exception:
        pass
    time.sleep(0.3)


# =============================================================================
# FLUXO DE LOGIN POR UC (MT)
# =============================================================================

def _popular_cache_depara(driver: webdriver.Chrome, instalacoes: "List[Instalacao]") -> None:
    """
    Percorre todas as instalações UMA ÚNICA VEZ na página De/Para
    e popula _uc_nova_cache com UC-antiga → UC-ANEEL.
    Fica na mesma aba durante toda a varredura (sem renavegar por UC).
    """
    ucs = [i.instalacao for i in instalacoes if i.instalacao not in _uc_nova_cache]
    if not ucs:
        log("De/Para: cache ja completo.", "DBG")
        return
    log(f"De/Para: consultando {len(ucs)} UC(s) em {URL_DEPARA} ...", "INFO")
    try:
        driver.get(URL_DEPARA)
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='oldUc']"))
        )
    except Exception as exc:
        log(f"De/Para: pagina nao carregou — {exc}", "WARN")
        for uc in ucs:
            _uc_nova_cache[uc] = uc
        return

    for uc_antiga in ucs:
        try:
            campo_old = driver.find_element(By.CSS_SELECTOR, "input[name='oldUc']")
            campo_new = driver.find_element(By.CSS_SELECTOR, "input[name='newUc']")
            # Limpa resultado anterior
            driver.execute_script("arguments[0].value = '';", campo_new)
            # Preenche UC antiga (Ctrl+A + Delete garante limpeza em inputs React)
            campo_old.click()
            campo_old.send_keys(Keys.CONTROL + "a")
            campo_old.send_keys(Keys.DELETE)
            campo_old.send_keys(uc_antiga)
            btn = driver.find_element(
                By.XPATH, "//button[.//span[normalize-space(text())='Pesquisar']]"
            )
            btn.click()
            WebDriverWait(driver, 15).until(
                lambda d: (
                    d.find_element(By.CSS_SELECTOR, "input[name='newUc']")
                     .get_attribute("value") or ""
                ).strip()
            )
            uc_nova = (
                driver.find_element(By.CSS_SELECTOR, "input[name='newUc']")
                      .get_attribute("value") or ""
            ).strip()
            if uc_nova:
                _uc_nova_cache[uc_antiga] = uc_nova
                log(f"  {uc_antiga} → {uc_nova}", "OK")
            else:
                log(f"  {uc_antiga}: newUc vazio — mantendo UC antiga", "WARN")
                _uc_nova_cache[uc_antiga] = uc_antiga
        except Exception as exc:
            log(f"  {uc_antiga}: erro ({exc}) — mantendo UC antiga", "WARN")
            _uc_nova_cache[uc_antiga] = uc_antiga

    log("De/Para: cache populado.", "OK")


def _buscar_uc_nova(uc_antiga: str) -> str:
    """Retorna a UC ANEEL do cache (populado por _popular_cache_depara). Fallback: uc_antiga."""
    return _uc_nova_cache.get(uc_antiga, uc_antiga)


def fazer_login_uc(driver: webdriver.Chrome, inst: Instalacao) -> bool:
    """
    Login individual por UC no portal COPEL MT.
    1. Resolve UC ANEEL via De/Para (app.copel.com/xuwweb/)
    2. Abre URL_LOGIN
    3. Clica no link "Acessar utilizando a Unidade Consumidora"
    4. Preenche formulario:numUCAneel (fallback: formulario:numUC),
       formulario:numDoc, formulario:pass
    5. Submete e aguarda tela de servicos
    """
    uc_login = inst.uc_nova or inst.instalacao
    log(f"Login UC {inst.instalacao} (ANEEL: {uc_login}) ...", "INFO")
    try:
        driver.get(URL_LOGIN)

        # Clica no link "Acessar utilizando a Unidade Consumidora"
        link_uc = WebDriverWait(driver, T_LOGIN).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a[href*='loginUc']"))
        )
        driver.execute_script("arguments[0].click();", link_uc)
        log("Clicado 'Acessar utilizando a Unidade Consumidora'", "DBG")

        # Resolve captcha se aparecer na tela de login por UC
        _resolver_captcha(driver)

        # O portal novo usa formulario:numUCAneel; mantemos fallback para o ID legado.
        try:
            campo_uc_sel = "formulario:numUCAneel"
            campo_uc = W(driver, By.ID, campo_uc_sel, T_LOGIN)
            campo_uc_id = "formulario:numUCAneel"
        except TimeoutException:
            campo_uc_sel = "formulario:numUC"
            campo_uc = W(driver, By.ID, campo_uc_sel, T_LOGIN)
            campo_uc_id = "formulario:numUC"
        preencher_input_login(driver, By.ID, campo_uc_sel, uc_login, T_LOGIN)
        log(f"UC ANEEL preenchida no campo {campo_uc_id}", "DBG")

        # Preenche CNPJ/CPF (apenas numeros)
        cnpj_numeros = re.sub(r"\D", "", inst.cnpj)
        preencher_input_login(driver, By.ID, "formulario:numDoc", cnpj_numeros, T_LOGIN)

        # Preenche Senha
        campo_senha = preencher_input_login(driver, By.ID, "formulario:pass", inst.senha, T_LOGIN)

        # Submete
        try:
            btn = driver.find_element(By.CSS_SELECTOR, "input[type='submit'], button[type='submit']")
            clicar(driver, btn, label="submit login UC")
        except NoSuchElementException:
            campo_senha.send_keys(Keys.RETURN)

        # Verifica erros de login
        time.sleep(2)
        erros = driver.find_elements(By.CSS_SELECTOR, ".ui-messages-error, .erro-login, .ui-message-error")
        if erros and any(e.text.strip() for e in erros):
            for e in erros:
                log(f"Erro login UC {inst.instalacao}: {e.text.strip()}", "ERR")
            return False

        # Aguarda tela de servicos — botao "ACESSAR TODOS OS SERVICOS +" ou link historico
        WebDriverWait(driver, T_LOGIN).until(
            EC.any_of(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button.seleciona")),
                EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='historicoPagamento']")),
            )
        )
        log(f"Login UC {inst.instalacao} OK. URL: {driver.current_url}", "OK")
        return True

    except TimeoutException:
        log(f"Timeout no login UC {inst.instalacao}", "ERR")
        return False
    except Exception as e:
        log(f"Excecao no login UC {inst.instalacao}: {e}", "ERR")
        return False


# =============================================================================
# NAVEGACAO: TODOS OS SERVICOS → HISTORICO DE PAGAMENTO
# =============================================================================

def acessar_historico(driver: webdriver.Chrome) -> bool:
    """
    Apos login:
      1. Clica "ACESSAR TODOS OS SERVICOS +" — refrorca ate 3x se necessario
      2. Navega direto para historicoPagamento.jsf
      3. Aguarda tabela ate 60s
    """
    T_HIST = 60

    try:
        btn_todos = None
        for tentativa in range(1, 4):
            try:
                btn_todos = WebDriverWait(driver, T_HIST).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "button.seleciona"))
                )
                break
            except TimeoutException:
                log(f"Botao 'ACESSAR TODOS' nao encontrado (tentativa {tentativa}/3)", "WARN")
                time.sleep(2)

        if btn_todos is None:
            log("Botao 'ACESSAR TODOS OS SERVICOS +' nao apareceu", "ERR")
            return False

        for tentativa in range(1, 4):
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn_todos)
            driver.execute_script("arguments[0].click();", btn_todos)
            log(f"Clicado 'ACESSAR TODOS OS SERVICOS +' (tentativa {tentativa})", "DBG")
            _aguardar_spinner(driver)

            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, "a[href*='historicoPagamento']")
                    )
                )
                break
            except TimeoutException:
                if tentativa < 3:
                    log("Link historico nao apareceu, reforcando clique...", "WARN")
                    time.sleep(2)
                else:
                    log("Link 'Historico de pagamento' nao apareceu apos 3 tentativas", "ERR")
                    return False

        # Navega direto para a pagina
        base = "/".join(driver.current_url.split("/")[:3])
        driver.get(base + "/avaweb/paginas/historicoPagamento.jsf")
        log("Navegando direto para historicoPagamento.jsf", "DBG")

        WebDriverWait(driver, T_HIST).until(
            EC.presence_of_element_located(
                (By.ID, "formHistoricoPagto:dtListaHistoricoPagto_data")
            )
        )
        log(f"Historico carregado. URL: {driver.current_url}", "OK")
        return True

    except TimeoutException:
        log("Timeout acessando historico de pagamento", "ERR")
        return False
    except Exception as e:
        log(f"Excecao em acessar_historico: {e}", "ERR")
        return False


# =============================================================================
# LEITURA DO HISTORICO
# =============================================================================

def ler_historico(driver: webdriver.Chrome) -> List[FaturaHistorico]:
    CORPO_ID = "formHistoricoPagto:dtListaHistoricoPagto_data"
    faturas: List[FaturaHistorico] = []

    try:
        W_vis(driver, By.ID, CORPO_ID)
        corpo = driver.find_element(By.ID, CORPO_ID)
        linhas = corpo.find_elements(By.XPATH, ".//tr[@data-ri]")

        for linha in linhas:
            try:
                ri = int(linha.get_attribute("data-ri"))
                tds = linha.find_elements(By.TAG_NAME, "td")
                if len(tds) < 8:
                    continue

                def _txt(td):
                    spans = td.find_elements(By.TAG_NAME, "span")
                    full = td.text.strip()
                    if spans:
                        label = spans[0].text.strip()
                        return full[len(label):].strip()
                    return full

                mes_raw   = _txt(tds[0])
                nr_fatura = _txt(tds[1])
                situacao  = _txt(tds[2])
                dt_venc   = _txt(tds[4])
                valor     = _txt(tds[6])

                links_via = linha.find_elements(By.CSS_SELECTOR, "a.ui-commandlink")
                if not links_via:
                    continue
                link_id = links_via[0].get_attribute("id") or ""

                mes_ref = mes_raw.replace("/", "-") if "/" in mes_raw else mes_raw

                partes = mes_ref.split("-")
                if len(partes) == 2:
                    try:
                        if int(partes[1]) < ANO_MINIMO:
                            continue
                    except ValueError:
                        continue

                faturas.append(FaturaHistorico(
                    data_ri         = ri,
                    mes_ref         = mes_ref,
                    mes_ref_raw     = mes_raw,
                    nr_fatura       = nr_fatura,
                    situacao        = situacao,
                    data_vencimento = dt_venc,
                    valor           = valor,
                    link_via_id     = link_id,
                ))
            except StaleElementReferenceException:
                continue
            except Exception as e:
                log(f"Erro ao ler linha historico: {e}", "WARN")
                continue

        log(f"Historico: {len(faturas)} fatura(s) encontrada(s) >= {ANO_MINIMO}", "INFO")
        return faturas

    except Exception as e:
        log(f"Erro ao ler historico: {e}", "ERR")
        return []


# =============================================================================
# DOWNLOAD DE 2a VIA
# =============================================================================

def _voltar_janela_principal(driver: webdriver.Chrome, handle_principal: str) -> None:
    try:
        handles = driver.window_handles
        for h in handles:
            if h != handle_principal:
                try:
                    driver.switch_to.window(h)
                    driver.close()
                except Exception:
                    pass
        driver.switch_to.window(handle_principal)
    except Exception:
        pass


def _clicar_botao_download_modal(driver: webdriver.Chrome, timeout: int = T_EL) -> bool:
    """
    Clica no botao final de download do modal da COPEL.

    O portal alterna entre IDs dinamicos e rotulos textuais como:
      - Fazer download da 2ª via
      - Fazer download da 2a via
      - Download
    """
    seletores = [
        (By.ID, "frmModalSegundaVia:j_idt124", "ID padrao"),
        (By.XPATH, "//button[.//span[normalize-space()='Fazer download da 2ª via']]", "texto 2a via (ª)"),
        (By.XPATH, "//button[.//span[normalize-space()='Fazer download da 2a via']]", "texto 2a via (a)"),
        (By.XPATH, "//span[normalize-space()='Fazer download da 2ª via']/ancestor::button[1]", "span 2a via (ª)"),
        (By.XPATH, "//span[normalize-space()='Fazer download da 2a via']/ancestor::button[1]", "span 2a via (a)"),
        (By.XPATH, "//button[.//span[normalize-space()='Download']]", "texto download"),
    ]

    ultimo_erro: Exception | None = None
    for by, seletor, rotulo in seletores:
        try:
            btn = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((by, seletor))
            )
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            time.sleep(0.2)
            try:
                btn.click()
                metodo = "click nativo"
            except Exception:
                try:
                    ActionChains(driver).move_to_element(btn).pause(0.1).click(btn).perform()
                    metodo = "ActionChains"
                except Exception:
                    try:
                        btn.send_keys(Keys.ENTER)
                        metodo = "ENTER"
                    except Exception:
                        driver.execute_script("arguments[0].click();", btn)
                        metodo = "click JS"
            log(f"Botao de download do modal clicado ({rotulo} | {metodo})", "DBG")
            _aguardar_spinner(driver)
            return True
        except Exception as exc:
            ultimo_erro = exc
            continue

    if ultimo_erro:
        log(f"Nao foi possivel clicar no botao de download do modal: {ultimo_erro}", "WARN")
    return False


def _diagnosticar_timeout_download(
    driver: webdriver.Chrome,
    temp_dir: Path,
    handle_principal: str,
    nr_fatura: str,
) -> None:
    try:
        arquivos = [p.name for p in temp_dir.glob("*")]
        if arquivos:
            log(f"Timeout download {nr_fatura} — arquivos no temp: {arquivos}", "WARN")
    except Exception:
        pass

    try:
        handles = driver.window_handles
        if len(handles) > 1:
            urls: list[str] = []
            atual = driver.current_window_handle
            for h in handles:
                try:
                    driver.switch_to.window(h)
                    urls.append(driver.current_url)
                except Exception:
                    continue
            try:
                driver.switch_to.window(atual if atual in handles else handle_principal)
            except Exception:
                pass
            log(f"Timeout download {nr_fatura} — abas abertas: {len(handles)} | urls={urls}", "WARN")
    except Exception:
        pass


def baixar_fatura(driver: webdriver.Chrome, fatura: FaturaHistorico,
                  temp_dir: Path, handle_principal: str) -> Optional[Path]:
    log(f"Baixando {fatura.mes_ref} — fatura {fatura.nr_fatura}", "DL")
    pdfs_antes = {p: p.stat().st_mtime for p in temp_dir.glob("*.pdf")}

    try:
        link_via = W(driver, By.ID, fatura.link_via_id)
        onclick = link_via.get_attribute("onclick") or ""
        if "PrimeFaces" in onclick or "submit" in onclick:
            js_call = onclick.replace("return false;", "").strip().rstrip(";")
            driver.execute_script(js_call)
        else:
            driver.execute_script("arguments[0].click();", link_via)
        _aguardar_spinner(driver)

        # Modal 2a via
        try:
            if not _clicar_botao_download_modal(driver):
                raise TimeoutException("Botao 'Fazer download da 2ª via' nao encontrado/clicavel")
            log("Modal 2a via — download disparado", "DBG")
        except TimeoutException:
            # Modal de 1a via
            log("Modal 2a via nao encontrado — tentando modal 1a via (AVA)", "WARN")
            try:
                try:
                    chk_span = WebDriverWait(driver, 3).until(
                        EC.element_to_be_clickable(
                            (By.CSS_SELECTOR, "span.ui-chkbox-icon.ui-icon-blank")
                        )
                    )
                    driver.execute_script("arguments[0].click();", chk_span)
                    time.sleep(0.5)
                    log("Modal 1a via — checkbox marcado", "DBG")
                except TimeoutException:
                    log("Modal 1a via — checkbox ja marcado, seguindo", "DBG")

                btn_emitir = WebDriverWait(driver, T_EL).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//button[.//span[@class='ui-button-text ui-c'"
                                   " and normalize-space(text())='Emitir']]")
                    )
                )
                driver.execute_script("arguments[0].click();", btn_emitir)
                log("Modal 1a via — botao Emitir clicado", "DBG")
                _aguardar_spinner(driver)

                if not _clicar_botao_download_modal(driver):
                    raise TimeoutException("Botao final de download nao encontrado no modal 1a via")
                log("Modal 1a via — download disparado", "DBG")
            except Exception as e_1via:
                log(f"Falha no modal 1a via: {e_1via}", "ERR")
                _voltar_janela_principal(driver, handle_principal)
                return None

        # Aguarda PDF
        deadline = time.time() + T_DOWNLOAD
        pdf_novo: Optional[Path] = None
        while time.time() < deadline:
            crdowns = list(temp_dir.glob("*.crdownload"))
            if not crdowns:
                pdfs_agora = {p: p.stat().st_mtime for p in temp_dir.glob("*.pdf")}
                novos = set(pdfs_agora) - set(pdfs_antes)
                atualizados = {
                    p for p in pdfs_agora
                    if p in pdfs_antes and pdfs_agora[p] != pdfs_antes[p]
                }
                candidatos = novos | atualizados
                if candidatos:
                    pdf_novo = max(candidatos, key=lambda p: p.stat().st_mtime)
                    break
            time.sleep(1)

        if pdf_novo is None:
            log(f"Timeout aguardando PDF da fatura {fatura.nr_fatura}", "ERR")
            _diagnosticar_timeout_download(driver, temp_dir, handle_principal, fatura.nr_fatura)
            _voltar_janela_principal(driver, handle_principal)
            return None

        log(f"PDF recebido: {pdf_novo.name}", "OK")

        try:
            btn_fechar = driver.find_element(
                By.CSS_SELECTOR,
                ".ui-dialog-titlebar-close, button[aria-label='Close']"
            )
            driver.execute_script("arguments[0].click();", btn_fechar)
        except Exception:
            try:
                driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            except Exception:
                pass
        time.sleep(0.5)
        _aguardar_spinner(driver)
        _voltar_janela_principal(driver, handle_principal)

        return pdf_novo

    except TimeoutException:
        log(f"Timeout ao baixar fatura {fatura.nr_fatura}", "ERR")
        _voltar_janela_principal(driver, handle_principal)
        return None
    except Exception as e:
        log(f"Excecao ao baixar fatura {fatura.nr_fatura}: {e}", "ERR")
        _voltar_janela_principal(driver, handle_principal)
        return None


# =============================================================================
# GRAVACAO NOS INDICES
# =============================================================================

def _mes_pasta(mes_ref: str) -> str:
    """'03-2026' → '03.2026'"""
    return mes_ref.replace("-", ".")

def _subpasta_tensao() -> str:
    return "MT"

def gravar_registro(master, indice_local: IndiceLocal,
                    instalacao: str, fatura: FaturaHistorico,
                    cnpj: str, arquivo_final: str,
                    carimbo_pre: str = "") -> None:
    carimbo = carimbo_pre

    if master:
        try:
            master.registrar(
                indice_bb  = carimbo,
                sistema    = "COPEL",
                uc         = instalacao,
                mes_ref    = fatura.mes_ref,
                fatura_id  = fatura.nr_fatura,
                cnpj       = cnpj,
                estado     = "PR",
                instalacao = instalacao,
                arquivo    = arquivo_final,
            )
            log(f"Master gravado: {carimbo}", "DBG")
        except Exception as e:
            log(f"ERRO ao gravar no master ({carimbo}): {e}", "ERR")

    indice_local.gravar(
        indice_bb  = carimbo,
        instalacao = instalacao,
        mes_ref    = fatura.mes_ref,
        nr_fatura  = fatura.nr_fatura,
        cnpj       = cnpj,
        arquivo    = arquivo_final,
    )
    log(f"Gravado: {carimbo} | {instalacao} | {fatura.mes_ref} | {arquivo_final}", "OK")


# =============================================================================
# ARGS
# =============================================================================

def _parse_args():
    import argparse
    p = argparse.ArgumentParser(description="COPEL MT Downloader")
    p.add_argument("--ucs", default="", help="Instalações separadas por vírgula (filtra a lista)")
    p.add_argument("--mes-ref", default="", help="Referências separadas por vírgula, ex: 02-2026,03-2026")
    p.add_argument("--force", action="store_true", help="Ignora checks de já baixado")
    return p.parse_args()


# =============================================================================
# MAIN
# =============================================================================

def main() -> int:
    args = _parse_args()
    ucs_filtro   = {u.strip() for u in args.ucs.split(",")   if u.strip()} if args.ucs   else set()
    refs_filtro  = {r.strip() for r in args.mes_ref.split(",") if r.strip()} if args.mes_ref else set()
    force        = args.force

    log("=" * 60)
    log("COPEL MT — Downloader iniciado")
    if ucs_filtro:  log(f"Filtro UCs    : {sorted(ucs_filtro)}", "INFO")
    if refs_filtro: log(f"Filtro refs   : {sorted(refs_filtro)}", "INFO")
    if force:       log("Modo FORCE    : ignorando checks de ja baixado", "WARN")
    log("=" * 60)

    instalacoes = carregar_instalacoes()
    if not instalacoes:
        log("Nenhuma instalacao COPEL MT. Abortando.", "ERR")
        return 1

    if ucs_filtro:
        instalacoes = [i for i in instalacoes if i.instalacao in ucs_filtro]
        log(f"Apos filtro UCs: {len(instalacoes)} instalacao(es)", "INFO")
        nao_encontradas = ucs_filtro - {i.instalacao for i in instalacoes}
        if nao_encontradas:
            log(f"UCs nao encontradas em acessos_copel.xlsx: {sorted(nao_encontradas)}", "WARN")

    if not instalacoes:
        log("Nenhuma instalacao apos filtro. Abortando.", "ERR")
        return 1
    log(f"Total instalacoes MT: {len(instalacoes)}", "INFO")

    indice_local = IndiceLocal()
    master       = _carregar_master()

    if master:
        proximo_master = master._proximo_num
        if indice_local.proximo < proximo_master:
            log(f"Contador local ajustado: {indice_local.proximo} → {proximo_master} (do master)", "INFO")
            indice_local.proximo = proximo_master

    script_dir = Path(__file__).resolve().parent
    temp_dir   = script_dir / "downloads_temp_copel_mt"
    _mkdir_seguro(temp_dir)
    driver = build_driver(temp_dir)

    try:
        ok_total   = 0
        skip_total = 0
        erro_total = 0

        for idx, inst in enumerate(instalacoes, 1):
            log(f"[{idx}/{len(instalacoes)}] Instalacao: {inst.instalacao}", "INFO")

            _voltar_janela_principal(driver, driver.current_window_handle)

            # Login individual por UC (MT)
            if not fazer_login_uc(driver, inst):
                erro_total += 1
                continue

            handle_principal = driver.current_window_handle

            # Acessa historico de pagamento
            if not acessar_historico(driver):
                erro_total += 1
                continue

            # Le faturas
            faturas = ler_historico(driver)
            if not faturas:
                log(f"Nenhuma fatura >= {ANO_MINIMO} para {inst.instalacao}", "SKIP")
                skip_total += 1
                continue

            # Filtra por referência se solicitado
            if refs_filtro:
                faturas = [f for f in faturas if f.mes_ref in refs_filtro]
                if not faturas:
                    log(f"Nenhuma fatura nas refs {refs_filtro} para {inst.instalacao}", "SKIP")
                    skip_total += 1
                    continue

            # Download de cada fatura nao baixada
            for fatura in faturas:
                if not force and indice_local.ja_baixado(inst.instalacao, fatura.mes_ref):
                    log(f"Ja baixado: {inst.instalacao} / {fatura.mes_ref}", "SKIP")
                    skip_total += 1
                    continue
                if not force and _master_ja_baixado(master, inst.instalacao, fatura.mes_ref, "COPEL"):
                    log(f"Ja no master: {inst.instalacao} / {fatura.mes_ref}", "SKIP")
                    skip_total += 1
                    continue

                time.sleep(1.5)
                pdf_temp = baixar_fatura(driver, fatura, temp_dir, handle_principal)
                if pdf_temp is None:
                    erro_total += 1
                    continue

                if master:
                    carimbo = master.consumir_carimbo()
                else:
                    carimbo = f"BB_{indice_local.proximo:07d}"

                # Move para servidor: DOWNLOAD COPEL / 03.2026 / MT / BB_xxxxx.pdf
                pasta_dest = COPEL_DIR / _mes_pasta(fatura.mes_ref) / _subpasta_tensao()
                _mkdir_seguro(pasta_dest)
                nome_final = f"{carimbo}.pdf"
                destino    = pasta_dest / nome_final

                try:
                    shutil.move(str(pdf_temp), str(destino))
                    log(f"PDF movido → {destino}", "FAT")
                except Exception as e:
                    log(f"Erro ao mover PDF: {e}", "ERR")
                    erro_total += 1
                    continue

                gravar_registro(master, indice_local,
                                inst.instalacao, fatura,
                                inst.cnpj, str(destino),
                                carimbo_pre=carimbo)
                ok_total += 1

        log("=" * 60)
        log(f"Concluido — baixados: {ok_total} | pulados: {skip_total} | erros: {erro_total}", "OK")
        log("=" * 60)
        return 0

    except KeyboardInterrupt:
        log("Interrompido pelo usuario.", "WARN")
        return 130
    except Exception:
        log(traceback.format_exc(), "ERR")
        return 1
    finally:
        try:
            driver.quit()
        except Exception:
            pass
        log("Driver encerrado.", "INFO")


if __name__ == "__main__":
    raise SystemExit(main())
