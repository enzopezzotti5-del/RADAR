"""
Regressao para o bug de baixadas=0: listar_faturas_na_tela() nao pode mais
parar no primeiro painel quando ele estiver VENCIDA/ja-processado, deixando
faturas elegiveis em paineis seguintes sem serem vistas.
"""
from core.downloaders.neoenergia import worker_coelba


class _FakePainel:
    def __init__(self, text: str):
        self.text = text

    def find_element(self, *_args, **_kwargs):
        # Forca o fallback de classificacao por texto bruto (linhas ~1503-1515),
        # que e o caminho exercitado sem precisar simular a estrutura DOM real
        # do bloco de situacao.
        raise Exception("no situacao span in fake DOM")


class _FakeWait:
    """Substitui WebDriverWait(driver, N).until(cond) por um retorno fixo."""

    def __init__(self, panels):
        self._panels = panels

    def __call__(self, _driver, _timeout):
        return self

    def until(self, _condition):
        if not self._panels:
            raise TimeoutError("no panels")
        return self._panels


def _patch_common(monkeypatch, panels):
    monkeypatch.setattr(worker_coelba, "WebDriverWait", _FakeWait(panels))
    monkeypatch.setattr(worker_coelba, "_expandir_painel", lambda driver, painel: None)
    monkeypatch.setattr(worker_coelba, "save_screenshot", lambda driver, name: None)


def test_parar_apos_n_zero_reads_every_panel_even_when_first_is_vencida(monkeypatch):
    panels = [
        _FakePainel("Fatura 01/2026 vencida R$ 100,00"),
        _FakePainel("Fatura 02/2026 vencida R$ 100,00"),
        _FakePainel("Fatura 03/2026 vencida R$ 100,00"),
        _FakePainel("Fatura 04/2026 vencida R$ 100,00"),
        _FakePainel("Fatura 05/2026 a vencer R$ 100,00"),
    ]
    _patch_common(monkeypatch, panels)

    faturas = worker_coelba.listar_faturas_na_tela(driver=object(), parar_apos_n=0)

    assert len(faturas) == 5
    assert faturas[-1].situacao == "A VENCER"
    assert [f.situacao for f in faturas[:4]] == ["VENCIDA"] * 4


def test_parar_apos_n_one_still_stops_early_when_explicitly_requested(monkeypatch):
    """parar_apos_n continua existindo e funcionando para quem o pedir
    explicitamente (ex.: um modo futuro de 'so a ultima fatura') — o que
    mudou foi o worker parar de usa-lo por padrao no fluxo diario."""
    panels = [
        _FakePainel("Fatura 01/2026 vencida R$ 100,00"),
        _FakePainel("Fatura 05/2026 a vencer R$ 100,00"),
    ]
    _patch_common(monkeypatch, panels)

    faturas = worker_coelba.listar_faturas_na_tela(driver=object(), parar_apos_n=1)

    assert len(faturas) == 1
    assert faturas[0].situacao == "VENCIDA"


def test_selecionar_faturas_pendentes_no_longer_passes_early_exit_by_default(monkeypatch):
    """A chamada dentro de selecionar_faturas_pendentes() e o ponto real do
    bug (era ali que _parar=1 acontecia no fluxo diario padrao). Verifica
    diretamente que listar_faturas_na_tela e chamado com parar_apos_n=0."""
    captured = {}

    def fake_listar(_driver, parar_apos_n=0):
        captured["parar_apos_n"] = parar_apos_n
        return []

    monkeypatch.setattr(worker_coelba, "listar_faturas_na_tela", fake_listar)

    worker_coelba.selecionar_faturas_pendentes(
        driver=object(), ja_baixados=set(), cnpj="00000000000191", instalacao="123"
    )

    assert captured["parar_apos_n"] == 0
