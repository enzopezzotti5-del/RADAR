"""
Inspeciona o portal Energisa com Selenium + BS4.
Abre o browser interativamente, pausando em cada etapa para mapear seletores.

Uso:
    .venv\\Scripts\\python.exe core\\downloaders\\energisa\\inspecionar_portal.py
    .venv\\Scripts\\python.exe core\\downloaders\\energisa\\inspecionar_portal.py --cnpj 12345678000100
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

URL_LOGIN = "https://servicos.energisa.com.br/login"
SEP = "=" * 70


def soup(driver: webdriver.Chrome) -> BeautifulSoup:
    return BeautifulSoup(driver.page_source, "html.parser")


def wait(driver: webdriver.Chrome, sec: int = 20) -> WebDriverWait:
    return WebDriverWait(driver, sec)


def _imprimir_inputs(s: BeautifulSoup, titulo: str) -> None:
    print(f"\n--- {titulo} ---")
    for el in s.find_all("input"):
        attrs = {k: v for k, v in el.attrs.items() if k in ("id","name","type","placeholder","class","maxlength","aria-label")}
        print(f"  <input {attrs}>")


def _imprimir_botoes(s: BeautifulSoup, titulo: str) -> None:
    print(f"\n--- {titulo} ---")
    for el in s.find_all("button"):
        texto = el.get_text(strip=True)[:60]
        attrs = {k: v for k, v in el.attrs.items() if k in ("id","class","type","aria-label")}
        print(f"  <button texto={texto!r} {attrs}>")


def _imprimir_clickaveis(s: BeautifulSoup, titulo: str) -> None:
    print(f"\n--- {titulo} ---")
    for el in s.find_all(attrs={"role": "button"}):
        texto = el.get_text(strip=True)[:80]
        print(f"  role=button tag={el.name} texto={texto!r} class={el.get('class','')}")
    for el in s.find_all("li"):
        texto = el.get_text(strip=True)[:80]
        if texto:
            print(f"  <li> texto={texto!r} class={el.get('class','')}")
    for el in s.find_all("a"):
        texto = el.get_text(strip=True)[:80]
        if texto:
            print(f"  <a> texto={texto!r} href={el.get('href','')} class={el.get('class','')}")


def _imprimir_textos_relevantes(s: BeautifulSoup, palavras: list[str]) -> None:
    print(f"\n--- Textos contendo {palavras} ---")
    for tag in s.find_all(True):
        texto = tag.get_text(strip=True)
        if any(p.lower() in texto.lower() for p in palavras) and len(texto) < 200:
            print(f"  <{tag.name}> {texto!r}")


def pausar(msg: str = "Pressione Enter para continuar...") -> None:
    input(f"\n{'>'*3} {msg}")


def iniciar_driver() -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    # Remove navigator.webdriver via CDP
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    driver.implicitly_wait(3)
    return driver


def inspecionar(cnpj: str = "") -> None:
    driver = iniciar_driver()

    try:
        # ------------------------------------------------------------------ #
        # ETAPA 1 — Página de login                                           #
        # ------------------------------------------------------------------ #
        print(f"\n{SEP}")
        print("ETAPA 1 — LOGIN")
        print(SEP)
        driver.get(URL_LOGIN)
        time.sleep(3)

        s = soup(driver)
        _imprimir_inputs(s, "Inputs na tela de login")
        _imprimir_botoes(s, "Botões na tela de login")
        _imprimir_textos_relevantes(s, ["CNPJ", "CPF", "Digite", "Entrar"])

        print(f"\nURL atual: {driver.current_url}")

        if cnpj:
            try:
                # Seletor confirmado via inspeção: data-cy="input-cpf-cnpj"
                campo = wait(driver).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, '[data-cy="input-cpf-cnpj"]'))
                )
                campo.click()
                campo.clear()
                # Remove zeros à esquerda se o CNPJ vier com padding (ex: 00000000027200 → 27200)
                import re as _re
                cnpj_limpo = _re.sub(r"\D", "", cnpj)  # só dígitos, preserva zeros
                print(f"  >> Digitando CNPJ: {cnpj_limpo}")
                campo.send_keys(cnpj_limpo)
                time.sleep(1)

                # Botão Entrar — tenta data-cy primeiro, depois texto
                btn = None
                for sel in ('[data-cy="btn-submit"]', 'button[type="submit"]'):
                    els = driver.find_elements(By.CSS_SELECTOR, sel)
                    if els:
                        btn = els[0]
                        break
                if not btn:
                    btns = driver.find_elements(By.TAG_NAME, "button")
                    btn = next((b for b in btns if "Entrar" in b.text and b.is_enabled()), None)
                if not btn:
                    btn = next((b for b in btns if b.is_displayed() and b.is_enabled()), None)

                if btn:
                    btn.click()
                    print("  >> Entrar clicado.")
                else:
                    pausar("Botão Entrar não encontrado — clique manualmente. Depois pressione Enter aqui.")
            except Exception as exc:
                print(f"  >> Falha: {exc}")
                pausar("Preencha o CNPJ manualmente e clique Entrar. Depois pressione Enter aqui.")
        else:
            pausar("Preencha o CNPJ manualmente no browser e clique Entrar. Depois pressione Enter aqui.")

        # ------------------------------------------------------------------ #
        # ETAPA 2 — Seleção de contato                                        #
        # ------------------------------------------------------------------ #
        print(f"\n{SEP}")
        print("ETAPA 2 — SELEÇÃO DE CONTATO")
        print(SEP)
        time.sleep(2)

        s = soup(driver)
        _imprimir_inputs(s, "Inputs na tela de contato")
        _imprimir_botoes(s, "Botões na tela de contato")
        _imprimir_clickaveis(s, "Elementos clicáveis (contatos)")
        _imprimir_textos_relevantes(s, ["email", "contato", "acaoenge", "acaoengenharia", "bben"])

        print(f"\nURL atual: {driver.current_url}")

        # Tenta clicar automaticamente no botão bbenergia
        clicou = False
        for dominio in ("acaoengenharia.com.br", "acaoenge.com.br"):
            try:
                xpath = f"//button[.//p[contains(text(), '{dominio}')]]"
                btn = wait(driver).until(EC.element_to_be_clickable((By.XPATH, xpath)))
                btn.click()
                print(f"  >> Contato *@{dominio} clicado automaticamente.")
                clicou = True
                break
            except Exception:
                continue
        if not clicou:
            pausar("Contato bbenergia não encontrado — selecione manualmente. Depois pressione Enter aqui.")
        else:
            time.sleep(1)

        # ------------------------------------------------------------------ #
        # ETAPA 3 — Tela de OTP                                               #
        # ------------------------------------------------------------------ #
        print(f"\n{SEP}")
        print("ETAPA 3 — OTP")
        print(SEP)
        time.sleep(2)

        s = soup(driver)
        _imprimir_inputs(s, "Inputs na tela de OTP")
        _imprimir_botoes(s, "Botões na tela de OTP")
        _imprimir_textos_relevantes(s, ["código", "codigo", "token", "tentativa", "aguarde"])

        # Inspeciona inputs individuais do OTP
        inputs_otp = s.find_all("input")
        print(f"\n  Total de inputs: {len(inputs_otp)}")
        for i, el in enumerate(inputs_otp):
            print(f"  [{i}] maxlength={el.get('maxlength')} type={el.get('type')} id={el.get('id')} class={el.get('class')}")

        print(f"\nURL atual: {driver.current_url}")
        pausar("Preencha o código OTP manualmente. Depois pressione Enter aqui.")

        # ------------------------------------------------------------------ #
        # ETAPA 4 — Home / Modal                                              #
        # ------------------------------------------------------------------ #
        print(f"\n{SEP}")
        print("ETAPA 4 — HOME (aguardando carregar)")
        print(SEP)
        time.sleep(3)

        s = soup(driver)
        _imprimir_botoes(s, "Botões na home (incluindo modal)")
        _imprimir_textos_relevantes(s, ["baixar", "2ª via", "2a via", "modal", "pix", "fechar", "ativar"])

        # Procura especificamente botão de fechar modal
        print("\n--- Candidatos a fechar modal ---")
        for el in s.find_all(True):
            texto = el.get_text(strip=True)
            if any(p in texto.lower() for p in ["não quero", "nao quero", "fechar", "×", "✕"]) and len(texto) < 60:
                print(f"  <{el.name}> texto={texto!r} class={el.get('class','')} id={el.get('id','')}")

        print(f"\nURL atual: {driver.current_url}")
        pausar("Feche o modal manualmente. Depois pressione Enter aqui.")

        # ------------------------------------------------------------------ #
        # ETAPA 5 — Home sem modal / Baixar 2ª via                           #
        # ------------------------------------------------------------------ #
        print(f"\n{SEP}")
        print("ETAPA 5 — HOME LIMPA / BAIXAR 2ª VIA")
        print(SEP)
        time.sleep(1)

        s = soup(driver)
        _imprimir_botoes(s, "Botões disponíveis")
        _imprimir_clickaveis(s, "Elementos clicáveis")
        _imprimir_textos_relevantes(s, ["baixar", "2ª via", "segunda via", "fatura", "download"])

        # Inspeciona cards de serviços
        print("\n--- Cards / serviços ---")
        for el in s.find_all(class_=lambda c: c and any(p in " ".join(c) for p in ["card","service","item","btn"])):
            texto = el.get_text(strip=True)[:100]
            if texto:
                print(f"  <{el.name}> class={el.get('class','')} texto={texto!r}")

        print(f"\nURL atual: {driver.current_url}")
        pausar("Clique em Baixar 2ª via manualmente. Depois pressione Enter aqui.")

        # ------------------------------------------------------------------ #
        # ETAPA 6 — Pós-download                                             #
        # ------------------------------------------------------------------ #
        print(f"\n{SEP}")
        print("ETAPA 6 — PÓS-DOWNLOAD")
        print(SEP)
        time.sleep(2)

        s = soup(driver)
        print(f"URL atual: {driver.current_url}")
        _imprimir_botoes(s, "Botões após download")
        _imprimir_textos_relevantes(s, ["download", "baixado", "sucesso", "fatura", "mês"])

        print(f"\n{SEP}")
        print("INSPEÇÃO CONCLUÍDA")
        print(SEP)
        pausar("Pressione Enter para fechar o browser.")

    finally:
        driver.quit()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Inspeciona o portal Energisa etapa a etapa")
    p.add_argument("--cnpj", default="", help="CNPJ para preencher (só informativo no prompt)")
    args = p.parse_args()
    inspecionar(cnpj=args.cnpj)
