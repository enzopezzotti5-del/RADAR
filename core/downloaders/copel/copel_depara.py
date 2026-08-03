#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
copel_depara.py — Gera mapeamento UC-antiga → UC-ANEEL percorrendo o portal COPEL.

Saídas:
    de_para_copel.csv              — mapeamento completo salvo em DOWNLOAD COPEL/
    acessos_copel.xlsx (opcional)  — coluna UC_ANEEL adicionada/atualizada

Uso:
    python copel_depara.py
    python copel_depara.py --atualizar-xlsx
"""

from __future__ import annotations

import argparse
import csv
import sys
import tempfile
import unicodedata

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.common.exceptions import TimeoutException
from core.project_paths import resolve_copel_accessos_xls

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

CNPJ_LOGIN = "00000000000191"
SENHA_LOGIN = "Acao*2024"
URL_LOGIN   = "https://www.copel.com/avaweb/paginaLogin/login.jsf"
URL_DEPARA_MT = "https://app.copel.com/xuwweb/"

TWOCAPTCHA_API_KEY = "3ea89b196b365e9db9d0fd245c628e4f"

ROOT_DIR    = Path("//10.10.250.21/Energia/ARQUIVOS ENZO")
COPEL_DIR   = ROOT_DIR / "DOWNLOAD COPEL"
ACESSOS_XLS = COPEL_DIR / "acessos_copel.xlsx"
ACESSOS_XLS_LOCAL = Path(__file__).resolve().parents[3] / "acessos_copel.xlsx"
DEPARA_CSV    = COPEL_DIR / "de_para_copel.csv"
DEPARA_CSV_MT = COPEL_DIR / "de_para_copel_mt.csv"

T_LOGIN = 90   # segundos para aguardar login (pode incluir captcha manual)
T_EL    = 15

DEPARA_FIELDS    = ["UC_ANTIGA", "UC_ANEEL", "CIDADE", "GRUPO", "SITUACAO", "DATA_COLETA"]
DEPARA_FIELDS_MT = ["UC_ANTIGA", "UC_ANEEL", "DATA_COLETA"]


# =============================================================================
# LOGGING
# =============================================================================

def _ascii_fold(s: str) -> str:
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode().upper()


def log(msg: str, level: str = "INFO") -> None:
    sym = {"INFO": "→", "OK": "✔", "ERR": "✖", "WARN": "⚠"}
    print(f"[{datetime.now():%H:%M:%S}] {sym.get(level, '•')} [{level}] {msg}", flush=True)


# =============================================================================
# DRIVER
# =============================================================================

def _find_chromedriver() -> str | None:
    """Procura chromedriver no cache do Selenium sem acesso à rede."""
    import subprocess
    cache = Path.home() / ".cache" / "selenium" / "chromedriver" / "win64"
    if not cache.exists():
        return None
    try:
        r = subprocess.run(
            ["powershell", "-c",
             "(gi 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe').VersionInfo.ProductVersion"],
            capture_output=True, text=True, timeout=5, creationflags=0x08000000,
        )
        major = r.stdout.strip().split(".")[0]
    except Exception:
        major = None
    for p in sorted(cache.iterdir()):
        exe = p / "chromedriver.exe"
        if exe.exists() and (not major or p.name.startswith(major + ".")):
            return str(exe)
    return None


def build_driver() -> webdriver.Chrome:
    temp_dl = Path(tempfile.mkdtemp(prefix="copel_depara_dl_"))
    profile_root = Path(__file__).resolve().parent / "chrome_profiles"
    profile_root.mkdir(parents=True, exist_ok=True)
    perfil_dir = Path(tempfile.mkdtemp(prefix="copel_depara_", dir=str(profile_root)))

    opts = Options()
    opts.add_argument(f"--user-data-dir={perfil_dir}")
    opts.add_experimental_option("prefs", {
        "download.default_directory": str(temp_dl),
        "download.prompt_for_download": False,
        "plugins.always_open_pdf_externally": True,
    })
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")

    cd = _find_chromedriver()
    if cd:
        from selenium.webdriver.chrome.service import Service
        drv = webdriver.Chrome(service=Service(cd), options=opts)
    else:
        drv = webdriver.Chrome(options=opts)

    drv.implicitly_wait(5)
    return drv


# =============================================================================
# LOGIN
# =============================================================================

def _W(driver, by, sel, timeout=T_EL):
    return WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, sel)))


def fazer_login(driver: webdriver.Chrome) -> bool:
    """
    Login no portal COPEL. Retorna True se bem-sucedido.
    Se houver captcha, o operador tem T_LOGIN segundos para resolvê-lo manualmente.
    """
    log(f"Abrindo {URL_LOGIN}")
    driver.get(URL_LOGIN)

    try:
        campo_cnpj = _W(driver, By.ID, "formulario:numDoc", T_LOGIN)
        campo_cnpj.clear()
        campo_cnpj.send_keys(CNPJ_LOGIN)

        campo_senha = _W(driver, By.ID, "formulario:pass")
        campo_senha.clear()
        campo_senha.send_keys(SENHA_LOGIN)

        try:
            btn = driver.find_element(
                By.CSS_SELECTOR, "input[type='submit'], button[type='submit']"
            )
            btn.click()
        except Exception:
            campo_senha.send_keys(Keys.RETURN)

        # Aguarda tabela de UCs (sinal de login concluído)
        _W(driver, By.ID, "formLogin:tbUcs", T_LOGIN)
        log("Login OK", "OK")
        return True

    except TimeoutException:
        log("Timeout aguardando tabela de UCs após login", "ERR")
        return False
    except Exception as e:
        log(f"Exceção no login: {e}", "ERR")
        return False


# =============================================================================
# LEITURA UC POR UC (via filtro da tabela)
# =============================================================================

# Seletor do campo de filtro da coluna "Unidade consumidora antiga" (col 1, j_idt48)
# O ID exato pode variar entre sessões; usamos o segundo input de filtro.
FILTRO_ANTIGA_IDX = 1   # índice entre os filtros visíveis (0=ANEEL, 1=antiga)

def _fechar_inatividade(driver: webdriver.Chrome) -> bool:
    """Detecta 'Tela inativa' via JS (sem XPath lento) e clica Recarregar."""
    try:
        clicou = driver.execute_script("""
            var els = document.querySelectorAll('button,a,input[type=button],input[type=submit]');
            for (var i = 0; i < els.length; i++) {
                if (els[i].offsetParent !== null &&
                    els[i].textContent.trim() === 'Recarregar') {
                    els[i].click(); return true;
                }
            }
            return false;
        """)
        if clicou:
            log("Modal 'Tela inativa' — Recarregar clicado", "WARN")
            _W(driver, By.ID, "formLogin:tbUcs_data", 30)
            log("Tabela recarregada", "OK")
        return bool(clicou)
    except Exception:
        return False


def _aguardar_overlay(driver: webdriver.Chrome, timeout: float = 3.0) -> None:
    """Aguarda statusDialog_modal sumir (overlay de loading do PrimeFaces)."""
    try:
        WebDriverWait(driver, timeout).until(
            EC.invisibility_of_element_located((By.ID, "statusDialog_modal"))
        )
    except Exception:
        pass


def _tabela_filtrada(driver: webdriver.Chrome, uc_antiga: str) -> bool:
    """Retorna True quando a tabela já exibe o resultado filtrado para uc_antiga."""
    try:
        tbody = driver.find_element(By.ID, "formLogin:tbUcs_data")
        linhas = tbody.find_elements(By.TAG_NAME, "tr")
        if not linhas:
            return True   # zero resultados = filtro aplicado, não encontrado
        tds = linhas[0].find_elements(By.TAG_NAME, "td")
        if len(tds) >= 2:
            col1 = tds[1].text.strip()
            return col1.lstrip("0") == uc_antiga.lstrip("0") or col1 == ""
        return True
    except Exception:
        return False


def _limpar_filtro(driver: webdriver.Chrome, filtro) -> None:
    """Limpa o campo de filtro via JS sem esperar AJAX completo."""
    try:
        driver.execute_script(
            "arguments[0].value = '';"
            "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));"
            "arguments[0].dispatchEvent(new Event('keyup',{bubbles:true}));",
            filtro,
        )
    except Exception:
        pass


# Cache do elemento filtro para evitar re-busca a cada UC
_filtro_cache: list | None = None


def _get_filtro(driver: webdriver.Chrome):
    """Retorna o elemento filtro da coluna 'UC antiga', com cache."""
    global _filtro_cache
    try:
        if _filtro_cache:
            _ = _filtro_cache[0].tag_name   # testa se ainda válido (stale?)
            return _filtro_cache[0]
    except Exception:
        _filtro_cache = None

    filtros = driver.find_elements(
        By.CSS_SELECTOR, "input[id^='formLogin:tbUcs:'][id$=':filter']"
    )
    if len(filtros) >= 2:
        _filtro_cache = [filtros[FILTRO_ANTIGA_IDX]]
        return _filtro_cache[0]
    return None


def _buscar_uc_antiga(driver: webdriver.Chrome, uc_antiga: str) -> dict | None:
    """
    Digita uc_antiga no filtro, aguarda resultado na tabela (sem sleep fixo),
    lê a primeira linha e retorna o mapeamento.
    """
    global _filtro_cache
    try:
        # Verifica inatividade antes de interagir
        _fechar_inatividade(driver)
        _aguardar_overlay(driver)

        filtro = _get_filtro(driver)
        if filtro is None:
            log(f"Filtro não encontrado para UC {uc_antiga}", "WARN")
            return None

        # Dispara filtro via JS
        driver.execute_script(
            "arguments[0].value = arguments[1];"
            "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));"
            "arguments[0].dispatchEvent(new Event('keyup',{bubbles:true}));",
            filtro, uc_antiga,
        )

        # Aguarda até a tabela mostrar o resultado (sem sleep fixo)
        _aguardar_overlay(driver, timeout=4.0)
        try:
            WebDriverWait(driver, 5.0).until(
                lambda d: _tabela_filtrada(d, uc_antiga)
            )
        except Exception:
            pass

        # Lê resultado
        tbody = driver.find_element(By.ID, "formLogin:tbUcs_data")
        linhas = tbody.find_elements(By.TAG_NAME, "tr")
        for linha in linhas:
            tds = linha.find_elements(By.TAG_NAME, "td")
            if len(tds) < 6:
                continue
            uc_aneel  = tds[0].text.strip()
            uc_result = tds[1].text.strip()
            cidade    = tds[2].text.strip()
            grupo     = tds[4].text.strip()
            situacao  = tds[5].text.strip()
            if uc_result == uc_antiga and uc_aneel:
                # Limpa filtro antes de sair (não bloqueia)
                _limpar_filtro(driver, filtro)
                _filtro_cache = None   # força re-busca (element pode ficar stale após AJAX)
                return {
                    "UC_ANTIGA": uc_antiga, "UC_ANEEL": uc_aneel,
                    "CIDADE": cidade, "GRUPO": grupo, "SITUACAO": situacao,
                }

        _limpar_filtro(driver, filtro)
        _filtro_cache = None
        return None

    except Exception as e:
        _filtro_cache = None
        log(f"Erro ao buscar UC {uc_antiga}: {e}", "WARN")
        return None


def _carregar_csv_existente(destino: Path) -> dict[str, dict]:
    """Lê o CSV de saída já existente e retorna dict UC_ANTIGA → registro."""
    existentes: dict[str, dict] = {}
    if not destino.exists():
        return existentes
    try:
        with open(destino, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                uc = row.get("UC_ANTIGA", "").strip()
                if uc:
                    existentes[uc] = row
        log(f"Retomada: {len(existentes)} UCs já no CSV", "OK")
    except Exception as e:
        log(f"Não foi possível ler CSV existente: {e}", "WARN")
    return existentes


def consultar_instalacoes(driver: webdriver.Chrome, instalacoes: list[str],
                          destino: Path) -> list[dict]:
    """
    Para cada instalação (UC antiga), busca a UC ANEEL via filtro da tabela.
    Suporta retomada (pula UCs já no CSV) e salva incrementalmente a cada 10.
    """
    log("Aguardando tabela de UCs...")
    _W(driver, By.ID, "formLogin:tbUcs_data", T_EL)

    # Retomada: carrega progresso anterior
    existentes = _carregar_csv_existente(destino)
    resultados: list[dict] = list(existentes.values())

    data_coleta = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    nao_encontrados: list[str] = []
    novos = 0

    pendentes = [uc for uc in instalacoes if uc not in existentes]
    total_orig = len(instalacoes)
    log(f"Pendentes: {len(pendentes)} de {total_orig} (já coletados: {len(existentes)})")

    for i, uc in enumerate(pendentes, 1):
        idx_global = len(existentes) + i
        log(f"[{idx_global:>3}/{total_orig}] Buscando UC {uc}...")
        resultado = _buscar_uc_antiga(driver, uc)
        if resultado:
            resultado["DATA_COLETA"] = data_coleta
            resultados.append(resultado)
            existentes[uc] = resultado
            novos += 1
            log(f"          {uc} → {resultado['UC_ANEEL']}  ({resultado['CIDADE']})", "OK")
        else:
            nao_encontrados.append(uc)
            log(f"          {uc} → não encontrado", "WARN")

        # Salva incrementalmente a cada 10 novas UCs
        if novos > 0 and novos % 10 == 0:
            salvar_csv(resultados, destino)

    log(f"Concluído: {len(resultados)} mapeados, {len(nao_encontrados)} não encontrados", "OK")
    if nao_encontrados:
        log(f"Sem DE/PARA: {nao_encontrados}", "WARN")
    return resultados


# =============================================================================
# SALVAR CSV
# =============================================================================

def salvar_csv(registros: list[dict], destino: Path,
               fields: list[str] | None = None) -> None:
    fields = fields or DEPARA_FIELDS
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        with open(destino, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(registros)
        log(f"CSV salvo: {destino}  ({len(registros)} linhas)", "OK")
    except Exception as e:
        log(f"Erro ao salvar CSV em {destino}: {e}", "ERR")
        local = Path(__file__).resolve().parent / destino.name
        with open(local, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(registros)
        log(f"CSV salvo localmente: {local}", "WARN")


# =============================================================================
# ATUALIZAR XLSX
# =============================================================================

def atualizar_xlsx(registros: list[dict]) -> None:
    """Adiciona/atualiza coluna UC_ANEEL em acessos_copel.xlsx."""
    planilha = next(
        (p for p in [resolve_copel_accessos_xls(COPEL_DIR), ACESSOS_XLS, ACESSOS_XLS_LOCAL] if p.exists()), None
    )
    if not planilha:
        log("acessos_copel.xlsx não encontrado — não foi possível atualizar", "ERR")
        return

    # Monta dicionário: UC_antiga (str) → UC_ANEEL
    depara: dict[str, str] = {
        r["UC_ANTIGA"]: r["UC_ANEEL"]
        for r in registros
        if r.get("UC_ANTIGA") and r.get("UC_ANEEL")
    }
    log(f"DE/PARA carregado: {len(depara)} mapeamentos")

    df = pd.read_excel(planilha, dtype=str)
    df.columns = [c.strip() for c in df.columns]

    col_inst = next((c for c in df.columns if "instalac" in c.lower()), None)
    if not col_inst:
        log("Coluna de instalação não encontrada no xlsx", "ERR")
        return

    df["UC_ANEEL"] = df[col_inst].str.strip().map(depara)
    encontrados = df["UC_ANEEL"].notna().sum()
    nao_mapeados = len(df) - encontrados
    log(f"Mapeados: {encontrados}  |  Sem correspondência: {nao_mapeados}")
    if nao_mapeados:
        sem = df.loc[df["UC_ANEEL"].isna(), col_inst].tolist()
        log(f"Sem DE/PARA: {sem[:10]}{'...' if len(sem) > 10 else ''}", "WARN")

    df.to_excel(planilha, index=False)
    log(f"acessos_copel.xlsx atualizado: {planilha}", "OK")


# =============================================================================
# MAIN
# =============================================================================

def _carregar_ucs_xlsx(tensao_filtro: str = "BAIXA") -> list[str]:
    """Lê acessos_copel.xlsx e retorna lista de UCs COPEL pelo filtro de tensão."""
    planilha = next(
        (p for p in [resolve_copel_accessos_xls(COPEL_DIR), ACESSOS_XLS, ACESSOS_XLS_LOCAL] if p.exists()), None
    )
    if not planilha:
        log("acessos_copel.xlsx não encontrado", "ERR")
        return []
    df = pd.read_excel(planilha, dtype=str)
    df.columns = [c.strip() for c in df.columns]
    col_conc   = next((c for c in df.columns if "concess"  in c.lower()), None)
    col_tensao = next((c for c in df.columns if "tens"     in c.lower()), None)
    col_inst   = next((c for c in df.columns if "instalac" in c.lower()), None)
    if not all([col_conc, col_tensao, col_inst]):
        log("Colunas obrigatórias não encontradas no xlsx", "ERR")
        return []
    mask = (df[col_conc].fillna("").apply(_ascii_fold).str.contains("COPEL", na=False) &
            df[col_tensao].fillna("").apply(_ascii_fold).str.contains(tensao_filtro, na=False))
    ucs = df.loc[mask, col_inst].dropna().str.strip().unique().tolist()
    label = "BT" if tensao_filtro == "BAIXA" else "MT"
    log(f"Instalações COPEL {label} no xlsx: {len(ucs)}", "OK")
    return ucs


# =============================================================================
# CAPTCHA (2captcha.com)
# =============================================================================

def _resolver_captcha_2captcha(sitekey: str, page_url: str) -> str | None:
    import json as _json
    import urllib.request as _req
    import urllib.error as _uerr

    if not TWOCAPTCHA_API_KEY or not sitekey:
        return None

    def _post(url, payload):
        req = _req.Request(url, data=payload,
                           headers={"Content-Type": "application/json"})
        return _json.loads(_req.urlopen(req, timeout=30).read())

    log("Enviando reCAPTCHA para 2captcha...", "DBG")
    try:
        payload = _json.dumps({
            "clientKey": TWOCAPTCHA_API_KEY,
            "task": {"type": "RecaptchaV2TaskProxyless",
                     "websiteURL": page_url, "websiteKey": sitekey},
        }).encode()
        res = _post("https://api.2captcha.com/createTask", payload)
        if res.get("errorId") != 0:
            log(f"2captcha createTask erro: {res}", "WARN")
            return None
        task_id = res["taskId"]
        log(f"2captcha taskId={task_id} aguardando...", "DBG")
        payload_get = _json.dumps({"clientKey": TWOCAPTCHA_API_KEY,
                                   "taskId": task_id}).encode()
        for _ in range(40):
            time.sleep(3)
            try:
                r2 = _post("https://api.2captcha.com/getTaskResult", payload_get)
            except Exception:
                continue
            if r2.get("status") == "ready":
                token = r2.get("solution", {}).get("gRecaptchaResponse", "")
                if token:
                    log("reCAPTCHA resolvido pelo 2captcha", "OK")
                    return token
        log("Timeout 2captcha", "WARN")
    except Exception as e:
        log(f"Erro 2captcha: {e}", "WARN")
    return None


def _injetar_captcha(driver: webdriver.Chrome, token: str) -> None:
    driver.execute_script("""
        const token = arguments[0];
        // Seta textarea g-recaptcha-response
        const targets = Array.from(document.querySelectorAll(
          '#g-recaptcha-response, textarea[name="g-recaptcha-response"]'));
        if (!targets.length) {
            const ta = document.createElement('textarea');
            ta.id = 'g-recaptcha-response'; ta.name = 'g-recaptcha-response';
            ta.style.display = 'none'; document.body.appendChild(ta); targets.push(ta);
        }
        targets.forEach(el => {
            el.value = token; el.innerHTML = token;
            el.dispatchEvent(new Event('input', {bubbles:true}));
            el.dispatchEvent(new Event('change', {bubbles:true}));
        });
        // Remove iframes do desafio (bframe) que bloqueiam a página
        document.querySelectorAll('iframe[name^="c-"], iframe[src*="bframe"]')
            .forEach(el => el.remove());
        // Dispara callback do site
        try {
            if (window.___grecaptcha_cfg && ___grecaptcha_cfg.clients) {
                for (const c of Object.values(___grecaptcha_cfg.clients)) {
                    function findCb(o, seen) {
                        if (!o || typeof o !== 'object' || seen.has(o)) return null;
                        seen.add(o);
                        for (const k of Object.keys(o)) {
                            if (k === 'callback' && typeof o[k] === 'function') return o[k];
                            const n = findCb(o[k], seen); if (n) return n;
                        }
                        return null;
                    }
                    const cb = findCb(c, new Set()); if (cb) { cb(token); break; }
                }
            }
        } catch(e) {}
        // Remove todos os overlays/backdrops do captcha que possam sobrar
        document.querySelectorAll('.rc-anchor-invisible-text, [id^="rc-imageselect"]')
            .forEach(el => el.remove());
    """, token)


def _resolver_captcha(driver: webdriver.Chrome, timeout: int = 120,
                       wait_aparece: int = 8) -> None:
    """Resolve reCAPTCHA via 2captcha se detectado; caso contrário aguarda manual.
    Aguarda até wait_aparece segundos para o captcha aparecer após uma ação."""
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
        import re as _re
        m = _re.search(r"[?&]k=([^&]+)", src)
        if m:
            sitekey = m.group(1)
    except Exception:
        pass
    page_url = driver.current_url
    if TWOCAPTCHA_API_KEY and sitekey:
        log(f"reCAPTCHA detectado (sitekey={sitekey[:12]}...) — resolvendo via 2captcha...", "WARN")
        try:
            token = _resolver_captcha_2captcha(sitekey, page_url)
            if token:
                _injetar_captcha(driver, token)
                time.sleep(1)
                # Garante que o iframe do desafio sumiu
                try:
                    WebDriverWait(driver, 5).until(
                        EC.invisibility_of_element_located(
                            (By.CSS_SELECTOR, "iframe[name^='c-'], iframe[src*='bframe']")
                        )
                    )
                except Exception:
                    driver.execute_script(
                        "document.querySelectorAll('iframe[name^=\"c-\"], iframe[src*=\"bframe\"]')"
                        ".forEach(el => el.remove());"
                    )
                log("Captcha resolvido automaticamente.", "OK")
                return
        except Exception as e:
            log(f"2captcha falhou ({e}) — aguardando resolução manual.", "WARN")
    log("reCAPTCHA detectado — resolva manualmente e aguarde.", "WARN")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2)
        try:
            if (driver.find_elements(By.CSS_SELECTOR, "#recaptcha-anchor[aria-checked='true']")
                    or not driver.find_elements(By.CSS_SELECTOR, "iframe[src*='recaptcha']")):
                log("Captcha resolvido — continuando.", "OK")
                return
        except Exception:
            return
    log("Timeout captcha — tentando prosseguir.", "WARN")


# =============================================================================
# DE/PARA MT — via app.copel.com/xuwweb/ (sem login)
# =============================================================================

_JS_LIMPAR_CAPTCHA = """
document.querySelectorAll('iframe').forEach(function(el){
    var s = el.src || ''; var n = el.name || '';
    if (s.indexOf('recaptcha') !== -1 || s.indexOf('bframe') !== -1 || n.indexOf('c-') === 0)
        el.remove();
});
"""


def _js_set(driver, selector, value):
    return driver.execute_script("""
        var el = document.querySelector(arguments[0]);
        if (!el) return false;
        var setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        setter.call(el, arguments[1]);
        el.dispatchEvent(new Event('input',  {bubbles:true}));
        el.dispatchEvent(new Event('change', {bubbles:true}));
        return true;
    """, selector, value)


def _js_get(driver, selector):
    return driver.execute_script(
        "var el = document.querySelector(arguments[0]); return el ? el.value : null;",
        selector,
    )


def _js_click_pesquisar(driver):
    driver.execute_script("""
        var btns = Array.from(document.querySelectorAll('button'));
        var btn = btns.find(function(b){
            return b.innerText && b.innerText.trim().indexOf('Pesquisar') !== -1;
        });
        if (btn) btn.click();
    """)


def consultar_instalacoes_mt(driver: webdriver.Chrome, instalacoes: list[str],
                              destino: Path) -> list[dict]:
    """
    Para cada UC antiga MT, preenche o formulário em URL_DEPARA_MT via JS puro
    (React nativeInputValueSetter), resolve captcha com 2captcha e lê newUc.
    Suporta retomada e salva incrementalmente a cada 10.
    """
    existentes = _carregar_csv_existente(destino)
    resultados: list[dict] = list(existentes.values())
    pendentes = [uc for uc in instalacoes if uc not in existentes]
    total = len(instalacoes)
    log(f"Pendentes: {len(pendentes)} de {total} (já coletados: {len(existentes)})")

    if not pendentes:
        return resultados

    log(f"Abrindo {URL_DEPARA_MT} ...")
    driver.get(URL_DEPARA_MT)
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='oldUc']"))
        )
    except TimeoutException:
        log("Página De/Para MT não carregou", "ERR")
        return resultados

    data_coleta = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    novos = 0

    for i, uc_antiga in enumerate(pendentes, 1):
        idx_global = len(existentes) + i
        log(f"[{idx_global:>3}/{total}] {uc_antiga} ...")
        try:
            # Remove iframes de captcha remanescentes
            driver.execute_script(_JS_LIMPAR_CAPTCHA)

            # Localiza elementos via Selenium (funciona com shadow DOM)
            campo_old = driver.find_element(By.CSS_SELECTOR, "input[name='oldUc']")
            campo_new = driver.find_element(By.CSS_SELECTOR, "input[name='newUc']")
            btn = driver.find_element(
                By.XPATH, "//button[.//span[normalize-space(text())='Pesquisar']]"
            )

            # Seta valores passando o elemento diretamente (não usa querySelector)
            _JS_REACT_SET = """
                var setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                setter.call(arguments[0], arguments[1]);
                arguments[0].dispatchEvent(new Event('input',  {bubbles:true}));
                arguments[0].dispatchEvent(new Event('change', {bubbles:true}));
            """
            driver.execute_script(_JS_REACT_SET, campo_new, "")
            driver.execute_script(_JS_REACT_SET, campo_old, uc_antiga)

            # Clica Pesquisar via JS (ignora overlays)
            driver.execute_script("arguments[0].click()", btn)

            # Aguarda e resolve captcha se aparecer
            _resolver_captcha(driver)

            # Remove resíduos e re-clica se newUc ainda vazio
            driver.execute_script(_JS_LIMPAR_CAPTCHA)
            nova_check = campo_new.get_attribute("value") or ""
            if not nova_check.strip():
                driver.execute_script("arguments[0].click()", btn)

            # Aguarda resultado
            WebDriverWait(driver, 20).until(
                lambda d: (d.find_element(By.CSS_SELECTOR, "input[name='newUc']")
                            .get_attribute("value") or "").strip()
            )
            uc_nova = (driver.find_element(By.CSS_SELECTOR, "input[name='newUc']")
                       .get_attribute("value") or "").strip()

            if uc_nova:
                reg = {"UC_ANTIGA": uc_antiga, "UC_ANEEL": uc_nova,
                       "DATA_COLETA": data_coleta}
                resultados.append(reg)
                existentes[uc_antiga] = reg
                novos += 1
                log(f"  → {uc_nova}", "OK")
            else:
                log("  → campo newUc vazio", "WARN")

        except Exception as exc:
            log(f"  → erro: {exc}", "WARN")

        if novos > 0 and novos % 10 == 0:
            salvar_csv(resultados, destino, fields=DEPARA_FIELDS_MT)

    log(f"Concluído MT: {novos} novos mapeamentos", "OK")
    return resultados


def atualizar_xlsx_mt(registros: list[dict]) -> None:
    """Adiciona/atualiza coluna UC_ANEEL nas linhas COPEL MT de acessos_copel.xlsx."""
    planilha = next(
        (p for p in [resolve_copel_accessos_xls(COPEL_DIR), ACESSOS_XLS, ACESSOS_XLS_LOCAL] if p.exists()), None
    )
    if not planilha:
        log("acessos_copel.xlsx não encontrado", "ERR")
        return
    depara: dict[str, str] = {
        r["UC_ANTIGA"]: r["UC_ANEEL"]
        for r in registros
        if r.get("UC_ANTIGA") and r.get("UC_ANEEL")
    }
    log(f"DE/PARA MT: {len(depara)} mapeamentos")
    df = pd.read_excel(planilha, dtype=str)
    df.columns = [c.strip() for c in df.columns]
    col_inst   = next((c for c in df.columns if "instalac" in c.lower()), None)
    col_conc   = next((c for c in df.columns if "concess"  in c.lower()), None)
    col_tensao = next((c for c in df.columns if "tens"     in c.lower()), None)
    if not col_inst:
        log("Coluna Instalação não encontrada", "ERR")
        return
    # Garante coluna UC_ANEEL; preserva valores BT já existentes
    if "UC_ANEEL" not in df.columns:
        df["UC_ANEEL"] = ""
    mask_mt = (
        df[col_conc].fillna("").apply(_ascii_fold).str.contains("COPEL", na=False) &
        df[col_tensao].fillna("").apply(_ascii_fold).str.contains("MEDIA", na=False)
    ) if col_conc and col_tensao else pd.Series([True] * len(df))
    df.loc[mask_mt, "UC_ANEEL"] = df.loc[mask_mt, col_inst].str.strip().map(depara)
    mapeados = df.loc[mask_mt, "UC_ANEEL"].notna().sum()
    log(f"Linhas MT atualizadas: {mapeados} / {mask_mt.sum()}")
    df.to_excel(planilha, index=False)
    log(f"acessos_copel.xlsx atualizado: {planilha}", "OK")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="COPEL DE/PARA — coleta mapeamento UC antiga → UC ANEEL do portal"
    )
    ap.add_argument(
        "--mt", action="store_true",
        help="Modo Média Tensão: usa app.copel.com/xuwweb/ sem login",
    )
    ap.add_argument(
        "--atualizar-xlsx", action="store_true",
        help="Além do CSV, atualiza coluna UC_ANEEL em acessos_copel.xlsx",
    )
    args = ap.parse_args()

    if args.mt:
        instalacoes = _carregar_ucs_xlsx(tensao_filtro="MEDIA")
        if not instalacoes:
            return 1
        driver = build_driver()
        try:
            registros = consultar_instalacoes_mt(driver, instalacoes, DEPARA_CSV_MT)
            if not registros:
                log("Nenhum registro MT coletado", "ERR")
                return 1
            salvar_csv(registros, DEPARA_CSV_MT, fields=DEPARA_FIELDS_MT)
            if args.atualizar_xlsx:
                atualizar_xlsx_mt(registros)
            else:
                log("Use --atualizar-xlsx para gravar UC_ANEEL no acessos_copel.xlsx", "INFO")
        finally:
            driver.quit()
        return 0

    # Modo padrão: BT via portal logado
    instalacoes = _carregar_ucs_xlsx(tensao_filtro="BAIXA")
    if not instalacoes:
        return 1

    driver = build_driver()
    try:
        if not fazer_login(driver):
            return 1

        registros = consultar_instalacoes(driver, instalacoes, DEPARA_CSV)
        if not registros:
            log("Nenhum registro coletado — encerrando", "ERR")
            return 1

        salvar_csv(registros, DEPARA_CSV)

        if args.atualizar_xlsx:
            atualizar_xlsx(registros)
        else:
            log("Use --atualizar-xlsx para gravar UC_ANEEL no acessos_copel.xlsx", "INFO")

    finally:
        driver.quit()

    return 0


if __name__ == "__main__":
    sys.exit(main())
