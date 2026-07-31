"""Watcher routes for Radar V2.

This module registers the legacy watcher page and API endpoints under the active Radar V2
application.
"""
from __future__ import annotations

from flask import jsonify, render_template, request

from radar_v2.watcher_service import (
    obter_resumo,
    obter_tarefa_agendada,
    obter_sessoes,
    obter_sessao_detalhe,
    obter_concessionarias,
    obter_arquivos,
    obter_alertas,
    obter_logs,
    reconhecer_alerta,
    adicionar_observacao,
)


def register_watcher_routes(app) -> None:

    @app.get("/api/watcher/resumo")
    def api_watcher_resumo():
        return jsonify(obter_resumo())

    @app.get("/api/watcher/tarefa")
    def api_watcher_tarefa():
        return jsonify(obter_tarefa_agendada())

    @app.get("/api/watcher/sessoes")
    def api_watcher_sessoes():
        return jsonify(obter_sessoes(
            status=request.args.get("status"),
            status_reconciliacao=request.args.get("status_reconciliacao"),
            concessionaria=request.args.get("concessionaria"),
            grupo=request.args.get("grupo"),
            referencia=request.args.get("referencia"),
            session_id=request.args.get("session_id"),
            q=request.args.get("q") or request.args.get("busca"),
        ))

    @app.get("/api/watcher/sessoes/<path:session_id>")
    def api_watcher_sessao_detalhe(session_id: str):
        if not session_id or not session_id.strip():
            return jsonify({"erro": "session_id invalido", "session_id": session_id}), 400
        s = obter_sessao_detalhe(session_id)
        if s is None:
            return jsonify({"erro": "sessao nao encontrada", "session_id": session_id}), 404
        return jsonify(s)

    @app.get("/api/watcher/concessionarias")
    def api_watcher_concessionarias():
        return jsonify(obter_concessionarias())

    @app.get("/api/watcher/arquivos")
    def api_watcher_arquivos():
        return jsonify(obter_arquivos())

    @app.get("/watcher")
    def watcher_page():
        return render_template("watcher.html")

    @app.get("/api/watcher/alertas")
    def api_watcher_alertas():
        return jsonify(obter_alertas())

    @app.get("/api/watcher/logs")
    def api_watcher_logs():
        try:
            limit = int(request.args.get("limit") or "100")
        except ValueError:
            limit = 100
        try:
            offset = int(request.args.get("offset") or "0")
        except ValueError:
            return jsonify({"erro": "offset invalido"}), 400
        if offset < 0:
            return jsonify({"erro": "offset invalido"}), 400
        return jsonify(obter_logs(
            limit=limit,
            offset=offset,
            session_id=request.args.get("session_id"),
            carimbo=request.args.get("carimbo"),
            concessionaria=request.args.get("concessionaria"),
            nivel=request.args.get("nivel"),
        ))

    @app.post("/api/watcher/alertas/<alerta_id>/reconhecer")
    def api_watcher_reconhecer_alerta(alerta_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(reconhecer_alerta(alerta_id, payload.get("observacao", "")))
        except ValueError as exc:
            return jsonify({"ok": False, "erro": str(exc)}), 400

    @app.post("/api/watcher/observacoes")
    def api_watcher_observacao():
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(adicionar_observacao(
                session_id=payload.get("session_id"),
                arquivo=payload.get("arquivo"),
                texto=payload.get("texto") or "",
            ))
        except ValueError as exc:
            return jsonify({"ok": False, "erro": str(exc)}), 400
