"""
Carrega e gerencia a lista de UCs da Energisa a partir de acessos_energisa.xlsx.

Estrutura esperada do XLSX:
    Medidor | Prefixo | Concessionária | Instalacao | CNPJ | Login | Senha | ...

Nota: nomes de coluna com acento são normalizados automaticamente para evitar
problemas de encoding entre versões do Excel/openpyxl.

Uso:
    from core.downloaders.energisa.indice_energisa import carregar_ucs, agrupar_por_cnpj
    ucs = carregar_ucs()
    grupos = agrupar_por_cnpj(ucs)
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import openpyxl

# ---------------------------------------------------------------------------
# Localização padrão da planilha
# ---------------------------------------------------------------------------

PLANILHA_DEFAULT = Path(
    r"\\10.10.250.21\Energia\ARQUIVOS ENZO\DOWNLOAD ENERGISA\acessos_energisa.xlsx"
)

# Domínios que identificam bbenergia (acaoenge.com.br é o domínio antigo,
# já redirecionado para acaoengenharia.com.br — ambos equivalentes)
DOMINIOS_BBENERGIA = ("acaoengenharia.com.br", "acaoenge.com.br")

# Palavras-chave que identificam concessionárias Energisa
_ENERGISA_KEYWORDS = ("ENERGISA", "CELTINS")


# ---------------------------------------------------------------------------
# Modelo de dados
# ---------------------------------------------------------------------------

@dataclass
class UCEnergisa:
    instalacao: str       # Número da instalação/UC
    instalacao_antiga: str
    cnpj: str             # CNPJ sem formatação (14 dígitos)
    concessionaria: str   # Ex: "ESS ENERGISA SUL SUDESTE - CAIUA"
    prefixo: str
    medidor: str
    tensao: str           # "Baixa Tensão" / "MédiaTensão" / "BT" / "MT"
    login: str            # Texto do campo Login na planilha
    conta_contrato: str
    responsavel: str
    usa_bbenergia: bool   # True se login referencia qualquer domínio bbenergia


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _sem_acento(s: str) -> str:
    """Remove acentos e retorna maiúsculo — usado para comparar nomes de coluna."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    ).upper()


def _limpar_cnpj(valor) -> str:
    if not valor:
        return ""
    return re.sub(r"\D", "", str(valor))


def _limpar_str(valor) -> str:
    return str(valor).strip() if valor is not None else ""


def _is_energisa(concessionaria: str) -> bool:
    c = concessionaria.upper()
    return any(kw in c for kw in _ENERGISA_KEYWORDS)


def _usa_bbenergia(login: str) -> bool:
    l = login.lower()
    return any(d in l for d in DOMINIOS_BBENERGIA)


class _HeaderMap:
    """Resolve índices de coluna ignorando acentos e maiúsculas/minúsculas."""

    def __init__(self, row: tuple) -> None:
        self._norm = [_sem_acento(str(c)) if c else "" for c in row]

    def idx(self, nome: str) -> int:
        n = _sem_acento(nome)
        try:
            return self._norm.index(n)
        except ValueError:
            return -1

    def get(self, row: tuple, nome: str) -> str:
        i = self.idx(nome)
        if i < 0 or i >= len(row):
            return ""
        return _limpar_str(row[i])


# ---------------------------------------------------------------------------
# Carregamento
# ---------------------------------------------------------------------------

def carregar_ucs(
    planilha: Path | str = PLANILHA_DEFAULT,
    so_energisa: bool = True,
) -> list[UCEnergisa]:
    """
    Carrega UCs da planilha acessos_energisa.xlsx.

    Args:
        planilha:     caminho do XLSX
        so_energisa:  se True (padrão), filtra apenas concessionárias Energisa
    """
    planilha = Path(planilha)
    if not planilha.exists():
        raise FileNotFoundError(f"Planilha não encontrada: {planilha}")

    wb = openpyxl.load_workbook(planilha, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if not rows:
        return []

    hm = _HeaderMap(rows[0])

    ucs: list[UCEnergisa] = []
    for row in rows[1:]:
        if not any(row):
            continue

        conc      = hm.get(row, "Concessionaria")   # sem acento — resolve "Concessionária"
        instalacao = hm.get(row, "Instalacao")
        cnpj      = _limpar_cnpj(hm.get(row, "CNPJ"))
        login     = hm.get(row, "Login")

        if so_energisa and not _is_energisa(conc):
            continue
        if not cnpj or not instalacao:
            continue

        ucs.append(UCEnergisa(
            instalacao=instalacao,
            instalacao_antiga=hm.get(row, "Instalacao Antiga"),
            cnpj=cnpj,
            concessionaria=conc,
            prefixo=hm.get(row, "Prefixo"),
            medidor=hm.get(row, "Medidor"),
            tensao=hm.get(row, "Tensao") or "BT",
            login=login,
            conta_contrato=hm.get(row, "Conta Contrato"),
            responsavel=hm.get(row, "Responsavel"),
            usa_bbenergia=_usa_bbenergia(login),
        ))

    return ucs


def carregar_ucs_bbenergia(planilha: Path | str = PLANILHA_DEFAULT) -> list[UCEnergisa]:
    """Retorna apenas UCs com login bbenergia (ambos os domínios)."""
    return [u for u in carregar_ucs(planilha) if u.usa_bbenergia]


# ---------------------------------------------------------------------------
# Agrupamento
# ---------------------------------------------------------------------------

def agrupar_por_cnpj(ucs: list[UCEnergisa]) -> dict[str, list[UCEnergisa]]:
    """
    Agrupa UCs por CNPJ.
    Retorna dict { cnpj: [UCEnergisa, ...] }
    """
    grupos: dict[str, list[UCEnergisa]] = {}
    for uc in ucs:
        grupos.setdefault(uc.cnpj, []).append(uc)
    return grupos


def iterar_grupos(planilha: Path | str = PLANILHA_DEFAULT) -> Iterator[tuple[str, list[UCEnergisa]]]:
    """
    Itera grupos (cnpj, [ucs]) apenas com UCs bbenergia, em ordem de CNPJ.
    """
    ucs = carregar_ucs_bbenergia(planilha)
    grupos = agrupar_por_cnpj(ucs)
    for cnpj in sorted(grupos):
        yield cnpj, grupos[cnpj]


# ---------------------------------------------------------------------------
# Diagnóstico rápido
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import collections

    todas = carregar_ucs()
    bb    = [u for u in todas if u.usa_bbenergia]
    grupos_bb = agrupar_por_cnpj(bb)
    unicos    = {k: v for k, v in grupos_bb.items() if len(v) == 1}
    multiplos = {k: v for k, v in grupos_bb.items() if len(v) > 1}

    print(f"Total UCs Energisa   : {len(todas)}")
    print(f"UCs bbenergia        : {len(bb)}")
    print(f"CNPJs bbenergia      : {len(grupos_bb)}")
    print(f"  únicos (1 UC)      : {len(unicos)}")
    print(f"  múltiplos (2+ UCs) : {len(multiplos)}  ({sum(len(v) for v in multiplos.values())} UCs)")
    print()
    print("Por concessionária (bbenergia):")
    for conc, n in collections.Counter(u.concessionaria for u in bb).most_common():
        print(f"  {n:4d}  {conc}")
    if multiplos:
        print("\nCNPJs múltiplos:")
        for cnpj, ucs in sorted(multiplos.items(), key=lambda x: -len(x[1])):
            print(f"  {cnpj}: {len(ucs)} UCs — {ucs[0].concessionaria}")
            for u in ucs:
                print(f"    {u.instalacao}  [{u.tensao}]")
