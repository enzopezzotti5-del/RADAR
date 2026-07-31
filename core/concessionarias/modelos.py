from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

class GrupoTensao(str, Enum): BT="bt"; MT="mt"
class EstadoImplementacao(str, Enum):
    SUPORTADO="suportado"; PARCIAL="parcial"; CODIGO_NAO_CONECTADO="codigo_existente_nao_conectado"; NAO_IMPLEMENTADO="nao_implementado"; DESABILITADO="desabilitado"
@dataclass(frozen=True)
class ContextoExecucao:
    concessionaria:str; grupo:GrupoTensao; mes:str; ano:str; pasta_entrada:Path; session_root:Path; retomar:bool=False; dry_run:bool=False
@dataclass(frozen=True)
class DecisaoClassificacao:
    concessionaria:str; grupo:GrupoTensao; confianca:float; evidencias:tuple[str,...]=()
@dataclass(frozen=True)
class PipelineSpec:
    estado:EstadoImplementacao; script:Path|None=None; identificador:str|None=None; argumentos:tuple[str,...]=(); aceita_pasta:bool=False; aceita_session_root:bool=False; aceita_retomar:bool=False; aceita_dry_run:bool=False; exige_auditoria:bool=False; atualiza_indice:bool=False; motivo:str=""
@dataclass(frozen=True)
class ConcessionariaSpec:
    id:str; nome:str; aliases:tuple[str,...]; grupos:dict[GrupoTensao,PipelineSpec]; observacoes:str=""
@dataclass
class ResultadoPipeline:
    sucesso:bool; return_code:int; session_id:str|None=None; artefatos:dict[str,str]=field(default_factory=dict); auditoria:str|None=None; arquivos_processados:int=0; arquivos_digitados:int=0; arquivos_pulados:int=0; arquivos_com_erro:int=0; erro:str|None=None
