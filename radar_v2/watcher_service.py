"""
radar/watcher_service.py — back-end somente leitura para monitoramento do watcher.

Fontes (prioridade):
  1. _sessoes/<session_id>.json
  2. _sessao_meta.json
  3. indice_master.csv
  4. localização física do PDF
  5. auditoria_resultados.csv da sessão
  6. logs/watcher.log

Nenhum arquivo de produção é alterado por este módulo.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from core.sessao_meta import ler_session_id, listar_sessoes

try:
    from core.pipelines._session_runtime import PIPELINE_SESSION_ROOT
except Exception:  # pragma: no cover - fallback em ambientes antigos
    PIPELINE_SESSION_ROOT = ROOT / "logs" / "pipeline_sessoes"

try:
    import psutil as _psutil
    _PSUTIL = True
except ImportError:
    _psutil = None  # type: ignore[assignment]
    _PSUTIL = False

# ── Caminhos (espelham a implantacao do watcher) ───────────────────────────
ROOT         = Path(__file__).resolve().parent.parent
LOG_FILE     = ROOT / "logs" / "watcher.log"
LOCK_FILE    = ROOT / "watcher.lock"
ACOES_LOG    = ROOT / "logs" / "radar_v2_acoes_watcher.jsonl"

SERVIDOR     = Path("//10.10.250.21/Energia")
PASTA_RAIZ   = SERVIDOR / "CONTASDEENERGIAELETRICA/BB/ENZO/Faturas"
STAGING_ROOT = SERVIDOR / "ARQUIVOS ENZO/lote_bt_staging"
INVESTIGAR   = SERVIDOR / "CONTASDEENERGIAELETRICA/BB/ENZO/Investigar"
DIGITADAS    = SERVIDOR / "CONTASDEENERGIAELETRICA/BB/ENZO/Digitadas"
JA_EXISTIAM  = SERVIDOR / "CONTASDEENERGIAELETRICA/BB/ENZO/Ja_existiam_no_Consen"
INDICE       = SERVIDOR / "ARQUIVOS ENZO/indice_master.csv"

TASK_NAME    = r"\Watcher_Energia"
PERIODO_MIN  = 10
ALERTA_ATEN  = 20 * 60   # segundos
ALERTA_CRIT  = 60 * 60

_USUARIO = os.environ.get("USERNAME") or os.environ.get("USER") or "desconhecido"


# ── Cache thread-safe ─────────────────────────────────────────────────────────

class _Cache:
    """TTL cache thread-safe com retorno de dados obsoletos em caso de falha."""

    __slots__ = ("_mu", "_data", "_ts_ok", "_ts_try", "_erro", "_ttl", "_refreshing")

    def __init__(self, ttl: float) -> None:
        self._mu    = threading.Lock()
        self._data: Any = None
        self._ts_ok  = 0.0
        self._ts_try = 0.0
        self._erro: str | None = None
        self._ttl   = ttl
        self._refreshing = False

    def get(self, fn: Callable[[], Any]) -> dict:
        now = time.monotonic()
        with self._mu:
            has_data = self._data is not None
            fresh = has_data and (now - self._ts_ok) < self._ttl
            if fresh:
                return {"data": self._data, "disponivel": True,
                        "dados_obsoletos": False,
                        "ultima_atualizacao": self._ts_iso(self._ts_ok),
                        "erro": None}

            if not self._refreshing:
                self._refreshing = True
                self._ts_try = now
                if not has_data:
                    try:
                        novo = fn()
                        self._data = novo
                        self._ts_ok = time.monotonic()
                        self._erro = None
                    except Exception as exc:
                        self._erro = str(exc)
                    finally:
                        self._refreshing = False
                else:
                    threading.Thread(target=self._refresh, args=(fn,), daemon=True).start()

            if has_data or self._data is not None:
                return {"data": self._data, "disponivel": True,
                        "dados_obsoletos": (now - self._ts_ok) >= self._ttl,
                        "ultima_atualizacao": self._ts_iso(self._ts_ok),
                        "erro": self._erro}

            return {"data": None, "disponivel": False,
                    "dados_obsoletos": False,
                    "ultima_atualizacao": None,
                    "erro": self._erro}

    def _refresh(self, fn: Callable[[], Any]) -> None:
        try:
            novo = fn()
            with self._mu:
                self._data = novo
                self._ts_ok = time.monotonic()
                self._erro = None
        except Exception as exc:
            with self._mu:
                self._erro = str(exc)
        finally:
            with self._mu:
                self._refreshing = False

    def _ts_iso(self, mono: float) -> str | None:
        if not mono:
            return None
        diff = time.monotonic() - mono
        return (datetime.now() - timedelta(seconds=diff)).isoformat(timespec="seconds")


_cache_sessoes  = _Cache(ttl=20.0)
_cache_indice   = _Cache(ttl=60.0)
_cache_arquivos = _Cache(ttl=60.0)
_cache_tarefa   = _Cache(ttl=30.0)


# ── Alertas reconhecidos ──────────────────────────────────────────────────────

_rec_mu  = threading.Lock()
_rec_ids: set[str] = set()
_rec_ts  = 0.0


def _ler_reconhecidos() -> set[str]:
    global _rec_ids, _rec_ts
    now = time.monotonic()
    with _rec_mu:
        if (now - _rec_ts) < 30.0:
            return set(_rec_ids)
    ids: set[str] = set()
    try:
        if ACOES_LOG.exists():
            with ACOES_LOG.open("r", encoding="utf-8", errors="replace") as f:
                for linha in f:
                    try:
                        d = json.loads(linha)
                        if d.get("acao") == "reconhecer_alerta" and d.get("alerta_id"):
                            ids.add(d["alerta_id"])
                    except Exception:
                        pass
    except Exception:
        pass
    with _rec_mu:
        _rec_ids = ids
        _rec_ts = time.monotonic()
    return ids


# ── Lock ──────────────────────────────────────────────────────────────────────

def _processo_parece_watcher(pid: int) -> bool:
    try:
        proc = _psutil.Process(pid)
        name = (proc.name() or "").lower()
        cmd = " ".join(proc.cmdline() or []).lower()
        cwd = (proc.cwd() or "").lower()
        if any(token in name for token in ("watcher", "python", "cmd", "powershell")):
            if "watcher" in cmd or "radar" in cmd or "rodar_watcher" in cmd:
                return True
        if "watcher" in cwd or "energia" in cwd:
            return True
    except Exception:
        return False
    return False


def _analisar_lock() -> dict:
    if not LOCK_FILE.exists():
        return {"existe": False, "status": "livre", "pid": None}
    try:
        idade_s = int(time.time() - LOCK_FILE.stat().st_mtime)
    except OSError:
        return {"existe": True, "status": "incerto", "pid": None}

    try:
        conteudo = LOCK_FILE.read_text(encoding="utf-8").strip()
        pid = int(conteudo) if conteudo.isdigit() else None
    except Exception:
        pid = None

    pid_ativo: bool | None = None
    status: str = "incerto"
    if pid is not None and _PSUTIL:
        try:
            if _psutil.pid_exists(pid):
                pid_ativo = True
                if _processo_parece_watcher(pid):
                    status = "ativo"
                else:
                    status = "possivelmente_obsoleto"
            else:
                pid_ativo = False
                status = "possivelmente_obsoleto" if idade_s > PERIODO_MIN * 60 else "incerto"
        except Exception:
            pid_ativo = None
            status = "possivelmente_obsoleto" if idade_s > PERIODO_MIN * 60 * 2 else "incerto"
    elif pid is not None:
        status = "possivelmente_obsoleto" if idade_s > PERIODO_MIN * 60 * 2 else "incerto"
    else:
        status = "incerto"

    return {"existe": True, "status": status, "pid": pid,
            "pid_ativo": pid_ativo, "idade_s": idade_s}


# ── Tarefa agendada (schtasks) ────────────────────────────────────────────────

def _ler_tarefa_raw() -> dict:
    try:
        res = subprocess.run(
            ["schtasks", "/query", "/tn", TASK_NAME, "/fo", "LIST", "/v"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
    except Exception as exc:
        raise RuntimeError(f"schtasks indisponivel: {exc}") from exc
    if res.returncode != 0:
        raise RuntimeError(f"schtasks exit {res.returncode}: {res.stderr.strip()[:200]}")

    parsed: dict[str, str] = {}
    for linha in res.stdout.splitlines():
        if ":" in linha:
            chave, _, valor = linha.partition(":")
            parsed[chave.strip()] = valor.strip()

    def _get(*chaves: str) -> str | None:
        for c in chaves:
            v = parsed.get(c)
            if v and v not in ("N/A", "Nunca", "Never"):
                return v
        return None

    def _parse_dt(s: str | None) -> str | None:
        if not s:
            return None
        for fmt in ("%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(s, fmt).isoformat(timespec="seconds")
            except ValueError:
                pass
        return s

    return {
        "nome": TASK_NAME,
        "estado": _get("Status", "Estado"),
        "ultima_execucao_agendada": _parse_dt(_get("Last Run Time", "Último Horário de Execução")),
        "proxima_execucao_agendada": _parse_dt(_get("Next Run Time", "Próximo Horário de Execução")),
        "ultimo_resultado": _get("Last Result", "Último Resultado"),
        "modo_logon": _get("Logon Mode", "Modo de Logon"),
        "comando": _get("Task To Run", "Tarefa a Executar"),
        "usuario": _get("Run As User", "Executar Como Usuário"),
    }


def obter_tarefa_agendada() -> dict:
    r = _cache_tarefa.get(_ler_tarefa_raw)
    tarefa = r["data"] or {}
    return {
        "disponivel": r["disponivel"],
        "dados_obsoletos": r["dados_obsoletos"],
        "ultima_atualizacao": r["ultima_atualizacao"],
        "erro": r["erro"],
        "estado": tarefa.get("estado"),
        "ultima_execucao": tarefa.get("ultima_execucao_agendada"),
        "proxima_execucao": tarefa.get("proxima_execucao_agendada"),
        "ultimo_resultado": tarefa.get("ultimo_resultado"),
        "usuario": tarefa.get("usuario"),
        "modo_execucao": tarefa.get("modo_logon"),
        "comando": tarefa.get("comando"),
        "tarefa": tarefa,
    }


# ── Log do watcher ────────────────────────────────────────────────────────────

_LOG_TAIL = 512 * 1024  # 512 KB


def _ler_log_tail(max_bytes: int = _LOG_TAIL) -> list[str]:
    if not LOG_FILE.exists():
        return []
    try:
        with LOG_FILE.open("rb") as f:
            f.seek(0, 2)
            tamanho = f.tell()
            inicio = max(0, tamanho - max_bytes)
            f.seek(inicio)
            raw = f.read()
    except OSError:
        return []

    texto = None
    for enc in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            texto = raw.decode(enc, errors="strict")
            break
        except UnicodeDecodeError:
            continue
    if texto is None:
        texto = raw.decode("cp1252", errors="replace")

    linhas = texto.splitlines()
    if inicio > 0 and linhas:
        linhas = linhas[1:]
    return linhas


def _ultima_atividade_log() -> dict:
    linhas = _ler_log_tail(200 * 1024)
    ultimo_inicio = ultimo_fim = ultimo_ts = None
    for linha in reversed(linhas):
        if not linha or len(linha) < 19:
            continue
        ts_str = linha[:19]
        try:
            datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if ultimo_ts is None:
            ultimo_ts = ts_str
        if "watcher finalizado" in linha and not ultimo_fim:
            ultimo_fim = ts_str
        if "watcher iniciado" in linha and not ultimo_inicio:
            ultimo_inicio = ts_str
        if ultimo_inicio and ultimo_fim:
            break
    return {"ultimo_ts_log": ultimo_ts, "ultimo_inicio": ultimo_inicio, "ultimo_fim": ultimo_fim}


def obter_logs(
    limit: int = 100,
    offset: int = 0,
    session_id: str | None = None,
    carimbo: str | None = None,
    concessionaria: str | None = None,
    nivel: str | None = None,
) -> dict:
    limit = min(max(1, limit), 1000)
    linhas = _ler_log_tail(_LOG_TAIL)
    filtradas = [
        l for l in linhas
        if (not session_id or session_id in l)
        and (not carimbo or carimbo in l)
        and (not concessionaria or concessionaria.upper() in l.upper())
        and (not nivel or nivel.upper() in l.upper())
    ]
    sub = filtradas[offset: offset + limit]
    texto = "\n".join(sub)
    return {
        "linhas": sub,
        "texto": texto,
        "log": texto,
        "logs": texto,
        "total": len(filtradas),
        "offset": offset,
        "limit": limit,
        "log_file": str(LOG_FILE),
    }


# ── Índice master ─────────────────────────────────────────────────────────────

def _ler_indice_raw() -> dict[str, dict]:
    resultado: dict[str, dict] = {}
    try:
        with INDICE.open(newline="", encoding="utf-8-sig", errors="replace") as f:
            for row in csv.DictReader(f):
                idx = (row.get("INDICE") or "").strip()
                if idx:
                    resultado[idx] = dict(row)
    except Exception as exc:
        raise RuntimeError(f"Indice inacessivel: {exc}") from exc
    return resultado


def _obter_indice() -> dict[str, dict]:
    return _cache_indice.get(_ler_indice_raw)["data"] or {}


# ── Sessões ───────────────────────────────────────────────────────────────────

def _ler_sessoes_raw() -> list[dict]:
    roots = [STAGING_ROOT, PIPELINE_SESSION_ROOT]
    sessoes: list[dict] = []
    for root in roots:
        if not root.exists():
            continue
        try:
            sessoes.extend(listar_sessoes(root))
        except Exception as exc:
            raise RuntimeError(f"Erro ao listar sessoes em {root}: {exc}") from exc

    if not sessoes:
        raise RuntimeError(f"Nenhuma raiz de sessao acessivel: {', '.join(str(r) for r in roots)}")

    try:
        active_id = ler_session_id(STAGING_ROOT)
        if active_id:
            active_path = STAGING_ROOT / "_sessao_meta.json"
            if active_path.exists():
                try:
                    active_data = json.loads(active_path.read_text(encoding="utf-8-sig"))
                    active_data["_path"] = str(active_path)
                    if not any(s.get("session_id") == active_data.get("session_id") for s in sessoes):
                        sessoes.insert(0, active_data)
                except Exception:
                    pass
    except Exception:
        pass

    sessoes.sort(key=lambda s: s.get("criado_em") or s.get("updated_at") or s.get("atualizado_em") or "", reverse=True)
    return sessoes


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    for candidate in (
        text,
        text.replace("Z", "+00:00"),
        text[:19],
    ):
        try:
            return datetime.fromisoformat(candidate)
        except Exception:
            continue
    return None


def _sessao_momento(sessao: dict) -> datetime | None:
    for chave in ("atualizado_em", "updated_at", "criado_em", "created_at"):
        ts = _parse_ts(sessao.get(chave))
        if ts is not None:
            return ts
    return None


def _deduplicar_sessoes(sessoes: list[dict]) -> list[dict]:
    por_id: dict[str, dict] = {}
    for sessao in sessoes:
        session_id = (sessao.get("session_id") or "").strip()
        if not session_id:
            continue
        atual = por_id.get(session_id)
        if atual is None:
            por_id[session_id] = sessao
            continue

        momento_atual = _sessao_momento(atual)
        momento_novo = _sessao_momento(sessao)
        if momento_novo and not momento_atual:
            por_id[session_id] = sessao
            continue
        if momento_novo and momento_atual and momento_novo > momento_atual:
            por_id[session_id] = sessao
            continue

        # Se os timestamps forem equivalentes, prefere o arquivo ativo de
        # compatibilidade (_sessao_meta.json) quando estiver presente.
        if str(sessao.get("_path") or "").endswith("_sessao_meta.json"):
            por_id[session_id] = sessao

    return list(por_id.values())


_TIMELINE_LABELS = {
    "deteccao": "Detecção",
    "carimbo": "Carimbo",
    "ocr": "OCR",
    "validacao_lote": "Validação do lote",
    "digitacao": "Digitação",
    "filtro": "Filtro",
    "execucao": "Execução",
}


def _timeline_sessao(sessao: dict) -> list[dict]:
    progresso = sessao.get("progresso") or {}
    timeline: list[dict] = []
    if not isinstance(progresso, dict):
        return timeline

    for etapa in ("deteccao", "carimbo", "ocr", "validacao_lote", "digitacao", "filtro", "execucao"):
        etapa_data = progresso.get(etapa)
        if not isinstance(etapa_data, dict):
            continue
        timeline.append({
            "etapa": etapa,
            "label": _TIMELINE_LABELS.get(etapa, etapa.replace("_", " ").title()),
            "status": etapa_data.get("status"),
            "quantidade": etapa_data.get("quantidade"),
            "inicio": etapa_data.get("inicio") or etapa_data.get("criado_em"),
            "fim": etapa_data.get("fim") or etapa_data.get("atualizado_em"),
            "timestamp": etapa_data.get("atualizado_em") or etapa_data.get("criado_em") or sessao.get("atualizado_em") or sessao.get("criado_em"),
            "campos": {k: v for k, v in etapa_data.items() if k not in {"status", "quantidade", "inicio", "fim", "criado_em", "atualizado_em"}},
        })

    return timeline


def _normalizar_arquivo_sessao(sessao: dict, arquivo: dict) -> dict:
    nome_original = (
        arquivo.get("nome_original")
        or arquivo.get("arquivo_origem")
        or arquivo.get("arquivo")
        or arquivo.get("nome")
    )
    nome_carimbado = (
        arquivo.get("nome_carimbado")
        or arquivo.get("arquivo_staging")
        or arquivo.get("arquivo_bb")
    )
    carimbo = (arquivo.get("carimbo") or "").strip() or None
    if not carimbo and nome_carimbado:
        m = re.search(r"(BB_)?(\d+)\.pdf$", str(nome_carimbado), flags=re.I)
        if m:
            carimbo = m.group(2)

    destino = (
        arquivo.get("destino")
        or arquivo.get("destino_final")
        or arquivo.get("destino_fim")
    )
    localizacao = arquivo.get("localizacao") or destino
    if not localizacao:
        if nome_carimbado:
            localizacao = "staging"
        elif nome_original:
            localizacao = "entrada"

    status = arquivo.get("status") or "pendente"
    ultima_etapa = arquivo.get("ultima_etapa") or arquivo.get("etapa") or "Nao informado"

    return {
        "arquivo": nome_carimbado or nome_original or (f"BB_{carimbo}.pdf" if carimbo else "Nao informado"),
        "arquivo_original": nome_original,
        "arquivo_bb": nome_carimbado or (f"BB_{carimbo}.pdf" if carimbo else None),
        "nome_original": nome_original,
        "nome_carimbado": nome_carimbado,
        "carimbo": carimbo,
        "instalacao": arquivo.get("instalacao") or arquivo.get("uc") or arquivo.get("unidade") or "Nao informado",
        "referencia": arquivo.get("referencia") or sessao.get("referencia") or "Nao informado",
        "grupo": arquivo.get("grupo") or sessao.get("grupo") or "Nao informado",
        "status": status,
        "ultima_etapa": ultima_etapa,
        "localizacao": localizacao or "Nao informado",
        "destino": destino,
        "erro": arquivo.get("erro"),
    }


def _enriquecer_sessao(sessao: dict, indice: dict) -> dict:
    s = _enriquecer(sessao, indice)
    progresso = s.get("progresso") or sessao.get("progresso") or {}
    arquivos = sessao.get("arquivos") or []
    s["timeline"] = _timeline_sessao(sessao)
    s["arquivos"] = [_normalizar_arquivo_sessao(s, arq if isinstance(arq, dict) else {}) for arq in arquivos]
    s["quantidade_pdfs"] = len(s["arquivos"])
    s["quantidade_arquivos"] = len(s["arquivos"])
    s["quantidade_concluidos"] = sum(1 for arq in s["arquivos"] if str(arq.get("status") or "").lower() in {"concluido", "digitado", "filtrado", "ok"})
    s["quantidade_erro"] = sum(1 for arq in s["arquivos"] if str(arq.get("status") or "").lower() in {"erro", "falha", "interrompido"})
    s["quantidade_pendentes"] = sum(1 for arq in s["arquivos"] if str(arq.get("status") or "").lower() in {"pendente", "carimbo_ok", "em_execucao"})
    s["progresso"] = progresso if isinstance(progresso, dict) else {}
    s["inicio"] = s.get("created_at") or s.get("criado_em")
    s["fim"] = s.get("updated_at") or s.get("atualizado_em")
    s["duracao_s"] = None
    inicio_ts = _parse_ts(s.get("inicio"))
    fim_ts = _parse_ts(s.get("fim"))
    if inicio_ts and fim_ts:
        s["duracao_s"] = max(0, int((fim_ts - inicio_ts).total_seconds()))
    return s


def _localizar_pdf(carimbo: str) -> str | None:
    """Retorna 'digitadas', 'ja_existiam', 'investigar', 'staging' ou None."""
    nome = f"{carimbo}.pdf" if carimbo.startswith("BB_") else f"BB_{carimbo}.pdf"
    for rotulo, pasta in (("digitadas", DIGITADAS), ("ja_existiam", JA_EXISTIAM),
                           ("investigar", INVESTIGAR)):
        try:
            if (pasta / nome).exists():
                return rotulo
        except OSError:
            pass
    try:
        for e in os.scandir(str(STAGING_ROOT)):
            if e.is_dir():
                try:
                    if (Path(e.path) / nome).exists():
                        return "staging"
                except OSError:
                    pass
    except OSError:
        pass
    return None


def _derivar_reconciliacao(sessao: dict, indice: dict) -> dict:
    arquivos = sessao.get("arquivos", [])
    status_sessao = sessao.get("status", "")

    if not arquivos:
        return {"status_reconciliacao": "resultado_desconhecido",
                "legada": True, "fontes_divergentes": False, "detalhes_fontes": []}

    if status_sessao not in ("concluido", "pipeline_ok"):
        return {"status_reconciliacao": "pendente",
                "legada": False, "fontes_divergentes": False, "detalhes_fontes": []}

    # Só checar fisicamente sessões recentes (últimos 30 dias)
    ts_str = sessao.get("atualizado_em") or sessao.get("criado_em") or ""
    recente = True
    if ts_str:
        try:
            ts_dt = datetime.fromisoformat(ts_str[:19])
            recente = (datetime.now() - ts_dt).days <= 30
        except Exception:
            pass

    resultados: list[str] = []
    divergentes: list[dict] = []

    for arq in arquivos:
        carimbo_raw = (arq.get("carimbo") or "").strip()
        if not carimbo_raw:
            resultados.append("resultado_desconhecido")
            continue

        idx_key = carimbo_raw if carimbo_raw.startswith("BB_") else f"BB_{carimbo_raw}"
        entrada_idx = indice.get(idx_key, {})
        status_idx = (entrada_idx.get("STATUS_DIGITACAO") or "").strip()
        arquivo_idx = (entrada_idx.get("ARQUIVO") or "").strip()

        # Localização física (apenas se recente e relevante)
        localizacao: str | None = None
        if recente and status_idx in ("DIGITADO", "PULADO", "ERRO", ""):
            localizacao = _localizar_pdf(idx_key)

        fontes: dict = {
            "indice": status_idx or "nao_encontrado",
            "sessao": arq.get("status") or "nao_informado",
        }
        if localizacao:
            fontes["localizacao_fisica"] = localizacao

        if not status_idx:
            resultados.append("resultado_desconhecido")
            divergentes.append({"carimbo": idx_key, "motivo": "nao encontrado no indice", **fontes})
        elif status_idx == "DIGITADO":
            if localizacao == "digitadas":
                resultados.append("confirmada")
            elif localizacao == "investigar":
                resultados.append("inconsistente")
                divergentes.append({"carimbo": idx_key,
                                     "motivo": "STATUS=DIGITADO mas PDF em Investigar/", **fontes})
            elif localizacao == "staging":
                resultados.append("inconsistente")
                divergentes.append({"carimbo": idx_key,
                                     "motivo": "STATUS=DIGITADO mas PDF ainda no staging", **fontes})
            elif localizacao is None and recente:
                resultados.append("resultado_desconhecido")
                divergentes.append({"carimbo": idx_key,
                                     "motivo": "STATUS=DIGITADO, PDF nao localizado", **fontes})
            else:
                resultados.append("confirmada")
        elif status_idx == "PULADO":
            resultados.append("confirmada_com_pulados")
        elif status_idx in ("PENDENTE", "ERRO"):
            resultados.append("pendente")
        else:
            resultados.append("resultado_desconhecido")

    if not resultados:
        rec = "resultado_desconhecido"
    elif "inconsistente" in resultados:
        rec = "inconsistente"
    elif "pendente" in resultados:
        rec = "pendente"
    elif "resultado_desconhecido" in resultados:
        rec = "resultado_desconhecido"
    elif "confirmada_com_pulados" in resultados:
        rec = "confirmada_com_pulados"
    else:
        rec = "confirmada"

    return {"status_reconciliacao": rec, "legada": False,
            "fontes_divergentes": bool(divergentes), "detalhes_fontes": divergentes}


def _enriquecer(sessao: dict, indice: dict) -> dict:
    s = dict(sessao)
    s.setdefault("session_id", Path(s.get("_path", "")).stem or "desconhecido")
    s.setdefault("concessionaria", "Nao informado")
    s.setdefault("grupo", "Nao informado")
    s.setdefault("referencia", "Nao informado")
    s.setdefault("status", "desconhecido")
    s.setdefault("execucao_status", s.get("status"))
    s.setdefault("reconciliacao_status", s.get("status_reconciliacao"))
    s.setdefault("etapa_atual", "Nao informado")
    s.setdefault("criado_em", None)
    s.setdefault("atualizado_em", None)
    s.setdefault("created_at", s.get("criado_em"))
    s.setdefault("updated_at", s.get("atualizado_em"))
    s.setdefault("atualizacao", s.get("atualizado_em") or s.get("criado_em"))
    s.setdefault("pdfs", len(s.get("arquivos") or []))
    s.setdefault("origem", "Nao informado")
    s.setdefault("staging", "Nao informado")
    s.setdefault("xlsx", "Nao informado")
    s.setdefault("auditoria", "Nao informado")
    s.setdefault("return_code", None)
    s.setdefault("retomavel", False)
    s.setdefault("motivo_parada", None)
    s.setdefault("arquivos", [])

    if s.pop("_erro_leitura", False):
        s["_legada"] = True
        s["status_reconciliacao"] = "resultado_desconhecido"
        s["fontes_divergentes"] = False
        s.pop("_path", None)
        return s

    recon = _derivar_reconciliacao(s, indice)
    s["status_reconciliacao"] = recon["status_reconciliacao"]
    s["fontes_divergentes"] = recon["fontes_divergentes"]
    if recon["fontes_divergentes"]:
        s["detalhes_fontes"] = recon["detalhes_fontes"]
    s["_legada"] = recon.get("legada", False)
    s.pop("_path", None)
    return s


def _ler_sessoes_enriquecidas() -> list[dict]:
    indice = _obter_indice()
    sessoes = _deduplicar_sessoes(_ler_sessoes_raw())
    sessoes.sort(key=lambda s: _sessao_momento(s) or datetime.min, reverse=True)
    return [_enriquecer_sessao(s, indice) for s in sessoes]


def obter_sessoes(
    status: str | None = None,
    concessionaria: str | None = None,
    grupo: str | None = None,
    referencia: str | None = None,
    status_reconciliacao: str | None = None,
    session_id: str | None = None,
    q: str | None = None,
) -> dict:
    r = _cache_sessoes.get(_ler_sessoes_enriquecidas)
    sess: list[dict] = list(r["data"] or [])

    if status and status not in ("todas", ""):
        if status == "pendentes":
            sess = [s for s in sess if s["status"] != "concluido"]
        elif status in ("retomaveis", "retomáveis"):
            sess = [s for s in sess if s.get("retomavel")]
        elif status in ("concluidas", "concluido"):
            sess = [s for s in sess if s.get("status") == "concluido"]
        elif status in ("inconsistentes", "inconsistente"):
            sess = [s for s in sess if s.get("status_reconciliacao") == "inconsistente"]
        else:
            sess = [s for s in sess if s.get("status") == status]

    if status_reconciliacao:
        sess = [
            s for s in sess
            if (s.get("status_reconciliacao") or "").lower() == status_reconciliacao.lower()
        ]

    if concessionaria:
        sess = [s for s in sess if s.get("concessionaria", "").lower() == concessionaria.lower()]
    if grupo:
        sess = [s for s in sess if s.get("grupo", "").upper() == grupo.upper()]
    if referencia:
        sess = [s for s in sess if referencia in (s.get("referencia") or "")]
    if session_id:
        sess = [s for s in sess if (s.get("session_id") or "").lower() == session_id.lower()]
    if q:
        needle = q.strip().lower()
        if needle:
            def _match(s: dict) -> bool:
                campos = [
                    s.get("session_id"),
                    s.get("concessionaria"),
                    s.get("grupo"),
                    s.get("referencia"),
                    s.get("status"),
                    s.get("execucao_status"),
                    s.get("reconciliacao_status"),
                    s.get("etapa_atual"),
                    s.get("motivo_parada"),
                ]
                for arq in s.get("arquivos") or []:
                    campos.extend([
                        arq.get("arquivo"),
                        arq.get("arquivo_original"),
                        arq.get("arquivo_bb"),
                        arq.get("nome_original"),
                        arq.get("nome_carimbado"),
                        arq.get("carimbo"),
                        arq.get("instalacao"),
                        arq.get("destino"),
                        arq.get("localizacao"),
                        arq.get("status"),
                        arq.get("erro"),
                    ])
                return any(needle in str(campo).lower() for campo in campos if campo not in (None, ""))

            sess = [s for s in sess if _match(s)]

    return {"disponivel": r["disponivel"], "dados_obsoletos": r["dados_obsoletos"],
            "ultima_atualizacao": r["ultima_atualizacao"], "erro": r["erro"],
            "sessoes": sess, "total": len(sess)}


def obter_sessao_detalhe(session_id: str) -> dict | None:
    r = _cache_sessoes.get(_ler_sessoes_enriquecidas)
    for s in (r["data"] or []):
        if (s.get("session_id") or "").lower() == session_id.lower():
            return s
    return None


# ── Resumo por concessionária ─────────────────────────────────────────────────

def obter_concessionarias() -> list[str]:
    r_s = _cache_sessoes.get(_ler_sessoes_enriquecidas)
    sessoes: list[dict] = r_s["data"] or []
    nomes = sorted({(s.get("concessionaria") or "Nao informado") for s in sessoes})
    return nomes


# ── Varredura de arquivos ─────────────────────────────────────────────────────

def _varrer_arquivos() -> dict:
    agora = time.time()
    entrada_parados: list[dict] = []
    staging_parados: list[dict] = []
    por_conc: dict[str, dict] = {}

    def _info_pdf(path: str, nome: str, conc: str, tipo: str) -> dict | None:
        try:
            st = os.stat(path)
        except OSError:
            return None
        idade_s = int(agora - st.st_mtime)
        nivel = "critico" if idade_s >= ALERTA_CRIT else ("atencao" if idade_s >= ALERTA_ATEN else None)
        if nivel is None:
            return None
        return {
            "nome": nome, "concessionaria": conc, "caminho": path,
            "tamanho": st.st_size,
            "ultima_modificacao": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            "idade_s": idade_s,
            "ciclos_estimados": max(1, idade_s // (PERIODO_MIN * 60)),
            "tipo": tipo,
            "tem_prefixo_bb": nome.upper().startswith("BB_"),
            "nivel_alerta": nivel,
        }

    # Varrer entrada (dois níveis: CONC/ e CONC/subpasta/)
    try:
        for conc_e in os.scandir(str(PASTA_RAIZ)):
            if not conc_e.is_dir():
                continue
            conc = conc_e.name
            por_conc.setdefault(conc, {"entrada": 0, "staging": 0})
            try:
                for f in os.scandir(conc_e.path):
                    if f.name.lower().endswith(".pdf"):
                        por_conc[conc]["entrada"] += 1
                        info = _info_pdf(f.path, f.name, conc, "entrada_flat")
                        if info:
                            entrada_parados.append(info)
                    elif f.is_dir():
                        try:
                            for f2 in os.scandir(f.path):
                                if f2.name.lower().endswith(".pdf"):
                                    por_conc[conc]["entrada"] += 1
                                    info = _info_pdf(f2.path, f2.name, conc, "subpasta_interna")
                                    if info:
                                        entrada_parados.append(info)
                        except OSError:
                            pass
            except OSError:
                pass
    except OSError:
        pass

    # Varrer staging (um nível: STAGING/CONC/*.pdf)
    try:
        for conc_e in os.scandir(str(STAGING_ROOT)):
            if not conc_e.is_dir() or conc_e.name.startswith("_"):
                continue
            conc = conc_e.name
            por_conc.setdefault(conc, {"entrada": 0, "staging": 0})
            try:
                for f in os.scandir(conc_e.path):
                    if f.name.lower().endswith(".pdf"):
                        por_conc[conc]["staging"] += 1
                        info = _info_pdf(f.path, f.name, conc, "staging")
                        if info:
                            staging_parados.append(info)
            except OSError:
                pass
    except OSError:
        pass

    return {"entrada_parados": entrada_parados, "staging_parados": staging_parados,
            "por_concessionaria": por_conc,
            "total_entrada_parados": len(entrada_parados),
            "total_staging_parados": len(staging_parados)}


def obter_arquivos() -> dict:
    r = _cache_arquivos.get(_varrer_arquivos)
    dados = r["data"] or {}
    return {
        "disponivel": r["disponivel"],
        "dados_obsoletos": r["dados_obsoletos"],
        "ultima_atualizacao": r["ultima_atualizacao"],
        "erro": r["erro"],
        "entrada": dados.get("entrada_parados", []),
        "staging": dados.get("staging_parados", []),
        "por_concessionaria": dados.get("por_concessionaria", {}),
        "total_entrada": dados.get("total_entrada_parados", 0),
        "total_staging": dados.get("total_staging_parados", 0),
    }


# ── Alertas ───────────────────────────────────────────────────────────────────

def _alert_id(tipo: str, session_id: str = "", carimbo: str = "", caminho: str = "") -> str:
    return hashlib.sha256(f"{tipo}:{session_id}:{carimbo}:{caminho}".encode()).hexdigest()[:16]


def _gerar_alertas_raw(sessoes: list[dict], arquivos: dict,
                        tarefa: dict | None, atividade: dict, lock_info: dict) -> list[dict]:
    alertas: list[dict] = []
    agora = datetime.now()

    def _add(nivel: str, tipo: str, titulo: str, motivo: str, acao: str,
             session_id: str = "", carimbo: str = "", caminho: str = "", **kw):
        alertas.append({
            "alerta_id": _alert_id(tipo, session_id, carimbo, caminho),
            "nivel": nivel, "tipo": tipo, "titulo": titulo,
            "motivo": motivo, "acao_recomendada": acao,
            "session_id": session_id or None, "carimbo": carimbo or None,
            "arquivo": caminho or None,
            "horario_problema": agora.isoformat(timespec="seconds"),
            "horario_deteccao": agora.isoformat(timespec="seconds"),
            **kw,
        })

    # Watcher sem atividade
    ultimo_ts = atividade.get("ultimo_ts_log")
    if ultimo_ts:
        try:
            ts_dt = datetime.strptime(ultimo_ts, "%Y-%m-%d %H:%M:%S")
            atraso = (agora - ts_dt).total_seconds() / 60
            if atraso > PERIODO_MIN * 3:
                _add("critico", "watcher_sem_atividade", "Watcher sem atividade",
                     f"Ultima atividade no log ha {int(atraso)} min (esperado: {PERIODO_MIN} min)",
                     "Verificar tarefa agendada e log do watcher")
            elif atraso > PERIODO_MIN * 1.5:
                _add("atencao", "watcher_atrasado", "Watcher possivelmente atrasado",
                     f"Ultima atividade ha {int(atraso)} min",
                     "Aguardar proximo ciclo ou verificar tarefa agendada")
        except ValueError:
            pass

    # Tarefa desabilitada
    if tarefa:
        estado = (tarefa.get("estado") or "").lower()
        if "disabled" in estado or "desabilit" in estado:
            _add("critico", "tarefa_desabilitada", "Tarefa agendada desabilitada",
                 f"Estado: {tarefa.get('estado')}",
                 "Habilitar tarefa no Agendador de Tarefas do Windows")

        # Tarefa executou mas sem atividade no log
        ultima_exec_ag = tarefa.get("ultima_execucao_agendada")
        if ultima_exec_ag and ultimo_ts:
            try:
                t_ag = datetime.fromisoformat(ultima_exec_ag)
                t_log = datetime.strptime(ultimo_ts, "%Y-%m-%d %H:%M:%S")
                if t_ag > t_log + timedelta(minutes=2):
                    _add("atencao", "tarefa_executou_sem_log",
                         "Tarefa executada sem atividade confirmada no log",
                         f"schtasks: ultima execucao {ultima_exec_ag}, log: {ultimo_ts}",
                        "Verificar se o watcher iniciou corretamente")
            except Exception:
                pass

    # Lock possivelmente obsoleto
    if lock_info.get("status") == "possivelmente_obsoleto":
        _add("atencao", "lock_obsoleto", "Lock possivelmente obsoleto",
             f"watcher.lock (PID {lock_info.get('pid')}, {lock_info.get('idade_s')}s) sem processo ativo confirmado",
             "Verificar se o watcher esta realmente parado antes de intervir",
             caminho=str(LOCK_FILE))

    # Sessões
    for s in sessoes:
        sid = s.get("session_id", "")
        if s.get("status") == "interrompido":
            if s.get("retomavel"):
                _add("atencao", "sessao_retomavel",
                     f"Sessao retomavel: {s.get('concessionaria')} {s.get('referencia')}",
                     s.get("motivo_parada") or "Sessao interrompida com retomada possivel",
                     "Retomar sessao ou aguardar proxima execucao do watcher",
                     session_id=sid)
            else:
                _add("atencao", "sessao_interrompida",
                     f"Sessao interrompida: {s.get('concessionaria')} {s.get('referencia')}",
                     s.get("motivo_parada") or "Sessao interrompida",
                     "Investigar causa e processar manualmente se necessario",
                     session_id=sid)

        if s.get("status_reconciliacao") == "inconsistente":
            for det in (s.get("detalhes_fontes") or []):
                c = det.get("carimbo") or ""
                _add("critico", "pdf_inconsistente", f"PDF inconsistente: {c}",
                     det.get("motivo") or "Divergencia entre fontes",
                     "Verificar manualmente e corrigir no CONSEN ou no indice",
                     session_id=sid, carimbo=c)

    # Arquivos parados
    for arq in (arquivos.get("entrada_parados") or []):
        _add(arq["nivel_alerta"], "arquivo_parado_entrada",
             f"PDF parado na entrada: {arq['nome']}",
             f"{arq['concessionaria']} — {arq['idade_s']//60} min ({arq['ciclos_estimados']} ciclos estimados)",
             "Verificar se o arquivo e valido e se o watcher o detectou",
             caminho=arq["caminho"])

    for arq in (arquivos.get("staging_parados") or []):
        _add(arq["nivel_alerta"], "arquivo_parado_staging",
             f"PDF parado no staging: {arq['nome']}",
             f"{arq['concessionaria']} — {arq['idade_s']//60} min no staging",
             "Verificar sessao associada e estado da digitacao",
             caminho=arq["caminho"])

    return alertas


def obter_alertas() -> list[dict]:
    r_s = _cache_sessoes.get(_ler_sessoes_enriquecidas)
    r_a = _cache_arquivos.get(_varrer_arquivos)
    r_t = _cache_tarefa.get(_ler_tarefa_raw)

    alertas = _gerar_alertas_raw(
        sessoes=r_s["data"] or [],
        arquivos=r_a["data"] or {},
        tarefa=r_t["data"],
        atividade=_ultima_atividade_log(),
        lock_info=_analisar_lock(),
    )
    reconhecidos = _ler_reconhecidos()
    for a in alertas:
        a["reconhecido"] = a["alerta_id"] in reconhecidos
        a["id"] = a["alerta_id"]

    alertas.sort(key=lambda a: ({"critico": 0, "atencao": 1, "informativo": 2}.get(a["nivel"], 3),))
    return alertas


# ── Resumo geral ──────────────────────────────────────────────────────────────

def obter_resumo() -> dict:
    lock_info  = _analisar_lock()
    atividade  = _ultima_atividade_log()
    r_s        = _cache_sessoes.get(_ler_sessoes_enriquecidas)
    r_t        = _cache_tarefa.get(_ler_tarefa_raw)
    r_a        = _cache_arquivos.get(_varrer_arquivos)

    sessoes: list[dict] = r_s["data"] or []
    tarefa = r_t["data"]
    arq_conc: dict = (r_a["data"] or {}).get("por_concessionaria", {})

    # Estimativa de próxima execução
    prox_exec = None
    fonte_prox = "nenhuma"
    if tarefa and tarefa.get("proxima_execucao_agendada"):
        prox_exec = tarefa["proxima_execucao_agendada"]
        fonte_prox = "schtasks"
    elif atividade.get("ultimo_ts_log"):
        try:
            ts_dt = datetime.strptime(atividade["ultimo_ts_log"], "%Y-%m-%d %H:%M:%S")
            prox_exec = (ts_dt + timedelta(minutes=PERIODO_MIN)).isoformat(timespec="seconds")
            fonte_prox = "estimativa_log"
        except Exception:
            pass

    # Status do watcher (três estados separados)
    watcher_status = "desconhecido"
    if lock_info["status"] == "ativo":
        watcher_status = "em_execucao"
    elif atividade.get("ultimo_ts_log"):
        try:
            atraso = (datetime.now() - datetime.strptime(
                atividade["ultimo_ts_log"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
            watcher_status = (
                "operacional" if atraso < PERIODO_MIN * 2.5 else
                "atrasado"   if atraso < PERIODO_MIN * 6 else
                "possivelmente_parado"
            )
        except Exception:
            pass

    total_e = sum(v.get("entrada", 0) for v in arq_conc.values())
    total_s = sum(v.get("staging", 0) for v in arq_conc.values())

    alertas = obter_alertas()
    alertas_criticos = sum(1 for a in alertas if (a.get("nivel") or "").lower() == "critico")
    alertas_atencao = sum(1 for a in alertas if (a.get("nivel") or "").lower() == "atencao")
    alertas_nao_reconhecidos = sum(1 for a in alertas if not a.get("reconhecido"))

    disponivel = bool(r_s["disponivel"] and r_t["disponivel"] and r_a["disponivel"])
    dados_obsoletos = bool(r_s["dados_obsoletos"] or r_t["dados_obsoletos"] or r_a["dados_obsoletos"])
    atualizacoes = [v for v in (r_s["ultima_atualizacao"], r_t["ultima_atualizacao"], r_a["ultima_atualizacao"]) if v]
    ultima_atualizacao = max(atualizacoes) if atualizacoes else None
    erro = next((v for v in (r_s["erro"], r_t["erro"], r_a["erro"]) if v), None)

    return {
        "disponivel": disponivel,
        "dados_obsoletos": dados_obsoletos,
        "ultima_atualizacao": ultima_atualizacao,
        "erro": erro,
        "watcher": {
            "status": watcher_status,
            "ultima_atividade": atividade.get("ultimo_ts_log"),
            "ultimo_inicio": atividade.get("ultimo_inicio"),
            "ultimo_fim": atividade.get("ultimo_fim"),
            "proxima_execucao": prox_exec,
            "fonte_proxima": fonte_prox,
            "lock": lock_info.get("status"),
            "pid": lock_info.get("pid"),
            "idade_lock": lock_info.get("idade_s"),
        },
        "tarefa_agendada": {
            "disponivel": r_t["disponivel"],
            "dados_obsoletos": r_t["dados_obsoletos"],
            "erro": r_t["erro"],
            "estado": tarefa.get("estado") if tarefa else None,
            "ultima_execucao": tarefa.get("ultima_execucao_agendada") if tarefa else None,
            "proxima_execucao": tarefa.get("proxima_execucao_agendada") if tarefa else None,
            "ultimo_resultado": tarefa.get("ultimo_resultado") if tarefa else None,
        },
        "pipelines": {
            "sessoes_ativas": sum(1 for s in sessoes if s.get("status") == "em_execucao"),
            "sessoes_pendentes": sum(1 for s in sessoes if s.get("status") != "concluido"),
            "sessoes_interrompidas": sum(1 for s in sessoes if s.get("status") == "interrompido"),
            "sessoes_retomaveis": sum(1 for s in sessoes if s.get("retomavel")),
            "sessoes_retornaveis": sum(1 for s in sessoes if s.get("retomavel")),
            "sessoes_totais": len(sessoes),
            "pdfs_entrada": total_e,
            "pdfs_staging": total_s,
            "pdfs_parados_entrada": (r_a["data"] or {}).get("total_entrada_parados", 0),
            "pdfs_parados_staging": (r_a["data"] or {}).get("total_staging_parados", 0),
            "alertas_criticos": alertas_criticos,
            "alertas_atencao": alertas_atencao,
            "alertas_nao_reconhecidos": alertas_nao_reconhecidos,
        },
        "sessoes": {
            "ativas": sum(1 for s in sessoes if s.get("status") == "em_execucao"),
            "pendentes": sum(1 for s in sessoes if s.get("status") != "concluido"),
            "interrompidas": sum(1 for s in sessoes if s.get("status") == "interrompido"),
            "retomáveis": sum(1 for s in sessoes if s.get("retomavel")),
            "total": len(sessoes),
        },
        "pdfs": {
            "entrada": total_e,
            "staging": total_s,
            "entrada_parados": (r_a["data"] or {}).get("total_entrada_parados", 0),
            "staging_parados": (r_a["data"] or {}).get("total_staging_parados", 0),
        },
        "fontes": {
            "sessoes_disponivel": r_s["disponivel"],
            "tarefa_disponivel": r_t["disponivel"],
            "arquivos_disponivel": r_a["disponivel"],
        },
    }


# ── Ações de escrita ──────────────────────────────────────────────────────────

_acoes_mu = threading.Lock()


def _gravar_acao(acao: dict) -> None:
    ACOES_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _acoes_mu:
        with ACOES_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(acao, ensure_ascii=False) + "\n")


def reconhecer_alerta(alerta_id: str, observacao: str = "") -> dict:
    if not alerta_id or len(alerta_id) != 16:
        raise ValueError("alerta_id invalido")
    _gravar_acao({
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "usuario": _USUARIO, "acao": "reconhecer_alerta",
        "alerta_id": alerta_id, "session_id": None, "arquivo": None,
        "resultado": "ok", "detalhes": (observacao or "")[:500],
    })
    with _rec_mu:
        global _rec_ts
        _rec_ts = 0.0
    return {"ok": True, "alerta_id": alerta_id}


def adicionar_observacao(session_id: str | None, arquivo: str | None, texto: str) -> dict:
    if not (texto or "").strip():
        raise ValueError("Observacao nao pode ser vazia")
    _gravar_acao({
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "usuario": _USUARIO, "acao": "observacao",
        "alerta_id": None, "session_id": session_id, "arquivo": arquivo,
        "resultado": "ok", "detalhes": texto.strip()[:1000],
    })
    return {"ok": True}
