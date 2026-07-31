#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Neoenergia Selenium Worker - Melancia
- Derivado da base funcional individual
- Mantém a estrutura real de índice, extrator e lógica
- Usa pasta temporária exclusiva: downloads_temp_melancia
- Não executa main sozinho
- Expõe: run_worker_melancia(jobs, shared_lock)

Formato esperado de jobs:
[
    {
        "cnpj": "00000000000191",
        "senha": "senha",
        "estados_esperados": ["Rio Grande do Norte", "Mato Grosso do Sul"]
    },
    ...
]
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
import re
import time
import shutil
import logging
from dataclasses import dataclass
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
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from core.project_paths import resolve_indice_master_csv


# ── Índice master unificado ────────────────────────────────────────────────────
def _carregar_master_modulo():
    import importlib.util
    script_dir = Path(__file__).resolve().parent
    candidatos = [
        script_dir.parent.parent.parent / "indice_master.py",
        script_dir / "indice_master.py",
        script_dir.parent / "indice_master.py",
        script_dir.parent.parent / "indice_master.py",
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
_progress_queue = None  # Queue do orquestrador para reportar progresso


# ============================================================
# CONFIG
# ============================================================

WORKER_NAME = "melancia"

BASE_DIR = Path(__file__).resolve().parent
DEV_DIR = BASE_DIR.parent

LOG_DIR = BASE_DIR / "logs"
TEMP_DOWNLOAD_DIR = BASE_DIR / "downloads_temp_melancia"
FAILED_LOGIN_FILE = BASE_DIR / "cnpjs_falha_login.csv"

FINAL_DOWNLOAD_ROOT = Path(r"\\10.10.250.21\Energia\ARQUIVOS ENZO\DOWNLOAD NEOENERGIA")
INDEX_FILE = FINAL_DOWNLOAD_ROOT / "indice_downloads_neoenergia.csv"
MASTER_FILE = resolve_indice_master_csv(prefer_network=False)

URL_PORTAL = "https://agenciavirtual.neoenergia.com"

HEADLESS = False
ANO_MINIMO = 2026
PAGE_LOAD_TIMEOUT = 120
ELEMENT_TIMEOUT = 40

PAUSE_AFTER_LOGIN = 2.0
PAUSE_PDF_SETTLE = 1.5

MOTIVO_EMISSAO = "Comprovar Residência"
INDEX_START = 2_000_000

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
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(f"neoenergia_{WORKER_NAME}")


def _inicializar_master() -> None:
    """Chamado apenas dentro de run_worker_melancia, nunca no nível de módulo."""
    global _master_mod, _master_obj, MASTER_FILE
    import io
    import contextlib

    # Suprimir stdout durante o carregamento do indice_master (evita spam no terminal)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mod, _ = _carregar_master_modulo()

    for linha in buf.getvalue().splitlines():
        if linha.strip():
            log.info(f"[master] {linha.strip()}")

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

    for linha in buf2.getvalue().splitlines():
        if linha.strip():
            log.info(f"[master] {linha.strip()}")

    log.info(
        f"✓ Master carregado: proximo carimbo {_master_obj.proximo_carimbo} "
        f"| {len(_master_obj._ja_baixados)} registros | {MASTER_FILE}"
    )


# ============================================================
# MODELS
# ============================================================

@dataclass
class CnpjInfo:
    cnpj: str
    senha: str
    estados_esperados: List[str] = None

    def __post_init__(self):
        if self.estados_esperados is None:
            self.estados_esperados = []


@dataclass
class UcTela:
    codigo: str
    status: str
    texto: str
    estado: str
    eh_filha_coletiva: bool = False


@dataclass
class EstadoTela:
    nome: str


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


# ============================================================
# HELPERS GERAIS
# ============================================================

def fmt_doc(valor: str) -> str:
    return "".join(ch for ch in str(valor) if ch.isdigit())


def normalize_text(s: str) -> str:
    return " ".join((s or "").split()).strip()


def wait_ready(driver: webdriver.Chrome, timeout: int = 30) -> None:
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
    )


def save_screenshot(driver: webdriver.Chrome, name: str) -> None:
    try:
        path = LOG_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{WORKER_NAME}_{name}.png"
        driver.save_screenshot(str(path))
        log.info(f"Screenshot: {path.name}")
    except Exception as e:
        log.warning(f"Falha ao salvar screenshot {name}: {e}")


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


def estado_slug(nome: str) -> str:
    mapa = {
        "Bahia": "BAHIA",
        "Pernambuco": "PERNAMBUCO",
        "Rio Grande do Norte": "RIO_GRANDE_DO_NORTE",
        "Mato Grosso do Sul": "MATO_GROSSO_DO_SUL",
        "São Paulo": "SAO_PAULO",
        "DESCONHECIDO": "DESCONHECIDO",
    }
    return mapa.get(nome, re.sub(r"[^A-Za-z0-9]+", "_", nome.upper()).strip("_"))


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


# ============================================================
# ÍNDICE CSV — estrutura real do funcional
# ============================================================

INDEX_FIELDS = [
    "id", "cnpj", "estado", "instalacao", "mes_referencia",
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
                    sistema="NEOENERGIA",
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


# ============================================================
# CSV DE FALHAS DE LOGIN
# ============================================================

def gravar_falha_login(cnpj: str, senha: str, motivo: str = "credencial_invalida") -> None:
    new_file = not FAILED_LOGIN_FILE.exists()
    try:
        with open(FAILED_LOGIN_FILE, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["CNPJ", "SENHA", "MOTIVO", "DATA_FALHA"])
            if new_file:
                w.writeheader()
            w.writerow({"CNPJ": cnpj, "SENHA": senha, "MOTIVO": motivo, "DATA_FALHA": current_timestamp_str()})
        log.info(f"  Falha de login registrada: {cnpj} ({motivo})")
    except Exception as e:
        log.warning(f"  Não foi possível gravar falha de login: {e}")


# ============================================================
# DRIVER
# ============================================================

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


# ============================================================
# LOGIN
# ============================================================

def _aguardar_spinner_sumir(driver, timeout: int = 30) -> None:
    try:
        WebDriverWait(driver, timeout).until(
            EC.invisibility_of_element_located((By.CSS_SELECTOR, "div.loading-spinner"))
        )
    except TimeoutException:
        pass


def fazer_login(driver: webdriver.Chrome, cnpj: str, senha: str) -> bool:
    log.info(f"Login: {cnpj}")
    driver.get(URL_PORTAL)
    wait_ready(driver, ELEMENT_TIMEOUT)
    _aguardar_spinner_sumir(driver)

    botao_inicial = [
        (By.CSS_SELECTOR, "button[aria-label='Conectar-se a agência virtual']"),
        (By.XPATH, "//button[contains(., 'Conectar-se')]"),
        (By.XPATH, "//a[contains(., 'Conectar-se')]"),
        (By.XPATH, "//*[contains(text(), 'Conectar-se à agência virtual')]"),
    ]
    if not wait_clickable_and_click(driver, botao_inicial, timeout=25, description="botão inicial"):
        save_screenshot(driver, "erro_botao_inicial")
        return False

    try:
        campo_doc = WebDriverWait(driver, 25).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "input#userId"))
        )
    except TimeoutException:
        campo_doc = find_first(driver, [
            (By.CSS_SELECTOR, "input[name='userId']"),
            (By.XPATH, "//input[contains(@id,'user') or contains(@name,'user')]"),
        ], timeout=10)
    if not campo_doc:
        save_screenshot(driver, "erro_campo_documento")
        return False

    for tentativa in range(3):
        campo_doc.click()
        campo_doc.clear()
        time.sleep(0.3)
        campo_doc.send_keys(cnpj)
        time.sleep(0.4)
        valor_atual = campo_doc.get_attribute("value") or ""
        if fmt_doc(valor_atual) == fmt_doc(cnpj):
            break
        log.warning(f"CNPJ digitado incompleto (tentativa {tentativa + 1}): '{valor_atual}' esperado '{cnpj}'")
        time.sleep(0.5)

    try:
        campo_senha = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.XPATH, "//input[@type='password']"))
        )
    except TimeoutException:
        campo_senha = find_first(driver, [
            (By.CSS_SELECTOR, "input[type='password']"),
        ], timeout=5)

    if not campo_senha:
        save_screenshot(driver, "erro_campo_senha")
        return False

    campo_senha.click()
    campo_senha.clear()
    time.sleep(0.2)
    campo_senha.send_keys(senha)

    botao_entrar = [
        (By.CSS_SELECTOR, "button.btn-neoprimary[title='Entrar']"),
        (By.XPATH, "//button[contains(., 'Entrar')]"),
        (By.XPATH, "//input[@type='submit']"),
    ]

    if not wait_clickable_and_click(driver, botao_entrar, timeout=15, description="botão entrar"):
        save_screenshot(driver, "erro_botao_entrar")
        return False

    time.sleep(PAUSE_AFTER_LOGIN)
    _aguardar_spinner_sumir(driver, timeout=20)

    try:
        WebDriverWait(driver, 25).until(
            lambda d: "/home" in d.current_url or "/imoveis" in d.current_url or _tela_tem_selecao_estados(d)
        )
    except TimeoutException:
        erros = [
            "usuário ou senha inválidos",
            "usuario ou senha invalidos",
            "dados incorretos",
            "credenciais inválidas",
            "credenciais invalidas",
        ]
        body = normalize_text(driver.find_element(By.TAG_NAME, "body").text).lower()
        if any(e in body for e in erros):
            return False
        save_screenshot(driver, "erro_login_timeout")
        return False

    if "/home" not in driver.current_url and "/imoveis" not in driver.current_url and not _tela_tem_selecao_estados(driver):
        log.warning(f"Login: URL inesperada após entrar — {driver.current_url}")

    log.info(f"Login OK — URL: {driver.current_url}")
    return True


# ============================================================
# ESTADOS
# ============================================================

def listar_estados_disponiveis(driver: webdriver.Chrome, estados_esperados: List[str] = None) -> List[EstadoTela]:
    nomes_validos = {"Bahia", "Pernambuco", "Rio Grande do Norte", "Mato Grosso do Sul", "São Paulo"}
    esperados = [e for e in (estados_esperados or []) if e in nomes_validos]

    def _coletar_cards_estado() -> List[str]:
        encontrados: List[str] = []
        vistos: Set[str] = set()
        try:
            cards = driver.find_elements(By.CSS_SELECTOR, "mat-card.card-estado")
            for card in cards:
                texto = normalize_text(card.text)
                for nome in nomes_validos:
                    if nome in texto and nome not in vistos:
                        encontrados.append(nome)
                        vistos.add(nome)
        except Exception:
            pass
        if not encontrados:
            try:
                cards = driver.find_elements(By.XPATH, "//mat-card[contains(@class,'card-estado')]")
                for card in cards:
                    texto = normalize_text(card.text)
                    for nome in nomes_validos:
                        if nome in texto and nome not in vistos:
                            encontrados.append(nome)
                            vistos.add(nome)
            except Exception:
                pass
        return encontrados

    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "mat-card.card-estado"))
        )
    except TimeoutException:
        log.warning("Timeout aguardando mat-card.card-estado — tentando fallback")

    if esperados:
        deadline = time.time() + 15
        while time.time() < deadline:
            encontrados = _coletar_cards_estado()
            if all(e in encontrados for e in esperados):
                break
            time.sleep(0.5)

    encontrados = _coletar_cards_estado()

    if esperados:
        faltam = set(esperados) - set(encontrados)
        extras = set(encontrados) - set(esperados)
        if faltam:
            log.warning(f"Estados esperados não apareceram na tela: {sorted(faltam)}")
        if extras:
            log.info(f"Estados extras na tela (não no Excel): {sorted(extras)}")
        ordem = [e for e in esperados if e in encontrados] + [e for e in sorted(extras)]
    else:
        ordem = sorted(encontrados)

    estados = [EstadoTela(nome=n) for n in ordem]
    log.info(f"Estados disponíveis: {[e.nome for e in estados]}")
    return estados


def selecionar_estado(driver: webdriver.Chrome, estado: str) -> bool:
    if estado == "DESCONHECIDO":
        return True
    log.info(f"Selecionando estado: {estado}")

    seletores = [
        (By.XPATH, f"//mat-card[contains(@class,'card-estado') and .//text()[normalize-space()='{estado}']]"),
        (By.XPATH, f"//a[contains(@class,'link-page')]//mat-card[contains(@class,'card-estado') and contains(.,'{estado}')]"),
        (By.XPATH, f"//mat-card[contains(.,'{estado}')]"),
        (By.XPATH, f"//*[normalize-space(text())='{estado}']"),
    ]
    if not wait_clickable_and_click(driver, seletores, timeout=20, description=f"card estado {estado}"):
        save_screenshot(driver, f"erro_estado_{estado.replace(' ', '_')}")
        return False

    try:
        WebDriverWait(driver, 15).until(
            lambda d: "selecionar-estado" not in d.current_url
        )
        log.info(f"  Estado {estado} selecionado — URL: {driver.current_url}")
    except TimeoutException:
        log.warning(f"  URL não mudou após selecionar {estado} — continuando mesmo assim")

    return True


def _tela_tem_selecao_estados(driver) -> bool:
    try:
        els = driver.find_elements(By.CSS_SELECTOR, "mat-card.card-estado")
        if els:
            return True
        els = driver.find_elements(By.XPATH, "//mat-card[contains(@class,'card-estado')]")
        return len(els) > 0
    except Exception:
        return False


def _clicar_botao_voltar(driver) -> bool:
    seletores = [
        (By.XPATH, "//div[normalize-space(text())='VOLTAR']"),
        (By.XPATH, "//div[contains(@class,'pe-2') and normalize-space(text())='VOLTAR']"),
        (By.XPATH, "//button[normalize-space(.)='VOLTAR']"),
        (By.XPATH, "//*[normalize-space(text())='VOLTAR']"),
    ]
    return wait_clickable_and_click(driver, seletores, timeout=5, description="botão VOLTAR")


def voltar_para_selecao_estados(driver: webdriver.Chrome) -> bool:
    log.info("Voltando para tela de seleção de estados...")

    if _tela_tem_selecao_estados(driver):
        log.info("  Já está na tela de seleção de estados")
        return True

    if _clicar_botao_voltar(driver):
        time.sleep(1.0)
        if _tela_tem_selecao_estados(driver):
            log.info("  Voltou via botão VOLTAR")
            return True

    url_estados = URL_PORTAL.rstrip("/") + "/#/home/selecionar-estado"
    for tentativa in range(1, 4):
        try:
            log.info(f"  Tentativa {tentativa}: navegando para {url_estados}")
            driver.get(url_estados)
            wait_ready(driver, ELEMENT_TIMEOUT)
            try:
                WebDriverWait(driver, 8).until(lambda d: _tela_tem_selecao_estados(d))
                log.info(f"  Tela de estados confirmada via URL direta (tentativa {tentativa})")
                return True
            except TimeoutException:
                log.warning(f"  Estados não apareceram — URL atual: {driver.current_url}")
        except Exception as e:
            log.warning(f"  Tentativa {tentativa} falhou: {e}")
        time.sleep(1.5)

    log.info("  Fallback: tentando driver.back()")
    for passo in range(1, 7):
        try:
            driver.back()
            time.sleep(1.2)
            if _tela_tem_selecao_estados(driver):
                log.info(f"  Tela de estados confirmada via back() ({passo} passo(s))")
                return True
        except Exception as e:
            log.warning(f"  back() falhou no passo {passo}: {e}")
            break

    save_screenshot(driver, "erro_voltar_estados")
    log.error(f"  Não foi possível voltar para seleção de estados — URL: {driver.current_url}")
    return False


# ============================================================
# UCS
# ============================================================

def _coletar_ucs_pagina(driver, estado: str, vistos: Set[str], cnpj_atual: str = "") -> List[UcTela]:
    candidatos = find_all_now(driver, [
        (By.XPATH, "//div[contains(@class,'box-imoveis')]"),
        (By.XPATH, "//h6[contains(@class,'unidade-consumidora-title')]/ancestor::div[contains(@class,'box-imoveis')][1]"),
        (By.XPATH, "//mat-icon[contains(.,'arrow_forward')]/ancestor::div[contains(@class,'box-imoveis')][1]"),
    ])
    ucs: List[UcTela] = []
    cnpj_digits = re.sub(r"\D", "", cnpj_atual)

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

            # ── 1) Tentar seletores DOM específicos do número de instalação ──
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
                    if 6 <= len(txt_inst) <= 15:
                        codigo = txt_inst
                        break
                except Exception:
                    continue

            # ── 2) Fallback: regex iterando todos os matches, excluindo CNPJ ─
            if not codigo:
                for m in re.finditer(r"(\d{6,15})", texto):
                    candidato = m.group(1)
                    # Pula se for subconjunto ou igual ao CNPJ do job atual
                    # Pula somente se o candidato FOR o CNPJ completo (14 dígitos iguais)
                    if cnpj_digits and len(candidato) == 14 and candidato == cnpj_digits:
                        continue
                    codigo = candidato
                    break

            # ── 3) Status da UC ───────────────────────────────────────────────
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


def listar_ucs_na_tela(driver, estado: str, cnpj: str = "") -> List[UcTela]:
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, "//div[contains(@class,'box-imoveis')]"))
        )
    except TimeoutException:
        pass

    _voltar_pagina_1_ucs(driver)

    ucs: List[UcTela] = []
    vistos: Set[str] = set()
    pagina = 1
    paginas_vazias = 0

    while True:
        log.info(f"  Lendo UCs — página {pagina} ({estado})")
        ucs_pagina = _coletar_ucs_pagina(driver, estado, vistos, cnpj_atual=cnpj)
        ucs.extend(ucs_pagina)

        if len(ucs_pagina) == 0:
            paginas_vazias += 1
            if paginas_vazias >= 1:
                break
        else:
            paginas_vazias = 0

        if not _proxima_pagina_ucs(driver):
            break
        pagina += 1

    log.info(f"UCs totais ({estado}): {len(ucs)}")
    return ucs


def entrar_na_uc_por_indice(driver, indice_uc: int) -> bool:
    log.info(f"Entrando na UC índice {indice_uc}")
    try:
        cards = WebDriverWait(driver, 25).until(
            EC.presence_of_all_elements_located((By.XPATH, "//div[contains(@class,'box-imoveis')]"))
        )
        if indice_uc < 1 or indice_uc > len(cards):
            log.error(f"Índice UC inválido: {indice_uc}/{len(cards)}")
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
        log.error(f"Falha ao entrar UC[{indice_uc}]: {e}")

    save_screenshot(driver, f"erro_entrar_uc_{indice_uc}")
    return False


# ============================================================
# FATURAS
# ============================================================

def _aguardar_tela_faturas_carregar(driver, timeout=20) -> bool:
    indicadores = [
        (By.XPATH, "//*[contains(.,'LISTA DE FATURAS')]"),
        (By.XPATH, "//*[contains(.,'Lista de faturas')]"),
        (By.XPATH, "//*[contains(.,'Histórico de faturas')]"),
        (By.XPATH, "//*[contains(.,'HISTÓRICO DE FATURAS')]"),
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
    log.info("Abrindo tela de faturas")
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
        (By.XPATH, "//div[contains(@class,'fatura-situacao')]/ancestor::*[3]"),
        (By.CSS_SELECTOR, "mat-expansion-panel"),
    ]

    paineis = []
    for by, sel in SELETORES_PAINEIS:
        try:
            encontrados = WebDriverWait(driver, 8).until(
                EC.presence_of_all_elements_located((by, sel))
            )
            if encontrados:
                paineis = encontrados
                break
        except Exception:
            continue

    if not paineis:
        save_screenshot(driver, "erro_sem_paineis_faturas")
        log.warning("  Nenhum painel de fatura encontrado")
        return faturas

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
                texto_sit = normalize_text(span_sit.text)
                cls_span = (span_sit.get_attribute("class") or "").lower()

                if "pago" in texto_sit.lower() or "pago" in cls_span:
                    situacao = "PAGO"
                elif "a vencer" in texto_sit.lower() or "vencer" in cls_span:
                    situacao = "A VENCER"
                elif "vencida" in texto_sit.lower() or "vencido" in texto_sit.lower():
                    situacao = "VENCIDA"
                elif "aberto" in texto_sit.lower() or "pendente" in texto_sit.lower():
                    situacao = "EM ABERTO"
            except Exception:
                try:
                    bloco_sit = painel.find_element(By.XPATH, ".//*[contains(@class,'fatura-situacao')]")
                    texto_sit = normalize_text(bloco_sit.text)
                    cls_sit = (bloco_sit.get_attribute("class") or "").lower()

                    if "pago" in texto_sit.lower() or "pago" in cls_sit:
                        situacao = "PAGO"
                    elif "a vencer" in texto_sit.lower() or "vencer" in cls_sit:
                        situacao = "A VENCER"
                    elif "vencida" in texto_sit.lower() or "vencido" in texto_sit.lower():
                        situacao = "VENCIDA"
                    elif "aberto" in texto_sit.lower() or "pendente" in texto_sit.lower():
                        situacao = "EM ABERTO"
                except Exception:
                    pass

            if situacao == "DESCONHECIDA":
                lower = texto.lower()
                if re.search(r"\bpago\b", lower):
                    situacao = "PAGO"
                elif re.search(r"\ba vencer\b", lower):
                    situacao = "A VENCER"
                elif re.search(r"\bvencid[ao]\b", lower):
                    situacao = "VENCIDA"
                elif re.search(r"\bem aberto\b", lower):
                    situacao = "EM ABERTO"
                elif re.search(r"\bpendente\b", lower):
                    situacao = "EM ABERTO"

            if "mínima" in texto.lower() or "minima" in texto.lower() or "fatura mínima" in texto.lower() or "fatura minima" in texto.lower():
                minimo = True

            f = FaturaTela(
                indice=i,
                referencia=referencia,
                vencimento=vencimento,
                situacao=situacao,
                data_emissao=data_emissao,
                texto=texto,
                valor=valor,
                minimo=minimo,
            )
            faturas.append(f)

            if situacao == "DESCONHECIDA":
                log.warning(f"  FAT[{i}] situação não reconhecida | ref={referencia} | texto={texto[:220]}")

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


def selecionar_faturas_pendentes(driver, ja_baixados: Set[str], cnpj: str, instalacao: str) -> List[FaturaTela]:
    log.info(f"  Seleção de faturas para instalação={instalacao} cnpj={cnpj}")

    faturas = listar_faturas_na_tela(driver)
    if not faturas:
        log.info("  Nenhuma fatura encontrada na tela")
        return []

    elegiveis = []
    for f in faturas:
        motivo = None

        if f.situacao != "A VENCER":
            motivo = f"situacao={f.situacao}"
        elif f.minimo:
            motivo = "fatura mínima"
        elif not _ano_referencia_valido(f.referencia):
            motivo = f"ano inválido ref={f.referencia}"
        elif ja_foi_baixado(ja_baixados, instalacao, f.referencia):
            motivo = "já consta no índice"

        if motivo:
            log.info(f"  DESCARTADA ref={f.referencia} | {motivo}")
            continue

        elegiveis.append(f)

    if not elegiveis:
        log.info(f"  Nenhuma fatura elegível 'A VENCER' para instalação {instalacao}")
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

    escolhida = sorted(elegiveis, key=lambda x: _ordem_ref(x.referencia), reverse=True)[0]
    log.info(f"  Selecionando apenas a última A VENCER: {escolhida.referencia}")

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
    selects = find_all_now(driver, [(By.TAG_NAME, "select")])
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
        (By.XPATH, "//*[contains(text(), 'Comprovar Residência')]"),
    ]

    for by, selector in combos:
        try:
            el = WebDriverWait(driver, 20).until(EC.element_to_be_clickable((by, selector)))
            driver.execute_script("arguments[0].click();", el)
            time.sleep(0.7)

            opcao = find_first(driver, [
                (By.XPATH, f"//*[contains(text(), '{motivo}')]"),
            ], timeout=10)
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
            btn = WebDriverWait(driver, 25).until(
                EC.element_to_be_clickable((by, selector))
            )
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
            btn = WebDriverWait(driver, 25).until(
                EC.element_to_be_clickable((by, selector))
            )
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            time.sleep(0.4)
            driver.execute_script("arguments[0].click();", btn)
            return True
        except Exception:
            continue
    return False


# ============================================================
# PROCESSAMENTO
# ============================================================

def mover_pdf_para_destino(pdf_temp: Path, estado: str, referencia: str, carimbo: str) -> Path:
    estado_dir = FINAL_DOWNLOAD_ROOT / estado_slug(estado)
    mes_dir = estado_dir / referencia_to_folder(referencia)
    mes_dir.mkdir(parents=True, exist_ok=True)

    # Nome final é sempre o carimbo — simples, único, sem ambiguidade
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


def processar_uc(
    driver,
    indice_uc: int,
    estado: str,
    cnpj: str,
    instalacao: str,
    ja_baixados: Set[str],
) -> int:
    log.info(f"  processar_uc() | estado={estado} | cnpj={cnpj} | instalacao={instalacao} | indice_uc={indice_uc}")
    _prog("tela", etapa="lista_ucs", estado=estado, uc=instalacao, cnpj=cnpj)

    if not entrar_na_uc_por_indice(driver, indice_uc):
        log.error(f"  Falha ao entrar na UC {instalacao}")
        return 0

    if not abrir_tela_faturas(driver):
        log.error(f"  Falha ao abrir tela de faturas da UC {instalacao}")
        return 0

    _prog("tela", etapa="faturas_2via", estado=estado, uc=instalacao, cnpj=cnpj)
    selecionadas = selecionar_faturas_pendentes(driver, ja_baixados, cnpj, instalacao)
    for fat in selecionadas:
        _prog("fatura", uc=instalacao, estado=estado, referencia=fat.referencia, situacao=fat.situacao)
    if not selecionadas:
        log.info(f"  Nenhuma fatura nova elegível na instalação {instalacao}")
        return 0

    if not clicar_download_faturas(driver):
        log.error(f"  Falha ao clicar no botão de download na instalação {instalacao}")
        return 0

    if not selecionar_motivo_emissao(driver, MOTIVO_EMISSAO):
        log.error(f"  Falha ao selecionar motivo na instalação {instalacao}")
        return 0

    before = list_temp_pdf_files()

    if not confirmar_baixar(driver):
        log.error(f"  Falha ao confirmar baixar na instalação {instalacao}")
        return 0

    pdf = wait_new_pdf(before, timeout=90)
    if not pdf:
        log.warning(f"  Nenhum novo PDF detectado na instalação {instalacao}")
        save_screenshot(driver, f"sem_pdf_{estado_slug(estado)}_{instalacao}")
        return 0

    confirmar_popup_sucesso(driver)

    f = selecionadas[0]

    if _shared_lock is not None:
        with _shared_lock:
            if ja_foi_baixado(ja_baixados, instalacao, f.referencia):
                try:
                    if pdf.exists():
                        pdf.unlink(missing_ok=True)
                except Exception:
                    pass
                log.info(f"  Fatura já registrada por outro worker: {instalacao} | {f.referencia}")
                return 0

            carimbo = next_index_id()
            destino = mover_pdf_para_destino(pdf, estado, f.referencia, carimbo)
            row = {
                "id": carimbo,
                "cnpj": fmt_doc(cnpj),
                "estado": estado,
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
                if pdf.exists():
                    pdf.unlink(missing_ok=True)
            except Exception:
                pass
            return 0

        carimbo = next_index_id()
        destino = mover_pdf_para_destino(pdf, estado, f.referencia, carimbo)
        row = {
            "id": carimbo,
            "cnpj": fmt_doc(cnpj),
            "estado": estado,
            "instalacao": instalacao,
            "mes_referencia": f.referencia,
            "data_download": current_timestamp_str(),
            "data_emissao": f.data_emissao,
            "arquivo": str(destino),
        }
        append_index_row(row, ja_baixados)

    _prog("download_ok", uc=instalacao, estado=estado, referencia=f.referencia, carimbo=carimbo, status="baixada")
    log.info(f"  Download concluído: instalação={instalacao} ref={f.referencia}")
    return 1


def _ucs_pagina_atual(driver, estado: str, cnpj: str = "") -> List[UcTela]:
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, "//div[contains(@class,'box-imoveis')]"))
        )
    except TimeoutException:
        pass
    return _coletar_ucs_pagina(driver, estado, vistos=set(), cnpj_atual=cnpj)


def processar_estado(driver, estado: str, cnpj: str, ja_baixados: Set[str]) -> int:
    if not selecionar_estado(driver, estado):
        log.error(f"  Não foi possível selecionar estado {estado}")
        return 0

    _aguardar_spinner_sumir(driver, timeout=15)

    total_ok = 0
    pagina = 1
    paginas_sem_ligadas = 0

    while True:
        log.info(f"  === Página {pagina} de UCs ({estado}) ===")
        ucs = _ucs_pagina_atual(driver, estado, cnpj=cnpj)

        if not ucs:
            log.info(f"  Página {pagina} sem UCs — encerrando estado {estado}")
            break

        ligadas = [u for u in ucs if u.status == "LIGADA"]
        log.info(f"  Página {pagina}: {len(ucs)} UC(s), {len(ligadas)} LIGADA(s)")
        _prog("ucs_pagina", estado=estado, pagina=pagina, total_ucs=len(ucs), ligadas=len(ligadas), cnpj=cnpj)

        if not ligadas:
            paginas_sem_ligadas += 1
            if paginas_sem_ligadas >= 2:
                log.info(f"  {paginas_sem_ligadas} páginas consecutivas sem ligadas — encerrando estado {estado}")
                break
            if not _proxima_pagina_ucs(driver):
                log.info(f"  Fim das páginas ({estado}) — todas UCs desligadas")
                break
            pagina += 1
            continue

        paginas_sem_ligadas = 0

        for pos, uc in enumerate(ligadas, start=1):
            indice_na_pagina = ucs.index(uc) + 1
            log.info(f"  --- UC[pág{pagina}:{pos}] codigo={uc.codigo or 'SEM_CODIGO'} ---")
            baixados_uc = 0
            try:
                _prog("uc_inicio", uc=uc.codigo or "", estado=estado, i=pos, total=len(ligadas), pagina=pagina)
                baixados_uc = processar_uc(
                    driver=driver,
                    indice_uc=indice_na_pagina,
                    estado=estado,
                    cnpj=cnpj,
                    instalacao=uc.codigo or "",
                    ja_baixados=ja_baixados,
                )
            except Exception as e:
                log.error(f"  Erro UC pag{pagina}:{pos}: {e}")
                save_screenshot(driver, f"erro_uc_p{pagina}_{pos}_{estado_slug(estado)}")

            total_ok += baixados_uc
            _prog("uc_fim", uc=uc.codigo or "", estado=estado, pagina=pagina, pdfs=baixados_uc, status=("download_ok" if baixados_uc > 0 else "sem_fatura"))
            voltou = False
            for _ in range(4):
                try:
                    driver.back()
                    time.sleep(0.4)
                    _aguardar_spinner_sumir(driver, timeout=8)
                    if driver.find_elements(By.XPATH, "//div[contains(@class,'box-imoveis')]"):
                        voltou = True
                        break
                    if _tela_tem_selecao_estados(driver):
                        log.info(f"  back() caiu em seleção de estados — re-selecionando {estado}")
                        if selecionar_estado(driver, estado):
                            _aguardar_spinner_sumir(driver, timeout=15)
                            try:
                                WebDriverWait(driver, 20).until(
                                    EC.presence_of_element_located((By.XPATH, "//div[contains(@class,'box-imoveis')]"))
                                )
                                voltou = True
                            except TimeoutException:
                                pass
                        break
                except Exception:
                    break

            if not voltou:
                url_home = URL_PORTAL.rstrip("/") + "/#/home"
                try:
                    driver.get(url_home)
                    _aguardar_spinner_sumir(driver, timeout=15)
                    if _tela_tem_selecao_estados(driver):
                        if selecionar_estado(driver, estado):
                            _aguardar_spinner_sumir(driver, timeout=15)
                    WebDriverWait(driver, 20).until(
                        EC.presence_of_element_located((By.XPATH, "//div[contains(@class,'box-imoveis')]"))
                    )
                    voltou = True
                except Exception:
                    pass

            if not voltou:
                log.warning(f"  Nao voltou para lista de UCs apos UC pag{pagina}:{pos} — abortando pagina")
                save_screenshot(driver, f"nao_voltou_lista_p{pagina}_{pos}_{estado_slug(estado)}")
                break

            for _ in range(pagina - 1):
                if not _proxima_pagina_ucs(driver):
                    break
                time.sleep(0.4)

        if not _proxima_pagina_ucs(driver):
            log.info(f"  Sem próxima página após página {pagina} — fim do estado {estado}")
            break
        pagina += 1

    return total_ok


def processar_cnpj(info: CnpjInfo, ja_baixados: Set[str]) -> int:
    driver = None
    total_ok = 0
    n_estados_esperados = len(info.estados_esperados or [])

    try:
        driver = build_driver()

        if not fazer_login(driver, info.cnpj, info.senha):
            log.error(f"Login falhou: {info.cnpj}")
            gravar_falha_login(info.cnpj, info.senha, motivo="credencial_invalida")
            return 0

        if n_estados_esperados == 1:
            estado_nome = info.estados_esperados[0]
            log.info(f"  Estado único — clicando direto no card: {estado_nome}")
            _aguardar_spinner_sumir(driver, timeout=15)
            if not selecionar_estado(driver, estado_nome):
                log.error(f"  Falha ao selecionar {estado_nome}")
                return 0
            total_ok += processar_estado(driver, estado_nome, info.cnpj, ja_baixados)
            return total_ok

        estados = listar_estados_disponiveis(driver, estados_esperados=info.estados_esperados)
        log.info(f"  estados_esperados: {info.estados_esperados}")
        log.info(f"  estados na tela  : {[e.nome for e in estados]}")

        if not estados:
            log.warning("Nenhum estado identificado — tentando fluxo direto")
            total_ok += processar_estado(driver, "DESCONHECIDO", info.cnpj, ja_baixados)
            return total_ok

        for i_est, estado in enumerate(estados, start=1):
            log.info("")
            log.info(f"  {'=' * 50}")
            log.info(f"  ESTADO {i_est}/{len(estados)}: {estado.nome}")
            log.info(f"  {'=' * 50}")
            try:
                if i_est > 1:
                    ok_voltou = voltar_para_selecao_estados(driver)
                    if not ok_voltou:
                        log.error(f"  Não foi possível voltar para seleção de estados — pulando {estado.nome}")
                        save_screenshot(driver, f"erro_voltar_para_{estado_slug(estado.nome)}")
                        continue
                    log.info(f"  Retorno para seleção de estados OK — selecionando {estado.nome}")

                total_ok += processar_estado(driver, estado.nome, info.cnpj, ja_baixados)
                log.info(f"  Estado {estado.nome} concluído")

            except Exception as e:
                log.error(f"Erro no estado {estado.nome}: {e}")
                save_screenshot(driver, f"erro_estado_{estado_slug(estado.nome)}")

    except Exception as e:
        log.error(f"Erro geral CNPJ {info.cnpj}: {e}")
        if driver:
            save_screenshot(driver, f"erro_cnpj_{info.cnpj}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    return total_ok


# ============================================================
# ENTRADA DO WORKER
# ============================================================

def _limpar_pasta_temp() -> None:
    try:
        if TEMP_DOWNLOAD_DIR.exists():
            for p in TEMP_DOWNLOAD_DIR.glob("*"):
                if p.is_file():
                    p.unlink(missing_ok=True)
    except Exception as e:
        log.warning(f"Falha ao limpar temp dir do {WORKER_NAME}: {e}")


def _coletar_jobs(jobs: Iterable) -> List[CnpjInfo]:
    saida: List[CnpjInfo] = []
    for item in jobs:
        if isinstance(item, CnpjInfo):
            saida.append(item)
            continue

        cnpj = fmt_doc(item.get("cnpj", ""))
        senha = (item.get("senha", "") or "").strip()
        estados = item.get("estados_esperados") or item.get("estados") or []

        if isinstance(estados, str):
            estados = [e.strip() for e in estados.split("|") if e.strip()]

        if len(cnpj) == 14 and senha:
            saida.append(CnpjInfo(cnpj=cnpj, senha=senha, estados_esperados=list(estados)))
    return saida


def _ordenar_cnpjs(cnpjs: List[CnpjInfo]) -> List[CnpjInfo]:
    pe = [c for c in cnpjs if (c.estados_esperados or []) == ["Pernambuco"]]
    sp = [c for c in cnpjs if (c.estados_esperados or []) == ["São Paulo"]]
    multi = [c for c in cnpjs if len(c.estados_esperados or []) > 1 and c not in pe and c not in sp]
    restantes = [c for c in cnpjs if c not in pe and c not in sp and c not in multi]
    return pe + sp + multi + restantes


def _prog(tipo: str, **kwargs) -> None:
    """Envia evento de progresso ao orquestrador."""
    if _progress_queue is not None:
        try:
            _progress_queue.put_nowait({"worker": WORKER_NAME, "tipo": tipo, **kwargs})
        except Exception:
            pass


def run_worker_melancia(jobs, shared_lock, progress_queue=None) -> int:
    global _shared_lock, _progress_queue
    _shared_lock = shared_lock
    _progress_queue = progress_queue

    _inicializar_master()
    if _master_obj is None:
        raise RuntimeError(
            "indice_master não disponível — impossível gerar carimbos seguros. "
            "Verifique a rede e o arquivo indice_master.py"
        )

    inicio = datetime.now()
    log.info("=" * 70)
    log.info(f"WORKER {WORKER_NAME.upper()} - NEOENERGIA")
    log.info(f"Início: {inicio.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"Temp dir: {TEMP_DOWNLOAD_DIR}")
    log.info(f"Destino: {FINAL_DOWNLOAD_ROOT}")
    log.info(f"Índice:  {INDEX_FILE}")
    log.info("=" * 70)

    _limpar_pasta_temp()

    cnpjs = _coletar_jobs(jobs)
    cnpjs = _ordenar_cnpjs(cnpjs)
    total_cnpjs = len(cnpjs)

    ja_baixados = carregar_ja_baixados()
    _prog("inicio", total=total_cnpjs)

    total_ok = 0
    for i, info in enumerate(cnpjs, start=1):
        estados_str = " + ".join(info.estados_esperados or ["?"])
        log.info("")
        log.info(f"[{i}/{total_cnpjs}] CNPJ {info.cnpj} | {estados_str}")
        _prog("cnpj_inicio", i=i, total=total_cnpjs, cnpj=info.cnpj, estados=estados_str)

        baixados_antes = total_ok
        total_ok += processar_cnpj(info, ja_baixados)
        baixados_agora = total_ok - baixados_antes

        _prog("cnpj_fim", i=i, total=total_cnpjs, cnpj=info.cnpj,
              estados=estados_str, pdfs=baixados_agora, total_pdfs=total_ok)

    fim = datetime.now()
    log.info("")
    log.info("=" * 70)
    log.info(f"Fim: {fim.strftime('%Y-%m-%d %H:%M:%S')} | Duração: {fim - inicio}")
    log.info(f"PDFs baixados nesta execução: {total_ok}")
    log.info(f"Log: {log_file}")
    log.info("=" * 70)
    _prog("fim", total_pdfs=total_ok, duracao=str(fim - inicio).split(".")[0])
    return total_ok








