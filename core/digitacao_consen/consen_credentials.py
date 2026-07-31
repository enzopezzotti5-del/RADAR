from __future__ import annotations

import os


DEFAULT_CONSEN_USUARIO = "Robo Digitador"
DEFAULT_CONSEN_SENHA = "Acao2026"


def resolver_credenciais_consen(
    usuario: str | None = None,
    senha: str | None = None,
) -> tuple[str, str]:
    usuario_final = str(
        usuario if usuario is not None else os.environ.get("CONSEN_USUARIO", DEFAULT_CONSEN_USUARIO)
    ).strip() or DEFAULT_CONSEN_USUARIO

    senha_final = str(
        senha if senha is not None else os.environ.get("CONSEN_SENHA", DEFAULT_CONSEN_SENHA)
    ).strip() or DEFAULT_CONSEN_SENHA

    return usuario_final, senha_final


def injetar_credenciais_consen_no_env(env: dict[str, str]) -> dict[str, str]:
    usuario, senha = resolver_credenciais_consen(
        env.get("CONSEN_USUARIO"),
        env.get("CONSEN_SENHA"),
    )
    env["CONSEN_USUARIO"] = usuario
    env["CONSEN_SENHA"] = senha
    return env
