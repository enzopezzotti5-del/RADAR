"""
Leitor IMAP para captura de OTP da Energisa.

Credenciais via variáveis de ambiente:
    ENERGISA_IMAP_HOST  — imap.acaoengenharia.com.br
    ENERGISA_IMAP_PORT  — 993 (IMAPS)
    ENERGISA_IMAP_USER  — robo.fatura@acaoengenharia.com.br
    ENERGISA_IMAP_PASS  — senha da caixa

Uso:
    from core.downloaders.energisa.email_reader import aguardar_otp, deletar_emails_energisa
    codigo = aguardar_otp(timeout=120)
"""

from __future__ import annotations

import imaplib
import email
import os
import re
import smtplib
import time
from email.header import decode_header
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    for _env_path in (
        Path(__file__).resolve().parents[3] / ".env",
        Path(__file__).resolve().parents[3] / ".env.local",
    ):
        if _env_path.exists():
            load_dotenv(_env_path)

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

IMAP_HOST = os.getenv("ENERGISA_IMAP_HOST", "imap.acaoengenharia.com.br")
IMAP_PORT = int(os.getenv("ENERGISA_IMAP_PORT", "993"))
IMAP_USER = os.getenv("ENERGISA_IMAP_USER", "robo.fatura@acaoengenharia.com.br")
IMAP_PASS = os.getenv("ENERGISA_IMAP_PASS", "")
SMTP_HOST = os.getenv("ENERGISA_SMTP_HOST", "smtp.acaoengenharia.com.br")
SMTP_PORT = int(os.getenv("ENERGISA_SMTP_PORT", "465"))

# Filtros para identificar email de OTP da Energisa
REMETENTES_ENERGISA = [
    "noreply@energisa.com.br",
    "atendimento@energisa.com.br",
    "sac.energisa.com.br",
    "energisa@",
]
ASSUNTOS_OTP = ["código", "codigo", "token", "acesso", "verificação", "verificacao"]

# Regex para extrair código de 4 dígitos — cada dígito pode vir isolado
# (ex.: "9 6 1 6"), pois o e-mail da Energisa renderiza um <span> por dígito.
_OTP_RE = re.compile(r"\b(\d)\s*(\d)\s*(\d)\s*(\d)\b")


# ---------------------------------------------------------------------------
# Conexão
# ---------------------------------------------------------------------------

def _conectar() -> imaplib.IMAP4_SSL:
    if not IMAP_PASS:
        raise RuntimeError(
            "ENERGISA_IMAP_PASS não definido. Adicione ao .env e recarregue."
        )
    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    conn.login(IMAP_USER, IMAP_PASS)
    return conn


# ---------------------------------------------------------------------------
# Diagnostico
# ---------------------------------------------------------------------------

def testar_imap() -> dict:
    """Valida conexao e autenticacao IMAP."""
    resultado = {
        "ok": False,
        "host": IMAP_HOST,
        "port": IMAP_PORT,
        "user": IMAP_USER,
    }
    conn = None
    try:
        conn = _conectar()
        status, _ = conn.select("INBOX")
        resultado.update({
            "ok": status == "OK",
            "select_status": status,
        })
    except Exception as exc:
        resultado["error"] = str(exc)
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:
                pass
    return resultado


def testar_smtp() -> dict:
    """Valida conexao e autenticacao SMTP SSL."""
    resultado = {
        "ok": False,
        "host": SMTP_HOST,
        "port": SMTP_PORT,
        "user": IMAP_USER,
    }
    conn = None
    try:
        if not IMAP_PASS:
            raise RuntimeError(
                "ENERGISA_IMAP_PASS nao definido. Adicione ao .env e recarregue."
            )
        conn = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20)
        conn.login(IMAP_USER, IMAP_PASS)
        resultado["ok"] = True
    except Exception as exc:
        resultado["error"] = str(exc)
    finally:
        if conn is not None:
            try:
                conn.quit()
            except Exception:
                pass
    return resultado


# ---------------------------------------------------------------------------
# Parsing de email
# ---------------------------------------------------------------------------

def _decode_str(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _subject(msg: email.message.Message) -> str:
    parts = decode_header(msg.get("Subject", ""))
    resultado = []
    for chunk, charset in parts:
        if isinstance(chunk, bytes):
            resultado.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            resultado.append(chunk)
    return " ".join(resultado)


def _from(msg: email.message.Message) -> str:
    return _decode_str(msg.get("From", ""))


def _body_texto(msg: email.message.Message) -> str:
    """Extrai texto plano ou HTML do email."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/html":
                payload = part.get_payload(decode=True)
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    return ""


def _is_energisa(msg: email.message.Message) -> bool:
    remetente = _from(msg).lower()
    return any(r in remetente for r in REMETENTES_ENERGISA)


def _extrair_otp(corpo: str) -> str | None:
    """Extrai o código de 4 dígitos do corpo do email."""
    # Remove HTML tags se necessário
    corpo_limpo = re.sub(r"<[^>]+>", " ", corpo)
    match = _OTP_RE.search(corpo_limpo)
    if match:
        return "".join(match.groups())
    return None


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def aguardar_otp(timeout: int = 180, poll: int = 5, uid_minimo: str | None = None) -> str:
    """
    Fica em polling na caixa IMAP até receber um email de OTP da Energisa.

    Busca ALL (não só UNSEEN) para não perder emails auto-marcados como lidos.

    Args:
        timeout:    segundos máximos de espera (padrão 180)
        poll:       intervalo entre verificações em segundos (padrão 5)
        uid_minimo: UID IMAP mínimo para ignorar emails antigos (obtido via uid_atual())

    Returns:
        Código OTP (string de 4 dígitos)

    Raises:
        TimeoutError: se nenhum código chegar dentro do timeout
    """
    uid_min_int = int(uid_minimo) if uid_minimo else 0
    prazo = time.time() + timeout
    while time.time() < prazo:
        try:
            conn = _conectar()
            conn.select("INBOX")

            # Busca ALL (não só UNSEEN) — evita perder emails auto-marcados como lidos
            _, data = conn.search(None, "ALL")
            uids = data[0].split() if data[0] else []

            for uid in reversed(uids):  # mais recentes primeiro
                uid_int = int(uid)
                if uid_int <= uid_min_int:
                    continue

                _, msg_data = conn.fetch(uid, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                if not isinstance(raw, bytes):
                    continue
                msg = email.message_from_bytes(raw)

                if not _is_energisa(msg):
                    continue

                corpo = _body_texto(msg)
                codigo = _extrair_otp(corpo)
                if codigo:
                    print(f"[email_reader] OTP encontrado no email UID={uid_int} assunto={_subject(msg)!r}")
                    conn.store(uid, "+FLAGS", "\\Deleted")
                    conn.expunge()
                    conn.logout()
                    return codigo

            conn.logout()
        except Exception as exc:
            print(f"[email_reader] Erro IMAP: {exc}")

        decorrido = timeout - int(prazo - time.time())
        print(f"[email_reader] Aguardando OTP... {decorrido}s / {timeout}s")
        time.sleep(poll)

    raise TimeoutError(f"OTP da Energisa não recebido em {timeout}s")


def uid_atual() -> str:
    """
    Retorna o maior UID atual na INBOX — usar antes de disparar o OTP
    para ignorar emails anteriores.
    """
    try:
        conn = _conectar()
        conn.select("INBOX")
        _, data = conn.search(None, "ALL")
        uids = data[0].split() if data[0] else []
        conn.logout()
        return uids[-1].decode() if uids else "0"
    except Exception:
        return "0"


def deletar_emails_energisa() -> int:
    """
    Deleta todos os emails de remetentes Energisa da INBOX.
    Usar na limpeza diária.

    Returns:
        Quantidade de emails deletados.
    """
    deletados = 0
    try:
        conn = _conectar()
        conn.select("INBOX")
        _, data = conn.search(None, "ALL")
        uids = data[0].split() if data[0] else []
        for uid in uids:
            _, msg_data = conn.fetch(uid, "(RFC822)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            if _is_energisa(msg):
                conn.store(uid, "+FLAGS", "\\Deleted")
                deletados += 1
        conn.expunge()
        conn.logout()
    except Exception as exc:
        print(f"[email_reader] Erro na limpeza: {exc}")
    return deletados


def deletar_todos_emails() -> int:
    """
    Deleta TODOS os emails da INBOX (limpeza diária total).

    Returns:
        Quantidade de emails deletados.
    """
    deletados = 0
    try:
        conn = _conectar()
        conn.select("INBOX")
        _, data = conn.search(None, "ALL")
        uids = data[0].split() if data[0] else []
        for uid in uids:
            conn.store(uid, "+FLAGS", "\\Deleted")
            deletados += 1
        conn.expunge()
        conn.logout()
    except Exception as exc:
        print(f"[email_reader] Erro na limpeza total: {exc}")
    return deletados


if __name__ == "__main__":
    print("[email_reader] Diagnostico IMAP:", testar_imap())
    print("[email_reader] Diagnostico SMTP:", testar_smtp())
