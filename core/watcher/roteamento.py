"""Roteamento: classifica PDF e resolve pipeline para o Watcher V2."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.classificador.identificar_concessionaria import identificar, ResultadoConcessionaria
from core.classificador.rotular import rotular
from core.classificador.politica import avaliar, DecisaoPolitica, ResultadoPolitica
from core.concessionarias.catalogo import REGISTRO, resolver_pipeline
from core.concessionarias.modelos import EstadoImplementacao, GrupoTensao


@dataclass(frozen=True)
class ResultadoRoteamento:
    # Concessionária
    concessionaria: ResultadoConcessionaria
    # Grupo BT/MT
    grupo: GrupoTensao | None
    confianca_grupo: float
    evidencias_grupo: tuple[str, ...]
    penalidades_grupo: tuple[str, ...]
    status_rotulagem: str
    # Política
    politica: ResultadoPolitica
    # Pipeline
    estado_suporte: str
    pipeline_script: str | None
    comando: list[str]


def rotear(texto: str, arquivo: str = "", pasta: str = "") -> ResultadoRoteamento:
    """Classifica um PDF e resolve o pipeline.

    `arquivo` e `pasta` são metadados — NÃO alteram a classificação.
    A concessionária vem exclusivamente do conteúdo textual.
    """
    # 1. Identificar concessionária
    res_conc = identificar(texto)

    # 2. Classificar BT/MT (independente da concessionária)
    res_grupo = rotular(texto, nome_arquivo=arquivo)
    grupo = GrupoTensao(res_grupo.grupo.lower()) if res_grupo.grupo else None

    # 3. Política de aceitação conjunta
    politica = avaliar(
        concessionaria=res_conc.canonica,
        confianca_concessionaria=res_conc.confianca,
        grupo=grupo,
        confianca_grupo=res_grupo.confianca,
        status_rotulagem=res_grupo.status,
        penalidades=list(res_grupo.penalidades),
    )

    # 4. Resolver pipeline (só se aceito ou suportado)
    estado_suporte = "desconhecido"
    pipeline_script: str | None = None
    comando: list[str] = []

    if res_conc.canonica and grupo:
        info = resolver_pipeline(res_conc.canonica, grupo)
        estado_suporte = info.get("estado", "desconhecido")
        pipeline_script = info.get("pipeline")
        # Comando planejado (não executado aqui)
        if info.get("pipeline"):
            cmd = ["python", info["pipeline"]]
            if info.get("argumentos"):
                cmd.extend(str(a) for a in info["argumentos"])
            comando = cmd

    return ResultadoRoteamento(
        concessionaria=res_conc,
        grupo=grupo,
        confianca_grupo=res_grupo.confianca,
        evidencias_grupo=tuple(res_grupo.evidencias),
        penalidades_grupo=tuple(res_grupo.penalidades),
        status_rotulagem=res_grupo.status,
        politica=politica,
        estado_suporte=estado_suporte,
        pipeline_script=pipeline_script,
        comando=comando,
    )
