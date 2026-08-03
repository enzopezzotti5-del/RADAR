"""Registro de processamento de cada PDF no Watcher V2."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from .estados import EstadoPDF, ResultadoDigitacao


@dataclass
class RegistroProcessamento:
    arquivo_original: str
    caminho_original: str
    sha256: str

    # Classificação
    concessionaria_prevista: str | None = None
    confianca_concessionaria: float = 0.0
    metodo_concessionaria: str = ""
    evidencia_concessionaria: str = ""

    grupo_previsto: str | None = None
    confianca_grupo: float = 0.0
    evidencias_grupo: list[str] = field(default_factory=list)
    penalidades_grupo: list[str] = field(default_factory=list)
    status_rotulagem: str = ""

    confianca_roteamento: float = 0.0
    decisao_politica: str = ""

    # Pipeline
    estado_suporte: str = ""
    pipeline_resolvido: str | None = None
    comando_planejado: list[str] = field(default_factory=list)

    # Operacional
    session_id: str | None = None
    staging: str | None = None
    carimbo: str | None = None
    destino: str | None = None
    motivo_rejeicao: str = ""
    tentativas: int = 0

    estado: EstadoPDF = EstadoPDF.DETECTADO
    timestamp_deteccao: str = field(default_factory=lambda: dt.datetime.now().isoformat())
    timestamp_conclusao: str | None = None

    # ── Substatus de digitação (retrocompatível) ──────────────────────────────
    resultado_digitacao: ResultadoDigitacao | None = None
    # Motivo detalhado do resultado (mensagem da página, campo vazio, etc.)
    detalhe_resultado: str = ""
    # Etapa do pipeline onde ocorreu (ex: "preenchimento_uc", "confirmacao_consen")
    etapa_resultado: str = ""
    # Método de confirmação usado (ex: "releitura_carimbo", "consulta_referencia")
    metodo_confirmacao: str = ""
    # Evidência gerada (caminho para HTML, screenshot, texto capturado)
    evidencia_resultado: str = ""

    # Valores críticos enviados e encontrados na releitura
    valor_esperado: float | None = None
    valor_encontrado: float | None = None
    campos_divergentes: list[str] = field(default_factory=list)

    # Manifesto de lote (para rastreabilidade de lote fechado)
    lote_id: str | None = None
    lote_incremental: bool = False

    def to_dict(self) -> dict:
        return {
            "arquivo_original": self.arquivo_original,
            "caminho_original": self.caminho_original,
            "sha256": self.sha256,
            "concessionaria_prevista": self.concessionaria_prevista,
            "confianca_concessionaria": self.confianca_concessionaria,
            "metodo_concessionaria": self.metodo_concessionaria,
            "evidencia_concessionaria": self.evidencia_concessionaria,
            "grupo_previsto": self.grupo_previsto,
            "confianca_grupo": self.confianca_grupo,
            "evidencias_grupo": self.evidencias_grupo,
            "penalidades_grupo": self.penalidades_grupo,
            "status_rotulagem": self.status_rotulagem,
            "confianca_roteamento": self.confianca_roteamento,
            "decisao_politica": self.decisao_politica,
            "estado_suporte": self.estado_suporte,
            "pipeline_resolvido": self.pipeline_resolvido,
            "comando_planejado": self.comando_planejado,
            "session_id": self.session_id,
            "staging": self.staging,
            "carimbo": self.carimbo,
            "destino": self.destino,
            "motivo_rejeicao": self.motivo_rejeicao,
            "tentativas": self.tentativas,
            "estado": self.estado.value,
            "timestamp_deteccao": self.timestamp_deteccao,
            "timestamp_conclusao": self.timestamp_conclusao,
            # substatus
            "resultado_digitacao": self.resultado_digitacao.value if self.resultado_digitacao else None,
            "detalhe_resultado": self.detalhe_resultado,
            "etapa_resultado": self.etapa_resultado,
            "metodo_confirmacao": self.metodo_confirmacao,
            "evidencia_resultado": self.evidencia_resultado,
            "valor_esperado": self.valor_esperado,
            "valor_encontrado": self.valor_encontrado,
            "campos_divergentes": self.campos_divergentes,
            "lote_id": self.lote_id,
            "lote_incremental": self.lote_incremental,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RegistroProcessamento":
        """Reconstrói um RegistroProcessamento a partir de um dicionário serializado."""
        obj = cls(
            arquivo_original=d.get("arquivo_original", ""),
            caminho_original=d.get("caminho_original", ""),
            sha256=d.get("sha256", ""),
        )
        obj.concessionaria_prevista = d.get("concessionaria_prevista")
        obj.confianca_concessionaria = float(d.get("confianca_concessionaria") or 0.0)
        obj.metodo_concessionaria = d.get("metodo_concessionaria") or ""
        obj.evidencia_concessionaria = d.get("evidencia_concessionaria") or ""
        obj.grupo_previsto = d.get("grupo_previsto")
        obj.confianca_grupo = float(d.get("confianca_grupo") or 0.0)
        obj.evidencias_grupo = list(d.get("evidencias_grupo") or [])
        obj.penalidades_grupo = list(d.get("penalidades_grupo") or [])
        obj.status_rotulagem = d.get("status_rotulagem") or ""
        obj.confianca_roteamento = float(d.get("confianca_roteamento") or 0.0)
        obj.decisao_politica = d.get("decisao_politica") or ""
        obj.estado_suporte = d.get("estado_suporte") or ""
        obj.pipeline_resolvido = d.get("pipeline_resolvido")
        obj.comando_planejado = list(d.get("comando_planejado") or [])
        obj.session_id = d.get("session_id")
        obj.staging = d.get("staging")
        obj.carimbo = d.get("carimbo")
        obj.destino = d.get("destino")
        obj.motivo_rejeicao = d.get("motivo_rejeicao") or ""
        obj.tentativas = int(d.get("tentativas") or 0)
        obj.timestamp_deteccao = d.get("timestamp_deteccao") or obj.timestamp_deteccao
        obj.timestamp_conclusao = d.get("timestamp_conclusao")
        estado_val = d.get("estado")
        if estado_val:
            try:
                obj.estado = EstadoPDF(estado_val)
            except ValueError:
                obj.estado = EstadoPDF.ERRO
        rd_val = d.get("resultado_digitacao")
        if rd_val:
            try:
                obj.resultado_digitacao = ResultadoDigitacao(rd_val)
            except ValueError:
                obj.resultado_digitacao = None
        obj.detalhe_resultado = d.get("detalhe_resultado") or ""
        obj.etapa_resultado = d.get("etapa_resultado") or ""
        obj.metodo_confirmacao = d.get("metodo_confirmacao") or ""
        obj.evidencia_resultado = d.get("evidencia_resultado") or ""
        ve = d.get("valor_esperado")
        obj.valor_esperado = float(ve) if ve is not None else None
        vf = d.get("valor_encontrado")
        obj.valor_encontrado = float(vf) if vf is not None else None
        obj.campos_divergentes = list(d.get("campos_divergentes") or [])
        obj.lote_id = d.get("lote_id")
        obj.lote_incremental = bool(d.get("lote_incremental", False))
        return obj
