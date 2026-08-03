#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pipeline Lote BT — carimbo + OCR + digitação + filtro para PDFs avulsos.

Suporta todas as concessionárias da pasta BB/ENZO/Baixa Tensão.

Uso:
    python pipeline_lote_bt.py --concessionaria cpfl    --pasta "//servidor/BT/CPFL"
    python pipeline_lote_bt.py --concessionaria celesc  --pasta "//servidor/BT/CELESC"
    python pipeline_lote_bt.py --concessionaria enel    --pasta "//servidor/BT/ENEL"
    python pipeline_lote_bt.py --concessionaria eq_go   --pasta "//servidor/BT/EQUATORIAL/GOIAS"
    python pipeline_lote_bt.py --concessionaria neo_celpe --pasta "//servidor/BT/NEOENERGIA/CELPE"

Fluxo:
    1) Atribui carimbos BB_ via indice_master para PDFs sem carimbo
    2) Copia PDFs como BB_XXXXXXX.pdf para staging em ARQUIVOS ENZO/lote_bt_staging/<conc>
       (ENEL e eq_go usam staging/<conc>/<mes>-<ano>/BT/ para compatibilidade com seus pipelines)
    3) Renomeia PDFs originais na pasta fonte para BB_XXXXXXX.pdf (evita re-detecção pelo watcher)
    4) Chama o pipeline correspondente com --pasta <staging>
       (OCR -> digitação -> filtro -> move para Digitadas)

Opções de etapa isolada:
    --so-carimbo    apenas atribui carimbos e copia para staging
    --so-pipeline   pula carimbo, usa staging já existente
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

LOCAL_DIR = Path(__file__).resolve().parent.parent
SERVIDOR = Path("//10.10.250.21/Energia")
sys.path.insert(0, str(LOCAL_DIR))
sys.path.insert(0, str(LOCAL_DIR.parent))

try:
    from core.pipelines._visual import _p, _info, _ok, _fail, _warn, _sep, _banner, _rodar as _rodar_visual
except ModuleNotFoundError:
    from _visual import _p, _info, _ok, _fail, _warn, _sep, _banner, _rodar as _rodar_visual

from indice_master import MasterIndice

try:
    from sessao_meta import criar_sessao as _sm_criar, atualizar_etapa as _sm_etapa, atualizar_status as _sm_status
    _SESSAO_META_OK = True
except ImportError:
    _SESSAO_META_OK = False
    def _sm_criar(*a, **k): return None  # type: ignore[misc]
    def _sm_etapa(*a, **k): pass  # type: ignore[misc]
    def _sm_status(*a, **k): pass  # type: ignore[misc]

PYTHON_EXE = str(Path(sys.executable).parent / "python.exe")

STAGING_ROOT   = SERVIDOR / "ARQUIVOS ENZO" / "lote_bt_staging"
DIGITADAS_DIR  = SERVIDOR / "CONTASDEENERGIAELETRICA" / "BB" / "ENZO" / "Digitadas"
EXISTENTES_DIR = SERVIDOR / "CONTASDEENERGIAELETRICA" / "BB" / "ENZO" / "Ja_existiam_no_Consen"
INVESTIGAR_DIR = SERVIDOR / "CONTASDEENERGIAELETRICA" / "BB" / "ENZO" / "Watcher_V2" / "Investigar"

# ---------------------------------------------------------------------------
# MAPA DE PIPELINES
# ---------------------------------------------------------------------------

PIPELINES: dict[str, Path] = {
    # Originais
    "cpfl":       LOCAL_DIR / "pipelines" / "pipeline_cpfl_bt.py",
    "ceee":       LOCAL_DIR / "pipelines" / "pipeline_ceee_bt.py",
    "elektro":    LOCAL_DIR / "pipelines" / "pipeline_neoenergia_elektro.py",
    "rge_sul":    LOCAL_DIR / "pipelines" / "pipeline_rge_sul_bt.py",
    # Novos
    "celesc":     LOCAL_DIR / "pipelines" / "pipeline_celesc_bt.py",
    "edp_sp":     LOCAL_DIR / "pipelines" / "pipeline_edp_sp_bt.py",
    "edp_es":     LOCAL_DIR / "pipelines" / "pipeline_edp_es_bt.py",
    "enel":       LOCAL_DIR / "pipelines" / "pipeline_enel.py",
    "energisa":   LOCAL_DIR / "pipelines" / "pipeline_energisa_bt.py",
    "energisa_ac": LOCAL_DIR / "pipelines" / "pipeline_energisa_bt.py",
    "energisa_mt": LOCAL_DIR / "pipelines" / "pipeline_energisa_bt.py",
    "energisa_ms": LOCAL_DIR / "pipelines" / "pipeline_energisa_bt.py",
    "energisa_mr": LOCAL_DIR / "pipelines" / "pipeline_energisa_bt.py",
    "energisa_pb": LOCAL_DIR / "pipelines" / "pipeline_energisa_bt.py",
    "energisa_ro": LOCAL_DIR / "pipelines" / "pipeline_energisa_bt.py",
    "energisa_se": LOCAL_DIR / "pipelines" / "pipeline_energisa_bt.py",
    "energisa_ss": LOCAL_DIR / "pipelines" / "pipeline_energisa_bt.py",
    "energisa_to": LOCAL_DIR / "pipelines" / "pipeline_energisa_bt.py",
    "amazonas":   LOCAL_DIR / "pipelines" / "pipeline_pequenas_bt.py",
    "eq_go":      LOCAL_DIR / "pipelines" / "pipeline_equatorial_go.py",
    "eq_pi":      LOCAL_DIR / "pipelines" / "pipeline_equatorial_pi_bt.py",
    "neo_celpe":  LOCAL_DIR / "pipelines" / "pipeline_neoenergia_pernambuco.py",
    "neo_coelba": LOCAL_DIR / "pipelines" / "pipeline_neoenergia_bahia.py",
    "neo_cosorn": LOCAL_DIR / "pipelines" / "pipeline_neoenergia_cosern.py",
    "neo_elektro":LOCAL_DIR / "pipelines" / "pipeline_neoenergia_elektro.py",
    "pequenas":   LOCAL_DIR / "pipelines" / "pipeline_pequenas_bt.py",
    "light":      LOCAL_DIR / "pipelines" / "pipeline_light_bt.py",
    "copel_bt":   LOCAL_DIR / "pipelines" / "pipeline_copel_bt.py",
    "copel_mt":   LOCAL_DIR / "pipelines" / "pipeline_copel_mt.py",
    "eq_pa":      LOCAL_DIR / "pipelines" / "pipeline_equatorial_pa_bt.py",
    "eq_al":      LOCAL_DIR / "pipelines" / "pipeline_equatorial_al_bt.py",
    "eq_ap":      LOCAL_DIR / "pipelines" / "pipeline_equatorial_ap_bt.py",
    "eq_ma":      LOCAL_DIR / "pipelines" / "pipeline_equatorial_ma_bt.py",
    "eq_go_mt":   LOCAL_DIR / "pipelines" / "pipeline_equatorial_go.py",
    "eq_pi_mt":   LOCAL_DIR / "pipelines" / "pipeline_equatorial_pi_mt.py",
    "neo_ceb":    LOCAL_DIR / "pipelines" / "pipeline_neoenergia_ceb_bt.py",
}

SISTEMA_INFO: dict[str, dict[str, str]] = {
    "cpfl":       {"sistema": "CPFL",        "estado": "SÃO PAULO"},
    "ceee":       {"sistema": "CEEE",        "estado": "RIO GRANDE DO SUL"},
    "elektro":    {"sistema": "ELEKTRO",     "estado": "SÃO PAULO"},
    "rge_sul":    {"sistema": "RGE",         "estado": "RIO GRANDE DO SUL"},
    "celesc":     {"sistema": "CELESC",      "estado": "SANTA CATARINA"},
    "edp_sp":     {"sistema": "EDP SP",      "estado": "SÃO PAULO"},
    "edp_es":     {"sistema": "EDP ES",      "estado": "ESPÍRITO SANTO"},
    "enel":       {"sistema": "ENEL",        "estado": "SÃO PAULO"},
    "energisa":   {"sistema": "ENERGISA",    "estado": ""},
    "energisa_ac": {"sistema": "ENERGISA", "estado": "ACRE"},
    "energisa_mt": {"sistema": "ENERGISA", "estado": "MATO GROSSO"},
    "energisa_ms": {"sistema": "ENERGISA", "estado": "MATO GROSSO DO SUL"},
    "energisa_mr": {"sistema": "ENERGISA", "estado": "MINAS GERAIS"},
    "energisa_pb": {"sistema": "ENERGISA", "estado": "PARAÍBA"},
    "energisa_ro": {"sistema": "ENERGISA", "estado": "RONDÔNIA"},
    "energisa_se": {"sistema": "ENERGISA", "estado": "SERGIPE"},
    "energisa_ss": {"sistema": "ENERGISA", "estado": ""},
    "energisa_to": {"sistema": "ENERGISA", "estado": "TOCANTINS"},
    "amazonas":   {"sistema": "AMBAR ENERGIA AM", "estado": "AMAZONAS"},
    "eq_go":      {"sistema": "EQUATORIAL",  "estado": "GOIÁS"},
    "eq_pi":      {"sistema": "EQUATORIAL",  "estado": "PIAUÍ"},
    "neo_celpe":  {"sistema": "NEOENERGIA",  "estado": "PERNAMBUCO"},
    "neo_coelba": {"sistema": "NEOENERGIA",  "estado": "BAHIA"},
    "neo_cosorn": {"sistema": "NEOENERGIA",  "estado": "RIO GRANDE DO NORTE"},
    "neo_elektro":{"sistema": "NEOENERGIA",  "estado": "SÃO PAULO"},
    "pequenas":   {"sistema": "PEQUENAS_BT", "estado": ""},
    "light":      {"sistema": "LIGHT",       "estado": "RIO DE JANEIRO"},
    "copel_bt":   {"sistema": "COPEL",       "estado": "PARANÁ"},
    "copel_mt":   {"sistema": "COPEL",       "estado": "PARANÁ"},
    "eq_pa":      {"sistema": "EQUATORIAL",  "estado": "PARÁ"},
    "eq_al":      {"sistema": "EQUATORIAL",  "estado": "ALAGOAS"},
    "eq_ap":      {"sistema": "EQUATORIAL",  "estado": "AMAPÁ"},
    "eq_ma":      {"sistema": "EQUATORIAL",  "estado": "MARANHÃO"},
    "eq_go_mt":   {"sistema": "EQUATORIAL",  "estado": "GOIÁS"},
    "eq_pi_mt":   {"sistema": "EQUATORIAL",  "estado": "PIAUÍ"},
    "neo_ceb":    {"sistema": "NEOENERGIA BRASILIA", "estado": "DISTRITO FEDERAL"},
}

# Concessionárias cujo pipeline espera staging/<conc>/<mes>-<ano>/BT/ (não flat).
# Para ENEL: sem etapa_carimbar próprio, o lote faz o carimbo e copia nessa estrutura.
# Para eq_go: tem etapa_carimbar próprio, mas recebe PDFs originais (não BB_) nessa estrutura.
STAGING_COM_SUBFOLDER: set[str] = {"enel", "eq_go"}

# Concessionárias que fazem o próprio carimbo internamente — NÃO consumimos carimbo aqui.
# O lote apenas copia os originais para o staging; o pipeline downstream carimbará.
PIPELINE_FAZ_CARIMBO: set[str] = {"eq_go"}

# Flags extras por concessionária ao chamar o pipeline
PIPELINE_EXTRA_FLAGS: dict[str, list[str]] = {
    "elektro":    ["--tipo", "bt"],
    "neo_elektro":["--tipo", "ambos"],
    "enel":       ["--tipo", "bt"],
    "eq_go":      [],
}

# Pipelines que salvam auditoria_resultados.csv fora do staging_root e precisam
# receber --auditoria-saida para que pipeline_lote_bt saiba onde encontrá-la.
SUPORTA_AUDITORIA_SAIDA: set[str] = {"eq_al", "eq_ma", "eq_pi", "neo_cosorn", "rge_sul"}


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _instalacao_do_stem(stem: str) -> str:
    # Captura UC no formato "1.175.194.016-43 - 26.08" → "1.175.194.016-43"
    # ou formato simples "123456789 - 23.07" → "123456789"
    s = stem.strip()
    m = re.match(r"^([\d.]+(?:-\d+)?)\s+-\s+", s)
    if m:
        return m.group(1)
    m = re.match(r"^(\d+)", s)
    return m.group(1) if m else s


def _carimbo_do_stem(stem: str) -> str | None:
    m = re.search(r"[Bb][Bb]_(\d+)", stem)
    return f"BB_{m.group(1)}" if m else None


_RE_MES_ANO_LONGO = re.compile(r"(\d{2})[.\-_](\d{4})")
_RE_MES_ANO_CURTO = re.compile(r"\d{2}\.(\d{2})\.(\d{2})\b")


def _sha256_pdf(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _inferir_mes_ano_pdf(pdf: Path) -> tuple[str, str] | None:
    nome = pdf.stem
    m = _RE_MES_ANO_LONGO.search(nome)
    if m:
        mes, ano = m.group(1), m.group(2)
        if 1 <= int(mes) <= 12:
            return mes, ano

    m2 = _RE_MES_ANO_CURTO.search(nome)
    if m2:
        mes = m2.group(1)
        ano = f"20{m2.group(2)}"
        if 1 <= int(mes) <= 12:
            return mes, ano
    return None


def _agrupar_pdfs_por_referencia(
    pdfs: list[Path],
    mes_padrao: str,
    ano_padrao: str,
) -> dict[tuple[str, str], list[Path]]:
    grupos: dict[tuple[str, str], list[Path]] = {}
    for pdf in pdfs:
        chave = _inferir_mes_ano_pdf(pdf) or (mes_padrao, ano_padrao)
        grupos.setdefault(chave, []).append(pdf)
    return dict(sorted(grupos.items(), key=lambda item: item[0]))


# ---------------------------------------------------------------------------
# ETAPA 1 — CARIMBO + STAGING
# ---------------------------------------------------------------------------

def etapa_carimbo(
    pasta: Path,
    concessionaria: str,
    mes: str,
    ano: str,
    pdfs: list[Path] | None = None,
) -> tuple[Path, list[str], str | None]:
    """
    Atribui carimbos BB_, copia PDFs para staging e renomeia originais.

    Retorna (staging_root, carimbos, session_id).
    staging_root é o que será passado como --pasta ao pipeline individual.
    """
    staging_root = STAGING_ROOT / concessionaria
    usa_subfolder = concessionaria in STAGING_COM_SUBFOLDER
    faz_carimbo_proprio = concessionaria in PIPELINE_FAZ_CARIMBO

    # Pasta onde os PDFs serão copiados dentro do staging
    if usa_subfolder:
        staging_pdfs = staging_root / f"{mes}-{ano}" / "BT"
    else:
        staging_pdfs = staging_root

    try:
        staging_pdfs.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _fail(f"Não foi possível criar staging {staging_pdfs}: {e}")
        return staging_root, [], None

    if pdfs is None:
        pdfs = sorted(p for p in pasta.glob("*.pdf") if p.is_file())
    else:
        pdfs = sorted(pdfs)
    if not pdfs:
        _fail(f"Nenhum PDF encontrado em {pasta}")
        return staging_root, [], None

    info = SISTEMA_INFO[concessionaria]
    mes_ref = f"{mes}-{ano}"
    master = MasterIndice() if not faz_carimbo_proprio else None
    carimbos: list[str] = []
    nomes_sessao: list[str] = []
    arquivos_meta: list[dict[str, str]] = []

    for pdf in pdfs:
        carimbo_existente = _carimbo_do_stem(pdf.stem)

        if faz_carimbo_proprio:
            # Pipelines como eq_go fazem o próprio carimbo: MOVE original para staging
            destino = staging_pdfs / pdf.name
            if not destino.exists():
                shutil.move(str(pdf), str(destino))
            nomes_sessao.append(destino.name)
            _info(f"  {pdf.name} → staging (carimbo delegado ao pipeline)")
            continue

        if carimbo_existente:
            carimbo = carimbo_existente
            _info(f"  {pdf.name:<50} → {carimbo}  [reutilizado]")
        else:
            carimbo = master.consumir_carimbo()
            uc = _instalacao_do_stem(pdf.stem)
            master.registrar(
                indice_bb=carimbo,
                sistema=info["sistema"],
                uc=uc,
                mes_ref=mes_ref,
                estado=info["estado"],
                arquivo=str(staging_pdfs / f"{carimbo}.pdf"),
            )
            _info(f"  {pdf.name:<50} → {carimbo}  [novo]")

        # MOVE original para staging como BB_XXXXX.pdf (fonte fica vazia)
        destino = staging_pdfs / f"{carimbo}.pdf"
        if not destino.exists():
            try:
                shutil.move(str(pdf), str(destino))
            except OSError as e:
                _warn(f"  Não foi possível mover {pdf.name} para staging: {e}")
                shutil.copy2(pdf, destino)  # fallback: copia se não conseguir mover
                # Renomeia original para BB_*.pdf para evitar re-detecção como "novo"
                try:
                    pdf.rename(pdf.parent / f"{carimbo}.pdf")
                except OSError:
                    pass

        carimbos.append(carimbo)
        nomes_sessao.append(destino.name)
        arquivos_meta.append({
            "arquivo_origem": pdf.name,
            "arquivo_staging": destino.name,
            "carimbo": carimbo,
            "mes": mes,
            "ano": ano,
            "sha256_origem": _sha256_pdf(destino),
        })

    manifesto = staging_root / "_sessao_manifesto.txt"
    try:
        manifesto.write_text("\n".join(sorted(dict.fromkeys(nomes_sessao))), encoding="utf-8")
        _info(f"Manifesto da sessao salvo em {manifesto}")
    except OSError as e:
        _warn(f"Nao foi possivel salvar manifesto da sessao em {manifesto}: {e}")

    # Progresso por arquivo para a sessão (formato sessao_meta)
    if faz_carimbo_proprio:
        arquivos_progresso = [
            {"nome_original": n, "nome_carimbado": None, "carimbo": None,
             "status": "pendente", "ultima_etapa": "staging", "destino": None, "erro": None}
            for n in nomes_sessao
        ]
    else:
        arquivos_progresso = [
            {"nome_original": a["arquivo_origem"], "nome_carimbado": a["arquivo_staging"],
             "carimbo": a["carimbo"], "status": "carimbo_ok", "ultima_etapa": "carimbo",
             "destino": None, "erro": None}
            for a in arquivos_meta
        ]

    try:
        session_id: str | None = _sm_criar(
            staging_root, concessionaria, "BT", mes, ano, arquivos_progresso
        )
        _sm_etapa(staging_root, session_id, "carimbo", "ok",
                  quantidade=len(carimbos) if not faz_carimbo_proprio else len(pdfs))
    except Exception as _sess_err:
        _warn(f"[sessao] Nao foi possivel criar/atualizar sessao: {_sess_err}")
        session_id = None

    meta = staging_root / "_sessao_meta.json"
    meta_payload = {
        "session_id": session_id,
        "concessionaria": concessionaria,
        "mes": mes,
        "ano": ano,
        "pasta_origem": str(pasta),
        "staging_root": str(staging_root),
        "staging_pdfs": str(staging_pdfs),
        "pdfs": sorted(dict.fromkeys(nomes_sessao)),
        "arquivos": arquivos_meta,
    }
    try:
        meta.write_text(json.dumps(meta_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _info(f"Metadata da sessao salva em {meta}")
    except OSError as e:
        _warn(f"Nao foi possivel salvar metadata da sessao em {meta}: {e}")

    if not faz_carimbo_proprio:
        _ok(f"Carimbo: {len(carimbos)} PDF(s) copiado(s) para {staging_pdfs}")
    else:
        _ok(f"Staging: {len(pdfs)} PDF(s) copiado(s) para {staging_pdfs}")

    return staging_root, carimbos, session_id


# ---------------------------------------------------------------------------
# ETAPA 2 — PIPELINE
# ---------------------------------------------------------------------------

def etapa_pipeline(
    concessionaria: str,
    staging_root: Path,
    mes: str,
    ano: str,
    carimbos: list[str],
    extra_flags: list[str] | None = None,
    limpar: bool = True,
    session_id: str | None = None,
) -> int:
    pipeline = PIPELINES[concessionaria]
    cmd = [
        PYTHON_EXE, str(pipeline),
        "--pasta", str(staging_root),
        "--mes", mes,
        "--ano", ano,
    ]

    extras = PIPELINE_EXTRA_FLAGS.get(concessionaria, [])
    cmd.extend(extras)

    # Repassa carimbos específicos apenas para pipelines que suportam --carimbo
    # (originais do lote_bt: cpfl, ceee, rge_sul, elektro)
    SUPORTA_CARIMBO = {
        "cpfl", "ceee", "rge_sul", "elektro",
        "eq_al", "eq_pa", "eq_ma", "eq_pi",
        "neo_ceb", "neo_cosorn",
    }
    if concessionaria in SUPORTA_CARIMBO:
        for c in carimbos:
            cmd.extend(["--carimbo", c])

    if extra_flags:
        cmd.extend(extra_flags)

    # Para pipelines que salvam auditoria fora do staging, passar caminho explícito
    # e limpar qualquer arquivo anterior para garantir que a auditoria seja desta sessão.
    auditoria_staging = staging_root / "auditoria_resultados.csv"
    if concessionaria in SUPORTA_AUDITORIA_SAIDA:
        try:
            auditoria_staging.unlink(missing_ok=True)
        except OSError:
            pass
        cmd.extend(["--auditoria-saida", str(auditoria_staging)])

    try:
        _sm_etapa(staging_root, session_id, "pipeline_externo", "em_execucao")
    except Exception as _sess_err:
        _warn(f"[sessao] Nao foi possivel atualizar etapa pipeline_externo: {_sess_err}")
    rc = _rodar_visual(f"PIPELINE {concessionaria.upper()} BT", cmd)
    try:
        if rc == 0:
            _sm_etapa(staging_root, session_id, "pipeline_externo", "ok", rc=rc)
        else:
            _sm_etapa(staging_root, session_id, "pipeline_externo", "erro", rc=rc)
            _sm_status(staging_root, session_id, "interrompido",
                       retomavel=True, motivo=f"pipeline_externo exit {rc}")
    except Exception as _sess_err:
        _warn(f"[sessao] Nao foi possivel atualizar sessao pos-pipeline: {_sess_err}")

    indice_ok = True
    destino_ok = True
    if rc == 0:
        indice_ok = _atualizar_indice_pos_pipeline(staging_root, concessionaria, carimbos)
        destino_ok = _atualizar_arquivos_finais_pos_pipeline(carimbos, staging_root)

    # Não limpar staging no modo --so-ocr: BB_*.pdf ainda são necessários para digitação
    if limpar and rc == 0 and indice_ok and destino_ok:
        _limpar_staging(staging_root, concessionaria)
    elif limpar and rc == 0:
        _warn("[limpeza] staging preservado: índice/destino final ainda não reconciliado")
    elif limpar:
        _warn("[limpeza] staging preservado: pipeline externo falhou; evidências mantidas para diagnóstico/retry")
    return rc


def _atualizar_indice_pos_pipeline(
    staging_root: Path,
    concessionaria: str,
    carimbos: list[str] | None = None,
) -> bool:
    """Ponto central de atualização do índice mestre após pipeline bem-sucedido.

    Busca auditoria_resultados.csv recursivamente em staging_root e chama
    marcar_digitados_auditoria. Idempotente: pipelines que já chamam _atualizar_master
    internamente resultam em uma segunda escrita harmoniosa (mesmo status).

    Pipelines com atualiza_indice=True no catálogo (ex: equatorial_go) gerenciam
    o índice de forma autônoma; este ponto central é o fallback para os demais.

    Para pipelines em SUPORTA_AUDITORIA_SAIDA (eq_ma, eq_pi): auditoria_resultados.csv
    é copiado para staging_root/ via --auditoria-saida antes desta chamada.

    Retorna True se o índice foi atualizado, False caso contrário.
    """
    try:
        from core.pipelines._visual import _atualizar_master as _am
    except ModuleNotFoundError:
        from _visual import _atualizar_master as _am  # type: ignore[no-redef]

    auditorias = sorted(staging_root.rglob("auditoria_resultados.csv"))
    if not auditorias:
        if carimbos and _carimbos_com_status_terminal(carimbos):
            _info(f"[MASTER/{concessionaria}] status já reconciliado por pipeline interno")
            return True
        _warn(
            f"[MASTER/{concessionaria}] auditoria_resultados.csv não encontrado em staging — "
            "índice NÃO atualizado. Verifique se o pipeline suporta --auditoria-saida."
        )
        return False
    ok = True
    for auditoria_csv in auditorias:
        try:
            _am(auditoria_csv.parent, LOCAL_DIR)
        except Exception as exc:
            _warn(f"[MASTER/{concessionaria}] Falha ao atualizar índice ({auditoria_csv.parent.name}): {exc}")
            ok = False
    return ok


def _carimbos_com_status_terminal(carimbos: list[str]) -> bool:
    """Confirma que todos os carimbos já têm status final no índice mestre."""
    if not carimbos:
        return True
    master = MasterIndice()
    master_file = Path(master.master_file)
    if not master_file.exists():
        return False
    esperados = set(carimbos)
    encontrados: set[str] = set()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with master_file.open(newline="", encoding=enc) as fh:
                for row in csv.DictReader(fh):
                    carimbo = (row.get("INDICE") or "").strip()
                    if carimbo not in esperados:
                        continue
                    status = (row.get("STATUS_DIGITACAO") or "").strip().upper()
                    if status not in {"DIGITADO", "PULADO", "ERRO"}:
                        return False
                    encontrados.add(carimbo)
            break
        except UnicodeDecodeError:
            continue
    return encontrados == esperados


def _localizar_destino_final(carimbo: str) -> Path | None:
    """Localiza o PDF final de um carimbo em destinos operacionais conhecidos."""
    for pasta in (DIGITADAS_DIR, EXISTENTES_DIR, INVESTIGAR_DIR):
        if not pasta.exists():
            continue
        candidatos = sorted(p for p in pasta.rglob(f"{carimbo}*.pdf") if p.is_file())
        if candidatos:
            return candidatos[0]
    return None


def _hashes_esperados_da_sessao(staging_root: Path) -> dict[str, str]:
    meta = staging_root / "_sessao_meta.json"
    if not meta.exists():
        return {}
    try:
        payload = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    hashes: dict[str, str] = {}
    for item in payload.get("arquivos") or []:
        carimbo = str(item.get("carimbo") or "").strip()
        sha = str(item.get("sha256_origem") or "").strip().lower()
        if carimbo and sha:
            hashes[carimbo] = sha
    return hashes


def _atualizar_arquivos_finais_pos_pipeline(
    carimbos: list[str],
    staging_root: Path | None = None,
) -> bool:
    """Atualiza indice_master.ARQUIVO para o destino físico final real dos carimbos."""
    if not carimbos:
        return True

    master = MasterIndice()
    hashes_esperados = _hashes_esperados_da_sessao(staging_root) if staging_root else {}
    ok = True
    for carimbo in sorted(dict.fromkeys(carimbos)):
        destino = _localizar_destino_final(carimbo)
        if destino is None:
            _warn(f"[MASTER] destino final não encontrado para {carimbo}; ARQUIVO não atualizado")
            ok = False
            continue
        if not master.atualizar_arquivo_final(
            carimbo,
            destino,
            hash_esperado=hashes_esperados.get(carimbo),
        ):
            _warn(f"[MASTER] falha ao atualizar ARQUIVO final de {carimbo}: {destino}")
            ok = False
        else:
            _info(f"[MASTER] ARQUIVO final reconciliado: {carimbo} -> {destino}")
    return ok


def _limpar_staging(staging_root: Path, concessionaria: str) -> None:
    """Move BB_*.pdf restantes no staging para Investigar (não foram tratados pelo filtro)."""
    sobras = list(staging_root.rglob("BB_*.pdf"))
    if not sobras:
        return

    try:
        INVESTIGAR_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _warn(f"[limpeza] Não foi possível criar pasta Investigar: {e}")
        return

    _warn(f"[limpeza] {len(sobras)} PDF(s) não tratados pelo filtro → Investigar")
    for pdf in sobras:
        destino = INVESTIGAR_DIR / pdf.name
        if destino.exists():
            _warn(f"  [limpeza] {pdf.name} já existe em Investigar — pulando")
            continue
        try:
            shutil.move(str(pdf), str(destino))
            _info(f"  [limpeza] {pdf.name} → Investigar")
        except OSError as e:
            _warn(f"  [limpeza] Falha ao mover {pdf.name}: {e}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    hoje = dt.date.today()
    p = argparse.ArgumentParser(
        description="Pipeline Lote BT: carimbo + OCR + digitação + filtro"
    )
    p.add_argument("--concessionaria", required=True, choices=sorted(PIPELINES),
                   metavar="CONC",
                   help=", ".join(sorted(PIPELINES)))
    p.add_argument("--pasta",      required=True, help="Pasta com os PDFs originais")
    p.add_argument("--mes",        default=f"{hoje.month:02d}")
    p.add_argument("--ano",        default=str(hoje.year))
    p.add_argument("--so-carimbo",   action="store_true",
                   help="Apenas atribui carimbos e copia para staging")
    p.add_argument("--so-pipeline",  action="store_true",
                   help="Pula carimbo, usa staging já existente")
    p.add_argument("--so-ocr",       action="store_true",
                   help="Carimbo + OCR apenas (sem digitação/filtro); staging fica pronto para --so-digitacao")
    p.add_argument("--so-digitacao", action="store_true",
                   help="Digitação + filtro apenas (usa staging existente criado por --so-ocr)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    conc  = args.concessionaria
    pasta = Path(args.pasta)
    mes   = f"{int(args.mes):02d}"
    ano   = str(int(args.ano))

    _banner(f"LOTE BT — {conc.upper()}", [
        f"Pasta   : {pasta}",
        f"Ref.    : {mes}/{ano}",
    ])

    so_ocr       = args.so_ocr
    so_digitacao = args.so_digitacao

    staging_root = STAGING_ROOT / conc
    carimbos: list[str] = []
    lotes: list[tuple[str, str, Path, list[str], str | None]] = []

    # --so-digitacao: reutiliza staging já existente (igual a --so-pipeline)
    pular_carimbo = args.so_pipeline or so_digitacao

    if not pular_carimbo:
        if not pasta.exists():
            _fail(f"Pasta não encontrada: {pasta}")
            return 1
        pdfs_origem = sorted(p for p in pasta.glob("*.pdf") if p.is_file())
        grupos = _agrupar_pdfs_por_referencia(pdfs_origem, mes, ano)
        if not grupos:
            _warn("Nenhum PDF processado na etapa de carimbo.")
            return 0

        if len(grupos) > 1:
            resumo = ", ".join(
                f"{mes_ref}/{ano_ref}={len(pdfs_ref)}"
                for (mes_ref, ano_ref), pdfs_ref in grupos.items()
            )
            _info(f"Lote misto detectado; processando por referencia: {resumo}")

        for (mes_ref, ano_ref), pdfs_ref in grupos.items():
            _sep("-")
            _info(f"Sub-lote {mes_ref}/{ano_ref}: {len(pdfs_ref)} PDF(s)")
            staging_root_ref, carimbos_ref, session_id_ref = etapa_carimbo(
                pasta, conc, mes_ref, ano_ref, pdfs=pdfs_ref
            )
            if not carimbos_ref and conc not in PIPELINE_FAZ_CARIMBO:
                _warn(f"Nenhum PDF processado na etapa de carimbo para {mes_ref}/{ano_ref}.")
                continue
            lotes.append((mes_ref, ano_ref, staging_root_ref, carimbos_ref, session_id_ref))
        if not lotes:
            return 0
    else:
        usa_subfolder = conc in STAGING_COM_SUBFOLDER
        staging_pdfs = staging_root / f"{mes}-{ano}" / "BT" if usa_subfolder else staging_root
        if not staging_pdfs.exists():
            _fail(f"Staging não encontrado: {staging_pdfs}")
            return 1
        carimbos = [p.stem for p in sorted(staging_pdfs.glob("BB_*.pdf"))]
        _info(f"[so-pipeline/so-digitacao] {len(carimbos)} PDFs no staging")
        # Reutiliza session_id existente para retomada
        try:
            from sessao_meta import ler_session_id as _ler_sid
            _existing_sid = _ler_sid(staging_root)
        except ImportError:
            _existing_sid = None
        lotes.append((mes, ano, staging_root, carimbos, _existing_sid))

    if args.so_carimbo:
        _ok("Staging pronto. Rode sem --so-carimbo para continuar.")
        return 0

    extra_flags: list[str] = []
    if so_ocr:
        extra_flags = ["--so-ocr"]
    elif so_digitacao:
        extra_flags = ["--so-digitacao"]

    rc_final = 0
    for mes_ref, ano_ref, staging_root_ref, carimbos_ref, session_id_ref in lotes:
        _sep("-")
        _info(f"Executando pipeline do sub-lote {mes_ref}/{ano_ref}")
        rc = etapa_pipeline(
            conc, staging_root_ref, mes_ref, ano_ref, carimbos_ref,
            extra_flags=extra_flags,
            limpar=not so_ocr,  # staging fica intacto no modo --so-ocr
            session_id=session_id_ref,
        )
        if rc != 0:
            rc_final = rc
        else:
            try:
                _sm_status(staging_root_ref, session_id_ref, "concluido",
                           retomavel=False, motivo=None, etapa_atual="pipeline_externo")
            except Exception as _sess_err:
                _warn(f"[sessao] Nao foi possivel finalizar sessao: {_sess_err}")

    return rc_final


if __name__ == "__main__":
    raise SystemExit(main())
