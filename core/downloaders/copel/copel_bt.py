#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
copel_bt.py  —  Downloader COPEL Baixa Tensão via Selenium
============================================================
Fluxo por UC:
  1. Login único: CNPJ 00000000000191 / Senha Acao*2024
  2. Filtra UC na tabela → clica "Selecionar"
  3. Clica "ACESSAR TODOS OS SERVIÇOS +"
  4. Clica "Histórico de pagamento"
  5. Para cada fatura de 2026 não baixada:
       a. Clica "2 via"
       b. Modal → "Fazer download da 2ª via"
       c. Aguarda PDF na pasta temp
       d. Move para servidor, grava no índice local + master
  6. Volta para seleção de UC (próxima iteração)
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
import tempfile
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
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from core.project_paths import resolve_copel_accessos_xls

try:
    from core.metrics.radar_metrics import emit_outcome as _emit_copel_outcome
    def _emit(outcome: str, *, instalacao: str, mes_ref: str, carimbo: str = "") -> None:
        _emit_copel_outcome(outcome, utility="COPEL BT", account_id=instalacao,
                            competence=mes_ref, invoice_id=carimbo or mes_ref)
except Exception:
    def _emit(outcome: str, **_: str) -> None:  # type: ignore[misc]
        pass


# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

CNPJ_LOGIN  = "00000000000191"   # só números, campo formulario:numDoc
SENHA_LOGIN = "Acao*2024"

ANO_MINIMO  = 2026               # baixa apenas faturas deste ano em diante

# API key do 2captcha.com para resolucao automatica de reCAPTCHA.
# Deixe vazio ("") para resolver manualmente.
TWOCAPTCHA_API_KEY = os.environ.get("TWOCAPTCHA_API_KEY", "3ea89b196b365e9db9d0fd245c628e4f")

ROOT_DIR    = Path("//10.10.250.21/Energia/ARQUIVOS ENZO")
COPEL_DIR   = ROOT_DIR / "DOWNLOAD COPEL"
ACESSOS_XLS = COPEL_DIR / "acessos_copel.xlsx"
ACESSOS_XLS_LOCAL = Path(__file__).resolve().parents[3] / "acessos_copel.xlsx"
INDEX_LOCAL = COPEL_DIR / "indice_faturas_copel_bt.csv"

_MASTER_SERVIDOR = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/indice_master.py")
MASTER_PY_LOCAL  = Path(__file__).resolve().parent.parent.parent / "indice_master.py"

URL_LOGIN = "https://www.copel.com/avaweb/paginaLogin/login.jsf"


def autonomous_exit_code(downloaded: int, errors: int) -> int:
    if errors:
        return 1
    return 0 if downloaded else 3

# Tempos (segundos)
T_LOGIN    = 60
T_EL       = 15
T_MODAL    = 4   # timeout por seletor no modal de download (botão aparece rápido ou não aparece)
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
    cnpj:       str   # CNPJ do cliente (ex: 00.000.000/2545-33)


@dataclass
class FaturaHistorico:
    data_ri:          int    # índice da linha no DOM (data-ri)
    mes_ref:          str    # "03-2026" (normalizado para índice)
    mes_ref_raw:      str    # "03/2026" (como aparece na tela)
    nr_fatura:        str    # "20263441768201"
    situacao:         str    # "Quitada", "Em aberto", etc.
    data_vencimento:  str    # "20/03/2026"
    valor:            str    # "685,30"
    link_via_id:      str    # "formHistoricoPagto:dtListaHistoricoPagto:0:j_idt79"


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
# ÃNDICE MASTER
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
                log("filelock não instalado — pip install filelock", "WARN")
            master = mod.MasterIndice(mod.MASTER_FILE)
            origem = "local" if Path(caminho).resolve() == MASTER_PY_LOCAL.resolve() else "servidor"
            log(
                f"Master: {len(master._ja_baixados)} registros | próximo: {master.proximo_carimbo} | fonte: {origem}",
                "OK",
            )
            return master
        except Exception as e:
            log(f"Falha ao carregar master: {e}", "WARN")
    log("indice_master.py não encontrado — usando índice local apenas", "WARN")
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
# ÍNDICE LOCAL COPEL BT
# =============================================================================

class IndiceLocal:
    """
    Índice local de faturas COPEL BT.
    O contador 'proximo' NÃO tem valor fixo — é inicializado a partir do
    master (quando disponível) ou dos registros já existentes no CSV.
    """

    def __init__(self):
        self.memoria: Set[Tuple[str, str]] = set()  # (instalacao, mes_ref)
        self.proximo: int = 0                        # definido em main() via master
        self._carregar()

    def _carregar(self) -> None:
        if not _exists_unc(INDEX_LOCAL):
            _mkdir_seguro(COPEL_DIR)
            with open(INDEX_LOCAL, "w", encoding="utf-8-sig", newline="") as f:
                csv.writer(f).writerow(INDEX_FIELDS)
            log("Índice local criado (vazio)", "INFO")
            return

        try:
            with open(INDEX_LOCAL, encoding="utf-8-sig", newline="") as f:
                conteudo = f.read()
        except Exception as e:
            log(f"Índice local inacessível: {e}", "WARN")
            return

        import io
        for row in csv.DictReader(io.StringIO(conteudo)):
            inst = row.get("INSTALACAO", "").strip()
            ref  = row.get("MES_REF", "").strip()
            if inst and ref:
                self.memoria.add((inst, ref))
            # Atualiza contador a partir dos registros existentes
            m = re.search(r"(\d+)$", row.get("INDICE", ""))
            if m:
                self.proximo = max(self.proximo, int(m.group(1)) + 1)

        log(f"Índice local: {len(self.memoria)} registros | próximo local={self.proximo or '(do master)'}", "OK")

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

def carregar_instalacoes() -> List[Instalacao]:
    """Lê acessos_copel.xlsx → filtra COPEL + Baixa Tensão."""
    candidatos = [resolve_copel_accessos_xls(COPEL_DIR), ACESSOS_XLS, ACESSOS_XLS_LOCAL]
    ultimo_erro: Exception | None = None
    df = None
    planilha_usada: Path | None = None
    for planilha in candidatos:
        try:
            if not planilha.exists():
                continue
            df = pd.read_excel(planilha, dtype=str)
            df.columns = [c.strip() for c in df.columns]
            planilha_usada = planilha
            break
        except Exception as e:
            ultimo_erro = e
            continue
    if df is None or planilha_usada is None:
        if ultimo_erro is not None:
            log(f"Erro ao ler planilha COPEL (rede/local): {ultimo_erro}", "ERR")
        else:
            log(f"Planilha não encontrada: {ACESSOS_XLS} nem fallback {ACESSOS_XLS_LOCAL}", "ERR")
        return []
    log(f"Planilha usada: {planilha_usada}", "INFO")

    col_conc   = next((c for c in df.columns if "concess"  in c.lower()), None)
    col_tensao = next((c for c in df.columns if "tens"     in c.lower()), None)
    col_inst   = next((c for c in df.columns if "instalac" in c.lower()), None)
    col_cnpj   = next((c for c in df.columns if c.upper() == "CNPJ"),     None)
    col_medi   = next((c for c in df.columns if "medidor"  in c.lower()), None)
    col_pref   = next((c for c in df.columns if "prefixo"  in c.lower()), None)

    faltando = [n for n, c in [("Concessionária", col_conc), ("Tensão", col_tensao),
                                ("Instalacao", col_inst), ("CNPJ", col_cnpj)] if c is None]
    if faltando:
        log(f"Colunas não encontradas: {faltando}", "ERR")
        return []

    mask = (df[col_conc].str.upper().str.contains("COPEL", na=False) &
            df[col_tensao].str.upper().str.contains("BAIXA", na=False))
    df_bt = df[mask].copy()
    log(f"Instalações COPEL BT: {len(df_bt)}", "OK")

    resultado = []
    for _, row in df_bt.iterrows():
        inst = str(row.get(col_inst, "") or "").strip()
        if not inst:
            continue
        resultado.append(Instalacao(
            medidor    = str(row.get(col_medi, "") or "").strip(),
            prefixo    = str(row.get(col_pref, "") or "").strip(),
            instalacao = inst,
            cnpj       = str(row.get(col_cnpj, "") or "").strip(),
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


def build_driver(temp_dl: Path) -> webdriver.Chrome:
    """
    Usa perfil Chrome persistente do usuario (Default) para que o Google
    reconheca o browser como humano e nao exija captcha de imagens.
    O perfil acumula cookies/historico entre execucoes — quanto mais usado,
    menor a chance de captcha.
    IMPORTANTE: feche o Chrome manualmente antes de rodar este script.
    """
    _mkdir_seguro(temp_dl)

    # Perfil temporário local para não depender do perfil padrão do Windows.
    profile_root = Path(__file__).resolve().parent / "chrome_profiles"
    _mkdir_seguro(profile_root)
    perfil_dir = Path(tempfile.mkdtemp(prefix="copel_bt_", dir=str(profile_root)))

    opts = Options()
    opts.add_argument(f"--user-data-dir={perfil_dir}")
    opts.add_experimental_option("prefs", {
        "download.default_directory":                    str(temp_dl.resolve()),
        "download.prompt_for_download":                  False,
        "download.directory_upgrade":                    True,
        "plugins.always_open_pdf_externally":            True,
        "profile.default_content_setting_values.automatic_downloads": 1,
    })
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--no-proxy-server")
    opts.add_argument("--no-restore-last-session")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--remote-debugging-port=0")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    _cd = _find_cached_chromedriver()
    if _cd:
        driver = webdriver.Chrome(service=Service(_cd), options=opts)
    else:
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            import os as _os
            _proxy_keys = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
            _proxy_bak = {k: _os.environ.pop(k, None) for k in _proxy_keys}
            try:
                driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
            finally:
                for k, v in _proxy_bak.items():
                    if v is not None:
                        _os.environ[k] = v
        except BaseException:
            driver = webdriver.Chrome(options=opts)

    driver.set_page_load_timeout(30)
    driver.execute_cdp_cmd("Page.setDownloadBehavior", {
        "behavior":     "allow",
        "downloadPath": str(temp_dl.resolve()),
    })
    driver._profile_dir = perfil_dir
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


def _resolver_captcha(driver: webdriver.Chrome, timeout: int = 120) -> None:
    """
    Se reCAPTCHA estiver visivel na pagina:
      - Se TWOCAPTCHA_API_KEY configurado: resolve automaticamente via 2captcha.com
      - Caso contrario: pausa ate resolucao manual (ate timeout segundos)
    """
    try:
        iframes = driver.find_elements(By.CSS_SELECTOR, "iframe[src*='recaptcha']")
        if not iframes:
            return
    except Exception:
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
    """Aguarda spinner/overlay do PrimeFaces sumir."""
    try:
        WebDriverWait(driver, t).until_not(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".ui-blockui, .ui-widget-overlay"))
        )
    except Exception:
        pass
    time.sleep(0.3)


# =============================================================================
# FLUXO DE LOGIN
# =============================================================================

def fazer_login(driver: webdriver.Chrome) -> Optional[str]:
    """
    Login no portal COPEL.
    Retorna a URL da tela de seleção de UC (str) em caso de sucesso, ou None.
    """
    log(f"Abrindo: {URL_LOGIN}", "INFO")
    driver.get(URL_LOGIN)
    _resolver_captcha(driver)

    try:
        campo_cnpj = W(driver, By.ID, "formulario:numDoc", T_LOGIN)
        campo_cnpj.clear()
        campo_cnpj.send_keys(CNPJ_LOGIN)

        campo_senha = W(driver, By.ID, "formulario:pass")
        campo_senha.clear()
        campo_senha.send_keys(SENHA_LOGIN)

        try:
            btn = driver.find_element(By.CSS_SELECTOR, "input[type='submit'], button[type='submit']")
            clicar(driver, btn, label="submit login")
        except NoSuchElementException:
            campo_senha.send_keys(Keys.RETURN)

        # Aguarda tabela de UCs (sinal inequÃ­voco de login OK)
        W_pres(driver, By.ID, "formLogin:tbUcs", T_LOGIN)

        erros = driver.find_elements(By.CSS_SELECTOR, ".ui-messages-error, .erro-login")
        if erros and any(e.text.strip() for e in erros):
            for e in erros:
                log(f"Erro login: {e.text.strip()}", "ERR")
            return None

        url_selecao = driver.current_url
        log(f"Login OK — URL seleção UC: {url_selecao}", "OK")
        return url_selecao

    except TimeoutException:
        log("Timeout aguardando tabela de UCs", "ERR")
        return None
    except Exception as e:
        log(f"Exceção no login: {e}", "ERR")
        return None


# =============================================================================
# SELEÃ‡ÃƒO DE UC
# =============================================================================

def selecionar_uc(driver: webdriver.Chrome, instalacao: str) -> bool:
    """
    Na tabela formLogin:tbUcs:
      1. Digita o nÃºmero da instalaÃ§Ã£o no filtro (AJAX filtra a tabela)
      2. Aguarda AJAX estabilizar
      3. Localiza linha com texto exato
      4. Dispara o onclick do link PrimeFaces via JavaScript (evita intercept)
    """
    FILTRO_SEL = "input[id^='formLogin:tbUcs:'][id$=':filter']"
    CORPO_ID  = "formLogin:tbUcs_data"
    # Índice 0 = coluna UC ANEEL (nova); índice 1 = coluna UC antiga.
    # O sufixo j_idtXX muda entre sessões mas a ordem das colunas é fixa.
    FILTRO_ANTIGA_IDX = 1

    log(f"Selecionando UC: {instalacao}", "INFO")
    try:
        W_vis(driver, By.ID, CORPO_ID)

        WebDriverWait(driver, T_EL).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, FILTRO_SEL))
        )
        filtros = driver.find_elements(By.CSS_SELECTOR, FILTRO_SEL)

        if len(filtros) <= FILTRO_ANTIGA_IDX:
            log(f"Filtro UC antiga (idx {FILTRO_ANTIGA_IDX}) ausente — {len(filtros)} filtro(s) encontrado(s)", "ERR")
            return False

        filtro = filtros[FILTRO_ANTIGA_IDX]
        fid = filtro.get_attribute("id") or "(sem id)"
        # O filtro PrimeFaces busca pelo valor sem zeros à esquerda.
        inst_digitada = instalacao.lstrip("0") or "0"

        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", filtro)
        filtro.click()
        filtro.send_keys(Keys.CONTROL, "a")
        filtro.send_keys(Keys.DELETE)
        filtro.send_keys(inst_digitada)
        driver.execute_script(
            """
            arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
            arguments[0].dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: '0' }));
            arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
            """,
            filtro,
        )
        _aguardar_spinner(driver, 10)
        time.sleep(1.0)
        log(f"Filtro UC antiga: {fid} | digitado: {inst_digitada}", "DBG")

        corpo = driver.find_element(By.ID, CORPO_ID)
        linhas = corpo.find_elements(By.XPATH, ".//tr[@data-ri]")

        # Célula exibe com zeros ("0073844390"); compara normalizado sem zeros
        link_alvo = None
        for linha in linhas:
            tds = linha.find_elements(By.TAG_NAME, "td")
            if not tds:
                continue
            if any(td.text.strip().lstrip("0") == inst_digitada for td in tds):
                links = linha.find_elements(By.CSS_SELECTOR, "a[aria-label='Selecionar']")
                if links:
                    link_alvo = links[0]
                break

        if link_alvo is None:
            log(f"UC {instalacao}: não encontrada ou sem botão Selecionar (PT/DS)", "WARN")
            return False

        # Dispara onclick via JS — PrimeFaces.ab não responde ao .click() normal
        onclick = link_alvo.get_attribute("onclick") or ""
        if "PrimeFaces.ab" in onclick:
            # Remove o "return false;" e executa o PrimeFaces.ab(...)
            js_call = onclick.replace("return false;", "").strip().rstrip(";")
            driver.execute_script(js_call)
            log(f"Clique PrimeFaces disparado para UC {instalacao}", "DBG")
        else:
            # Fallback para clique direto
            driver.execute_script("arguments[0].click();", link_alvo)

        # Aguarda navegaÃ§Ã£o: tabela de seleÃ§Ã£o some do DOM
        WebDriverWait(driver, T_EL).until_not(
            EC.presence_of_element_located((By.ID, CORPO_ID))
        )
        log(f"UC {instalacao} selecionada. URL: {driver.current_url}", "OK")
        return True

    except TimeoutException:
        log(f"Timeout selecionando UC {instalacao}", "ERR")
        return False
    except Exception as e:
        log(f"Exceção selecionando UC {instalacao}: {e}", "ERR")
        return False


# =============================================================================
# NAVEGAÇÃO: TODOS OS SERVIÇOS → HISTÓRICO DE PAGAMENTO
# =============================================================================

def acessar_historico(driver: webdriver.Chrome) -> bool:
    """
    Após selecionar UC:
      1. Clica "ACESSAR TODOS OS SERVIÇOS +" — reforça até 3x se necessário
      2. Clica link "Histórico de pagamento"
      3. Aguarda tabela até 60s
    """
    T_HIST = 60   # timeout estendido para esta tela

    try:
        # — Passo 1: ACESSAR TODOS OS SERVIÇOS + — tentativa com retorno se necessário
        # Tenta até 3 vezes com intervalo crescente
        btn_todos = None
        for tentativa in range(1, 4):
            try:
                btn_todos = WebDriverWait(driver, T_HIST).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "button.seleciona"))
                )
                break
            except TimeoutException:
                log(f"Botão 'ACESSAR TODOS' não encontrado (tentativa {tentativa}/3)", "WARN")
                time.sleep(2)

        if btn_todos is None:
            log("Botão 'ACESSAR TODOS OS SERVIÇOS +' não apareceu", "ERR")
            return False

        for tentativa in range(1, 4):
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn_todos)
            driver.execute_script("arguments[0].click();", btn_todos)
            log(f"Clicado 'ACESSAR TODOS OS SERVIÇOS +' (tentativa {tentativa})", "DBG")
            _aguardar_spinner(driver)

            # Verifica se o link de histórico apareceu
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, "a[href*='historicoPagamento']")
                    )
                )
                break   # link apareceu, segue
            except TimeoutException:
                if tentativa < 3:
                    log("Link histórico não apareceu, reforçando clique...", "WARN")
                    time.sleep(2)
                else:
                    log("Link 'Histórico de pagamento' não apareceu após 3 tentativas", "ERR")
                    return False

        # — Passo 2: Histórico de pagamento — navega direto pela URL —
        base = "/".join(driver.current_url.split("/")[:3])   # "https://www.copel.com"
        driver.get(base + "/avaweb/paginas/historicoPagamento.jsf")
        log("Navegando direto para historicoPagamento.jsf", "DBG")

        # â"€â"€ Passo 3: Aguarda tabela atÃ© 60s â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
        WebDriverWait(driver, T_HIST).until(
            EC.presence_of_element_located(
                (By.ID, "formHistoricoPagto:dtListaHistoricoPagto_data")
            )
        )
        log(f"Histórico carregado. URL: {driver.current_url}", "OK")
        return True

    except TimeoutException:
        log("Timeout acessando histórico de pagamento", "ERR")
        return False
    except Exception as e:
        log(f"Exceção em acessar_historico: {e}", "ERR")
        return False


# =============================================================================
# LEITURA DO HISTÃ"RICO
# =============================================================================

def ler_historico(driver: webdriver.Chrome) -> List[FaturaHistorico]:
    """
    Lê todas as linhas visíveis da tabela de histórico de pagamento.
    Retorna apenas faturas com ano >= ANO_MINIMO.
    (Lê somente a primeira página — normalmente suficiente para o ano corrente)
    """
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

                # Extrai texto removendo o <span class="ui-column-title"> (mobile label)
                def _txt(td):
                    spans = td.find_elements(By.TAG_NAME, "span")
                    full = td.text.strip()
                    if spans:
                        label = spans[0].text.strip()
                        return full[len(label):].strip()
                    return full

                mes_raw   = _txt(tds[0])   # "03/2026"
                nr_fatura = _txt(tds[1])   # "20263441768201"
                situacao  = _txt(tds[2])   # "Quitada"
                # tds[3] = Origem
                dt_venc   = _txt(tds[4])   # "20/03/2026"
                # tds[5] = Data pagamento
                valor     = _txt(tds[6])   # "685,30"

                # Verifica se link "2 via" existe nessa linha
                links_via = linha.find_elements(By.CSS_SELECTOR, "a.ui-commandlink")
                if not links_via:
                    continue
                link_id = links_via[0].get_attribute("id") or ""

                # Normaliza mes_ref: "03/2026" â†' "03-2026"
                mes_ref = mes_raw.replace("/", "-") if "/" in mes_raw else mes_raw

                # Filtra por ano
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
                log(f"Erro ao ler linha histórico: {e}", "WARN")
                continue

        log(f"Histórico: {len(faturas)} fatura(s) encontrada(s) ≥ {ANO_MINIMO}", "INFO")
        return faturas

    except Exception as e:
        log(f"Erro ao ler histórico: {e}", "ERR")
        return []


# =============================================================================
# DOWNLOAD DE 2ª VIA
# =============================================================================

def _pdfs_em(pasta: Path) -> Set[Path]:
    """Retorna conjunto de Paths dos PDFs existentes, usando mtime como dedup."""
    return {p for p in pasta.glob("*.pdf")}

def _voltar_janela_principal(driver: webdriver.Chrome, handle_principal: str) -> None:
    """Fecha abas extras abertas pelo download e volta para a janela principal."""
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


def _clicar_botao_download_modal(driver: webdriver.Chrome, timeout: int = T_MODAL) -> bool:
    """
    Clica no botao final de download do modal da COPEL.

    O portal alterna entre IDs dinamicos e diferentes rotulos no botao final.
    """
    seletores = [
        (By.ID, "frmModalSegundaVia:j_idt124", "ID padrao"),
        (By.XPATH, "//button[.//span[normalize-space()='Fazer download da 2ª via']]", "texto 2a via (ª)"),
        (By.XPATH, "//button[.//span[normalize-space()='Fazer download da 2a via']]", "texto 2a via (a)"),
        (By.XPATH, "//span[normalize-space()='Fazer download da 2ª via']/ancestor::button[1]", "span 2a via (ª)"),
        (By.XPATH, "//span[normalize-space()='Fazer download da 2a via']/ancestor::button[1]", "span 2a via (a)"),
        (By.XPATH, "//button[.//span[normalize-space()='Download']]", "texto download"),
        (By.XPATH, "//button[.//span[normalize-space()='Baixar']]", "texto baixar"),
        (By.XPATH, "//div[contains(@class,'ui-dialog') and not(contains(@style,'display: none'))]//button[.//span[contains(@class,'ui-button-text')]]", "unico botao visivel no modal"),
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
            log(f"Botão de download do modal clicado ({rotulo} | {metodo})", "DBG")
            _aguardar_spinner(driver)
            return True
        except Exception as exc:
            ultimo_erro = exc
            continue

    if ultimo_erro:
        log(f"Não foi possível clicar no botão de download do modal: {ultimo_erro}", "WARN")
    return False


def baixar_fatura(driver: webdriver.Chrome, fatura: FaturaHistorico,
                  temp_dir: Path, handle_principal: str) -> Optional[Path]:
    """
    Clica no link "2 via" → aguarda modal → clica download.
    Fecha qualquer aba extra aberta pelo download e volta para a janela principal.
    Retorna o Path do PDF baixado (em temp_dir) ou None em caso de falha.
    """
    log(f"Baixando {fatura.mes_ref} — fatura {fatura.nr_fatura} (link_id={fatura.link_via_id!r})", "DL")
    # Snapshot: nomes + mtime para detectar arquivo novo OU substituÃ­do
    pdfs_antes = {p: p.stat().st_mtime for p in temp_dir.glob("*.pdf")}

    try:
        # Clica no link "2 via" via JS (PrimeFaces.addSubmitParam + submit)
        # Busca pelo ID armazenado; se vazio (portal COPEL omite id em faturas "Em aberto"),
        # usa fallback por data-ri para não depender do atributo id.
        if fatura.link_via_id:
            link_via = W(driver, By.ID, fatura.link_via_id)
        else:
            CORPO_HIST = "formHistoricoPagto:dtListaHistoricoPagto_data"
            corpo_hist = W_vis(driver, By.ID, CORPO_HIST)
            linha_hist = corpo_hist.find_element(
                By.XPATH, f".//tr[@data-ri='{fatura.data_ri}']"
            )
            links_cmd = linha_hist.find_elements(By.CSS_SELECTOR, ".ui-commandlink")
            if not links_cmd:
                raise TimeoutException(
                    f"Nenhum link de download (data-ri={fatura.data_ri}, link_id vazio)"
                )
            link_via = links_cmd[0]
            log(f"Link 2ª via encontrado por data-ri={fatura.data_ri} (sem id)", "DBG")
        onclick = link_via.get_attribute("onclick") or ""
        if "PrimeFaces" in onclick or "submit" in onclick:
            js_call = onclick.replace("return false;", "").strip().rstrip(";")
            driver.execute_script(js_call)
        else:
            driver.execute_script("arguments[0].click();", link_via)
        _aguardar_spinner(driver)

        # Aguarda modal — pode ser "2ª via" (normal) ou "1ª via" (primeira emissão)
        # Modal 2ª via: id="frmModalSegundaVia:j_idt124"
        # Modal 1ª via: checkbox "Li e concordo" + botão "Emitir"
        try:
            if not _clicar_botao_download_modal(driver):
                raise TimeoutException("Botão final de download da 2ª via não encontrado")
            log("Modal 2ª via — download disparado", "DBG")
        except TimeoutException:
            # Modal de 1ª via (AVA):
            #   Checkbox PrimeFaces: <span class="ui-chkbox-icon ui-icon ui-icon-blank ui-c">
            #   Botão emitir:        <span class="ui-button-text ui-c">Emitir</span>
            log("Modal 2ª via não encontrado — tentando modal 1ª via (AVA)", "WARN")
            try:
                # Marca o checkbox apenas se estiver desmarcado (ui-icon-blank)
                # Se já vier marcado na abertura do modal, ignora e segue
                try:
                    chk_span = WebDriverWait(driver, 3).until(
                        EC.element_to_be_clickable(
                            (By.CSS_SELECTOR, "span.ui-chkbox-icon.ui-icon-blank")
                        )
                    )
                    driver.execute_script("arguments[0].click();", chk_span)
                    time.sleep(0.5)
                    log("Modal 1ª via — checkbox marcado", "DBG")
                except TimeoutException:
                    log("Modal 1ª via — checkbox já marcado, seguindo", "DBG")

                # Botão "Emitir"
                btn_emitir = WebDriverWait(driver, T_EL).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//button[.//span[@class='ui-button-text ui-c'"
                                   " and normalize-space(text())='Emitir']]")
                    )
                )
                driver.execute_script("arguments[0].click();", btn_emitir)
                log("Modal 1ª via — botão Emitir clicado", "DBG")
                _aguardar_spinner(driver)

                if not _clicar_botao_download_modal(driver):
                    raise TimeoutException("Botão final de download não encontrado no modal 1ª via")
                log("Modal 1ª via — download disparado", "DBG")
            except Exception as e_1via:
                log(f"Falha no modal 1ª via: {e_1via}", "ERR")
                _voltar_janela_principal(driver, handle_principal)
                return None

        # Aguarda PDF aparecer ou ser substituÃ­do em temp_dir
        # Detecta: arquivo novo OU arquivo existente com mtime diferente
        deadline = time.time() + T_DOWNLOAD
        pdf_novo: Optional[Path] = None
        while time.time() < deadline:
            crdowns = list(temp_dir.glob("*.crdownload"))
            if not crdowns:
                pdfs_agora = {p: p.stat().st_mtime for p in temp_dir.glob("*.pdf")}
                # Arquivo novo
                novos = set(pdfs_agora) - set(pdfs_antes)
                # Arquivo com mesmo nome mas mtime diferente (sobrescrito)
                atualizados = {
                    p for p in pdfs_agora
                    if p in pdfs_antes and pdfs_agora[p] != pdfs_antes[p]
                }
                candidatos = novos | atualizados
                if candidatos:
                    # Pega o mais recente
                    pdf_novo = max(candidatos, key=lambda p: p.stat().st_mtime)
                    break
            time.sleep(1)

        if pdf_novo is None:
            log(f"Timeout aguardando PDF da fatura {fatura.nr_fatura}", "ERR")
            _voltar_janela_principal(driver, handle_principal)
            return None

        log(f"PDF recebido: {pdf_novo.name}", "OK")

        # Fecha modal se ainda estiver aberto
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
        log(f"Exceção ao baixar fatura {fatura.nr_fatura}: {e}", "ERR")
        _voltar_janela_principal(driver, handle_principal)
        return None


# =============================================================================
# GRAVAÃ‡ÃƒO NOS ÃNDICES
# =============================================================================

def _mes_pasta(mes_ref: str) -> str:
    """'03-2026' → '03.2026'"""
    return mes_ref.replace("-", ".")

def _subpasta_tensao() -> str:
    """COPEL BT → sempre 'BT'."""
    return "BT"

def gravar_registro(master, indice_local: IndiceLocal,
                    instalacao: str, fatura: FaturaHistorico,
                    cnpj: str, arquivo_final: str,
                    carimbo_pre: str = "") -> None:
    """
    Grava no master (se disponÃ­vel) e no Ã­ndice local.
    carimbo_pre: carimbo jÃ¡ consumido antes (para nÃ£o consumir duas vezes).
    """
    carimbo = carimbo_pre  # jÃ¡ foi consumido pelo chamador

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
# MAIN
# =============================================================================

def _parse_args():
    import argparse
    p = argparse.ArgumentParser(description="COPEL BT Downloader")
    p.add_argument("--ucs", default="", help="Instalações separadas por vírgula (filtra a lista)")
    p.add_argument("--mes-ref", default="", help="Referências separadas por vírgula, ex: 02-2026,03-2026")
    p.add_argument("--force", action="store_true", help="Ignora checks de já baixado")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    ucs_filtro = {u.strip() for u in args.ucs.split(",") if u.strip()} if args.ucs else set()
    refs_filtro = {r.strip() for r in args.mes_ref.split(",") if r.strip()} if args.mes_ref else set()
    force = args.force

    log("=" * 60)
    log("COPEL BT — Downloader iniciado")
    if ucs_filtro:
        log(f"Filtro UCs    : {sorted(ucs_filtro)}", "INFO")
    if refs_filtro:
        log(f"Filtro refs   : {sorted(refs_filtro)}", "INFO")
    if force:
        log("Modo FORCE    : ignorando checks de já baixado", "WARN")
    log("=" * 60)

    instalacoes = carregar_instalacoes()
    if not instalacoes:
        log("Nenhuma instalação COPEL BT. Abortando.", "ERR")
        return 1

    if ucs_filtro:
        instalacoes = [i for i in instalacoes if i.instalacao in ucs_filtro]
        log(f"Após filtro UCs: {len(instalacoes)} instalação(ões)", "INFO")
        nao_encontradas = ucs_filtro - {i.instalacao for i in instalacoes}
        if nao_encontradas:
            log(f"UCs não encontradas em acessos_copel.xlsx: {sorted(nao_encontradas)}", "WARN")

    if not instalacoes:
        log("Nenhuma instalação após filtro. Abortando.", "ERR")
        return 1
    log(f"Total instalações BT: {len(instalacoes)}", "INFO")

    # â"€â"€ Ãndices â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    indice_local = IndiceLocal()
    master       = _carregar_master()

    # Sincroniza contador local com master (se local estÃ¡ vazio ou atrasado)
    if master:
        proximo_master = master._proximo_num
        if indice_local.proximo < proximo_master:
            log(f"Contador local ajustado: {indice_local.proximo} → {proximo_master} (do master)", "INFO")
            indice_local.proximo = proximo_master

    # â"€â"€ Driver â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    script_dir = Path(__file__).resolve().parent
    temp_dir   = script_dir / "downloads_temp_copel"
    _mkdir_seguro(temp_dir)
    driver = build_driver(temp_dir)

    try:
        # â"€â"€ LOGIN â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
        url_selecao = fazer_login(driver)
        if not url_selecao:
            log("Falha no login. Encerrando.", "ERR")
            return 1

        # Handle da janela principal â€" salvo uma vez, nunca muda
        handle_principal = driver.current_window_handle

        # â"€â"€ LOOP POR INSTALAÃ‡ÃƒO â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
        ok_total = 0
        skip_total = 0
        erro_total = 0

        for idx, inst in enumerate(instalacoes, 1):
            log(f"[{idx}/{len(instalacoes)}] Instalação: {inst.instalacao}", "INFO")

            # â"€â"€ Garante janela principal ativa antes de navegar â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
            _voltar_janela_principal(driver, handle_principal)

            # â"€â"€ Volta para tela de seleÃ§Ã£o de UC â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
            if idx > 1:
                try:
                    driver.get(url_selecao)
                    W_pres(driver, By.ID, "formLogin:tbUcs_data", T_EL)
                except Exception:
                    log("Tabela de UCs não recarregou — tentando novo login", "WARN")
                    url_selecao = fazer_login(driver)
                    if not url_selecao:
                        log("Falha no re-login. Abortando.", "ERR")
                        break
                    handle_principal = driver.current_window_handle

            # â"€â"€ Seleciona a UC â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
            if not selecionar_uc(driver, inst.instalacao):
                erro_total += 1
                continue

            # â"€â"€ Acessa HistÃ³rico de Pagamento â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
            if not acessar_historico(driver):
                erro_total += 1
                continue

            # â"€â"€ LÃª faturas â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
            faturas = ler_historico(driver)
            if not faturas:
                log(f"Nenhuma fatura ≥ {ANO_MINIMO} para {inst.instalacao}", "SKIP")
                skip_total += 1
                continue

            if refs_filtro:
                faturas = [f for f in faturas if f.mes_ref in refs_filtro]
                if not faturas:
                    log(f"Nenhuma fatura nas refs {refs_filtro} para {inst.instalacao}", "SKIP")
                    skip_total += 1
                    continue

            # â"€â"€ Download de cada fatura nÃ£o baixada â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
            for fatura in faturas:
                # Dedup
                if not force and indice_local.ja_baixado(inst.instalacao, fatura.mes_ref):
                    log(f"Já baixado: {inst.instalacao} / {fatura.mes_ref}", "SKIP")
                    _emit("skipped_existing", instalacao=inst.instalacao, mes_ref=fatura.mes_ref)
                    skip_total += 1
                    continue
                if not force and _master_ja_baixado(master, inst.instalacao, fatura.mes_ref, "COPEL"):
                    log(f"Já no master: {inst.instalacao} / {fatura.mes_ref}", "SKIP")
                    _emit("skipped_existing", instalacao=inst.instalacao, mes_ref=fatura.mes_ref)
                    skip_total += 1
                    continue

                # Baixa PDF
                time.sleep(0.5)   # delay antes de cada download
                pdf_temp = baixar_fatura(driver, fatura, temp_dir, handle_principal)
                if pdf_temp is None:
                    erro_total += 1
                    continue

                # Consome carimbo antes de mover para usar no nome do arquivo
                if master:
                    carimbo = master.consumir_carimbo()
                else:
                    carimbo = f"BB_{indice_local.proximo:07d}"

                # Move para servidor: DOWNLOAD COPEL / 03.2026 / BT / BB_xxxxx.pdf
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

                # Grava nos Ã­ndices (carimbo jÃ¡ consumido)
                gravar_registro(master, indice_local,
                                inst.instalacao, fatura,
                                inst.cnpj, str(destino),
                                carimbo_pre=carimbo)
                _emit("downloaded", instalacao=inst.instalacao, mes_ref=fatura.mes_ref, carimbo=carimbo)
                ok_total += 1

        # â"€â"€ Resumo final â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
        log("=" * 60)
        log(f"Concluído — baixados: {ok_total} | pulados: {skip_total} | erros: {erro_total}", "OK")
        log("=" * 60)
        return autonomous_exit_code(ok_total, erro_total)

    except KeyboardInterrupt:
        log("Interrompido pelo usuário.", "WARN")
        return 130
    except Exception:
        log(traceback.format_exc(), "ERR")
        return 1
    finally:
        try:
            driver.quit()
            shutil.rmtree(getattr(driver, "_profile_dir", None) or "", ignore_errors=True)
        except Exception:
            pass
        log("Driver encerrado.", "INFO")


if __name__ == "__main__":
    raise SystemExit(main())

