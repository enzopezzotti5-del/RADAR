#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Neoenergia Selenium Worker - COELBA
- Mantém a estrutura real de índice, extrator e lógica
- Usa pasta temporária exclusiva: downloads_temp_coelba
- Não executa main sozinho
- Expõe: run_worker_coelba(jobs, shared_lock)
"""

from __future__ import annotations

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
CORE_DIR = Path(__file__).resolve().parent.parent.parent
REPO_ROOT = CORE_DIR.parent
for _path in (str(REPO_ROOT), str(CORE_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
import _venv_check  # noqa

import csv
import re
import time
import shutil
import logging
import unicodedata
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set, Iterable

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import (
    InvalidSessionIdException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

from core.downloaders.neoenergia.classificacao_ocr import organizar_downloads_neoenergia
from core.project_paths import resolve_indice_master_csv

try:
    from core.metrics.radar_metrics import emit_outcome as _emit_neo_outcome
    def _emit(outcome: str, *, instalacao: str, ref: str, carimbo: str = "") -> None:
        _emit_neo_outcome(outcome, utility="COELBA", account_id=instalacao,
                          competence=ref, invoice_id=carimbo or ref)
except Exception:
    def _emit(outcome: str, **_: str) -> None:  # type: ignore[misc]
        pass


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
_progress_queue = None  # Queue do orquestrador para reportar progresso
_ucs_alvo: Optional[Set[str]] = None  # Filtro corrente de UCs para resgates direcionados
_ucs_alvo_norm: Optional[Set[str]] = None
_ucs_alvo_default: Optional[Set[str]] = None  # Filtro base do run (quando não houver override por CNPJ)
_ucs_alvo_norm_default: Optional[Set[str]] = None
_ignorar_indice = False
_baixar_todas_faturas_ano = False
_ano_alvo: Optional[int] = None
_destino_subpasta: Optional[str] = None
_permitir_qualquer_situacao = False
_permitir_qualquer_ano = False
_refs_alvo_norm: Optional[Set[str]] = None
_pagina_inicial_ucs = 1
_skip_alvos_iniciais = 0
_usar_pesquisa_direta = True

WORKER_NAME = "coelba"

BASE_DIR = Path(__file__).resolve().parent
DEV_DIR = BASE_DIR.parent

LOG_DIR = BASE_DIR / "logs"
TEMP_DOWNLOAD_DIR = BASE_DIR / f"downloads_temp_{WORKER_NAME}"
FAILED_LOGIN_FILE = BASE_DIR / "cnpjs_falha_login.csv"

FINAL_DOWNLOAD_ROOT = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD NEOENERGIA")
INDEX_FILE = FINAL_DOWNLOAD_ROOT / "indice_downloads_neoenergia.csv"
MASTER_FILE = resolve_indice_master_csv(prefer_network=False)
PROFILE_ROOT = BASE_DIR / "chrome_profiles"
COELBA_CORRECAO_DIR = Path(r"\\10.10.250.21\Energia\ARQUIVOS ENZO\DOWNLOAD NEOENERGIA\coelba acesso correcao")

URL_PORTAL = "https://agenciavirtual.neoenergia.com"

HEADLESS = False
ANO_MINIMO = 2026
PAGE_LOAD_TIMEOUT = 120
ELEMENT_TIMEOUT = 40

PAUSE_AFTER_LOGIN = 1.2
PAUSE_PDF_SETTLE = 0.9

MOTIVO_EMISSAO = "Comprovar Residência"
INDEX_START = 2_000_000

LOG_DIR.mkdir(parents=True, exist_ok=True)
try:
    FINAL_DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

log_file = LOG_DIR / f"neoenergia_{WORKER_NAME}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
log = logging.getLogger(f"neoenergia_{WORKER_NAME}")


def _configurar_runtime(worker_name: str) -> None:
    global WORKER_NAME, TEMP_DOWNLOAD_DIR, FAILED_LOGIN_FILE, log_file, log

    WORKER_NAME = str(worker_name or "coelba").strip().lower() or "coelba"
    TEMP_DOWNLOAD_DIR = BASE_DIR / f"downloads_temp_{WORKER_NAME}"
    FAILED_LOGIN_FILE = BASE_DIR / "cnpjs_falha_login.csv"
    TEMP_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        FINAL_DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    log_file = LOG_DIR / f"neoenergia_{WORKER_NAME}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logger = logging.getLogger(f"neoenergia_{WORKER_NAME}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger.propagate = False
    log = logger


_configurar_runtime(WORKER_NAME)


def _inicializar_master() -> None:
    """Chamado apenas dentro de run_worker_coelba, nunca no nível de módulo."""
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
class CnpjInfo:
    cnpj: str
    senha: str
    estados_esperados: List[str] = None
    ucs_alvo: List[str] = None

    def __post_init__(self):
        if self.estados_esperados is None:
            self.estados_esperados = []
        if self.ucs_alvo is None:
            self.ucs_alvo = []


@dataclass
class UcTela:
    codigo: str
    status: str
    texto: str
    estado: str
    eh_filha_coletiva: bool = False
    identificadores: Optional[Set[str]] = None


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


class CampoPesquisaAusente(RuntimeError):
    pass


class ReiniciarSessaoPesquisa(RuntimeError):
    def __init__(self, skip_alvos: int, total_ok: int = 0, motivo: str = ""):
        super().__init__(motivo or "reiniciar_sessao_pesquisa")
        self.skip_alvos = max(0, int(skip_alvos or 0))
        self.total_ok = max(0, int(total_ok or 0))
        self.motivo = motivo or "reiniciar_sessao_pesquisa"


class ReiniciarSessaoLogin(RuntimeError):
    def __init__(self, motivo: str = "reiniciar_sessao_login"):
        super().__init__(motivo)
        self.motivo = motivo or "reiniciar_sessao_login"


def fmt_doc(valor: str) -> str:
    return "".join(ch for ch in str(valor) if ch.isdigit())


def _normalizar_uc(codigo: str) -> str:
    digits = fmt_doc(codigo)
    return str(int(digits)) if digits else ""


def _digits_uc(codigo: str) -> str:
    return fmt_doc(codigo)


def _normalizar_lista_ucs_alvo(ucs_alvo: Optional[Iterable[str]]) -> tuple[Optional[Set[str]], Optional[Set[str]]]:
    if ucs_alvo is None:
        return None, None

    alvo_raw = {_digits_uc(u) for u in ucs_alvo if str(u).strip() and _digits_uc(u)}
    alvo_norm = {_normalizar_uc(u) for u in ucs_alvo if str(u).strip() and _normalizar_uc(u)}
    return alvo_raw or set(), alvo_norm or set()


def _definir_filtro_uc_corrente(ucs_alvo: Optional[Iterable[str]]) -> None:
    global _ucs_alvo, _ucs_alvo_norm

    alvo_raw, alvo_norm = _normalizar_lista_ucs_alvo(ucs_alvo)
    _ucs_alvo = alvo_raw
    _ucs_alvo_norm = alvo_norm


def normalize_text(s: str) -> str:
    return " ".join((s or "").split()).strip()


def wait_ready(driver: webdriver.Chrome, timeout: int = 30) -> None:
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
    )


def save_screenshot(driver: webdriver.Chrome, name: str) -> None:
    # Screenshots desativados para Neoenergia (reduz ruido e volume de logs).
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
        time.sleep(0.2)
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


def _normalize_cmp(s: str) -> str:
    txt = normalize_text(s or "")
    txt = unicodedata.normalize("NFKD", txt)
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    return txt.lower()


def _elemento_visivel(el) -> bool:
    try:
        if not el.is_displayed():
            return False
        rect = el.rect or {}
        return float(rect.get("width", 0) or 0) > 0 and float(rect.get("height", 0) or 0) > 0
    except Exception:
        return False


def _elemento_parece_clicavel(el) -> bool:
    try:
        tag = (el.tag_name or "").strip().lower()
        if tag in {"button", "a", "input", "label"}:
            return True
        role = (el.get_attribute("role") or "").strip().lower()
        if role in {"button", "radio", "menuitem", "option", "checkbox"}:
            return True
        tipo = (el.get_attribute("type") or "").strip().lower()
        if tipo in {"button", "submit", "radio", "checkbox"}:
            return True
        classes = _normalize_cmp(el.get_attribute("class") or "")
        if any(token in classes for token in ("radio", "checkbox", "btn", "button")):
            return True
    except Exception:
        pass
    return False


def _achar_ancestral_clicavel(driver, el):
    candidatos = [
        ".",
        "./ancestor::label[1]",
        "./ancestor::button[1]",
        "./ancestor::input[@type='radio'][1]",
        "./ancestor::*[@role='radio'][1]",
        "./ancestor::button[1]",
        "./ancestor::*[@role='button'][1]",
        "./ancestor::mat-radio-button[1]",
        "./ancestor::mat-option[1]",
        "./ancestor::li[1]",
    ]
    for xp in candidatos:
        try:
            alvo = el.find_element(By.XPATH, xp)
            if _elemento_visivel(alvo) and (xp != "." or _elemento_parece_clicavel(alvo)):
                return alvo
        except Exception:
            continue
    return None


def _botao_baixar_habilitado(driver, modal=None):
    containers = [modal] if modal is not None else []
    if modal is None:
        try:
            achado = _achar_modal_emissao(driver, timeout=1)
            if achado is not None:
                containers.append(achado)
        except Exception:
            pass
    containers.append(driver)

    for container in containers:
        try:
            candidatos = container.find_elements(
                By.XPATH,
                ".//button[contains(., 'BAIXAR') or .//div[contains(normalize-space(.), 'BAIXAR')] or @title='Baixar']"
                if container is not driver
                else "//button[contains(., 'BAIXAR') or .//div[contains(normalize-space(.), 'BAIXAR')] or @title='Baixar']",
            )
        except Exception:
            continue
        for btn in candidatos:
            try:
                if not _elemento_visivel(btn):
                    continue
                disabled = (btn.get_attribute("disabled") or "").strip().lower()
                aria_disabled = (btn.get_attribute("aria-disabled") or "").strip().lower()
                classes = _normalize_cmp(btn.get_attribute("class") or "")
                if disabled or aria_disabled == "true" or "disabled" in classes:
                    continue
                return btn
            except Exception:
                continue
    return None


def _find_descendants_by_text(container, textos: Iterable[str]):
    textos_norm = [_normalize_cmp(t) for t in textos if t]
    encontrados = []
    seletores = [
        (By.XPATH, ".//*"),
        (By.CSS_SELECTOR, "button, label, span, div, li, mat-option, mat-radio-button, a"),
    ]
    vistos = set()
    for by, selector in seletores:
        try:
            elementos = container.find_elements(by, selector)
        except Exception:
            continue
        for el in elementos:
            try:
                if el.id in vistos or not _elemento_visivel(el):
                    continue
                txt = _normalize_cmp((el.text or "") + " " + (el.get_attribute("textContent") or ""))
                if txt and any(t in txt for t in textos_norm):
                    encontrados.append(el)
                    vistos.add(el.id)
            except Exception:
                continue
    return encontrados


def _achar_modal_emissao(driver, timeout: int = 12):
    deadline = time.time() + timeout
    seletores = [
        (By.CSS_SELECTOR, "div[role='dialog']"),
        (By.CSS_SELECTOR, "mat-dialog-container"),
        (By.CSS_SELECTOR, "div.modal.show"),
        (By.CSS_SELECTOR, "div.modal-content"),
        (By.CSS_SELECTOR, "div.cdk-overlay-pane"),
        (By.CSS_SELECTOR, "div.swal2-popup"),
        (By.XPATH, "//*[contains(., '2ª via da fatura') or contains(., '2a via da fatura') or contains(., 'Comprovar Residência') or contains(., 'Comprovar Residencia') or contains(., 'BAIXAR')]"),
    ]
    palavras = (
        "2 via da fatura",
        "qual motivo voce deseja emitir",
        "comprovar residencia",
        "baixar",
    )

    while time.time() < deadline:
        candidatos = []
        vistos = set()
        for by, selector in seletores:
            try:
                elementos = driver.find_elements(by, selector)
            except Exception:
                continue
            for el in elementos:
                try:
                    if el.id in vistos or not _elemento_visivel(el):
                        continue
                    texto = _normalize_cmp((el.text or "") + " " + (el.get_attribute("textContent") or ""))
                    score = sum(1 for p in palavras if p in texto)
                    if score > 0:
                        candidatos.append((score, len(texto), el))
                        vistos.add(el.id)
                except Exception:
                    continue
        if candidatos:
            candidatos.sort(key=lambda item: (item[0], item[1]), reverse=True)
            return candidatos[0][2]
        time.sleep(0.15)
    return None


def estado_slug(nome: str) -> str:
    mapa = {
        "Bahia": "COELBA",
        "Pernambuco": "CELPE",
        "Rio Grande do Norte": "COSERN",
        "Mato Grosso do Sul": "ELEKTRO",
        "São Paulo": "ELEKTRO",
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


def snapshot_temp_pdf_files() -> dict[str, tuple[int, int]]:
    snap: dict[str, tuple[int, int]] = {}
    for p in TEMP_DOWNLOAD_DIR.glob("*.pdf"):
        try:
            st = p.stat()
            snap[p.name] = (st.st_mtime_ns, st.st_size)
        except OSError:
            continue
    return snap


def wait_new_pdf(before: dict[str, tuple[int, int]] | Set[str], timeout: int = 90) -> Optional[Path]:
    if isinstance(before, set):
        before_map = {name: (0, 0) for name in before}
    else:
        before_map = dict(before)

    start = time.time()
    while time.time() - start < timeout:
        current = snapshot_temp_pdf_files()
        changed: list[tuple[int, int, str]] = []
        for name, meta in current.items():
            old_meta = before_map.get(name)
            if old_meta is None or meta != old_meta:
                changed.append((meta[0], meta[1], name))

        if changed:
            _, _, newest = sorted(changed)[-1]
            path = TEMP_DOWNLOAD_DIR / newest
            time.sleep(PAUSE_PDF_SETTLE)
            try:
                if path.exists():
                    st = path.stat()
                    if st.st_size > 0:
                        return path
            except OSError:
                pass
        time.sleep(0.35)
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


def _safe_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError as e:
        log.warning(f"Acesso ao caminho indisponível ({path}): {e}")
        return False


INDEX_FIELDS = [
    "id", "cnpj", "estado", "instalacao", "mes_referencia",
    "data_download", "data_emissao", "arquivo"
]


def _chave_indice(instalacao: str, mes_referencia: str) -> str:
    inst_norm = str(instalacao).strip().lstrip("0") or "0"
    ref_norm = _normalizar_referencia(mes_referencia)
    return f"{inst_norm}|{ref_norm}"


def carregar_ja_baixados() -> Set[str]:
    ja: Set[str] = set()
    if not _safe_exists(INDEX_FILE):
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
        log.info(f"  [CSV] id={row.get('id')!r} arquivo={str(row.get('arquivo',''))[-40:]!r}")
        new_file = not _safe_exists(INDEX_FILE)
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
    PROFILE_ROOT.mkdir(parents=True, exist_ok=True)
    profile_dir = Path(tempfile.mkdtemp(prefix=f"{WORKER_NAME}_", dir=str(PROFILE_ROOT)))

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
    options.add_argument(f"--user-data-dir={profile_dir.resolve()}")
    options.add_argument("--remote-debugging-port=0")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
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
    driver._profile_dir = profile_dir
    return driver


def _aguardar_spinner_sumir(driver, timeout: int = 30) -> None:
    try:
        WebDriverWait(driver, timeout).until(
            EC.invisibility_of_element_located((By.CSS_SELECTOR, "div.loading-spinner"))
        )
    except TimeoutException:
        pass


def fazer_login(driver: webdriver.Chrome, cnpj: str, senha: str) -> bool:
    log.info(f"Login: {cnpj}")
    try:
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
            time.sleep(0.15)
            campo_doc.send_keys(cnpj)
            time.sleep(0.2)
            valor_atual = campo_doc.get_attribute("value") or ""
            if fmt_doc(valor_atual) == fmt_doc(cnpj):
                break
            log.warning(f"CNPJ digitado incompleto (tentativa {tentativa + 1}): '{valor_atual}' esperado '{cnpj}'")
            time.sleep(0.25)

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
        time.sleep(0.1)
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

        log.info("Login OK")
        return True
    except (InvalidSessionIdException, WebDriverException) as e:
        log.warning(f"Login interrompido por sessão/Chrome inválido para {cnpj}: {e}")
        raise ReiniciarSessaoLogin("sessao_chrome_invalida_durante_login")


def _fazer_login_com_retentativa(driver: webdriver.Chrome, cnpj: str, senha: str, tentativas: int = 2) -> tuple[Optional[webdriver.Chrome], bool]:
    ultimo_driver = driver
    for tentativa in range(1, max(1, tentativas) + 1):
        try:
            ok = fazer_login(ultimo_driver, cnpj, senha)
            return ultimo_driver, ok
        except ReiniciarSessaoLogin as e:
            log.warning(
                f"Login reiniciado para {cnpj} após '{e.motivo}' "
                f"(tentativa {tentativa}/{tentativas})"
            )
            try:
                ultimo_driver.quit()
                shutil.rmtree(getattr(ultimo_driver, "_profile_dir", None) or "", ignore_errors=True)
            except Exception:
                pass
            if tentativa >= tentativas:
                raise
            ultimo_driver = build_driver()
    return ultimo_driver, False


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
        deadline = time.time() + 8
        while time.time() < deadline:
            encontrados = _coletar_cards_estado()
            if all(e in encontrados for e in esperados):
                break
            time.sleep(0.25)

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


def _tela_tem_lista_ucs(driver) -> bool:
    try:
        return len(driver.find_elements(By.XPATH, "//div[contains(@class,'box-imoveis')]")) > 0
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
        time.sleep(0.5)
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
        time.sleep(0.8)

    log.info("  Fallback: tentando driver.back()")
    for passo in range(1, 7):
        try:
            driver.back()
            time.sleep(0.6)
            if _tela_tem_selecao_estados(driver):
                log.info(f"  Tela de estados confirmada via back() ({passo} passo(s))")
                return True
        except Exception as e:
            log.warning(f"  back() falhou no passo {passo}: {e}")
            break

    save_screenshot(driver, "erro_voltar_estados")
    log.error(f"  Não foi possível voltar para seleção de estados — URL: {driver.current_url}")
    return False


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
            identificadores: Set[str] = set()

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
                        identificadores.add(txt_inst)
                        break
                except Exception:
                    continue

            # ── 2) Fallback: regex iterando todos os matches, excluindo CNPJ ─
            for m in re.finditer(r"(\d{6,15})", texto):
                candidato = m.group(1)
                # Pula somente se o candidato FOR o CNPJ completo (14 dígitos iguais)
                if cnpj_digits and len(candidato) == 14 and candidato == cnpj_digits:
                    continue
                identificadores.add(candidato)
                if not codigo:
                    codigo = candidato

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

            ucs.append(
                UcTela(
                    codigo=codigo,
                    status=status,
                    texto=texto,
                    estado=estado,
                    identificadores=identificadores,
                )
            )
        except StaleElementReferenceException:
            continue
        except Exception:
            continue
    return ucs


def _proxima_pagina_ucs(driver) -> bool:
    seletores = [
        (By.CSS_SELECTOR, "a.page-link[aria-label='Next']"),
        (By.XPATH, "//a[@aria-label='Next' and contains(@class,'page-link')]"),
        (By.XPATH, "//a[contains(@class,'page-link') and contains(normalize-space(.),'Próximo')]"),
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
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(0.25)
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
                time.sleep(0.35)
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
                    time.sleep(0.25)
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
        # Cards já estão carregados após pesquisa direta — tenta sem espera longa
        cards = find_all_now(driver, [(By.XPATH, "//div[contains(@class,'box-imoveis')]")])
        if not cards:
            cards = WebDriverWait(driver, 8).until(
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
                _aguardar_spinner_sumir(driver, timeout=15)
                return True
        except Exception:
            pass

        if click_js(driver, card, f"UC[{indice_uc}] card"):
            _aguardar_spinner_sumir(driver, timeout=15)
            return True

    except Exception as e:
        log.error(f"Falha ao entrar UC[{indice_uc}]: {e}")

    save_screenshot(driver, f"erro_entrar_uc_{indice_uc}")
    return False


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
                    time.sleep(0.2)
                    return True
            except Exception:
                continue
        time.sleep(0.2)
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
            WebDriverWait(driver, 2).until(
                lambda d: painel.find_element(
                    By.XPATH, ".//mat-expansion-panel-header"
                ).get_attribute("aria-expanded") == "true"
            )
    except Exception:
        pass


def listar_faturas_na_tela(driver, max_paineis: int = 0, parar_apos_n: int = 0) -> List[FaturaTela]:
    """
    parar_apos_n > 0: para de expandir painéis assim que encontrar N faturas válidas.
    Útil no modo "última conta" onde só precisamos da fatura mais recente.
    max_paineis > 0: limita o total de painéis expandidos (aplicado após parar_apos_n).
    """
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
        save_screenshot(driver, "erro_sem_paineis_faturas")
        log.warning("  Nenhum painel de fatura encontrado")
        return faturas

    total_paineis = len(paineis)
    if max_paineis and max_paineis < total_paineis:
        paineis = paineis[:max_paineis]
    log.info(f"  Painéis de fatura encontrados ({total_paineis}) via: {seletor_usado}")

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

                log.info(f"  FAT[{i}] sit via classe: cls='{cls_span}' txt='{texto_sit}' → {situacao}")

            except Exception:
                try:
                    bloco_sit = painel.find_element(By.XPATH, ".//*[contains(@class,'fatura-situacao')]")
                    texto_sit = normalize_text(bloco_sit.text).lower()
                    cls_sit = (bloco_sit.get_attribute("class") or "").lower()

                    if "vencer" in cls_sit or "a vencer" in texto_sit:
                        situacao = "A VENCER"
                    elif "paga" in cls_sit or "pago" in cls_sit or texto_sit in {"paga", "pago"}:
                        situacao = "PAGO"
                    elif "vencida" in cls_sit or "vencido" in cls_sit or "vencida" in texto_sit or "vencido" in texto_sit:
                        situacao = "VENCIDA"
                    elif "aberto" in cls_sit or "pendente" in cls_sit or "aberto" in texto_sit or "pendente" in texto_sit:
                        situacao = "EM ABERTO"

                    log.info(f"  FAT[{i}] sit via bloco: cls='{cls_sit}' txt='{texto_sit}' → {situacao}")
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
                log.info(f"  FAT[{i}] sit via texto bruto → {situacao}")

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

            if parar_apos_n and len(faturas) >= parar_apos_n:
                log.info(f"  Early exit: {parar_apos_n} fatura(s) encontrada(s) no painel {i}/{total_paineis}")
                break

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
    if _permitir_qualquer_ano:
        return True
    m = re.search(r"/(\d{4})", normalize_text(referencia))
    if not m:
        return False
    try:
        ano = int(m.group(1))
        if _ano_alvo is not None:
            return ano == _ano_alvo
        return ano >= ANO_MINIMO
    except Exception:
        return False


def _normalizar_referencia(ref: str) -> str:
    ref = normalize_text(ref).upper()
    m_num = re.search(r"(\d{2})\s*/\s*(\d{4})", ref)
    if m_num:
        return f"{int(m_num.group(1)):02d}/{m_num.group(2)}"

    meses = {
        "JANEIRO": "01", "FEVEREIRO": "02", "MARCO": "03", "MARÇO": "03",
        "ABRIL": "04", "MAIO": "05", "JUNHO": "06", "JULHO": "07",
        "AGOSTO": "08", "SETEMBRO": "09", "OUTUBRO": "10",
        "NOVEMBRO": "11", "DEZEMBRO": "12",
    }
    m_txt = re.search(r"([A-ZÁÇÃÉÊÍÓÔÚ]+)\s*/\s*(\d{4})", ref)
    if m_txt:
        mes = meses.get(m_txt.group(1), "00")
        return f"{mes}/{m_txt.group(2)}"
    return ref


def _ordem_ref(ref: str) -> tuple[int, int]:
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


def situacao_slug(situacao: str) -> str:
    base = normalize_text(situacao).upper()
    mapa = {
        "A VENCER": "A_VENCER",
        "EM ABERTO": "EM_ABERTO",
        "VENCIDA": "VENCIDA",
        "PAGO": "PAGO",
        "DESCONHECIDA": "DESCONHECIDA",
    }
    return mapa.get(base, re.sub(r"[^A-Z0-9]+", "_", base).strip("_") or "SEM_SITUACAO")


def _fluxo_direcionado_ativo() -> bool:
    return any(
        [
            _ucs_alvo is not None,
            _destino_subpasta,
            _refs_alvo_norm is not None,
            _ignorar_indice,
            _baixar_todas_faturas_ano,
            _permitir_qualquer_situacao,
            _permitir_qualquer_ano,
            _pagina_inicial_ucs > 1,
            _skip_alvos_iniciais > 0,
        ]
    )


def _pasta_destino_referencia(estado: str, situacao: str, referencia: str) -> Path:
    estado_dir = FINAL_DOWNLOAD_ROOT / estado_slug(estado)
    if _destino_subpasta:
        estado_dir = estado_dir / _destino_subpasta

    if _fluxo_direcionado_ativo():
        return estado_dir / situacao_slug(situacao) / referencia_to_folder(referencia)

    return estado_dir / referencia_to_folder(referencia)


def _deve_usar_pesquisa_direta() -> bool:
    return _usar_pesquisa_direta and _ucs_alvo is not None and len(_ucs_alvo) >= 1


def _uc_casa_com_alvos(uc: UcTela) -> bool:
    if _ucs_alvo is None:
        return True

    candidatos_raw: Set[str] = set()
    candidatos_norm: Set[str] = set()
    codigo_raw = _digits_uc(uc.codigo or "")
    codigo_norm = _normalizar_uc(uc.codigo or "")
    if codigo_raw:
        candidatos_raw.add(codigo_raw)
    if codigo_norm:
        candidatos_norm.add(codigo_norm)

    for ident in (uc.identificadores or set()):
        ident_raw = _digits_uc(ident)
        ident_norm = _normalizar_uc(ident)
        if ident_raw:
            candidatos_raw.add(ident_raw)
        if ident_norm:
            candidatos_norm.add(ident_norm)

    if candidates_raw := (candidatos_raw & _ucs_alvo):
        log.info(f"  [RESGATE] Match UC por identificador bruto: codigo={uc.codigo or 'SEM_CODIGO'} chaves={sorted(candidates_raw)}")
        return True
    if _ucs_alvo_norm and (candidates_norm := (candidatos_norm & _ucs_alvo_norm)):
        log.info(f"  [RESGATE] Match UC por identificador normalizado: codigo={uc.codigo or 'SEM_CODIGO'} chaves={sorted(candidates_norm)}")
        return True
    return False


def _marcador_pagina_ucs(driver) -> str:
    partes: List[str] = []
    seletores_rotulo = [
        (By.CSS_SELECTOR, ".mat-mdc-paginator-range-label"),
        (By.CSS_SELECTOR, ".mat-paginator-range-label"),
        (By.XPATH, "//*[contains(@class,'paginator') and contains(@class,'range')]"),
    ]
    for by, sel in seletores_rotulo:
        try:
            els = driver.find_elements(by, sel)
            for el in els:
                txt = normalize_text(el.text)
                if txt:
                    partes.append(txt)
                    raise StopIteration
        except StopIteration:
            break
        except Exception:
            continue

    try:
        cards = driver.find_elements(By.XPATH, "//div[contains(@class,'box-imoveis')]")
        for card in cards[:2]:
            txt = normalize_text(card.text)
            if txt:
                partes.append(txt[:120])
    except Exception:
        pass

    return " | ".join(partes)


def _aguardar_mudanca_pagina_ucs(driver, marcador_anterior: str, timeout: float = 8.0) -> bool:
    deadline = time.time() + max(1.0, timeout)
    while time.time() < deadline:
        _aguardar_spinner_sumir(driver, timeout=2)
        atual = _marcador_pagina_ucs(driver)
        if atual and atual != marcador_anterior:
            return True
        time.sleep(0.15)
    return False


def _avancar_para_proxima_pagina_ucs(driver, timeout: float = 2.5) -> bool:
    marcador_antes = _marcador_pagina_ucs(driver)
    for tentativa in range(1, 4):
        _aguardar_spinner_sumir(driver, timeout=3)
        if not _proxima_pagina_ucs(driver):
            return False
        if _aguardar_mudanca_pagina_ucs(driver, marcador_antes, timeout=timeout):
            log.info("  Próxima página confirmada")
            return True
        log.warning(f"  Próxima página sem mudança visível (tentativa {tentativa}/3)")
        time.sleep(0.2)
    return False


def _seletores_input_pesquisa_ucs():
    return [
        (By.CSS_SELECTOR, "input.pesquisar"),
        (By.CSS_SELECTOR, "input.pesquisar[placeholder*='digo do Cliente']"),
        (By.CSS_SELECTOR, "input[placeholder*='digo do Cliente']"),
        (By.XPATH, "//input[contains(@placeholder,'digo do Cliente')]"),
        (By.XPATH, "//input[contains(@class,'pesquisar')]"),
    ]


def _seletores_botao_pesquisa_ucs():
    return [
        (By.XPATH, "//span[contains(normalize-space(.), 'Pesquisar')]/ancestor::button[1]"),
        (By.XPATH, "//button[.//span[contains(normalize-space(.), 'Pesquisar')]]"),
        (By.XPATH, "//button[contains(., 'Pesquisar')]"),
    ]


def _limpar_input(el) -> None:
    try:
        el.send_keys(Keys.CONTROL, "a")
        el.send_keys(Keys.BACKSPACE)
    except Exception:
        pass
    try:
        el.clear()
    except Exception:
        pass


def _pesquisar_codigo_cliente_ucs(driver, codigo: str, timeout: float = 6.0) -> bool:
    codigo_raw = _digits_uc(codigo)
    codigo_norm = _normalizar_uc(codigo)
    variantes = []
    for valor in (codigo_raw, codigo_norm):
        if valor and valor not in variantes:
            variantes.append(valor)

    if not variantes:
        return False

    campo = find_first(driver, _seletores_input_pesquisa_ucs(), timeout=4)
    if not campo:
        raise CampoPesquisaAusente("campo de pesquisa de Código do Cliente não encontrado")

    for tentativa, termo in enumerate(variantes, start=1):
        marcador_antes = _marcador_pagina_ucs(driver)
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", campo)
        except Exception:
            pass

        try:
            campo.click()
        except Exception:
            pass
        _limpar_input(campo)
        time.sleep(0.05)
        campo.send_keys(termo)
        log.info(f"  [RESGATE] Pesquisando código do cliente: {termo} (tentativa {tentativa}/{len(variantes)})")

        if not wait_clickable_and_click(driver, _seletores_botao_pesquisa_ucs(), timeout=5, description="pesquisar código do cliente"):
            raise CampoPesquisaAusente(f"botão Pesquisar não encontrado para código {termo}")

        deadline = time.time() + max(2.5, timeout)
        while time.time() < deadline:
            _aguardar_spinner_sumir(driver, timeout=2)
            atuais = _ucs_pagina_atual(driver, "DESCONHECIDO")
            if atuais:
                # Match exato: código está nos identificadores do card
                if any(codigo_norm in {
                    _normalizar_uc(u.codigo or ""),
                    *{_normalizar_uc(i) for i in (u.identificadores or set())}
                } for u in atuais):
                    return True
                # Match relaxado: página mudou e retornou exatamente 1 card
                # (portal correlacionou conta contrato → instalação; o card mostra o
                # número de instalação, não o código pesquisado)
                marcador_atual = _marcador_pagina_ucs(driver)
                if len(atuais) == 1 and (
                    not marcador_antes or marcador_atual != marcador_antes
                ):
                    log.info(
                        f"  [RESGATE] Pesquisa por {codigo_norm} retornou 1 card "
                        f"(codigo={atuais[0].codigo!r}) sem match de identificador — aceitando"
                    )
                    return True
            marcador_atual = _marcador_pagina_ucs(driver)
            if marcador_atual and marcador_atual != marcador_antes and atuais:
                return True
            time.sleep(0.1)

    log.warning(f"  Pesquisa do código {codigo_raw or codigo_norm} não retornou UCs visíveis")
    return False


def _processar_alvos_por_pesquisa(driver, estado: str, cnpj: str, ja_baixados: Set[str]) -> int:
    total_ok = 0
    processados: Set[str] = set()
    alvos_ordenados = sorted(_ucs_alvo or set(), key=lambda x: (-len(x), x))
    if _skip_alvos_iniciais > 0:
        log.info(f"  [RESGATE] Retomada configurada: pulando {_skip_alvos_iniciais} código(s) já percorridos")
        alvos_ordenados = alvos_ordenados[_skip_alvos_iniciais:]

    for pos_alvo, alvo in enumerate(alvos_ordenados, start=1):
        alvo_raw = _digits_uc(alvo)
        alvo_norm = _normalizar_uc(alvo)
        if not alvo_norm or alvo_norm in processados:
            continue

        log.info(f"  [RESGATE] Pesquisa direta {pos_alvo}/{len(alvos_ordenados)}: código {alvo_raw or alvo_norm}")
        try:
            encontrou_pesquisa = _pesquisar_codigo_cliente_ucs(driver, alvo_raw or alvo_norm)
        except CampoPesquisaAusente as e:
            raise ReiniciarSessaoPesquisa(
                skip_alvos=_skip_alvos_iniciais + pos_alvo - 1,
                total_ok=total_ok,
                motivo=str(e),
            )
        if not encontrou_pesquisa:
            _prog("uc_fim", uc=alvo_norm, estado=estado, pagina=0, pdfs=0, status="nao_encontrada")
            continue

        ucs = _ucs_pagina_atual(driver, estado, cnpj=cnpj)
        alvo_pagina = [u for u in ucs if _uc_casa_com_alvos(u)]
        if not alvo_pagina:
            # Match formal falhou (o portal pode mostrar o número de instalação no card
            # em vez do código pesquisado, ex.: conta contrato). Se a pesquisa trouxe
            # apenas 1 card, usamos ele diretamente — a pesquisa pelo portal já fez a
            # correlação correta.
            if len(ucs) == 1:
                log.info(
                    f"  [RESGATE] Match relaxado: pesquisa por {alvo_norm} retornou 1 UC "
                    f"(codigo={ucs[0].codigo!r}) sem match formal — aceitando como alvo"
                )
                alvo_pagina = ucs
            else:
                log.info(f"  [RESGATE] Nenhuma UC correspondente ao código {alvo_norm} após pesquisa ({len(ucs)} cards)")
                _prog("uc_fim", uc=alvo_norm, estado=estado, pagina=0, pdfs=0, status="nao_encontrada")
                continue

        uc = alvo_pagina[0]
        indice_na_pagina = ucs.index(uc) + 1
        chaves_uc = {_normalizar_uc(uc.codigo or "")}
        chaves_uc.update({_normalizar_uc(i) for i in (uc.identificadores or set())})
        processados.update({c for c in chaves_uc if c})

        baixados_uc = 0
        try:
            _prog("uc_inicio", uc=uc.codigo or alvo_norm, estado=estado, i=pos_alvo, total=len(alvos_ordenados), pagina=0)
            baixados_uc = processar_uc(
                driver=driver,
                indice_uc=indice_na_pagina,
                estado=estado,
                cnpj=cnpj,
                instalacao=uc.codigo or alvo_norm,
                ja_baixados=ja_baixados,
            )
        except Exception as e:
            log.error(f"  Erro na UC pesquisada {alvo_norm}: {e}")
            save_screenshot(driver, f"erro_uc_pesquisa_{alvo_norm}_{estado_slug(estado)}")

        total_ok += baixados_uc
        _prog("uc_fim", uc=uc.codigo or alvo_norm, estado=estado, pagina=0, pdfs=baixados_uc, status=("download_ok" if baixados_uc > 0 else "sem_fatura"))

        voltou = False
        for _ in range(3):
            try:
                driver.back()
                time.sleep(0.15)
                _aguardar_spinner_sumir(driver, timeout=5)
                if find_first(driver, _seletores_input_pesquisa_ucs(), timeout=3):
                    voltou = True
                    break
                if driver.find_elements(By.XPATH, "//div[contains(@class,'box-imoveis')]"):
                    voltou = True
                    break
            except Exception:
                break

        if not voltou:
            log.warning(f"  Não voltou para a lista pesquisável após código {alvo_norm} — tentando recuperar")
            try:
                url_home = URL_PORTAL.rstrip("/") + "/#/home"
                driver.get(url_home)
                _aguardar_spinner_sumir(driver, timeout=8)
                if _tela_tem_selecao_estados(driver):
                    selecionar_estado(driver, estado)
                    _aguardar_spinner_sumir(driver, timeout=8)
                if find_first(driver, _seletores_input_pesquisa_ucs(), timeout=8):
                    log.info("  [RESGATE] Lista pesquisável recuperada com sucesso")
                    continue
            except Exception as e:
                log.warning(f"  [RESGATE] Falha ao recuperar lista pesquisável: {e}")
            raise ReiniciarSessaoPesquisa(
                skip_alvos=_skip_alvos_iniciais + pos_alvo,
                total_ok=total_ok,
                motivo=f"não voltou para a lista pesquisável após código {alvo_norm}",
            )

    return total_ok


def _marcar_checkbox_fatura(driver, ref: str) -> bool:
    candidatos_checkbox = [
        f"//*[contains(normalize-space(.), '{ref}')]/preceding::input[contains(@class,'mat-checkbox-input')][1]",
        f"//*[contains(normalize-space(.), '{ref}')]/preceding::mat-checkbox[1]",
        f"//*[contains(normalize-space(.), '{ref}')]/ancestor::mat-expansion-panel[1]//input[contains(@class,'mat-checkbox-input')]",
        f"//*[contains(normalize-space(.), '{ref}')]/ancestor::mat-expansion-panel[1]//mat-checkbox",
    ]

    for xp in candidatos_checkbox:
        try:
            els = driver.find_elements(By.XPATH, xp)
            for el in els:
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                    time.sleep(0.15)
                    driver.execute_script("arguments[0].click();", el)
                    return True
                except Exception:
                    continue
        except Exception:
            continue
    return False


def selecionar_faturas_pendentes(
    driver,
    ja_baixados: Set[str],
    cnpj: str,
    instalacao: str,
    refs_ignoradas: Optional[Set[str]] = None,
) -> List[FaturaTela]:
    log.info(f"  Seleção de faturas para instalação={instalacao} cnpj={cnpj}")

    # Varre sempre todos os painéis disponíveis (até `total_paineis`, tipicamente
    # <=24). O corte antigo (parar_apos_n=1 fora dos modos "todas as faturas"/
    # "refs alvo") parava no primeiro painel lido, e se essa fatura estivesse
    # VENCIDA ou já constasse no índice, nenhuma fatura elegível em painéis
    # seguintes era vista — causa raiz de baixadas=0 mesmo com faturas elegíveis
    # disponíveis. A classificação/dedupe abaixo já opera corretamente sobre a
    # lista completa (era o caminho já exercitado pelos modos especiais).
    faturas = listar_faturas_na_tela(driver, parar_apos_n=0)
    if not faturas:
        log.info("  Nenhuma fatura encontrada na tela")
        return []

    refs_ignoradas = refs_ignoradas or set()
    elegiveis = []
    for f in faturas:
        motivo = None
        ref_norm = _normalizar_referencia(f.referencia)

        if not _permitir_qualquer_situacao and not _baixar_todas_faturas_ano and f.situacao != "A VENCER":
            motivo = f"situacao={f.situacao}"
        elif f.minimo:
            motivo = "fatura mínima"
        elif _refs_alvo_norm is not None and _normalizar_referencia(f.referencia) not in _refs_alvo_norm:
            motivo = "fora das referências alvo"
        elif not _ano_referencia_valido(f.referencia):
            motivo = f"ano inválido ref={f.referencia}"
        elif ref_norm in refs_ignoradas:
            motivo = "já processada nesta execução"
        elif not _ignorar_indice and ja_foi_baixado(ja_baixados, instalacao, f.referencia):
            motivo = "já consta no índice"
            _emit("skipped_existing", instalacao=instalacao, ref=f.referencia)

        if motivo:
            log.info(f"  DESCARTADA ref={f.referencia} | {motivo}")
            continue

        elegiveis.append(f)

    if not elegiveis:
        log.info(f"  Nenhuma fatura elegível para instalação {instalacao}")
        return []

    elegiveis = sorted(elegiveis, key=lambda x: _ordem_ref(x.referencia), reverse=True)
    if _baixar_todas_faturas_ano:
        refs = ", ".join(f.referencia for f in elegiveis)
        log.info(f"  Faturas elegíveis do ano alvo: {refs}")
        return elegiveis

    escolhida = elegiveis[0]
    if _permitir_qualquer_situacao or _permitir_qualquer_ano or _refs_alvo_norm is not None:
        log.info(
            f"  Selecionando a última referência elegível: "
            f"{escolhida.referencia} | situação={escolhida.situacao}"
        )
    else:
        log.info(f"  Selecionando apenas a última A VENCER: {escolhida.referencia}")

    try:
        if not _marcar_checkbox_fatura(driver, escolhida.referencia):
            log.warning(f"  Não foi possível marcar checkbox da fatura {escolhida.referencia}")
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
                    time.sleep(0.2)
                    driver.execute_script("arguments[0].click();", el)
                    return True
                except Exception:
                    continue
        except Exception:
            continue

    save_screenshot(driver, "erro_botao_download_inicial")
    return False


def selecionar_motivo_emissao(driver, motivo: str = MOTIVO_EMISSAO) -> bool:
    modal = _achar_modal_emissao(driver, timeout=20)
    if not modal:
        save_screenshot(driver, "erro_modal_motivo_emissao")
        return False

    # Primeiro tenta selecionar o radio/label correto dentro do pop-up.
    radios = [
        (By.XPATH, f".//label[contains(normalize-space(.), '{motivo}')]"),
        (By.XPATH, f".//*[contains(normalize-space(.), '{motivo}')]/ancestor::label[1]"),
        (By.XPATH, f".//*[contains(normalize-space(.), '{motivo}')]/preceding::input[@type='radio'][1]"),
        (By.XPATH, f".//*[contains(normalize-space(.), '{motivo}')]/ancestor::*[@role='radio'][1]"),
    ]
    for by, selector in radios:
        try:
            for el in modal.find_elements(by, selector):
                if not _elemento_visivel(el):
                    continue
                alvo = _achar_ancestral_clicavel(driver, el) or el
                if _clicar_elemento(driver, alvo, f"radio motivo='{motivo}'"):
                    time.sleep(0.25)
                    if _botao_baixar_habilitado(driver, modal):
                        return True
        except Exception:
            continue

    # Depois tenta clicar por texto, mas só aceita como sucesso quando o botão BAIXAR habilita.
    for el in _find_descendants_by_text(modal, [motivo]):
        alvo = _achar_ancestral_clicavel(driver, el) or el
        if _clicar_elemento(driver, alvo, f"motivo='{motivo}'"):
            time.sleep(0.25)
            if _botao_baixar_habilitado(driver, modal):
                return True

    # Se o modal usar select/combobox, limita a busca só ao conteúdo do próprio pop-up.
    try:
        selects = modal.find_elements(By.TAG_NAME, "select")
    except Exception:
        selects = []
    for s in selects:
        try:
            if not _elemento_visivel(s):
                continue
            sel = Select(s)
            for opt in sel.options:
                if _normalize_cmp(motivo) in _normalize_cmp(opt.text):
                    sel.select_by_visible_text(opt.text)
                    time.sleep(0.25)
                    return True
        except Exception:
            continue

    combos = [
        (By.XPATH, ".//label[contains(., 'Motivo')]/following::*[self::div or self::input][1]"),
        (By.XPATH, ".//*[contains(text(), 'Motivo')]/following::*[self::div or self::input][1]"),
        (By.XPATH, ".//input[@role='combobox']"),
        (By.XPATH, ".//*[@role='combobox']"),
    ]

    for by, selector in combos:
        try:
            for el in modal.find_elements(by, selector):
                if not _elemento_visivel(el):
                    continue
                if not _clicar_elemento(driver, el, "combobox motivo emissao"):
                    continue
                time.sleep(0.2)

                for opcao in _find_descendants_by_text(driver, [motivo]):
                    alvo = _achar_ancestral_clicavel(driver, opcao) or opcao
                    if _clicar_elemento(driver, alvo, f"opcao motivo='{motivo}'"):
                        time.sleep(0.25)
                        if _botao_baixar_habilitado(driver, modal):
                            return True
        except Exception:
            continue

    save_screenshot(driver, "erro_motivo_emissao")
    return False


def confirmar_baixar(driver) -> bool:
    modal = _achar_modal_emissao(driver, timeout=10)
    if modal:
        btn_habilitado = _botao_baixar_habilitado(driver, modal)
        if btn_habilitado is not None:
            if _clicar_elemento(driver, btn_habilitado, "confirmar baixar habilitado"):
                return True

    if modal:
        for el in _find_descendants_by_text(modal, ["BAIXAR"]):
            alvo = _achar_ancestral_clicavel(driver, el) or el
            if _elemento_parece_clicavel(alvo) and _clicar_elemento(driver, alvo, "confirmar baixar modal"):
                return True

        seletores_modal = [
            (By.XPATH, ".//button[@title='Baixar' and contains(@class,'btn-neodarkgreen')]"),
            (By.XPATH, ".//button[contains(@class,'btn-neodarkgreen')][.//div[contains(normalize-space(.), 'BAIXAR')]]"),
            (By.XPATH, ".//button[@title='Baixar']"),
            (By.XPATH, ".//button[contains(., 'BAIXAR')]"),
        ]
        for by, selector in seletores_modal:
            try:
                for btn in modal.find_elements(by, selector):
                    if _elemento_visivel(btn) and _clicar_elemento(driver, btn, "botao baixar modal"):
                        return True
            except Exception:
                continue

    seletores = [
        (By.XPATH, "//div[@role='dialog']//button[contains(., 'BAIXAR')]"),
        (By.XPATH, "//mat-dialog-container//button[contains(., 'BAIXAR')]"),
        (By.XPATH, "//button[@title='Baixar' and contains(@class,'btn-neodarkgreen')]"),
    ]
    for by, selector in seletores:
        try:
            btn = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((by, selector)))
            if _clicar_elemento(driver, btn, "confirmar baixar fallback"):
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
            time.sleep(0.15)
            driver.execute_script("arguments[0].click();", btn)
            return True
        except Exception:
            continue
    return False


def capturar_popup_resultado(driver, timeout: int = 25) -> tuple[bool, Optional[str], Optional[str]]:
    try:
        popup = WebDriverWait(driver, timeout).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, "div.swal2-popup"))
        )
    except Exception:
        return False, None, None

    partes: List[str] = []
    seletores_texto = [
        (By.CSS_SELECTOR, ".swal2-title"),
        (By.CSS_SELECTOR, ".swal2-html-container"),
        (By.CSS_SELECTOR, ".swal2-content"),
    ]
    for by, selector in seletores_texto:
        try:
            for el in popup.find_elements(by, selector):
                texto = normalize_text((el.text or "") + " " + (el.get_attribute("textContent") or ""))
                if texto:
                    partes.append(texto)
        except Exception:
            continue

    texto_popup = normalize_text(" ".join(partes)) or normalize_text(
        (popup.text or "") + " " + (popup.get_attribute("textContent") or "")
    )
    texto_norm = _normalize_cmp(texto_popup)

    tipo = "info"
    marcadores_erro = (
        "indispon",
        "nao foi possivel",
        "erro",
        "falha",
        "nao disponivel",
        "pdf",
        "tente novamente",
    )
    marcadores_sucesso = (
        "sucesso",
        "solicitacao realizada",
        "solicitacao efetuada",
        "gerado",
        "emitido",
        "download iniciado",
    )
    if any(token in texto_norm for token in marcadores_erro):
        tipo = "erro"
    elif any(token in texto_norm for token in marcadores_sucesso):
        tipo = "sucesso"

    confirmar_popup_sucesso(driver)
    return True, tipo, texto_popup or None


def mover_pdf_para_destino(pdf_temp: Path, estado: str, situacao: str, referencia: str, carimbo: str) -> Path:
    mes_dir = _pasta_destino_referencia(estado, situacao, referencia)
    mes_dir.mkdir(parents=True, exist_ok=True)  # exceção sobe se falhar — intencional

    destino = mes_dir / f"{carimbo}.pdf"

    log.info(f"  [MOVE] temp={pdf_temp.name!r} carimbo={carimbo!r} destino_pretendido={destino.name!r}")

    if destino.exists():
        i = 2
        while True:
            alt = mes_dir / f"{carimbo}_{i}.pdf"
            if not alt.exists():
                destino = alt
                break
            i += 1
        log.info(f"  [MOVE] conflito -> destino ajustado={destino.name!r}")

    if destino.is_dir():
        log.error(f"  [MOVE] DESTINO É DIRETÓRIO (shutil.move usaria nome original!): {destino}")
    resultado = shutil.move(str(pdf_temp), str(destino))
    resultado_path = Path(resultado) if resultado else destino
    log.info(f"  [MOVE] ok -> pretendido={destino.name!r} shutil_retornou={resultado_path.name!r}")
    if resultado_path.name != destino.name:
        log.error(f"  [MOVE] DIVERGÊNCIA: destino={destino.name!r} != resultado={resultado_path.name!r}")
    return destino


def _baixar_fatura_selecionada(
    driver,
    estado: str,
    cnpj: str,
    instalacao: str,
    ja_baixados: Set[str],
    f: FaturaTela,
) -> int:
    if not clicar_download_faturas(driver):
        log.error(f"  Falha ao clicar no botão de download na instalação {instalacao}")
        return 0

    if not selecionar_motivo_emissao(driver, MOTIVO_EMISSAO):
        log.error(f"  Falha ao selecionar motivo na instalação {instalacao}")
        return 0

    before = snapshot_temp_pdf_files()

    if not confirmar_baixar(driver):
        log.error(f"  Falha ao confirmar baixar na instalação {instalacao}")
        return 0

    houve_popup, tipo_popup, texto_popup = capturar_popup_resultado(driver)
    if houve_popup and texto_popup:
        log.info(f"  Popup após confirmar baixar [{tipo_popup}]: {texto_popup}")
    if tipo_popup == "erro":
        log.warning(f"  Portal retornou indisponibilidade/erro do PDF na instalação {instalacao}")
        return 0

    pdf = wait_new_pdf(before, timeout=90)
    if not pdf:
        log.warning(f"  Nenhum novo PDF detectado na instalação {instalacao}")
        save_screenshot(driver, f"sem_pdf_{estado_slug(estado)}_{instalacao}")
        return 0

    if _shared_lock is not None:
        with _shared_lock:
            if not _ignorar_indice and ja_foi_baixado(ja_baixados, instalacao, f.referencia):
                try:
                    if pdf.exists():
                        pdf.unlink(missing_ok=True)
                except Exception:
                    pass
                log.info(f"  Fatura já registrada por outro worker: {instalacao} | {f.referencia}")
                _emit("skipped_existing", instalacao=instalacao, ref=f.referencia)
                return 0

            try:
                mes_dir = _pasta_destino_referencia(estado, f.situacao, f.referencia)
                mes_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                log.error(f"  Erro ao criar pasta de destino ({f.referencia}): {e}")
                try:
                    if pdf.exists():
                        pdf.unlink(missing_ok=True)
                except Exception:
                    pass
                return 0

            carimbo = next_index_id()
            destino = mover_pdf_para_destino(pdf, estado, f.situacao, f.referencia, carimbo)
            row = {
                "id": carimbo,
                "cnpj": fmt_doc(cnpj),
                "estado": estado,
                "instalacao": instalacao,
                "mes_referencia": _normalizar_referencia(f.referencia),
                "data_download": current_timestamp_str(),
                "data_emissao": f.data_emissao,
                "arquivo": str(destino),
            }
            append_index_row(row, ja_baixados)
    else:
        if not _ignorar_indice and ja_foi_baixado(ja_baixados, instalacao, f.referencia):
            try:
                if pdf.exists():
                    pdf.unlink(missing_ok=True)
            except Exception:
                pass
            _emit("skipped_existing", instalacao=instalacao, ref=f.referencia)
            return 0

        try:
            mes_dir = _pasta_destino_referencia(estado, f.situacao, f.referencia)
            mes_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            log.error(f"  Erro ao criar pasta de destino ({f.referencia}): {e}")
            try:
                if pdf.exists():
                    pdf.unlink(missing_ok=True)
            except Exception:
                pass
            return 0

        carimbo = next_index_id()
        destino = mover_pdf_para_destino(pdf, estado, f.situacao, f.referencia, carimbo)
        row = {
            "id": carimbo,
            "cnpj": fmt_doc(cnpj),
            "estado": estado,
            "instalacao": instalacao,
            "mes_referencia": _normalizar_referencia(f.referencia),
            "data_download": current_timestamp_str(),
            "data_emissao": f.data_emissao,
            "arquivo": str(destino),
        }
        append_index_row(row, ja_baixados)

    _emit("downloaded", instalacao=instalacao, ref=f.referencia, carimbo=carimbo)
    _prog("download_ok", uc=instalacao, estado=estado, referencia=f.referencia, carimbo=carimbo, status="baixada")
    log.info(f"  Download concluído: instalação={instalacao} ref={f.referencia}")
    return 1


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
    if not _baixar_todas_faturas_ano:
        selecionadas = selecionar_faturas_pendentes(driver, ja_baixados, cnpj, instalacao)
        for fat in selecionadas:
            _prog("fatura", uc=instalacao, estado=estado, referencia=fat.referencia, situacao=fat.situacao)
        if not selecionadas:
            log.info(f"  Nenhuma fatura nova elegível na instalação {instalacao}")
            return 0
        return _baixar_fatura_selecionada(driver, estado, cnpj, instalacao, ja_baixados, selecionadas[0])

    refs_processadas_local: Set[str] = set()
    total_uc = 0

    while True:
        elegiveis = selecionar_faturas_pendentes(
            driver,
            ja_baixados,
            cnpj,
            instalacao,
            refs_ignoradas=refs_processadas_local,
        )
        if not elegiveis:
            if total_uc == 0:
                log.info(f"  Nenhuma fatura nova elegível na instalação {instalacao}")
            break

        fat = elegiveis[0]
        refs_processadas_local.add(_normalizar_referencia(fat.referencia))
        _prog("fatura", uc=instalacao, estado=estado, referencia=fat.referencia, situacao=fat.situacao)
        log.info(f"  Selecionando fatura do ano alvo: {fat.referencia} | situação={fat.situacao}")

        if not _marcar_checkbox_fatura(driver, fat.referencia):
            log.warning(f"  Não foi possível marcar checkbox da fatura {fat.referencia}")
            break

        ok = _baixar_fatura_selecionada(driver, estado, cnpj, instalacao, ja_baixados, fat)
        if not ok:
            break
        total_uc += ok

        try:
            driver.refresh()
            wait_ready(driver, timeout=30)
            _aguardar_tela_faturas_carregar(driver, timeout=25)
            time.sleep(0.4)
        except Exception as e:
            log.warning(f"  Falha ao recarregar tela de faturas após download: {e}")
            break

    return total_uc


def _ucs_pagina_atual(driver, estado: str, cnpj: str = "") -> List[UcTela]:
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, "//div[contains(@class,'box-imoveis')]"))
        )
    except TimeoutException:
        pass
    return _coletar_ucs_pagina(driver, estado, vistos=set(), cnpj_atual=cnpj)


def _aguardar_ucs_pagina(driver, estado: str, cnpj: str = "", timeout: int = 90, intervalo: float = 2.0) -> List[UcTela]:
    deadline = time.time() + max(5, timeout)
    tentativa = 0
    ultimo_total = 0

    while time.time() < deadline:
        tentativa += 1
        _aguardar_spinner_sumir(driver, timeout=10)
        ucs = _ucs_pagina_atual(driver, estado, cnpj=cnpj)
        if ucs:
            if tentativa > 1:
                log.info(f"  Grade de UCs carregou após {tentativa} tentativa(s): {len(ucs)} UC(s)")
            return ucs

        try:
            total_boxes = len(driver.find_elements(By.XPATH, "//div[contains(@class,'box-imoveis')]"))
        except Exception:
            total_boxes = 0
        ultimo_total = total_boxes
        espera = min(3.0, intervalo if tentativa <= 3 else intervalo + 0.5)
        log.info(
            f"  Grade de UCs ainda vazia para {estado} "
            f"(tentativa {tentativa}, elementos={total_boxes}) — aguardando {espera:.1f}s"
        )
        time.sleep(espera)

    log.warning(f"  Timeout aguardando grade de UCs para {estado} — último total visível: {ultimo_total}")
    return []


def _assinatura_ucs(ucs: List[UcTela]) -> Set[str]:
    assinatura: Set[str] = set()
    for uc in ucs:
        codigo = _normalizar_uc(uc.codigo or "")
        if codigo:
            assinatura.add(codigo)
        for ident in (uc.identificadores or set()):
            ident_norm = _normalizar_uc(ident)
            if ident_norm:
                assinatura.add(ident_norm)
    return assinatura


def _pagina_ucs_corresponde(ucs_atual: List[UcTela], ucs_esperadas: List[UcTela]) -> bool:
    atual = _assinatura_ucs(ucs_atual)
    esperada = _assinatura_ucs(ucs_esperadas)
    if not atual or not esperada:
        return False
    return len(atual & esperada) >= min(2, len(esperada))


def _ir_para_pagina_ucs(driver, estado: str, pagina_alvo: int, cnpj: str, ucs_esperadas: Optional[List[UcTela]] = None) -> bool:
    if pagina_alvo <= 1:
        return True

    modo_rapido = ucs_esperadas is None and pagina_alvo > 2

    if modo_rapido:
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//div[contains(@class,'box-imoveis')]"))
            )
        except Exception:
            pass
    else:
        try:
            atuais = _aguardar_ucs_pagina(driver, estado, cnpj=cnpj, timeout=20, intervalo=2.0)
            if ucs_esperadas and atuais and _pagina_ucs_corresponde(atuais, ucs_esperadas):
                log.info(f"  Já está reposicionado na página {pagina_alvo}")
                return True
        except Exception:
            pass

    for n_pag in range(pagina_alvo - 1):
        avancou = False
        marcador_antes = _marcador_pagina_ucs(driver) if modo_rapido else ""
        for tentativa in range(1, 5):
            time.sleep(0.1 if modo_rapido else 0.25)
            _aguardar_spinner_sumir(driver, timeout=10)
            if _avancar_para_proxima_pagina_ucs(driver, timeout=2.0 if modo_rapido else 3.0):
                if modo_rapido:
                    log.info(f"  Reposicionamento rápido: página {n_pag + 2}/{pagina_alvo}")
                    avancou = True
                    break
                avancou = True
                break
            log.warning(f"  Paginação {n_pag + 1}/{pagina_alvo - 1} não avançou (tentativa {tentativa}/4)")
            time.sleep(0.35 if modo_rapido else 0.8)
        if not avancou:
            log.warning(f"  Reposicionamento falhou na paginação {n_pag + 1}/{pagina_alvo - 1}")
            return False

        if not modo_rapido or n_pag == (pagina_alvo - 2):
            try:
                _aguardar_ucs_pagina(driver, estado, cnpj=cnpj, timeout=20, intervalo=1.5)
            except Exception:
                pass

    if ucs_esperadas:
        try:
            atuais = _aguardar_ucs_pagina(driver, estado, cnpj=cnpj, timeout=20, intervalo=2.0)
            if atuais and _pagina_ucs_corresponde(atuais, ucs_esperadas):
                return True
            log.warning(f"  Após reposicionar, a página {pagina_alvo} não corresponde à lista esperada")
            return False
        except Exception:
            return False
    return True


def processar_estado(driver, estado: str, cnpj: str, ja_baixados: Set[str], estado_ja_selecionado: bool = False) -> int:
    if _tela_tem_lista_ucs(driver):
        log.info(f"  Lista de UCs já aberta para {estado} — seguindo sem clicar no estado")
    elif estado_ja_selecionado:
        log.info(f"  Estado {estado} já foi selecionado — aguardando lista de UCs")
    else:
        if not selecionar_estado(driver, estado):
            log.error(f"  Não foi possível selecionar estado {estado}")
            return 0

    _aguardar_spinner_sumir(driver, timeout=4 if estado_ja_selecionado else 8)

    if _deve_usar_pesquisa_direta():
        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//div[contains(@class,'box-imoveis')]"))
            )
        except Exception:
            pass
        if find_first(driver, _seletores_input_pesquisa_ucs(), timeout=8):
            log.info("  [RESGATE] Usando pesquisa por Código do Cliente em vez de paginação")
            return _processar_alvos_por_pesquisa(driver, estado, cnpj, ja_baixados)
        raise ReiniciarSessaoPesquisa(
            skip_alvos=_skip_alvos_iniciais,
            total_ok=0,
            motivo="campo de pesquisa ausente após login/relogin",
        )
    elif _ucs_alvo is not None:
        log.info(
            "  [RESGATE] Filtro de UC reduzido para este CNPJ; "
            "usando paginação e fallback por UC ligada"
        )

    total_ok = 0
    pagina = max(1, int(_pagina_inicial_ucs or 1))
    paginas_sem_ligadas = 0

    if pagina > 1:
        log.info(f"  Avançando direto para a página inicial configurada: {pagina}")
        ok_inicio = _ir_para_pagina_ucs(driver, estado=estado, pagina_alvo=pagina, cnpj=cnpj)
        if not ok_inicio:
            log.error(f"  Não foi possível chegar à página inicial {pagina} em {estado}")
            return 0

    while True:
        log.info(f"  === Página {pagina} de UCs ({estado}) ===")
        timeout_ucs = 90 if pagina == 1 else 25
        intervalo_ucs = 2.5 if pagina == 1 else 1.5
        ucs = _aguardar_ucs_pagina(driver, estado, cnpj=cnpj, timeout=timeout_ucs, intervalo=intervalo_ucs)

        if not ucs:
            log.info(f"  Página {pagina} sem UCs — encerrando estado {estado}")
            break

        ligadas = [u for u in ucs if u.status == "LIGADA"]
        if _ucs_alvo is not None:
            codigos_pagina = [(u.codigo or "", u.status) for u in ucs]
            log.info(f"  [RESGATE] UCs encontradas na página {pagina}: {codigos_pagina}")
            alvo_pagina = [u for u in ucs if _uc_casa_com_alvos(u)]
            log.info(
                f"  Página {pagina}: {len(ucs)} UC(s), {len(ligadas)} LIGADA(s), "
                f"{len(alvo_pagina)} alvo(s) (filtro_uc=ativo)"
            )
            _prog("ucs_pagina", estado=estado, pagina=pagina, total_ucs=len(ucs), ligadas=len(ligadas), cnpj=cnpj)
            if alvo_pagina:
                processar_lista = alvo_pagina
            elif len(_ucs_alvo) == 1 and len(ligadas) == 1:
                uc_fallback = ligadas[0]
                log.info(
                    "  [RESGATE] Nenhum match exato para a UC alvo nesta página, "
                    "mas existe apenas 1 UC LIGADA para o CNPJ; usando fallback."
                )
                processar_lista = [uc_fallback]
            else:
                if not _avancar_para_proxima_pagina_ucs(driver, timeout=2.5):
                    break
                pagina += 1
                continue
        else:
            log.info(f"  Página {pagina}: {len(ucs)} UC(s), {len(ligadas)} LIGADA(s)")
            _prog("ucs_pagina", estado=estado, pagina=pagina, total_ucs=len(ucs), ligadas=len(ligadas), cnpj=cnpj)

            if not ligadas:
                paginas_sem_ligadas += 1
                if paginas_sem_ligadas >= 2:
                    log.info(f"  {paginas_sem_ligadas} páginas consecutivas sem ligadas — encerrando estado {estado}")
                    break
                if not _avancar_para_proxima_pagina_ucs(driver, timeout=2.5):
                    log.info(f"  Fim das páginas ({estado}) — todas UCs desligadas")
                    break
                pagina += 1
                continue
            processar_lista = ligadas

        paginas_sem_ligadas = 0

        for pos, uc in enumerate(processar_lista, start=1):
            indice_na_pagina = ucs.index(uc) + 1
            log.info(f"  --- UC[pág{pagina}:{pos}] codigo={uc.codigo or 'SEM_CODIGO'} ---")
            baixados_uc = 0
            try:
                _prog("uc_inicio", uc=uc.codigo or "", estado=estado, i=pos, total=len(processar_lista), pagina=pagina)
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
                    time.sleep(0.2)
                    _aguardar_spinner_sumir(driver, timeout=8)
                    if driver.find_elements(By.XPATH, "//div[contains(@class,'box-imoveis')]"):
                        voltou = True
                        break
                    if _tela_tem_selecao_estados(driver):
                        log.info(f"  back() caiu em seleção de estados — re-selecionando {estado}")
                        if selecionar_estado(driver, estado):
                            _aguardar_spinner_sumir(driver, timeout=10)
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
                    _aguardar_spinner_sumir(driver, timeout=10)
                    if _tela_tem_selecao_estados(driver):
                        if selecionar_estado(driver, estado):
                            _aguardar_spinner_sumir(driver, timeout=10)
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

            if pagina > 1:
                log.info(f"  Reposicionando para página {pagina} após processar UC")
                ok_paginou = _ir_para_pagina_ucs(
                    driver,
                    estado=estado,
                    pagina_alvo=pagina,
                    cnpj=cnpj,
                    ucs_esperadas=ucs,
                )
                if not ok_paginou:
                    # Não conseguiu repaginar — recomeça o estado do zero para evitar
                    # processar UCs da página errada (causa de "lista mas não acessa")
                    log.warning(f"  Abortando página {pagina} e reiniciando estado {estado} da página 1")
                    pagina = 1
                    continue

        if not _avancar_para_proxima_pagina_ucs(driver, timeout=2.5):
            log.info(f"  Sem próxima página após página {pagina} — fim do estado {estado}")
            break
        pagina += 1

    return total_ok


def processar_cnpj(info: CnpjInfo, ja_baixados: Set[str]) -> int:
    global _skip_alvos_iniciais
    driver = None
    total_ok = 0
    n_estados_esperados = len(info.estados_esperados or [])
    ucs_alvo_cnpj = info.ucs_alvo or sorted(_ucs_alvo_default or set())
    _definir_filtro_uc_corrente(ucs_alvo_cnpj if ucs_alvo_cnpj else None)

    if _ucs_alvo is not None:
        log.info(f"  [RESGATE] Filtro ativo para CNPJ {info.cnpj}: {len(_ucs_alvo)} UC(s)")

    try:
        if n_estados_esperados == 1 and _ucs_alvo is not None:
            estado_nome = info.estados_esperados[0]
            tentativas_reinicio = 0

            while True:
                if driver:
                    try:
                        driver.quit()
                        shutil.rmtree(getattr(driver, "_profile_dir", None) or "", ignore_errors=True)
                    except Exception:
                        pass
                    driver = None

                driver = build_driver()
                driver, login_ok = _fazer_login_com_retentativa(driver, info.cnpj, info.senha)
                if not login_ok:
                    log.error(f"Login falhou: {info.cnpj}")
                    gravar_falha_login(info.cnpj, info.senha, motivo="credencial_invalida")
                    return total_ok

                try:
                    log.info(f"  Estado único detectado: {estado_nome}")
                    _aguardar_spinner_sumir(driver, timeout=15)

                    if _tela_tem_lista_ucs(driver):
                        log.info(f"  Portal já abriu direto na lista de UCs de {estado_nome} — seguindo sem clicar no card")
                        total_ok += processar_estado(driver, estado_nome, info.cnpj, ja_baixados)
                        return total_ok

                    if _tela_tem_selecao_estados(driver):
                        log.info(f"  Tela de estados visível — clicando no card {estado_nome}")
                        if not selecionar_estado(driver, estado_nome):
                            log.error(f"  Falha ao selecionar {estado_nome}")
                            return total_ok
                        total_ok += processar_estado(driver, estado_nome, info.cnpj, ja_baixados, estado_ja_selecionado=True)
                        return total_ok

                    log.warning(f"  Nem tela de estados nem lista de UCs foram detectadas após o login de {info.cnpj}")
                    save_screenshot(driver, f"estado_unico_sem_tela_{info.cnpj}")
                    return total_ok

                except ReiniciarSessaoPesquisa as e:
                    total_ok += e.total_ok
                    tentativas_reinicio += 1
                    _skip_alvos_iniciais = max(0, e.skip_alvos)
                    log.warning(
                        f"  [RESGATE] Reiniciando Chrome após '{e.motivo}'. "
                        f"Retomando do alvo {_skip_alvos_iniciais + 1}"
                    )
                    if tentativas_reinicio >= 12:
                        log.error("  [RESGATE] Limite de reinícios atingido — encerrando CNPJ atual")
                        return total_ok
                    continue

        driver = build_driver()
        driver, login_ok = _fazer_login_com_retentativa(driver, info.cnpj, info.senha)
        if not login_ok:
            log.error(f"Login falhou: {info.cnpj}")
            gravar_falha_login(info.cnpj, info.senha, motivo="credencial_invalida")
            return 0

        if n_estados_esperados == 1:
            estado_nome = info.estados_esperados[0]
            log.info(f"  Estado único detectado: {estado_nome}")
            _aguardar_spinner_sumir(driver, timeout=15)

            if _tela_tem_lista_ucs(driver):
                log.info(f"  Portal já abriu direto na lista de UCs de {estado_nome} — seguindo sem clicar no card")
                total_ok += processar_estado(driver, estado_nome, info.cnpj, ja_baixados)
                return total_ok

            if _tela_tem_selecao_estados(driver):
                log.info(f"  Tela de estados visível — clicando no card {estado_nome}")
                if not selecionar_estado(driver, estado_nome):
                    log.error(f"  Falha ao selecionar {estado_nome}")
                    return 0
                total_ok += processar_estado(driver, estado_nome, info.cnpj, ja_baixados, estado_ja_selecionado=True)
                return total_ok

            log.warning(f"  Nem tela de estados nem lista de UCs foram detectadas após o login de {info.cnpj}")
            save_screenshot(driver, f"estado_unico_sem_tela_{info.cnpj}")
            return 0

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
                shutil.rmtree(getattr(driver, "_profile_dir", None) or "", ignore_errors=True)
            except Exception:
                pass

    return total_ok


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
        ucs_alvo = item.get("ucs_alvo") or []

        if isinstance(estados, str):
            estados = [e.strip() for e in estados.split("|") if e.strip()]
        if isinstance(ucs_alvo, str):
            ucs_alvo = [u.strip() for u in ucs_alvo.split("|") if u.strip()]

        if len(cnpj) == 14 and senha:
            saida.append(
                CnpjInfo(
                    cnpj=cnpj,
                    senha=senha,
                    estados_esperados=list(estados),
                    ucs_alvo=list(ucs_alvo),
                )
            )
    return saida


def _resolver_csv_coelba_correcao() -> Optional[Path]:
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


def run_worker_coelba(
    jobs,
    shared_lock,
    progress_queue=None,
    ucs_alvo=None,
    ignorar_indice: bool = False,
    baixar_todas_faturas_ano: bool = False,
    ano_alvo: Optional[int] = None,
    destino_subpasta: Optional[str] = None,
    permitir_qualquer_situacao: bool = False,
    permitir_qualquer_ano: bool = False,
    refs_alvo: Optional[Iterable[str]] = None,
    pagina_inicial_ucs: int = 1,
    skip_alvos_iniciais: int = 0,
    usar_pesquisa_direta: bool = True,
    worker_name: Optional[str] = None,
) -> int:
    global _shared_lock, _progress_queue, _ucs_alvo, _ucs_alvo_norm, _ucs_alvo_default, _ucs_alvo_norm_default, _ignorar_indice
    global _baixar_todas_faturas_ano, _ano_alvo, _destino_subpasta
    global _permitir_qualquer_situacao, _permitir_qualquer_ano, _refs_alvo_norm
    global _pagina_inicial_ucs, _skip_alvos_iniciais, _usar_pesquisa_direta
    _configurar_runtime(worker_name or WORKER_NAME)
    _shared_lock = shared_lock
    _progress_queue = progress_queue
    _ignorar_indice = bool(ignorar_indice)
    _baixar_todas_faturas_ano = bool(baixar_todas_faturas_ano)
    _ano_alvo = int(ano_alvo) if ano_alvo else None
    _destino_subpasta = str(destino_subpasta).strip("\\/ ") if destino_subpasta else None
    _permitir_qualquer_situacao = bool(permitir_qualquer_situacao)
    _permitir_qualquer_ano = bool(permitir_qualquer_ano)
    _refs_alvo_norm = {
        _normalizar_referencia(ref)
        for ref in (refs_alvo or [])
        if str(ref or "").strip()
    } or None
    _pagina_inicial_ucs = max(1, int(pagina_inicial_ucs or 1))
    _skip_alvos_iniciais = max(0, int(skip_alvos_iniciais or 0))
    _usar_pesquisa_direta = bool(usar_pesquisa_direta)
    _ucs_alvo_default, _ucs_alvo_norm_default = _normalizar_lista_ucs_alvo(ucs_alvo)
    _definir_filtro_uc_corrente(ucs_alvo)
    if _ucs_alvo_default is not None:
        log.info(f"[COELBA] Filtro base de UCs ativo: {len(_ucs_alvo_default)} UC(s) alvo")
    if _ignorar_indice:
        log.info("[COELBA] Redownload ativo: índice de já baixados será ignorado")
    if _baixar_todas_faturas_ano:
        alvo = _ano_alvo if _ano_alvo is not None else f">={ANO_MINIMO}"
        log.info(f"[COELBA] Modo completo ativo: baixar todas as faturas elegíveis de {alvo}")
    elif _permitir_qualquer_situacao or _permitir_qualquer_ano:
        log.info(
            "[COELBA] Modo última referência ativa: "
            f"qualquer_situacao={_permitir_qualquer_situacao} "
            f"qualquer_ano={_permitir_qualquer_ano}"
        )
    if _refs_alvo_norm:
        log.info(f"[COELBA] Referências alvo: {', '.join(sorted(_refs_alvo_norm))}")
    if _destino_subpasta:
        log.info(f"[COELBA] Subpasta de destino ativa: {_destino_subpasta}")
    if _fluxo_direcionado_ativo():
        log.info("[COELBA] Destino ativo: resgate/direcionado com separação por situação")
    else:
        log.info("[COELBA] Destino ativo: fluxo normal salvando direto na pasta do mês")
    if _pagina_inicial_ucs > 1:
        log.info(f"[COELBA] Página inicial configurada: {_pagina_inicial_ucs}")
    if _skip_alvos_iniciais > 0:
        log.info(f"[COELBA] Retomada configurada: pular {_skip_alvos_iniciais} código(s) alvo")
    if not _usar_pesquisa_direta:
        log.info("[COELBA] Pesquisa direta por Código do Cliente desativada; usando varredura paginada")

    _inicializar_master()
    if _master_obj is None:
        raise RuntimeError(
            "indice_master não disponível — impossível gerar carimbos seguros. "
            "Verifique a rede e o arquivo indice_master.py"
        )

    inicio = datetime.now()
    log.info(f"WORKER {WORKER_NAME.upper()} | início {inicio.strftime('%H:%M:%S')}")

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
    log.info(f"Fim: {fim.strftime('%H:%M:%S')} | duração {str(fim - inicio).split('.')[0]} | PDFs {total_ok}")
    if _progress_queue is None and not _fluxo_direcionado_ativo():
        try:
            log.info("Iniciando organização final por OCR (baixa/media)...")
            organizacao = organizar_downloads_neoenergia(
                FINAL_DOWNLOAD_ROOT,
                index_file=INDEX_FILE,
                master_file=MASTER_FILE,
                logger=log,
            )
            log.info(
                "Organizacao OCR concluida | movidos=%s | BT=%s | MT=%s | refs_corrigidas=%s | sem_classificacao=%s",
                organizacao.movidos_bt + organizacao.movidos_mt,
                organizacao.movidos_bt,
                organizacao.movidos_mt,
                organizacao.referencias_corrigidas,
                organizacao.nao_classificados,
            )
        except Exception as e:
            log.warning(f"Falha ao organizar downloads por OCR: {e}")
    log.info(f"Log local: {log_file}")
    _prog("fim", total_pdfs=total_ok, duracao=str(fim - inicio).split(".")[0])
    return total_ok


if __name__ == "__main__":
    import argparse
    import csv as _csv
    from multiprocessing import Lock

    _ap = argparse.ArgumentParser(description="Worker COELBA")
    _ap.add_argument(
        "--condicao",
        choices=["a_vencer", "todos", "nao_pagas"],
        default="a_vencer",
        help="Condição das faturas a baixar: a_vencer (padrão) | todos | nao_pagas",
    )
    _args = _ap.parse_args()
    _permitir_qualquer = _args.condicao in ("todos", "nao_pagas")

    _csv_path = _resolver_csv_coelba_correcao() or (BASE_DIR / "cnpjs_neoenergia.csv")
    if not _csv_path.exists():
        print(f"[COELBA] CSV não encontrado: {_csv_path}")
        raise SystemExit(1)

    _estados_worker = ["Bahia"]
    _jobs = []
    with open(_csv_path, encoding="utf-8-sig") as _f:
        for _row in _csv.DictReader(_f):
            _estados = [e.strip() for e in _row.get("ESTADOS", "").split("|") if e.strip()]
            if any(e in _estados_worker for e in _estados):
                _jobs.append({
                    "cnpj": _row["CNPJ"].strip(),
                    "senha": _row["SENHA"].strip(),
                    "estados_esperados": _estados,
                })

    print(f"[COELBA] {len(_jobs)} jobs carregados de {_csv_path}")
    if _permitir_qualquer:
        print(f"[COELBA] Condição: {_args.condicao} (todas as faturas não pagas)")
    if not _jobs:
        print(f"[COELBA] Nenhum job para {_estados_worker}. Verifique o CSV.")
        raise SystemExit(0)

    run_worker_coelba(_jobs, shared_lock=Lock(), permitir_qualquer_situacao=_permitir_qualquer)








