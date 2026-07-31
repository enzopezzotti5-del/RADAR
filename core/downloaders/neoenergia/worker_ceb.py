#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Neoenergia Selenium Worker - CEB (Neoenergia Brasília)
- Portal exclusivo: agenciavirtual.neoenergiabrasilia.com.br
- Login: campo CPF/CNPJ* (CNPJ da empresa) + CPF do Responsável* + Senha + reCAPTCHA manual
- Captcha: reCAPTCHA checkbox — operador resolve; worker aguarda ENTRAR ficar habilitado
- Estado único: BRASILIA (sem tela de seleção de estados)
- Pasta de destino: //10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD CEB
- Expõe: run_worker_ceb(jobs, shared_lock)
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
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import _venv_check  # noqa

import csv
import re
import time
import shutil
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set, Iterable

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from core.project_paths import resolve_indice_master_csv


def _carregar_master_modulo():
    import importlib.util
    script_dir = Path(__file__).resolve().parent
    candidatos = [
        script_dir.parent.parent.parent / "indice_master.py",
    ]
    for caminho in candidatos:
        if caminho.exists():
            print(f"[master] Encontrado em: {caminho}")
            spec = importlib.util.spec_from_file_location("indice_master", caminho)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod, caminho.parent
    print("[master] indice_master.py nao encontrado. Tentei:")
    for c in candidatos:
        print(f"         {c}")
    return None, None


_master_mod = None
_master_obj = None
_shared_lock = None
_progress_queue = None
_ucs_alvo: Optional[Set[str]] = None
_ignorar_indice = False

WORKER_NAME = "ceb"

BASE_DIR = Path(__file__).resolve().parent
DEV_DIR = BASE_DIR.parent

LOG_DIR = BASE_DIR / "logs"
TEMP_DOWNLOAD_DIR = BASE_DIR / "downloads_temp_ceb"
FAILED_LOGIN_FILE = BASE_DIR / "cnpjs_falha_login_ceb.csv"

FINAL_DOWNLOAD_ROOT = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD CEB")
INDEX_FILE = FINAL_DOWNLOAD_ROOT / "indice_downloads_ceb.csv"
MASTER_FILE = resolve_indice_master_csv(prefer_network=False)

# Portal exclusivo da Neoenergia Brasília (ex-CEB)
URL_PORTAL = "https://agenciavirtual.neoenergiabrasilia.com.br"

# Estado único — CEB opera apenas no Distrito Federal
ESTADO_CEB = "BRASILIA"

HEADLESS = False
ANO_MINIMO = 2026
PAGE_LOAD_TIMEOUT = 120
ELEMENT_TIMEOUT = 40

PAUSE_AFTER_LOGIN = 2.0
PAUSE_PDF_SETTLE = 1.5

# Timeout máximo para o operador resolver o captcha manualmente (segundos)
CAPTCHA_MANUAL_TIMEOUT = 180

MOTIVO_EMISSAO = "Comprovar Residência"
INDEX_START = 2_000_000  # Alinhado ao master sequencial global

TEMP_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

try:
    FINAL_DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

log_file = LOG_DIR / f"neoenergia_{WORKER_NAME}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(f"neoenergia_{WORKER_NAME}")


def _inicializar_master() -> None:
    global _master_mod, _master_obj, MASTER_FILE
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mod, _ = _carregar_master_modulo()

    if mod is None:
        log.warning("indice_master.py nao encontrado — usando fallback local")
        return

    _master_mod = mod
    MASTER_FILE = _master_mod.MASTER_FILE

    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        try:
            _master_obj = _master_mod.MasterIndice(MASTER_FILE)
        except Exception as e:
            log.error(f"Falha ao carregar master: {e} — usando fallback local")
            _master_obj = None
            return

    log.info(
        f"Master pronto: {_master_obj.proximo_carimbo} | {len(_master_obj._ja_baixados)} registros"
    )


@dataclass
class CredCEB:
    """
    Credencial de acesso ao portal CEB.
    Formulário: CPF/CNPJ* (cnpj) → CPF do Responsável* (cpf) → Senha → reCAPTCHA → ENTRAR
    """
    cnpj: str   # Vai no campo "CPF/CNPJ*"
    cpf: str    # Vai no campo "CPF do Responsável*"
    senha: str


@dataclass
class UcTela:
    codigo: str
    status: str
    texto: str
    estado: str
    eh_filha_coletiva: bool = False


@dataclass
class FaturaTela:
    indice: int
    referencia: str
    vencimento: str
    situacao: str
    data_emissao: str
    texto: str
    valor: str = ""
    minimo: bool = False


def fmt_doc(valor: str) -> str:
    return "".join(ch for ch in str(valor) if ch.isdigit())


def normalize_text(s: str) -> str:
    return " ".join((s or "").split()).strip()


def _normalizar_uc(codigo: str) -> str:
    digits = "".join(ch for ch in str(codigo) if ch.isdigit())
    return str(int(digits)) if digits else ""


def wait_ready(driver: webdriver.Chrome, timeout: int = 30) -> None:
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
    )


def save_screenshot(driver: webdriver.Chrome, name: str) -> None:
    log.debug("Screenshot desativado: %s", name)


def click_js(driver, el, label: str = "elemento") -> bool:
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        driver.execute_script("arguments[0].click();", el)
        log.debug(f"Clique JS: {label}")
        return True
    except Exception as e:
        log.warning(f"Falha clique JS em {label}: {e}")
        return False


def _clicar_elemento(driver, el, label: str = "") -> bool:
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        try:
            el.click()
        except Exception:
            driver.execute_script("arguments[0].click();", el)
        log.debug(f"Clique: {label}")
        return True
    except Exception:
        return False


def wait_clickable_and_click(driver, selectors, timeout: int = 20, description: str = "elemento") -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for by, sel in selectors:
            try:
                els = driver.find_elements(by, sel)
                for el in els:
                    if el.is_displayed():
                        if _clicar_elemento(driver, el, description):
                            return True
            except Exception:
                continue
        time.sleep(0.35)
    log.debug(f"wait_clickable_and_click: '{description}' nao encontrado em {timeout}s")
    return False


def find_first(driver, selectors, timeout: int = 15):
    for by, selector in selectors:
        try:
            return WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((by, selector))
            )
        except Exception:
            continue
    return None


def find_all_now(driver, selectors):
    for by, selector in selectors:
        els = driver.find_elements(by, selector)
        if els:
            return els
    return []


def referencia_to_folder(ref: str) -> str:
    ref = normalize_text(ref).upper()
    meses = {
        "JANEIRO": "01", "FEVEREIRO": "02", "MARCO": "03", "MARÇO": "03",
        "ABRIL": "04", "MAIO": "05", "JUNHO": "06", "JULHO": "07",
        "AGOSTO": "08", "SETEMBRO": "09", "OUTUBRO": "10",
        "NOVEMBRO": "11", "DEZEMBRO": "12",
    }
    m = re.search(r"([A-ZÁÇÃÉÊÍÓÔÚ]+)\s*/\s*(\d{4})", ref)
    if m:
        mes_num = meses.get(m.group(1), "00")
        return f"{m.group(2)}-{mes_num}"
    m2 = re.search(r"(\d{2})\s*/\s*(\d{4})", ref)
    if m2:
        return f"{m2.group(2)}-{m2.group(1)}"
    return "SEM_REFERENCIA"


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]+', "_", name)
    return re.sub(r"\s+", "_", name).strip("_")


def list_temp_pdf_files() -> Set[str]:
    return {p.name for p in TEMP_DOWNLOAD_DIR.glob("*.pdf")}


def wait_new_pdf(before: Set[str], timeout: int = 90) -> Optional[Path]:
    start = time.time()
    while time.time() - start < timeout:
        current = list_temp_pdf_files()
        new_files = current - before
        if new_files:
            newest = sorted(new_files)[-1]
            path = TEMP_DOWNLOAD_DIR / newest
            time.sleep(PAUSE_PDF_SETTLE)
            if path.exists() and path.stat().st_size > 0:
                return path
        time.sleep(0.8)
    return None


def parse_data_emissao(texto: str) -> str:
    texto_n = normalize_text(texto)
    m = re.search(r"data\s+emiss[aã]o[:\s]+(\d{2}/\d{2}/\d{2,4})", texto_n, re.IGNORECASE)
    if m:
        return m.group(1)
    datas = re.findall(r"\d{2}/\d{2}/\d{2,4}", texto_n)
    if len(datas) >= 2:
        return datas[1]
    if len(datas) == 1:
        return datas[0]
    return ""


def current_timestamp_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


INDEX_FIELDS = [
    "id", "cnpj", "cpf", "estado", "instalacao", "mes_referencia",
    "data_download", "data_emissao", "arquivo"
]


def _chave_indice(instalacao: str, mes_referencia: str) -> str:
    inst_norm = str(instalacao).strip().lstrip("0") or "0"
    ref_norm = normalize_text(mes_referencia).upper().strip()
    return f"{inst_norm}|{ref_norm}"


def carregar_ja_baixados() -> Set[str]:
    ja: Set[str] = set()
    if not INDEX_FILE.exists():
        return ja
    try:
        with open(INDEX_FILE, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                instalacao = row.get("instalacao", "")
                mes_ref = row.get("mes_referencia", "")
                if instalacao and mes_ref:
                    ja.add(_chave_indice(instalacao, mes_ref))
    except Exception as e:
        log.warning(f"Erro ao ler índice: {e}")
    log.info(f"Índice carregado: {len(ja)} registros já baixados")
    return ja


def ja_foi_baixado(ja_baixados: Set[str], instalacao: str, mes_referencia: str) -> bool:
    if _ignorar_indice:
        return False
    return _chave_indice(instalacao, mes_referencia) in ja_baixados


def next_index_id() -> str:
    """Retorna o próximo carimbo BB_XXXXXX via indice_master. Falha se master não carregado."""
    if _master_obj is not None:
        return _master_obj.consumir_carimbo()
    raise RuntimeError(
        "indice_master não carregado — impossível gerar carimbo seguro. "
        "Verifique a rede e o arquivo indice_master.py"
    )


def append_index_row(row: dict, ja_baixados: Set[str]) -> None:
    def _gravar():
        new_file = not INDEX_FILE.exists()
        try:
            FINAL_DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
            with open(INDEX_FILE, "a", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=INDEX_FIELDS)
                if new_file:
                    w.writeheader()
                w.writerow({k: row.get(k, "") for k in INDEX_FIELDS})
        except Exception as e:
            log.error(f"Erro ao gravar índice local: {e}")

        if _master_obj is not None:
            try:
                _master_obj.registrar(
                    indice_bb=row.get("id", ""),
                    sistema="NEOENERGIA_CEB",
                    uc=row.get("instalacao", ""),
                    mes_ref=row.get("mes_referencia", ""),
                    cnpj=row.get("cnpj", ""),
                    estado=row.get("estado", ""),
                    instalacao=row.get("instalacao", ""),
                    arquivo=row.get("arquivo", ""),
                )
            except Exception as e:
                log.warning(f"Erro ao gravar no master: {e}")

        chave = _chave_indice(row.get("instalacao", ""), row.get("mes_referencia", ""))
        ja_baixados.add(chave)

    _gravar()


def gravar_falha_login(cnpj: str, cpf: str, senha: str, motivo: str = "credencial_invalida") -> None:
    new_file = not FAILED_LOGIN_FILE.exists()
    try:
        with open(FAILED_LOGIN_FILE, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["CNPJ", "CPF", "SENHA", "MOTIVO", "DATA_FALHA"])
            if new_file:
                w.writeheader()
            w.writerow({"CNPJ": cnpj, "CPF": cpf, "SENHA": senha, "MOTIVO": motivo, "DATA_FALHA": current_timestamp_str()})
        log.info(f"  Falha de login registrada: CNPJ={cnpj} ({motivo})")
    except Exception as e:
        log.warning(f"  Não foi possível gravar falha de login: {e}")


def _find_cached_chromedriver() -> str | None:
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


def build_driver() -> webdriver.Chrome:
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")

    prefs = {
        "download.default_directory": str(TEMP_DOWNLOAD_DIR.resolve()),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True,
        "profile.default_content_setting_values.automatic_downloads": 1,
        "profile.content_settings.exceptions.automatic_downloads.*.setting": 1,
        "profile.default_content_setting_values.fonts": 2,
    }
    options.add_experimental_option("prefs", prefs)
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1366,768")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--blink-settings=imagesEnabled=false")
    options.add_argument("--blink-settings=cssImagesEnabled=false")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-sync")
    options.add_argument("--metrics-recording-only")
    options.add_argument("--mute-audio")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-proxy-server")
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)
    options.page_load_strategy = "normal"

    _cd = _find_cached_chromedriver()
    if _cd:
        from selenium.webdriver.chrome.service import Service as _Svc
        driver = webdriver.Chrome(service=_Svc(_cd), options=options)
    else:
        driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    driver.implicitly_wait(2)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


def _aguardar_spinner_sumir(driver, timeout: int = 30) -> None:
    try:
        WebDriverWait(driver, timeout).until(
            EC.invisibility_of_element_located((By.CSS_SELECTOR, "div.loading-spinner"))
        )
    except TimeoutException:
        pass


# ---------------------------------------------------------------------------
# reCAPTCHA via 2captcha (mesmo método do CEMIG)
# ---------------------------------------------------------------------------

CAPTCHA_API_KEY = "3ea89b196b365e9db9d0fd245c628e4f"


def _obter_sitekey_ceb(driver) -> Optional[str]:
    """
    Extrai a sitekey do reCAPTCHA da página CEB.
    Tenta: data-sitekey no DOM, URL do iframe recaptcha, ou grecaptcha JS.
    """
    # 1) atributo data-sitekey direto no div
    try:
        el = driver.find_element(By.CSS_SELECTOR, "[data-sitekey]")
        sk = el.get_attribute("data-sitekey")
        if sk:
            return sk
    except Exception:
        pass

    # 2) URL do iframe do recaptcha contém ?k=SITEKEY
    try:
        iframes = driver.find_elements(By.CSS_SELECTOR, "iframe[src*='recaptcha']")
        for iframe in iframes:
            src = iframe.get_attribute("src") or ""
            m = re.search(r"[?&]k=([^&]+)", src)
            if m:
                return m.group(1)
    except Exception:
        pass

    # 3) Extrai via JS do objeto grecaptcha
    try:
        sk = driver.execute_script("""
            try {
                var frames = document.querySelectorAll('iframe[src*="recaptcha"]');
                for (var f of frames) {
                    var m = f.src.match(/[?&]k=([^&]+)/);
                    if (m) return m[1];
                }
                var el = document.querySelector('[data-sitekey]');
                if (el) return el.getAttribute('data-sitekey');
            } catch(e) {}
            return null;
        """)
        if sk:
            return sk
    except Exception:
        pass

    return None


def _resolver_captcha_2captcha(site_url: str, sitekey: str) -> Optional[str]:
    """
    Resolve reCAPTCHA v2 via 2captcha API.
    Retorna token g-recaptcha-response ou None se falhar.
    """
    import urllib.request as _req
    import json as _json

    log.info(f"  [2captcha] Enviando — sitekey={sitekey[:20]}...")
    try:
        payload = _json.dumps({
            "clientKey": CAPTCHA_API_KEY,
            "task": {
                "type":       "RecaptchaV2TaskProxyless",
                "websiteURL": site_url,
                "websiteKey": sitekey,
            }
        }).encode()
        req = _req.Request(
            "https://api.2captcha.com/createTask",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        res = _json.loads(_req.urlopen(req, timeout=30).read())
        if res.get("errorId") != 0:
            log.warning(f"  [2captcha] createTask erro: {res}")
            return None
        tid = res["taskId"]
        log.info(f"  [2captcha] taskId={tid} — aguardando solução...")

        payload_get = _json.dumps({
            "clientKey": CAPTCHA_API_KEY,
            "taskId":    tid,
        }).encode()
        for _ in range(40):
            time.sleep(3)
            req2 = _req.Request(
                "https://api.2captcha.com/getTaskResult",
                data=payload_get,
                headers={"Content-Type": "application/json"},
            )
            res2 = _json.loads(_req.urlopen(req2, timeout=30).read())
            if res2.get("status") == "ready":
                token = res2.get("solution", {}).get("gRecaptchaResponse", "")
                log.info("  [2captcha] reCAPTCHA resolvido!")
                return token

        log.warning("  [2captcha] Timeout — sem solução em 120s")
        return None
    except Exception as e:
        log.warning(f"  [2captcha] Erro: {e}")
        return None


def _injetar_captcha_ceb(driver, token: str) -> None:
    """
    Injeta o token no campo oculto g-recaptcha-response e dispara o callback
    Angular/JS para que o formulário reconheça o captcha como resolvido.
    """
    driver.execute_script(f"""
        // Injeta no campo oculto padrão
        var el = document.getElementById('g-recaptcha-response');
        if (el) {{ el.innerHTML = '{token}'; el.value = '{token}'; }}

        // Dispara callback registrado via data-callback
        try {{
            var cb = document.querySelector('[data-callback]');
            if (cb) {{
                var fn = cb.getAttribute('data-callback');
                if (fn && window[fn]) window[fn]('{token}');
            }}
        }} catch(e) {{}}

        // Tenta callback via grecaptcha widget (índice 0)
        try {{
            if (window.___grecaptcha_cfg) {{
                var clients = window.___grecaptcha_cfg.clients;
                if (clients) {{
                    for (var k in clients) {{
                        var c = clients[k];
                        for (var p in c) {{
                            if (c[p] && typeof c[p].callback === 'function') {{
                                c[p].callback('{token}');
                            }}
                        }}
                    }}
                }}
            }}
        }} catch(e) {{}}
    """)
    time.sleep(0.5)


def _entrar_habilitado(driver) -> bool:
    """Retorna True se o botão ENTRAR estiver enabled e visível."""
    seletores = [
        (By.XPATH, "//button[normalize-space(.)='ENTRAR' and not(@disabled)]"),
        (By.XPATH, "//button[contains(normalize-space(.),'ENTRAR') and not(@disabled)]"),
        (By.XPATH, "//button[@type='submit' and not(@disabled)]"),
    ]
    for by, sel in seletores:
        try:
            els = driver.find_elements(by, sel)
            if any(e.is_displayed() and e.is_enabled() for e in els):
                return True
        except Exception:
            continue
    return False


def _resolver_recaptcha_ceb(driver) -> bool:
    """
    Tenta resolver o reCAPTCHA automaticamente via 2captcha.
    Fallback: aguarda resolução manual pelo operador (até CAPTCHA_MANUAL_TIMEOUT s).
    Retorna True se o botão ENTRAR ficou habilitado.
    """
    # Extrai sitekey da página
    sitekey = _obter_sitekey_ceb(driver)
    if sitekey:
        token = _resolver_captcha_2captcha(driver.current_url, sitekey)
        if token:
            _injetar_captcha_ceb(driver, token)
            # Aguarda até 10s o botão habilitar após injeção
            deadline = time.time() + 10
            while time.time() < deadline:
                if _entrar_habilitado(driver):
                    log.info("  reCAPTCHA injetado — botão ENTRAR habilitado")
                    return True
                time.sleep(0.8)
            log.warning("  Token injetado mas ENTRAR ainda desabilitado — tentando clique direto")
            return True  # prossegue mesmo assim; clique pode funcionar
        else:
            log.warning("  2captcha não retornou token — aguardando resolução manual")
    else:
        log.warning("  Sitekey não encontrada — aguardando resolução manual")

    # Fallback manual
    log.warning("=" * 60)
    log.warning("  [CEB] Resolva o reCAPTCHA manualmente no navegador!")
    log.warning(f"  Aguardando botão ENTRAR ficar habilitado (até {CAPTCHA_MANUAL_TIMEOUT}s)...")
    log.warning("=" * 60)
    print(f"\n{'=' * 60}")
    print("  [CEB] Resolva o reCAPTCHA manualmente no navegador!")
    print(f"  Timeout: {CAPTCHA_MANUAL_TIMEOUT} segundos")
    print(f"{'=' * 60}\n")

    start = time.time()
    while time.time() - start < CAPTCHA_MANUAL_TIMEOUT:
        if _entrar_habilitado(driver):
            log.info("  reCAPTCHA resolvido manualmente — ENTRAR habilitado")
            return True
        time.sleep(1.5)

    log.error(f"  reCAPTCHA não resolvido em {CAPTCHA_MANUAL_TIMEOUT}s")
    return False


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def _preencher_campo_angular(driver, el, valor: str, label: str) -> bool:
    """
    Preenche um campo de formulário Angular (ngxmaskinput / reactive forms).
    Usa send_keys character-by-character + dispara eventos nativos para que
    o Angular atualize o FormControl e os validadores.
    """
    try:
        # Garante foco e limpa
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        el.click()
        time.sleep(0.15)
        # Seleciona tudo e deleta (mais confiável que .clear() em campos mascarados)
        el.send_keys(Keys.CONTROL + "a")
        el.send_keys(Keys.DELETE)
        time.sleep(0.2)

        # Digita caractere a caractere (necessário para ngxmaskinput processar a máscara)
        for ch in valor:
            el.send_keys(ch)
            time.sleep(0.03)

        # Dispara eventos nativos que o Angular escuta para validar o FormControl
        driver.execute_script("""
            var el = arguments[0];
            ['input', 'change', 'blur', 'keyup'].forEach(function(ev) {
                el.dispatchEvent(new Event(ev, {bubbles: true}));
            });
        """, el)
        time.sleep(0.3)

        atual_digits = fmt_doc(el.get_attribute("value") or "")
        esperado_digits = fmt_doc(valor)
        if atual_digits and esperado_digits and atual_digits == esperado_digits:
            log.info(f"  Campo '{label}' preenchido: {el.get_attribute('value')}")
            return True

        log.warning(f"  Campo '{label}' valor inesperado: '{el.get_attribute('value')}' (esperado dígitos: {esperado_digits})")
        return True  # prossegue mesmo com divergência de máscara
    except Exception as e:
        log.warning(f"  Erro ao preencher '{label}': {e}")
        return False


def _localizar_campo(driver, seletores: list, label: str, timeout: int = 15):
    """Localiza o primeiro elemento visível e interativo da lista de seletores."""
    for by, sel in seletores:
        try:
            el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, sel)))
            if el and el.is_displayed():
                return el
        except Exception:
            continue
    log.error(f"  Campo '{label}' não encontrado (seletores: {seletores})")
    save_screenshot(driver, f"erro_campo_{label.replace(' ', '_')}")
    return None


def fazer_login(driver: webdriver.Chrome, cnpj: str, cpf: str, senha: str) -> bool:
    """
    Fluxo de login CEB:
      1. Abre o portal
      2. Clica em 'Acessar' / 'Conectar-se' se necessário
      3. Preenche campo CPF/CNPJ*  → cnpj
      4. Aguarda campo 'CPF do Responsável*' aparecer → preenche cpf
      5. Preenche Senha
      6. Avisa operador para resolver reCAPTCHA checkbox
      7. Aguarda botão ENTRAR ficar habilitado e clica
      8. Confirma redirect para home/imoveis
    """
    log.info(f"Login CEB: CNPJ={cnpj} CPF={cpf}")
    driver.get(URL_PORTAL)
    wait_ready(driver, ELEMENT_TIMEOUT)
    _aguardar_spinner_sumir(driver)

    # ── Abre o modal de login ─────────────────────────────────────────────────
    # O portal CEB mostra um botão "Acessar" na home que abre o modal com os campos
    botao_modal = [
        (By.XPATH, "//button[contains(normalize-space(.),'Acessar')]"),
        (By.XPATH, "//a[contains(normalize-space(.),'Acessar')]"),
        (By.XPATH, "//button[contains(normalize-space(.),'Entrar')]"),
        (By.XPATH, "//a[contains(normalize-space(.),'Entrar')]"),
        (By.XPATH, "//button[contains(normalize-space(.),'Login')]"),
    ]
    # Clica no botão que abre o modal; aguarda o campo CPF/CNPJ aparecer
    wait_clickable_and_click(driver, botao_modal, timeout=15, description="botão abrir modal login")

    # Aguarda o modal/campos estarem presentes antes de preencher
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input#cpfCnpjModal"))
        )
        log.info("  Modal de login aberto")
    except TimeoutException:
        log.warning("  input#cpfCnpjModal não apareceu — tentando continuar mesmo assim")

    time.sleep(0.4)

    # ── Campo 1: CPF/CNPJ* → id="cpfCnpjModal" ───────────────────────────────
    el_cnpj = _localizar_campo(driver, [
        (By.CSS_SELECTOR, "input#cpfCnpjModal"),
        (By.CSS_SELECTOR, "input[formcontrolname='CpfCnpj']"),
        (By.XPATH, "//input[contains(@placeholder,'CPF ou CNPJ')]"),
    ], "CPF/CNPJ", timeout=15)
    if not el_cnpj or not _preencher_campo_angular(driver, el_cnpj, cnpj, "CPF/CNPJ"):
        return False

    # ── Campo 2: CPF do Responsável* → id="rgResponsavelModal" ───────────────
    el_cpf = _localizar_campo(driver, [
        (By.CSS_SELECTOR, "input#rgResponsavelModal"),
        (By.CSS_SELECTOR, "input[formcontrolname='RgCpfResponsavel']"),
        (By.XPATH, "//input[contains(@placeholder,'CPF do responsável')]"),
    ], "CPF_Responsavel", timeout=15)
    if not el_cpf or not _preencher_campo_angular(driver, el_cpf, cpf, "CPF_Responsavel"):
        return False

    # ── Campo 3: Senha → id="senhaModal" ─────────────────────────────────────
    el_senha = _localizar_campo(driver, [
        (By.CSS_SELECTOR, "input#senhaModal"),
        (By.CSS_SELECTOR, "input[formcontrolname='Senha']"),
        (By.XPATH, "//input[@type='password']"),
    ], "Senha", timeout=15)
    if not el_senha or not _preencher_campo_angular(driver, el_senha, senha, "Senha"):
        return False

    # ── reCAPTCHA — resolve via 2captcha (fallback: manual) ──────────────────
    if not _entrar_habilitado(driver):
        if not _resolver_recaptcha_ceb(driver):
            return False

    # ── Clica em ENTRAR ───────────────────────────────────────────────────────
    botao_entrar = [
        (By.XPATH, "//button[normalize-space(.)='ENTRAR' and not(@disabled)]"),
        (By.XPATH, "//button[contains(normalize-space(.),'ENTRAR') and not(@disabled)]"),
        (By.XPATH, "//button[@type='submit' and not(@disabled)]"),
    ]
    if not wait_clickable_and_click(driver, botao_entrar, timeout=10, description="botão ENTRAR"):
        save_screenshot(driver, "erro_botao_entrar")
        log.error("  Botão ENTRAR não encontrado/habilitado")
        return False

    time.sleep(PAUSE_AFTER_LOGIN)
    _aguardar_spinner_sumir(driver, timeout=20)

    # ── Confirma redirect para área logada ────────────────────────────────────
    try:
        WebDriverWait(driver, 35).until(
            lambda d: (
                "/home" in d.current_url
                or "/imoveis" in d.current_url
                or _tela_tem_lista_ucs(d)
                or "selecionar" in d.current_url
                or "dashboard" in d.current_url
            )
        )
    except TimeoutException:
        erros = [
            "usuário ou senha inválidos",
            "usuario ou senha invalidos",
            "dados incorretos",
            "credenciais inválidas",
            "documento inválido",
            "cpf inválido",
            "cnpj inválido",
        ]
        body = normalize_text(driver.find_element(By.TAG_NAME, "body").text).lower()
        if any(e in body for e in erros):
            log.error(f"  Credencial inválida — CNPJ={cnpj} CPF={cpf}")
            return False
        save_screenshot(driver, "erro_login_timeout")
        log.error(f"  Timeout aguardando home após login — URL: {driver.current_url}")
        return False

    log.info(f"  Login CEB OK — URL: {driver.current_url}")
    return True


# ---------------------------------------------------------------------------
# UCs
# ---------------------------------------------------------------------------

def _tela_tem_lista_ucs(driver) -> bool:
    try:
        return len(driver.find_elements(By.XPATH, "//div[contains(@class,'box-imoveis')]")) > 0
    except Exception:
        return False


def _coletar_ucs_pagina(driver, estado: str, vistos: Set[str], cpf_atual: str = "") -> List[UcTela]:
    candidatos = find_all_now(driver, [
        (By.XPATH, "//div[contains(@class,'box-imoveis')]"),
        (By.XPATH, "//h6[contains(@class,'unidade-consumidora-title')]/ancestor::div[contains(@class,'box-imoveis')][1]"),
        (By.XPATH, "//mat-icon[contains(.,'arrow_forward')]/ancestor::div[contains(@class,'box-imoveis')][1]"),
    ])
    ucs: List[UcTela] = []
    cpf_digits = re.sub(r"\D", "", cpf_atual)

    for el in candidatos:
        try:
            texto = normalize_text(el.text)
            if not texto or len(texto) < 5:
                continue
            chave = texto[:180]
            if chave in vistos:
                continue
            vistos.add(chave)

            codigo = ""
            status = "LIGADA"

            _seletores_instalacao = [
                ".//span[contains(@class,'unidade-consumidora')]",
                ".//h6[contains(@class,'unidade-consumidora-title')]",
                ".//p[contains(@class,'instalacao')]",
                ".//span[contains(@class,'instalacao')]",
                ".//div[contains(@class,'instalacao')]",
                ".//small[contains(@class,'instalacao')]",
            ]
            for sel in _seletores_instalacao:
                try:
                    el_inst = el.find_element(By.XPATH, sel)
                    txt_inst = re.sub(r"\D", "", (el_inst.text or ""))
                    if 4 <= len(txt_inst) <= 15:
                        codigo = txt_inst
                        break
                except Exception:
                    continue

            if not codigo:
                for m in re.finditer(r"(\d{4,15})", texto):
                    candidato = m.group(1)
                    if cpf_digits and len(candidato) == 11 and candidato == cpf_digits:
                        continue
                    codigo = candidato
                    break

            try:
                span = el.find_element(By.XPATH, ".//span[contains(@class,'btn-status-imovel')]")
                cls = (span.get_attribute("class") or "").lower()
                if "desligada" in cls:
                    status = "DESLIGADA"
                elif "ligada" in cls:
                    status = "LIGADA"
                else:
                    txt_span = (span.text or "").strip().upper()
                    status = "DESLIGADA" if "DESLIGADA" in txt_span else "LIGADA"
            except Exception:
                txt_lower = texto.lower()
                if "desligada" in txt_lower:
                    status = "DESLIGADA"
                elif "ligada" in txt_lower:
                    status = "LIGADA"

            ucs.append(UcTela(codigo=codigo, status=status, texto=texto, estado=estado))
        except StaleElementReferenceException:
            continue
        except Exception:
            continue
    return ucs


def _proxima_pagina_ucs(driver) -> bool:
    seletores = [
        (By.XPATH, "//button[@aria-label='Next page']"),
        (By.XPATH, "//button[@aria-label='Próxima página']"),
        (By.XPATH, "//button[contains(@class,'mat-paginator-navigation-next') and not(@disabled)]"),
        (By.XPATH, "//button[.//mat-icon[contains(.,'navigate_next')] and not(@disabled)]"),
        (By.XPATH, "//li[contains(@class,'pagination-next')]/a"),
        (By.XPATH, "//a[contains(@aria-label,'Next')]"),
    ]
    for by, sel in seletores:
        try:
            btn = driver.find_element(by, sel)
            if btn.is_enabled() and btn.is_displayed():
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(0.5)
                return True
        except Exception:
            continue
    return False


def _voltar_pagina_1_ucs(driver) -> None:
    seletores_first = [
        (By.XPATH, "//button[@aria-label='First page']"),
        (By.XPATH, "//button[@aria-label='Primeira página']"),
        (By.XPATH, "//button[contains(@class,'mat-paginator-navigation-first') and not(@disabled)]"),
    ]
    seletores_prev = [
        (By.XPATH, "//button[@aria-label='Previous page']"),
        (By.XPATH, "//button[@aria-label='Página anterior']"),
        (By.XPATH, "//button[contains(@class,'mat-paginator-navigation-previous') and not(@disabled)]"),
        (By.XPATH, "//button[.//mat-icon[contains(.,'navigate_before')] and not(@disabled)]"),
    ]
    for by, sel in seletores_first:
        try:
            btn = driver.find_element(by, sel)
            if btn.is_enabled() and btn.is_displayed():
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(0.8)
                return
        except Exception:
            continue
    for _ in range(30):
        clicou = False
        for by, sel in seletores_prev:
            try:
                btn = driver.find_element(by, sel)
                if btn.is_enabled() and btn.is_displayed():
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(0.6)
                    clicou = True
                    break
            except Exception:
                continue
        if not clicou:
            break


def _ucs_pagina_atual(driver, estado: str, cpf: str = "") -> List[UcTela]:
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, "//div[contains(@class,'box-imoveis')]"))
        )
    except TimeoutException:
        pass
    return _coletar_ucs_pagina(driver, estado, vistos=set(), cpf_atual=cpf)


# ---------------------------------------------------------------------------
# Faturas
# ---------------------------------------------------------------------------

def _aguardar_tela_faturas_carregar(driver, timeout=20) -> bool:
    indicadores = [
        (By.XPATH, "//*[contains(.,'LISTA DE FATURAS')]"),
        (By.XPATH, "//*[contains(.,'Lista de faturas')]"),
        (By.XPATH, "//*[contains(.,'Histórico de faturas')]"),
        (By.XPATH, "//mat-expansion-panel"),
        (By.CSS_SELECTOR, "mat-expansion-panel"),
        (By.XPATH, "//*[contains(@class,'fatura-situacao')]"),
        (By.XPATH, "//*[contains(@class,'fatura-item')]"),
    ]
    deadline = time.time() + timeout
    while time.time() < deadline:
        for by, sel in indicadores:
            try:
                els = driver.find_elements(by, sel)
                if els:
                    time.sleep(0.5)
                    return True
            except Exception:
                continue
        time.sleep(0.4)
    return False


def abrir_tela_faturas(driver) -> bool:
    log.info("  Abrindo tela de faturas")
    seletores = [
        (By.XPATH, "//span[contains(normalize-space(.),'Faturas e 2ª via de faturas')]/ancestor::mat-card[1]"),
        (By.XPATH, "//span[contains(normalize-space(.),'Faturas e 2')]/ancestor::mat-card[1]"),
        (By.XPATH, "//img[contains(@src,'fatura.svg')]/ancestor::mat-card[1]"),
        (By.XPATH, "//mat-card[contains(@class,'card-neoenergia')][.//span[contains(.,'Faturas')]]"),
    ]
    for by, selector in seletores:
        try:
            el = WebDriverWait(driver, 4).until(EC.element_to_be_clickable((by, selector)))
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            driver.execute_script("arguments[0].click();", el)
            _aguardar_tela_faturas_carregar(driver, timeout=20)
            return True
        except Exception:
            continue
    try:
        by, selector = seletores[0]
        el = WebDriverWait(driver, 30).until(EC.element_to_be_clickable((by, selector)))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        driver.execute_script("arguments[0].click();", el)
        _aguardar_tela_faturas_carregar(driver, timeout=25)
        return True
    except Exception:
        pass
    save_screenshot(driver, "erro_abrir_faturas")
    return False


def _expandir_painel(driver, painel) -> None:
    try:
        header = painel.find_element(By.XPATH, ".//mat-expansion-panel-header")
        if header.get_attribute("aria-expanded") != "true":
            driver.execute_script("arguments[0].click();", header)
            WebDriverWait(driver, 6).until(
                lambda d: painel.find_element(
                    By.XPATH, ".//mat-expansion-panel-header"
                ).get_attribute("aria-expanded") == "true"
            )
            time.sleep(0.15)
    except Exception:
        pass


def listar_faturas_na_tela(driver) -> List[FaturaTela]:
    faturas: List[FaturaTela] = []
    SELETORES_PAINEIS = [
        (By.XPATH, "//mat-expansion-panel"),
        (By.XPATH, "//*[contains(@class,'mat-expansion-panel')]"),
        (By.XPATH, "//div[contains(@class,'fatura-item')]"),
        (By.XPATH, "//div[contains(@class,'fatura-row')]"),
        (By.CSS_SELECTOR, "mat-expansion-panel"),
    ]
    paineis = []
    seletor_usado = ""
    for by, sel in SELETORES_PAINEIS:
        try:
            encontrados = WebDriverWait(driver, 8).until(
                EC.presence_of_all_elements_located((by, sel))
            )
            if encontrados:
                paineis = encontrados
                seletor_usado = sel
                break
        except Exception:
            continue

    if not paineis:
        log.warning("  Nenhum painel de fatura encontrado")
        return faturas

    log.info(f"  Painéis de fatura encontrados ({len(paineis)}) via: {seletor_usado}")

    for i, painel in enumerate(paineis, start=1):
        try:
            _expandir_painel(driver, painel)
            texto = normalize_text(painel.text)
            if not texto:
                continue

            referencia = ""
            vencimento = ""
            valor = ""
            minimo = False
            data_emissao = parse_data_emissao(texto)

            m_ref = re.search(r"([A-ZÁÇÃÉÊÍÓÔÚ]+/\d{4}|\d{2}/\d{4})", texto, re.IGNORECASE)
            if m_ref:
                referencia = normalize_text(m_ref.group(1)).upper()

            datas = re.findall(r"\d{2}/\d{2}/\d{2,4}", texto)
            if datas:
                vencimento = datas[0]

            m_valor = re.search(r"R\$\s*([\d\.\,]+)", texto)
            if m_valor:
                valor = m_valor.group(1)

            situacao = "DESCONHECIDA"
            try:
                span_sit = painel.find_element(
                    By.XPATH,
                    ".//div[contains(@class,'fatura-situacao')]//span[contains(@class,'font-bold')]"
                )
                texto_sit = normalize_text(span_sit.text).lower()
                cls_span = (span_sit.get_attribute("class") or "").lower()
                if "vencer" in cls_span or "a vencer" in texto_sit:
                    situacao = "A VENCER"
                elif "paga" in cls_span or "pago" in cls_span or texto_sit in {"paga", "pago"}:
                    situacao = "PAGO"
                elif "vencida" in cls_span or "vencido" in cls_span or "vencida" in texto_sit or "vencido" in texto_sit:
                    situacao = "VENCIDA"
                elif "aberto" in cls_span or "pendente" in cls_span or "aberto" in texto_sit or "pendente" in texto_sit:
                    situacao = "EM ABERTO"
            except Exception:
                pass

            if situacao == "DESCONHECIDA":
                lower = texto.lower()
                if re.search(r"\bpaga\b", lower) or re.search(r"\bpago\b", lower):
                    situacao = "PAGO"
                elif re.search(r"\ba vencer\b", lower):
                    situacao = "A VENCER"
                elif re.search(r"\bvencid[ao]\b", lower):
                    situacao = "VENCIDA"
                elif re.search(r"\bem aberto\b", lower):
                    situacao = "EM ABERTO"
                elif re.search(r"\bpendente\b", lower):
                    situacao = "EM ABERTO"

            if "mínima" in texto.lower() or "minima" in texto.lower():
                minimo = True

            faturas.append(FaturaTela(
                indice=i,
                referencia=referencia,
                vencimento=vencimento,
                situacao=situacao,
                data_emissao=data_emissao,
                texto=texto,
                valor=valor,
                minimo=minimo,
            ))
        except Exception:
            continue

    log.info(f"  Total de faturas lidas na tela: {len(faturas)}")
    for f in faturas:
        log.info(
            f"  FAT[{f.indice}] ref={f.referencia} sit={f.situacao} "
            f"venc={f.vencimento} emissao={f.data_emissao} minimo={f.minimo} valor={f.valor}"
        )
    return faturas


def _ano_referencia_valido(referencia: str) -> bool:
    m = re.search(r"/(\d{4})", normalize_text(referencia))
    if not m:
        return False
    try:
        return int(m.group(1)) >= ANO_MINIMO
    except Exception:
        return False


def selecionar_faturas_pendentes(driver, ja_baixados: Set[str], cpf: str, instalacao: str) -> List[FaturaTela]:
    log.info(f"  Seleção de faturas para instalação={instalacao} cpf={cpf}")
    faturas = listar_faturas_na_tela(driver)
    if not faturas:
        log.info("  Nenhuma fatura encontrada na tela")
        return []

    elegiveis_avencer = []
    elegiveis_fallback = []
    for f in faturas:
        motivo = None
        if f.minimo:
            motivo = "fatura mínima"
        elif not _ano_referencia_valido(f.referencia):
            motivo = f"ano inválido ref={f.referencia}"
        elif ja_foi_baixado(ja_baixados, instalacao, f.referencia):
            motivo = "já consta no índice"

        if motivo:
            log.info(f"  DESCARTADA ref={f.referencia} | {motivo}")
            continue

        elegiveis_fallback.append(f)
        if f.situacao == "A VENCER":
            elegiveis_avencer.append(f)

    if not elegiveis_fallback:
        log.info(f"  Nenhuma fatura válida disponível para instalação {instalacao}")
        return []

    def _ordem_ref(ref: str):
        ref = normalize_text(ref).upper()
        m_num = re.search(r"(\d{2})/(\d{4})", ref)
        if m_num:
            return int(m_num.group(2)), int(m_num.group(1))
        meses = {
            "JANEIRO": 1, "FEVEREIRO": 2, "MARCO": 3, "MARÇO": 3,
            "ABRIL": 4, "MAIO": 5, "JUNHO": 6, "JULHO": 7,
            "AGOSTO": 8, "SETEMBRO": 9, "OUTUBRO": 10,
            "NOVEMBRO": 11, "DEZEMBRO": 12,
        }
        m_txt = re.search(r"([A-ZÁÇÃÉÊÍÓÔÚ]+)/(\d{4})", ref)
        if m_txt:
            return int(m_txt.group(2)), meses.get(m_txt.group(1), 0)
        return 0, 0

    pool_escolha = elegiveis_avencer or elegiveis_fallback
    escolhida = sorted(pool_escolha, key=lambda x: _ordem_ref(x.referencia), reverse=True)[0]
    if elegiveis_avencer:
        log.info(f"  Selecionando última A VENCER: {escolhida.referencia}")
    else:
        log.info(f"  Sem A VENCER; selecionando última disponível: {escolhida.referencia} | {escolhida.situacao}")

    try:
        ref = escolhida.referencia
        candidatos_checkbox = [
            f"//*[contains(normalize-space(.), '{ref}')]/preceding::input[contains(@class,'mat-checkbox-input')][1]",
            f"//*[contains(normalize-space(.), '{ref}')]/preceding::mat-checkbox[1]",
            f"//*[contains(normalize-space(.), '{ref}')]/ancestor::mat-expansion-panel[1]//input[contains(@class,'mat-checkbox-input')]",
            f"//*[contains(normalize-space(.), '{ref}')]/ancestor::mat-expansion-panel[1]//mat-checkbox",
        ]
        clicou = False
        for xp in candidatos_checkbox:
            try:
                els = driver.find_elements(By.XPATH, xp)
                for el in els:
                    try:
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                        time.sleep(0.15)
                        driver.execute_script("arguments[0].click();", el)
                        clicou = True
                        break
                    except Exception:
                        continue
                if clicou:
                    break
            except Exception:
                continue

        if not clicou:
            log.warning(f"  Não foi possível marcar checkbox da fatura {ref}")
            return []
        return [escolhida]
    except Exception as e:
        log.warning(f"  Falha ao selecionar fatura pendente: {e}")
        return []


def clicar_download_faturas(driver) -> bool:
    seletores = [
        (By.XPATH, "//mat-icon[@svgicon='download']"),
        (By.XPATH, "//mat-icon[@data-mat-icon-name='download']"),
        (By.XPATH, "//button[.//mat-icon[@svgicon='download']]"),
        (By.XPATH, "//button[.//mat-icon[@data-mat-icon-name='download']]"),
        (By.XPATH, "//button[@aria-label='Download da fatura']"),
        (By.XPATH, "//button[contains(@aria-label, 'Download')]"),
    ]
    for by, selector in seletores:
        try:
            elementos = WebDriverWait(driver, 25).until(
                EC.presence_of_all_elements_located((by, selector))
            )
            for el in elementos:
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                    time.sleep(0.4)
                    driver.execute_script("arguments[0].click();", el)
                    return True
                except Exception:
                    continue
        except Exception:
            continue
    save_screenshot(driver, "erro_botao_download_inicial")
    return False


def selecionar_motivo_emissao(driver, motivo: str = MOTIVO_EMISSAO) -> bool:
    selects = driver.find_elements(By.TAG_NAME, "select")
    for s in selects:
        try:
            sel = Select(s)
            for opt in sel.options:
                if motivo.lower() in opt.text.lower():
                    sel.select_by_visible_text(opt.text)
                    time.sleep(0.7)
                    return True
        except Exception:
            continue

    combos = [
        (By.XPATH, "//label[contains(., 'Motivo')]/following::*[self::div or self::input][1]"),
        (By.XPATH, "//*[contains(text(), 'Motivo')]/following::*[self::div or self::input][1]"),
        (By.XPATH, "//div[contains(@class,'select')]"),
        (By.XPATH, "//input[@role='combobox']"),
        (By.XPATH, f"//*[contains(text(), '{motivo}')]"),
    ]
    for by, selector in combos:
        try:
            el = WebDriverWait(driver, 20).until(EC.element_to_be_clickable((by, selector)))
            driver.execute_script("arguments[0].click();", el)
            time.sleep(0.7)
            opcao = find_first(driver, [(By.XPATH, f"//*[contains(text(), '{motivo}')]")], timeout=10)
            if opcao:
                driver.execute_script("arguments[0].click();", opcao)
                time.sleep(0.7)
                return True
        except Exception:
            continue
    save_screenshot(driver, "erro_motivo_emissao")
    return False


def confirmar_baixar(driver) -> bool:
    seletores = [
        (By.XPATH, "//button[@title='Baixar' and contains(@class,'btn-neodarkgreen')]"),
        (By.XPATH, "//button[contains(@class,'btn-neodarkgreen')][.//div[normalize-space()='BAIXAR']]"),
        (By.XPATH, "//button[@title='Baixar'][.//div[contains(normalize-space(.), 'BAIXAR')]]"),
        (By.XPATH, "//button[.//div[normalize-space()='BAIXAR']]"),
        (By.XPATH, "//button[contains(., 'BAIXAR')]"),
    ]
    for by, selector in seletores:
        try:
            btn = WebDriverWait(driver, 25).until(EC.element_to_be_clickable((by, selector)))
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            time.sleep(0.4)
            driver.execute_script("arguments[0].click();", btn)
            return True
        except Exception:
            continue
    save_screenshot(driver, "erro_confirmar_baixar_modal")
    return False


def confirmar_popup_sucesso(driver) -> bool:
    seletores = [
        (By.XPATH, "//button[contains(@class,'swal2-confirm') and normalize-space()='OK']"),
        (By.XPATH, "//button[contains(@class,'swal2-confirm') and contains(., 'OK')]"),
        (By.XPATH, "//button[contains(@class,'swal2-confirm')]"),
    ]
    for by, selector in seletores:
        try:
            btn = WebDriverWait(driver, 25).until(EC.element_to_be_clickable((by, selector)))
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            time.sleep(0.4)
            driver.execute_script("arguments[0].click();", btn)
            return True
        except Exception:
            continue
    return False


def mover_pdf_para_destino(pdf_temp: Path, referencia: str, carimbo: str) -> Path:
    mes_dir = FINAL_DOWNLOAD_ROOT / referencia_to_folder(referencia)
    mes_dir.mkdir(parents=True, exist_ok=True)
    destino = mes_dir / f"{carimbo}.pdf"
    if destino.exists():
        i = 2
        while True:
            alt = mes_dir / f"{carimbo}_{i}.pdf"
            if not alt.exists():
                destino = alt
                break
            i += 1
    shutil.move(str(pdf_temp), str(destino))
    return destino


# ---------------------------------------------------------------------------
# Processamento UC / estado
# ---------------------------------------------------------------------------

def entrar_na_uc_por_indice(driver, indice_uc: int) -> bool:
    log.info(f"  Entrando na UC índice {indice_uc}")
    try:
        cards = WebDriverWait(driver, 25).until(
            EC.presence_of_all_elements_located((By.XPATH, "//div[contains(@class,'box-imoveis')]"))
        )
        if indice_uc < 1 or indice_uc > len(cards):
            log.error(f"  Índice UC inválido: {indice_uc}/{len(cards)}")
            return False
        card = cards[indice_uc - 1]
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", card)
        try:
            btn = card.find_element(By.XPATH, ".//mat-icon[contains(.,'arrow_forward')]")
            if click_js(driver, btn, f"UC[{indice_uc}] arrow_forward"):
                time.sleep(1.5)
                return True
        except Exception:
            pass
        if click_js(driver, card, f"UC[{indice_uc}] card"):
            time.sleep(1.5)
            return True
    except Exception as e:
        log.error(f"  Falha ao entrar UC[{indice_uc}]: {e}")
    save_screenshot(driver, f"erro_entrar_uc_{indice_uc}")
    return False


def processar_uc(
    driver,
    indice_uc: int,
    cnpj: str,
    cpf: str,
    instalacao: str,
    ja_baixados: Set[str],
) -> int:
    log.info(f"  processar_uc() | cnpj={cnpj} cpf={cpf} | instalacao={instalacao} | indice_uc={indice_uc}")
    _prog("tela", etapa="lista_ucs", estado=ESTADO_CEB, uc=instalacao, cnpj=cnpj)

    if not entrar_na_uc_por_indice(driver, indice_uc):
        log.error(f"  Falha ao entrar na UC {instalacao}")
        return 0

    if not abrir_tela_faturas(driver):
        log.error(f"  Falha ao abrir tela de faturas da UC {instalacao}")
        return 0

    _prog("tela", etapa="faturas_2via", estado=ESTADO_CEB, uc=instalacao, cnpj=cnpj)
    selecionadas = selecionar_faturas_pendentes(driver, ja_baixados, cnpj, instalacao)
    for fat in selecionadas:
        _prog("fatura", uc=instalacao, estado=ESTADO_CEB, referencia=fat.referencia, situacao=fat.situacao)
    if not selecionadas:
        log.info(f"  Nenhuma fatura válida nova disponível na instalação {instalacao}")
        return 0

    if not clicar_download_faturas(driver):
        log.error(f"  Falha ao clicar no botão de download — instalação {instalacao}")
        return 0

    if not selecionar_motivo_emissao(driver, MOTIVO_EMISSAO):
        log.error(f"  Falha ao selecionar motivo — instalação {instalacao}")
        return 0

    before = list_temp_pdf_files()

    if not confirmar_baixar(driver):
        log.error(f"  Falha ao confirmar baixar — instalação {instalacao}")
        return 0

    pdf = wait_new_pdf(before, timeout=90)
    if not pdf:
        log.warning(f"  Nenhum novo PDF detectado — instalação {instalacao}")
        save_screenshot(driver, f"sem_pdf_{instalacao}")
        return 0

    confirmar_popup_sucesso(driver)

    f = selecionadas[0]

    if _shared_lock is not None:
        with _shared_lock:
            if ja_foi_baixado(ja_baixados, instalacao, f.referencia):
                try:
                    pdf.unlink(missing_ok=True)
                except Exception:
                    pass
                log.info(f"  Fatura já registrada por outro worker: {instalacao} | {f.referencia}")
                return 0

            try:
                mes_dir = FINAL_DOWNLOAD_ROOT / referencia_to_folder(f.referencia)
                mes_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                log.error(f"  Erro ao criar pasta de destino ({f.referencia}): {e}")
                try:
                    pdf.unlink(missing_ok=True)
                except Exception:
                    pass
                return 0

            carimbo = next_index_id()
            destino = mover_pdf_para_destino(pdf, f.referencia, carimbo)
            row = {
                "id": carimbo,
                "cnpj": fmt_doc(cnpj),
                "cpf": fmt_doc(cpf),
                "estado": ESTADO_CEB,
                "instalacao": instalacao,
                "mes_referencia": f.referencia,
                "data_download": current_timestamp_str(),
                "data_emissao": f.data_emissao,
                "arquivo": str(destino),
            }
            append_index_row(row, ja_baixados)
    else:
        if ja_foi_baixado(ja_baixados, instalacao, f.referencia):
            try:
                pdf.unlink(missing_ok=True)
            except Exception:
                pass
            return 0

        try:
            mes_dir = FINAL_DOWNLOAD_ROOT / referencia_to_folder(f.referencia)
            mes_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            log.error(f"  Erro ao criar pasta de destino ({f.referencia}): {e}")
            try:
                pdf.unlink(missing_ok=True)
            except Exception:
                pass
            return 0

        carimbo = next_index_id()
        destino = mover_pdf_para_destino(pdf, f.referencia, carimbo)
        row = {
            "id": carimbo,
            "cpf": fmt_doc(cpf),
            "estado": ESTADO_CEB,
            "instalacao": instalacao,
            "mes_referencia": f.referencia,
            "data_download": current_timestamp_str(),
            "data_emissao": f.data_emissao,
            "arquivo": str(destino),
        }
        append_index_row(row, ja_baixados)

    _prog("download_ok", uc=instalacao, estado=ESTADO_CEB, referencia=f.referencia, carimbo=carimbo, status="baixada")
    log.info(f"  Download concluído: instalação={instalacao} ref={f.referencia}")
    return 1


def processar_cred(info: CredCEB, ja_baixados: Set[str]) -> int:
    driver = None
    total_ok = 0

    try:
        driver = build_driver()

        if not fazer_login(driver, info.cnpj, info.cpf, info.senha):
            log.error(f"Login falhou: CNPJ={info.cnpj} CPF={info.cpf}")
            gravar_falha_login(info.cnpj, info.cpf, info.senha, motivo="credencial_invalida")
            return 0

        _aguardar_spinner_sumir(driver, timeout=15)

        # CEB: sem seleção de estado — vai direto para lista de UCs
        if not _tela_tem_lista_ucs(driver):
            # Tenta navegar para home/imoveis se não carregou automaticamente
            for url_tentativa in [
                URL_PORTAL.rstrip("/") + "/#/home/imoveis",
                URL_PORTAL.rstrip("/") + "/#/home",
            ]:
                try:
                    driver.get(url_tentativa)
                    wait_ready(driver, ELEMENT_TIMEOUT)
                    _aguardar_spinner_sumir(driver, timeout=10)
                    try:
                        WebDriverWait(driver, 15).until(lambda d: _tela_tem_lista_ucs(d))
                        break
                    except TimeoutException:
                        continue
                except Exception:
                    continue

        if not _tela_tem_lista_ucs(driver):
            log.warning(f"  Lista de UCs não encontrada após login — URL: {driver.current_url}")
            save_screenshot(driver, f"sem_lista_ucs_{info.cpf}")
            return 0

        # Itera todas as UCs
        _voltar_pagina_1_ucs(driver)
        pagina = 1
        paginas_sem_ligadas = 0

        while True:
            log.info(f"  === Página {pagina} de UCs ===")
            ucs = _ucs_pagina_atual(driver, ESTADO_CEB, cpf=info.cpf)

            if not ucs:
                log.info(f"  Página {pagina} sem UCs — encerrando")
                break

            ligadas = [u for u in ucs if u.status == "LIGADA"]

            if _ucs_alvo is not None:
                alvo_pagina = [u for u in ucs if _normalizar_uc(u.codigo or "") in _ucs_alvo]
                log.info(f"  Página {pagina}: {len(ucs)} UC(s), {len(alvo_pagina)} alvo(s) [filtro ativo]")
                _prog("ucs_pagina", estado=ESTADO_CEB, pagina=pagina, total_ucs=len(ucs), ligadas=len(ligadas), cpf=info.cpf)
                if not alvo_pagina:
                    if not _proxima_pagina_ucs(driver):
                        break
                    pagina += 1
                    continue
                processar_lista = alvo_pagina
            else:
                log.info(f"  Página {pagina}: {len(ucs)} UC(s), {len(ligadas)} LIGADA(s)")
                _prog("ucs_pagina", estado=ESTADO_CEB, pagina=pagina, total_ucs=len(ucs), ligadas=len(ligadas), cpf=info.cpf)
                if not ligadas:
                    paginas_sem_ligadas += 1
                    if paginas_sem_ligadas >= 2:
                        log.info(f"  {paginas_sem_ligadas} páginas sem ligadas — encerrando")
                        break
                    if not _proxima_pagina_ucs(driver):
                        break
                    pagina += 1
                    continue
                processar_lista = ligadas

            paginas_sem_ligadas = 0

            for pos, uc in enumerate(processar_lista, start=1):
                indice_na_pagina = ucs.index(uc) + 1
                log.info(f"  --- UC[pág{pagina}:{pos}] codigo={uc.codigo or 'SEM_CODIGO'} status={uc.status} ---")
                baixados_uc = 0
                try:
                    _prog("uc_inicio", uc=uc.codigo or "", estado=ESTADO_CEB, i=pos, total=len(processar_lista), pagina=pagina)
                    baixados_uc = processar_uc(
                        driver=driver,
                        indice_uc=indice_na_pagina,
                        cnpj=info.cnpj,
                        cpf=info.cpf,
                        instalacao=uc.codigo or "",
                        ja_baixados=ja_baixados,
                    )
                except Exception as e:
                    log.error(f"  Erro UC pag{pagina}:{pos}: {e}")
                    save_screenshot(driver, f"erro_uc_p{pagina}_{pos}")

                total_ok += baixados_uc
                _prog("uc_fim", uc=uc.codigo or "", estado=ESTADO_CEB, pagina=pagina, pdfs=baixados_uc,
                      status=("download_ok" if baixados_uc > 0 else "sem_fatura"))

                # Volta para lista de UCs
                voltou = False
                for _ in range(4):
                    try:
                        driver.back()
                        time.sleep(0.4)
                        _aguardar_spinner_sumir(driver, timeout=8)
                        if driver.find_elements(By.XPATH, "//div[contains(@class,'box-imoveis')]"):
                            voltou = True
                            break
                    except Exception:
                        break

                if not voltou:
                    try:
                        driver.get(URL_PORTAL.rstrip("/") + "/#/home/imoveis")
                        wait_ready(driver, ELEMENT_TIMEOUT)
                        _aguardar_spinner_sumir(driver, timeout=15)
                        WebDriverWait(driver, 20).until(
                            EC.presence_of_element_located((By.XPATH, "//div[contains(@class,'box-imoveis')]"))
                        )
                        voltou = True
                    except Exception:
                        pass

                if not voltou:
                    log.warning(f"  Não voltou para lista de UCs após UC pag{pagina}:{pos} — abortando página")
                    save_screenshot(driver, f"nao_voltou_lista_p{pagina}_{pos}")
                    break

                if pagina > 1:
                    try:
                        WebDriverWait(driver, 15).until(
                            EC.presence_of_all_elements_located((By.XPATH, "//div[contains(@class,'box-imoveis')]"))
                        )
                    except Exception:
                        pass
                    _aguardar_spinner_sumir(driver, timeout=10)
                    ok_paginou = True
                    for n_pag in range(pagina - 1):
                        time.sleep(0.6)
                        if not _proxima_pagina_ucs(driver):
                            ok_paginou = False
                            break
                        time.sleep(0.5)
                    if not ok_paginou:
                        log.warning("  Reposicionamento falhou — reiniciando da página 1")
                        pagina = 1
                        continue

            if not _proxima_pagina_ucs(driver):
                log.info(f"  Sem próxima página após página {pagina} — fim")
                break
            pagina += 1

    except Exception as e:
        log.error(f"Erro geral CNPJ={info.cnpj}: {e}")
        if driver:
            save_screenshot(driver, f"erro_cnpj_{info.cnpj}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    return total_ok


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _limpar_pasta_temp() -> None:
    try:
        if TEMP_DOWNLOAD_DIR.exists():
            for p in TEMP_DOWNLOAD_DIR.glob("*"):
                if p.is_file():
                    p.unlink(missing_ok=True)
    except Exception as e:
        log.warning(f"Falha ao limpar temp dir: {e}")


def _coletar_jobs(jobs: Iterable) -> List[CredCEB]:
    """
    Converte lista de jobs (dicts ou CredCEB) em lista de CredCEB.
    Cada combinação única (cnpj, cpf, senha) gera uma sessão de login separada.
    """
    vistos: set[tuple] = set()
    saida: List[CredCEB] = []
    for item in jobs:
        if isinstance(item, CredCEB):
            chave = (fmt_doc(item.cnpj), fmt_doc(item.cpf), item.senha.strip())
            if chave not in vistos:
                vistos.add(chave)
                saida.append(item)
            continue

        cnpj = fmt_doc(item.get("cnpj", ""))
        cpf = fmt_doc(item.get("cpf", ""))
        senha = (item.get("senha", "") or "").strip()

        if len(cnpj) == 14 and len(cpf) == 11 and senha:
            chave = (cnpj, cpf, senha)
            if chave not in vistos:
                vistos.add(chave)
                saida.append(CredCEB(cnpj=cnpj, cpf=cpf, senha=senha))

    return saida


def _prog(tipo: str, **kwargs) -> None:
    if _progress_queue is not None:
        try:
            _progress_queue.put_nowait({"worker": WORKER_NAME, "tipo": tipo, **kwargs})
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Ponto de entrada público
# ---------------------------------------------------------------------------

def run_worker_ceb(jobs, shared_lock, progress_queue=None, ucs_alvo=None, ignorar_indice: bool = False) -> int:
    global _shared_lock, _progress_queue, _ucs_alvo, _ignorar_indice
    _shared_lock = shared_lock
    _progress_queue = progress_queue
    _ignorar_indice = bool(ignorar_indice)
    if ucs_alvo is not None:
        _ucs_alvo = {_normalizar_uc(u) for u in ucs_alvo if str(u).strip()}
        log.info(f"[CEB] Filtro de UCs ativo: {len(_ucs_alvo)} UC(s) alvo")
    else:
        _ucs_alvo = None
    if _ignorar_indice:
        log.info("[CEB] Redownload ativo: índice de já baixados será ignorado")

    _inicializar_master()
    if _master_obj is None:
        raise RuntimeError(
            "indice_master não disponível — impossível gerar carimbos seguros. "
            "Verifique a rede e o arquivo indice_master.py"
        )

    inicio = datetime.now()
    log.info(f"WORKER CEB | início {inicio.strftime('%H:%M:%S')} | master=OK")

    _limpar_pasta_temp()

    creds = _coletar_jobs(jobs)
    total_creds = len(creds)

    ja_baixados = carregar_ja_baixados()
    _prog("inicio", total=total_creds)

    total_ok = 0
    for i, info in enumerate(creds, start=1):
        log.info("")
        log.info(f"[{i}/{total_creds}] CNPJ={info.cnpj} CPF={info.cpf}")
        _prog("cnpj_inicio", i=i, total=total_creds, cnpj=info.cnpj, estados=ESTADO_CEB)

        baixados_antes = total_ok
        total_ok += processar_cred(info, ja_baixados)
        baixados_agora = total_ok - baixados_antes

        _prog("cnpj_fim", i=i, total=total_creds, cnpj=info.cnpj,
              estados=ESTADO_CEB, pdfs=baixados_agora, total_pdfs=total_ok)

    fim = datetime.now()
    log.info("")
    log.info(f"Fim: {fim.strftime('%H:%M:%S')} | duração {str(fim - inicio).split('.')[0]} | PDFs {total_ok}")
    log.info(f"Log local: {log_file}")
    _prog("fim", total_pdfs=total_ok, duracao=str(fim - inicio).split(".")[0])
    return total_ok


# ---------------------------------------------------------------------------
# Execução direta (modo standalone)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from multiprocessing import Lock

    _xlsx_path = FINAL_DOWNLOAD_ROOT / "NOVO ACESSO - NEOENERGIA CEB.xlsx"

    if not _xlsx_path.exists():
        print(f"[CEB] Excel não encontrado: {_xlsx_path}")
        raise SystemExit(1)

    try:
        import openpyxl as _xl
    except ImportError:
        print("[CEB] openpyxl não instalado. Execute: pip install openpyxl")
        raise SystemExit(1)

    _wb = _xl.load_workbook(str(_xlsx_path), read_only=True)
    _ws = _wb.active

    # Lê cabeçalhos e monta lista bruta
    _headers = None
    _jobs_raw: list[dict] = []
    for _row in _ws.iter_rows(values_only=True):
        if _headers is None:
            _headers = [str(c).strip() if c else "" for c in _row]
            continue
        _d = dict(zip(_headers, _row))
        # Colunas do Excel: CNPJ | CPF DO ACESSO | SENHA DE ACESSO (espaço no final)
        _cnpj_v  = fmt_doc(str(_d.get("CNPJ", "") or ""))
        _cpf_v   = fmt_doc(str(_d.get("CPF DO ACESSO", "") or ""))
        _senha_v = str(_d.get("SENHA DE ACESSO ", "") or "").strip()
        if not _senha_v:
            _senha_v = str(_d.get("SENHA DE ACESSO", "") or "").strip()
        if len(_cnpj_v) == 14 and len(_cpf_v) == 11 and _senha_v:
            _jobs_raw.append({"cnpj": _cnpj_v, "cpf": _cpf_v, "senha": _senha_v})

    if not _jobs_raw:
        print("[CEB] Nenhuma credencial válida (CNPJ+CPF+Senha) encontrada no Excel.")
        raise SystemExit(0)

    # Deduplica por (CNPJ, CPF, Senha) — cada combinação = uma sessão de login
    _vistos: set = set()
    _jobs: list[dict] = []
    for _j in _jobs_raw:
        _k = (_j["cnpj"], _j["cpf"], _j["senha"])
        if _k not in _vistos:
            _vistos.add(_k)
            _jobs.append(_j)

    print(f"[CEB] {len(_jobs)} sessão(ões) únicas de login "
          f"({len(_jobs_raw)} linhas no Excel)")

    run_worker_ceb(_jobs, shared_lock=Lock())
