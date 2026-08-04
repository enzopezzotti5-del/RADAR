"""Bounded concurrent DNS/TLS/HTTP checks for downloader public entry pages."""
from __future__ import annotations

import concurrent.futures
import json
import socket
import time
from urllib.parse import urlparse

import requests


PORTALS = {
    "CPFL_RGE": "https://www.cpfl.com.br/agencia/area-cliente/cadastro",
    "CEMIG": "https://atende.cemig.com.br",
    "COPEL": "https://www.copel.com/avaweb/paginaLogin/login.jsf",
    "NEOENERGIA": "https://agenciavirtual.neoenergia.com",
    "ENEL": "https://portalhome.eneldistribuicaosp.com.br",
    "CELESC": "https://conecte.celesc.com.br/autenticacao/login",
    "EQUATORIAL": "https://goias.equatorialenergia.com.br/LoginGO.aspx",
    "LIGHT": "https://agenciavirtual.light.com.br/portal/",
}


def check(item: tuple[str, str]) -> dict:
    group, url = item
    started = time.monotonic()
    host = urlparse(url).hostname or ""
    try:
        addresses = sorted({row[4][0] for row in socket.getaddrinfo(host, 443)})
        response = requests.get(url, timeout=(8, 15), allow_redirects=True, stream=True)
        response.close()
        status = "PREFLIGHT_PASS" if response.status_code < 500 else "BLOCKED_EXTERNAL"
        return {"resource_group": group, "status": status, "dns": bool(addresses),
                "tls": response.url.startswith("https://"), "http_status": response.status_code,
                "duration_s": round(time.monotonic() - started, 2)}
    except (requests.RequestException, OSError) as exc:
        return {"resource_group": group, "status": "BLOCKED_EXTERNAL", "dns": False,
                "tls": False, "http_status": None, "error_type": type(exc).__name__,
                "duration_s": round(time.monotonic() - started, 2)}


def main() -> int:
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(check, PORTALS.items()))
    print(json.dumps({"parallelism": 4, "duration_s": round(time.monotonic() - started, 2),
                      "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
