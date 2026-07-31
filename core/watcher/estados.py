"""Estados do ciclo de vida de um PDF no Watcher V2."""
from __future__ import annotations
from enum import Enum


class EstadoPDF(str, Enum):
    DETECTADO              = "detectado"
    AGUARDANDO_ESTABILIDADE = "aguardando_estabilidade"
    DUPLICADO              = "duplicado"
    CLASSIFICADO           = "classificado"
    ACEITO_AUTOMATICAMENTE = "aceito_automaticamente"
    REVISAO_MANUAL         = "revisao_manual"
    CONCESSIONARIA_DESCONHECIDA = "concessionaria_desconhecida"
    TIPO_NAO_SUPORTADO     = "tipo_nao_suportado"
    STAGING_CRIADO         = "staging_criado"
    CARIMBADO              = "carimbado"
    PIPELINE_INICIADO      = "pipeline_iniciado"
    PIPELINE_CONCLUIDO     = "pipeline_concluido"
    ERRO                   = "erro"


class ResultadoDigitacao(str, Enum):
    """Substatus de resultado após o pipeline de digitação no CONSEN.

    Retrocompatível: o campo STATUS_DIGITACAO do indice_master.csv continua
    sendo preenchido com os valores canônicos (DIGITADO/PENDENTE/PULADO/PROCESSANDO).
    Este enum detalha o resultado dentro do estado_v2.json e do manifesto de sessão.
    """
    # Sucesso confirmado por releitura do CONSEN
    DIGITADO_CONFIRMADO       = "DIGITADO_CONFIRMADO"

    # Referência já existia no CONSEN antes da tentativa de salvamento
    REFERENCIA_JA_EXISTENTE   = "REFERENCIA_JA_EXISTENTE"

    # UC informada não está cadastrada no CONSEN (evidência explícita da página)
    UC_NAO_CADASTRADA         = "UC_NAO_CADASTRADA"

    # CONSEN indicou sucesso mas releitura posterior não encontrou registro
    FALSO_SALVAMENTO          = "FALSO_SALVAMENTO"

    # Registro existe mas campos críticos estão vazios, zerados ou inconsistentes
    SALVAMENTO_INCOMPLETO     = "SALVAMENTO_INCOMPLETO"

    # Auditoria retornou sem valor mas estado real no CONSEN é inconclusivo
    AUDITORIA_INCONCLUSIVA    = "AUDITORIA_INCONCLUSIVA"

    # Mais de um sinal contraditório para BT/MT ou concessionária
    CLASSIFICACAO_AMBIGUA     = "CLASSIFICACAO_AMBIGUA"

    # Falha na extração de campo obrigatório (data, valor, UC, etc.)
    ERRO_EXTRACAO             = "ERRO_EXTRACAO"

    # Erro técnico (Selenium, ChromeDriver, timeout, rede)
    ERRO_TECNICO              = "ERRO_TECNICO"

    # Aguardando nova tentativa; carimbo anterior preservado
    PENDENTE_RETRY            = "PENDENTE_RETRY"

    # Mapeamento para STATUS_DIGITACAO canônico do indice_master.csv
    _ignore_ = ["_STATUS_MAP"]

    @classmethod
    def para_status_digitacao(cls, resultado: "ResultadoDigitacao") -> str:
        """Converte substatus para o STATUS_DIGITACAO canônico do índice master."""
        _map = {
            cls.DIGITADO_CONFIRMADO:     "DIGITADO",
            cls.REFERENCIA_JA_EXISTENTE: "PULADO",
            cls.UC_NAO_CADASTRADA:       "PENDENTE",
            cls.FALSO_SALVAMENTO:        "PENDENTE",
            cls.SALVAMENTO_INCOMPLETO:   "PENDENTE",
            cls.AUDITORIA_INCONCLUSIVA:  "PENDENTE",
            cls.CLASSIFICACAO_AMBIGUA:   "PENDENTE",
            cls.ERRO_EXTRACAO:           "PENDENTE",
            cls.ERRO_TECNICO:            "PENDENTE",
            cls.PENDENTE_RETRY:          "PENDENTE",
        }
        return _map.get(resultado, "PENDENTE")
