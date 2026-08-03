from __future__ import annotations

import pytest

from core.downloaders.cpfl.cpfl_guard import (
    DEFAULT_MAX_UCS_PER_TITULAR,
    ExpansaoUcsError,
    resolver_max_ucs_por_titular,
    validar_expansao_ucs,
)


def test_cpfl_guard_preserva_patamar_operacional_observado():
    validar_expansao_ucs(
        titular_id="normal",
        titular_texto="Titular normal",
        total_ucs=184,
        max_ucs=DEFAULT_MAX_UCS_PER_TITULAR,
    )


def test_cpfl_guard_bloqueia_expansao_e_identifica_origem():
    with pytest.raises(ExpansaoUcsError) as exc_info:
        validar_expansao_ucs(
            titular_id="0060000999",
            titular_texto="Titular expandido",
            total_ucs=603,
            max_ucs=DEFAULT_MAX_UCS_PER_TITULAR,
        )
    mensagem = str(exc_info.value)
    assert "EXPANSAO_UCS_BLOQUEADA" in mensagem
    assert "0060000999" in mensagem
    assert "total_ucs=603" in mensagem
    assert "limite=368" in mensagem


def test_cpfl_guard_e_configuravel_por_ambiente(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CPFL_MAX_UCS_PER_TITULAR", "700")
    assert resolver_max_ucs_por_titular() == 700
    assert resolver_max_ucs_por_titular(500) == 500


def test_cpfl_guard_rejeita_configuracao_invalida(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CPFL_MAX_UCS_PER_TITULAR", "nao-numerico")
    with pytest.raises(ValueError, match="inteiro positivo"):
        resolver_max_ucs_por_titular()
