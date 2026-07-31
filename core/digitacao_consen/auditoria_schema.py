# -*- coding: utf-8 -*-
"""Contrato comum do arquivo auditoria_resultados.csv.

Este modulo centraliza o layout atual e a leitura tolerante dos layouts
antigos. A ideia e preservar o fluxo existente e apenas garantir que os
consumidores entendam tanto CSV legado quanto CSV com memoria de calculo.
"""

from __future__ import annotations

import csv
from pathlib import Path


AUDITORIA_HEADERS = [
    "linha_excel",
    "instalacao",
    "data_referencia_esperada",
    "carimbo",
    "valor_auditoria",
    "pct_diferenca",
    "itens_divergentes",
    "memoria_calculo",
    "status",
]

AUDITORIA_HEADERS_SEM_MEMORIA = [
    "linha_excel",
    "instalacao",
    "data_referencia_esperada",
    "carimbo",
    "valor_auditoria",
    "pct_diferenca",
    "itens_divergentes",
    "status",
]

AUDITORIA_HEADERS_LEGACY = [
    "linha_excel",
    "instalacao",
    "data_referencia_esperada",
    "carimbo",
    "valor_auditoria",
    "status",
]

STATUS_AUDITORIA_OK = {
    "sucesso_auditoria",
    "auditoria_sem_valor",
    "pulado_referencia_existente",
    "pulado_carimbo_existente",
}

STATUS_AUDITORIA_CONHECIDOS = {
    *STATUS_AUDITORIA_OK,
    "erro_extracao",
    "erro_auditoria",
    "erro_digitacao",
    "erro_instalacao",
    "erro_carregar_instalacao",
    "erro_tela_auditoria",
    "erro_tela_instalacao",
    "erro_proxima_fatura",
    "erro_inesperado",
}


def _norm(value) -> str:
    return "" if value is None else str(value).strip()


def normalizar_linha_auditoria(header: list[str], cols: list[str]) -> dict:
    """Converte uma linha CSV em dict com todas as colunas oficiais."""
    header_norm = [_norm(col).lower() for col in header]
    valores = [_norm(col) for col in cols]
    row = {key: "" for key in AUDITORIA_HEADERS}

    if header_norm == AUDITORIA_HEADERS:
        for key, value in zip(AUDITORIA_HEADERS, valores):
            row[key] = value
        if len(valores) > len(AUDITORIA_HEADERS):
            row["status"] = valores[-1]
        return row

    if header_norm == AUDITORIA_HEADERS_SEM_MEMORIA:
        vals = valores + [""] * max(0, len(AUDITORIA_HEADERS_SEM_MEMORIA) - len(valores))
        row.update({
            "linha_excel": vals[0],
            "instalacao": vals[1],
            "data_referencia_esperada": vals[2],
            "carimbo": vals[3],
            "valor_auditoria": vals[4],
            "pct_diferenca": vals[5],
            "itens_divergentes": vals[6],
            "memoria_calculo": "",
            "status": vals[-1] if len(valores) > len(AUDITORIA_HEADERS_SEM_MEMORIA) else vals[7],
        })
        return row

    if header_norm == AUDITORIA_HEADERS_LEGACY:
        if len(valores) >= len(AUDITORIA_HEADERS):
            for key, value in zip(AUDITORIA_HEADERS, valores):
                row[key] = value
            row["status"] = valores[-1]
        elif len(valores) >= len(AUDITORIA_HEADERS_SEM_MEMORIA):
            row.update({
                "linha_excel": valores[0],
                "instalacao": valores[1],
                "data_referencia_esperada": valores[2],
                "carimbo": valores[3],
                "valor_auditoria": valores[4],
                "pct_diferenca": valores[5],
                "itens_divergentes": valores[6],
                "memoria_calculo": "",
                "status": valores[7],
            })
        else:
            vals = valores + [""] * max(0, len(AUDITORIA_HEADERS_LEGACY) - len(valores))
            row.update({
                "linha_excel": vals[0],
                "instalacao": vals[1],
                "data_referencia_esperada": vals[2],
                "carimbo": vals[3],
                "valor_auditoria": vals[4],
                "status": vals[5],
            })
        return row

    for key, value in zip(header_norm, valores):
        if key in row:
            row[key] = value
    if len(valores) > len(header_norm):
        row["status"] = valores[-1]
    return row


def extrair_status_auditoria(row: dict) -> str:
    """Lê status mesmo em CSV legado com colunas extras desalinhadas."""
    candidatos: list[str] = []
    status = _norm(row.get("status", ""))
    if status:
        candidatos.append(status)

    extras = row.get(None) or []
    if not isinstance(extras, list):
        extras = [extras]
    candidatos.extend(_norm(item) for item in extras if _norm(item))

    for cand in reversed(candidatos):
        cand_lower = cand.lower()
        if cand_lower in STATUS_AUDITORIA_CONHECIDOS or cand_lower.startswith("erro_no_fluxo:"):
            return cand_lower

    return status.lower()


def ler_auditoria_csv_flexivel(path: Path) -> list[dict]:
    """Lê auditoria_resultados.csv com delimitador ';' ou ',' e layouts antigos/atuais."""
    if not Path(path).exists():
        return []

    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        for sep in (";", ","):
            try:
                with Path(path).open("r", newline="", encoding=enc) as f:
                    rows = list(csv.reader(f, delimiter=sep))
                if not rows or len(rows[0]) < 2:
                    continue
                header = rows[0]
                return [
                    normalizar_linha_auditoria(header, cols)
                    for cols in rows[1:]
                    if any(_norm(col) for col in cols)
                ]
            except UnicodeDecodeError:
                continue
            except Exception:
                continue
    return []


def migrar_auditoria_legacy(path: Path) -> None:
    """Atualiza arquivo legado para o cabeçalho oficial sem perder linhas."""
    path = Path(path)
    if not path.exists():
        return

    try:
        with path.open("r", newline="", encoding="utf-8-sig") as f:
            header = next(csv.reader(f, delimiter=";"), [])
    except Exception:
        header = []

    if [_norm(col).lower() for col in header] == AUDITORIA_HEADERS:
        return

    rows = ler_auditoria_csv_flexivel(path)
    if not rows:
        return

    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=AUDITORIA_HEADERS, delimiter=";")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in AUDITORIA_HEADERS})


def append_resultado_auditoria(path: Path, row: dict) -> None:
    """Acrescenta uma linha no layout oficial, migrando legado antes se existir."""
    path = Path(path)
    migrar_auditoria_legacy(path)
    existe = path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=AUDITORIA_HEADERS, delimiter=";")
        if not existe:
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in AUDITORIA_HEADERS})


def upsert_resultado_auditoria(
    path: Path,
    row: dict,
    key_fields: tuple[str, ...] = ("carimbo",),
) -> None:
    """Substitui linha onde todos key_fields coincidem; adiciona se não houver match.

    Escreve atomicamente via arquivo temporário + os.replace.
    Em caso de erro, cai de volta em append_resultado_auditoria.
    """
    import os
    import tempfile

    path = Path(path)
    try:
        migrar_auditoria_legacy(path)
        linhas_existentes = ler_auditoria_csv_flexivel(path) if path.exists() else []

        nova_linha = {key: row.get(key, "") for key in AUDITORIA_HEADERS}
        chave_nova = {k: nova_linha.get(k, "") for k in key_fields}

        substituiu = False
        linhas_saida: list[dict] = []
        for linha in linhas_existentes:
            chave_linha = {k: linha.get(k, "") for k in key_fields}
            if chave_linha == chave_nova:
                linhas_saida.append(nova_linha)
                substituiu = True
            else:
                linhas_saida.append({key: linha.get(key, "") for key in AUDITORIA_HEADERS})
        if not substituiu:
            linhas_saida.append(nova_linha)

        dir_ = path.parent
        dir_.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(dir_), suffix=".tmp", prefix="auditoria_")
        try:
            with os.fdopen(fd, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=AUDITORIA_HEADERS, delimiter=";")
                writer.writeheader()
                writer.writerows(linhas_saida)
            os.replace(tmp_name, str(path))
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except Exception:
        append_resultado_auditoria(path, row)
