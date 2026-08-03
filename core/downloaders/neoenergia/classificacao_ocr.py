from __future__ import annotations

import csv
import hashlib
import logging
import re
import sys
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pdfplumber

from core.ocr import ocr_neoenergia  # noqa: E402


MES_DIR_RE = re.compile(r"^(?:\d{4}-\d{2}|\d{2}\.\d{4})$")
TIPO_PASTA = {
    "bt": "BT",
    "mt": "MT",
}
PASTA_CONCESSIONARIA = {
    "BAHIA": "COELBA",
    "COELBA": "COELBA",
    "PERNAMBUCO": "CELPE",
    "CELPE": "CELPE",
    "RIO_GRANDE_DO_NORTE": "COSERN",
    "COSERN": "COSERN",
    "SAO_PAULO": "ELEKTRO",
    "MATO_GROSSO_DO_SUL": "ELEKTRO",
    "ELEKTRO": "ELEKTRO",
    "DESCONHECIDO": "DESCONHECIDO",
}

log = logging.getLogger("neoenergia_classificacao_ocr")


@dataclass
class AnalisePdfNeoenergia:
    tipo: str | None
    mes_referencia: str | None


@dataclass
class OrganizacaoResumo:
    pastas_lidas: int = 0
    pdfs_lidos: int = 0
    movidos_bt: int = 0
    movidos_mt: int = 0
    referencias_corrigidas: int = 0
    nao_classificados: int = 0
    indice_atualizado: int = 0
    master_atualizado: int = 0


@dataclass
class SaneamentoSufixosResumo:
    renomeados_simples: int = 0
    conflitos_resolvidos: int = 0
    duplicatas_exatas_removidas: int = 0
    indice_atualizado: int = 0
    master_atualizado: int = 0


@dataclass
class RestauracaoNomesResumo:
    renomeados: int = 0
    ja_corretos: int = 0
    nao_encontrados: int = 0
    conflitos: int = 0
    indice_atualizado: int = 0
    master_atualizado: int = 0


def _extrair_texto_pdf(pdf_path: Path, max_paginas: int = 3) -> str:
    partes: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for pagina in pdf.pages[:max_paginas]:
            texto = pagina.extract_text() or ""
            if texto.strip():
                partes.append(texto)
    return "\n".join(partes).strip()


def _normalizar_ref_mes_ano(ref: str) -> str | None:
    txt = ocr_neoenergia._texto_normalizado(str(ref or ""))
    if not txt:
        return None

    m_num = re.search(r"(\d{2})\s*/\s*(\d{4})", txt)
    if m_num:
        return f"{int(m_num.group(1)):02d}/{m_num.group(2)}"

    m_path = re.search(r"(\d{4})-(\d{2})", txt)
    if m_path:
        return f"{m_path.group(2)}/{m_path.group(1)}"

    meses = {
        "JANEIRO": "01",
        "FEVEREIRO": "02",
        "MARCO": "03",
        "ABRIL": "04",
        "MAIO": "05",
        "JUNHO": "06",
        "JULHO": "07",
        "AGOSTO": "08",
        "SETEMBRO": "09",
        "OUTUBRO": "10",
        "NOVEMBRO": "11",
        "DEZEMBRO": "12",
    }
    m_txt = re.search(r"([A-Z]+)\s*/\s*(\d{4})", txt)
    if m_txt:
        mes = meses.get(m_txt.group(1))
        if mes:
            return f"{mes}/{m_txt.group(2)}"
    return None


def _mes_para_pasta(ref: str | None) -> str | None:
    ref_norm = _normalizar_ref_mes_ano(ref or "")
    if not ref_norm:
        return None
    mes, ano = ref_norm.split("/")
    return f"{ano}-{mes}"


def _extrair_ref_resumo(texto: str) -> str | None:
    texto_norm = ocr_neoenergia._texto_normalizado(texto)
    m = re.search(r"REF:?[\sA-Z/$:-]{0,120}(\d{2}/\d{4})", texto_norm, flags=re.DOTALL)
    if m:
        return _normalizar_ref_mes_ano(m.group(1))
    return None


def _extrair_ref_fallback_datas(texto: str) -> str | None:
    _, leitura_atual = ocr_neoenergia._extract_leituras(texto)
    if isinstance(leitura_atual, date):
        return f"{leitura_atual.month:02d}/{leitura_atual.year}"

    vencimento = ocr_neoenergia._extract_vencimento(texto)
    if isinstance(vencimento, date):
        return f"{vencimento.month:02d}/{vencimento.year}"

    return None


def analisar_texto_neoenergia(texto: str) -> AnalisePdfNeoenergia:
    if not texto or not texto.strip():
        return AnalisePdfNeoenergia(tipo=None, mes_referencia=None)

    tipo, _, _, _ = ocr_neoenergia._detectar_tipo_tarifa(texto)
    tipo_pasta = TIPO_PASTA.get(tipo)
    mes_referencia = _extrair_ref_resumo(texto) or _extrair_ref_fallback_datas(texto)
    return AnalisePdfNeoenergia(tipo=tipo_pasta, mes_referencia=mes_referencia)


def analisar_pdf_neoenergia(pdf_path: Path) -> AnalisePdfNeoenergia:
    try:
        texto = _extrair_texto_pdf(pdf_path)
    except Exception as exc:
        log.warning("Falha ao ler PDF %s: %s", pdf_path, exc)
        return AnalisePdfNeoenergia(tipo=None, mes_referencia=None)
    return analisar_texto_neoenergia(texto)


def _encontrar_pasta_mes_relativa(partes: tuple[str, ...]) -> tuple[int, str] | None:
    for i, parte in enumerate(partes):
        if MES_DIR_RE.match(parte):
            return i, parte
    return None


def _normalizar_prefixo_concessionaria(prefixo: tuple[str, ...]) -> tuple[str, ...]:
    if not prefixo:
        return prefixo
    primeira = prefixo[0]
    primeira_norm = re.sub(r"[^A-Za-z0-9]+", "_", primeira.upper()).strip("_")
    canonical = PASTA_CONCESSIONARIA.get(primeira_norm, primeira_norm)
    return (canonical, *prefixo[1:])


def _resolver_destino_unico(destino: Path) -> Path:
    if not destino.exists():
        return destino
    # Strip accumulated numeric suffixes (_2, _2_2, _2_2_3, …) before incrementing
    stem = re.sub(r"(_\d+)+$", "", destino.stem)
    suffix = destino.suffix
    i = 2
    while True:
        alt = destino.with_name(f"{stem}_{i}{suffix}")
        if not alt.exists():
            return alt
        i += 1


def _reescrever_csv_por_arquivo(
    caminho: Path,
    coluna_arquivo: str,
    atualizacoes: dict[str, dict[str, str]],
    extras: dict[str, str],
) -> int:
    if not caminho.exists() or not atualizacoes:
        return 0

    linhas: list[dict[str, str]] = []
    atualizados = 0
    with open(caminho, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        for row in reader:
            arquivo = str(row.get(coluna_arquivo, "") or "")
            update = atualizacoes.get(arquivo)
            if update:
                row[coluna_arquivo] = update["arquivo"]
                for col_csv, col_update in extras.items():
                    if col_update in update:
                        row[col_csv] = update[col_update]
                atualizados += 1
            linhas.append(row)

    tmp = caminho.with_suffix(caminho.suffix + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(linhas)
    tmp.replace(caminho)
    return atualizados


def _ler_csv(caminho: Path) -> tuple[list[str], list[dict[str, str]]]:
    with open(caminho, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def _gravar_csv(caminho: Path, fieldnames: list[str], linhas: list[dict[str, str]]) -> None:
    tmp = caminho.with_suffix(caminho.suffix + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(linhas)
    tmp.replace(caminho)


def _normalizar_indice_bb(valor: str) -> str:
    txt = str(valor or "").strip().upper()
    if not txt:
        return ""
    if txt.startswith("BB_"):
        return txt
    if txt.isdigit():
        return f"BB_{txt}"
    return txt


def _bb_para_numero(valor: str) -> str:
    txt = _normalizar_indice_bb(valor)
    return txt.replace("BB_", "", 1) if txt else ""


def _proximo_bb(index_rows: list[dict[str, str]], master_rows: list[dict[str, str]]) -> str:
    max_num = 0
    for row in index_rows:
        num = _bb_para_numero(row.get("id", ""))
        if num.isdigit():
            max_num = max(max_num, int(num))
    for row in master_rows:
        num = _bb_para_numero(row.get("INDICE", ""))
        if num.isdigit():
            max_num = max(max_num, int(num))
    return f"BB_{max_num + 1}"


def _parse_bb_nome(nome: str) -> tuple[str, str | None] | None:
    m = re.match(r"^(BB_\d+)((?:_\d+)*)\.pdf$", nome, flags=re.IGNORECASE)
    if not m:
        return None
    sufixos = m.group(2) or ""
    return m.group(1).upper(), sufixos.lstrip("_") or None


def _atualizar_path_em_linhas(
    linhas: list[dict[str, str]],
    coluna: str,
    antigo: str,
    novo: str,
) -> int:
    atualizados = 0
    for row in linhas:
        if str(row.get(coluna, "")) == antigo:
            row[coluna] = novo
            atualizados += 1
    return atualizados


def _saneamento_paths_bb(
    download_root: Path,
) -> dict[str, list[Path]]:
    grupos: dict[str, list[Path]] = {}
    for pdf in sorted(download_root.rglob("*.pdf")):
        parsed = _parse_bb_nome(pdf.name)
        if not parsed:
            continue
        base, _ = parsed
        grupos.setdefault(base, []).append(pdf)
    return grupos


def organizar_downloads_neoenergia(
    download_root: Path,
    *,
    index_file: Path | None = None,
    master_file: Path | None = None,
    logger: logging.Logger | None = None,
) -> OrganizacaoResumo:
    logger = logger or log
    resumo = OrganizacaoResumo()
    atualizacoes: dict[str, dict[str, str]] = {}
    pastas_mes_lidas: set[Path] = set()

    for pdf in sorted(download_root.rglob("*.pdf")):
        resumo.pdfs_lidos += 1
        try:
            relativo = pdf.relative_to(download_root)
        except ValueError:
            logger.warning("PDF fora da raiz esperada: %s", pdf)
            resumo.nao_classificados += 1
            continue

        info_mes = _encontrar_pasta_mes_relativa(relativo.parts)
        if info_mes is None:
            logger.warning("Sem pasta de referencia identificavel para %s", pdf)
            resumo.nao_classificados += 1
            continue

        idx_mes, pasta_mes_atual = info_mes
        prefixo = _normalizar_prefixo_concessionaria(relativo.parts[:idx_mes])
        if not prefixo:
            logger.warning("Sem prefixo de estado para %s", pdf)
            resumo.nao_classificados += 1
            continue

        pasta_mes_original = download_root.joinpath(*prefixo, pasta_mes_atual)
        pastas_mes_lidas.add(pasta_mes_original)

        analise = analisar_pdf_neoenergia(pdf)
        if analise.tipo is None:
            resumo.nao_classificados += 1
            logger.warning("OCR sem classificacao para %s", pdf)
            continue

        pasta_mes_correta = _mes_para_pasta(analise.mes_referencia) or pasta_mes_atual
        destino_dir = download_root.joinpath(*prefixo, pasta_mes_correta, analise.tipo)
        destino_dir.mkdir(parents=True, exist_ok=True)
        destino_base = destino_dir / pdf.name
        # Evita chamar _resolver_destino_unico quando o arquivo já está no lugar
        # certo: sem isso, o arquivo existente geraria BB_XXXXX_2, BB_XXXXX_2_2…
        # acumulando sufixos a cada re-execução.
        destino = destino_base if str(destino_base) == str(pdf) else _resolver_destino_unico(destino_base)

        mudou_ref = pasta_mes_correta != pasta_mes_atual
        mudou_tipo = len(relativo.parts) <= idx_mes + 1 or relativo.parts[idx_mes + 1].upper() != analise.tipo

        if str(destino) != str(pdf):
            if not pdf.exists():
                logger.warning("Arquivo nao encontrado durante reorganizacao: %s", pdf)
                continue
            try:
                pdf.rename(destino)
            except FileNotFoundError:
                logger.warning("Arquivo sumiu antes do move: %s", pdf)
                continue
            atualizacoes[str(pdf)] = {
                "arquivo": str(destino),
                "mes_referencia": analise.mes_referencia or _normalizar_ref_mes_ano(pasta_mes_atual) or "",
                "MES_REF": (analise.mes_referencia or _normalizar_ref_mes_ano(pasta_mes_atual) or "").replace("/", "-"),
            }

        if analise.tipo == "BT":
            resumo.movidos_bt += 1
        elif analise.tipo == "MT":
            resumo.movidos_mt += 1
        if mudou_ref:
            resumo.referencias_corrigidas += 1

        logger.info(
            "PDF reorganizado por OCR: %s -> %s | ref=%s%s%s",
            pdf.name,
            analise.tipo,
            analise.mes_referencia or "desconhecida",
            " | pasta_mes_corrigida" if mudou_ref else "",
            " | tipo_corrigido" if mudou_tipo else "",
        )

    resumo.pastas_lidas = len(pastas_mes_lidas)
    resumo.indice_atualizado = (
        _reescrever_csv_por_arquivo(
            index_file,
            "arquivo",
            atualizacoes,
            {},
        )
        if index_file
        else 0
    )
    resumo.master_atualizado = (
        _reescrever_csv_por_arquivo(
            master_file,
            "ARQUIVO",
            atualizacoes,
            {"MES_REF": "MES_REF"},
        )
        if master_file
        else 0
    )
    return resumo


def _sufixo_numerico_count(nome: str) -> int:
    """Conta quantos segmentos numéricos acumulados o nome tem. BB_1234_2_2_3 → 3."""
    m = re.match(r"^BB_\d+((?:_\d+)+)\.pdf$", nome, flags=re.IGNORECASE)
    if not m:
        return 0
    return len(re.findall(r"_\d+", m.group(1)))


def _colapsar_sufixos_acumulados(
    paths: list[Path],
    index_rows: list[dict[str, str]],
    master_rows: list[dict[str, str]],
    resumo: "SaneamentoSufixosResumo",
    logger: logging.Logger,
) -> list[Path]:
    """Remove arquivos com sufixos acumulados (_2_2, _2_2_3…) que sejam cópia
    exata de outro arquivo do mesmo grupo. Retorna a lista compactada."""
    if not any(_sufixo_numerico_count(p.name) > 1 for p in paths):
        return paths

    hashes: dict[str, Path] = {}
    sobreviventes: list[Path] = []
    removidos: set[Path] = set()

    # Ordena: base sem sufixo primeiro, depois menor quantidade de sufixos
    ordenados = sorted(paths, key=lambda p: _sufixo_numerico_count(p.name))

    for p in ordenados:
        h = hashlib.md5(p.read_bytes()).hexdigest()
        if h in hashes:
            canonical = hashes[h]
            resumo.indice_atualizado += _atualizar_path_em_linhas(index_rows, "arquivo", str(p), str(canonical))
            resumo.master_atualizado += _atualizar_path_em_linhas(master_rows, "ARQUIVO", str(p), str(canonical))
            p.unlink()
            resumo.duplicatas_exatas_removidas += 1
            removidos.add(p)
            logger.info("Sufixo acumulado removido (duplicata): %s -> %s", p.name, canonical.name)
        else:
            hashes[h] = p
            sobreviventes.append(p)

    return sobreviventes


def sanear_sufixos_neoenergia(
    download_root: Path,
    *,
    index_file: Path,
    master_file: Path,
    logger: logging.Logger | None = None,
) -> SaneamentoSufixosResumo:
    logger = logger or log
    resumo = SaneamentoSufixosResumo()
    index_fields, index_rows = _ler_csv(index_file)
    master_fields, master_rows = _ler_csv(master_file)
    grupos = _saneamento_paths_bb(download_root)

    for base, paths_orig in sorted(grupos.items()):
        # Passo 1: remover sufixos acumulados (_2_2, _2_2_3…) que sejam cópias exatas
        paths = _colapsar_sufixos_acumulados(paths_orig, index_rows, master_rows, resumo, logger)

        parsed_info = [(_parse_bb_nome(p.name), p) for p in paths]
        base_paths = [p for parsed, p in parsed_info if parsed and parsed[1] is None]
        suffix_paths = [p for parsed, p in parsed_info if parsed and parsed[1] is not None]

        if base_paths and suffix_paths:
            base_path = base_paths[0]
            hash_base = hashlib.md5(base_path.read_bytes()).hexdigest()
            all_equal = True
            for atual in suffix_paths:
                if hashlib.md5(atual.read_bytes()).hexdigest() != hash_base:
                    all_equal = False
                    break
            if all_equal:
                for atual in suffix_paths:
                    resumo.indice_atualizado += _atualizar_path_em_linhas(index_rows, "arquivo", str(atual), str(base_path))
                    resumo.master_atualizado += _atualizar_path_em_linhas(master_rows, "ARQUIVO", str(atual), str(base_path))
                    atual.unlink()
                    resumo.duplicatas_exatas_removidas += 1
                    logger.info("Duplicata exata removida: %s", atual.name)
                continue

        if len(paths) == 1:
            atual = paths[0]
            parsed = _parse_bb_nome(atual.name)
            if not parsed or parsed[1] is None:
                continue
            destino = atual.with_name(f"{base}.pdf")
            if destino.exists():
                logger.warning("Destino sem sufixo ja existe, pulando: %s", destino)
                continue
            atual.rename(destino)
            resumo.renomeados_simples += 1
            resumo.indice_atualizado += _atualizar_path_em_linhas(index_rows, "arquivo", str(atual), str(destino))
            resumo.master_atualizado += _atualizar_path_em_linhas(master_rows, "ARQUIVO", str(atual), str(destino))
            logger.info("Sufixo removido: %s -> %s", atual.name, destino.name)
            continue

        if len(paths) != 2 or any(_parse_bb_nome(p.name) is None or _parse_bb_nome(p.name)[1] is None for p in paths):
            logger.warning("Grupo com conflito nao suportado automaticamente: %s", base)
            continue

        idx_refs = [row for row in index_rows if _normalizar_indice_bb(row.get("id", "")) == base]
        mst_refs = [row for row in master_rows if _normalizar_indice_bb(row.get("INDICE", "")) == base]
        if len(idx_refs) != 1 or len(mst_refs) != 1:
            logger.warning("Conflito sem referencia unica em indice/master: %s", base)
            continue

        idx_row = idx_refs[0]
        mst_row = mst_refs[0]
        idx_ref = _normalizar_ref_mes_ano(idx_row.get("mes_referencia", ""))
        mst_ref = _normalizar_ref_mes_ano(str(mst_row.get("MES_REF", "")).replace("-", "/"))

        analises = {p: analisar_pdf_neoenergia(p) for p in paths}
        path_idx = next((p for p, a in analises.items() if a.mes_referencia == idx_ref), None)
        path_mst = next((p for p, a in analises.items() if a.mes_referencia == mst_ref), None)
        if path_idx is None or path_mst is None or path_idx == path_mst:
            logger.warning("Nao consegui casar refs do conflito automaticamente: %s", base)
            continue

        novo_bb = _proximo_bb(index_rows, master_rows)
        novo_num = _bb_para_numero(novo_bb)

        destino_mst = path_mst.with_name(f"{base}.pdf")
        destino_idx = path_idx.with_name(f"{novo_bb}.pdf")
        if destino_mst.exists() or destino_idx.exists():
            logger.warning("Destino de conflito ja existe, pulando: %s", base)
            continue

        path_mst.rename(destino_mst)
        path_idx.rename(destino_idx)

        resumo.master_atualizado += _atualizar_path_em_linhas(master_rows, "ARQUIVO", str(path_mst), str(destino_mst))
        resumo.indice_atualizado += _atualizar_path_em_linhas(index_rows, "arquivo", str(path_idx), str(destino_idx))

        idx_row["id"] = novo_num
        idx_row["arquivo"] = str(destino_idx)

        mst_row["ARQUIVO"] = str(destino_mst)
        mst_row["MES_REF"] = (mst_ref or "").replace("/", "-")

        novo_master = deepcopy(mst_row)
        novo_master["INDICE"] = novo_bb
        novo_master["MES_REF"] = (idx_ref or "").replace("/", "-")
        novo_master["ARQUIVO"] = str(destino_idx)
        master_rows.append(novo_master)

        resumo.conflitos_resolvidos += 1
        resumo.master_atualizado += 1
        logger.info(
            "Conflito resolvido: %s -> %s e %s",
            base,
            destino_mst.name,
            destino_idx.name,
        )

    _gravar_csv(index_file, index_fields, index_rows)
    _gravar_csv(master_file, master_fields, master_rows)
    return resumo


def _norm_id_numero(val: str) -> str:
    """Retorna apenas a parte numérica do id, sem prefixo BB_."""
    v = str(val or "").strip().upper()
    if v.startswith("BB_"):
        v = v[3:]
    return v.lstrip("0") or "0"


def restaurar_nomes_pelo_indice(
    *,
    index_file: Path,
    master_file: Path | None = None,
    logger: logging.Logger | None = None,
) -> RestauracaoNomesResumo:
    """
    Renomeia arquivos cujo nome no disco não corresponde ao id do índice
    Neoenergia (ex.: BB_18.pdf quando o id é 2010127 → renomeia para BB_2010127.pdf).
    Atualiza a coluna 'arquivo' no índice e, se disponível, 'ARQUIVO' no master.
    """
    logger = logger or log
    resumo = RestauracaoNomesResumo()

    if not index_file.exists():
        logger.warning("restaurar_nomes: índice não encontrado: %s", index_file)
        return resumo

    index_fields, index_rows = _ler_csv(index_file)
    master_fields: list[str] = []
    master_rows: list[dict[str, str]] = []
    if master_file and master_file.exists():
        master_fields, master_rows = _ler_csv(master_file)

    BB_RE = re.compile(r"^BB_\d+((?:_\d+)*)\.pdf$", re.IGNORECASE)

    for row in index_rows:
        id_val   = (row.get("id") or "").strip()
        arq_str  = (row.get("arquivo") or "").strip()
        if not id_val or not arq_str:
            continue

        id_num   = _norm_id_numero(id_val)
        id_bb    = f"BB_{id_num}"
        arq_path = Path(arq_str)

        if not BB_RE.match(arq_path.name):
            continue

        num_arq = _norm_id_numero(arq_path.stem)  # stem de BB_18.pdf → BB_18 → 18
        # stem já inclui o prefixo BB_, normalizar:
        stem_norm = arq_path.stem.upper()
        if stem_norm.startswith("BB_"):
            num_arq = stem_norm[3:].lstrip("0") or "0"
        else:
            num_arq = stem_norm.lstrip("0") or "0"

        if num_arq == id_num:
            resumo.ja_corretos += 1
            continue

        if not arq_path.exists():
            logger.warning("restaurar_nomes: arquivo não encontrado: %s (id=%s)", arq_path.name, id_bb)
            resumo.nao_encontrados += 1
            continue

        destino = arq_path.parent / f"{id_bb}.pdf"
        if destino.exists():
            logger.warning("restaurar_nomes: conflito — %s já existe, pulando %s", destino.name, arq_path.name)
            resumo.conflitos += 1
            continue

        try:
            arq_path.rename(destino)
        except Exception as exc:
            logger.error("restaurar_nomes: erro ao renomear %s → %s: %s", arq_path.name, destino.name, exc)
            continue

        resumo.renomeados += 1
        logger.info("Restaurado: %s -> %s", arq_path.name, destino.name)

        row["arquivo"] = str(destino)
        resumo.indice_atualizado += 1

        resumo.master_atualizado += _atualizar_path_em_linhas(
            master_rows, "ARQUIVO", str(arq_path), str(destino)
        )

    _gravar_csv(index_file, index_fields, index_rows)
    if master_file and master_fields:
        _gravar_csv(master_file, master_fields, master_rows)

    return resumo
