"""
benchmark.py — Avaliação do classificador determinístico.

Separa:
  acurácia entre os classificados (rotulado + rotulado_com_baixa_confianca)
  cobertura total (% rotulados sobre o total)
  taxa de rejeição (grupo_desconhecido, texto_insuficiente, pdf_nao_localizado)
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field


@dataclass
class BenchmarkResult:
    total: int
    rotulados: int
    rejeitados: int
    conflitos: int
    por_status: dict[str, int]
    por_grupo: dict[str, int]          # {"BT": N, "MT": N, None: N}
    por_concessionaria: dict[str, dict]
    distribuicao_confianca: dict[str, int]  # faixas: "0.0-0.4", "0.4-0.7", "0.7-1.0"
    classes_pequenas: list[str]


def calcular(registros: list[dict]) -> BenchmarkResult:
    """
    Recebe lista de registros com campos:
      concessionaria_canonica, grupo, subgrupo, confianca, status
    """
    total = len(registros)
    por_status: Counter = Counter()
    por_grupo: Counter = Counter()
    por_conc: dict[str, dict] = defaultdict(lambda: {"total": 0, "rotulados": 0, "BT": 0, "MT": 0, "rejeicoes": 0})
    confiancas = {"0.0-0.4": 0, "0.4-0.7": 0, "0.7-1.0": 0}

    for r in registros:
        status = r.get("status_rotulagem", "grupo_desconhecido")
        grupo = r.get("grupo")
        conc = r.get("concessionaria_canonica", "DESCONHECIDA")
        conf = float(r.get("confianca", 0.0))

        por_status[status] += 1
        por_grupo[str(grupo)] += 1
        por_conc[conc]["total"] += 1

        if status in ("rotulado", "rotulado_com_baixa_confianca"):
            por_conc[conc]["rotulados"] += 1
            if grupo == "BT":
                por_conc[conc]["BT"] += 1
            elif grupo == "MT":
                por_conc[conc]["MT"] += 1
            if conf < 0.4:
                confiancas["0.0-0.4"] += 1
            elif conf < 0.7:
                confiancas["0.4-0.7"] += 1
            else:
                confiancas["0.7-1.0"] += 1
        else:
            por_conc[conc]["rejeicoes"] += 1

    rotulados = por_status.get("rotulado", 0) + por_status.get("rotulado_com_baixa_confianca", 0)
    rejeitados = total - rotulados - por_status.get("conflito_de_evidencias", 0)
    conflitos = por_status.get("conflito_de_evidencias", 0)

    # Classes pequenas: concessionárias com menos de 10 rotulados
    classes_pequenas = [c for c, d in por_conc.items() if d["rotulados"] < 10]

    return BenchmarkResult(
        total=total,
        rotulados=rotulados,
        rejeitados=rejeitados,
        conflitos=conflitos,
        por_status=dict(por_status),
        por_grupo=dict(por_grupo),
        por_concessionaria=dict(por_conc),
        distribuicao_confianca=confiancas,
        classes_pequenas=sorted(classes_pequenas),
    )


def imprimir(resultado: BenchmarkResult) -> None:
    pct = lambda n, d: f"{100*n//d if d else 0}%"
    print(f"\n{'='*60}")
    print(f"  BENCHMARK CLASSIFICADOR BT/MT")
    print(f"{'='*60}")
    print(f"  Total PDFs         : {resultado.total}")
    print(f"  Rotulados          : {resultado.rotulados} ({pct(resultado.rotulados, resultado.total)})")
    print(f"  Rejeitados         : {resultado.rejeitados} ({pct(resultado.rejeitados, resultado.total)})")
    print(f"  Conflitos          : {resultado.conflitos} ({pct(resultado.conflitos, resultado.total)})")
    print(f"\n  Por grupo:")
    for g, n in sorted(resultado.por_grupo.items()):
        print(f"    {g or 'None':6s}: {n}")
    print(f"\n  Por status:")
    for s, n in sorted(resultado.por_status.items(), key=lambda x: -x[1]):
        print(f"    {s:40s}: {n}")
    print(f"\n  Distribuição de confiança (apenas rotulados):")
    for faixa, n in resultado.distribuicao_confianca.items():
        print(f"    {faixa}: {n}")
    if resultado.classes_pequenas:
        print(f"\n  Classes com < 10 rotulados ({len(resultado.classes_pequenas)}):")
        for c in resultado.classes_pequenas[:20]:
            d = resultado.por_concessionaria[c]
            print(f"    {c:35s}: {d['rotulados']:3d} rotulados ({d['BT']} BT, {d['MT']} MT)")
    print(f"{'='*60}\n")
