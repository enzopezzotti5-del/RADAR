from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
RUNTIME_DIR = PROJECT_ROOT / "runtime"
VAR_DIR = PROJECT_ROOT / "var"
LOGS_DIR = PROJECT_ROOT / "logs"

ARQUIVOS_ENZO_DIR = Path("//10.10.250.21/Energia/ARQUIVOS ENZO")
INDICE_MASTER_REDE = ARQUIVOS_ENZO_DIR / "indice_master.csv"
INDICE_MASTER_NEXT_REDE = ARQUIVOS_ENZO_DIR / "indice_master_next.txt"

RUNTIME_INDICE_DIR = RUNTIME_DIR / "indice"
LOCAL_INDICE_MASTER = RUNTIME_INDICE_DIR / "indice_master.csv"
LOCAL_INDICE_MASTER_LOCK = RUNTIME_INDICE_DIR / "indice_master.csv.lock"
LOCAL_INDICE_NEXT = RUNTIME_INDICE_DIR / "indice_master_next.txt"

VENCIMENTO_DIR = VAR_DIR / "vencimento"
VENCIMENTO_INPUT_DIR = VENCIMENTO_DIR / "input"
VENCIMENTO_AUDITORIA_DIR = VENCIMENTO_DIR / "auditoria"
VENCIMENTO_RESULTADOS_DIR = VENCIMENTO_DIR / "resultados"
VENCIMENTO_LOGS_DIR = VENCIMENTO_DIR / "logs"

SECRETS_DIR = VAR_DIR / "secrets"
COPPEL_SECRETS_XLS = SECRETS_DIR / "accessos_copel.xlsx"
LEGACY_ROOT_COPPEL_XLS = PROJECT_ROOT / "acessos_copel.xlsx"
LEGACY_ROOT_SECRET_KEY = PROJECT_ROOT / ".secret_key"
SECRET_KEY_PATH = SECRETS_DIR / ".secret_key"

NEOENERGIA_BRASILIA_DIR = VAR_DIR / "neoenergia_brasilia" / "resultados"
REFERENCES_DIR = VAR_DIR / "references"
TASK_SCHEDULER_DIR = VAR_DIR / "task_scheduler"
INDICE_SNAPSHOTS_DIR = VAR_DIR / "snapshots" / "indice_master"

WINDOWS_LAUNCHERS_DIR = SCRIPTS_DIR / "infra" / "windows"


def ensure_local_dirs() -> None:
    for path in (
        RUNTIME_INDICE_DIR,
        VENCIMENTO_INPUT_DIR,
        VENCIMENTO_AUDITORIA_DIR,
        VENCIMENTO_RESULTADOS_DIR,
        VENCIMENTO_LOGS_DIR,
        SECRETS_DIR,
        NEOENERGIA_BRASILIA_DIR,
        REFERENCES_DIR,
        TASK_SCHEDULER_DIR,
        INDICE_SNAPSHOTS_DIR,
        WINDOWS_LAUNCHERS_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def is_network_available() -> bool:
    try:
        return ARQUIVOS_ENZO_DIR.exists()
    except (OSError, PermissionError):
        return False


def default_vencimento_input() -> Path:
    return VENCIMENTO_INPUT_DIR / "data_vencimento.xlsx"


def default_vencimento_checkpoint() -> Path:
    return VENCIMENTO_AUDITORIA_DIR / "data_vencimento_auditoria.csv"


def default_vencimento_output() -> Path:
    return VENCIMENTO_RESULTADOS_DIR / "data_vencimento_auditoria_resultado.xlsx"


def default_vencimento_report() -> Path:
    return VENCIMENTO_RESULTADOS_DIR / "data_vencimento_relatorio_final.xlsx"


def default_vencimento_manual_input() -> Path:
    return VENCIMENTO_AUDITORIA_DIR / "vencimento_auditoria.xlsx"


def default_vencimento_manual_output() -> Path:
    return VENCIMENTO_RESULTADOS_DIR / "vencimento_auditoria_resultado.xlsx"


def default_vencimento_log(name: str) -> Path:
    return VENCIMENTO_LOGS_DIR / name


def resolve_existing(preferred: Path, *fallbacks: Path) -> Path:
    for path in (preferred, *fallbacks):
        if path.exists():
            return path
    return preferred


def resolve_vencimento_input() -> Path:
    return resolve_existing(default_vencimento_input(), PROJECT_ROOT / "data_vencimento.xlsx")


def resolve_vencimento_checkpoint() -> Path:
    return resolve_existing(default_vencimento_checkpoint(), PROJECT_ROOT / "data_vencimento_auditoria.csv")


def resolve_vencimento_output() -> Path:
    return resolve_existing(default_vencimento_output(), PROJECT_ROOT / "data_vencimento_auditoria_resultado.xlsx")


def resolve_vencimento_report() -> Path:
    return resolve_existing(default_vencimento_report(), PROJECT_ROOT / "data_vencimento_relatorio_final.xlsx")


def resolve_vencimento_manual_input() -> Path:
    return resolve_existing(default_vencimento_manual_input(), PROJECT_ROOT / "vencimento_auditoria.xlsx")


def resolve_vencimento_manual_output() -> Path:
    return resolve_existing(default_vencimento_manual_output(), PROJECT_ROOT / "vencimento_auditoria_resultado.xlsx")


def resolve_local_indice_master() -> Path:
    return resolve_existing(LOCAL_INDICE_MASTER, INDICE_SNAPSHOTS_DIR / "indice_master.csv", PROJECT_ROOT / "indice_master.csv")


def resolve_local_indice_next() -> Path:
    return resolve_existing(LOCAL_INDICE_NEXT, PROJECT_ROOT / "indice_master_next.txt")


def resolve_secret_key_path() -> Path:
    env_path = os.environ.get("ENERGIA_SECRET_KEY_FILE")
    if env_path:
        return Path(env_path)
    return resolve_existing(SECRET_KEY_PATH, LEGACY_ROOT_SECRET_KEY)


def copel_accessos_candidates(network_dir: Path | None = None) -> list[Path]:
    env_path = os.environ.get("COPEL_ACCESSOS_XLS_PATH")
    candidates: list[Path] = []
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(COPPEL_SECRETS_XLS)
    if network_dir is None:
        network_dir = ARQUIVOS_ENZO_DIR / "DOWNLOAD COPEL"
    candidates.append(network_dir / "acessos_copel.xlsx")
    candidates.append(LEGACY_ROOT_COPPEL_XLS)
    return candidates


def resolve_copel_accessos_xls(network_dir: Path | None = None) -> Path:
    candidates = copel_accessos_candidates(network_dir=network_dir)
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def resolve_indice_master_csv(prefer_network: bool = True) -> Path:
    if prefer_network and is_network_available():
        return INDICE_MASTER_REDE
    return resolve_existing(LOCAL_INDICE_MASTER, PROJECT_ROOT / "indice_master.csv", INDICE_SNAPSHOTS_DIR / "indice_master.csv")
