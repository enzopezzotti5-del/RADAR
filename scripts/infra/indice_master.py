#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
indice_master.py
================
Índice master unificado — ENEL SP, ENEL CE, ENEL RJ, Neoenergia, CEMIG, Equatorial GO.

Localização : rede canônica em ARQUIVOS ENZO, com fallback local em runtime/indice/
"""

from __future__ import annotations

import csv
import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.project_paths import (
    ARQUIVOS_ENZO_DIR,
    LOCAL_INDICE_MASTER,
    LOCAL_INDICE_MASTER_LOCK,
    LOCAL_INDICE_NEXT,
)

try:
    from digitacao_consen.auditoria_schema import ler_auditoria_csv_flexivel
except ModuleNotFoundError:
    ler_auditoria_csv_flexivel = None

# ── filelock com fallback gracioso ────────────────────────────────────────────
try:
    from filelock import FileLock, Timeout as FileLockTimeout
    _FILELOCK_OK = True
except ImportError:
    _FILELOCK_OK = False
    import warnings
    warnings.warn(
        "[indice_master] 'filelock' não instalado — "
        "execute: pip install filelock\n"
        "Operando sem lock de arquivo (risco em acessos concorrentes).",
        stacklevel=2,
    )

_MASTER_FILE_OVERRIDE = os.environ.get("ENERGIA_INDICE_MASTER_FILE", "").strip()
_LOCK_FILE_OVERRIDE = os.environ.get("ENERGIA_INDICE_LOCK_FILE", "").strip()
_COUNTER_FILE_OVERRIDE = os.environ.get("ENERGIA_INDICE_NEXT_FILE", "").strip()

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

_BASE_LOCAL = Path(_MASTER_FILE_OVERRIDE).parent if _MASTER_FILE_OVERRIDE else LOCAL_INDICE_MASTER.parent

# Índices individuais de cada downloader ficam no servidor
_BASE_REDE = ARQUIVOS_ENZO_DIR

# O CSV master fica preferencialmente na rede; fallback local quando a rede
# estiver indisponível.
_MASTER_REDE  = _BASE_REDE  / "indice_master.csv"
_MASTER_LOCAL = _BASE_LOCAL / "indice_master.csv"

# Lock e contador ficam NA REDE para garantir exclusão mútua entre máquinas.
# FileLock usa LockFileEx do Windows que funciona sobre SMB3/UNC.
# Fallback local é usado apenas se a rede estiver inacessível.
_LOCK_FILE_REDE    = _BASE_REDE  / "indice_master.csv.lock"
_COUNTER_FILE_REDE = _BASE_REDE  / "indice_master_next.txt"
_LOCK_FILE_LOCAL    = _BASE_LOCAL / "indice_master.csv.lock"
_COUNTER_FILE_LOCAL = _BASE_LOCAL / "indice_master_next.txt"

_BASE_LOCAL.mkdir(parents=True, exist_ok=True)

# Timeout maior para absorver latência de rede (30s → 60s)
LOCK_TIMEOUT = 60


def _rede_acessivel() -> bool:
    try:
        return _BASE_REDE.exists()
    except (OSError, PermissionError):
        return False


def _escolher_master_file() -> Path:
    """Retorna o caminho de rede se o diretório pai estiver acessível."""
    if _MASTER_FILE_OVERRIDE:
        return Path(_MASTER_FILE_OVERRIDE)
    try:
        if _MASTER_REDE.parent.exists():
            return _MASTER_REDE
    except (OSError, PermissionError):
        pass
    return _MASTER_LOCAL


def _escolher_lock_file() -> Path:
    """Lock na rede para exclusão entre máquinas; fallback local se offline."""
    if _LOCK_FILE_OVERRIDE:
        return Path(_LOCK_FILE_OVERRIDE)
    if _rede_acessivel():
        return _LOCK_FILE_REDE
    return _LOCK_FILE_LOCAL


def _escolher_counter_file() -> Path:
    """Contador na rede para sequência única entre máquinas; fallback local."""
    if _COUNTER_FILE_OVERRIDE:
        return Path(_COUNTER_FILE_OVERRIDE)
    if _rede_acessivel():
        return _COUNTER_FILE_REDE
    return _COUNTER_FILE_LOCAL


def _path_exists_safe(path: Path) -> bool:
    try:
        return path.exists()
    except (OSError, PermissionError):
        return False


MASTER_FILE = _escolher_master_file()

MASTER_FIELDS = [
    "INDICE",
    "CONCESSIONARIA",
    "SISTEMA",
    "ESTADO",
    "UC",
    "MES_REF",
    "FATURA_ID",
    "CNPJ",
    "DATA_DOWNLOAD",
    "ARQUIVO",
    "STATUS_DIGITACAO",
    "DATA_DIGITACAO",
]

# Mapeamento dos status do auditoria_resultados.csv para o master.
_STATUS_AUDITORIA_PARA_MASTER: dict[str, str] = {
    "sucesso_auditoria":         "DIGITADO",
    "auditoria_sem_valor":       "PENDENTE",
    "pulado_carimbo_existente":  "DIGITADO",
    "pulado_referencia_existente": "PULADO",
}


def _normalizar_indice_bb(valor: str) -> str:
    txt = (valor or "").strip().upper()
    if not txt:
        return ""
    if txt.endswith(".0"):
        txt = txt[:-2]
    if txt.startswith("BB_"):
        return txt
    if txt.isdigit():
        return f"BB_{txt}"
    return txt

# Nome legível da concessionária por sistema — obrigatório para rastreabilidade.
_CONCESSIONARIA_SISTEMA: dict[str, str] = {
    "CEB":            "CEB",
    "CELPE":          "CELPE",
    "CEMIG":          "CEMIG",
    "COELBA":         "COELBA",
    "COPEL":          "COPEL",
    "COSERN":         "COSERN",
    "CPFL":           "CPFL Paulista",
    "EDP":            "EDP Espírito Santo",
    "ELEKTRO":        "ELEKTRO",
    "ENEL":           "Enel São Paulo",
    "ENEL_CE":        "Enel Ceará",
    "ENEL_RJ":        "Enel Rio de Janeiro",
    "ENEL_SP":        "Enel São Paulo",
    "ENERGISA":       "Energisa Rondônia",
    "EQUATORIAL":     "Equatorial GO",
    "NEOENERGIA_CEB": "CEB",
    "RGE":            "RGE Sul",
}

# Neoenergia opera sob marcas locais — a CONCESSIONARIA é derivada do ESTADO.
_NEOENERGIA_POR_ESTADO: dict[str, str] = {
    "BAHIA":               "COELBA",
    "DISTRITO FEDERAL":    "CEB",
    "PERNAMBUCO":          "CELPE",
    "RIO GRANDE DO NORTE": "COSERN",
    "SÃO PAULO":           "ELEKTRO",
    "MATO GROSSO DO SUL":  "ELEKTRO",
}

# Estado extenso por sistema — fallback quando o downloader não passa o estado.
# Neoenergia e Equatorial passam o estado dinamicamente via row, então não constam aqui.
_ESTADO_FIXO_SISTEMA: dict[str, str] = {
    "CEB":            "DISTRITO FEDERAL",
    "ENEL":           "SÃO PAULO",
    "ENEL_SP":        "SÃO PAULO",
    "ENEL_CE":        "CEARÁ",
    "ENEL_RJ":        "RIO DE JANEIRO",
    "CEMIG":          "MINAS GERAIS",
    "COPEL":          "PARANÁ",
    "CPFL":           "SÃO PAULO",
    "EQUATORIAL":     "GOIÁS",
    "EDP":            "ESPÍRITO SANTO",
    "ENERGISA":       "RONDÔNIA",
    "NEOENERGIA_CEB": "DISTRITO FEDERAL",
    "RGE":            "RIO GRANDE DO SUL",
}

# Expansão de abreviações de estado (usada na migração e como fallback)
_ESTADO_ABREV: dict[str, str] = {
    "SP": "SÃO PAULO",
    "CE": "CEARÁ",
    "RJ": "RIO DE JANEIRO",
    "MG": "MINAS GERAIS",
    "PR": "PARANÁ",
    "GO": "GOIÁS",
    "ES": "ESPÍRITO SANTO",
    "BA": "BAHIA",
    "PE": "PERNAMBUCO",
    "AM": "AMAZONAS",
    "PA": "PARÁ",
    "MT": "MATO GROSSO",
    "MS": "MATO GROSSO DO SUL",
    "AL": "ALAGOAS",
    "SE": "SERGIPE",
    "PI": "PIAUÍ",
    "MA": "MARANHÃO",
    "RN": "RIO GRANDE DO NORTE",
    "PB": "PARAÍBA",
    "TO": "TOCANTINS",
    "RO": "RONDÔNIA",
    "AC": "ACRE",
    "RR": "RORAIMA",
    "AP": "AMAPÁ",
    "RS": "RIO GRANDE DO SUL",
    "SC": "SANTA CATARINA",
    "DF": "DISTRITO FEDERAL",
}

_mem_lock = threading.Lock()
_log = logging.getLogger("indice_master")

# =============================================================================
# NORMALIZAÇÃO
# =============================================================================

_MESES_PT = {
    "01": "JANEIRO",   "02": "FEVEREIRO", "03": "MARÇO",
    "04": "ABRIL",     "05": "MAIO",      "06": "JUNHO",
    "07": "JULHO",     "08": "AGOSTO",    "09": "SETEMBRO",
    "10": "OUTUBRO",   "11": "NOVEMBRO",  "12": "DEZEMBRO",
    "JANEIRO": "01",   "FEVEREIRO": "02", "MARCO": "03",   "MARÇO": "03",
    "ABRIL": "04",     "MAIO": "05",      "JUNHO": "06",
    "JULHO": "07",     "AGOSTO": "08",    "SETEMBRO": "09",
    "OUTUBRO": "10",   "NOVEMBRO": "11",  "DEZEMBRO": "12",
}


def normalizar_mes_ref(ref: str) -> str:
    ref = (ref or "").strip().upper()
    if len(ref) == 7 and ref[2] == "-" and ref[:2].isdigit():
        return ref
    if len(ref) == 7 and ref[2] == "/" and ref[:2].isdigit():
        return ref[:2] + "-" + ref[3:]
    for sep in ("/", "-"):
        if sep in ref:
            partes = ref.split(sep, 1)
            if len(partes) == 2:
                nome, ano = partes[0].strip(), partes[1].strip()
                num = _MESES_PT.get(nome)
                if num and ano.isdigit():
                    return f"{num}-{ano}"
    return ref

def normalizar_sistema_dedup(sistema: str) -> str:
    sist = str(sistema or "").strip().upper()
    if sist.startswith("ENEL"):
        return "ENEL"
    return sist


def chave_dedup(uc_ou_instalacao: str, mes_ref: str, sistema: str = "") -> str:
    _uc_raw = str(uc_ou_instalacao or "").strip()
    uc_norm = "".join(c for c in _uc_raw if c.isdigit()).lstrip("0") or "0"
    ref_norm = normalizar_mes_ref(mes_ref)
    sist_norm = normalizar_sistema_dedup(sistema)
    if sist_norm:
        return f"{sist_norm}|{uc_norm}|{ref_norm}"
    return f"{uc_norm}|{ref_norm}"


# =============================================================================
# LOCK DE ARQUIVO
# =============================================================================

class _NullLock:
    def __enter__(self): return self
    def __exit__(self, *_): pass


def _make_filelock(master_file: Path):  # noqa: ARG001 — master_file reservado para compatibilidade
    if not _FILELOCK_OK:
        return _NullLock()
    lock_path = _escolher_lock_file()
    _log.debug(f"[master] lock em: {lock_path}")
    return FileLock(str(lock_path), timeout=LOCK_TIMEOUT)


def _atomic_replace(src: Path, dst: Path, *, retries: int = 6, delay: float = 0.5) -> None:
    """
    Renomeia src → dst com retry para WinError 5 (acesso negado em UNC).
    No Windows, outro processo lendo o CSV via rede pode bloquear o replace momentaneamente.
    """
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            src.replace(dst)
            return
        except OSError as exc:
            last_exc = exc
            winerror = getattr(exc, "winerror", None)
            if winerror == 5 and attempt < retries - 1:  # WinError 5 = Acesso negado
                time.sleep(delay * (attempt + 1))
                continue
            raise
    raise last_exc  # type: ignore[misc]


# =============================================================================
# CLASSE PRINCIPAL
# =============================================================================

class MasterIndice:

    def __init__(self, master_file: Path = MASTER_FILE, *, scan_individual_indexes: bool = True):
        self.master_file = Path(master_file)
        self._filelock   = _make_filelock(self.master_file)
        self._counter_file = (
            _escolher_counter_file()
            if self.master_file.resolve() == Path(MASTER_FILE).resolve()
            else self.master_file.with_name("indice_master_next.txt")
        )
        self._reservation_file = self._counter_file.with_name("indice_master_carimbos.jsonl")
        self._proximo_num: int      = 2_000_000
        self._ja_baixados: set      = set()
        self._indices_registrados: set[str] = set()
        self._origens_por_uc_mes: dict[str, set[str]] = {}
        self._faturas_por_sistema: set[str] = set()
        self._carregar()
        if scan_individual_indexes:
            self._varrer_indices_individuais()
        # Aplica reservas pendentes do arquivo-contador (carimbos consumidos mas
        # ainda não gravados no CSV por uma execução anterior ou paralela).
        try:
            txt = self._counter_file.read_text(encoding="utf-8").strip()
            if txt:
                num = int(txt)
                if num > self._proximo_num:
                    self._proximo_num = num
        except (FileNotFoundError, ValueError):
            pass
        # Garante que o counter file reflita o estado real do master.
        # Sem isso, downloader que lê next.txt sem instanciar MasterIndice
        # pode ler valor defasado e colidir com carimbos já atribuídos pelo watcher.
        try:
            self._counter_file.write_text(str(self._proximo_num), encoding="utf-8")
        except Exception:
            pass

    def _registrar_estado_carimbo(
        self,
        indice_bb: str,
        estado: str,
        *,
        sistema: str = "",
        uc: str = "",
        mes_ref: str = "",
        arquivo: str = "",
    ) -> None:
        evento = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "indice": _normalizar_indice_bb(indice_bb),
            "estado": estado,
            "sistema": sistema,
            "uc": uc,
            "mes_ref": mes_ref,
            "arquivo": arquivo,
            "pid": os.getpid(),
            "cwd": os.getcwd(),
            "session_id": os.environ.get("WATCHER_V2_SESSION_ID")
            or os.environ.get("ENERGIA_SESSION_ID")
            or "",
            "counter_file": str(self._counter_file),
            "master_file": str(self.master_file),
        }
        try:
            self._reservation_file.parent.mkdir(parents=True, exist_ok=True)
            with self._reservation_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(evento, ensure_ascii=False) + "\n")
        except Exception as exc:
            _log.warning(f"[master] não gravou trilha do carimbo {indice_bb}: {exc}")

    def inutilizar_carimbo(self, indice_bb: str, *, motivo: str = "") -> None:
        """
        Registra um carimbo como inutilizado na trilha operacional.

        Não cria linha no índice e não altera o contador. Use quando há lacuna
        sem evidência suficiente para reutilização segura.
        """
        self._registrar_estado_carimbo(
            indice_bb,
            "INUTILIZADO",
            arquivo=motivo,
        )

    def _registrar_origem(self, sistema: str, uc_ou_instalacao: str, mes_ref: str) -> None:
        uc_mes_key = chave_dedup(uc_ou_instalacao, mes_ref)
        sist_norm = normalizar_sistema_dedup(sistema)
        if sist_norm:
            self._origens_por_uc_mes.setdefault(uc_mes_key, set()).add(sist_norm)
            self._ja_baixados.add(chave_dedup(uc_ou_instalacao, mes_ref, sist_norm))

    def _registrar_indice_existente(self, indice_str: str) -> None:
        indice_str = (indice_str or "").strip()
        if indice_str.startswith("BB_"):
            self._indices_registrados.add(indice_str)

    def _registrar_fatura_existente(self, sistema: str, fatura_id: str) -> None:
        sist_norm = normalizar_sistema_dedup(sistema)
        fid_norm = (fatura_id or "").strip()
        if sist_norm and fid_norm:
            self._faturas_por_sistema.add(f"{sist_norm}|{fid_norm}")

    def _criar_arquivo(self) -> None:
        self.master_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.master_file, "w", newline="", encoding="utf-8-sig") as f:
            csv.DictWriter(f, fieldnames=MASTER_FIELDS).writeheader()
        _log.info(f"[master] Arquivo criado: {self.master_file}")

    def _carregar(self) -> None:
        if not self.master_file.exists():
            _log.warning(f"[master] Não encontrado — criando: {self.master_file}")
            self._criar_arquivo()
            return

        _log.info(f"[master] Lendo: {self.master_file}")
        lidas = bb_count = 0

        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                with open(self.master_file, newline="", encoding=enc) as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        lidas += 1
                        indice_str = ""
                        for k in row:
                            if k.strip().upper().replace("\ufeff", "") == "INDICE":
                                indice_str = (row[k] or "").strip()
                                break

                        sistema = (row.get("SISTEMA") or "").strip()
                        uc      = (row.get("UC") or row.get("INSTALACAO") or "").strip()
                        mes_ref = (row.get("MES_REF") or "").strip()
                        fatura_id = (row.get("FATURA_ID") or "").strip()

                        if indice_str.startswith("BB_"):
                            bb_count += 1
                            self._registrar_indice_existente(indice_str)
                            try:
                                num = int(indice_str[3:])
                                if num >= self._proximo_num:
                                    self._proximo_num = num + 1
                            except ValueError:
                                pass

                        if uc and mes_ref:
                            self._registrar_origem(sistema, uc, mes_ref)
                        if fatura_id:
                            self._registrar_fatura_existente(sistema, fatura_id)

                _log.info(
                    f"[master] OK — {lidas} linhas | {bb_count} BB_ | "
                    f"próximo: BB_{self._proximo_num}"
                )
                return

            except UnicodeDecodeError:
                continue
            except Exception as e:
                _log.error(f"[master] Erro ao ler (enc={enc}): {e}")
                raise

    def _varrer_indices_individuais(self) -> None:
        """Usa _BASE_REDE para localizar os índices individuais de cada sistema."""
        fontes = [
            ("ENEL_SP",     _BASE_REDE / "DOWNLOAD ENEL"        / "indice_faturas.csv",                  "UC",         "MES_REF",        "INDICE"),
            ("ENEL_CE",     _BASE_REDE / "DOWNLOAD ENEL CE"     / "indice_faturas.csv",                  "UC",         "MES_REF",        "INDICE"),
            ("ENEL_RJ",     _BASE_REDE / "DOWNLOAD ENEL RJ"     / "indice_faturas_enel_rj.csv",          "UC",         "MES_REF",        "INDICE"),
            ("NEOENERGIA",  _BASE_REDE / "DOWNLOAD NEOENERGIA"  / "indice_downloads_neoenergia.csv",     "instalacao", "mes_referencia", "id"),
            ("CEMIG",       _BASE_REDE / "DOWNLOAD CEMIG"        / "indice_faturas_cemig.csv",            "UC",         "MES_REF",        "INDICE"),
            ("EQUATORIAL",  _BASE_REDE / "DOWNLOAD EQUATORIAL"  / "indice_downloads_equatorial.csv",     "INSTALACAO", "MES_REF",        "INDICE"),
            ("COPEL",       _BASE_REDE / "DOWNLOAD COPEL"        / "indice_faturas_copel_mt.csv",         "INSTALACAO", "MES_REF",        "INDICE"),
            ("COPEL",       _BASE_REDE / "DOWNLOAD COPEL"        / "indice_faturas_copel_bt.csv",         "INSTALACAO", "MES_REF",        "INDICE"),
            ("CPFL",        _BASE_REDE / "DOWNLOAD CPFL"         / "indice_faturas_cpfl.csv",             "UC",         "MES_REF",        "INDICE"),
        ]

        for sistema, path, col_uc, col_mes, col_id in fontes:
            if not _path_exists_safe(path):
                _log.debug(f"[master] Índice {sistema} não encontrado: {path}")
                continue
            lidas = 0
            for enc in ("utf-8-sig", "utf-8", "latin-1"):
                try:
                    with open(path, newline="", encoding=enc) as f:
                        for row in csv.DictReader(f):
                            lidas += 1
                            uc      = (row.get(col_uc)  or "").strip()
                            mes_ref = (row.get(col_mes) or "").strip()
                            id_str  = (row.get(col_id)  or "").strip()
                            fatura_id = (row.get("FATURA_ID") or row.get("chave_unica") or "").strip()

                            if uc and mes_ref:
                                self._registrar_origem(sistema, uc, mes_ref)
                            if fatura_id:
                                self._registrar_fatura_existente(sistema, fatura_id)

                            if id_str.startswith("BB_"):
                                self._registrar_indice_existente(id_str)
                                try:
                                    num = int(id_str[3:])
                                    if num >= self._proximo_num:
                                        self._proximo_num = num + 1
                                except ValueError:
                                    pass
                            else:
                                try:
                                    num = int(id_str)
                                    if num >= self._proximo_num:
                                        self._proximo_num = num + 1
                                except ValueError:
                                    pass

                    _log.info(f"[master] {sistema}: {lidas} linhas | próximo: BB_{self._proximo_num}")
                    break
                except UnicodeDecodeError:
                    continue
                except Exception as e:
                    _log.warning(f"[master] Erro ao ler índice {sistema}: {e}")
                    break

    def _sincronizar_proximo_num(self) -> None:
        # 1. Arquivo-contador: persiste reservas que ainda n\u00e3o chegaram ao CSV
        counter_file = _escolher_counter_file()
        try:
            txt = counter_file.read_text(encoding="utf-8").strip()
            if txt:
                num = int(txt)
                if num >= self._proximo_num:
                    self._proximo_num = num
        except (FileNotFoundError, ValueError):
            pass
        except Exception:
            pass

        if not self.master_file.exists():
            return
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                with open(self.master_file, newline="", encoding=enc) as f:
                    for row in csv.DictReader(f):
                        for k in row:
                            if k.strip().upper().replace("\ufeff", "") == "INDICE":
                                s = (row[k] or "").strip()
                                if s.startswith("BB_"):
                                    try:
                                        num = int(s[3:])
                                        if num >= self._proximo_num:
                                            self._proximo_num = num + 1
                                    except ValueError:
                                        pass
                                break
                return
            except UnicodeDecodeError:
                continue
            except Exception:
                return

    @property
    def proximo_carimbo(self) -> str:
        return f"BB_{self._proximo_num}"

    def ja_foi_baixado(self, uc_ou_instalacao: str, mes_ref: str, sistema: str | None = None) -> bool:
        if sistema:
            return chave_dedup(uc_ou_instalacao, mes_ref, sistema) in self._ja_baixados
        return chave_dedup(uc_ou_instalacao, mes_ref) in self._origens_por_uc_mes

    def sistemas_ja_registrados(self, uc_ou_instalacao: str, mes_ref: str) -> list[str]:
        return sorted(self._origens_por_uc_mes.get(chave_dedup(uc_ou_instalacao, mes_ref), set()))

    def indice_existe(self, indice_bb: str) -> bool:
        return (indice_bb or "").strip() in self._indices_registrados

    def ja_foi_baixado_por_fatura(self, fatura_id: str, sistema: str) -> bool:
        fid_norm = (fatura_id or "").strip()
        sist_norm = normalizar_sistema_dedup(sistema)
        if not fid_norm or not sist_norm:
            return False
        return f"{sist_norm}|{fid_norm}" in self._faturas_por_sistema

    def consumir_carimbo(self) -> str:
        with _mem_lock:
            try:
                with self._filelock:
                    self._sincronizar_proximo_num()
                    carimbo = f"BB_{self._proximo_num}"
                    self._proximo_num += 1
                    # Persiste a reserva no arquivo-contador ANTES de soltar o
                    # lock — impede que outro processo leia o mesmo número do CSV
                    # enquanto registrar() ainda não gravou esta entrada.
                    try:
                        _escolher_counter_file().write_text(
                            str(self._proximo_num), encoding="utf-8"
                        )
                    except Exception as e_cnt:
                        _log.warning(f"[master] não gravou contador: {e_cnt}")
                    self._registrar_estado_carimbo(carimbo, "RESERVADO")
                    return carimbo
            except Exception as e:
                _log.warning(f"[master] consumir_carimbo sem lock: {e}")
                carimbo = f"BB_{self._proximo_num}"
                self._proximo_num += 1
                self._registrar_estado_carimbo(carimbo, "RESERVADO_SEM_LOCK")
                return carimbo

    def registrar(
        self,
        indice_bb:     str,
        sistema:       str,
        uc:            str,
        mes_ref:       str,
        fatura_id:     str = "",
        cnpj:          str = "",
        estado:        str = "",
        instalacao:    str = "",   # mantido para compatibilidade — unificado em UC
        arquivo:       str = "",
        concessionaria: str = "",  # derivado automaticamente de sistema se vazio
    ) -> None:
        sistema_up   = sistema.strip().upper()
        mes_ref_norm = normalizar_mes_ref(mes_ref)

        # UC unificado: usa `uc` se preenchido, senão `instalacao`
        uc_final = (uc or instalacao or "").strip()

        # Estado: prioridade → param → fixo por sistema → abreviação expandida
        if not estado:
            estado = _ESTADO_FIXO_SISTEMA.get(sistema_up, "")
        estado = _ESTADO_ABREV.get(estado.strip().upper(), estado.strip().upper())

        # Concessionária: derivada automaticamente se não informada.
        # Neoenergia usa marca local dependendo do estado.
        if not concessionaria:
            if sistema_up == "NEOENERGIA":
                concessionaria = _NEOENERGIA_POR_ESTADO.get(estado, "Neoenergia")
            else:
                concessionaria = _CONCESSIONARIA_SISTEMA.get(sistema_up, sistema_up)

        with _mem_lock:
            try:
                with self._filelock:
                    novo = not self.master_file.exists()
                    with open(self.master_file, "a", newline="", encoding="utf-8-sig") as f:
                        w = csv.DictWriter(f, fieldnames=MASTER_FIELDS)
                        if novo:
                            w.writeheader()
                        w.writerow({
                            "INDICE":           indice_bb,
                            "CONCESSIONARIA":   concessionaria,
                            "SISTEMA":          sistema_up,
                            "ESTADO":           estado,
                            "UC":               uc_final,
                            "MES_REF":          mes_ref_norm,
                            "FATURA_ID":        fatura_id,
                            "CNPJ":             cnpj,
                            "DATA_DOWNLOAD":    datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                            "ARQUIVO":          arquivo,
                            "STATUS_DIGITACAO": "PENDENTE",
                            "DATA_DIGITACAO":   "",
                        })
            except Exception as e:
                _log.error(f"[master] Falha ao gravar (filelock/IO): {e}")
                raise

            try:
                num = int(indice_bb[3:])
                if num >= self._proximo_num:
                    self._proximo_num = num + 1
            except (ValueError, IndexError):
                pass
            self._registrar_origem(sistema, uc_final, mes_ref_norm)
            self._registrar_indice_existente(indice_bb)
            self._registrar_fatura_existente(sistema, fatura_id)
            self._registrar_estado_carimbo(
                indice_bb,
                "CONSUMIDO",
                sistema=sistema_up,
                uc=uc_final,
                mes_ref=mes_ref_norm,
                arquivo=arquivo,
            )

        _log.info(
            f"[master] Gravado: {indice_bb} | {concessionaria} | UC={uc_final} | {mes_ref_norm}"
        )

    def atualizar_digitacao(self, indice_bb: str, status: str, *, log_evento: bool = True) -> bool:
        """
        Atualiza STATUS_DIGITACAO e DATA_DIGITACAO de um registro existente.
        Reescreve o CSV atomicamente via lock. Retorna True se encontrou o registro.
        """
        indice_bb = _normalizar_indice_bb(indice_bb)
        if not indice_bb:
            return False

        status_norm = (status or "").strip().upper()
        data_str = (
            datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            if status_norm in {"DIGITADO", "PULADO"}
            else ""
        )

        with _mem_lock:
            try:
                with self._filelock:
                    if not self.master_file.exists():
                        return False

                    linhas: list[dict] = []
                    encontrado = False

                    for enc in ("utf-8-sig", "utf-8", "latin-1"):
                        try:
                            with open(self.master_file, newline="", encoding=enc) as f:
                                linhas = list(csv.DictReader(f))
                            break
                        except UnicodeDecodeError:
                            continue

                    for row in linhas:
                        if _normalizar_indice_bb(row.get("INDICE") or "") == indice_bb:
                            row["STATUS_DIGITACAO"] = status_norm or status
                            row["DATA_DIGITACAO"]   = data_str
                            encontrado = True

                    if not encontrado:
                        return False

                    tmp = self.master_file.with_suffix(".tmp")
                    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
                        w = csv.DictWriter(f, fieldnames=MASTER_FIELDS, extrasaction="ignore")
                        w.writeheader()
                        for row in linhas:
                            # garante que colunas novas existam mesmo em linhas antigas
                            row.setdefault("STATUS_DIGITACAO", "")
                            row.setdefault("DATA_DIGITACAO", "")
                            w.writerow(row)
                    _atomic_replace(tmp, self.master_file)
                    if log_evento:
                        _log.info(f"[master] Digitacao: {indice_bb} -> {status}")
                    return True

            except Exception as e:
                _log.error(f"[master] Falha em atualizar_digitacao({indice_bb}): {e}")
                return False

    def atualizar_arquivo_final(
        self,
        indice_bb: str,
        arquivo_final: "str | Path",
        *,
        hash_esperado: str | None = None,
        log_evento: bool = True,
    ) -> bool:
        """
        Atualiza ARQUIVO para o destino físico final de um carimbo.

        A operação é atômica e idempotente. Ela só altera uma linha quando o
        carimbo existe exatamente uma vez e o arquivo final existe.
        """
        indice_bb = _normalizar_indice_bb(indice_bb)
        if not indice_bb:
            return False

        destino = Path(arquivo_final)
        if not destino.exists() or not destino.is_file():
            _log.error(f"[master] arquivo final inexistente para {indice_bb}: {destino}")
            return False

        if hash_esperado:
            import hashlib

            h = hashlib.sha256()
            with destino.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    h.update(chunk)
            if h.hexdigest().lower() != hash_esperado.lower():
                _log.error(f"[master] hash divergente para {indice_bb}: {destino}")
                return False

        destino_str = str(destino)

        with _mem_lock:
            try:
                with self._filelock:
                    if not self.master_file.exists():
                        return False

                    linhas: list[dict] = []
                    for enc in ("utf-8-sig", "utf-8", "latin-1"):
                        try:
                            with open(self.master_file, newline="", encoding=enc) as f:
                                linhas = list(csv.DictReader(f))
                            break
                        except UnicodeDecodeError:
                            continue

                    matches = [
                        row
                        for row in linhas
                        if _normalizar_indice_bb(row.get("INDICE") or "") == indice_bb
                    ]
                    if len(matches) != 1:
                        _log.error(f"[master] {indice_bb} ausente ou duplicado: {len(matches)}")
                        return False

                    row = matches[0]
                    if (row.get("ARQUIVO") or "") == destino_str:
                        return True

                    row["ARQUIVO"] = destino_str

                    tmp = self.master_file.with_suffix(".tmp")
                    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
                        w = csv.DictWriter(f, fieldnames=MASTER_FIELDS, extrasaction="ignore")
                        w.writeheader()
                        for item in linhas:
                            for field in MASTER_FIELDS:
                                item.setdefault(field, "")
                            w.writerow(item)
                    _atomic_replace(tmp, self.master_file)
                    if log_evento:
                        _log.info(f"[master] Arquivo final: {indice_bb} -> {destino_str}")
                    return True

            except Exception as e:
                _log.error(f"[master] Falha em atualizar_arquivo_final({indice_bb}): {e}")
                return False


# =============================================================================
# FUNÇÃO UTILITÁRIA: MARCAR DIGITADOS A PARTIR DO AUDITORIA_RESULTADOS.CSV
# =============================================================================

def marcar_digitados_do_auditoria(
    auditoria_csv: "Path | str",
    master: "MasterIndice",
) -> dict[str, int]:
    """
    Lê o auditoria_resultados.csv gerado pela etapa de digitação e atualiza
    STATUS_DIGITACAO / DATA_DIGITACAO no master para cada carimbo encontrado.

    Retorna contadores: {'digitado': N, 'pulado': N, 'erro': N, 'nao_encontrado': N}
    """
    from pathlib import Path as _Path  # import local para não poluir o escopo global
    auditoria_path = _Path(auditoria_csv)
    if not auditoria_path.exists():
        _log.warning(f"[master] auditoria_resultados nao encontrado: {auditoria_path}")
        return {}

    contadores: dict[str, int] = {"digitado": 0, "pulado": 0, "erro": 0, "nao_encontrado": 0}

    if ler_auditoria_csv_flexivel is not None:
        linhas_padrao = ler_auditoria_csv_flexivel(auditoria_path)
        if linhas_padrao:
            for row in linhas_padrao:
                carimbo = _normalizar_indice_bb(str(row.get("carimbo", "")).strip())
                status = str(row.get("status", "")).strip().lower()

                if not carimbo or not carimbo.startswith("BB_"):
                    continue

                if status in _STATUS_AUDITORIA_PARA_MASTER:
                    status_master = _STATUS_AUDITORIA_PARA_MASTER[status]
                elif status.startswith("erro"):
                    status_master = "ERRO"
                else:
                    status_master = "PENDENTE"

                ok = master.atualizar_digitacao(carimbo, status_master, log_evento=False)
                chave = status_master.lower() if status_master.lower() in contadores else "nao_encontrado"
                if not ok:
                    chave = "nao_encontrado"
                contadores[chave] = contadores.get(chave, 0) + 1

            _log.info(
                f"[master] marcar_digitados: {contadores} | fonte: {auditoria_path.name}"
            )
            return contadores

    linhas: list[dict] = []
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(auditoria_path, newline="", encoding=enc) as f:
                linhas = list(csv.reader(f, delimiter=";"))
            break
        except UnicodeDecodeError:
            continue

    if not linhas:
        return contadores

    # Detecta cabeçalho e índice da coluna carimbo/status
    header = [c.strip().lower() for c in linhas[0]]
    try:
        idx_carimbo = header.index("carimbo")
        idx_status  = header.index("status")
    except ValueError:
        _log.warning(f"[master] auditoria sem colunas 'carimbo'/'status': {auditoria_path}")
        return contadores

    for cols in linhas[1:]:
        if len(cols) <= max(idx_carimbo, idx_status):
            continue
        carimbo = _normalizar_indice_bb(cols[idx_carimbo].strip())
        # Alguns auditoria_resultados.csv chegam com ';' extras no campo de
        # detalhes. Nesses casos, o status real continua sendo a última coluna.
        status_col = cols[-1] if len(cols) > len(header) else cols[idx_status]
        status  = status_col.strip().lower()

        if not carimbo or not carimbo.startswith("BB_"):
            continue

        if status in _STATUS_AUDITORIA_PARA_MASTER:
            status_master = _STATUS_AUDITORIA_PARA_MASTER[status]
        elif status.startswith("erro"):
            status_master = "ERRO"
        else:
            status_master = "PENDENTE"

        ok = master.atualizar_digitacao(carimbo, status_master, log_evento=False)

        chave = status_master.lower() if status_master.lower() in contadores else "nao_encontrado"
        if not ok:
            chave = "nao_encontrado"
        contadores[chave] = contadores.get(chave, 0) + 1

    _log.info(
        f"[master] marcar_digitados: {contadores} | fonte: {auditoria_path.name}"
    )
    return contadores


# =============================================================================
# MIGRAÇÃO DE ÍNDICES LEGADOS
# =============================================================================

def _migrar_indice_generico(
    indice_path, master, sistema, col_uc="UC", col_mes="MES_REF",
    col_indice="INDICE", col_fatura_id="FATURA_ID", col_cnpj="CNPJ",
    col_estado="ESTADO", col_arquivo="ARQUIVO", dry_run=False,
) -> int:
    if not Path(indice_path).exists():
        print(f"Índice {sistema} não encontrado: {indice_path}")
        return 0

    migradas = 0
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(indice_path, newline="", encoding=enc) as f:
                for row in csv.DictReader(f):
                    uc      = (row.get(col_uc)       or "").strip()
                    mes_ref = (row.get(col_mes)       or "").strip()
                    indice  = (row.get(col_indice)    or "").strip()
                    fid     = (row.get(col_fatura_id) or "").strip()
                    cnpj    = (row.get(col_cnpj)      or "").strip()
                    estado  = (row.get(col_estado)    or "").strip()
                    arquivo = (row.get(col_arquivo)   or "").strip()

                    if not uc or not mes_ref:
                        continue
                    if master.ja_foi_baixado(uc, mes_ref, sistema):
                        continue

                    if not dry_run:
                        master.registrar(
                            indice_bb=indice, sistema=sistema, uc=uc,
                            mes_ref=mes_ref, fatura_id=fid, cnpj=cnpj,
                            estado=estado, arquivo=arquivo,
                        )
                    migradas += 1
            break
        except UnicodeDecodeError:
            continue

    print(f"Migração {sistema}: {migradas} linhas {'(dry run)' if dry_run else 'gravadas'} no master")
    return migradas


def migrar_indice_enel(indice_enel, master, sistema="ENEL_SP", dry_run=False):
    return _migrar_indice_generico(indice_enel, master, sistema=sistema, dry_run=dry_run)

def migrar_indice_enel_rj(indice_enel_rj, master, dry_run=False):
    return _migrar_indice_generico(indice_enel_rj, master, sistema="ENEL_RJ", dry_run=dry_run)

def migrar_indice_neoenergia(indice_neo, master, dry_run=False):
    return _migrar_indice_generico(
        indice_neo, master, sistema="NEOENERGIA",
        col_uc="instalacao", col_mes="mes_referencia", col_indice="id",
        col_fatura_id="chave_unica", col_cnpj="cnpj", col_estado="estado",
        dry_run=dry_run,
    )

def migrar_indice_cemig(indice_cemig, master, dry_run=False):
    return _migrar_indice_generico(indice_cemig, master, sistema="CEMIG", dry_run=dry_run)

def migrar_indice_equatorial(indice_eq, master, dry_run=False):
    return _migrar_indice_generico(
        indice_eq, master, sistema="EQUATORIAL",
        col_uc="INSTALACAO", col_mes="MES_REF", col_indice="INDICE",
        dry_run=dry_run,
    )


# =============================================================================
# CLI DE DIAGNÓSTICO
# =============================================================================

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")

    _INDICES_PADRAO = {
        "--migrar-enel-sp":    (_BASE_REDE / "DOWNLOAD ENEL"        / "indice_faturas.csv",                  migrar_indice_enel,        {"sistema": "ENEL_SP"}),
        "--migrar-enel-ce":    (_BASE_REDE / "DOWNLOAD ENEL CE"     / "indice_faturas.csv",                  migrar_indice_enel,        {"sistema": "ENEL_CE"}),
        "--migrar-enel-rj":    (_BASE_REDE / "DOWNLOAD ENEL RJ"     / "indice_faturas_enel_rj.csv",          migrar_indice_enel_rj,     {}),
        "--migrar-neoenergia": (_BASE_REDE / "DOWNLOAD NEOENERGIA"  / "indice_downloads_neoenergia.csv",     migrar_indice_neoenergia,  {}),
        "--migrar-cemig":      (_BASE_REDE / "DOWNLOAD CEMIG"        / "indice_faturas_cemig.csv",            migrar_indice_cemig,       {}),
        "--migrar-equatorial": (_BASE_REDE / "DOWNLOAD EQUATORIAL"  / "indice_downloads_equatorial.csv",     migrar_indice_equatorial,  {}),
        "--migrar-enel":       (_BASE_REDE / "DOWNLOAD ENEL"        / "indice_faturas.csv",                  migrar_indice_enel,        {"sistema": "ENEL_SP"}),
    }

    master = MasterIndice()

    print()
    _origem = "rede" if master.master_file == _MASTER_REDE else "LOCAL (rede indisponível)"
    _lock_path    = _escolher_lock_file()
    _counter_path = _escolher_counter_file()
    _lock_origem    = "rede" if _lock_path == _LOCK_FILE_REDE else "LOCAL (rede indisponível)"
    _counter_origem = "rede" if _counter_path == _COUNTER_FILE_REDE else "LOCAL (rede indisponível)"
    print(f"  Master      : {master.master_file}  ({_origem})")
    print(f"  Lock        : {_lock_path}  ({_lock_origem})")
    print(f"  Contador    : {_counter_path}  ({_counter_origem})")
    print(f"  Índices raw : {_BASE_REDE}  (servidor)")
    print(f"  Existe      : {master.master_file.exists()}")
    print(f"  Registros   : {len(master._ja_baixados)}")
    print(f"  Próx. BB_   : {master.proximo_carimbo}")
    print(f"  filelock    : {'✓ disponível' if _FILELOCK_OK else '✗ NÃO instalado — pip install filelock'}")
    print()

    dry = "--dry-run" in sys.argv
    migrou_algo = False

    for flag, (caminho_padrao, fn_migrar, kwargs) in _INDICES_PADRAO.items():
        if flag not in sys.argv:
            continue
        migrou_algo = True
        idx = sys.argv.index(flag)
        if idx + 1 < len(sys.argv) and not sys.argv[idx + 1].startswith("--"):
            caminho = Path(sys.argv[idx + 1].strip().strip('"').strip("'"))
        else:
            caminho = caminho_padrao

        print(f"  Índice      : {caminho}")
        if not caminho.exists():
            print(f"\nERRO: arquivo não encontrado — {caminho}")
            sys.exit(1)

        fn_migrar(caminho, master, dry_run=dry, **kwargs)
        print(f"  Próx. após migração: {master.proximo_carimbo}")
        print()

    if not migrou_algo:
        print("  Flags de migração disponíveis:")
        for flag in _INDICES_PADRAO:
            print(f"    {flag} [caminho]  [--dry-run]")
        print()

    if "--teste-lock" in sys.argv:
        print("Testando lock...")
        c1 = master.consumir_carimbo()
        c2 = master.consumir_carimbo()
        c3 = master.consumir_carimbo()
        print(f"  Carimbos gerados: {c1}, {c2}, {c3}")
        print("  Lock OK")
