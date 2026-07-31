#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
consen_api.py — API de alto nível para operar o CONSEN via Selenium.

Dois modos de busca:

  1. Por carimbo (BB_XXXXXXX):
        with ConsenSession.abrir() as s:
            s.buscar_carimbo("BB_2008391")
            s.editar_campos({"fatICMS": "12,00"})
            s.salvar_e_auditar()

  2. Por instalação + mês de referência:
        with ConsenSession.abrir() as s:
            s.buscar_por_instalacao("000116994011", mes="05", ano="2026")
            s.editar_campos({"fatICMS": "12,00"})
            s.salvar_e_auditar()

  Os dois modos produzem o mesmo estado após a busca: formulário aberto,
  pronto para editar_campos → salvar_e_auditar.

Outras operações disponíveis:
    s.obter_campos()               → {campo_id: valor_atual}
    s.obter_campo("fatICMS")       → "12,00"
    s.preencher_campo("fatICMS", "12,00")
    s.alterar_instalacao("000116994011")
    s.listar_meses_instalacao("000116994011")  → ["03/2026", "04/2026"]
    s.snapshot(saida_dir, carimbo)
    s.registrar(execucao_csv, carimbo, status)

Uso em script de correção (padrão recomendado):
    with ConsenSession.abrir() as s:
        for carimbo in carimbos:
            s.buscar_carimbo(carimbo, CAMPOS_CRITICOS)
            resultado = s.editar_campos(_build_correcoes(ocr), ORDEM_CAMPOS)
            if salvar:
                s.salvar_e_auditar()
"""
from __future__ import annotations

import datetime as _dt
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Garante que tanto ENERGIA/ quanto ENERGIA/core/ estejam no path,
# independente de como o módulo for importado (como script ou como pacote).
_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent
_ROOT = _CORE.parent
for _p in (_ROOT, _CORE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# Delega às implementações existentes — não duplica.
try:
    from digitacao_consen.correcao_fluxo_base import (
        abrir_driver_logado,
        abrir_tela_edicao_carimbo,
        aplicar_correcoes,
        carregar_fatura_por_carimbo,
        coletar_campos_visiveis,
        localizar_input_exato,
        normalizar_carimbo,
        registrar_execucao,
        salvar_auditar_e_avancar,
        salvar_snapshot,
        valor_vazio,
        LOGIN_URL,
    )
    from digitacao_consen.digitacao_consen_enel import (
        _aguardar_sem_spinner,
        abrir_tela_instalacao,
        aguardar_tela_instalacao_pronta,
        clicar_botao_carregar_instalacao,
        clicar_botao_proxima_fatura,
        clicar_botao_salvar,
        fechar_driver_seguro,
        formatar_ddmmyyyy,
        formatar_valor_para_campo,
        localizar_tabela_faturas,
        log,
        obter_datas_referencia_tabela,
        abrir_referencia_existente_para_edicao,
        parse_data_ddmmyyyy,
        preencher_elemento_html,
        preencher_input_texto,
        warn,
    )
except ModuleNotFoundError:
    from correcao_fluxo_base import (  # type: ignore
        abrir_driver_logado,
        abrir_tela_edicao_carimbo,
        aplicar_correcoes,
        carregar_fatura_por_carimbo,
        coletar_campos_visiveis,
        localizar_input_exato,
        normalizar_carimbo,
        registrar_execucao,
        salvar_auditar_e_avancar,
        salvar_snapshot,
        valor_vazio,
        LOGIN_URL,
    )
    from digitacao_consen_enel import (  # type: ignore
        _aguardar_sem_spinner,
        abrir_tela_instalacao,
        aguardar_tela_instalacao_pronta,
        clicar_botao_carregar_instalacao,
        clicar_botao_proxima_fatura,
        clicar_botao_salvar,
        fechar_driver_seguro,
        formatar_ddmmyyyy,
        formatar_valor_para_campo,
        localizar_tabela_faturas,
        log,
        obter_datas_referencia_tabela,
        abrir_referencia_existente_para_edicao,
        parse_data_ddmmyyyy,
        preencher_elemento_html,
        preencher_input_texto,
        warn,
    )

import os as _os

_BASE_URL = LOGIN_URL.rsplit("login.php", 1)[0]
_EDIT_URL_DEFAULT = _os.environ.get(
    "CONSEN_EDITA_FATURA_CARIMBO_URL",
    f"{_BASE_URL}index.php#bpg/gestao/fatura/editaFaturaCarimbo.php",
)

# Campos que representam o número de instalação/UC no formulário CONSEN.
_CAMPOS_INSTALACAO = (
    "fatInstalacao",
    "instalacao",
    "numInstalacao",
    "uc",
    "fatUC",
)


# ── Tipos de retorno ──────────────────────────────────────────────────────────

@dataclass
class CampoAlterado:
    campo: str
    valor_anterior: str
    valor_novo: str
    confirmado: bool


@dataclass
class ResultadoEdicao:
    carimbo: str
    alterados: list[CampoAlterado] = field(default_factory=list)
    sem_mudanca: int = 0
    erros: list[str] = field(default_factory=list)
    salvo: bool = False

    @property
    def n_alterados(self) -> int:
        return sum(1 for c in self.alterados if c.confirmado)

    @property
    def ok(self) -> bool:
        return not self.erros

    def resumo(self) -> str:
        partes = [f"BB_{normalizar_carimbo(self.carimbo)}:"]
        partes.append(f"{self.n_alterados} campo(s) alterado(s)")
        if self.sem_mudanca:
            partes.append(f"{self.sem_mudanca} já correto(s)")
        if self.erros:
            partes.append(f"{len(self.erros)} erro(s): {'; '.join(self.erros[:2])}")
        if self.salvo:
            partes.append("[SALVO]")
        return " | ".join(partes)


# ── Sessão principal ──────────────────────────────────────────────────────────

class ConsenSession:
    """
    Sessão ativa no CONSEN.

    Use como context manager para garantir que o driver seja fechado:

        with ConsenSession.abrir() as s:
            s.buscar_carimbo("BB_2008391")
            s.editar_campos({"fatICMS": "12,00"})
            s.salvar_e_auditar()
    """

    def __init__(self, driver, wait, edit_url: str = _EDIT_URL_DEFAULT) -> None:
        self._driver = driver
        self._wait = wait
        self._edit_url = edit_url
        self._carimbo_atual: str = ""

    # ── Ciclo de vida ─────────────────────────────────────────────────────────

    @classmethod
    def abrir(cls, edit_url: str = _EDIT_URL_DEFAULT) -> "ConsenSession":
        """Inicia o Chrome, faz login e retorna a sessão pronta para uso."""
        driver, wait = abrir_driver_logado()
        session = cls(driver, wait, edit_url)
        abrir_tela_edicao_carimbo(driver, wait, edit_url)
        return session

    def fechar(self) -> None:
        """Fecha o navegador com segurança."""
        try:
            fechar_driver_seguro(self._driver)
        except Exception:
            pass

    def __enter__(self) -> "ConsenSession":
        return self

    def __exit__(self, *_) -> None:
        self.fechar()

    # ── Navegação ─────────────────────────────────────────────────────────────

    def _descartar_mudancas(self) -> None:
        """Suprime o dialog 'deseja sair sem salvar?' antes de navegar."""
        try:
            self._driver.execute_script("window.onbeforeunload = null;")
        except Exception:
            pass
        # Aceita alerta caso já tenha aparecido
        try:
            self._driver.switch_to.alert.accept()
        except Exception:
            pass

    def buscar_carimbo(
        self,
        carimbo: str,
        aguardar_campos: tuple[str, ...] = (),
    ) -> None:
        """
        Carrega a fatura pelo carimbo na tela de edição.

        Parâmetros:
            carimbo         — número ou "BB_XXXXXXX" (normalizado automaticamente)
            aguardar_campos — IDs de campos que devem estar presentes para confirmar
                              que a página carregou (ex: "fatValorNFiscal")
        """
        self._carimbo_atual = normalizar_carimbo(carimbo)
        self._descartar_mudancas()
        abrir_tela_edicao_carimbo(self._driver, self._wait, self._edit_url)
        carregar_fatura_por_carimbo(
            self._driver, self._wait, self._carimbo_atual, aguardar_campos
        )

    def buscar_por_instalacao(
        self,
        uc: str,
        mes: str,
        ano: str,
    ) -> None:
        """
        Abre a fatura de uma UC num mês de referência e deixa o formulário
        pronto para edição — equivalente a buscar_carimbo mas pelo número
        de instalação + período.

        Parâmetros:
            uc  — número de instalação (ex: "000116994011")
            mes — mês de referência, dois dígitos (ex: "05")
            ano — ano de referência, quatro dígitos (ex: "2026")

        Fluxo interno:
            1. Navega para a tela de Instalação
            2. Digita a UC e clica em Carregar
            3. Lê a tabela de meses disponíveis
            4. Seleciona o mês e abre o formulário de edição
        """
        mes_z = str(mes).zfill(2)
        ano_s = str(ano)
        data_alvo = _dt.date(int(ano_s), int(mes_z), 1)

        log(f"buscar_por_instalacao: UC={uc} ref={mes_z}/{ano_s}")
        abrir_tela_instalacao(self._driver, self._wait)
        aguardar_tela_instalacao_pronta(self._driver, self._wait)
        preencher_input_texto(self._driver, self._wait, "instalacao", uc, pausa_antes=0.2)
        clicar_botao_carregar_instalacao(self._driver, self._wait)
        _aguardar_sem_spinner(self._driver, timeout=10, min_wait=0.5)
        abrir_referencia_existente_para_edicao(self._driver, self._wait, data_alvo)
        self._carimbo_atual = ""  # carimbo é desconhecido neste fluxo

    def listar_meses_instalacao(self, uc: str) -> list[str]:
        """
        Retorna os meses disponíveis na tabela de faturas de uma UC.
        Não abre nenhuma fatura para edição — apenas lê a tabela.

        Retorno: lista de strings no formato "MM/YYYY", ex: ["03/2026", "04/2026"]
        """
        log(f"listar_meses_instalacao: UC={uc}")
        abrir_tela_instalacao(self._driver, self._wait)
        aguardar_tela_instalacao_pronta(self._driver, self._wait)
        preencher_input_texto(self._driver, self._wait, "instalacao", uc, pausa_antes=0.2)
        clicar_botao_carregar_instalacao(self._driver, self._wait)
        _aguardar_sem_spinner(self._driver, timeout=10, min_wait=0.5)
        datas = obter_datas_referencia_tabela(self._driver, self._wait)
        return [f"{d.month:02d}/{d.year}" for d in datas]

    # ── Leitura ───────────────────────────────────────────────────────────────

    def obter_campos(self) -> dict[str, str]:
        """
        Retorna todos os campos visíveis do formulário atual.

        Retorno: {campo_id: valor_atual}
        """
        brutos = coletar_campos_visiveis(self._driver)
        resultado: dict[str, str] = {}
        for campo in brutos:
            identificador = campo.get("id") or campo.get("name") or ""
            if identificador:
                resultado[identificador] = str(campo.get("value") or "").strip()
        return resultado

    def obter_campo(self, nome: str) -> str:
        """Lê o valor atual de um campo específico pelo id ou name."""
        try:
            el = localizar_input_exato(self._driver, self._wait, nome)
            return (el.get_attribute("value") or "").strip()
        except Exception:
            return ""

    # ── Escrita ───────────────────────────────────────────────────────────────

    def preencher_campo(self, nome: str, valor: Any) -> bool:
        """
        Preenche um único campo e retorna True se confirmado.

        Equivalente a: campo[nome] = valor
        """
        try:
            el = localizar_input_exato(self._driver, self._wait, nome)
            return bool(preencher_elemento_html(self._driver, el, valor))
        except Exception as exc:
            warn(f"preencher_campo({nome!r}): {type(exc).__name__}: {exc}")
            return False

    def editar_campos(
        self,
        correcoes: dict[str, Any],
        ordem: tuple[str, ...] = (),
    ) -> ResultadoEdicao:
        """
        Aplica um dicionário de correções: {campo_id: valor_novo}.

        Campos cujo valor atual já coincide com o desejado são pulados.
        Retorna ResultadoEdicao com detalhes por campo.

        Parâmetros:
            correcoes — {campo_id: valor} — campos para alterar
            ordem     — sequência de preenchimento (campos fora da ordem
                        são preenchidos depois, em ordem de inserção)
        """
        carimbo = self._carimbo_atual or "?"
        resultado = ResultadoEdicao(carimbo=carimbo)

        if not correcoes:
            log(f"editar_campos: nenhuma correção para BB_{carimbo}.")
            return resultado

        campos_ord = [c for c in ordem if c in correcoes]
        campos_ord += [c for c in correcoes if c not in campos_ord]

        for campo in campos_ord:
            valor_novo = correcoes[campo]
            if valor_vazio(valor_novo):
                continue
            try:
                el = localizar_input_exato(self._driver, self._wait, campo)
                valor_anterior = (el.get_attribute("value") or "").strip()
                valor_fmt = str(formatar_valor_para_campo(campo, valor_novo, "text"))

                if valor_anterior == valor_fmt:
                    resultado.sem_mudanca += 1
                    log(f"  {campo}: já correto ({valor_anterior})")
                    continue

                ok = bool(preencher_elemento_html(self._driver, el, valor_novo))
                resultado.alterados.append(
                    CampoAlterado(
                        campo=campo,
                        valor_anterior=valor_anterior,
                        valor_novo=valor_fmt,
                        confirmado=ok,
                    )
                )
                if ok:
                    log(f"  {campo}: {valor_anterior!r} -> {valor_fmt!r}")
                else:
                    warn(f"  {campo}: preenchimento não confirmado")
            except Exception as exc:
                msg = f"{campo}: {type(exc).__name__}: {exc}"
                warn(f"  {msg}")
                resultado.erros.append(msg)

        return resultado

    def alterar_instalacao(self, nova_uc: str) -> bool:
        """
        Altera o número de instalação/UC no formulário ativo.

        Tenta os IDs mais comuns (fatInstalacao, instalacao, uc…).
        Retorna True se conseguiu preencher algum deles.
        """
        nova_uc = str(nova_uc).strip()
        for campo_id in _CAMPOS_INSTALACAO:
            try:
                el = self._driver.find_elements(By.ID, campo_id)
                if el and el[0].is_displayed():
                    ok = bool(preencher_elemento_html(self._driver, el[0], nova_uc))
                    if ok:
                        log(f"alterar_instalacao: {campo_id} = {nova_uc!r}")
                        return True
            except Exception:
                continue
        warn(f"alterar_instalacao: nenhum campo de UC encontrado para {nova_uc!r}")
        return False

    # ── Persistência ──────────────────────────────────────────────────────────

    def salvar(self) -> None:
        """Clica em Salvar e aguarda o spinner desaparecer."""
        clicar_botao_salvar(self._driver, self._wait)
        _aguardar_sem_spinner(self._driver, timeout=10, min_wait=0.5)

    def salvar_e_auditar(self, *, rapido: bool = False) -> tuple[str, str, str]:
        """
        Salva a fatura, abre a tela de auditoria, fecha a aba e clica em Próxima Fatura.

        rapido=True: não lê o resultado da auditoria — clica auditoria e fecha
        imediatamente. Reduz o tempo por fatura de ~8-15 s para ~2-3 s.

        Retorna (pct_diferenca, valor_diferenca, itens_divergentes).
        """
        carimbo = self._carimbo_atual or "?"
        return salvar_auditar_e_avancar(self._driver, self._wait, carimbo, rapido=rapido)

    def proxima_fatura(self) -> None:
        """Clica em Próxima Fatura sem salvar (ex: pular um carimbo)."""
        clicar_botao_proxima_fatura(self._driver, self._wait)
        _aguardar_sem_spinner(self._driver, timeout=8, min_wait=0.3)

    def reabrir_tela_edicao(self) -> None:
        """Volta para a tela de busca por carimbo (útil entre lotes)."""
        self._descartar_mudancas()
        abrir_tela_edicao_carimbo(self._driver, self._wait, self._edit_url)

    # ── Diagnóstico ───────────────────────────────────────────────────────────

    def snapshot(self, saida_dir: Path, carimbo: str | None = None) -> None:
        """
        Salva HTML e JSON de todos os campos visíveis para diagnóstico.

        Gera dois arquivos em saida_dir:
            BB_XXXXXXX_edicao.html
            BB_XXXXXXX_campos.json
        """
        carimbo = carimbo or self._carimbo_atual or "0"
        salvar_snapshot(self._driver, saida_dir, carimbo)

    # ── Utilitários de execução ───────────────────────────────────────────────

    def registrar(
        self,
        execucao_csv: Path,
        carimbo: str,
        status: str,
        detalhe: str = "",
    ) -> None:
        """Registra resultado de um carimbo no CSV de controle de execução."""
        registrar_execucao(execucao_csv, carimbo, status, detalhe)


# ── Helpers de script ─────────────────────────────────────────────────────────

def carregar_lista_carimbos_args(args) -> list[str]:
    """
    Lê carimbos de args.carimbo (list) e/ou args.carimbos_arquivo (txt).
    Retorna lista deduplicada de números normalizados.

    Uso típico em parse_args():
        p.add_argument("--carimbo", action="append", default=[])
        p.add_argument("--carimbos-arquivo", type=str, default="")
    """
    itens: list[str] = list(args.carimbo or [])
    arq = getattr(args, "carimbos_arquivo", "") or ""
    if arq:
        p = Path(arq)
        for linha in p.read_text(encoding="utf-8-sig").splitlines():
            linha = linha.strip()
            if linha and not linha.startswith("#"):
                itens.append(linha)
    vistos: set[str] = set()
    resultado: list[str] = []
    for item in itens:
        try:
            n = normalizar_carimbo(item)
            if n not in vistos:
                vistos.add(n)
                resultado.append(n)
        except ValueError:
            pass
    return resultado


def formatar_correcao(header: str, valor: Any) -> str:
    """Formata um valor OCR para o formato esperado pelo campo CONSEN."""
    return str(formatar_valor_para_campo(header, valor, "text"))
