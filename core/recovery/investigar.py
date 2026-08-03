from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from core.classificador.politica import DecisaoPolitica
from core.concessionarias.catalogo import resolver_pipeline
from core.concessionarias.modelos import ContextoExecucao, EstadoImplementacao, GrupoTensao
from core.digitacao_consen.auditoria_schema import (
    STATUS_AUDITORIA_OK,
    extrair_status_auditoria,
    ler_auditoria_csv_flexivel,
)
from core.watcher.arquivos import calcular_sha256, varrer_pdfs
from core.watcher.roteamento import rotear


CARIMBO_NOME_RE = re.compile(r"^(BB_\d+)\.pdf$", re.IGNORECASE)
MES_REF_RE = re.compile(r"(?P<mes>0[1-9]|1[0-2])[-/](?P<ano>20\d{2})")
STATUS_DIGITADO = {"DIGITADO"}
STATUS_JA_EXISTIA = {"PULADO"}


@dataclass(frozen=True)
class RecoveryPaths:
    investigar_root: Path
    output_root: Path
    watcher_v2_root: Path
    watcher_v2_staging_root: Path
    watcher_v2_output_root: Path
    indice_master_path: Path
    runtime_root: Path

    @property
    def digitadas_root(self) -> Path:
        return self.output_root / "Digitadas"

    @property
    def ja_existiam_root(self) -> Path:
        return self.output_root / "Ja_existiam_no_Consen"

    @property
    def classificar_root(self) -> Path:
        return self.investigar_root / "Classificacao"

    @property
    def nao_suportado_root(self) -> Path:
        return self.investigar_root / "Tipo_Nao_Suportado"

    @property
    def duplicatas_root(self) -> Path:
        return self.investigar_root / "Duplicatas"

    @property
    def corrompido_root(self) -> Path:
        return self.investigar_root / "Arquivo_Corrompido"

    @property
    def erros_root(self) -> Path:
        return self.investigar_root / "Erros"


@dataclass
class AuditSnapshot:
    watcher_oficial_ativo: bool
    watcher_v2_ativo: bool
    chromedriver_ativo: bool
    chrome_ativo: bool
    lock_watcher_oficial: bool
    lock_watcher_v2: bool
    lock_indice_master: bool
    tarefas_agendadas: list[str] = field(default_factory=list)
    sessoes_ativas: list[str] = field(default_factory=list)
    bloqueios: list[str] = field(default_factory=list)


@dataclass
class InventoryEntry:
    caminho_completo: str
    nome_atual: str
    tamanho_bytes: int
    mtime: str
    sha256: str
    carimbo_bb: str | None
    nome_bb_valido: bool
    copias_mesmo_hash: int
    concessionaria: str | None
    grupo: str | None
    referencia: str | None
    confianca_concessionaria: float
    confianca_grupo: float
    evidencias: list[str]
    estado_catalogo: str
    pipeline_previsto: str | None
    indice_linhas: int
    status_digitacao: str
    auditoria_status: str
    categoria_recuperacao: str
    resultado_previsto: str
    acao_planejada: str
    comando_planejado: list[str]
    staging_previsto: str | None
    destino_canonico: str | None
    erro: str = ""


def default_paths(project_root: Path) -> RecoveryPaths:
    servidor = Path(r"\\10.10.250.21\Energia")
    output_root = servidor / "CONTASDEENERGIAELETRICA" / "BB" / "ENZO"
    watcher_v2_root = output_root / "Watcher_V2"
    return RecoveryPaths(
        investigar_root=output_root / "Investigar",
        output_root=output_root,
        watcher_v2_root=watcher_v2_root,
        watcher_v2_staging_root=servidor / "ARQUIVOS ENZO" / "watcher_v2" / "staging",
        watcher_v2_output_root=watcher_v2_root,
        indice_master_path=servidor / "ARQUIVOS ENZO" / "indice_master.csv",
        runtime_root=project_root / "runtime" / "recuperacao_investigar",
    )


def _extract_text(pdf_path: Path) -> str:
    try:
        import pdfplumber

        with pdfplumber.open(str(pdf_path)) as pdf:
            partes: list[str] = []
            for page in pdf.pages[:4]:
                texto = page.extract_text() or ""
                if texto:
                    partes.append(texto)
            return "\n".join(partes)
    except Exception:
        return ""


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    for encoding in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                return list(csv.DictReader(handle))
        except UnicodeDecodeError:
            continue
    return []


def _normalizar_carimbo(value: str | None) -> str:
    raw = (value or "").strip().upper()
    if not raw:
        return ""
    if raw.endswith(".PDF"):
        raw = raw[:-4]
    if raw.startswith("BB_"):
        return raw
    if raw.isdigit():
        return f"BB_{raw}"
    return raw


def _carimbo_do_nome(name: str) -> tuple[str | None, bool]:
    match = CARIMBO_NOME_RE.fullmatch(name)
    if not match:
        return None, False
    return match.group(1).upper(), True


def _referencia_do_texto(texto: str) -> str | None:
    match = MES_REF_RE.search(texto)
    if not match:
        return None
    return f"{match.group('mes')}-{match.group('ano')}"


def _parse_grupo(grupo: str | None) -> GrupoTensao | None:
    if not grupo:
        return None
    try:
        return GrupoTensao(grupo.lower())
    except ValueError:
        return None


def _master_index(master_path: Path) -> dict[str, list[dict[str, str]]]:
    index: dict[str, list[dict[str, str]]] = {}
    for row in _read_csv_rows(master_path):
        carimbo = _normalizar_carimbo(row.get("INDICE"))
        if carimbo:
            index.setdefault(carimbo, []).append(row)
    return index


def _auditoria_index(roots: list[Path]) -> dict[str, str]:
    mapa: dict[str, str] = {}
    for root in roots:
        if not root.exists():
            continue
        for csv_path in root.rglob("auditoria_resultados.csv"):
            for row in ler_auditoria_csv_flexivel(csv_path):
                carimbo = _normalizar_carimbo(row.get("carimbo"))
                status = extrair_status_auditoria(row)
                if carimbo and status:
                    mapa[carimbo] = status
    return mapa


def _pipeline_plan(
    concessionaria: str | None,
    grupo: str | None,
    referencia: str | None,
    *,
    retomar: bool,
    staging_root: Path,
) -> tuple[str, str | None, list[str]]:
    grupo_enum = _parse_grupo(grupo)
    if not concessionaria or not grupo_enum or not referencia:
        return EstadoImplementacao.NAO_IMPLEMENTADO.value, None, []

    mes, ano = referencia.split("-", 1)
    contexto = ContextoExecucao(
        concessionaria=concessionaria,
        grupo=grupo_enum,
        mes=mes,
        ano=ano,
        pasta_entrada=staging_root,
        session_root=staging_root,
        retomar=retomar,
        dry_run=False,
    )
    resolved = resolver_pipeline(concessionaria, grupo_enum, contexto)
    return (
        resolved.get("estado", EstadoImplementacao.NAO_IMPLEMENTADO.value),
        resolved.get("pipeline"),
        [str(item) for item in resolved.get("argumentos", [])],
    )


def _destino_canonico(resultado_previsto: str, paths: RecoveryPaths, concessionaria: str | None, grupo: str | None) -> str | None:
    if resultado_previsto in {"DIGITADO", "RETOMADO_E_CONCLUIDO"}:
        return str(paths.digitadas_root)
    if resultado_previsto == "JA_EXISTIA_NO_CONSEN":
        return str(paths.ja_existiam_root)
    if resultado_previsto == "DUPLICATA":
        return str(paths.duplicatas_root)
    if resultado_previsto == "TIPO_NAO_SUPORTADO":
        conc = (concessionaria or "DESCONHECIDA").replace("/", "_")
        grp = (grupo or "DESCONHECIDO").upper()
        return str(paths.nao_suportado_root / conc / grp)
    if resultado_previsto == "REVISAO_MANUAL":
        conc = (concessionaria or "DESCONHECIDA").replace("/", "_")
        grp = (grupo or "DESCONHECIDO").upper()
        return str(paths.classificar_root / conc / grp)
    if resultado_previsto == "ARQUIVO_CORROMPIDO":
        return str(paths.corrompido_root)
    if resultado_previsto == "ERRO_TECNICO":
        return str(paths.erros_root)
    return None


def classify_entry(
    pdf_path: Path,
    *,
    sha256: str,
    duplicate_count: int,
    master_rows: dict[str, list[dict[str, str]]],
    auditorias: dict[str, str],
    staging_root: Path,
    paths: RecoveryPaths,
    text_extractor=_extract_text,
) -> InventoryEntry:
    stat = pdf_path.stat()
    carimbo_bb, nome_bb_valido = _carimbo_do_nome(pdf_path.name)
    indice_rows = master_rows.get(carimbo_bb or "", [])
    indice_linhas = len(indice_rows)
    status_digitacao = ""
    referencia = None
    if indice_linhas == 1:
        status_digitacao = (indice_rows[0].get("STATUS_DIGITACAO") or "").strip().upper()
        referencia = (indice_rows[0].get("MES_REF") or "").strip().replace("/", "-")

    auditoria_status = auditorias.get(carimbo_bb or "", "")
    texto = text_extractor(pdf_path)
    if not texto or len(texto.strip()) < 30:
        return InventoryEntry(
            caminho_completo=str(pdf_path),
            nome_atual=pdf_path.name,
            tamanho_bytes=stat.st_size,
            mtime=dt.datetime.fromtimestamp(stat.st_mtime).isoformat(),
            sha256=sha256,
            carimbo_bb=carimbo_bb,
            nome_bb_valido=nome_bb_valido,
            copias_mesmo_hash=duplicate_count,
            concessionaria=None,
            grupo=None,
            referencia=referencia,
            confianca_concessionaria=0.0,
            confianca_grupo=0.0,
            evidencias=[],
            estado_catalogo="corrompido",
            pipeline_previsto=None,
            indice_linhas=indice_linhas,
            status_digitacao=status_digitacao,
            auditoria_status=auditoria_status,
            categoria_recuperacao="H",
            resultado_previsto="ARQUIVO_CORROMPIDO",
            acao_planejada="nao processar; manter em Arquivo_Corrompido",
            comando_planejado=[],
            staging_previsto=None,
            destino_canonico=str(paths.corrompido_root),
            erro="texto insuficiente ou PDF ilegivel",
        )

    roteamento = rotear(texto, arquivo=pdf_path.name)
    concessionaria = roteamento.concessionaria.canonica
    grupo = roteamento.grupo.value if roteamento.grupo else None
    referencia = referencia or _referencia_do_texto(texto)
    estado_catalogo, pipeline_previsto, comando_planejado = _pipeline_plan(
        concessionaria,
        grupo,
        referencia,
        retomar=bool(carimbo_bb),
        staging_root=staging_root,
    )
    evidencias = [roteamento.concessionaria.evidencia, *roteamento.evidencias_grupo]
    evidencias = [item for item in evidencias if item]

    categoria = "E"
    resultado_previsto = "REVISAO_MANUAL"
    acao_planejada = "manter em Investigar/Classificacao"
    erro = ""

    if duplicate_count > 1:
        categoria = "G"
        resultado_previsto = "DUPLICATA"
        acao_planejada = "nao processar novamente; escolher copia canonica"
        comando_planejado = []
    elif carimbo_bb and indice_linhas > 1:
        categoria = "ERRO"
        resultado_previsto = "ERRO_TECNICO"
        acao_planejada = "bloquear tratamento; indice com linhas duplicadas"
        comando_planejado = []
        erro = "carimbo com mais de uma linha no indice"
    elif carimbo_bb and indice_linhas == 1 and status_digitacao in STATUS_DIGITADO:
        categoria = "A"
        resultado_previsto = "DIGITADO"
        acao_planejada = "nao redigitar; reconciliar destino canonico"
        comando_planejado = []
    elif carimbo_bb and indice_linhas == 1 and (
        auditoria_status == "pulado_referencia_existente" or status_digitacao in STATUS_JA_EXISTIA
    ):
        categoria = "B"
        resultado_previsto = "JA_EXISTIA_NO_CONSEN"
        acao_planejada = "nao redigitar; reconciliar para Ja_existiam_no_Consen"
        comando_planejado = []
    elif carimbo_bb and indice_linhas == 1 and status_digitacao in {"PENDENTE", "ERRO", ""}:
        if estado_catalogo == EstadoImplementacao.SUPORTADO.value and comando_planejado:
            categoria = "C"
            resultado_previsto = "RETOMADO_E_CONCLUIDO"
            acao_planejada = "retomar sem novo carimbo"
        else:
            categoria = "F"
            resultado_previsto = "TIPO_NAO_SUPORTADO"
            acao_planejada = "nao executar pipeline sem suporte"
            comando_planejado = []
    elif not carimbo_bb:
        if roteamento.politica.decisao == DecisaoPolitica.ACEITO_AUTOMATICAMENTE:
            if duplicate_count == 1 and estado_catalogo == EstadoImplementacao.SUPORTADO.value and comando_planejado:
                categoria = "D"
                resultado_previsto = "RETOMADO_E_CONCLUIDO"
                acao_planejada = "elegivel; carimbar somente apos staging e lock"
            else:
                categoria = "F"
                resultado_previsto = "TIPO_NAO_SUPORTADO"
                acao_planejada = "nao executar pipeline sem suporte"
                comando_planejado = []
        elif roteamento.politica.decisao == DecisaoPolitica.REVISAO_MANUAL:
            categoria = "E"
            resultado_previsto = "REVISAO_MANUAL"
            acao_planejada = "manter em Investigar/Classificacao"
            comando_planejado = []
        else:
            categoria = "E"
            resultado_previsto = "REVISAO_MANUAL"
            acao_planejada = "classificacao insuficiente; sem carimbo"
            comando_planejado = []

    destino_canonico = _destino_canonico(resultado_previsto, paths, concessionaria, grupo)
    return InventoryEntry(
        caminho_completo=str(pdf_path),
        nome_atual=pdf_path.name,
        tamanho_bytes=stat.st_size,
        mtime=dt.datetime.fromtimestamp(stat.st_mtime).isoformat(),
        sha256=sha256,
        carimbo_bb=carimbo_bb,
        nome_bb_valido=nome_bb_valido,
        copias_mesmo_hash=duplicate_count,
        concessionaria=concessionaria,
        grupo=grupo,
        referencia=referencia,
        confianca_concessionaria=roteamento.concessionaria.confianca,
        confianca_grupo=roteamento.confianca_grupo,
        evidencias=evidencias,
        estado_catalogo=estado_catalogo,
        pipeline_previsto=pipeline_previsto,
        indice_linhas=indice_linhas,
        status_digitacao=status_digitacao,
        auditoria_status=auditoria_status,
        categoria_recuperacao=categoria,
        resultado_previsto=resultado_previsto,
        acao_planejada=acao_planejada,
        comando_planejado=comando_planejado,
        staging_previsto=str(staging_root),
        destino_canonico=destino_canonico,
        erro=erro,
    )


def inventariar(paths: RecoveryPaths, *, text_extractor=_extract_text) -> list[InventoryEntry]:
    pdfs = varrer_pdfs(paths.investigar_root, recursivo=True)
    master_rows = _master_index(paths.indice_master_path)
    auditorias = _auditoria_index([paths.output_root, paths.watcher_v2_staging_root, paths.watcher_v2_output_root])

    hashes: dict[str, int] = {}
    pdf_hashes: dict[Path, str] = {}
    for pdf in pdfs:
        sha = calcular_sha256(pdf)
        pdf_hashes[pdf] = sha
        hashes[sha] = hashes.get(sha, 0) + 1

    staging_root = paths.watcher_v2_staging_root / "recuperacao_investigar"
    entries = [
        classify_entry(
            pdf,
            sha256=pdf_hashes[pdf],
            duplicate_count=hashes[pdf_hashes[pdf]],
            master_rows=master_rows,
            auditorias=auditorias,
            staging_root=staging_root,
            paths=paths,
            text_extractor=text_extractor,
        )
        for pdf in pdfs
    ]
    return sorted(entries, key=lambda item: (item.resultado_previsto, item.nome_atual))


def summarize(entries: list[InventoryEntry]) -> dict[str, int]:
    summary = {
        "total_pdfs": len(entries),
        "hashes_unicos": len({item.sha256 for item in entries}),
        "duplicatas": sum(1 for item in entries if item.resultado_previsto == "DUPLICATA"),
        "carimbados": sum(1 for item in entries if item.carimbo_bb),
        "nao_carimbados": sum(1 for item in entries if not item.carimbo_bb),
        "ja_digitados": sum(1 for item in entries if item.resultado_previsto == "DIGITADO"),
        "ja_existentes": sum(1 for item in entries if item.resultado_previsto == "JA_EXISTIA_NO_CONSEN"),
        "carimbados_pendentes": sum(1 for item in entries if item.categoria_recuperacao == "C"),
        "sem_carimbo_elegiveis": sum(1 for item in entries if item.categoria_recuperacao == "D"),
        "classificacao_incerta": sum(1 for item in entries if item.categoria_recuperacao == "E"),
        "tipo_nao_suportado": sum(1 for item in entries if item.resultado_previsto == "TIPO_NAO_SUPORTADO"),
        "corrompidos": sum(1 for item in entries if item.resultado_previsto == "ARQUIVO_CORROMPIDO"),
        "erro_tecnico": sum(1 for item in entries if item.resultado_previsto == "ERRO_TECNICO"),
        "bt": sum(1 for item in entries if item.grupo == "bt"),
        "mt": sum(1 for item in entries if item.grupo == "mt"),
        "desconhecidos": sum(1 for item in entries if not item.grupo or not item.concessionaria),
    }
    return summary


def validar_staging_recuperacao(staging_root: Path) -> Path:
    session_id = dt.datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    session_root = staging_root / "recuperacao_investigar" / session_id
    entrada = session_root / "entrada"
    pipeline = session_root / "pipeline"
    saida = session_root / "saida"
    for path in (entrada, pipeline, saida):
        path.mkdir(parents=True, exist_ok=False)

    probe = session_root / "._probe.tmp"
    probe.write_text("probe", encoding="utf-8")
    _ = probe.read_text(encoding="utf-8")
    probe.unlink()

    (session_root / "_sessao_manifesto.txt").write_text("", encoding="utf-8")
    (session_root / "_sessao_meta.json").write_text(
        json.dumps({"session_id": session_id, "criado_em": dt.datetime.now().isoformat()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return session_root


def validar_lote_homogeneo(entries: list[InventoryEntry]) -> tuple[bool, str]:
    if not entries:
        return True, ""
    grupos = {(item.concessionaria, item.grupo, item.referencia, item.categoria_recuperacao) for item in entries}
    if len(grupos) > 1:
        return False, "lote misto: concessionaria/grupo/referencia/modo divergentes"
    categorias = {item.categoria_recuperacao for item in entries}
    if "C" in categorias and "D" in categorias:
        return False, "lote misto: carimbados e nao carimbados"
    return True, ""


def escrever_inventario(paths: RecoveryPaths, entries: list[InventoryEntry]) -> tuple[Path, Path]:
    paths.runtime_root.mkdir(parents=True, exist_ok=True)
    csv_path = paths.runtime_root / "inventario.csv"
    json_path = paths.runtime_root / "inventario.json"

    rows = [asdict(item) for item in entries]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else [
            "caminho_completo", "nome_atual", "tamanho_bytes", "mtime", "sha256",
            "carimbo_bb", "nome_bb_valido", "copias_mesmo_hash", "concessionaria", "grupo",
            "referencia", "confianca_concessionaria", "confianca_grupo", "evidencias",
            "estado_catalogo", "pipeline_previsto", "indice_linhas", "status_digitacao",
            "auditoria_status", "categoria_recuperacao", "resultado_previsto", "acao_planejada",
            "comando_planejado", "staging_previsto", "destino_canonico", "erro",
        ])
        writer.writeheader()
        for row in rows:
            writer.writerow({
                **row,
                "evidencias": " | ".join(row["evidencias"]),
                "comando_planejado": " ".join(row["comando_planejado"]),
            })

    json_path.write_text(
        json.dumps({"gerado_em": dt.datetime.now().isoformat(), "itens": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return csv_path, json_path


def auditar_ambiente(paths: RecoveryPaths) -> AuditSnapshot:
    processos = _listar_processos()
    tarefas = _listar_tarefas()
    sessoes = _listar_sessoes(paths.watcher_v2_staging_root)
    watcher_oficial = any("watcher_energia" in task.lower() for task in tarefas)
    watcher_v2 = any("watcher_v2" in task.lower() for task in tarefas)
    locks = {
        "watcher_oficial": (paths.output_root / "Watcher_Energia.lock").exists(),
        "watcher_v2": (paths.watcher_v2_root / "watcher_v2.lock").exists(),
        "indice_master": (paths.indice_master_path.parent / "indice_master.csv.lock").exists(),
    }
    bloqueios: list[str] = []
    if locks["indice_master"]:
        bloqueios.append("lock do indice_master.csv presente")
    if watcher_v2 and sessoes:
        bloqueios.append("Watcher V2 com sessoes ativas")
    return AuditSnapshot(
        watcher_oficial_ativo=watcher_oficial,
        watcher_v2_ativo=watcher_v2,
        chromedriver_ativo=any("chromedriver" in proc for proc in processos),
        chrome_ativo=any(proc in {"chrome", "msedge"} for proc in processos),
        lock_watcher_oficial=locks["watcher_oficial"],
        lock_watcher_v2=locks["watcher_v2"],
        lock_indice_master=locks["indice_master"],
        tarefas_agendadas=tarefas,
        sessoes_ativas=sessoes,
        bloqueios=bloqueios,
    )


def _listar_processos() -> set[str]:
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return set()
    found: set[str] = set()
    for line in result.stdout.splitlines():
        name = line.strip().strip('"').split('","')[0].lower()
        if name:
            found.add(name.replace(".exe", ""))
    return found


def _listar_tarefas() -> list[str]:
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/FO", "LIST", "/V"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    tarefas: list[str] = []
    for line in result.stdout.splitlines():
        if line.lower().startswith("nome da tarefa:") or line.lower().startswith("taskname:"):
            tarefas.append(line.split(":", 1)[1].strip())
    return tarefas


def _listar_sessoes(staging_root: Path) -> list[str]:
    base = staging_root / "_sessoes"
    if not base.exists():
        return []
    return [item.stem for item in base.glob("*.json")]


def imprimir_resumo(entries: list[InventoryEntry]) -> str:
    summary = summarize(entries)
    linhas = [
        f"TOTAL_PDFS={summary['total_pdfs']}",
        f"HASHES_UNICOS={summary['hashes_unicos']}",
        f"DUPLICATAS={summary['duplicatas']}",
        f"CARIMBADOS={summary['carimbados']}",
        f"NAO_CARIMBADOS={summary['nao_carimbados']}",
        f"JA_DIGITADOS={summary['ja_digitados']}",
        f"JA_EXISTENTES={summary['ja_existentes']}",
        f"CARIMBADOS_PENDENTES={summary['carimbados_pendentes']}",
        f"SEM_CARIMBO_ELEGIVEIS={summary['sem_carimbo_elegiveis']}",
        f"CLASSIFICACAO_INCERTA={summary['classificacao_incerta']}",
        f"TIPO_NAO_SUPORTADO={summary['tipo_nao_suportado']}",
        f"CORROMPIDOS={summary['corrompidos']}",
        f"ERRO_TECNICO={summary['erro_tecnico']}",
    ]
    return "\n".join(linhas)


def main(argv: list[str] | None = None, *, project_root: Path | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Recuperacao segura de PDFs em Investigar")
    parser.add_argument("--inventariar", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--carimbo")
    parser.add_argument("--sha256")
    parser.add_argument("--executar", action="store_true")
    parser.add_argument("--executar-elegiveis", action="store_true")
    parser.add_argument("--lote-maximo", type=int, default=5)
    args = parser.parse_args(argv)

    root = project_root or Path(__file__).resolve().parents[2]
    paths = default_paths(root)
    audit = auditar_ambiente(paths)
    entries = inventariar(paths)
    csv_path, json_path = escrever_inventario(paths, entries)

    print(json.dumps(asdict(audit), ensure_ascii=False, indent=2))
    print(imprimir_resumo(entries))
    print(f"INVENTARIO_CSV={csv_path}")
    print(f"INVENTARIO_JSON={json_path}")

    if args.executar or args.executar_elegiveis:
        print("EXECUCAO_REAL_SUPORTADA=false")
        print("BLOQUEIO=primeira versao exposta apenas para auditoria, inventario e dry-run")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
