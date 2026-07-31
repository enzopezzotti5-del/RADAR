"""
identificar_concessionaria.py — Identifica a distribuidora a partir do conteúdo do PDF.

Hierarquia de evidências:
  Determinante (confiança 0.99): CNPJ exato da distribuidora
  Forte       (confiança 0.90): Razão social exata / nome jurídico no cabeçalho
  Complementar (confiança 0.75): Nome comercial, marca com estado
  Alias       (confiança 0.65): Nome histórico, sigla, subsidiária

O caminho do arquivo é armazenado como metadado mas NUNCA decide a concessionária.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class ResultadoConcessionaria:
    canonica: str | None
    confianca: float
    metodo: str        # "cnpj" | "razao_social" | "nome_comercial" | "alias" | "desconhecida"
    evidencia: str


# ---------------------------------------------------------------------------
# Tier 1 — Determinante: CNPJ da distribuidora
# Normalizado para 14 dígitos sem formatação.
# ---------------------------------------------------------------------------

_CNPJ_DIST: dict[str, str] = {
    # CEMIG
    "06981180000116": "CEMIG",
    # CELESC
    "08336783000190": "CELESC",
    # COPEL
    "04368898000106": "COPEL",
    # ENEL (SP / CE / RJ / GO)
    "04925346000146": "ENEL",   # Eletropaulo SP
    "07047251000170": "ENEL",   # Coelce CE
    "31597143000111": "ENEL",   # Ampla RJ (CNPJ antigo)
    "33050071000158": "ENEL",   # Ampla RJ (CNPJ vigente — Ampla Energia e Serviços S.A.)
    # LIGHT
    "60444437000146": "LIGHT",
    # CPFL
    "33050196000188": "CPFL",   # CPFL Paulista
    "60182940000155": "CPFL",   # CPFL Piratininga
    "53859112000169": "CPFL",   # CPFL Santa Cruz
    "04172213000151": "CPFL",   # CPFL Leste Paulista
    "43073394000133": "CPFL",   # CPFL Energia (holding)
    # RGE
    "02016440000162": "RGE",
    # EDP
    "02302100000106": "EDP",    # EDP SP Bandeirante
    "28152650000171": "EDP ES", # EDP ES Escelsa
    # Neoenergia
    "15139629000194": "NEOENERGIA/COELBA",
    "10835932000108": "NEOENERGIA/CELPE",
    "08324196000181": "NEOENERGIA/COSERN",
    "02328280000197": "NEOENERGIA/ELEKTRO",
    "00537027000101": "NEOENERGIA/CEB",
    "07522669000192": "NEOENERGIA/CEB",  # CEB Distribuição (NF3e)
    # Equatorial
    "01543032000172": "EQUATORIAL/GOIAS",
    "01093836000133": "EQUATORIAL/PARA",
    "06272793000184": "EQUATORIAL/MARANHÃO",
    "06840748000189": "EQUATORIAL/PIAUI",
    "15130614000159": "EQUATORIAL/ALAGOAS",
    "05546584000138": "EQUATORIAL/CEEE - RS",
    "01235245000174": "EQUATORIAL/AMAPÁ",
    # Energisa
    "03467321000199": "ENERGISA/MATO GROSSO",
    "15413826000150": "ENERGISA/MATO GROSSO DO SUL",
    "05978420000101": "ENERGISA/SUL SUDESTE",
    "07282377000120": "ENERGISA/SUL SUDESTE",   # variante
    "12272084000160": "ENERGISA/SERGIPE",
    "13017462000163": "ENERGISA/SERGIPE",       # variante
    "01614076000104": "ENERGISA/PARAIBA",
    "05622677000107": "ENERGISA/RONDONIA",
    "25086034000171": "ENERGISA/TOCANTINS",
    "05974178000137": "ENERGISA/TOCANTINS",     # variante
    "19527639000158": "ENERGISA/MINAS RIO",
    "06980768000151": "ENERGISA/ACRE",
    # CHESP
    "01377555000110": "CHESP",
    # Amazonas
    "02341467000120": "AMAZONAS",
    # CEEE (legado, antes da Equatorial)
    "08467115000100": "EQUATORIAL/CEEE - RS",
}

_RE_CNPJ = re.compile(r"\b(\d{2})\.?(\d{3})\.?(\d{3})[/\s](\d{4})-?(\d{2})\b")

# Sequências de 20+ dígitos (chave NF3e, código de barras, etc.)
# — usadas para extrair CNPJ como substring quando não está formatado
_RE_DIGITOS_LONGOS = re.compile(r"\d{20,}")


def _normalizar_cnpj(raw: str) -> str:
    """Remove formatação, retorna 14 dígitos."""
    return re.sub(r"\D", "", raw)


def _buscar_cnpj_dist(texto: str) -> tuple[str | None, str]:
    """Procura CNPJ de distribuidora no texto. Retorna (canonica, evidencia).

    Busca em:
    1. CNPJs formatados (XX.XXX.XXX/XXXX-XX)
    2. Chave de acesso NF3e (44 dígitos) — CNPJ emitente nos bytes 6-19
    """
    # 1. CNPJs formatados
    for m in _RE_CNPJ.finditer(texto):
        digs = "".join(m.groups())
        if digs in _CNPJ_DIST:
            return _CNPJ_DIST[digs], f"CNPJ {m.group(0)}"
    # 2. Sequências longas de dígitos (chave NF3e=44d, código de barras)
    # NF3e: cUF(2)+AAMM(4)+CNPJ(14)+... → CNPJ sempre em [6:20]
    # Varrer janelas de 14 dígitos nas primeiras 24 posições para evitar falsos positivos
    for m in _RE_DIGITOS_LONGOS.finditer(texto):
        seq = m.group(0)
        max_start = min(len(seq) - 14, 24)
        for start in range(max_start + 1):
            substr = seq[start: start + 14]
            if substr in _CNPJ_DIST:
                return _CNPJ_DIST[substr], f"chave digitos CNPJ {substr}"
    return None, ""


# ---------------------------------------------------------------------------
# Tier 2 — Forte: razão social / nome jurídico
# ---------------------------------------------------------------------------

_RAZAO_SOCIAL: list[tuple[re.Pattern, str]] = [
    # CEMIG
    (re.compile(r"CEMIG\s+DISTRIBUI[CÇ][AÃ]O\s+S\.?A\.?", re.I), "CEMIG"),
    (re.compile(r"CEMIG\s+DISTRIBUI[CÇ][AÃ]O", re.I), "CEMIG"),
    # CELESC
    (re.compile(r"CELESC\s+DISTRIBUI[CÇ][AÃ]O\s+S\.?A\.?", re.I), "CELESC"),
    # COPEL
    (re.compile(r"COPEL\s+DISTRIBUI[CÇ][AÃ]O\s+S\.?A\.?", re.I), "COPEL"),
    # LIGHT
    (re.compile(r"LIGHT\s+SERVI[CÇ]OS\s+DE\s+ELETRICIDADE\s+S\.?A\.?", re.I), "LIGHT"),
    # ENEL
    (re.compile(r"ENEL\s+DISTRIBUI[CÇ][AÃ]O\s+(?:SP|CE|RIO)", re.I), "ENEL"),
    (re.compile(r"ELETROPAULO\s+METROPOLITANA\s+ELETRICIDADE", re.I), "ENEL"),
    (re.compile(r"COMPANHIA\s+ENERG[EÉ]TICA\s+DO\s+CEAR[AÁ]", re.I), "ENEL"),
    (re.compile(r"AMPLA\s+ENERGIA\s+E\s+SERVI[CÇ]OS", re.I), "ENEL"),
    # CPFL
    (re.compile(r"COMPANHIA\s+PAULISTA\s+DE\s+FOR[CÇ]A\s+E\s+LUZ", re.I), "CPFL"),
    (re.compile(r"CPFL\s+PIRATININGA", re.I), "CPFL"),
    (re.compile(r"CPFL\s+SANTA\s+CRUZ", re.I), "CPFL"),
    # RGE
    (re.compile(r"RIO\s+GRANDE\s+ENERGIA\s+S\.?A\.?", re.I), "RGE"),
    # EDP
    (re.compile(r"EDP\s+SP\s+DISTRIB\w*\s+DE\s+ENERGIA", re.I), "EDP"),
    (re.compile(r"BANDEIRANTE\s+ENERGIA\s+S\.?A\.?", re.I), "EDP"),
    (re.compile(r"EDP\s+ES\s+DISTRIB\w*\s+DE\s+ENERGIA", re.I), "EDP ES"),
    (re.compile(r"ESCELSA\s+ESPÍRITO\s+SANTO\s+CENTR[AÁ]IS", re.I), "EDP ES"),
    # Neoenergia
    (re.compile(r"COMPANHIA\s+DE\s+ELETRICIDADE\s+DO\s+ESTADO\s+DA\s+BAHIA", re.I), "NEOENERGIA/COELBA"),
    (re.compile(r"COMPANHIA\s+ENERG[EÉ]TICA\s+DE\s+PERNAMBUCO", re.I), "NEOENERGIA/CELPE"),
    (re.compile(r"COMPANHIA\s+ENERG[EÉ]TICA\s+DO\s+RIO\s+GRANDE\s+DO\s+NORTE", re.I), "NEOENERGIA/COSERN"),
    (re.compile(r"NEOENERGIA\s+ELEKTRO", re.I), "NEOENERGIA/ELEKTRO"),
    (re.compile(r"NEOENERGIA\s+BRAS[IÍ]LIA|CEB\s+DISTRIBUI[CÇ][AÃ]O", re.I), "NEOENERGIA/CEB"),
    # Equatorial
    (re.compile(r"EQUATORIAL\s+GOI[AÁ]S\s+DISTRIBUI", re.I), "EQUATORIAL/GOIAS"),
    (re.compile(r"EQUATORIAL\s+PAR[AÁ]\s+DISTRIB", re.I), "EQUATORIAL/PARA"),
    (re.compile(r"EQUATORIAL\s+MARANH[AÃ]O\s+DISTRIB", re.I), "EQUATORIAL/MARANHÃO"),
    (re.compile(r"EQUATORIAL\s+PIAU[IÍ]\s+DISTRIB", re.I), "EQUATORIAL/PIAUI"),
    (re.compile(r"EQUATORIAL\s+ALAGOAS\s+DISTRIB", re.I), "EQUATORIAL/ALAGOAS"),
    (re.compile(r"EQUATORIAL\s+AMAPÁ\s+DISTRIB|EQUATORIAL\s+AMAPA\s+DISTRIB", re.I), "EQUATORIAL/AMAPÁ"),
    (re.compile(r"COMPANHIA\s+ESTADUAL\s+DE\s+DISTRIBUI[CÇ][AÃ]O\s+DE\s+ENERGIA", re.I), "EQUATORIAL/CEEE - RS"),
    # Energisa
    (re.compile(r"ENERGISA\s+MATO\s+GROSSO\s+-\s+DISTRIBUIDORA", re.I), "ENERGISA/MATO GROSSO"),
    (re.compile(r"ENERGISA\s+MATO\s+GROSSO\s+DO\s+SUL", re.I), "ENERGISA/MATO GROSSO DO SUL"),
    (re.compile(r"ENERGISA\s+SUL\s+SUDESTE|ESS\s+ENERGISA\s+SUL\s+SUDESTE", re.I), "ENERGISA/SUL SUDESTE"),
    (re.compile(r"ENERGISA\s+SERGIPE|ESE\s+-\s+ENERGISA\s+SERGIPE", re.I), "ENERGISA/SERGIPE"),
    (re.compile(r"ENERGISA\s+PARA[IÍ]BA|EPB\s+-\s+ENERGISA\s+PARA", re.I), "ENERGISA/PARAIBA"),
    (re.compile(r"ENERGISA\s+RON[DÔ]NIA|CERON\s+-\s+ENERGISA\s+RONDONIA", re.I), "ENERGISA/RONDONIA"),
    (re.compile(r"ENERGISA\s+TOCANTINS|ETO\s+ENERGISA\s+TOCANTINS", re.I), "ENERGISA/TOCANTINS"),
    (re.compile(r"ENERGISA\s+MINAS\s+RIO|EMR\s+-\s+ENERGISA\s+MINAS\s+RIO", re.I), "ENERGISA/MINAS RIO"),
    (re.compile(r"ENERGISA\s+ACRE|ELETROACRE", re.I), "ENERGISA/ACRE"),
    # CHESP
    (re.compile(r"COMPANHIA\s+HIDROEL[EÉ]TRICA\s+S[AÃ]O\s+PATR[IÍ]CIO", re.I), "CHESP"),
    # Amazonas
    (re.compile(r"AMAZONAS\s+ENERGIA|AME\s+-\s+AMAZONAS\s+ENERGIA", re.I), "AMAZONAS"),
]


# ---------------------------------------------------------------------------
# Tier 3 — Complementar: nome comercial, marca, sigla
# ---------------------------------------------------------------------------

_NOME_COMERCIAL: list[tuple[re.Pattern, str]] = [
    # CEMIG
    (re.compile(r"\bCEMIG\b", re.I), "CEMIG"),
    # CELESC
    (re.compile(r"\bCELESC\b", re.I), "CELESC"),
    # COPEL
    (re.compile(r"\bCOPEL\b", re.I), "COPEL"),
    # LIGHT
    (re.compile(r"\bLIGHT\b\s+(?:SERVI|ENER|ELÉTRIC)", re.I), "LIGHT"),
    # ENEL
    (re.compile(r"\bENEL\b\s+(?:SP|CE|CE[AÁ]R|RJ|RIO|DISTRIBU|DISTRIB|BRASIL)", re.I), "ENEL"),
    # CPFL
    (re.compile(r"\bCPFL\b", re.I), "CPFL"),
    # RGE
    (re.compile(r"\bRGE\b(?:\s+-\s+ANTIGA\s+AES\s+SUL)?", re.I), "RGE"),
    # EDP SP
    (re.compile(r"\bEDP\s+SP\b", re.I), "EDP"),
    # EDP ES
    (re.compile(r"\bEDP\s+ES\b", re.I), "EDP ES"),
    # Neoenergia
    (re.compile(r"COELBA|NEOENERGIA\s+BAHIA|NEOENERGIA\s+BA", re.I), "NEOENERGIA/COELBA"),
    (re.compile(r"CELPE|NEOENERGIA\s+PERNAMBUCO|NEOENERGIA\s+PE", re.I), "NEOENERGIA/CELPE"),
    (re.compile(r"COSERN|NEOENERGIA\s+RIO\s+GRANDE\s+DO\s+NORTE|NEOENERGIA\s+RN", re.I), "NEOENERGIA/COSERN"),
    (re.compile(r"NEOENERGIA\s+ELEKTRO|ELEKTRO", re.I), "NEOENERGIA/ELEKTRO"),
    (re.compile(r"NEOENERGIA\s+BRAS[IÍ]LIA|NEOENERGIA\s+CEB|\bCEB\s+DISTRIB", re.I), "NEOENERGIA/CEB"),
    # Equatorial
    (re.compile(r"EQUATORIAL\s+GOI[AÁ]S|CELG[-\s]D", re.I), "EQUATORIAL/GOIAS"),
    (re.compile(r"EQUATORIAL\s+PAR[AÁ]|CELPA\b", re.I), "EQUATORIAL/PARA"),
    (re.compile(r"EQUATORIAL\s+MARANH[AÃ]O|CEMAR\b", re.I), "EQUATORIAL/MARANHÃO"),
    (re.compile(r"EQUATORIAL\s+PIAU[IÍ]|CEPISA\b", re.I), "EQUATORIAL/PIAUI"),
    (re.compile(r"EQUATORIAL\s+ALAGOAS|CEAL\b", re.I), "EQUATORIAL/ALAGOAS"),
    (re.compile(r"EQUATORIAL\s+AMAPÁ|EQUATORIAL\s+AMAPA|\bCEA\b.*AMAPÁ|EQUATORIAL\s+AM\b", re.I), "EQUATORIAL/AMAPÁ"),
    (re.compile(r"EQUATORIAL\s+CEEE|CEEE[-\s]RS", re.I), "EQUATORIAL/CEEE - RS"),
    # Energisa
    (re.compile(r"ENERGISA\s+MT\b|EMT\b.*ENERGISA", re.I), "ENERGISA/MATO GROSSO"),
    (re.compile(r"ENERGISA\s+MS\b|EMS\b.*ENERSUL|ENERSUL\b", re.I), "ENERGISA/MATO GROSSO DO SUL"),
    (re.compile(r"ENERGISA\s+SUL|CAIUÁ", re.I), "ENERGISA/SUL SUDESTE"),
    (re.compile(r"ENERGISA\s+SE\b|ESE\b.*ENERGISA", re.I), "ENERGISA/SERGIPE"),
    (re.compile(r"ENERGISA\s+PB\b|EPB\b.*ENERGISA", re.I), "ENERGISA/PARAIBA"),
    (re.compile(r"ENERGISA\s+RO\b|CERON\b", re.I), "ENERGISA/RONDONIA"),
    (re.compile(r"ENERGISA\s+TO\b|CELTINS\b", re.I), "ENERGISA/TOCANTINS"),
    (re.compile(r"ENERGISA\s+MR\b|EMR\b.*ENERGISA", re.I), "ENERGISA/MINAS RIO"),
    (re.compile(r"ENERGISA\s+AC\b|ELETROACRE\b", re.I), "ENERGISA/ACRE"),
    # CHESP
    (re.compile(r"\bCHESP\b", re.I), "CHESP"),
    # Amazonas
    (re.compile(r"AMAZONAS\s+ENERGIA\b|AME\s+AMAZONAS", re.I), "AMAZONAS"),
]

# Cooperativas/pequenas: qualquer match → PEQUENAS
_PEQUENAS_CNPJS = {
    "27485069000109", "86532348000145", "97081434000103",
    "86512670000102",
}
_PEQUENAS_NOMES: list[re.Pattern] = [
    re.compile(r"\b(?:NOVA\s+PALMA|ELFSM|DEMEI|CERMISSOES|COOPERALIAN[CÇ]A|COOPERZEM|COOPER\s+COCAL"
               r"|COORSEL|CERA[CÇ]A|HIDROPAN|CEMIRIM|COOPERSUL|CERGRAL|CERFOX|COCEL|CERAL|CERIPA"
               r"|FORCEL|CEGERO|EFLUL|COOPERA|CERPALO|CEJAMA|CERGAPA|CERMOFUL|CERSUL|CERTEL"
               r"|CEPRAG|ELETROCAR|CERBRAANORTE|CEFLEX)\b", re.I),
]


def _normalizar_texto(texto: str) -> str:
    """NFC + colapsa whitespace."""
    t = unicodedata.normalize("NFC", texto)
    return re.sub(r"\s+", " ", t)


def identificar(texto: str) -> ResultadoConcessionaria:
    """Identifica a concessionária a partir do texto do PDF.

    Nunca usa o nome da pasta ou qualquer informação externa.
    Retorna ResultadoConcessionaria com canonica=None se desconhecida.
    """
    t = _normalizar_texto(texto)

    # Tier 1 — CNPJ
    canonica, evidencia = _buscar_cnpj_dist(t)
    if canonica:
        return ResultadoConcessionaria(canonica, 0.99, "cnpj", evidencia)

    # Verificar CNPJs de pequenas cooperativas
    for m in _RE_CNPJ.finditer(t):
        digs = _normalizar_cnpj(m.group(0))
        if digs in _PEQUENAS_CNPJS:
            return ResultadoConcessionaria("PEQUENAS", 0.90, "cnpj", f"CNPJ {m.group(0)} (cooperativa)")

    # Tier 2 — Razão social
    for pat, canon in _RAZAO_SOCIAL:
        m = pat.search(t)
        if m:
            return ResultadoConcessionaria(canon, 0.90, "razao_social", m.group(0)[:80])

    # Pequenas por nome
    for pat in _PEQUENAS_NOMES:
        m = pat.search(t)
        if m:
            return ResultadoConcessionaria("PEQUENAS", 0.80, "nome_comercial", m.group(0)[:60])

    # Tier 3 — Nome comercial
    for pat, canon in _NOME_COMERCIAL:
        m = pat.search(t)
        if m:
            return ResultadoConcessionaria(canon, 0.75, "nome_comercial", m.group(0)[:60])

    return ResultadoConcessionaria(None, 0.0, "desconhecida", "nenhuma evidência encontrada")
