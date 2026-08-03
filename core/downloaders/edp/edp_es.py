"""
EDP Espirito Santo - Downloader automatico de faturas.
Selenium com Chrome usando perfil real do usuario.
Uso: python edp_es.py [--mes MM] [--ano AAAA] [--inicio N]
"""

import sys
import ctypes as _ctypes
from pathlib import Path
# Isola do CTRL_C_EVENT do Windows (evita KeyboardInterrupt em Selenium/SSL)
if sys.platform == "win32":
    try:
        _ctypes.windll.kernel32.SetConsoleCtrlHandler(None, True)
    except Exception:
        pass
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import _venv_check  # noqa

print("[EDP] Carregando modulos...", flush=True)

import re
import shutil
import subprocess
import time
import logging
import argparse
import json
import os
import unicodedata
from datetime import datetime, date

import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

print("[EDP] Modulos carregados.", flush=True)

# =============================================================================
import importlib.util as _ilu

_MASTER_SERVER = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/indice_master.py")
_MASTER_LOCAL  = Path(__file__).resolve().parent.parent.parent / "indice_master.py"

def _carregar_master():
    for caminho in [_MASTER_SERVER, _MASTER_LOCAL]:
        try:
            if caminho.exists():
                spec = _ilu.spec_from_file_location("indice_master", str(caminho))
                mod  = _ilu.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod
        except Exception:
            continue
    return None

_master_mod = _carregar_master()
if _master_mod:
    _master_obj = _master_mod.MasterIndice(_master_mod.MASTER_FILE)
    print(f"[master] Carregado: {len(_master_obj._ja_baixados)} registros | proximo: {_master_obj.proximo_carimbo}", flush=True)
else:
    _master_obj = None
    print("[master] Nao encontrado - nomes de arquivo sem carimbo BB", flush=True)


# =============================================================================

PLANILHA_SENHAS = "//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD EDP ESCELSA/senhas_edp_escelsa.xlsx"
PASTA_DOWNLOAD  = "//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD EDP ESCELSA"
PASTA_AUDITORIA = "//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD EDP ESCELSA"

# Perfil real do Chrome - abra chrome://version e veja "Caminho do perfil"
# O ultimo componente do caminho e o CHROME_PROFILE (ex: Default, Profile 1)
CHROME_USER_DATA = r"C:\Users\Revit\AppData\Local\Google\Chrome\User Data"
CHROME_PROFILE   = "Default"

# Pasta temporaria local (download do Chrome vai aqui, depois move para servidor)
TEMP_DIR = Path(__file__).resolve().parent / "downloads_temp_edp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

URL_PORTAL = "https://www.edponline.com.br/grandes-clientes"
URL_LOGIN  = "https://www.edponline.com.br/grandes-clientes/login"

TIMEOUT   = 30
T_CLICK   = 1.2
T_UC      = 1.5
SVG_VERDE = "minha-instalacao-verde-pequeno.svg"

MESES_PT = {
    "JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4,
    "MAI": 5, "JUN": 6, "JUL": 7, "AGO": 8,
    "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12,
}

# =============================================================================

log = logging.getLogger("edp")
log.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
_sh  = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)
log.addHandler(_sh)


def _add_file_log():
    try:
        Path(PASTA_AUDITORIA).mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(
            Path(PASTA_AUDITORIA) / f"edp_{datetime.now():%Y%m%d_%H%M%S}.log",
            encoding="utf-8",
        )
        fh.setFormatter(_fmt)
        log.addHandler(fh)
    except Exception as e:
        log.warning(f"Log em arquivo indisponivel: {e}")


# =============================================================================

def norm(texto):
    return (unicodedata.normalize("NFKD", str(texto))
            .encode("ASCII", "ignore").decode().upper().strip())


def mes_nome(mes):
    return ["", "Janeiro", "Fevereiro", "Marco", "Abril", "Maio", "Junho",
            "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"][mes]


def gerar_nome_arquivo(inst, mes, ano, pasta, n=0):
    """Gera nome do arquivo PDF com carimbo BB se master estiver disponivel."""
    if _master_obj is not None:
        carimbo = _master_obj.consumir_carimbo()
        return Path(pasta) / f"{carimbo}.pdf"
    uc = re.sub(r"\D", "", str(inst))[-10:]
    s  = f"_{n}" if n else ""
    return Path(pasta) / f"EDP_ES_{uc}_{mes:02d}{ano}{s}.pdf"


def screenshot(driver, nome):
    try:
        p = Path(PASTA_AUDITORIA) / f"{nome}_{datetime.now():%H%M%S}.png"
        driver.save_screenshot(str(p))
        log.info(f"Screenshot: {p.name}")
    except Exception:
        pass


# =============================================================================

def _localizar_chrome():
    """Retorna o caminho do binario do Chrome ou None se nao encontrado."""
    candidatos = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        str(Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files\Google\Chrome Beta\Application\chrome.exe",
        r"C:\Program Files\Chromium\Application\chrome.exe",
    ]
    for c in candidatos:
        if Path(c).exists():
            return c
    # Tenta via winreg
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe")
        val, _ = winreg.QueryValueEx(key, "")
        winreg.CloseKey(key)
        if val and val.strip():
            return val.strip()
    except Exception:
        pass
    # Tenta via PowerShell (mais confiavel em servidores)
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion"
             "\\App Paths\\chrome.exe' -ErrorAction SilentlyContinue).'(default)'"],
            capture_output=True, text=True, timeout=10,
        )
        val = r.stdout.strip()
        if val:
            return val
    except Exception:
        pass
    return None


def _user_data_dir_chrome():
    override = os.getenv("EDP_CHROME_USER_DATA_DIR", "").strip()
    if override:
        return Path(override)
    return Path(CHROME_USER_DATA)


def _descobrir_profile_directory(user_data_dir: Path) -> str:
    override = os.getenv("EDP_CHROME_PROFILE", "").strip()
    if override:
        return override

    local_state = user_data_dir / "Local State"
    candidatos = []

    try:
        if local_state.exists():
            data = json.loads(local_state.read_text(encoding="utf-8"))
            profile = data.get("profile", {}) or {}
            last_used = str(profile.get("last_used", "")).strip()
            if last_used:
                candidatos.append(last_used)
            for item in profile.get("last_active_profiles", []) or []:
                item = str(item or "").strip()
                if item and item not in candidatos:
                    candidatos.append(item)
    except Exception:
        pass

    candidatos.extend([CHROME_PROFILE, "Profile 1", "Profile 2", "Default"])
    vistos = set()
    for nome in candidatos:
        nome = str(nome or "").strip()
        if not nome or nome in vistos:
            continue
        vistos.add(nome)
        if (user_data_dir / nome).exists():
            return nome

    for pasta in sorted(user_data_dir.glob("Profile *")):
        if pasta.is_dir():
            return pasta.name

    return CHROME_PROFILE


def _ignorar_cache_chrome(_, nomes):
    ignorar = {
        "Cache",
        "Code Cache",
        "GPUCache",
        "GrShaderCache",
        "ShaderCache",
        "DawnCache",
        "Crashpad",
        "BrowserMetrics",
        "OptimizationHints",
        "Subresource Filter",
        "Safe Browsing",
        "Component Updates",
    }
    return [nome for nome in nomes if nome in ignorar]


def _preparar_perfil_automacao(user_data_dir: Path, profile_directory: str) -> Path:
    destino_root = TEMP_DIR / "_chrome_user_data"
    shutil.rmtree(destino_root, ignore_errors=True)
    destino_root.mkdir(parents=True, exist_ok=True)

    for nome in ["Local State", "First Run"]:
        origem = user_data_dir / nome
        destino = destino_root / nome
        try:
            if origem.exists():
                shutil.copy2(origem, destino)
        except Exception:
            pass

    origem_profile = user_data_dir / profile_directory
    destino_profile = destino_root / profile_directory
    if not origem_profile.exists():
        raise FileNotFoundError(f"Perfil Chrome nao encontrado: {origem_profile}")

    shutil.copytree(
        origem_profile,
        destino_profile,
        ignore=_ignorar_cache_chrome,
        dirs_exist_ok=True,
    )
    return destino_root


def _aplicar_stealth(driver):
    try:
        driver.execute_cdp_cmd("Network.enable", {})
    except Exception:
        pass

    try:
        driver.execute_cdp_cmd(
            "Network.setExtraHTTPHeaders",
            {"headers": {"Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"}},
        )
    except Exception:
        pass

    try:
        driver.execute_cdp_cmd("Emulation.setTimezoneOverride", {"timezoneId": "America/Sao_Paulo"})
    except Exception:
        pass

    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
                Object.defineProperty(navigator, 'languages', {get: () => ['pt-BR', 'pt', 'en-US', 'en']});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                window.chrome = window.chrome || { runtime: {} };

                const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
                if (originalQuery) {
                    window.navigator.permissions.query = (parameters) => (
                        parameters && parameters.name === 'notifications'
                            ? Promise.resolve({ state: Notification.permission })
                            : originalQuery(parameters)
                    );
                }

                const getParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(parameter) {
                    if (parameter === 37445) return 'Intel Inc.';
                    if (parameter === 37446) return 'Intel Iris OpenGL Engine';
                    return getParameter.call(this, parameter);
                };
            """
        },
    )


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


def build_driver(user_data_dir=None, profile_directory=None, fallback_on_error=False):
    opts = Options()
    chrome_bin = _localizar_chrome()
    if chrome_bin:
        opts.binary_location = chrome_bin
        print(f"[driver] Chrome encontrado: {chrome_bin}", flush=True)
    else:
        print("[driver] Chrome nao localizado - usando caminho padrao.", flush=True)

    origem_user_data = Path(user_data_dir).expanduser() if user_data_dir else _user_data_dir_chrome()
    perfil_escolhido = str(profile_directory).strip() if profile_directory else _descobrir_profile_directory(origem_user_data)

    print(f"[driver] user-data-dir origem={origem_user_data}", flush=True)
    print(f"[driver] profile-directory={perfil_escolhido}", flush=True)
    if not origem_user_data.exists():
        raise FileNotFoundError(f"user-data-dir nao existe: {origem_user_data}")

    try:
        perfil_automacao = _preparar_perfil_automacao(origem_user_data, perfil_escolhido)
        print(f"[driver] perfil clonado={perfil_automacao}", flush=True)
    except Exception as e:
        print(f"[driver] Falha ao clonar perfil ({e}) - usando perfil direto.", flush=True)
        perfil_automacao = origem_user_data

    opts.add_argument(f"--user-data-dir={perfil_automacao}")
    opts.add_argument(f"--profile-directory={perfil_escolhido}")
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-notifications")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--disable-session-crashed-bubble")
    opts.add_argument("--disable-restore-background-contents")
    opts.add_argument("--disable-background-mode")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--lang=pt-BR")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--no-proxy-server")
    opts.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_experimental_option("prefs", {
        "download.default_directory": str(TEMP_DIR),
        "download.prompt_for_download": False,
        "plugins.always_open_pdf_externally": True,
        "profile.exit_type": "Normal",
        "profile.default_content_setting_values.notifications": 2,
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
    })

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

    service.creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    def _start_driver(driver_opts):
        return webdriver.Chrome(service=service, options=driver_opts)

    try:
        driver = _start_driver(opts)
    except Exception as e:
        msg = str(e).lower()
        if "session not created" in msg or "chrome instance exited" in msg:
            log.error("Falha no Chrome com profile atual. Verifique se nao ha outro Chrome aberto com o mesmo perfil.")
            print("[driver] Falha no Chrome com profile atual. Verifique se nao ha outro Chrome aberto com o mesmo perfil.", flush=True)
            if not fallback_on_error:
                raise
            log.warning("Fallback habilitado: iniciando com profile temporario isolado")
            print("[driver] Fallback habilitado: iniciando com profile temporario isolado", flush=True)

            fallback_dir = str(Path(os.getenv('TMP', Path.home() / 'AppData/Local/Temp')) / 'edp_chrome_profile')
            Path(fallback_dir).mkdir(parents=True, exist_ok=True)
            opts_fallback = Options()
            if chrome_bin:
                opts_fallback.binary_location = chrome_bin
            opts_fallback.add_argument(f"--user-data-dir={fallback_dir}")
            opts_fallback.add_argument("--profile-directory=Default")
            opts_fallback.add_argument("--start-maximized")
            opts_fallback.add_argument("--disable-notifications")
            opts_fallback.add_argument("--disable-blink-features=AutomationControlled")
            opts_fallback.add_argument("--no-first-run")
            opts_fallback.add_argument("--no-default-browser-check")
            opts_fallback.add_argument("--disable-session-crashed-bubble")
            opts_fallback.add_argument("--disable-restore-background-contents")
            opts_fallback.add_argument("--disable-background-mode")
            opts_fallback.add_argument("--disable-dev-shm-usage")
            opts_fallback.add_argument("--no-sandbox")
            opts_fallback.add_experimental_option("excludeSwitches", ["enable-automation"])
            opts_fallback.add_experimental_option("useAutomationExtension", False)
            opts_fallback.add_experimental_option("prefs", {
                "download.default_directory": str(TEMP_DIR),
                "download.prompt_for_download": False,
                "plugins.always_open_pdf_externally": True,
                "profile.exit_type": "Normal",
                "profile.default_content_setting_values.notifications": 2,
            })
            try:
                driver = _start_driver(opts_fallback)
                print(f"[driver] Fallback iniciado usando profile temporario {fallback_dir}", flush=True)
            except Exception as e2:
                log.error(f"Nao foi possivel iniciar Chrome nem com fallback: {e2}")
                raise
        else:
            raise

    _aplicar_stealth(driver)
    driver.set_page_load_timeout(60)
    time.sleep(2)
    return driver


def clicar(driver, el):
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    time.sleep(0.3)
    try:
        el.click()
    except BaseException:
        driver.execute_script("arguments[0].click();", el)
    time.sleep(T_CLICK)


def _aceitar_todos_cookies(driver, tentativas=3) -> bool:
    seletores_css = [
        "button#onetrust-accept-btn-handler",
        "button[aria-label*='Aceitar']",
        "button[title*='Aceitar']",
        ".cc-btn.cc-allow",
        "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
        "#CybotCookiebotDialogBodyButtonAccept",
        "button.accept-cookies",
        "button.cookie-accept",
    ]
    seletores_xpath = [
        "//button[contains(., 'Aceitar todos')]",
        "//button[contains(., 'ACEITAR TODOS')]",
        "//button[contains(., 'Aceitar')]",
        "//button[contains(., 'Concordo')]",
        "//button[contains(., 'Permitir')]",
        "//a[contains(., 'Aceitar')]",
        "//a[contains(., 'Concordo')]",
    ]

    def _tentar_no_contexto() -> bool:
        for sel in seletores_css:
            try:
                for el in driver.find_elements(By.CSS_SELECTOR, sel):
                    if el.is_displayed():
                        clicar(driver, el)
                        return True
            except Exception:
                pass
        for xp in seletores_xpath:
            try:
                for el in driver.find_elements(By.XPATH, xp):
                    if el.is_displayed():
                        clicar(driver, el)
                        return True
            except Exception:
                pass
        return False

    for _ in range(tentativas):
        try:
            driver.switch_to.default_content()
        except Exception:
            pass

        if _tentar_no_contexto():
            print("[LOGIN] Cookies aceitos.", flush=True)
            time.sleep(1)
            return True

        try:
            iframes = driver.find_elements(By.TAG_NAME, "iframe")
        except Exception:
            iframes = []

        for frame in iframes:
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame(frame)
                if _tentar_no_contexto():
                    print("[LOGIN] Cookies aceitos em iframe.", flush=True)
                    time.sleep(1)
                    driver.switch_to.default_content()
                    return True
            except Exception:
                continue
            finally:
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass

        try:
            driver.execute_script("""
                const sels = [
                    '#onetrust-banner-sdk',
                    '.onetrust-pc-dark-filter',
                    '.cc-window',
                    '#CybotCookiebotDialog',
                    '[id*=\"cookie\"]',
                    '[class*=\"cookie-banner\"]'
                ];
                for (const sel of sels) {
                    document.querySelectorAll(sel).forEach(el => {
                        el.style.display = 'none';
                        el.style.visibility = 'hidden';
                    });
                }
            """)
        except Exception:
            pass
        time.sleep(0.8)
    return False


# =============================================================================

def fazer_login(driver, email, senha):
    print("[LOGIN] Navegando para o portal...", flush=True)

    for t in range(3):
        try:
            driver.get(URL_PORTAL)
        except Exception as e:
            print(f"[LOGIN] get() erro ({t+1}): {e}", flush=True)
        time.sleep(4)
        url = driver.current_url
        print(f"[LOGIN] URL ({t+1}): {url}", flush=True)

        if "edponline.com.br" in url and "grandes-clientes" in url:
            break

        if t == 1:
            print(f"[LOGIN] Redirecionando para login direto: {URL_LOGIN}", flush=True)
            try:
                driver.get(URL_LOGIN)
            except Exception as e:
                print(f"[LOGIN] fallback get() erro: {e}", flush=True)
            time.sleep(4)
            url = driver.current_url
            print(f"[LOGIN] URL fallback: {url}", flush=True)
            if "edponline.com.br" in url:
                break
    else:
        log.error("Nao conseguiu navegar para o portal.")
        screenshot(driver, "nav_falhou")
        return False

    _aceitar_todos_cookies(driver)

    try:
        WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.ID, "grid-instalacoes")))
        print("[LOGIN] Sessao ativa detectada.", flush=True)
        return True
    except TimeoutException:
        print("[LOGIN] Sem sessao ativa, fazendo login...", flush=True)

    try:
        btn_chrome_login = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH,
                "//button[contains(., 'Fazer login no Chrome') or contains(., 'Fazer login') or contains(., 'login no Chrome') ]")))
        print("[LOGIN] Botao 'Fazer login no Chrome' encontrado, clicando...", flush=True)
        clicar(driver, btn_chrome_login)
        time.sleep(4)
    except TimeoutException:
        pass

    _aceitar_todos_cookies(driver)

    print("[LOGIN] Verificando radio ES...", flush=True)
    try:
        radios = WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located(
                (By.CSS_SELECTOR, "span.custom-radio__box")))
        for i, radio in enumerate(radios):
            try:
                label = radio.find_element(By.XPATH, "./ancestor::label")
                texto = norm(label.text)
                print(f"[LOGIN] radio[{i}] = '{texto}'", flush=True)
                if "ESPIRITO" in texto or "ESCELSA" in texto:
                    try:
                        inp = label.find_element(By.CSS_SELECTOR, "input[type='radio']")
                        if not inp.is_selected():
                            clicar(driver, radio)
                            print("[LOGIN] Radio ES clicado.", flush=True)
                        else:
                            print("[LOGIN] Radio ES ja selecionado.", flush=True)
                    except Exception:
                        clicar(driver, radio)
                    break
            except Exception:
                pass
    except TimeoutException:
        print("[LOGIN] Radios nao encontrados - assumindo ES padrao.", flush=True)

    time.sleep(0.5)
    _aceitar_todos_cookies(driver)

    print("[LOGIN] Preenchendo e-mail...", flush=True)
    try:
        fe = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "Email")))
        fe.click()
        time.sleep(0.3)
        fe.clear()
        for ch in email:
            fe.send_keys(ch)
            time.sleep(0.04)
        driver.execute_script("arguments[0].dispatchEvent(new Event('input', {bubbles:true}));", fe)
        driver.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles:true}));", fe)
        time.sleep(0.4)
        print("[LOGIN] E-mail preenchido.", flush=True)
    except TimeoutException:
        log.error("Campo Email nao encontrado.")
        screenshot(driver, "sem_email")
        return False

    print("[LOGIN] Preenchendo senha...", flush=True)
    try:
        fs = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "Senha")))
        fs.click()
        time.sleep(0.3)
        fs.clear()
        for ch in senha:
            fs.send_keys(ch)
            time.sleep(0.05)
        fs.send_keys(Keys.TAB)
        driver.execute_script("arguments[0].dispatchEvent(new Event('input', {bubbles:true}));", fs)
        driver.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles:true}));", fs)
        time.sleep(0.5)
        print("[LOGIN] Senha preenchida.", flush=True)
    except TimeoutException:
        log.error("Campo Senha nao encontrado.")
        screenshot(driver, "sem_senha")
        return False

    print("[LOGIN] Ativando botao Entrar...", flush=True)
    try:
        info_btn = driver.execute_script("""
            ['Email', 'Senha'].forEach(function(id) {
                var el = document.getElementById(id);
                if (!el) return;
                el.dispatchEvent(new Event('input',  {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true, key: 'Tab'}));
                el.blur();
            });
            var btn = document.querySelector("button[type='submit'], input[type='submit'], button");
            if (!btn) return null;
            return {
                text: (btn.innerText || btn.value || '').trim(),
                disabled: btn.disabled,
                aria: btn.getAttribute('aria-disabled'),
                cls: btn.className || ''
            };
        """)
        print(f"[LOGIN] Estado botao apos eventos: {info_btn}", flush=True)
        time.sleep(1.0)
    except Exception as ex:
        log.warning(f"Erro ao disparar eventos React: {ex}")

    _aceitar_todos_cookies(driver)
    print("[LOGIN] Clicando em Entrar...", flush=True)
    btn = None
    ultimo_estado = None
    fim = time.time() + 20
    while time.time() < fim and btn is None:
        candidatos = []
        for by, sel in [
            (By.CSS_SELECTOR, "button[type='submit'], input[type='submit']"),
            (By.XPATH, "//button[contains(., 'Entrar') or contains(., 'Acessar') or contains(., 'Login') or contains(., 'ACESSAR') or contains(., 'ENTRAR') ]"),
            (By.XPATH, "//input[@type='submit']"),
        ]:
            try:
                candidatos.extend(driver.find_elements(by, sel))
            except Exception:
                pass

        for el in candidatos:
            try:
                if not el.is_displayed():
                    continue
                texto = (el.text or el.get_attribute('value') or '').strip()
                disabled = el.get_attribute('disabled')
                aria = (el.get_attribute('aria-disabled') or '').strip().lower()
                classe = (el.get_attribute('class') or '').strip()
                estado = (texto, disabled, aria, classe)
                if estado != ultimo_estado:
                    print(f"[LOGIN] Candidato botao: texto='{texto}' disabled={disabled} aria={aria} class='{classe}'", flush=True)
                    ultimo_estado = estado
                if disabled in (None, '', 'false') and aria != 'true':
                    btn = el
                    break
            except Exception:
                continue
        if btn is None:
            time.sleep(0.5)

    if btn is None:
        try:
            btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit'], input[type='submit'], button")
            print("[LOGIN] Botao nao habilitou; tentando clique JS de contingencia.", flush=True)
            driver.execute_script(
                "arguments[0].removeAttribute('disabled');"
                "arguments[0].scrollIntoView({block:'center'});"
                "arguments[0].click();",
                btn,
            )
        except Exception:
            log.error("Botao Entrar nao ficou clicavel.")
            screenshot(driver, "sem_botao")
            return False
    else:
        print(f"[LOGIN] Botao pronto: '{btn.text or btn.get_attribute('value') or ''}'", flush=True)
        try:
            clicar(driver, btn)
        except Exception:
            driver.execute_script("arguments[0].click();", btn)

    print("[LOGIN] Aguardando grid...", flush=True)
    try:
        WebDriverWait(driver, 40).until(
            EC.presence_of_element_located((By.ID, "grid-instalacoes")))
        print("[LOGIN] Login OK! Grid carregada.", flush=True)
        log.info("Login concluido.")
        return True
    except TimeoutException:
        print("[LOGIN] Grid nao carregou na primeira tentativa; tentando submit do form...", flush=True)
        try:
            driver.execute_script("""
                var form = document.querySelector('form');
                if (form) {
                    if (form.requestSubmit) form.requestSubmit();
                    else form.submit();
                }
            """)
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.ID, "grid-instalacoes")))
            print("[LOGIN] Login OK! Grid carregada apos submit do form.", flush=True)
            log.info("Login concluido.")
            return True
        except Exception:
            pass

        log.error(f"Grid nao carregou. URL: {driver.current_url}")
        for e in driver.find_elements(By.CSS_SELECTOR,
                ".validation-summary-errors, .alert-danger, .field-validation-error"):
            if e.text.strip():
                log.error(f"Erro na pagina: {e.text.strip()}")
        screenshot(driver, "grid_falhou")
        return False

def coletar_ucs(driver):
    ucs = []
    try:
        sel_el = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.ID, "RowsPerPage")))
        Select(sel_el).select_by_value("25")
        time.sleep(2)
    except Exception:
        pass

    pagina = 1
    while True:
        log.info(f"  Pagina {pagina} da grid...")
        rows = driver.find_elements(By.CSS_SELECTOR, "table.table tbody tr")
        for tr in rows:
            try:
                link = tr.find_element(
                    By.CSS_SELECTOR, "a.instalacaoOuUnidadeConsumidora")
                imgs = link.find_elements(By.TAG_NAME, "img")
                if not imgs:
                    continue
                if SVG_VERDE not in (imgs[0].get_attribute("src") or ""):
                    continue
                ucs.append({
                    "uc":   link.text.split()[0],
                    "inst": link.get_attribute(
                                "data-instalacao-ou-unidade-consumidora"),
                })
            except NoSuchElementException:
                continue

        btns = driver.find_elements(By.XPATH,
            "//ul[contains(@class,'pagination')]"
            "//li[not(contains(@class,'disabled'))]"
            "//a[contains(@class,'page-link') and text()='\u203a']")
        if not btns:
            break
        clicar(driver, btns[0])
        time.sleep(2)
        pagina += 1

    log.info(f"  Total UCs elegiveis: {len(ucs)}")
    return ucs


# =============================================================================

def ir_para_lista(driver):
    if "grid-instalacoes" in driver.page_source:
        return True
    driver.get(URL_PORTAL)
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.ID, "grid-instalacoes")))
        return True
    except TimeoutException:
        return False


def clicar_uc(driver, inst):
    for _ in range(20):
        try:
            link = driver.find_element(By.CSS_SELECTOR,
                f"a.instalacaoOuUnidadeConsumidora"
                f"[data-instalacao-ou-unidade-consumidora='{inst}']")
            clicar(driver, link)
            return True
        except NoSuchElementException:
            btns = driver.find_elements(By.XPATH,
                "//ul[contains(@class,'pagination')]"
                "//li[not(contains(@class,'disabled'))]"
                "//a[contains(@class,'page-link') and text()='\u203a']")
            if not btns:
                return False
            clicar(driver, btns[0])
            time.sleep(2)
    return False


# =============================================================================

def abrir_extrato(driver):
    try:
        btn = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "a[href*='extrato-de-contas']")))
        clicar(driver, btn)
        return True
    except TimeoutException:
        log.warning("  Botao extrato nao encontrado.")
        return False


def baixar_mes(driver, mes, ano, pasta_destino, inst):
    mes_str = mes_nome(mes)
    ano_str = str(ano)
    mes_fmt = f"{mes:02d}/{ano}"
    time.sleep(2)

    bloco = None
    for xpath in [
        (f"//*[contains(text(),'{mes_str}') and contains(text(),'{ano_str}')]"
         f"/ancestor::div[contains(@class,'card') or contains(@class,'conta')"
         f" or contains(@class,'fatura')][1]"),
        (f"//*[contains(text(),'{mes_fmt}')]"
         f"/ancestor::div[contains(@class,'card') or contains(@class,'conta')"
         f" or contains(@class,'fatura')][1]"),
        (f"//*[contains(text(),'{mes_str}') and contains(text(),'{ano_str}')]"
         f"/ancestor::li[1]"),
    ]:
        els = driver.find_elements(By.XPATH, xpath)
        if els:
            bloco = els[0]
            break

    if not bloco:
        log.warning(f"  Mes {mes_str}/{ano} nao encontrado.")
        return "nao_encontrado", ""

    try:
        btn_ver = bloco.find_element(By.XPATH,
            ".//*[contains(text(),'Visualizar')]")
        clicar(driver, btn_ver)
        time.sleep(1.5)
    except NoSuchElementException:
        log.warning("  Botao Visualizar nao encontrado.")
        return "erro", ""

    try:
        btn_dl = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH,
                "//*[contains(text(),'Baixar') and (self::a or self::button)]"
                " | //*[contains(@class,'cloud-download')]/ancestor::a[1]")))
    except TimeoutException:
        log.warning("  Botao Baixar nao encontrado.")
        return "erro", ""

    # Baixa na pasta temp local
    antes = set(TEMP_DIR.glob("*.pdf"))
    clicar(driver, btn_dl)

    fim = time.time() + 90
    while time.time() < fim:
        agora = {f for f in TEMP_DIR.glob("*.pdf")
                 if not f.name.endswith(".crdownload")}
        novos = agora - antes
        if novos:
            pdf_temp = novos.pop()

            # Gera nome definitivo (com carimbo BB se master estiver disponivel)
            destino = gerar_nome_arquivo(inst, mes, ano, pasta_destino)
            c = 1
            while destino.exists():
                destino = gerar_nome_arquivo(inst, mes, ano, pasta_destino, c)
                c += 1

            # Move do temp local para pasta destino (servidor)
            try:
                Path(pasta_destino).mkdir(parents=True, exist_ok=True)
                shutil.move(str(pdf_temp), str(destino))
                log.info(f"  Salvo: {destino.name}")

                # Registra no master
                if _master_obj is not None:
                    try:
                        _master_obj.registrar(
                            indice_bb=destino.stem,
                            sistema="EDP_ES",
                            uc=re.sub(r"\D", "", str(inst))[-10:],
                            mes_ref=f"{mes:02d}-{ano}",
                            fatura_id=inst,
                            arquivo=str(destino),
                        )
                    except Exception as e:
                        log.warning(f"  Master nao registrado: {e}")

            except Exception as e:
                log.error(f"  Erro ao mover PDF: {e}")
                return "erro", ""

            return "ok", destino.name
        time.sleep(0.8)

    log.error(f"  Timeout - PDF nao baixou para {inst}.")
    return "erro", ""


# =============================================================================

class Auditoria:
    def __init__(self, pasta, mes, ano):
        self.path = Path(pasta) / f"auditoria_edp_{ano}{mes:02d}.csv"
        self._load()

    def _load(self):
        if self.path.exists():
            self.df = pd.read_csv(self.path, dtype=str)
        else:
            self.df = pd.DataFrame(
                columns=["uc", "inst", "status", "arquivo", "ts"])

    def feito(self, inst):
        return inst in self.df["inst"].values

    def salvar(self, uc, inst, status, arquivo=""):
        nova = pd.DataFrame([{
            "uc": uc, "inst": inst, "status": status,
            "arquivo": arquivo,
            "ts": datetime.now().isoformat(timespec="seconds"),
        }])
        self.df = pd.concat([self.df, nova], ignore_index=True)
        self.df.to_csv(self.path, index=False, encoding="utf-8-sig")


# =============================================================================

def carregar_credenciais(planilha):
    df = pd.read_excel(planilha, dtype=str)
    df.columns = df.columns.str.strip()
    col_e = "E-MAIL DE ACESSO"
    col_s = "SENHA DE ACESSO"
    mask = (
        (df[col_e].str.strip().str.lower() != "sem acesso") &
        (df[col_s].str.strip().str.lower() != "sem acesso")
    )
    grupos = []
    for (email, senha), g in df[mask].groupby([col_e, col_s]):
        email, senha = email.strip(), senha.strip()
        if "sem acesso" not in email.lower():
            grupos.append({"email": email, "senha": senha, "total": len(g)})
    return grupos


# =============================================================================

def processar(email, senha, mes, ano, pasta, aud, inicio=0,
              user_data_dir=None, profile_directory=None,
              fallback_on_error=False):
    print("\n[EDP] Abrindo Chrome...", flush=True)
    driver = build_driver(user_data_dir=user_data_dir,
                          profile_directory=profile_directory,
                          fallback_on_error=fallback_on_error)

    try:
        if not fazer_login(driver, email, senha):
            log.error("Login falhou.")
            return

        log.info("Coletando UCs...")
        ucs   = coletar_ucs(driver)
        total = len(ucs)
        ok = erro = nao_enc = pulado = 0

        for idx, uc in enumerate(ucs):
            if idx < inicio:
                continue

            n     = idx + 1
            inst  = uc["inst"]
            label = uc["uc"]
            barra = f"[{n}/{total}]"

            if aud.feito(inst):
                log.info(f"{barra} {label} ja processada - pulando.")
                pulado += 1
                continue

            log.info(f"{barra} UC {label} (inst {inst})...")

            if not ir_para_lista(driver):
                log.error(f"{barra} Nao voltou para lista.")
                aud.salvar(label, inst, "erro_nav")
                erro += 1
                continue

            if not clicar_uc(driver, inst):
                log.error(f"{barra} UC {label} nao encontrada.")
                aud.salvar(label, inst, "erro_nao_encontrada")
                erro += 1
                continue

            if not abrir_extrato(driver):
                aud.salvar(label, inst, "erro_extrato")
                ir_para_lista(driver)
                erro += 1
                continue

            resultado, arquivo = baixar_mes(driver, mes, ano, pasta, inst)
            aud.salvar(label, inst, resultado, arquivo)

            if resultado == "ok":               ok += 1
            elif resultado == "nao_encontrado": nao_enc += 1
            else:                               erro += 1

            h   = datetime.now().strftime("%H:%M")
            pct = int(n / total * 100)
            print(
                f"  {h} | {pct:3d}% {barra} "
                f"ok={ok} erro={erro} nao_enc={nao_enc} pulados={pulado}",
                flush=True)

            ir_para_lista(driver)
            time.sleep(T_UC)

        log.info(
            f"\n{'-'*60}\n  CONCLUIDO [{email}]\n"
            f"  Total={total} OK={ok} NaoEnc={nao_enc} "
            f"Erros={erro} Pulados={pulado}\n{'-'*60}")

    finally:
        driver.quit()
        print("[EDP] Chrome fechado.", flush=True)


# =============================================================================

def main():
    print("[MAIN] Iniciando...", flush=True)
    _add_file_log()

    parser = argparse.ArgumentParser(description="Download faturas EDP ES")
    hoje = date.today()
    parser.add_argument("--mes",      type=int, default=hoje.month)
    parser.add_argument("--ano",      type=int, default=hoje.year)
    parser.add_argument("--inicio",   type=int, default=0,
                        help="Indice da UC para retomar (0 = inicio)")
    parser.add_argument("--planilha", type=str, default=PLANILHA_SENHAS)
    parser.add_argument("--destino",  type=str, default=PASTA_DOWNLOAD)
    parser.add_argument("--chrome-user-data-dir", type=str, default=None,
                        help="Pasta de perfil do Chrome (user data dir)")
    parser.add_argument("--chrome-profile-dir", type=str, default=None,
                        help="Perfil do Chrome (Default, Profile 1 etc.)")
    parser.add_argument("--chrome-fallback", action="store_true",
                        help="Permite fallback para profile temporario em caso de falha de sessao")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info(f"  EDP ES - {args.mes:02d}/{args.ano}")
    log.info("=" * 60)

    pasta = Path(args.destino)
    pasta.mkdir(parents=True, exist_ok=True)

    aud    = Auditoria(PASTA_AUDITORIA, args.mes, args.ano)
    grupos = carregar_credenciais(args.planilha)

    if not grupos:
        log.error("Nenhuma credencial valida encontrada.")
        sys.exit(1)

    log.info(f"{len(grupos)} grupo(s) de credenciais.")

    for g in grupos:
        log.info(f"Conta: {g['email']} ({g['total']} UCs)")
        processar(
            email=g["email"],
            senha=g["senha"],
            mes=args.mes,
            ano=args.ano,
            pasta=pasta,
            aud=aud,
            inicio=args.inicio,
            user_data_dir=args.chrome_user_data_dir,
            profile_directory=args.chrome_profile_dir,
            fallback_on_error=args.chrome_fallback,
        )
        args.inicio = 0


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print(f"\n[ERRO FATAL] {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
