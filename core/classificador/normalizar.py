"""
normalizar.py — Mapeamento de nomes de provedores para nomes canônicos do CATALOG.

Regras:
- Correspondência por nome exato primeiro.
- Fallback por substring/sigla quando o nome muda entre exportações.
- Distribuidoras pequenas não listadas no CATALOG → "PEQUENAS".
- Casos sem mapeamento seguro → None (revisão necessária).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Tabela de mapeamento: nome_original → (nome_canonico, regra)
# ---------------------------------------------------------------------------
# Regras:
#   exact   — nome igual ao campo "provedor" no treino_watcher
#   alias   — variação conhecida do mesmo ente
#   sigla   — identificado pela sigla/nome histórico
#   pequena — cooperativa/distribuidora local processada como BT pelo pipeline_demei_bt
#   revisao — sem mapeamento seguro, requer revisão manual

_MAPA_RAW: list[tuple[str, str, str]] = [
    # ---------- Grandes distribuidoras ----------
    ("CEMIG",                                           "CEMIG",                    "exact"),
    ("CELESC",                                          "CELESC",                   "exact"),
    ("COPEL",                                           "COPEL",                    "exact"),
    ("LIGHT",                                           "LIGHT",                    "exact"),
    ("CHESP",                                           "CHESP",                    "exact"),
    # ---------- ENEL ----------
    ("ENEL SP - ELETROPAULO",                           "ENEL",                     "alias"),
    ("ENEL CEARA - COELCE",                             "ENEL",                     "alias"),
    ("ENEL RJ - AMPLA",                                 "ENEL",                     "alias"),
    # ---------- CPFL ----------
    ("CPFL - PAULISTA",                                 "CPFL",                     "alias"),
    ("CPFL - COMPANHIA PIRATININGA",                    "CPFL",                     "alias"),
    ("CPFL SANTA CRUZ - CLFSC",                         "CPFL",                     "alias"),
    ("CPFL SANTA CRUZ - CSPE CPFL SUL PAULISTA",        "CPFL",                     "alias"),
    # ---------- RGE ----------
    ("RGE",                                             "RGE",                      "exact"),
    ("RGE - ANTIGA AES SUL",                            "RGE",                      "alias"),
    # ---------- EDP ----------
    ("EDP SP - BANDEIRANTE",                            "EDP",                      "alias"),
    ("EDP ES - ESCELSA",                                "EDP ES",                   "alias"),
    # ---------- Neoenergia ----------
    ("NEOENERGIA BAHIA - COELBA",                       "NEOENERGIA/COELBA",        "alias"),
    ("NEOENERGIA PERNAMBUCO - CELPE",                   "NEOENERGIA/CELPE",         "alias"),
    ("NEOENERGIA RIO GRANDE DO NORTE - COSERN",         "NEOENERGIA/COSERN",        "alias"),
    ("NEOENERGIA ELEKTRO",                              "NEOENERGIA/ELEKTRO",       "exact"),
    ("NEOENERGIA BRASILIA - CEB",                       "NEOENERGIA/CEB",           "alias"),
    # ---------- Equatorial ----------
    ("EQUATORIAL GOIÁS - CELG",                         "EQUATORIAL/GOIAS",         "alias"),
    ("EQUATORIAL PARA - CELPA",                         "EQUATORIAL/PARA",          "alias"),
    ("EQUATORIAL MARANHÃO - CEMAR",                     "EQUATORIAL/MARANHÃO",      "alias"),
    ("EQUATORIAL PIAUÍ - CEPISA",                       "EQUATORIAL/PIAUI",         "alias"),
    ("EQUATORIAL ALAGOAS - CEAL",                       "EQUATORIAL/ALAGOAS",       "alias"),
    ("EQUATORIAL CEEE",                                 "EQUATORIAL/CEEE - RS",     "alias"),
    ("EQUATORIAL AMAPA - CEA",                          "EQUATORIAL/AMAPÁ",         "alias"),
    # ---------- Energisa ----------
    ("EMT - ENERGISA MATO GROSSO - CEMAT",              "ENERGISA/MATO GROSSO",     "sigla"),
    ("EMS - MATO GROSSO DO SUL - ENERSUL",              "ENERGISA/MATO GROSSO DO SUL", "sigla"),
    ("ESS ENERGISA SUL SUDESTE - CAIUA",                "ENERGISA/SUL SUDESTE",     "sigla"),
    ("ETO ENERGISA TOCANTINS - CELTINS",                "ENERGISA/TOCANTINS",       "sigla"),
    ("ESE - ENERGISA SERGIPE",                          "ENERGISA/SERGIPE",         "sigla"),
    ("CERON - ENERGISA RONDONIA",                       "ENERGISA/RONDONIA",        "sigla"),
    ("EMR - ENERGISA MINAS RIO (ANTIGA EMG)",           "ENERGISA/MINAS RIO",       "sigla"),
    ("EMR - ENERGISA MINAS RIO (ANTIGA ENF)",           "ENERGISA/MINAS RIO",       "sigla"),
    ("EPB - ENERGISA PARAÍBA",                          "ENERGISA/PARAIBA",         "sigla"),
    ("EPB - ENERGISA PARAÍBA - ANTIGA EBO ENERGISA BORBOREMA", "ENERGISA/PARAIBA", "sigla"),
    ("ENERGISA ACRE - ELETROACRE",                      "ENERGISA/ACRE",            "alias"),
    # ---------- Amazonas ----------
    ("AME - AMAZONAS ENERGIA",                          "AMAZONAS",                 "sigla"),
    # ---------- Pequenas distribuidoras / cooperativas ----------
    ("NOVA PALMA",                                      "PEQUENAS",                 "pequena"),
    ("SANTA MARIA - ELFSM",                             "PEQUENAS",                 "pequena"),
    ("DEMEI IJUÍ",                                      "PEQUENAS",                 "pequena"),
    ("CERMISSOES",                                      "PEQUENAS",                 "pequena"),
    ("BRACO DO NORTE - CERBRAANORTE",                   "PEQUENAS",                 "pequena"),
    ("COOPERALIANÇA",                                   "PEQUENAS",                 "pequena"),
    ("COOPERZEM",                                       "PEQUENAS",                 "pequena"),
    ("COOPER COCAL",                                    "PEQUENAS",                 "pequena"),
    ("COORSEL",                                         "PEQUENAS",                 "pequena"),
    ("CERAÇA",                                          "PEQUENAS",                 "pequena"),
    ("HIDROPAN",                                        "PEQUENAS",                 "pequena"),
    ("CEMIRIM",                                         "PEQUENAS",                 "pequena"),
    ("COOPERSUL",                                       "PEQUENAS",                 "pequena"),
    ("CERGRAL",                                         "PEQUENAS",                 "pequena"),
    ("CERFOX",                                          "PEQUENAS",                 "pequena"),
    ("COCEL",                                           "PEQUENAS",                 "pequena"),
    ("CERAL",                                           "PEQUENAS",                 "pequena"),
    ("CERIPA",                                          "PEQUENAS",                 "pequena"),
    ("PACTO ENERGIA - FORCEL",                          "PEQUENAS",                 "pequena"),
    ("CEGERO",                                          "PEQUENAS",                 "pequena"),
    ("EFLUL",                                           "PEQUENAS",                 "pequena"),
    ("COOPERA",                                         "PEQUENAS",                 "pequena"),
    ("CERPALO",                                         "PEQUENAS",                 "pequena"),
    ("CEJAMA",                                          "PEQUENAS",                 "pequena"),
    ("CERGAPA",                                         "PEQUENAS",                 "pequena"),
    ("CERMOFUL",                                        "PEQUENAS",                 "pequena"),
    ("CERSUL",                                          "PEQUENAS",                 "pequena"),
    ("CERTEL",                                          "PEQUENAS",                 "pequena"),
    ("CEPRAG",                                          "PEQUENAS",                 "pequena"),
    ("ELETROCAR",                                       "PEQUENAS",                 "pequena"),
    # ---------- Revisão necessária ----------
    ("RORAIMA ENERGIA - BVE",                           "REVISAO",                  "revisao"),
    ("SULGIPE",                                         "REVISAO",                  "revisao"),
    ("CELETRO",                                         "REVISAO",                  "revisao"),
    ("DCELT ENERGIA - IGUAÇU",                          "REVISAO",                  "revisao"),
    ("MUX ENERGIA",                                     "REVISAO",                  "revisao"),
    ("DMEPC",                                           "REVISAO",                  "revisao"),
    ("CASTRO DIS",                                      "REVISAO",                  "revisao"),
]

# Índice rápido: nome original → (canonico, regra)
_INDICE: dict[str, tuple[str, str]] = {orig: (canon, regra) for orig, canon, regra in _MAPA_RAW}


def normalizar(nome_original: str) -> tuple[str | None, str | None]:
    """Retorna (nome_canonico, regra) ou (None, None) se desconhecido."""
    resultado = _INDICE.get(nome_original)
    if resultado:
        return resultado

    # Fallback: normalização Unicode pode ter causado divergência (acentos)
    import unicodedata
    nome_norm = unicodedata.normalize("NFC", nome_original.strip())
    for orig, (canon, regra) in _INDICE.items():
        if unicodedata.normalize("NFC", orig) == nome_norm:
            return canon, regra

    return None, None


def tabela_mapeamento() -> list[dict]:
    """Retorna a tabela completa para auditoria."""
    return [
        {"nome_original": orig, "nome_canonico": canon, "regra": regra}
        for orig, canon, regra in _MAPA_RAW
    ]
