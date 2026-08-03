"""
core/schemas/fatura.py
----------------------
Schemas Pydantic v2 para validação dos dados OCR antes da digitação no CONSEN.

Uso:
    from core.schemas.fatura import FaturaMT, FaturaBT, validar_linha_ocr

    # Valida um dict vindo do XLSX OCR
    resultado = validar_linha_ocr(row_dict)
    if resultado.erros:
        log.warning(f"Campos inválidos: {resultado.erros}")

    # Ou com acesso direto ao schema
    try:
        fat = FaturaMT.model_validate(row_dict)
    except ValidationError as e:
        ...
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


# ---------------------------------------------------------------------------
# Constantes de domínio
# ---------------------------------------------------------------------------

_ICMS_MAX = 0.35       # 35% — teto regulatório brasileiro
_PIS_MAX  = 0.02       # 2%
_COFINS_MAX = 0.10     # 10%
_VALOR_MAX = 2_000_000 # R$ 2M — sanidade; faturas acima disso são suspeitas

_SUBGRUPOS_VALIDOS = {
    "A1", "A2", "A3", "A3a", "A4", "AS",
    "B1", "B2", "B3", "B4",
    "A4 [<13,8kV]", "A4 [2,3kV a 25kV]",
}

_DATA_MIN = dt.date(2000, 1, 1)
_DATA_MAX_OFFSET = dt.timedelta(days=180)   # não aceita datas > 6 meses no futuro


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _nn(v: Any) -> float:
    """Converte None/'' para 0.0 e garante float não-negativo."""
    if v is None or v == "":
        return 0.0
    return float(v)


# ---------------------------------------------------------------------------
# Schema base — campos comuns a BT e MT
# ---------------------------------------------------------------------------

class FaturaBase(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        str_strip_whitespace=True,
    )

    # ── Identificação ───────────────────────────────────────────────────────
    Instalacao:        str            = Field(..., min_length=1, description="UC / Instalação")
    fatCarimbo:        Optional[str]  = Field(None, description="BB_XXXXXXX")
    CNPJ:              Optional[str]  = None
    concCod:           Optional[int]  = Field(None, ge=1, description="Código da concessionária no CONSEN")

    # ── Datas ───────────────────────────────────────────────────────────────
    fatDataReferencia: Optional[dt.date] = Field(None, description="Mês de referência (1º dia do mês)")
    fatDataEmissao:    Optional[dt.date] = None
    fatDataVcto:       Optional[dt.date] = None
    fatDataCadastro:   Optional[dt.date] = None
    fatDataLeituraAnterior: Optional[dt.date] = None
    fatDataLeituraAtual:    Optional[dt.date] = None

    # ── Valores financeiros ─────────────────────────────────────────────────
    fatValorFatura:    float = Field(..., gt=0, le=_VALOR_MAX,
                                    description="Valor total da fatura (R$)")
    fatValorNotaFiscal: Optional[float] = Field(None, ge=0)
    fatIlumPublica:    Optional[float]  = Field(None, ge=0)

    # ── Tributos (frações, ex: 0.18 para 18% ICMS) ─────────────────────────
    fatICMS:           Optional[float] = Field(None, ge=0, le=_ICMS_MAX)
    fatPIS:            Optional[float] = Field(None, ge=0, le=_PIS_MAX)
    fatCOFINS:         Optional[float] = Field(None, ge=0, le=_COFINS_MAX)

    # ── Tarifação ───────────────────────────────────────────────────────────
    cadTarifaCod:      Optional[str]   = None
    cadSubGrupoCod:    Optional[str]   = None

    # ── Bandeiras ──────────────────────────────────────────────────────────
    fatValBandeira:    Optional[float] = Field(None, ge=0)
    fatValBandeira2:   Optional[float] = Field(None, ge=0)

    # ── Observações ─────────────────────────────────────────────────────────
    obsCod_1:   Optional[int]   = None
    obsValor_1: Optional[float] = Field(None, ge=0)
    obsCod_2:   Optional[int]   = None
    obsValor_2: Optional[float] = Field(None, ge=0)
    obsCod_3:   Optional[int]   = None
    obsValor_3: Optional[float] = Field(None, ge=0)
    obsCod_4:   Optional[int]   = None
    obsValor_4: Optional[float] = Field(None, ge=0)
    obsCod_5:   Optional[int]   = None
    obsValor_5: Optional[float] = Field(None, ge=0)

    # ── Retenções ───────────────────────────────────────────────────────────
    fatDescPisPercRetImposto:    Optional[float] = Field(None, ge=0)
    fatDescPisValRetImposto:     Optional[float] = Field(None, ge=0)
    fatDescCofinsPercRetImposto: Optional[float] = Field(None, ge=0)
    fatDescCofinsValRetImposto:  Optional[float] = Field(None, ge=0)
    fatDescCsllPercRetImposto:   Optional[float] = Field(None, ge=0)
    fatDescCsllValRetImposto:    Optional[float] = Field(None, ge=0)
    fatDescIrpjPercRetImposto:   Optional[float] = Field(None, ge=0)
    fatDescIrpjValRetImposto:    Optional[float] = Field(None, ge=0)
    fatTributoFederalPerc:       Optional[float] = Field(None, ge=0)
    fatTributoFederalVal:        Optional[float] = Field(None, ge=0)

    # ── Multas / outros ─────────────────────────────────────────────────────
    fatMultas:          Optional[float] = Field(None, ge=0)
    fatMultasDiversas:  Optional[float] = Field(None, ge=0)
    fatDescontoFio:     Optional[float] = Field(None, ge=0)
    fatDescontoFioKwh:  Optional[float] = Field(None, ge=0)

    # ── Controle interno ────────────────────────────────────────────────────
    ARQUIVO:  Optional[str] = None
    ERRO:     Optional[str] = None

    # ── Validators ──────────────────────────────────────────────────────────

    @field_validator("fatDataReferencia", "fatDataEmissao", "fatDataVcto",
                     "fatDataCadastro", "fatDataLeituraAnterior", "fatDataLeituraAtual",
                     mode="before")
    @classmethod
    def _coerce_date(cls, v: Any) -> Any:
        """Aceita datetime, date ou string ISO."""
        if v is None or v == "":
            return None
        if isinstance(v, dt.datetime):
            return v.date()
        if isinstance(v, dt.date):
            return v
        if isinstance(v, str):
            try:
                return dt.date.fromisoformat(v[:10])
            except ValueError:
                return None
        return v

    @field_validator("fatDataReferencia", mode="after")
    @classmethod
    def _data_referencia_razoavel(cls, v: Optional[dt.date]) -> Optional[dt.date]:
        if v is None:
            return v
        hoje = dt.date.today()
        if v < _DATA_MIN:
            raise ValueError(f"fatDataReferencia {v} anterior a {_DATA_MIN}")
        if v > hoje + _DATA_MAX_OFFSET:
            raise ValueError(f"fatDataReferencia {v} muito no futuro (hoje={hoje})")
        return v

    @field_validator("fatICMS", "fatPIS", "fatCOFINS", mode="before")
    @classmethod
    def _coerce_float_none(cls, v: Any) -> Any:
        if v is None or v == "":
            return None
        return float(v)

    @field_validator("cadSubGrupoCod", mode="after")
    @classmethod
    def _subgrupo_conhecido(cls, v: Optional[str]) -> Optional[str]:
        # Aviso não-bloqueante: subgrupos desconhecidos podem ser novos códigos
        if v and v not in _SUBGRUPOS_VALIDOS:
            # Não levanta exceção — só registra como aviso (ver ResultadoValidacao)
            pass
        return v

    @model_validator(mode="after")
    def _consistencia_datas_leitura(self) -> "FaturaBase":
        ant = self.fatDataLeituraAnterior
        atu = self.fatDataLeituraAtual
        if ant and atu and ant >= atu:
            raise ValueError(
                f"fatDataLeituraAnterior ({ant}) >= fatDataLeituraAtual ({atu})"
            )
        return self


# ---------------------------------------------------------------------------
# Schema BT — Baixa Tensão (grupo B, consumo simples)
# ---------------------------------------------------------------------------

class FaturaBT(FaturaBase):
    """Fatura Baixa Tensão: sem demanda, consumo único."""

    fatConPontaRegistrado:    Optional[float] = Field(None, ge=0)
    fatConPontaFaturado:      Optional[float] = Field(None, ge=0)
    fatConPontaValorReais:    Optional[float] = Field(None, ge=0)

    @model_validator(mode="after")
    def _bt_sem_demanda(self) -> "FaturaBT":
        sub = (self.cadSubGrupoCod or "").upper()
        if sub.startswith("A"):
            raise ValueError(
                f"FaturaBT recebeu subgrupo grupo A ({sub}) — use FaturaMT"
            )
        return self


# ---------------------------------------------------------------------------
# Schema MT — Média / Alta Tensão (grupo A, demanda contratada)
# ---------------------------------------------------------------------------

class FaturaMT(FaturaBase):
    """Fatura Média/Alta Tensão: demanda contratada, ponta/fora-ponta."""

    # Demanda contratada
    fatDemContratadaPonta:  Optional[float] = Field(None, ge=0)
    fatDemContratadaFPonta: Optional[float] = Field(None, ge=0)

    # Demanda registrada
    fatDemPontaRegistrada:       Optional[float] = Field(None, ge=0)
    fatDemFPontaIndRegistrada:   Optional[float] = Field(None, ge=0)
    fatDemFPontaCapRegistrada:   Optional[float] = Field(None, ge=0)

    # Demanda faturada
    fatDemPontaFaturada:         Optional[float] = Field(None, ge=0)
    fatDemFPontaIndFaturada:     Optional[float] = Field(None, ge=0)

    # Demanda ultrapassagem
    fatDemPontaUltra:            Optional[float] = Field(None, ge=0)
    fatDemFPontaIndUltra:        Optional[float] = Field(None, ge=0)

    # Demanda excedente
    fatDemPontaExcFaturada:      Optional[float] = Field(None, ge=0)
    fatDemFPontaExcFaturada:     Optional[float] = Field(None, ge=0)
    fatDemPontaExcRegistrada:    Optional[float] = Field(None, ge=0)
    fatDemFPontaExcRegistrada:   Optional[float] = Field(None, ge=0)

    # Consumo ponta / fora-ponta
    fatConPontaRegistrado:       Optional[float] = Field(None, ge=0)
    fatConFPontaIndRegistrado:   Optional[float] = Field(None, ge=0)
    fatConFPontaCapRegistrado:   Optional[float] = Field(None, ge=0)
    fatConIntermediarioRegistrado: Optional[float] = Field(None, ge=0)

    fatConPontaFaturado:         Optional[float] = Field(None, ge=0)
    fatConFPontaIndFaturado:     Optional[float] = Field(None, ge=0)
    fatConFPontaCapFaturado:     Optional[float] = Field(None, ge=0)
    fatConIntermediarioFaturado: Optional[float] = Field(None, ge=0)

    # Excedente consumo
    fatConPontaExcRegistrado:    Optional[float] = Field(None, ge=0)
    fatConFPontaIndExcRegistrado: Optional[float] = Field(None, ge=0)
    fatConPontaExcFaturado:      Optional[float] = Field(None, ge=0)
    fatConFPontaIndExcFaturado:  Optional[float] = Field(None, ge=0)

    # Injetado (GD)
    fatConPontaInjetadoRegistrado:  Optional[float] = Field(None, ge=0)
    fatConPontaInjetadoFaturado:    Optional[float] = Field(None, ge=0)
    fatConFPontaInjetadoRegistrado: Optional[float] = Field(None, ge=0)
    fatConFPontaInjetadoFaturado:   Optional[float] = Field(None, ge=0)

    # Valores em R$
    fatConPontaValorReais:       Optional[float] = Field(None, ge=0)
    fatConFPontaIndValorReais:   Optional[float] = Field(None, ge=0)
    fatDemPontaValorReais:       Optional[float] = Field(None, ge=0)
    fatDemFPontaIndValorReais:   Optional[float] = Field(None, ge=0)
    fatDemPontaExcValorReais:    Optional[float] = Field(None, ge=0)
    fatDemFPontaExcValorReais:   Optional[float] = Field(None, ge=0)

    # Desconto fio B (TUSD)
    fatConCreditoTUSDPontaValorReais:  Optional[float] = Field(None, ge=0)
    fatConCreditoTUSDFPontaValorReais: Optional[float] = Field(None, ge=0)
    fatBeneficioTarifarioBrutoValorReais: Optional[float] = Field(None, ge=0)
    fatBeneficioLiquidoValorReais:     Optional[float] = Field(None, ge=0)

    @model_validator(mode="after")
    def _mt_tem_subgrupo_a(self) -> "FaturaMT":
        sub = (self.cadSubGrupoCod or "").upper()
        if sub and sub.startswith("B"):
            raise ValueError(
                f"FaturaMT recebeu subgrupo grupo B ({sub}) — use FaturaBT"
            )
        return self

    @model_validator(mode="after")
    def _demanda_faturada_vs_contratada(self) -> "FaturaMT":
        """Demanda faturada não pode ser > 2x a contratada (sanidade)."""
        cont = self.fatDemContratadaFPonta or 0.0
        fat  = self.fatDemFPontaIndFaturada or 0.0
        if cont > 0 and fat > cont * 2:
            raise ValueError(
                f"fatDemFPontaIndFaturada ({fat}) > 2× contratada ({cont}) — verificar"
            )
        return self


# ---------------------------------------------------------------------------
# Resultado de validação (soft — registra avisos sem lançar exceção)
# ---------------------------------------------------------------------------

@dataclass
class ResultadoValidacao:
    valido:   bool
    schema:   Optional[FaturaMT | FaturaBT] = None
    erros:    list[str] = field(default_factory=list)
    avisos:   list[str] = field(default_factory=list)
    carimbo:  str = ""
    instalacao: str = ""


def validar_linha_ocr(
    row: dict[str, Any],
    tipo: str = "MT",
) -> ResultadoValidacao:
    """
    Valida uma linha do XLSX OCR.

    Args:
        row:  dict com os campos da linha (chaves = headers do XLSX)
        tipo: "MT" ou "BT"

    Returns:
        ResultadoValidacao com .valido, .erros, .avisos, .schema
    """
    from pydantic import ValidationError

    carimbo    = str(row.get("fatCarimbo") or "")
    instalacao = str(row.get("Instalacao") or row.get("Instalação") or "")

    # Normaliza chave com acento
    if "Instalação" in row and "Instalacao" not in row:
        row = {**row, "Instalacao": row["Instalação"]}

    SchemaClass = FaturaMT if tipo.upper() == "MT" else FaturaBT
    avisos: list[str] = []

    # Aviso não-bloqueante: subgrupo desconhecido
    sub = str(row.get("cadSubGrupoCod") or "")
    if sub and sub not in _SUBGRUPOS_VALIDOS:
        avisos.append(f"cadSubGrupoCod desconhecido: '{sub}'")

    try:
        schema = SchemaClass.model_validate(row)
        return ResultadoValidacao(
            valido=True, schema=schema,
            avisos=avisos, carimbo=carimbo, instalacao=instalacao,
        )
    except ValidationError as exc:
        erros = [f"{e['loc'][0]}: {e['msg']}" for e in exc.errors()]
        return ResultadoValidacao(
            valido=False, erros=erros,
            avisos=avisos, carimbo=carimbo, instalacao=instalacao,
        )
