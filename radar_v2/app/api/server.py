"""Ponto de entrada Flask V2.

App factory única do Radar V2, com sessão, proteção centralizada de rotas
e serviços injetados como extensions.
"""
from __future__ import annotations

import logging
import os
from datetime import timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from dotenv import load_dotenv

from .routes import bp as api_bp
from .watcher_routes import register_watcher_routes
from ..repositories.storage import ensure_db
from ..services.run_service import RunService
from ..services.schedule_service import ScheduleService
from ..services.task_catalog_service import TaskCatalogService
from ..services.preflight_service import PreflightService
from ..services.downloader_health_service import DownloaderHealthService

TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "web" / "templates"
STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "web" / "static"
REACT_DIST = Path(__file__).resolve().parent.parent.parent / "web" / "react"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# The production launcher runs from the project root, but loading explicitly
# keeps configuration independent from the caller's current directory.
load_dotenv(PROJECT_ROOT / ".env")

PUBLIC_ENDPOINTS = {"login", "logout", "health", "api_v2.health", "api_v2.session_status", "static"}

# Centralized auth toggle. Default is disabled: this instance is reachable
# only on the internal corporate network (bound to 0.0.0.0 but no external
# port-forward/firewall rule exposes it beyond the LAN), so the historical
# permissive login screen (any non-empty username/password) added no real
# protection and is removed from the UX. Setting RADAR_V2_AUTH_ENABLED=true
# restores the session-gate behavior without any other code changes.
# Read live (not frozen at import time) so tests and process-level env
# changes both take effect without needing a module reload.
def _auth_enabled() -> bool:
    return os.environ.get("RADAR_V2_AUTH_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%H:%M:%S",
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _safe_next_path(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        return None
    if not value.startswith("/") or value.startswith("//"):
        return None
    lowered = value.lower()
    if lowered.startswith(("javascript:", "data:")):
        return None
    return value


def _login_destination() -> str:
    return _safe_next_path(request.args.get("next")) or "/"


def _is_authenticated() -> bool:
    if not _auth_enabled():
        return True
    return bool(session.get("authenticated"))


def _authenticate(username: str) -> None:
    # TODO: substituir autenticação permissiva por validação real de usuários.
    session.clear()
    session.permanent = True
    session["authenticated"] = True
    session["username"] = username.strip()


def _json_unauthorized():
    return jsonify({
        "ok": False,
        "erro": "Autenticação necessária",
        "codigo": "NAO_AUTENTICADO",
    }), 401


def create_app() -> Flask:
    ensure_db()

    app = Flask(
        __name__,
        template_folder=str(TEMPLATE_DIR),
        static_folder=str(STATIC_DIR),
    )
    app.config["JSON_ENSURE_ASCII"] = False
    app.config["SECRET_KEY"] = os.environ.get("RADAR_V2_SECRET_KEY") or os.environ.get("SECRET_KEY") or "radar-v2-dev-secret"
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = _env_bool("RADAR_V2_SESSION_COOKIE_SECURE", False)
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=int(os.environ.get("RADAR_V2_SESSION_HOURS", "8")))
    app.config["SESSION_REFRESH_EACH_REQUEST"] = True

    if app.config["SECRET_KEY"] == "radar-v2-dev-secret":
        logging.getLogger(__name__).warning(
            "Radar V2 está usando SECRET_KEY de desenvolvimento; defina RADAR_V2_SECRET_KEY em produção."
        )

    catalog = TaskCatalogService()
    preflight = PreflightService(catalog)
    downloader_health = DownloaderHealthService(catalog, preflight)
    global_preflight = preflight.global_report()
    preflight_log = logging.getLogger("radar_v2.preflight")
    preflight_log.info("ENV_FILE_LOADED: %s", global_preflight["env_file_loaded"])
    preflight_log.info(
        "ENV_KEYS_AVAILABLE: %s/%s",
        global_preflight["env_keys_available"], global_preflight["env_keys_required"],
    )
    preflight_log.info("MISSING_REQUIRED_KEYS: %s", global_preflight["missing_required_keys"])
    preflight_log.info("GLOBAL_PREFLIGHT: %s", global_preflight["status"])

    run_svc = RunService(preflight)
    sched_svc = ScheduleService(run_svc, catalog)
    if _env_bool("RADAR_V2_SCHEDULER_ENABLED", True) and global_preflight["status"] == "READY":
        sched_svc.start()
    elif global_preflight["status"] != "READY":
        logging.getLogger(__name__).error(
            "Radar V2 scheduler bloqueado pelo preflight: %s",
            [issue["requirement"] for issue in global_preflight["issues"]],
        )
    else:
        logging.getLogger(__name__).info("Radar V2 scheduler desativado por ambiente.")

    # Email sync is intentionally independent of the downloader preflight: it
    # never touches IMAP/CONSEN/a browser, only a local read-only manifest
    # file, so it should not be blocked by unrelated downloader credentials
    # being unavailable.
    from ..services.email_sync_scheduler import EmailSyncScheduler
    email_sync = EmailSyncScheduler()
    if _env_bool("RADAR_EMAIL_SYNC_ENABLED", True):
        email_sync.start()
    else:
        logging.getLogger(__name__).info("Sincronizacao de e-mail desativada por ambiente.")

    app.extensions["run_service"] = run_svc
    app.extensions["task_catalog"] = catalog
    app.extensions["schedule_service"] = sched_svc
    app.extensions["preflight_service"] = preflight
    app.extensions["email_sync_scheduler"] = email_sync
    app.extensions["downloader_health_service"] = downloader_health
    app.extensions["react_dist"] = REACT_DIST

    app.register_blueprint(api_bp)
    register_watcher_routes(app)

    @app.before_request
    def enforce_authentication():
        if not _auth_enabled():
            return None

        endpoint = request.endpoint or ""

        if endpoint in PUBLIC_ENDPOINTS:
            return None
        # Vite emits production bundles at /assets. They must load before a
        # session exists, otherwise Flask redirects module requests to /login
        # and Chrome rejects the returned HTML for its JavaScript MIME type.
        if request.path.startswith(("/static/", "/assets/")):
            return None
        if request.path == "/favicon.ico":
            return None

        if _is_authenticated():
            return None

        if request.path.startswith("/api/"):
            return _json_unauthorized()

        next_path = request.full_path.rstrip("?") if request.query_string else request.path
        next_path = _safe_next_path(next_path) or request.path
        return redirect(url_for("login", next=next_path))

    @app.after_request
    def set_security_headers(response):
        if response.mimetype == "text/html":
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    def react_index():
        if not (REACT_DIST / "index.html").is_file():
            return None
        return send_from_directory(REACT_DIST, "index.html")

    @app.get("/")
    def home():
        return react_index() or redirect(url_for("executions"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not _auth_enabled():
            return redirect("/")

        if _is_authenticated() and request.method == "GET":
            return redirect(_login_destination())

        error = None
        next_path = _safe_next_path(request.values.get("next")) or url_for("executions")

        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = (request.form.get("password") or "").strip()
            if not username:
                error = "Informe um usuário."
            elif not password:
                error = "Informe uma senha."
            else:
                _authenticate(username)
                return redirect(next_path)

        if request.method == "GET":
            return react_index() or render_template("login.html", error=error, next_path=next_path)
        return render_template("login.html", error=error, next_path=next_path)

    @app.route("/logout", methods=["GET", "POST"])
    def logout():
        session.clear()
        if not _auth_enabled():
            return redirect("/")
        return redirect(url_for("login"))

    @app.get("/executions")
    def executions():
        return render_template("executions.html")

    @app.get("/schedules")
    def schedules():
        return render_template("schedules.html")

    @app.get("/painel")
    def painel():
        return render_template("painel.html")

    @app.get("/historico")
    def historico():
        return render_template("historico.html")

    @app.get("/<path:frontend_path>")
    def react_frontend(frontend_path: str):
        """Serve the compiled React UI while preserving legacy Flask routes."""
        candidate = REACT_DIST / frontend_path
        if candidate.is_file():
            return send_from_directory(REACT_DIST, frontend_path)
        return react_index() or ("Not found", 404)

    return app


def run() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--threads", type=int, default=8)
    args, _ = parser.parse_known_args()

    app = create_app()
    try:
        from waitress import serve

        logging.getLogger("waitress").setLevel(logging.WARNING)
        print(f"[Radar V2] http://{args.host}:{args.port}")
        serve(app, host=args.host, port=args.port, threads=args.threads)
    except ModuleNotFoundError:
        print(f"[Radar V2] http://localhost:{args.port}  (Flask dev)")
        app.run(host=args.host, port=args.port, debug=False, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
