#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


URL_CORPORATIVO = "https://portalhome.eneldistribuicaosp.com.br/#/corporativo"


def log(msg: str) -> None:
    print(f"[enel_sp_browser] {msg}")


def _find_cached_chromedriver() -> str | None:
    cache = Path.home() / ".cache" / "selenium" / "chromedriver" / "win64"
    if not cache.exists():
        return None
    for p in sorted(cache.iterdir(), reverse=True):
        exe = p / "chromedriver.exe"
        if exe.exists():
            return str(exe)
    return None


def build_driver(download_dir: Path, headless: bool = False) -> webdriver.Chrome:
    download_dir.mkdir(parents=True, exist_ok=True)

    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--window-size=1600,1000")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_experimental_option(
        "prefs",
        {
            "download.default_directory": str(download_dir.resolve()),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "plugins.always_open_pdf_externally": True,
            "profile.default_content_setting_values.automatic_downloads": 1,
        },
    )
    if headless:
        options.add_argument("--headless=new")

    chromedriver = _find_cached_chromedriver()
    service = Service(chromedriver) if chromedriver else Service()
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(90)
    _dl_path = str(download_dir.resolve())
    try:
        driver.execute_cdp_cmd(
            "Browser.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": _dl_path, "eventsEnabled": True},
        )
    except Exception:
        driver.execute_cdp_cmd(
            "Page.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": _dl_path},
        )
    return driver


def cleanup_driver(driver: webdriver.Chrome | None) -> None:
    if driver is None:
        return
    try:
        driver.quit()
    except Exception:
        pass


def save_debug(driver: webdriver.Chrome, out_dir: Path, prefix: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{prefix}.html").write_text(driver.page_source, encoding="utf-8")
    (out_dir / f"{prefix}.url.txt").write_text(driver.current_url or "", encoding="utf-8")
    driver.save_screenshot(str(out_dir / f"{prefix}.png"))


def _click_xpath(driver: webdriver.Chrome, xpaths: list[str], timeout: int = 20) -> None:
    last_exc = None
    for xp in xpaths:
        try:
            el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.XPATH, xp)))
            try:
                el.click()
            except Exception:
                driver.execute_script("arguments[0].click();", el)
            return
        except Exception as exc:
            last_exc = exc
    raise last_exc or RuntimeError(f"Não encontrou/clicou em nenhum XPath: {xpaths}")


def _is_pdf(path: Path) -> bool:
    """Verifica magic bytes — trata arquivos sem extensão baixados via CDP allowAndName."""
    try:
        with path.open("rb") as f:
            return f.read(4) == b"%PDF"
    except Exception:
        return False


def _click_element(driver: webdriver.Chrome, element) -> None:
    try:
        element.click()
        return
    except Exception:
        pass

    try:
        ActionChains(driver).move_to_element(element).pause(0.2).click(element).perform()
        return
    except Exception:
        pass

    driver.execute_script("arguments[0].click();", element)


def _find_enabled_button(driver: webdriver.Chrome, labels: list[str]):
    for label in labels:
        buttons = driver.find_elements(
            By.XPATH,
            f"//button[not(@disabled) and contains(normalize-space(.), '{label}')]",
        )
        if buttons:
            return buttons[0]
    return None


def _wait_enabled_button(driver: webdriver.Chrome, labels: list[str], timeout: int = 20):
    return WebDriverWait(driver, timeout).until(lambda d: _find_enabled_button(d, labels))


def _select_row_radio(driver: webdriver.Chrome, row) -> None:
    candidatos = [
        ".//md-radio-button[@role='radio']",
        ".//*[@role='radio']",
        ".//input[@type='radio']",
        ".//*[contains(@class, 'md-container')]",
    ]

    ultimo_erro = None
    for xp in candidatos:
        elementos = row.find_elements(By.XPATH, xp)
        for elemento in elementos:
            try:
                _click_element(driver, elemento)
                return
            except Exception as exc:
                ultimo_erro = exc

    try:
        _click_element(driver, row)
        return
    except Exception as exc:
        ultimo_erro = exc

    raise ultimo_erro or RuntimeError("Nao foi possivel selecionar a linha desejada.")


def abrir_fluxo_login(driver: webdriver.Chrome) -> None:
    driver.get(URL_CORPORATIVO)
    WebDriverWait(driver, 40).until(
        lambda d: "/autenticacao/login" in (d.current_url or "") or bool(d.find_elements(By.ID, "email"))
    )


def fazer_login(driver: webdriver.Chrome, email: str, senha: str) -> None:
    email_input = WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, "email")))
    senha_input = WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, "senha")))
    email_input.clear()
    email_input.send_keys(email)
    senha_input.clear()
    senha_input.send_keys(senha)

    WebDriverWait(driver, 20).until(
        lambda d: any(btn.is_enabled() for btn in d.find_elements(By.XPATH, "//button[contains(normalize-space(.), 'Entrar')]"))
    )
    botoes = driver.find_elements(By.XPATH, "//button[contains(normalize-space(.), 'Entrar')]")
    if botoes:
        try:
            botoes[0].click()
        except Exception:
            try:
                driver.execute_script("arguments[0].click();", botoes[0])
            except Exception:
                senha_input.send_keys(Keys.ENTER)
    else:
        senha_input.send_keys(Keys.ENTER)


def aguardar_pos_login(driver: webdriver.Chrome, timeout: int = 90) -> str:
    fim = time.time() + timeout
    ultimo_url = ""
    while time.time() < fim:
        url = driver.current_url or ""
        ultimo_url = url
        src = driver.page_source or ""
        if "/trocar-instalacao" in url:
            return "trocar-instalacao"
        if "/selecionar-cnpj-corp" in url:
            return "selecionar-cnpj-corp"
        if "/home" in url or "Minha Conta" in src or "Selecione abaixo a instala" in src:
            return "home"
        if "/autenticacao/login" not in url and "Acesse sua conta" not in src:
            return "fora-login"
        time.sleep(1)
    raise TimeoutError(f"Pós-login não avançou. Última URL: {ultimo_url}")


def selecionar_primeiro_cnpj(driver: webdriver.Chrome) -> None:
    WebDriverWait(driver, 60).until(
        lambda d: "/trocar-instalacao" in (d.current_url or "") or "Selecione abaixo o CNPJ" in (d.page_source or "")
    )
    primeira_linha = WebDriverWait(driver, 20).until(
        lambda d: d.find_element(By.XPATH, "(//div[contains(@class,'desktop_table')]//tr[td])[1]")
    )
    _select_row_radio(driver, primeira_linha)
    time.sleep(1.0)
    botao_seguinte = _wait_enabled_button(driver, ["SEGUINTE", "Seguinte"], timeout=20)
    _click_element(driver, botao_seguinte)
    WebDriverWait(driver, 60).until(
        lambda d: "/selecionar-cnpj-corp" in (d.current_url or "") or "Selecione abaixo a instala" in (d.page_source or "")
    )


def selecionar_instalacao(driver: webdriver.Chrome, uc: str) -> None:
    uc_txt = str(uc).strip()
    uc_sem_zero = uc_txt.lstrip("0") or "0"
    WebDriverWait(driver, 60).until(
        lambda d: "/selecionar-cnpj-corp" in (d.current_url or "")
        or "Selecione abaixo a instala" in (d.page_source or "")
        or "SERVIÇOS SEM INSTALAÇÃO" in (d.page_source or "")
    )

    filtros = driver.find_elements(By.XPATH, "//input[contains(@placeholder, 'Instala')]")
    if filtros:
        campo = filtros[0]
        campo.clear()
        campo.send_keys(uc_txt)
        time.sleep(2)

    linhas = driver.find_elements(
        By.XPATH,
        f"//tr[.//*[contains(normalize-space(.), '{uc_txt}')] or .//*[contains(normalize-space(.), '{uc_sem_zero}')]]",
    )
    for linha in linhas:
        try:
            _select_row_radio(driver, linha)
            time.sleep(1.0)
            botao_entrar = _wait_enabled_button(driver, ["ENTRAR", "Entrar"], timeout=20)
            _click_element(driver, botao_entrar)
            WebDriverWait(driver, 60).until(
                lambda d: "/selecionar-cnpj-corp" not in (d.current_url or "")
                or "Minha Conta" in (d.page_source or "")
            )
            return
        except Exception:
            pass

    rotulos = driver.find_elements(By.XPATH, f"//*[contains(normalize-space(.), '{uc_sem_zero}')]")
    for el in rotulos:
        try:
            linha = el.find_element(By.XPATH, "./ancestor::tr[1]")
            _select_row_radio(driver, linha)
            time.sleep(1.0)
            botao_entrar = _wait_enabled_button(driver, ["ENTRAR", "Entrar"], timeout=20)
            _click_element(driver, botao_entrar)
            WebDriverWait(driver, 60).until(
                lambda d: "/selecionar-cnpj-corp" not in (d.current_url or "")
                or "Minha Conta" in (d.page_source or "")
            )
            return
        except Exception:
            continue

    raise RuntimeError(f"UC não encontrada na lista: {uc_txt}")


def abrir_contas_pagamentos(driver: webdriver.Chrome) -> None:
    _click_xpath(
        driver,
        [
            "//span[contains(normalize-space(.), 'Minha Conta')]",
            "//a[contains(normalize-space(.), 'Minha Conta')]",
            "//button[contains(normalize-space(.), 'Minha Conta')]",
        ],
        timeout=20,
    )
    time.sleep(2)
    _click_xpath(
        driver,
        [
            "//span[contains(normalize-space(.), 'Contas e pagamentos')]",
            "//a[contains(normalize-space(.), 'Contas e pagamentos')]",
            "//button[contains(normalize-space(.), 'Contas e pagamentos')]",
        ],
        timeout=20,
    )
    time.sleep(2)
    # Portal pode mostrar uma página de tiles intermediária; se estiver nela,
    # clicar no tile "Contas e pagamentos" para chegar à lista de faturas.
    if "/segunda-via" in (driver.current_url or "") or "segunda-via" in (driver.current_url or ""):
        try:
            tile = WebDriverWait(driver, 8).until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//*[contains(@class,'tile') or contains(@class,'card') or self::a or self::button]"
                    "[.//*[contains(normalize-space(.), 'Contas e pagamentos')] or "
                    "contains(normalize-space(.), 'Contas e pagamentos')]"
                ))
            )
            _click_element(driver, tile)
            time.sleep(2)
        except Exception:
            pass
    WebDriverWait(driver, 30).until(lambda d: "Contas e pagamentos" in (d.page_source or ""))


def _selecionar_filtro_contas(driver: webdriver.Chrome, label: str) -> bool:
    xpaths = [
        f"//md-radio-button[@role='radio' and .//label[normalize-space(.)='{label}']]",
        f"//label[normalize-space(.)='{label}']/ancestor::md-radio-button[1]",
        f"//*[@role='radio' and .//*[normalize-space(.)='{label}']]",
    ]

    for xp in xpaths:
        elementos = driver.find_elements(By.XPATH, xp)
        for elemento in elementos:
            try:
                _click_element(driver, elemento)
                WebDriverWait(driver, 10).until(
                    lambda d, texto=label: d.find_elements(
                        By.XPATH,
                        f"//md-radio-button[@role='radio' and @aria-checked='true' and .//label[normalize-space(.)='{texto}']]",
                    )
                )
                time.sleep(1.5)
                return True
            except Exception:
                continue
    return False


def baixar_fatura(driver: webdriver.Chrome, mes_ref: str, download_dir: Path, timeout: int = 60) -> Path:
    mes_ref = str(mes_ref).strip()
    ano = mes_ref.split("-")[-1]
    mes_num = mes_ref.split("-")[0]
    mapa_mes = {
        "01": "Janeiro", "02": "Fevereiro", "03": "Março", "04": "Abril",
        "05": "Maio", "06": "Junho", "07": "Julho", "08": "Agosto",
        "09": "Setembro", "10": "Outubro", "11": "Novembro", "12": "Dezembro",
    }
    nome_mes = mapa_mes.get(mes_num, mes_num)
    refs_tela = [
        f"{nome_mes}/{ano[-2:]}",
        f"{nome_mes}/{ano}",
        f"{mes_num}/{ano}",
        mes_ref.replace("-", "/"),
    ]

    vistos = {p.name for p in download_dir.glob("*")}

    ultimo_erro = None
    filtros = [None, "Pendentes", "Pagas", "Todas"]
    for filtro in filtros:
        if filtro:
            _selecionar_filtro_contas(driver, filtro)

        seletores_download = []
        for ref_tela in refs_tela:
            seletores_download.extend(
                [
                    # Seletor primário: div.act2 dentro do md-list-item que contém a referência
                    f"//md-list-item[.//*[contains(normalize-space(.), '{ref_tela}')]]//div[contains(@class,'act2') and contains(@class,'act-enable')]",
                    # ng-click gerarPdf dentro do item com a referência
                    f"//md-list-item[.//*[contains(normalize-space(.), '{ref_tela}')]]//*[@ng-click and contains(@ng-click,'gerarPdf')]",
                    # Linha <tr> com referência (portais alternativos)
                    f"//tr[.//*[contains(normalize-space(.), '{ref_tela}')]]//div[contains(@class,'act2')]",
                    f"//tr[.//*[contains(normalize-space(.), '{ref_tela}')]]//*[@ng-click and contains(@ng-click,'gerarPdf')]",
                ]
            )
        # Fallbacks globais — div.act2 ou ng-click gerarPdf sem filtrar por ref
        seletores_download.append("//div[contains(@class,'act2') and contains(@class,'act-enable')]")
        seletores_download.append("//*[@ng-click and contains(@ng-click,'gerarPdf')]")

        for xp in seletores_download:
            try:
                alvo = WebDriverWait(driver, 6).until(EC.element_to_be_clickable((By.XPATH, xp)))
                log(f"Clicando baixar conta via: {xp[:80]}")
                _click_element(driver, alvo)
                ultimo_erro = None
                break
            except Exception as exc:
                ultimo_erro = exc

        if ultimo_erro is None:
            break

    if ultimo_erro is not None:
        raise ultimo_erro

    # Se a fatura abriu numa nova aba, aplica o comportamento de download e fecha a aba.
    time.sleep(2)
    if len(driver.window_handles) > 1:
        nova_aba = driver.window_handles[-1]
        driver.switch_to.window(nova_aba)
        _dl = str(download_dir.resolve())
        try:
            driver.execute_cdp_cmd("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": _dl, "eventsEnabled": True})
        except Exception:
            pass
        try:
            driver.execute_cdp_cmd("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": _dl})
        except Exception:
            pass
        time.sleep(3)
        driver.close()
        driver.switch_to.window(driver.window_handles[0])

    fim = time.time() + timeout
    while time.time() < fim:
        candidatos = [p for p in download_dir.glob("*") if p.name not in vistos and not p.name.endswith(".crdownload")]
        pdfs = [p for p in candidatos if p.suffix.lower() == ".pdf" or _is_pdf(p)]
        if pdfs:
            pdf = max(pdfs, key=lambda p: p.stat().st_mtime)
            # Garante extensão .pdf no destino final
            if pdf.suffix.lower() != ".pdf":
                destino = pdf.with_suffix(".pdf")
                pdf.rename(destino)
                pdf = destino
            time.sleep(1)
            return pdf
        # Enquanto houver .crdownload, o download ainda está em progresso
        if list(download_dir.glob("*.crdownload")):
            fim = max(fim, time.time() + 30)
        time.sleep(1)
    raise TimeoutError("PDF não apareceu na pasta de download")


def baixar_fatura_enel_sp_via_navegador(
    email: str,
    senha: str,
    uc: str,
    mes_ref: str,
    download_dir: Path,
    debug_dir: Path | None = None,
    headless: bool = False,
) -> Path:
    driver = None
    debug_dir = debug_dir or (download_dir / "_debug")
    try:
        driver = build_driver(download_dir=download_dir, headless=headless)
        abrir_fluxo_login(driver)
        save_debug(driver, debug_dir, "01_login")
        fazer_login(driver, email, senha)
        estado = aguardar_pos_login(driver, timeout=90)
        save_debug(driver, debug_dir, "02_pos_login")
        if estado == "trocar-instalacao":
            selecionar_primeiro_cnpj(driver)
            save_debug(driver, debug_dir, "03_cnpj")
        else:
            save_debug(driver, debug_dir, "03_sem_cnpj")
        selecionar_instalacao(driver, uc)
        save_debug(driver, debug_dir, "04_instalacao")
        abrir_contas_pagamentos(driver)
        save_debug(driver, debug_dir, "05_contas_pagamentos")
        pdf = baixar_fatura(driver, mes_ref, download_dir)
        save_debug(driver, debug_dir, "06_pdf_baixado")
        log(f"PDF baixado via navegador: {pdf}")
        return pdf
    except Exception:
        if driver is not None:
            try:
                save_debug(driver, debug_dir, "99_erro")
            except Exception:
                pass
        raise
    finally:
        cleanup_driver(driver)
