"""
Automação do portal Energisa conectando ao Chrome já aberto pelo usuário.

PRÉ-REQUISITO:
    Abrir o Chrome com depuração remota usando o atalho na área de trabalho:
        abrir_chrome_energisa.bat

    O script conecta ao Chrome em execução via CDP — sem nenhuma flag de
    automação, usando o perfil real (Profile 2) com fingerprint e cookies reais.

Uso:
    from core.downloaders.energisa.portal import PortalEnergisa
    with PortalEnergisa() as portal:
        resultados = portal.baixar_faturas_cnpj(cnpj="00000000531391", ucs=["265.194.015-76"])
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from core.downloaders.energisa.email_reader import aguardar_otp, uid_atual

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

URL_LOGIN  = "https://servicos.energisa.com.br/login"
URL_LOGOUT = "https://servicos.energisa.com.br/logout"

_CHROME_EXE  = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
_USER_DATA   = r"C:\Users\Revit\AppData\Local\energisa_chrome_profile"
_DEBUG_ADDR  = "127.0.0.1:9222"

TIMEOUT_ELEMENT  = 20   # segundos
TIMEOUT_OTP      = 120  # segundos
TIMEOUT_DOWNLOAD = 60   # segundos


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _soup(driver: webdriver.Chrome) -> BeautifulSoup:
    return BeautifulSoup(driver.page_source, "html.parser")

def _wait(driver: webdriver.Chrome, timeout: int = TIMEOUT_ELEMENT) -> WebDriverWait:
    return WebDriverWait(driver, timeout)


def _abrir_chrome_se_necessario() -> None:
    """Abre Chrome com debug port se ainda não estiver rodando."""
    import urllib.request
    try:
        urllib.request.urlopen(f"http://{_DEBUG_ADDR}/json/version", timeout=2)
        print("[portal] Chrome já está em execução na porta 9222.")
        return
    except Exception:
        pass

    print("[portal] Abrindo Chrome com depuração remota...")
    subprocess.Popen([
        _CHROME_EXE,
        "--remote-debugging-port=9222",
        f"--user-data-dir={_USER_DATA}",
        "--profile-directory=Default",
        "--no-first-run",
        "--no-default-browser-check",
    ])
    # Aguarda Chrome iniciar e expor a porta
    for _ in range(20):
        time.sleep(1)
        try:
            urllib.request.urlopen(f"http://{_DEBUG_ADDR}/json/version", timeout=2)
            print("[portal] Chrome pronto.")
            return
        except Exception:
            continue
    raise RuntimeError(
        "Chrome não respondeu na porta 9222 após 20s.\n"
        "Abra manualmente: abrir_chrome_energisa.bat"
    )


# ---------------------------------------------------------------------------
# Classe principal
# ---------------------------------------------------------------------------

class PortalEnergisa:
    """
    Context manager que conecta ao Chrome do usuário via CDP.
    Chrome deve estar aberto com --remote-debugging-port=9222 (via abrir_chrome_energisa.bat).
    """

    def __init__(self, download_dir: str | Path = "D:/downloads/energisa"):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.driver: webdriver.Chrome | None = None

    def __enter__(self) -> "PortalEnergisa":
        self._iniciar_driver()
        self._abrir_aba_energisa()
        return self

    def __exit__(self, *_) -> None:
        # Fecha só a aba que abrimos — não fecha o Chrome
        if self.driver:
            try:
                aba = getattr(self, "_aba_energisa", None)
                handles = self.driver.window_handles
                if aba and aba in handles and len(handles) > 1:
                    self.driver.switch_to.window(aba)
                    self.driver.close()
                    self.driver.switch_to.window(handles[0] if handles[0] != aba else handles[-1])
            except Exception:
                pass
            # NÃO chama quit() — o Chrome é do usuário
            self.driver = None

    def _iniciar_driver(self) -> None:
        _abrir_chrome_se_necessario()

        opts = Options()
        opts.add_experimental_option("debuggerAddress", _DEBUG_ADDR)
        opts.set_capability("goog:loggingPrefs", {"performance": "ALL", "browser": "ALL"})
        # Configura download para a pasta correta via CDP depois de conectar
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=opts)
        # Diretório de download via CDP
        self.driver.execute_cdp_cmd("Page.setDownloadBehavior", {
            "behavior": "allow",
            "downloadPath": str(self.download_dir),
        })
        self.driver.implicitly_wait(3)
        print(f"[portal] Conectado ao Chrome: {self.driver.title or '(sem título)'}")

    def _abrir_aba_energisa(self) -> None:
        """Abre uma aba nova para a automação — não perturba abas abertas."""
        handles_antes = set(self.driver.window_handles)
        self.driver.execute_script("window.open('about:blank', '_blank');")
        time.sleep(0.5)
        nova = next(
            (h for h in self.driver.window_handles if h not in handles_antes),
            None
        )
        if nova:
            self._aba_energisa = nova
            self.driver.switch_to.window(nova)
            print(f"[portal] Aba nova aberta: {nova}")
        else:
            self._aba_energisa = self.driver.current_window_handle
            print("[portal] Usando aba atual (sem nova aba disponível).")

    def limpar_logs_performance(self) -> None:
        """Descarta eventos acumulados do performance log do Chrome."""
        try:
            self.driver.get_log("performance")
        except Exception:
            pass

    def capturar_rede_recente(self, filtro_url: str = "energisa", incluir_next: bool = False) -> list[dict]:
        """Le e normaliza eventos de rede recentes via performance log."""
        eventos: list[dict] = []
        try:
            bruto = self.driver.get_log("performance")
        except Exception as exc:
            print(f"[portal] Falha ao ler performance log: {exc}")
            return eventos

        for item in bruto:
            try:
                msg = json.loads(item["message"])["message"]
            except Exception:
                continue

            metodo = msg.get("method", "")
            params = msg.get("params", {})
            if metodo == "Network.requestWillBeSent":
                req = params.get("request", {})
                url = req.get("url", "")
                if filtro_url and filtro_url.lower() not in url.lower():
                    continue
                if not incluir_next and "/_next/" in url:
                    continue
                eventos.append({
                    "tipo": "request",
                    "requestId": params.get("requestId"),
                    "method": req.get("method"),
                    "url": url,
                    "headers": req.get("headers", {}),
                    "postData": req.get("postData", ""),
                })
            elif metodo == "Network.responseReceived":
                resp = params.get("response", {})
                url = resp.get("url", "")
                if filtro_url and filtro_url.lower() not in url.lower():
                    continue
                if not incluir_next and "/_next/" in url:
                    continue
                eventos.append({
                    "tipo": "response",
                    "requestId": params.get("requestId"),
                    "status": resp.get("status"),
                    "url": url,
                    "mimeType": resp.get("mimeType", ""),
                    "headers": resp.get("headers", {}),
                })
        return eventos

    def capturar_console_recente(self) -> list[dict]:
        """Le mensagens recentes do console do navegador."""
        try:
            return self.driver.get_log("browser")
        except Exception as exc:
            print(f"[portal] Falha ao ler browser log: {exc}")
            return []

    # ------------------------------------------------------------------
    # Etapas do fluxo
    # ------------------------------------------------------------------

    def _navegar(self, url: str, espera: float = 2.0) -> None:
        """Navega via JS para preservar cookies SBSD do Akamai."""
        self.driver.execute_script(f"window.location.href = '{url}';")
        time.sleep(espera)

    def _preencher_input_react(self, elemento, valor: str) -> None:
        """Preenche input React disparando eventos sintéticos corretamente."""
        self.driver.execute_script("""
            var el = arguments[0], val = arguments[1];
            var nativeSet = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            nativeSet.call(el, val);
            el.dispatchEvent(new Event('input',  { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('blur',   { bubbles: true }));
        """, elemento, valor)

    def _etapa_login_cnpj(self, cnpj: str) -> None:
        # Navega para login via JS (preserva SBSD cookies do Akamai)
        self._navegar(URL_LOGIN, espera=3)
        campo = _wait(self.driver).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '[data-cy="input-cpf-cnpj"]'))
        )
        cnpj_limpo = re.sub(r"\D", "", cnpj)
        campo.click()
        time.sleep(0.3)
        self._preencher_input_react(campo, cnpj_limpo)
        time.sleep(1.5)

        btn = None
        for sel in ('[data-cy="btn-submit"]', 'button[type="submit"]'):
            els = self.driver.find_elements(By.CSS_SELECTOR, sel)
            if els:
                btn = els[0]
                break
        if not btn:
            for b in self.driver.find_elements(By.TAG_NAME, "button"):
                if "Entrar" in (b.text or ""):
                    btn = b
                    break
        if btn:
            from selenium.webdriver.common.action_chains import ActionChains
            ActionChains(self.driver).move_to_element(btn).click().perform()
            print(f"[portal] Entrar clicado (CNPJ={cnpj_limpo})")

        # Aguarda URL mudar para selecionar-numero e força reload completo
        # (React Router faz soft nav sem SSR; reload força hidratação correta)
        for _ in range(15):
            time.sleep(1)
            if "selecionar-numero" in self.driver.current_url:
                print("[portal] selecionar-numero detectado — forçando reload SSR")
                url_atual = self.driver.current_url
                self.driver.get(url_atual)
                time.sleep(3)
                return
        print("[portal] selecionar-numero não apareceu após 15s")

    def _ler_next_data(self) -> dict:
        """Retorna o conteúdo do __NEXT_DATA__ da página atual."""
        try:
            bruto = self.driver.execute_script(
                'return document.getElementById("__NEXT_DATA__")?.textContent || ""'
            )
            return json.loads(bruto) if bruto else {}
        except Exception as exc:
            print(f"[portal] Falha ao ler __NEXT_DATA__: {exc}")
            return {}

    def _extrair_contatos_selecionar_numero(self) -> list[dict]:
        """
        Extrai os contatos de /login/selecionar-numero no mesmo formato
        consumido pelo front hidratado.
        """
        next_data = self._ler_next_data()
        dados = (((next_data.get("props") or {}).get("pageProps") or {}).get("data") or {})
        contatos: list[dict] = []

        for item in dados.get("listaTelefone", []) or []:
            celular = item.get("celular") or ""
            if celular:
                contatos.append({
                    "tipo": "SMS",
                    "valor": celular,
                    "valorFormatado": celular,
                    "dadosOriginais": item,
                })

        dados_usuario = dados.get("dadosUsuario") or {}
        if (not contatos) and dados_usuario.get("celular"):
            contatos.append({
                "tipo": "SMS",
                "valor": dados_usuario["celular"],
                "valorFormatado": dados_usuario["celular"],
                "dadosOriginais": {
                    "celular": dados_usuario["celular"],
                    "isUsuario": True,
                },
            })

        for item in dados.get("listaEmail", []) or []:
            for endereco in item.get("endereco", []) or []:
                email = endereco.get("endereco") or ""
                if email:
                    contatos.append({
                        "tipo": "EMAIL",
                        "valor": email,
                        "valorFormatado": email,
                        "dadosOriginais": {
                            **item,
                            "endereco": [endereco],
                        },
                    })

        if not any(c["tipo"] == "EMAIL" for c in contatos) and dados_usuario.get("Email"):
            contatos.append({
                "tipo": "EMAIL",
                "valor": dados_usuario["Email"],
                "valorFormatado": dados_usuario["Email"],
                "dadosOriginais": {
                    "email": dados_usuario["Email"],
                    "isUsuario": True,
                },
            })

        return contatos

    def _escolher_contato_preferido(self, contatos: list[dict]) -> dict | None:
        """Prefere o e-mail da bbenergia; depois outros e-mails; depois SMS."""
        for contato in contatos:
            valor = (contato.get("valor") or "").lower()
            if contato.get("tipo") == "EMAIL" and "bben" in valor and "acaoengenharia.com.br" in valor:
                return contato
        for contato in contatos:
            valor = (contato.get("valor") or "").lower()
            if contato.get("tipo") == "EMAIL" and "bben" in valor and "acaoenge.com.br" in valor:
                return contato
        for contato in contatos:
            valor = (contato.get("valor") or "").lower()
            if contato.get("tipo") == "EMAIL" and "acaoengenharia.com.br" in valor:
                return contato
        for contato in contatos:
            valor = (contato.get("valor") or "").lower()
            if contato.get("tipo") == "EMAIL" and "acaoenge.com.br" in valor:
                return contato
        for contato in contatos:
            if contato.get("tipo") == "EMAIL":
                return contato
        return contatos[0] if contatos else None

    def _executar_runtime_js(self, script: str, *args):
        """Executa JS assíncrono no contexto da página com acesso ao webpack runtime."""
        bootstrap = """
            const done = arguments[arguments.length - 1];
            const userArgs = Array.from(arguments).slice(0, -1);
            var req;
            self.webpackChunk_N_E.push([[Math.random()], {}, function(r){ req = r; }]);
        """
        return self.driver.execute_async_script(bootstrap + script, *args)

    def _ler_ate_cookie(self) -> str:
        """Lê o token pré-autenticação (accessTokenEnergisa) dos cookies do browser."""
        try:
            cookies = self.driver.get_cookies()
            for c in cookies:
                if c.get("name") == "accessTokenEnergisa":
                    return c.get("value", "")
        except Exception:
            pass
        return ""

    def _enviar_codigo_contato_runtime(self, contato: dict, ate: str = "") -> dict:
        """
        Usa o axios do app para enviar o código de segurança do contato escolhido.

        Replica exatamente a chamada real:
          POST /api/autenticacao/CodigoSeguranca/EmailPorUC?codigoEmpresaWeb=X&cdc=Y&digitoVerificador=Z&posicao=N
          Headers: ispublic: 1
          Body:    {ate, udk, utk, refreshToken, retk}
        """
        if not ate:
            ate = self._ler_ate_cookie()
        return self._executar_runtime_js(
            """
            const contato = userArgs[0];
            const ate     = userArgs[1];
            const api     = req(3711).A;

            // Body exato que o portal envia
            const body = {
              ate:          ate,
              udk:          '',
              utk:          '',
              refreshToken: '',
              retk:         '',
            };
            // Header obrigatório descoberto via engenharia reversa
            const cfg = { headers: { ispublic: '1' } };

            let promise;
            if (contato.tipo === 'EMAIL') {
              const d   = contato.dadosOriginais || {};
              const end = ((d.endereco || [])[0] || {});
              const url = `/api/autenticacao/CodigoSeguranca/EmailPorUC`
                + `?codigoEmpresaWeb=${d.codigoEmpresaWeb}`
                + `&cdc=${d.cdc}`
                + `&digitoVerificador=${d.digitoVerificador}`
                + `&posicao=${end.posicaoDoEmail != null ? end.posicaoDoEmail : 0}`;
              promise = api.post(url, body, cfg);
            } else {
              const d   = contato.dadosOriginais || {};
              const url = `/api/autenticacao/CodigoSeguranca/ucCliente`
                + `?codigoEmpresaWeb=${d.codigoEmpresaWeb}`
                + `&cdc=${d.cdc}`
                + `&digitoVerificador=${d.digitoVerificador}`
                + `&posicao=${d.posicao != null ? d.posicao : 0}`
                + `&canal=SMS`;
              promise = api.post(url, body, cfg);
            }

            promise
              .then(resp => done({ok: true, status: resp.status, data: resp.data}))
              .catch(err => done({
                ok:             false,
                message:        String(err),
                responseStatus: err && err.response ? err.response.status : null,
                responseData:   err && err.response ? JSON.stringify(err.response.data) : null,
              }));
            """,
            contato,
            ate,
        )

    def _validar_otp_runtime(self, cnpj: str, codigo: str) -> dict:
        """Valida o OTP e grava os tokens em localStorage/cookies como o front faz."""
        return self._executar_runtime_js(
            """
            const cnpj = userArgs[0];
            const codigo = userArgs[1];
            const api = req(3711).A;
            const x = req(93945);
            const doc = x.Ww(cnpj);
            api.post(`/api/autenticacao/UsuarioClienteEnergisa/Autenticacao/PorCpfCnpj?doc=${doc}&codigoSegurancaRecebido=${codigo}`)
              .then(resp => {
                const infos = resp.data.infos || {};
                const now = Date.now();
                localStorage.setItem('utk', infos.utk || '');
                localStorage.setItem('udk', infos.udk || '');
                localStorage.setItem('refreshToken', infos.refreshToken || '');
                localStorage.setItem('utk-timestamp', String(now + 54e5));
                try {
                  if (x.Ay) x.Ay('accessTokenEnergisa');
                  if (x.uC) {
                    x.uC('rtk', infos.refreshToken, now + 10368e6);
                    x.uC('utk', infos.utk, now + 54e5);
                  }
                } catch (e) {}
                done({ok: true, status: resp.status, data: resp.data});
              })
              .catch(err => done({
                ok: false,
                message: String(err),
                responseStatus: err && err.response ? err.response.status : null,
                responseData: err && err.response ? err.response.data : null,
              }));
            """,
            cnpj,
            codigo,
        )

    def _listar_ucs_runtime(self, cnpj: str) -> dict:
        """Consulta as UCs após autenticação pelo mesmo client do front."""
        return self._executar_runtime_js(
            """
            const cnpj = userArgs[0];
            const api = req(3711).A;
            const x = req(93945);
            const doc = x.Ww(cnpj);
            api.get(`/api/usuarios/UnidadeConsumidora?doc=${doc}`)
              .then(resp => done({ok: true, status: resp.status, data: resp.data}))
              .catch(err => done({
                ok: false,
                message: String(err),
                responseStatus: err && err.response ? err.response.status : null,
                responseData: err && err.response ? err.response.data : null,
              }));
            """,
            cnpj,
        )

    def _preparar_uc_para_faturas_runtime(self, uc_item: dict, redirect: str = "") -> dict:
        """
        Replica a seleção de UC do front antes do redirect para login-faturas-ssr.
        """
        return self._executar_runtime_js(
            """
            const uc = userArgs[0];
            const redirect = userArgs[1] || '';
            const api = req(3711).A;
            const codigoEmpresaWeb = uc.codigoEmpresaWeb;
            const numeroUc = uc.numeroUc || uc.numeroCdc;
            const digito = uc.digitoVerificador;
            const grupoLeitura = uc.grupoLeitura || '';
            const maxAge = 10368e3;

            function setCookie(name, value, maxAgeSec) {
              document.cookie = `${name}=${value}; path=/; max-age=${maxAgeSec}; SameSite=Lax`;
            }

            setCookie('CodigoEmpresaWeb', String(codigoEmpresaWeb), maxAge);
            setCookie('NumeroUc', String(numeroUc), maxAge);
            setCookie('Digito', String(digito), maxAge);

            Promise.all([
              api.post(`/api/clientes/UnidadeConsumidora/Informacao?codigoEmpresaWeb=${codigoEmpresaWeb}&uc=${numeroUc}&digitoVerificador=${digito}`),
              api.get(`/api/AutoLeitura/VerificaAutoLeitura?CodigoEmpresaWeb=${codigoEmpresaWeb}&Cdc=${numeroUc}&DigitoVerificador=${digito}`),
            ]).then(([infoResp, autoResp]) => {
              done({
                ok: true,
                infoStatus: infoResp.status,
                autoStatus: autoResp.status,
                infoData: infoResp.data,
                autoData: autoResp.data,
                redirectUrl: `/login/login-faturas-ssr?codigoEmpresaWeb=${codigoEmpresaWeb}&numeroCdc=${numeroUc}&digitoVerificador=${digito}&GrupoLeitura=${grupoLeitura}&Redirect=${redirect}`,
              });
            }).catch(err => done({
              ok: false,
              message: String(err),
              responseStatus: err && err.response ? err.response.status : null,
              responseData: err && err.response ? err.response.data : null,
            }));
            """,
            uc_item,
            redirect,
        )

    def _router_push_runtime(self, rota: str) -> dict:
        """Navega por client-side routing usando o router do Next exposto em window.next."""
        return self._executar_runtime_js(
            """
            const rota = userArgs[0];
            const nextObj = window.next || {};
            const router = nextObj.router;
            if (!router || typeof router.push !== 'function') {
              done({ok: false, message: 'window.next.router.push indisponivel'});
              return;
            }
            Promise.resolve(router.push(rota))
              .then(() => {
                setTimeout(() => done({
                  ok: true,
                  route: rota,
                  currentUrl: window.location.href,
                  pathname: window.location.pathname,
                }), 1200);
              })
              .catch(err => done({
                ok: false,
                route: rota,
                message: String(err),
                currentUrl: window.location.href,
                pathname: window.location.pathname,
              }));
            """,
            rota,
        )

    def _abrir_faturas_runtime(self, redirect_url: str) -> dict:
        """
        Reproduz o fluxo do front:
        1. push para /login/login-faturas-ssr
        2. push client-side para /faturas
        """
        primeira = self._router_push_runtime(redirect_url)
        if not primeira.get("ok"):
            return {
                "ok": False,
                "step": "login-faturas-ssr",
                "firstNavigation": primeira,
            }

        segunda = self._router_push_runtime("/faturas")
        return {
            "ok": bool(segunda.get("ok")),
            "step": "faturas",
            "firstNavigation": primeira,
            "secondNavigation": segunda,
        }

    def _listar_faturas_runtime(self) -> dict:
        """
        Lê a lista de faturas da página /faturas.
        Tenta __NEXT_DATA__ e depois o Redux store.
        """
        return self._executar_runtime_js(
            """
            // Tenta __NEXT_DATA__ primeiro (SSR)
            try {
              var ndEl = document.getElementById('__NEXT_DATA__');
              if (ndEl && ndEl.textContent) {
                var nd = JSON.parse(ndEl.textContent);
                var pp = ((nd.props || {}).pageProps || {});
                var faturas = pp.faturas || (pp.dadosServerSide || {}).faturas || [];
                if (faturas && faturas.length > 0) {
                  done({ok: true, fonte: '__NEXT_DATA__', faturas: faturas});
                  return;
                }
              }
            } catch(e) {}

            // Tenta leitura via API (Next.js data endpoint)
            var api = req(3711).A;
            api.get('/api/clientes/Historico/Faturas')
              .then(resp => done({ok: true, fonte: 'api', data: resp.data}))
              .catch(err => {
                // Fallback: retorna fatura dummy para testar o endpoint
                done({
                  ok: false,
                  message: String(err),
                  responseStatus: err && err.response ? err.response.status : null,
                  hint: 'Use _next/data para obter faturas via SSR',
                });
              });
            """
        )

    def _listar_faturas_next_data(self, build_id: str = "i0Z-0Hw_AbLcxOOYrAgid") -> dict:
        """
        Busca a lista de faturas via endpoint Next.js SSR, usando cookies
        do driver atual (incluindo utk/rtk de autenticação).
        """
        import requests as py_requests

        # Extrai cookies do Selenium
        selenium_cookies = self.driver.get_cookies()
        jar = py_requests.cookies.RequestsCookieJar()
        for c in selenium_cookies:
            jar.set(c["name"], c["value"], domain=c.get("domain", ""), path=c.get("path", "/"))

        url = f"https://servicos.energisa.com.br/_next/data/{build_id}/faturas.json"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Referer": "https://servicos.energisa.com.br/faturas",
        }
        try:
            r = py_requests.get(url, headers=headers, cookies=jar, timeout=20)
            if r.status_code == 200:
                data = r.json()
                pp = data.get("pageProps", {})
                faturas = pp.get("faturas") or (pp.get("dadosServerSide") or {}).get("faturas") or []
                redirect = pp.get("redirect", "")
                return {"ok": True, "faturas": faturas, "redirect": redirect, "raw": pp}
            return {"ok": False, "status": r.status_code, "text": r.text[:500]}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _baixar_pdf_fatura_runtime(
        self,
        uc_data: dict,
        fatura: dict,
        autenticado: bool = True,
    ) -> dict:
        """
        Chama POST /api/clientes/SegundaVia/Download via runtime JS
        e retorna o PDF como base64.

        uc_data: { codigoEmpresaWeb, digitoVerificador, ... }
        fatura:  { cdcVinculado, digitoVerificadorCdc, anoReferencia,
                   mesReferencia, numeroFatura, ... }
        """
        return self._executar_runtime_js(
            """
            const ucData    = userArgs[0];
            const fatData   = userArgs[1];
            const autent    = userArgs[2];
            const api       = req(3711).A;

            const body = {
              codigoEmpresaWeb:   ucData.codigoEmpresaWeb,
              cdc:                fatData.cdcVinculado,
              digitoVerificadorCdc:
                fatData.digitoVerificadorCdc != null
                  ? fatData.digitoVerificadorCdc
                  : ucData.digitoVerificador,
              ano:    fatData.anoReferencia,
              mes:    fatData.mesReferencia,
              cdcRed: null,
              fatura: fatData.numeroFatura,
            };

            const endpoint = '/api/clientes/' + (autent ? 'SegundaVia' : 'Boleto') + '/Download';

            api.post(endpoint, body, {
              responseType: 'arraybuffer',
              headers: { Accept: 'application/pdf' },
            })
            .then(resp => {
              const arr  = new Uint8Array(resp.data);
              let binary = '';
              for (let i = 0; i < arr.byteLength; i++) {
                binary += String.fromCharCode(arr[i]);
              }
              done({
                ok:      true,
                base64:  btoa(binary),
                size:    arr.byteLength,
                endpoint: endpoint,
              });
            })
            .catch(err => done({
              ok:             false,
              message:        String(err),
              responseStatus: err && err.response ? err.response.status : null,
              responseData:   err && err.response ? JSON.stringify(err.response.data) : null,
              endpoint:       endpoint,
            }));
            """,
            uc_data,
            fatura,
            autenticado,
        )

    def _salvar_pdf_base64(
        self,
        base64_str: str,
        nome: str,
    ) -> Path:
        """Decodifica base64 e salva PDF em self.download_dir."""
        import base64 as b64
        dados = b64.b64decode(base64_str)
        dest = self.download_dir / nome
        dest.write_bytes(dados)
        return dest

    def baixar_faturas_uc_completo(
        self,
        cnpj: str,
        uc_item: dict,
        meses: list[dict] | None = None,
    ) -> list[Path]:
        """
        Fluxo novo via runtime:
          1. Login CNPJ + OTP
          2. Prepara UC → login-faturas-ssr
          3. Navega para /faturas (SSR popula os dados)
          4. Lê lista de faturas via Next.js data endpoint
          5. Para cada fatura: POST /api/clientes/SegundaVia/Download → salva PDF

        uc_item: objeto UC retornado por /api/usuarios/UnidadeConsumidora
        meses:   lista opcional de {'mes': 6, 'ano': 2026} para filtrar
        """
        resultados: list[Path] = []
        uid_antes = uid_atual()

        # --- Login ---
        self._etapa_login_cnpj_v2(cnpj)
        if not self._etapa_selecionar_contato_v2():
            print(f"[portal] Sem contato bbenergia para CNPJ={cnpj}")
            return resultados

        try:
            self._etapa_preencher_otp(uid_antes)
        except TimeoutError:
            print(f"[portal] OTP não chegou para CNPJ={cnpj}")
            return resultados

        # --- Preparar UC e navegar para /faturas ---
        prep = self._preparar_uc_para_faturas_runtime(uc_item)
        if not prep.get("ok"):
            print(f"[portal] Falha ao preparar UC: {prep}")
            return resultados

        redirect_url = prep.get("redirectUrl", "")
        nav = self._abrir_faturas_runtime(redirect_url)
        print(f"[portal] Navegação /faturas: {nav.get('step')} ok={nav.get('ok')}")
        time.sleep(3)  # SSR precisa processar

        # --- Lê lista de faturas ---
        faturas_result = self._listar_faturas_next_data()
        if not faturas_result.get("ok") or not faturas_result.get("faturas"):
            print(f"[portal] Faturas não carregadas: {faturas_result}")
            return resultados

        faturas = faturas_result["faturas"]
        print(f"[portal] {len(faturas)} fatura(s) encontrada(s)")

        # Filtra por meses se especificado
        if meses:
            filtro = {(m["ano"], m["mes"]) for m in meses}
            faturas = [
                f for f in faturas
                if (f.get("anoReferencia"), f.get("mesReferencia")) in filtro
            ]
            print(f"[portal] Após filtro de meses: {len(faturas)} fatura(s)")

        # --- Baixa cada fatura ---
        for fat in faturas:
            numero   = fat.get("numeroFatura", "?")
            ano      = fat.get("anoReferencia", "?")
            mes      = fat.get("mesReferencia", "?")
            nome_pdf = f"energisa_{cnpj}_{ano}_{str(mes).zfill(2)}_{numero}.pdf"

            print(f"[portal] Baixando fatura {numero} ({mes}/{ano})...")
            resultado_dl = self._baixar_pdf_fatura_runtime(
                uc_data=uc_item,
                fatura=fat,
                autenticado=True,
            )

            if resultado_dl.get("ok") and resultado_dl.get("base64"):
                pdf_path = self._salvar_pdf_base64(resultado_dl["base64"], nome_pdf)
                print(f"[portal] PDF salvo: {pdf_path} ({resultado_dl.get('size', 0):,} bytes)")
                resultados.append(pdf_path)
            else:
                print(f"[portal] FALHA no download fatura {numero}: {resultado_dl}")

        return resultados

    def _etapa_login_cnpj_v2(self, cnpj: str, forcar_reload_ssr: bool = False) -> None:
        # Navega para login via JS (preserva cookies do fluxo atual)
        self._navegar(URL_LOGIN, espera=3)
        campo = _wait(self.driver).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '[data-cy="input-cpf-cnpj"]'))
        )
        self.limpar_logs_performance()
        self.capturar_console_recente()
        cnpj_limpo = re.sub(r"\D", "", cnpj)
        campo.click()
        time.sleep(0.3)
        try:
            campo.send_keys(Keys.CONTROL, "a")
            campo.send_keys(Keys.DELETE)
            campo.send_keys(cnpj_limpo)
        except Exception:
            pass
        valor_atual = (campo.get_attribute("value") or "").strip()
        if re.sub(r"\D", "", valor_atual) != cnpj_limpo:
            self._preencher_input_react(campo, cnpj_limpo)
        self.driver.execute_script(
            "arguments[0].dispatchEvent(new Event('blur', { bubbles: true }));",
            campo,
        )
        time.sleep(1.5)

        btn = None
        for sel in ('[data-cy="submit-cnpj-cpf"]', '[data-cy="btn-submit"]', 'button[type="submit"]'):
            els = self.driver.find_elements(By.CSS_SELECTOR, sel)
            if els:
                btn = els[0]
                break
        if not btn:
            for b in self.driver.find_elements(By.TAG_NAME, "button"):
                if "Entrar" in (b.text or ""):
                    btn = b
                    break
        if btn:
            from selenium.webdriver.common.action_chains import ActionChains
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            time.sleep(0.3)
            try:
                self.driver.execute_script("arguments[0].click();", btn)
            except Exception:
                ActionChains(self.driver).move_to_element(btn).pause(0.2).click().perform()
            print(f"[portal] Entrar clicado (CNPJ={cnpj_limpo})")

        for _ in range(15):
            time.sleep(1)
            if "selecionar-numero" in self.driver.current_url:
                # Sempre faz reload SSR — sem reload a página fica sem dados
                print("[portal] selecionar-numero detectado — forçando reload SSR")
                url_atual = self.driver.current_url
                self.driver.get(url_atual)
                time.sleep(3)
                return

        print(f"[portal] URL apÃ³s submit: {self.driver.current_url}")
        try:
            print(f"[portal] Valor atual do campo: {campo.get_attribute('value')!r}")
        except Exception:
            pass
        for item in self.capturar_console_recente()[:10]:
            print(f"[portal][browser] {item.get('level')}: {item.get('message')}")
        for ev in self.capturar_rede_recente(filtro_url='', incluir_next=True)[:20]:
            print(f"[portal][rede] {ev.get('tipo')} {ev.get('method') or ev.get('status')} {ev.get('url')}")
        print("[portal] selecionar-numero nÃ£o apareceu apÃ³s 15s")

    def _etapa_selecionar_contato(self) -> bool:
        try:
            _wait(self.driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "button p"))
            )
        except Exception:
            pass
        time.sleep(2)

        for dominio in ("acaoengenharia.com.br", "acaoenge.com.br"):
            try:
                xpath = f"//button[.//p[contains(text(), '{dominio}')]]"
                btn = self.driver.find_element(By.XPATH, xpath)
                btn.click()
                print(f"[portal] Contato selecionado: *@{dominio}")
                return True
            except Exception:
                continue

        # Debug: mostra o que está na tela
        s = _soup(self.driver)
        btns = s.find_all("button")
        print(f"[portal] {len(btns)} botão(ões) na tela. Textos:")
        for b in btns[:10]:
            print(f"  -> {b.get_text(strip=True)[:100]!r}")
        print("[portal] Contato bbenergia não encontrado na lista.")
        return False

    def _etapa_selecionar_contato_v2(self) -> bool:
        time.sleep(2)
        contatos = self._extrair_contatos_selecionar_numero()
        contato = self._escolher_contato_preferido(contatos)
        if contato:
            alvo = contato.get("valorFormatado") or contato.get("valor") or ""
            tipo = contato.get("tipo") or "?"
            for xpath in (
                f"//button[contains(., '{alvo}')]",
                f"//button[.//p[contains(text(), '{alvo}')]]",
                "//button[contains(., 'bben')]",
                "//button[contains(., 'acaoengenharia.com.br')]",
                "//button[contains(., 'acaoenge.com.br')]",
            ):
                try:
                    btn = _wait(self.driver, 10).until(
                        EC.element_to_be_clickable((By.XPATH, xpath))
                    )
                    self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                    time.sleep(0.2)
                    try:
                        btn.click()
                    except Exception:
                        self.driver.execute_script("arguments[0].click();", btn)
                    print(f"[portal] Contato selecionado via tela: {tipo} {alvo}")
                    return True
                except Exception:
                    continue
            print(f"[portal] Contato preferido identificado no SSR, mas não apareceu clicável: {tipo} {alvo}")

        s = _soup(self.driver)
        btns = s.find_all("button")
        print(f"[portal] {len(btns)} botão(ões) na tela. Textos:")
        for b in btns[:10]:
            print(f"  -> {b.get_text(strip=True)[:100]!r}")
        print("[portal] Contato bbenergia não encontrado na lista.")
        return False

    def _etapa_preencher_otp(self, uid_antes: str) -> None:
        codigo = aguardar_otp(timeout=TIMEOUT_OTP, uid_minimo=uid_antes)
        print(f"[portal] OTP recebido: {codigo}")
        time.sleep(0.5)

        inputs_individuais = self.driver.find_elements(By.CSS_SELECTOR, "input[maxlength='1']")
        if len(inputs_individuais) >= 4:
            for campo, digito in zip(inputs_individuais[:4], codigo):
                campo.clear()
                campo.send_keys(digito)
                time.sleep(0.1)
        else:
            for sel in ("input[type='number']", "input[type='tel']", "input[inputmode='numeric']", "input"):
                els = self.driver.find_elements(By.CSS_SELECTOR, sel)
                if els:
                    els[0].clear()
                    els[0].send_keys(codigo)
                    break
            for b in self.driver.find_elements(By.TAG_NAME, "button"):
                if b.text.strip() and b.is_enabled():
                    b.click()
                    break

    def _etapa_selecionar_uc(self, instalacao: str) -> bool:
        try:
            _wait(self.driver, 8).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//span[contains(text(), 'Número da UC')]")
                )
            )
        except Exception:
            print("[portal] Tela de seleção de UC não apareceu (UC única).")
            return True
        time.sleep(1)

        # Modal "Escolha um imóvel para acessar" (aberto via 'trocar imóvel'):
        # cada UC é um <label data-testid="card-uc"> com um <span> "Número da
        # UC: <instalacao>" dentro — clicar no label seleciona o rádio e o
        # próprio app navega/recarrega com a UC escolhida.
        cards = self.driver.find_elements(By.CSS_SELECTOR, '[data-testid="card-uc"]')
        if cards:
            alvo = None
            for card in cards:
                try:
                    if instalacao in card.text:
                        alvo = card
                        break
                except Exception:
                    continue
            if alvo is None:
                alvo = cards[0]
                print(f"[portal] UC {instalacao} não encontrada no modal — selecionando primeira.")
            else:
                print(f"[portal] UC {instalacao} selecionada (modal de imóveis).")
            try:
                alvo.click()
            except Exception:
                self.driver.execute_script("arguments[0].click();", alvo)
            time.sleep(1.5)
            return True

        xpath = f"//button[.//span[normalize-space()='{instalacao}']]"
        try:
            self.driver.find_element(By.XPATH, xpath).click()
            print(f"[portal] UC {instalacao} selecionada.")
            return True
        except Exception:
            pass
        try:
            self.driver.find_element(
                By.XPATH, "//button[.//span[contains(text(), 'Número da UC')]]"
            ).click()
            print(f"[portal] UC {instalacao} não encontrada — selecionando primeira.")
            return True
        except Exception as exc:
            print(f"[portal] Falha ao selecionar UC: {exc}")
            return False

    def _fechar_modais(self) -> None:
        for xpath in (
            "//button[.//img[contains(@src, 'icon_close_white')]]",
            "//*[contains(text(), 'Não quero ativar agora')]",
            "//*[contains(text(), 'Não quero ativar agora.')]",
        ):
            try:
                btn = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, xpath))
                )
                btn.click()
                print("[portal] Modal PIX fechado.")
                time.sleep(0.8)
                break
            except Exception:
                continue

        try:
            self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.5)
        except Exception:
            pass

        try:
            for dialog in self.driver.find_elements(
                By.CSS_SELECTOR, "[role='dialog'], [role='alertdialog']"
            ):
                btns = [b for b in dialog.find_elements(By.TAG_NAME, "button")
                        if b.is_displayed() and b.is_enabled()]
                if len(btns) == 1:
                    btns[0].click()
                    print("[portal] Modal caixinha fechado.")
                    time.sleep(0.5)
                    break
        except Exception:
            pass

    def _etapa_home(self) -> None:
        try:
            _wait(self.driver, 30).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "button.action-button--download")
                )
            )
        except Exception:
            time.sleep(3)
        self._fechar_modais()

    def _localizar_botao_baixar_2a_via(self):
        """
        A página renderiza mais de um botão com a classe
        'action-button--download' (variações responsivas mobile/desktop) —
        apenas um deles fica visível por vez. Retorna o visível.
        """
        candidatos = _wait(self.driver).until(
            EC.presence_of_all_elements_located(
                (By.CSS_SELECTOR, "button.action-button--download")
            )
        )
        for candidato in candidatos:
            if candidato.is_displayed() and candidato.is_enabled():
                return candidato
        return None

    def _etapa_baixar_2a_via(self) -> Path | None:
        arquivos_antes = set(self.download_dir.glob("*.pdf"))
        try:
            btn = self._localizar_botao_baixar_2a_via()
            if not btn:
                print("[portal] Botão 'Baixar 2ª via' visível não encontrado.")
                return None
            btn.click()
        except Exception as exc:
            print(f"[portal] Erro ao clicar Baixar 2ª via: {exc}")
            return None

        prazo = time.time() + TIMEOUT_DOWNLOAD
        while time.time() < prazo:
            novos = {p for p in set(self.download_dir.glob("*.pdf")) - arquivos_antes
                     if p.suffix != ".crdownload"}
            if novos:
                pdf = sorted(novos, key=lambda p: p.stat().st_mtime)[-1]
                ant = -1
                for _ in range(6):
                    time.sleep(0.5)
                    tam = pdf.stat().st_size
                    if tam == ant and tam > 0:
                        return pdf
                    ant = tam
                return pdf
            time.sleep(1)

        print("[portal] Timeout aguardando download.")
        return None

    def capturar_rede_baixar_2a_via(self, janela_segundos: int = 12) -> list[dict]:
        """
        Clica em "Baixar 2a via" e retorna os eventos de rede capturados logo
        apos o clique.
        """
        self.limpar_logs_performance()
        try:
            self.driver.execute_cdp_cmd("Network.enable", {})
        except Exception:
            pass

        try:
            btn = self._localizar_botao_baixar_2a_via()
            if not btn:
                print("[portal] Botão 'Baixar 2ª via' visível não encontrado para captura.")
                return []
            btn.click()
            print("[portal] Clique de captura na 2a via executado.")
        except Exception as exc:
            print(f"[portal] Erro ao clicar Baixar 2a via para captura: {exc}")
            return []

        prazo = time.time() + janela_segundos
        eventos: list[dict] = []
        while time.time() < prazo:
            eventos.extend(self.capturar_rede_recente())
            time.sleep(0.7)

        vistos = set()
        unicos = []
        for ev in eventos:
            chave = (
                ev.get("tipo"),
                ev.get("method", ev.get("status")),
                ev.get("url"),
            )
            if chave in vistos:
                continue
            vistos.add(chave)
            unicos.append(ev)
        return unicos

    def _etapa_trocar_uc(self) -> bool:
        # A página renderiza variações mobile/desktop do mesmo texto — apenas
        # uma fica visível por vez, então não dá pra confiar no primeiro
        # elemento do DOM (mesmo problema do botão de download).
        for xpath in (
            "//*[contains(text(), 'meus imóveis')]",
            "//*[contains(text(), 'meus imoveis')]",
            "//*[contains(text(), 'Trocar')]",
            "//*[contains(text(), 'Ver os meus')]",
        ):
            try:
                candidatos = WebDriverWait(self.driver, 5).until(
                    EC.presence_of_all_elements_located((By.XPATH, xpath))
                )
            except Exception:
                continue
            for candidato in candidatos:
                if not (candidato.is_displayed() and candidato.is_enabled()):
                    continue
                try:
                    candidato.click()
                except Exception:
                    try:
                        self.driver.execute_script("arguments[0].click();", candidato)
                    except Exception:
                        continue
                print("[portal] Clicou em 'trocar imóvel'.")
                time.sleep(1.5)
                return True
        print("[portal] Botão de troca de UC não encontrado.")
        return False

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def baixar_fatura(self, cnpj: str, uc: str | None = None) -> Path | None:
        """Fluxo completo para uma UC. Para múltiplas UCs use baixar_faturas_cnpj()."""
        print(f"\n[portal] Iniciando CNPJ={cnpj} UC={uc}")
        uid_antes = uid_atual()
        self._etapa_login_cnpj_v2(cnpj)
        if not self._etapa_selecionar_contato_v2():
            return None
        try:
            self._etapa_preencher_otp(uid_antes)
        except TimeoutError:
            print(f"[portal] OTP não chegou para CNPJ={cnpj}")
            return None
        if uc:
            self._etapa_selecionar_uc(uc)
        self._etapa_home()
        return self._etapa_baixar_2a_via()

    def baixar_faturas_cnpj(self, cnpj: str, ucs: list[str]) -> dict[str, Path | None]:
        """
        Fluxo completo para um CNPJ com uma ou múltiplas UCs.
        Login uma vez (CNPJ + OTP) → para cada UC: selecionar → baixar → trocar.
        """
        print(f"\n[portal] CNPJ={cnpj} — {len(ucs)} UC(s): {ucs}")
        resultado: dict[str, Path | None] = {}
        uid_antes = uid_atual()

        self._etapa_login_cnpj_v2(cnpj)
        if not self._etapa_selecionar_contato_v2():
            print(f"[portal] Contato bbenergia não encontrado para CNPJ={cnpj}")
            return {uc: None for uc in ucs}

        try:
            self._etapa_preencher_otp(uid_antes)
        except TimeoutError:
            print(f"[portal] OTP não chegou para CNPJ={cnpj}")
            return {uc: None for uc in ucs}

        for i, instalacao in enumerate(ucs):
            self._etapa_selecionar_uc(instalacao)
            self._etapa_home()
            pdf = self._etapa_baixar_2a_via()
            resultado[instalacao] = pdf
            print(f"[portal] [{i+1}/{len(ucs)}] {instalacao} → "
                  f"{pdf.name if pdf else 'FALHA'}")

            if i < len(ucs) - 1:
                if not self._etapa_trocar_uc():
                    for restante in ucs[i + 1:]:
                        resultado[restante] = None
                    break
                time.sleep(1)

        return resultado

    def inspecionar_pagina(self, url: str | None = None) -> BeautifulSoup:
        if url:
            self._navegar(url)
            time.sleep(2)
        return _soup(self.driver)
