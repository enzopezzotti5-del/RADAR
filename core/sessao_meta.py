#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sessao_meta.py — rastreamento de progresso por sessão de processamento.

Cada sessão tem um arquivo JSON próprio em:
    staging_root / "_sessoes" / "{session_id}.json"

O _sessao_meta.json na staging_root é mantido para compatibilidade com a
lógica de retomada do watcher e passa a incluir o campo "session_id".

Não altera carimbo, OCR, digitação, filtros, destinos ou regras de retomada.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Internos
# ---------------------------------------------------------------------------

def _agora() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _novo_session_id(conc: str, mes: str, ano: str) -> str:
    ts_hex = f"{int(time.time()):08x}"
    # Sub-segundo para unicidade quando multiplas sessoes sao criadas no mesmo segundo
    sub = f"{(int(time.monotonic() * 1000) % 0x10000):04x}"
    return f"{conc}_{mes}{ano}_{ts_hex}{sub}"


def _sessao_path(staging_root: Path, session_id: str) -> Path:
    return staging_root / "_sessoes" / f"{session_id}.json"


def _salvar_atomico(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def ler_session_id(staging_root: Path) -> str | None:
    """Lê o session_id do _sessao_meta.json existente (compatibilidade retomada)."""
    meta = staging_root / "_sessao_meta.json"
    if not meta.exists():
        return None
    try:
        return json.loads(meta.read_text(encoding="utf-8")).get("session_id")
    except Exception:
        return None


def criar_sessao(
    staging_root: Path,
    conc: str,
    grupo: str,
    mes: str,
    ano: str,
    arquivos: list[dict[str, Any]],
    *,
    reutilizar_se_existe: bool = True,
) -> str:
    """
    Cria (ou reutiliza) sessão de progresso. Retorna o session_id.

    Se _sessao_meta.json já tem session_id (retomada), reutiliza o mesmo ID
    e preserva etapas já concluídas e arquivos com status final.
    """
    session_id: str | None = None
    if reutilizar_se_existe:
        session_id = ler_session_id(staging_root)
    if not session_id:
        session_id = _novo_session_id(conc, mes, ano)

    agora = _agora()
    n = len(arquivos)

    data: dict[str, Any] = {
        "session_id": session_id,
        "concessionaria": conc,
        "grupo": grupo,
        "referencia": f"{mes}/{ano}",
        "status": "iniciado",
        "etapa_atual": "carimbo",
        "criado_em": agora,
        "atualizado_em": agora,
        "progresso": {
            "deteccao":       {"status": "ok", "quantidade": n},
            "carimbo":        {"status": "pendente", "quantidade": 0},
            "ocr":            {"status": "pendente"},
            "validacao_lote": {"status": "pendente"},
            "digitacao":      {"status": "pendente", "digitados": 0, "pulados": 0, "erros": 0},
            "filtro":         {"status": "pendente"},
        },
        "arquivos": arquivos,
        "retomavel": False,
        "motivo_parada": None,
    }

    # Ao retomar: preserva criado_em e etapas/arquivos já concluídos
    sfile = _sessao_path(staging_root, session_id)
    if sfile.exists():
        try:
            old = json.loads(sfile.read_text(encoding="utf-8"))
            data["criado_em"] = old.get("criado_em", agora)
            for etapa, val in old.get("progresso", {}).items():
                if val.get("status") in ("ok", "concluido") and etapa in data["progresso"]:
                    data["progresso"][etapa] = val
            old_arqs = {
                a.get("carimbo"): a
                for a in old.get("arquivos", [])
                if a.get("carimbo")
            }
            for arq in data["arquivos"]:
                old_arq = old_arqs.get(arq.get("carimbo"))
                if old_arq and old_arq.get("status") in ("digitado", "pulado", "filtrado", "investigar"):
                    arq.update(old_arq)
        except Exception:
            pass

    _salvar_atomico(sfile, data)
    return session_id


def atualizar_etapa(
    staging_root: Path,
    session_id: str | None,
    etapa: str,
    status: str,
    **campos: Any,
) -> None:
    """Atualiza o progresso de uma etapa específica de forma atômica."""
    if not session_id:
        return
    sfile = _sessao_path(staging_root, session_id)
    if not sfile.exists():
        return
    try:
        data = json.loads(sfile.read_text(encoding="utf-8"))
    except Exception:
        return

    data["atualizado_em"] = _agora()
    data["etapa_atual"] = etapa

    # Status global derivado do estado da etapa
    if status == "erro":
        data["status"] = "interrompido"
    elif status == "em_execucao":
        data["status"] = "em_execucao"
    elif status == "ok" and etapa == "filtro":
        data["status"] = "concluido"

    etapa_data = dict(data.get("progresso", {}).get(etapa, {}))
    etapa_data["status"] = status
    etapa_data.update(campos)
    data.setdefault("progresso", {})[etapa] = etapa_data

    _salvar_atomico(sfile, data)


def atualizar_status(
    staging_root: Path,
    session_id: str | None,
    status: str,
    *,
    retomavel: bool = False,
    motivo: str | None = None,
    etapa_atual: str | None = None,
) -> None:
    """Atualiza o status global da sessão."""
    if not session_id:
        return
    sfile = _sessao_path(staging_root, session_id)
    if not sfile.exists():
        return
    try:
        data = json.loads(sfile.read_text(encoding="utf-8"))
    except Exception:
        return

    data["atualizado_em"] = _agora()
    data["status"] = status
    data["retomavel"] = retomavel
    data["motivo_parada"] = motivo
    if etapa_atual:
        data["etapa_atual"] = etapa_atual

    _salvar_atomico(sfile, data)


def atualizar_arquivo(
    staging_root: Path,
    session_id: str | None,
    *,
    carimbo: str | None = None,
    nome_carimbado: str | None = None,
    status: str,
    ultima_etapa: str,
    destino: str | None = None,
    erro: str | None = None,
) -> None:
    """Atualiza status de um arquivo individual dentro da sessão."""
    if not session_id:
        return
    sfile = _sessao_path(staging_root, session_id)
    if not sfile.exists():
        return
    try:
        data = json.loads(sfile.read_text(encoding="utf-8"))
    except Exception:
        return

    data["atualizado_em"] = _agora()
    updated = False
    for arq in data.get("arquivos", []):
        match = (
            (carimbo and arq.get("carimbo") == carimbo)
            or (nome_carimbado and arq.get("nome_carimbado") == nome_carimbado)
        )
        if match:
            arq["status"] = status
            arq["ultima_etapa"] = ultima_etapa
            if destino is not None:
                arq["destino"] = destino
            if erro is not None:
                arq["erro"] = erro
            updated = True
            break

    if updated:
        _salvar_atomico(sfile, data)


def registrar_carimbo_arquivo(
    staging_root: Path,
    session_id: str | None,
    nome_original: str,
    carimbo: str,
    nome_carimbado: str,
) -> None:
    """
    Preenche carimbo de um arquivo que ainda não tinha (eq_go: PIPELINE_FAZ_CARIMBO).
    Chamado pela etapa interna de carimbo do pipeline específico.
    """
    if not session_id:
        return
    sfile = _sessao_path(staging_root, session_id)
    if not sfile.exists():
        return
    try:
        data = json.loads(sfile.read_text(encoding="utf-8"))
    except Exception:
        return

    data["atualizado_em"] = _agora()
    for arq in data.get("arquivos", []):
        if arq.get("nome_original") == nome_original and not arq.get("carimbo"):
            arq["carimbo"] = carimbo
            arq["nome_carimbado"] = nome_carimbado
            arq["status"] = "carimbo_ok"
            arq["ultima_etapa"] = "carimbo"
            break

    _salvar_atomico(sfile, data)


def carregar(staging_root: Path, session_id: str) -> dict | None:
    """Carrega e retorna o JSON de uma sessão. None se não existir."""
    sfile = _sessao_path(staging_root, session_id)
    if not sfile.exists():
        return None
    try:
        return json.loads(sfile.read_text(encoding="utf-8"))
    except Exception:
        return None


def listar_sessoes(raiz: Path) -> list[dict]:
    """
    Retorna todas as sessões encontradas em raiz/**/_sessoes/*.json,
    ordenadas da mais recente para a mais antiga.
    """
    sessoes: list[dict] = []
    for sfile in raiz.rglob("_sessoes/*.json"):
        try:
            d = json.loads(sfile.read_text(encoding="utf-8"))
            d["_path"] = str(sfile)
            sessoes.append(d)
        except Exception:
            pass
    sessoes.sort(key=lambda s: s.get("criado_em", ""), reverse=True)
    return sessoes


# ---------------------------------------------------------------------------
# Context manager de alto nível — para pipelines que não têm staging próprio
# ---------------------------------------------------------------------------

class PipelineSessao:
    """
    Context manager que cria, mantém e fecha uma sessão de pipeline de forma
    automática, garantindo persistência mesmo em caso de exceção.

    Uso mínimo:
        with PipelineSessao(staging_root, conc="CELESC", grupo="BT",
                            mes="04", ano="2026") as sess:
            sess.etapa("ocr", "em_execucao")
            # ... lógica ...
            sess.etapa("ocr", "ok", quantidade=n)
            sess.adicionar_arquivos([{"carimbo": c, ...} for c in carimbos])
            sess.etapa("digitacao", "ok")
            sess.etapa("filtro", "ok")

    O bloco finally garante que o status correto (concluido / interrompido /
    erro) é gravado mesmo se uma exceção for lançada.
    """

    def __init__(
        self,
        staging_root: Path,
        *,
        conc: str,
        grupo: str,
        mes: str,
        ano: str,
        arquivos: list[dict[str, Any]] | None = None,
        reutilizar_se_existe: bool = True,
    ) -> None:
        self._root = staging_root
        self._conc = conc
        self._grupo = grupo
        self._mes = mes
        self._ano = ano
        self._arquivos: list[dict[str, Any]] = arquivos or []
        self._reutilizar = reutilizar_se_existe
        self.session_id: str | None = None
        self._encerrado = False

    # ── interface pública ──────────────────────────────────────────────────

    def etapa(self, etapa: str, status: str, **campos: Any) -> None:
        """Atualiza o progresso de uma etapa."""
        if self.session_id:
            atualizar_etapa(self._root, self.session_id, etapa, status, **campos)

    def status(self, status: str, *, retomavel: bool = False,
               motivo: str | None = None, etapa_atual: str | None = None) -> None:
        """Atualiza o status global da sessão."""
        if self.session_id:
            atualizar_status(self._root, self.session_id,
                             status, retomavel=retomavel, motivo=motivo,
                             etapa_atual=etapa_atual)

    def arquivo(self, *, carimbo: str | None = None, nome_carimbado: str | None = None,
                status_arq: str, ultima_etapa: str, destino: str | None = None,
                erro: str | None = None) -> None:
        """Atualiza status de um arquivo individual."""
        if self.session_id:
            atualizar_arquivo(
                self._root, self.session_id,
                carimbo=carimbo, nome_carimbado=nome_carimbado,
                status=status_arq, ultima_etapa=ultima_etapa,
                destino=destino, erro=erro,
            )

    def adicionar_arquivos(self, arquivos: list[dict[str, Any]]) -> None:
        """
        Adiciona arquivos à lista da sessão e persiste.
        Útil quando a lista de arquivos só é conhecida após o início da sessão.
        """
        if not self.session_id:
            return
        sfile = _sessao_path(self._root, self.session_id)
        if not sfile.exists():
            return
        try:
            data = json.loads(sfile.read_text(encoding="utf-8"))
        except Exception:
            return
        existentes = {a.get("carimbo") for a in data.get("arquivos", []) if a.get("carimbo")}
        for arq in arquivos:
            if arq.get("carimbo") not in existentes:
                data["arquivos"].append(arq)
        data["atualizado_em"] = _agora()
        _salvar_atomico(sfile, data)

    # ── context manager ───────────────────────────────────────────────────

    def __enter__(self) -> "PipelineSessao":
        try:
            self.session_id = criar_sessao(
                self._root,
                self._conc,
                self._grupo,
                self._mes,
                self._ano,
                self._arquivos,
                reutilizar_se_existe=self._reutilizar,
            )
        except Exception:
            self.session_id = None
        return self

    def __exit__(
        self,
        exc_type: type | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> bool:
        if self._encerrado or not self.session_id:
            return False
        self._encerrado = True
        try:
            if exc_type is None:
                atualizar_status(self._root, self.session_id, "concluido",
                                 retomavel=False, motivo=None)
            elif exc_type is KeyboardInterrupt:
                atualizar_status(self._root, self.session_id, "interrompido",
                                 retomavel=True, motivo="Interrompido pelo usuário (KeyboardInterrupt)")
            else:
                motivo = f"{exc_type.__name__}: {exc_val}" if exc_val else str(exc_type)
                atualizar_status(self._root, self.session_id, "erro",
                                 retomavel=False, motivo=motivo[:500])
        except Exception:
            pass
        return False  # não suprime a exceção
