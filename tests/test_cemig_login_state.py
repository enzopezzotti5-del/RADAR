from pathlib import Path
import sys

sys.path.insert(0, str(Path.cwd()))
from core.downloaders.cemig import cemig  # noqa: E402


class _Element:
    def __init__(self, text="", displayed=True):
        self.text, self._displayed = text, displayed
    def is_displayed(self):
        return self._displayed


class _Driver:
    def __init__(self, url, messages=()):
        self.current_url, self.messages = url, messages
    def find_elements(self, *_):
        return list(self.messages)


def test_click_without_navigation_is_not_login_ok():
    assert cemig._resultado_login(_Driver("https://atende.cemig.com.br/Login/Index")) is None


def test_captcha_rejected_is_explicit():
    assert cemig._resultado_login(_Driver("https://atende.cemig.com.br/Login/Index", [_Element("CAPTCHA inválido")])) == "CEMIG_CAPTCHA_NAO_ACEITO"


def test_login_rejected_is_explicit():
    assert cemig._resultado_login(_Driver("https://atende.cemig.com.br/Login/Index", [_Element("Usuário ou senha inválidos")])) == "LOGIN_REJEITADO"


def test_authenticated_routes_are_login_ok():
    assert cemig._resultado_login(_Driver("https://atende.cemig.com.br/Home/Index")) == "LOGIN_OK"


def test_credential_guard_detects_cleared_fields(monkeypatch):
    class Field:
        def get_attribute(self, _): return ""
    monkeypatch.setattr(cemig, "log", lambda *args, **kwargs: None)
    assert not cemig._credenciais_preservadas(Field(), Field(), "user", "secret")
