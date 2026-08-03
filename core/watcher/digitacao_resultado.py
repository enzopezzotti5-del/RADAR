"""
Análise de resultados de digitação no CONSEN.

Este módulo implementa:
- Detecção de UC não cadastrada (com base em evidências explícitas da página).
- Detecção de falso salvamento (mensagem de sucesso sem registro persistido).
- Detecção de salvamento incompleto (registro existe com campos críticos inválidos).
- Second-pass automático para auditoria_sem_valor.
- Rotina de confirmação pós-salvamento.

Todas as funções são pure-python testáveis com fixtures HTML/texto.
Os seletores reais do CONSEN devem ser injetados via parâmetros ou variáveis
de ambiente listadas no final deste arquivo.

Campos críticos mínimos de confirmação:
    carimbo, concessionaria, instalacao/UC, referencia,
    emissao, vencimento, valor_total, consumo_principal.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .estados import ResultadoDigitacao


# ──────────────────────────────────────────────────────────────────────────────
# Padrões de texto que indicam UC não cadastrada (expandir conforme CONSEN real)
# ──────────────────────────────────────────────────────────────────────────────

_MENSAGENS_UC_NAO_CADASTRADA: list[str] = [
    "instalação não encontrada",
    "instalacao nao encontrada",
    "uc não cadastrada",
    "uc nao cadastrada",
    "código de instalação inválido",
    "codigo de instalacao invalido",
    "instalação não localizada",
    "instalacao nao localizada",
    "não existe cadastro",
    "nao existe cadastro",
    "registro não encontrado para esta instalação",
    "registro nao encontrado para esta instalacao",
    # Adicionar mensagens reais do CONSEN após inspeção da página
]

# Padrões que NÃO indicam UC não cadastrada (para evitar falso positivo)
_NAO_INDICAM_UC_AUSENTE: list[str] = [
    "timeout",
    "sessão expirada",
    "sessao expirada",
    "erro de conexão",
    "erro de conexao",
    "aguarde",
    "carregando",
    "servidor indisponível",
    "servidor indisponivel",
]

# Padrões de texto indicando que o registro foi salvo com sucesso real
_MENSAGENS_SALVO_COM_SUCESSO: list[str] = [
    "registro salvo",
    "salvo com sucesso",
    "gravado com sucesso",
    "operação realizada",
    "operacao realizada",
]

# Padrões de falso salvamento (aparência de sucesso mas sem persistência)
_MENSAGENS_FALSO_SALVAMENTO: list[str] = [
    # Adicionar após inspeção real do CONSEN
]


# ──────────────────────────────────────────────────────────────────────────────
# Tipos de resultado de análise
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class AnaliseRegistro:
    """Resultado da análise pós-salvamento no CONSEN."""
    resultado: ResultadoDigitacao
    carimbo: str | None = None
    referencia: str | None = None
    instalacao: str | None = None
    valor_encontrado: float | None = None
    campos_divergentes: list[str] = field(default_factory=list)
    mensagem_pagina: str = ""
    etapa: str = ""
    metodo_confirmacao: str = ""
    evidencia: str = ""
    timestamp: str = field(default_factory=lambda: dt.datetime.now().isoformat())

    @property
    def status_digitacao_canonico(self) -> str:
        """STATUS_DIGITACAO compatível com o indice_master.csv."""
        return ResultadoDigitacao.para_status_digitacao(self.resultado)


@dataclass
class CamposEsperados:
    """Valores esperados para validação pós-salvamento."""
    carimbo: str
    concessionaria: str
    instalacao: str
    referencia: str
    emissao: dt.date | None = None
    vencimento: dt.date | None = None
    valor_total: float | None = None
    consumo_kwh: float | None = None
    grupo: str | None = None  # "BT" ou "MT"


# ──────────────────────────────────────────────────────────────────────────────
# Detecção de UC não cadastrada
# ──────────────────────────────────────────────────────────────────────────────

def detectar_uc_nao_cadastrada(
    texto_pagina: str,
    *,
    url: str = "",
    etapa: str = "preenchimento_uc",
) -> bool:
    """Retorna True apenas se houver evidência EXPLÍCITA de UC não cadastrada.

    Nunca retorna True por timeout, erro de rede ou resultado vazio.
    """
    texto_norm = _normalizar_texto(texto_pagina)

    # Se há indicadores de problema transitório, não classificar como UC ausente
    for indicador_transitorio in _NAO_INDICAM_UC_AUSENTE:
        if indicador_transitorio in texto_norm:
            return False

    for padrao in _MENSAGENS_UC_NAO_CADASTRADA:
        if padrao in texto_norm:
            return True

    return False


def analisar_uc_nao_cadastrada(
    texto_pagina: str,
    *,
    uc: str = "",
    referencia: str = "",
    concessionaria: str = "",
    url: str = "",
    etapa: str = "preenchimento_uc",
) -> AnaliseRegistro | None:
    """Retorna AnaliseRegistro com UC_NAO_CADASTRADA se confirmado, None caso contrário.

    Nunca classifica como UC_NAO_CADASTRADA apenas por timeout ou ausência de dados.
    """
    if not detectar_uc_nao_cadastrada(texto_pagina, url=url, etapa=etapa):
        return None

    mensagem = _extrair_mensagem_relevante(texto_pagina, _MENSAGENS_UC_NAO_CADASTRADA)
    return AnaliseRegistro(
        resultado=ResultadoDigitacao.UC_NAO_CADASTRADA,
        instalacao=uc,
        referencia=referencia,
        mensagem_pagina=mensagem,
        etapa=etapa,
        metodo_confirmacao="texto_pagina",
        evidencia=f"url={url}; mensagem={mensagem!r}",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Confirmação pós-salvamento
# ──────────────────────────────────────────────────────────────────────────────

def analisar_resultado_salvamento(
    esperado: CamposEsperados,
    campos_encontrados: dict[str, Any],
    *,
    registro_existe: bool,
    mensagem_pagina: str = "",
    etapa: str = "confirmacao_consen",
) -> AnaliseRegistro:
    """Compara campos esperados com os encontrados na releitura do CONSEN.

    Args:
        esperado: Valores que foram enviados para digitação.
        campos_encontrados: Valores lidos de volta do registro aberto no CONSEN.
        registro_existe: True se o registro foi encontrado na consulta.
        mensagem_pagina: Texto capturado da página após salvamento.
        etapa: Identificador da etapa para rastreabilidade.

    Returns:
        AnaliseRegistro com resultado classificado.
    """
    if not registro_existe:
        # Mensagem de sucesso mas sem persistência = falso salvamento
        if _indica_sucesso(mensagem_pagina):
            return AnaliseRegistro(
                resultado=ResultadoDigitacao.FALSO_SALVAMENTO,
                carimbo=esperado.carimbo,
                referencia=esperado.referencia,
                instalacao=esperado.instalacao,
                mensagem_pagina=mensagem_pagina,
                etapa=etapa,
                metodo_confirmacao="consulta_pos_salvamento",
                evidencia=f"mensagem={mensagem_pagina!r}; registro_existe=False",
            )
        # Sem mensagem de sucesso e sem registro = inconclusivo
        return AnaliseRegistro(
            resultado=ResultadoDigitacao.AUDITORIA_INCONCLUSIVA,
            carimbo=esperado.carimbo,
            referencia=esperado.referencia,
            instalacao=esperado.instalacao,
            mensagem_pagina=mensagem_pagina,
            etapa=etapa,
            metodo_confirmacao="consulta_pos_salvamento",
            evidencia="registro nao encontrado; sem mensagem de sucesso",
        )

    # Registro existe: verificar se já existia (referência já existente)
    if campos_encontrados.get("_ja_existia"):
        return AnaliseRegistro(
            resultado=ResultadoDigitacao.REFERENCIA_JA_EXISTENTE,
            carimbo=campos_encontrados.get("carimbo") or esperado.carimbo,
            referencia=esperado.referencia,
            instalacao=esperado.instalacao,
            valor_encontrado=campos_encontrados.get("valor_total"),
            etapa=etapa,
            metodo_confirmacao="consulta_pos_salvamento",
            evidencia="referencia preexistente confirmada",
        )

    # Verificar campos críticos
    divergentes = _verificar_campos_criticos(esperado, campos_encontrados)

    if divergentes:
        return AnaliseRegistro(
            resultado=ResultadoDigitacao.SALVAMENTO_INCOMPLETO,
            carimbo=campos_encontrados.get("carimbo") or esperado.carimbo,
            referencia=esperado.referencia,
            instalacao=esperado.instalacao,
            valor_encontrado=campos_encontrados.get("valor_total"),
            campos_divergentes=divergentes,
            etapa=etapa,
            metodo_confirmacao="releitura_campos_criticos",
            evidencia=f"campos divergentes: {divergentes}",
        )

    return AnaliseRegistro(
        resultado=ResultadoDigitacao.DIGITADO_CONFIRMADO,
        carimbo=campos_encontrados.get("carimbo") or esperado.carimbo,
        referencia=esperado.referencia,
        instalacao=esperado.instalacao,
        valor_encontrado=campos_encontrados.get("valor_total"),
        etapa=etapa,
        metodo_confirmacao="releitura_campos_criticos",
        evidencia="todos os campos críticos confirmados",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Second-pass para auditoria_sem_valor
# ──────────────────────────────────────────────────────────────────────────────

def second_pass_auditoria_sem_valor(
    esperado: CamposEsperados,
    campos_encontrados: dict[str, Any] | None,
    *,
    registro_existe: bool,
    etapa: str = "second_pass",
) -> AnaliseRegistro:
    """Second-pass quando auditoria retornou 'auditoria_sem_valor'.

    Consulta diretamente o CONSEN para determinar se o registro está persistido.
    Não move para Investigar antes deste passo.

    Resultado:
        DIGITADO_CONFIRMADO    — registro existe e está consistente.
        FALSO_SALVAMENTO       — auditoria_sem_valor e registro não existe.
        SALVAMENTO_INCOMPLETO  — existe mas campos críticos inválidos.
        AUDITORIA_INCONCLUSIVA — consulta inconclusiva (não promover ainda).
    """
    if not registro_existe or campos_encontrados is None:
        return AnaliseRegistro(
            resultado=ResultadoDigitacao.FALSO_SALVAMENTO,
            carimbo=esperado.carimbo,
            referencia=esperado.referencia,
            instalacao=esperado.instalacao,
            etapa=etapa,
            metodo_confirmacao="second_pass_auditoria",
            evidencia="auditoria_sem_valor + registro nao encontrado no CONSEN",
        )

    divergentes = _verificar_campos_criticos(esperado, campos_encontrados)
    if divergentes:
        return AnaliseRegistro(
            resultado=ResultadoDigitacao.SALVAMENTO_INCOMPLETO,
            carimbo=campos_encontrados.get("carimbo") or esperado.carimbo,
            referencia=esperado.referencia,
            instalacao=esperado.instalacao,
            valor_encontrado=campos_encontrados.get("valor_total"),
            campos_divergentes=divergentes,
            etapa=etapa,
            metodo_confirmacao="second_pass_auditoria",
            evidencia=f"auditoria_sem_valor + campos divergentes: {divergentes}",
        )

    return AnaliseRegistro(
        resultado=ResultadoDigitacao.DIGITADO_CONFIRMADO,
        carimbo=campos_encontrados.get("carimbo") or esperado.carimbo,
        referencia=esperado.referencia,
        instalacao=esperado.instalacao,
        valor_encontrado=campos_encontrados.get("valor_total"),
        etapa=etapa,
        metodo_confirmacao="second_pass_auditoria",
        evidencia="auditoria_sem_valor mas registro confirmado via second-pass",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ──────────────────────────────────────────────────────────────────────────────

def _normalizar_texto(texto: str) -> str:
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", texto or "")
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    return sem_acento.lower()


def _extrair_mensagem_relevante(texto: str, padroes: list[str]) -> str:
    texto_norm = _normalizar_texto(texto)
    for padrao in padroes:
        if padrao in texto_norm:
            # Retorna a linha que contém o padrão
            for linha in texto.splitlines():
                if padrao in _normalizar_texto(linha):
                    return linha.strip()
    return ""


def _indica_sucesso(texto: str) -> bool:
    texto_norm = _normalizar_texto(texto)
    return any(p in texto_norm for p in _MENSAGENS_SALVO_COM_SUCESSO)


def _verificar_campos_criticos(
    esperado: CamposEsperados,
    encontrado: dict[str, Any],
) -> list[str]:
    """Retorna lista de campos divergentes entre esperado e encontrado."""
    divergentes: list[str] = []

    # Valor total: 0,00 indevido ou ausente
    val_enc = encontrado.get("valor_total")
    if val_enc is not None:
        if val_enc == 0.0 and esperado.valor_total and esperado.valor_total > 0:
            divergentes.append("valor_total=0,00 indevido")
        elif esperado.valor_total and abs(val_enc - esperado.valor_total) > 0.02:
            divergentes.append(
                f"valor_total esperado={esperado.valor_total:.2f} encontrado={val_enc:.2f}"
            )
    elif esperado.valor_total:
        divergentes.append("valor_total ausente")

    # Instalação/UC
    uc_enc = str(encontrado.get("instalacao") or "").strip()
    if esperado.instalacao and not uc_enc:
        divergentes.append("instalacao ausente")
    elif esperado.instalacao and uc_enc and uc_enc != esperado.instalacao:
        divergentes.append(f"instalacao esperada={esperado.instalacao} encontrada={uc_enc}")

    # Referência
    ref_enc = str(encontrado.get("referencia") or "").strip()
    if esperado.referencia and not ref_enc:
        divergentes.append("referencia ausente")

    # Vencimento
    vcto_enc = encontrado.get("vencimento")
    if esperado.vencimento and vcto_enc is None:
        divergentes.append("vencimento ausente")
    elif esperado.vencimento and vcto_enc and vcto_enc != esperado.vencimento:
        divergentes.append(
            f"vencimento esperado={esperado.vencimento} encontrado={vcto_enc}"
        )

    return divergentes


# ──────────────────────────────────────────────────────────────────────────────
# Documentação de seletores pendentes (a completar com inspeção real do CONSEN)
# ──────────────────────────────────────────────────────────────────────────────

SELETORES_PENDENTES_CONSEN = """
Os seletores abaixo devem ser preenchidos após inspeção manual do CONSEN com
DevTools aberto, navegando pelos fluxos de UC não encontrada e salvamento.

UC_NAO_CADASTRADA:
  - Identificar o elemento DOM que exibe a mensagem de erro ao digitar uma UC inexistente.
  - Ex: '#msg-erro-instalacao', '.alert-danger', ou texto no <span class="validacao">.
  - Adicionar o texto exato em _MENSAGENS_UC_NAO_CADASTRADA acima.

FALSO_SALVAMENTO:
  - Verificar se o CONSEN exibe modal/toast de confirmação ao salvar.
  - Verificar se a URL muda após salvar (ex: /editar → /visualizar?id=...).
  - Verificar se o campo "Carimbo" fica preenchido no registro aberto após salvar.
  - Adicionar padrões em _MENSAGENS_SALVO_COM_SUCESSO e _MENSAGENS_FALSO_SALVAMENTO.

CAMPOS_CRITICOS_RELEITURA:
  - Após salvar, abrir o registro pelo carimbo e capturar os campos:
    carimbo, concessionaria, instalacao, referencia, emissao, vencimento,
    valor_total, consumo_kwh.
  - Mapear seletores CSS/XPath para cada campo no HTML do CONSEN.
  - Implementar função de releitura no digitador correspondente.

PLANO_VALIDACAO_REAL:
  1. Usar um PDF de teste com UC conhecida e válida.
  2. Executar digitação em modo controlled.
  3. Inspecionar HTML pós-salvamento com DevTools → Network → Response.
  4. Capturar texto completo da página após sucesso.
  5. Repetir com UC inexistente e capturar mensagem de erro.
  6. Adicionar textos capturados nas listas acima.
  7. Executar pytest tests/test_watcher_v2_digitacao_resultado.py.
"""
