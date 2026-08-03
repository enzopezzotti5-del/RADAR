#!/usr/bin/env python3
"""Diagnóstico: imprime todas as opções do dropdown de UC na página de Segunda Via."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import time
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait

INSTALACAO = "580018258"
CNPJ       = "00000000000191"
URL_LOGIN  = "https://goias.equatorialenergia.com.br/LoginGO.aspx"

opts = uc.ChromeOptions()
opts.add_argument("--start-maximized")
opts.add_argument("--lang=pt-BR")
driver = uc.Chrome(options=opts, use_subprocess=True, version_main=149)
driver.set_page_load_timeout(60)

try:
    print("Abrindo login...")
    driver.get(URL_LOGIN)
    time.sleep(3)

    # Preenche instalação
    campo_uc = driver.find_element(By.CSS_SELECTOR, "input[id*='txtUC']")
    campo_uc.clear()
    campo_uc.send_keys(INSTALACAO)
    time.sleep(0.5)

    # Preenche CNPJ
    campo_doc = driver.find_element(By.CSS_SELECTOR, "input[id*='txtDocumento']")
    campo_doc.clear()
    campo_doc.send_keys(CNPJ)
    time.sleep(0.5)

    # Clica Entrar
    btn = driver.find_element(By.XPATH, "//button[@type='button' and contains(@onclick,'ValidarCamposAreaLogada')]")
    btn.click()
    time.sleep(5)

    print(f"URL pós-login: {driver.current_url}")

    # Fecha modal se existir
    try:
        btn_close = driver.find_element(By.CSS_SELECTOR, "button[data-dismiss='modal']")
        btn_close.click()
        time.sleep(1)
    except Exception:
        pass

    # Abre menu Contas → Segunda Via
    try:
        driver.find_element(By.CSS_SELECTOR, "label[for='A']").click()
        time.sleep(1)
    except Exception:
        pass
    driver.find_element(By.ID, "LinkSegundaVia").click()
    time.sleep(3)

    print(f"URL Segunda Via: {driver.current_url}")

    # Dump de TODOS os selects
    selects = driver.find_elements(By.TAG_NAME, "select")
    print(f"\nTotal de <select> na página: {len(selects)}")
    for s in selects:
        sid  = s.get_attribute("id") or "(sem id)"
        snam = s.get_attribute("name") or "(sem name)"
        try:
            opcoes = Select(s).options
            print(f"\n--- SELECT id='{sid}' name='{snam}' ({len(opcoes)} opções) ---")
            for o in opcoes:
                val = o.get_attribute("value") or ""
                txt = o.text or ""
                print(f"  value='{val}'  text='{txt}'")
        except Exception as e:
            print(f"  [erro ao ler: {e}]")

finally:
    input("\nPressione Enter para fechar o Chrome...")
    driver.quit()
