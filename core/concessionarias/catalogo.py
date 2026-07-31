"""
catalogo.py — Registro tipado de concessionárias. Fonte canônica de verdade.

Dependência: este módulo NÃO importa de scripts/infra/watcher.py.
O watcher.py pode derivar seu CATALOG via gerar_catalog_legacy().

32 concessionárias operacionais, 64 combinações BT/MT.
"""
from __future__ import annotations

from pathlib import Path

from .modelos import (
    ConcessionariaSpec,
    EstadoImplementacao,
    GrupoTensao,
    PipelineSpec,
)

ROOT = Path(__file__).resolve().parents[2]
PL = ROOT / "core" / "pipelines"


def _s(
    script: str | None,
    identificador: str | None = None,
    args: tuple[str, ...] = (),
    pasta: bool = False,
    session: bool = False,
    retomar: bool = False,
    dry_run: bool = False,
    auditoria: bool = False,
    atualiza_indice: bool = False,
    motivo: str = "",
) -> PipelineSpec:
    """Atalho para PipelineSpec suportado."""
    scr = PL / script if script else None
    return PipelineSpec(
        EstadoImplementacao.SUPORTADO,
        scr,
        identificador,
        args,
        pasta,
        session,
        retomar,
        dry_run,
        auditoria,
        atualiza_indice,
        motivo,
    )


def _n(motivo: str = "não implementado") -> PipelineSpec:
    return PipelineSpec(EstadoImplementacao.NAO_IMPLEMENTADO, motivo=motivo)


# ---------------------------------------------------------------------------
# REGISTRO — 32 concessionárias
# ---------------------------------------------------------------------------

REGISTRO: dict[str, ConcessionariaSpec] = {

    "ENEL": ConcessionariaSpec(
        "ENEL", "ENEL (SP / CE / RJ)",
        ("ENEL", "ELETROPAULO", "COELCE", "AMPLA"),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "enel"),
            GrupoTensao.MT: _s("pipeline_enel_mt_lote.py", "enel_mt", pasta=True),
        },
    ),

    "CELESC": ConcessionariaSpec(
        "CELESC", "CELESC Distribuição",
        ("CELESC", "CELESC DISTRIBUIÇÃO"),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "celesc"),
            GrupoTensao.MT: _s("pipeline_celesc_mt.py", "celesc_mt", pasta=True),
        },
    ),

    # CEMIG: pipeline_cemig processa de DOWNLOAD CEMIG com PDFs já carimbed (BB_*.pdf).
    # Não aceita --pasta de staging do watcher_v2. PDFs de Faturas_V2 precisam de
    # pré-carimbo antes de entrar no pipeline. Veja Investigar/CEMIG_Requer_Pre_Carimbo.
    "CEMIG": ConcessionariaSpec(
        "CEMIG", "CEMIG Distribuição",
        ("CEMIG", "CEMIG DISTRIBUIÇÃO"),
        {
            GrupoTensao.BT: _s("pipeline_cemig.py", args=("--so-bt",)),
            GrupoTensao.MT: _s("pipeline_cemig.py", args=("--so-mt",)),
        },
    ),

    "COPEL": ConcessionariaSpec(
        "COPEL", "Copel Distribuição",
        ("COPEL",),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "copel_bt"),
            GrupoTensao.MT: _s("pipeline_copel_mt.py", "copel_mt", pasta=True),
        },
    ),

    "CPFL": ConcessionariaSpec(
        "CPFL", "CPFL (Paulista / Piratininga / Santa Cruz)",
        ("CPFL", "CPFL PAULISTA", "CPFL PIRATININGA", "CPFL SANTA CRUZ"),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "cpfl"),
            GrupoTensao.MT: _s("pipeline_cpfl_mt.py", "cpfl_mt", pasta=True),
        },
    ),

    "EDP": ConcessionariaSpec(
        "EDP", "EDP SP Bandeirante",
        ("EDP", "EDP SP", "BANDEIRANTE"),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "edp_sp"),
            GrupoTensao.MT: _n("OCR e pipeline MT inexistentes"),
        },
    ),

    "EDP ES": ConcessionariaSpec(
        "EDP ES", "EDP ES Escelsa",
        ("EDP ES", "ESCELSA"),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "edp_es"),
            GrupoTensao.MT: _n("OCR e pipeline MT inexistentes"),
        },
    ),

    "RGE": ConcessionariaSpec(
        "RGE", "RGE / Antiga AES Sul",
        ("RGE", "RGE SUL"),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "rge_sul"),
            GrupoTensao.MT: _n("pipeline MT inexistente"),
        },
    ),

    "LIGHT": ConcessionariaSpec(
        "LIGHT", "Light Serviços de Eletricidade",
        ("LIGHT",),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "light"),
            GrupoTensao.MT: _s("pipeline_light_mt.py", "light_mt", pasta=True),
        },
    ),

    "NEOENERGIA/COELBA": ConcessionariaSpec(
        "NEOENERGIA/COELBA", "Neoenergia Bahia - COELBA",
        ("NEOENERGIA/COELBA", "COELBA", "NEOENERGIA BAHIA"),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "neo_coelba"),
            GrupoTensao.MT: _s("pipeline_neoenergia_bahia.py", "neo_coelba_mt",
                                args=("--tipo", "mt"), pasta=True),
        },
    ),

    "NEOENERGIA/CELPE": ConcessionariaSpec(
        "NEOENERGIA/CELPE", "Neoenergia Pernambuco - CELPE",
        ("NEOENERGIA/CELPE", "CELPE", "NEOENERGIA PERNAMBUCO"),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "neo_celpe"),
            GrupoTensao.MT: _s("pipeline_neoenergia_pernambuco.py", "neo_celpe_mt",
                                args=("--tipo", "mt"), pasta=True),
        },
    ),

    "NEOENERGIA/COSERN": ConcessionariaSpec(
        "NEOENERGIA/COSERN", "Neoenergia RN - COSERN",
        ("NEOENERGIA/COSERN", "COSERN", "NEOENERGIA RIO GRANDE DO NORTE"),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "neo_cosorn"),
            GrupoTensao.MT: _s("pipeline_neoenergia_cosern.py", "neo_cosern_mt",
                                args=("--tipo", "mt")),
        },
    ),

    "NEOENERGIA/ELEKTRO": ConcessionariaSpec(
        "NEOENERGIA/ELEKTRO", "Neoenergia Elektro",
        ("NEOENERGIA/ELEKTRO", "ELEKTRO"),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "neo_elektro"),
            GrupoTensao.MT: _s("pipeline_neoenergia_elektro.py", "neo_elektro_mt",
                                args=("--tipo", "mt"), pasta=True),
        },
    ),

    "NEOENERGIA/CEB": ConcessionariaSpec(
        "NEOENERGIA/CEB", "Neoenergia Brasília - CEB",
        ("NEOENERGIA/CEB", "CEB DISTRIBUIÇÃO", "CEB"),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "neo_ceb"),
            GrupoTensao.MT: _n("pipeline MT inexistente"),
        },
    ),

    "EQUATORIAL/GOIAS": ConcessionariaSpec(
        "EQUATORIAL/GOIAS", "Equatorial Goiás - CELG-D",
        ("EQUATORIAL/GOIAS", "CELG-D", "CELG"),
        {
            GrupoTensao.BT: _s("pipeline_equatorial_go.py", "eq_go",
                                args=("--tipo-tensao", "bt"), pasta=True, session=True,
                                retomar=True, auditoria=True, atualiza_indice=True),
            GrupoTensao.MT: _s("pipeline_equatorial_go.py", "eq_go_mt",
                                args=("--tipo-tensao", "mt"), pasta=True, session=True,
                                retomar=True, auditoria=True, atualiza_indice=True),
        },
    ),

    "EQUATORIAL/PIAUI": ConcessionariaSpec(
        "EQUATORIAL/PIAUI", "Equatorial Piauí - CEPISA",
        ("EQUATORIAL/PIAUI", "CEPISA"),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "eq_pi"),
            GrupoTensao.MT: _s("pipeline_equatorial_pi_mt.py", "eq_pi_mt"),
        },
    ),

    "EQUATORIAL/PARA": ConcessionariaSpec(
        "EQUATORIAL/PARA", "Equatorial Pará - CELPA",
        ("EQUATORIAL/PARA", "CELPA"),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "eq_pa"),
            GrupoTensao.MT: _s("pipeline_equatorial_pa_bt.py", "eq_pa_mt", pasta=True),
        },
    ),

    "EQUATORIAL/ALAGOAS": ConcessionariaSpec(
        "EQUATORIAL/ALAGOAS", "Equatorial Alagoas - CEAL",
        ("EQUATORIAL/ALAGOAS", "CEAL"),
        {
            GrupoTensao.BT: _s("pipeline_equatorial_al_bt.py"),
            GrupoTensao.MT: _n("pipeline MT inexistente"),
        },
    ),

    "EQUATORIAL/MARANHÃO": ConcessionariaSpec(
        "EQUATORIAL/MARANHÃO", "Equatorial Maranhão - CEMAR",
        ("EQUATORIAL/MARANHÃO", "CEMAR"),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "eq_ma"),
            GrupoTensao.MT: _s("pipeline_equatorial_ma_mt.py", "eq_ma_mt", pasta=True),
        },
    ),

    "EQUATORIAL/AMAPÁ": ConcessionariaSpec(
        "EQUATORIAL/AMAPÁ", "Equatorial Amapá - CEA",
        ("EQUATORIAL/AMAPÁ", "CEA", "EQUATORIAL AMAPA"),
        {
            GrupoTensao.BT: _s("pipeline_equatorial_ap_bt.py", "eq_ap"),
            GrupoTensao.MT: _n("pipeline MT inexistente"),
        },
    ),

    "EQUATORIAL/CEEE - RS": ConcessionariaSpec(
        "EQUATORIAL/CEEE - RS", "Equatorial CEEE-RS",
        ("EQUATORIAL/CEEE - RS", "CEEE-RS", "CEEE"),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "ceee"),
            GrupoTensao.MT: _n("pipeline MT inexistente"),
        },
    ),

    "ENERGISA/MATO GROSSO": ConcessionariaSpec(
        "ENERGISA/MATO GROSSO", "Energisa Mato Grosso - CEMAT",
        ("ENERGISA/MATO GROSSO", "EMT", "CEMAT"),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "energisa_mt"),
            GrupoTensao.MT: _s("pipeline_energisa_mt.py", "energisa_mt", pasta=True),
        },
    ),

    "ENERGISA/MATO GROSSO DO SUL": ConcessionariaSpec(
        "ENERGISA/MATO GROSSO DO SUL", "Energisa MS - Enersul",
        ("ENERGISA/MATO GROSSO DO SUL", "EMS", "ENERSUL"),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "energisa_ms"),
            GrupoTensao.MT: _s("pipeline_energisa_mt.py", "energisa_ms", pasta=True),
        },
    ),

    "ENERGISA/SUL SUDESTE": ConcessionariaSpec(
        "ENERGISA/SUL SUDESTE", "Energisa Sul Sudeste - Caiuá",
        ("ENERGISA/SUL SUDESTE", "ESS", "CAIUÁ"),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "energisa_ss"),
            GrupoTensao.MT: _s("pipeline_energisa_mt.py", "energisa_ss", pasta=True),
        },
    ),

    "ENERGISA/SERGIPE": ConcessionariaSpec(
        "ENERGISA/SERGIPE", "Energisa Sergipe",
        ("ENERGISA/SERGIPE", "ESE"),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "energisa_se"),
            GrupoTensao.MT: _s("pipeline_energisa_mt.py", "energisa_se", pasta=True),
        },
    ),

    "ENERGISA/PARAIBA": ConcessionariaSpec(
        "ENERGISA/PARAIBA", "Energisa Paraíba",
        ("ENERGISA/PARAIBA", "EPB"),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "energisa_pb"),
            GrupoTensao.MT: _s("pipeline_energisa_mt.py", "energisa_pb", pasta=True),
        },
    ),

    "ENERGISA/RONDONIA": ConcessionariaSpec(
        "ENERGISA/RONDONIA", "Energisa Rondônia - CERON",
        ("ENERGISA/RONDONIA", "CERON"),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "energisa_ro"),
            GrupoTensao.MT: _s("pipeline_energisa_mt.py", "energisa_ro", pasta=True),
        },
    ),

    "ENERGISA/TOCANTINS": ConcessionariaSpec(
        "ENERGISA/TOCANTINS", "Energisa Tocantins - CELTINS",
        ("ENERGISA/TOCANTINS", "ETO", "CELTINS"),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "energisa_to"),
            GrupoTensao.MT: _s("pipeline_energisa_mt.py", "energisa_to", pasta=True),
        },
    ),

    "ENERGISA/MINAS RIO": ConcessionariaSpec(
        "ENERGISA/MINAS RIO", "Energisa Minas Rio",
        ("ENERGISA/MINAS RIO", "EMR"),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "energisa_mr"),
            GrupoTensao.MT: _s("pipeline_energisa_mt.py", "energisa_mr", pasta=True),
        },
    ),

    "ENERGISA/ACRE": ConcessionariaSpec(
        "ENERGISA/ACRE", "Energisa Acre - Eletroacre",
        ("ENERGISA/ACRE", "ELETROACRE"),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "energisa_ac"),
            GrupoTensao.MT: _n("pipeline MT não confirmado"),
        },
    ),

    "CHESP": ConcessionariaSpec(
        "CHESP", "CHESP - Companhia Hidroelétrica São Patrício",
        ("CHESP",),
        {
            GrupoTensao.BT: _s("pipeline_chesp_bt.py", pasta=True),
            GrupoTensao.MT: _s("pipeline_chesp_mt.py", pasta=True),
        },
    ),

    "PEQUENAS": ConcessionariaSpec(
        "PEQUENAS", "Pequenas distribuidoras / cooperativas",
        ("PEQUENAS", "DEMEI", "ELFSM", "CERMISSOES", "COOPERJAM", "NOVA_PALMA", "CERFRON"),
        {
            GrupoTensao.BT: _s("pipeline_lote_bt.py", "pequenas"),
            GrupoTensao.MT: _n("sem OCR/pipeline MT para pequenas"),
        },
    ),

    "AMAZONAS": ConcessionariaSpec(
        "AMAZONAS", "Amazonas Energia",
        ("AMAZONAS", "AME"),
        {
            GrupoTensao.BT: _n("sem fluxo operacional"),
            GrupoTensao.MT: _n("sem fluxo operacional"),
        },
    ),
}

# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def obter_concessionaria(identificador: str) -> ConcessionariaSpec:
    return REGISTRO[identificador]


def listar_concessionarias() -> tuple[ConcessionariaSpec, ...]:
    return tuple(REGISTRO.values())


def resolver_pipeline(
    concessionaria: str,
    grupo: GrupoTensao,
    ctx: "ContextoExecucao | None" = None,  # type: ignore[name-defined]
) -> dict:
    from .adaptadores import comando_legado

    spec = REGISTRO.get(concessionaria)
    p = spec.grupos.get(grupo) if spec else None
    if p is None:
        return {
            "estado": EstadoImplementacao.NAO_IMPLEMENTADO.value,
            "pipeline": None,
            "motivo": "concessionaria ou grupo desconhecido",
            "argumentos": [],
        }
    return {
        "estado": p.estado.value,
        "pipeline": str(p.script) if p.script else None,
        "motivo": p.motivo,
        "argumentos": comando_legado(p, ctx) if ctx else list(p.argumentos),
        "aceita_pasta": p.aceita_pasta,
        "aceita_session_root": p.aceita_session_root,
    }


def obter_cobertura() -> list[dict]:
    return [
        {"id": s.id, **{g.value: p.estado.value for g, p in s.grupos.items()}}
        for s in REGISTRO.values()
    ]


def validar_catalogo() -> list[str]:
    erros = []
    for s in REGISTRO.values():
        if set(s.grupos) != {GrupoTensao.BT, GrupoTensao.MT}:
            erros.append(f"{s.id}: grupos incompletos")
        for p in s.grupos.values():
            if p.estado is EstadoImplementacao.SUPORTADO and p.script and not p.script.exists():
                erros.append(f"{s.id}: script inexistente: {p.script}")
    return erros


def gerar_catalog_legacy() -> dict:
    """Gera representação CATALOG compatível com o watcher.py legado."""
    out: dict = {}
    for key, spec in REGISTRO.items():
        entry: dict = {}
        for grupo_str, grupo_enum in (("bt", GrupoTensao.BT), ("mt", GrupoTensao.MT)):
            p = spec.grupos[grupo_enum]
            if p.estado is EstadoImplementacao.NAO_IMPLEMENTADO:
                entry[grupo_str] = None
            else:
                cfg: dict = {}
                if p.identificador:
                    cfg["conc"] = p.identificador
                if p.script and p.identificador:
                    # direct_script só quando não é o pipeline_lote_bt genérico
                    if p.script.name != "pipeline_lote_bt.py":
                        cfg["direct_script"] = p.script
                elif p.script and not p.identificador:
                    cfg["direct_script"] = p.script
                if p.argumentos:
                    cfg["extra_args"] = list(p.argumentos)
                if p.aceita_pasta:
                    cfg["pasta_arg"] = True
                if p.aceita_session_root:
                    cfg["session_root_arg"] = True
                entry[grupo_str] = cfg or None
        out[key] = entry
    return out
