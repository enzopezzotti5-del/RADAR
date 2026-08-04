"""Bounded browser compatibility check; it never opens a provider portal."""
from __future__ import annotations

import json
from selenium import webdriver
from selenium.webdriver.chrome.options import Options


def main() -> int:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-first-run")
    driver = None
    try:
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(15)
        driver.get("data:text/html,<title>Radar preflight</title><p id='ok'>ok</p>")
        caps = driver.capabilities
        print(json.dumps({
            "status": "PREFLIGHT_PASS", "browser": caps.get("browserName"),
            "browser_version": caps.get("browserVersion"),
            "driver_version": (caps.get("chrome", {}) or {}).get("chromedriverVersion", "").split(" ")[0],
            "page_ready": driver.title == "Radar preflight",
        }))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "PREFLIGHT_FAIL", "error_type": type(exc).__name__}))
        return 1
    finally:
        if driver is not None:
            driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
