# -*- coding: utf-8 -*-
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

# =============================================================================
# CONFIGURAÇÕES DO LAUNCHER CLI
# =============================================================================

BASE_DIR = Path(__file__).parent
CORE_DIR = BASE_DIR / "core"

PYTHON_EXE = str(BASE_DIR / ".venv" / "Scripts" / "python.exe")

SCRIPTS = [
    str(CORE_DIR / "downloaders" / "cemig"         / "cemig.py"),
    str(CORE_DIR / "downloaders" / "enel_sp"        / "enel_sp.py"),
    str(CORE_DIR / "downloaders" / "enel_ce"        / "enel_ce.py"),
    str(CORE_DIR / "downloaders" / "enel_rj"        / "enel_rj.py"),
    str(CORE_DIR / "downloaders" / "equatorial_go"  / "equatorial_goias.py"),
]

NEOENERGIA_ATIVAR = True
NEOENERGIA_DIR    = str(CORE_DIR / "downloaders" / "neoenergia")
NEOENERGIA_CSV    = str(Path(NEOENERGIA_DIR) / "cnpjs_neoenergia.csv")

EMAIL_SMTP_HOST = os.environ.get("EMAIL_SMTP_HOST", "smtp.gmail.com")
EMAIL_SMTP_PORT = int(os.environ.get("EMAIL_SMTP_PORT", 465))
EMAIL_SMTP_TLS  = False

EMAIL_REMETENTE     = os.environ.get("EMAIL_REMETENTE", "")
EMAIL_SENHA         = os.environ.get("EMAIL_SENHA", "")
EMAIL_DESTINATARIOS = [
    e.strip()
    for e in os.environ.get("EMAIL_DESTINATARIOS", "").split(",")
    if e.strip()
]

LOG_DIR           = str(BASE_DIR / "logs")
TIMEOUT_SEGUNDOS  = 0

os.makedirs(LOG_DIR, exist_ok=True)

# =============================================================================
# PIPELINE CEMIG
# =============================================================================

PIPELINE_CEMIG_ATIVAR = True
PIPELINE_CEMIG_SCRIPT = str(CORE_DIR / "pipelines" / "pipeline_cemig.py")

# =============================================================================
# PIPELINE ENEL
# =============================================================================

PIPELINE_ENEL_ATIVAR = True
PIPELINE_ENEL_SCRIPT = str(CORE_DIR / "pipelines" / "pipeline_enel.py")
PIPELINE_ENEL_MES    = ""    # vazio = mês atual automático
PIPELINE_ENEL_ANO    = ""    # vazio = ano atual automático
PIPELINE_ENEL_TIPO   = "bt"  # "bt" | "mt" | "ambos"

# =============================================================================
# PIPELINE NEOENERGIA ELEKTRO
# =============================================================================

PIPELINE_NEOENERGIA_ELEKTRO_ATIVAR = False
PIPELINE_NEOENERGIA_ELEKTRO_SCRIPT = str(CORE_DIR / "pipelines" / "pipeline_neoenergia_elektro.py")

# =============================================================================
# PIPELINE NEOENERGIA BAHIA
# =============================================================================

PIPELINE_NEOENERGIA_BAHIA_ATIVAR = False
PIPELINE_NEOENERGIA_BAHIA_SCRIPT = str(CORE_DIR / "pipelines" / "pipeline_neoenergia_bahia.py")

# =============================================================================
# PIPELINE NEOENERGIA PERNAMBUCO
# =============================================================================

PIPELINE_NEOENERGIA_PERNAMBUCO_ATIVAR = True
PIPELINE_NEOENERGIA_PERNAMBUCO_SCRIPT = str(CORE_DIR / "pipelines" / "pipeline_neoenergia_pernambuco.py")
