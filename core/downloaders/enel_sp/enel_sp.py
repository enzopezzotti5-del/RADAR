#!/usr/bin/env python3
"""
Bot Enel SP - Versão DEFINITIVA FINAL (V6) + BT/MT por mês + downloads_temp_enel
---------------------------------------------------------------------------------
- Mantém o fluxo que já funcionava
- Separa PDFs em DOWNLOAD ENEL/<MES_REF>/<BT|MT|NAO_IDENTIFICADA>/
- Usa pasta temporária local downloads_temp_enel ao lado do script
- Índice local registra CLASSIFICACAO e ARQUIVO
- Mantém integração com indice_master.py sem alterar o formato dele
"""

import sys
import ctypes as _ctypes
# Isola este processo do CTRL_C_EVENT do Windows (evita KeyboardInterrupt em SSL/sockets)
if sys.platform == "win32":
    try:
        _ctypes.windll.kernel32.SetConsoleCtrlHandler(None, True)
    except Exception:
        pass
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import requests
import base64
import os
import uuid
import csv
import time
import shutil
import re
import json
from pathlib import Path
from datetime import datetime
from typing import Dict
from collections import defaultdict

import pdfplumber

# ── Índice master unificado ────────────────────────────────────────────────────
import importlib.util as _ilu

_MASTER_SERVER = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/indice_master.py")
_MASTER_LOCAL  = Path(__file__).resolve().parents[2] / "indice_master.py"

_master_mod_path = next(
    (p for p in [_MASTER_LOCAL, _MASTER_SERVER] if p.exists()),
    None
)

if _master_mod_path:
    print(f"[master] Carregando: {_master_mod_path}")
    _spec = _ilu.spec_from_file_location("indice_master", str(_master_mod_path))
    _mod  = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    MasterIndice    = _mod.MasterIndice
    MASTER_FILE     = _mod.MASTER_FILE
    _normalizar_ref = getattr(_mod, "normalizar_mes_ref", lambda r: r)
    _chave_dedup    = getattr(
        _mod,
        "chave_dedup",
        lambda uc, ref, sistema=None: f"{str(uc).lstrip('0') or '0'}|{ref}" if not sistema else f"{str(uc).lstrip('0') or '0'}|{ref}|{sistema}",
    )
    _FILELOCK_OK    = getattr(_mod, "_FILELOCK_OK", False)
    _usar_master    = True
    if not _FILELOCK_OK:
        print("[master] AVISO: filelock não instalado — pip install filelock")
else:
    MasterIndice    = None
    MASTER_FILE     = None
    _normalizar_ref = lambda r: r
    _chave_dedup    = lambda uc, ref, *_: f"{uc.lstrip('0') or '0'}|{ref}"
    _usar_master    = False
    print("[master] indice_master.py não encontrado — modo local")


try:
    from core.metrics.radar_metrics import emit_outcome as _emit_outcome
    def _emit_metric(outcome: str, *, uc: str, ref: str, belnr: str) -> None:
        _emit_outcome(outcome, utility="ENEL SP", account_id=uc, competence=ref, invoice_id=belnr)
except Exception:
    def _emit_metric(outcome: str, *, uc: str, ref: str, belnr: str) -> None:  # type: ignore[misc]
        pass


class EnelDownloaderArquivista:
    def __init__(self, email: str, password: str, api_key: str, cookies: str, user_agent: str, root_dir: str):
        self.email = email
        self.password = password
        self.api_key = api_key
        self.acn_guid = f"{uuid.uuid4()}:{uuid.uuid4()}"
        self.base_url = "https://portalhome.eneldistribuicaosp.com.br"

        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.temp_dir = os.path.join(self.script_dir, "downloads_temp_enel")
        os.makedirs(self.temp_dir, exist_ok=True)

        self.root_dir = root_dir
        root_norm = self.root_dir.replace("\\", "/").upper()
        if "/DOWNLOAD ENEL" in root_norm:
            self.output_base_dir = self.root_dir
        else:
            self.output_base_dir = os.path.join(self.root_dir, "DOWNLOAD ENEL")
        self.output_base_dir = os.path.normpath(self.output_base_dir)
        self.index_base_dir = self._resolver_base_indice_enel(self.root_dir)

        os.makedirs(self.output_base_dir, exist_ok=True)
        os.makedirs(self.index_base_dir, exist_ok=True)

        self.index_file = os.path.join(self.index_base_dir, "indice_faturas.csv")
        self.index_sources = self._listar_indices_fontes()
        self.log_duplicadas_file = os.path.join(self.output_base_dir, "log_ucs_puladas_duplicadas.csv")

        self._master = MasterIndice(MASTER_FILE) if _usar_master else None

        self.cache_habilitado = False
        self.cache_file = None
        self.ucs_verificadas_cache = set()

        self.indice_fatura = 2000000
        self.memoria_download = set()      # chaves normalizadas "uc_sem_zeros|MM-YYYY"
        self.faturas_baixadas = set()      # IDs da execução atual
        self.faturas_do_indice = set()     # IDs históricos do índice
        self._belnrs_historicos = set()    # BELNRs históricos para proteção cross-execução
        self.meses_por_uc = defaultdict(set)

        self.qtd_baixadas_hoje = 0
        self.qtd_duplicadas = 0
        self.qtd_puladas_cache = 0
        self.duplicadas_consecutivas = 0
        self.qtd_ucs_nao_encontradas = 0
        self.status_busca_por_uc = {}

        self._carregar_indice()

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=utf-8",
            "X-ACN-GUID": self.acn_guid,
            "Cookie": cookies,
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/"
        })

        self.tokens = {"id": None, "sap_fbid": None}
        self.sso_guid = None
        self.lista_sap = []

    def _novo_acn_guid(self) -> str:
        return f"{uuid.uuid4()}:{uuid.uuid4()}"

    def _seed_portal_cookies(self):
        try:
            self.session.get(
                f"{self.base_url}/",
                timeout=25.0,
                headers={
                    "User-Agent": self.session.headers.get("User-Agent", ""),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                },
            )
        except BaseException as e:
            self.log(f"Falha ao semear cookies do portal: {e}", "WARNING")

    def _resolver_base_indice_enel(self, root_dir: str) -> str:
        root_txt = str(root_dir or "").strip()
        if not root_txt:
            return os.path.normpath("DOWNLOAD ENEL")

        root_norm = root_txt.replace("\\", "/")
        root_upper = root_norm.upper()
        marcador = "/DOWNLOAD ENEL"
        pos = root_upper.find(marcador)
        if pos >= 0:
            fim = pos + len(marcador)
            return os.path.normpath(root_norm[:fim].replace("/", os.sep))

        return os.path.normpath(os.path.join(root_txt, "DOWNLOAD ENEL"))

    def _listar_indices_fontes(self) -> list[str]:
        candidatos = [
            os.path.join(self.index_base_dir, "indice_faturas.csv"),
            os.path.join(self.output_base_dir, "indice_faturas.csv"),
        ]
        vistos: set[str] = set()
        fontes: list[str] = []
        for caminho in candidatos:
            chave = os.path.normcase(os.path.normpath(caminho))
            if chave in vistos:
                continue
            vistos.add(chave)
            fontes.append(os.path.normpath(caminho))
        return fontes

    def _ref_parece_mes(self, valor: str) -> bool:
        return bool(re.fullmatch(r"\d{2}-\d{4}", str(valor or "").strip()))

    def _parse_index_row(self, row: dict) -> tuple[str, str, str, str]:
        indice_str = str(row.get("INDICE") or "").strip()
        uc = str(row.get("UC") or "").strip()
        ref = str(row.get("MES_REF") or "").strip()
        fatura_id = str(row.get("FATURA_ID") or "").strip()
        data_download = str(row.get("DATA_DOWNLOAD") or "").strip()
        extras = [str(valor or "").strip() for valor in (row.get(None) or [])]

        # Corrige linhas gravadas com 7 colunas sob cabeçalho legado de 5 colunas:
        # INDICE,UC,MES_REF,FATURA_ID,DATA_DOWNLOAD
        # vira
        # INDICE,UC,CLASSIFICACAO,MES_REF,FATURA_ID,DATA_DOWNLOAD,ARQUIVO
        if not self._ref_parece_mes(ref) and self._ref_parece_mes(fatura_id):
            ref = fatura_id
            fatura_id = data_download

        return indice_str, uc, ref, fatura_id

    def _detectar_layout_indice(self, caminho: str) -> str:
        if not os.path.exists(caminho):
            return "full"

        try:
            with open(caminho, mode="r", encoding="utf-8-sig", newline="") as f:
                header = next(csv.reader(f), [])
        except Exception:
            return "full"

        header_norm = [str(col or "").strip().upper() for col in header]
        if header_norm[:5] == ["INDICE", "UC", "MES_REF", "FATURA_ID", "DATA_DOWNLOAD"] and "CLASSIFICACAO" not in header_norm:
            return "compact"
        return "full"

    def _normalizar_uc_busca(self, valor: str) -> str:
        texto = str(valor or "").strip().upper()
        if not texto:
            return ""
        return texto.lstrip("0") or "0" if texto.isdigit() else texto

    def _encontrar_instalacao(self, anlage: str) -> Dict | None:
        alvo_bruto = str(anlage or "").strip().upper()
        alvo_norm = self._normalizar_uc_busca(alvo_bruto)

        for inst in self.lista_sap:
            inst_anlage = str(inst.get("ANLAGE", "")).strip().upper()
            if inst_anlage == alvo_bruto:
                return inst

        for inst in self.lista_sap:
            inst_anlage = str(inst.get("ANLAGE", "")).strip().upper()
            if self._normalizar_uc_busca(inst_anlage) == alvo_norm:
                return inst

        return None

    def _serializar_resposta(self, payload: Dict | None) -> str:
        if not payload:
            return ""
        try:
            return json.dumps(payload, ensure_ascii=False, sort_keys=True).upper()
        except Exception:
            return str(payload).upper()

    def _resposta_indica_fluxo_ativacao(self, payload: Dict | None) -> bool:
        texto = self._serializar_resposta(payload)
        if not texto:
            return False
        marcadores = (
            "ATIVAR CADASTRO",
            "ATIVE SEU CADASTRO",
            "ATIVE SUA CONTA",
            "NOVA SENHA",
            "CONFIRME A SENHA",
            "SENHA RECEBIDA POR SMS",
            "SENHA RECEBIDA POR E-MAIL",
            "TROCA DE SENHA",
        )
        return any(marcador in texto for marcador in marcadores)

    def _resposta_confirma_instalacao(self, payload: Dict | None, anlage: str) -> bool:
        texto = self._serializar_resposta(payload)
        if not texto:
            return False

        alvo = str(anlage or "").strip().upper()
        alvo_norm = self._normalizar_uc_busca(alvo)
        if not alvo and not alvo_norm:
            return False

        candidatos = {alvo}
        if alvo_norm:
            candidatos.add(alvo_norm)
            candidatos.add(alvo_norm.zfill(10))
        if alvo.isdigit():
            candidatos.add(alvo.lstrip("0") or "0")
            candidatos.add(alvo.zfill(10))

        return any(c and c in texto for c in candidatos)

    def _carregar_contexto_portal(self) -> Dict | None:
        cnpj_data = self._post(
            f"{self.base_url}/api/sap/getlogincorpselectcnpj",
            {
                "ET_CNPJ": "",
                "I_303_INSTALACAO": "0002684152",
                "I_303_RAIZ_CNPJ": "00000000",
                "I_ANLAGE": "",
                "I_BANDEIRA": "X",
                "I_CANAL": "NCOR",
                "I_COD_SERV": "GU",
                "I_FBIDTOKEN": self.tokens["sap_fbid"],
                "I_LISTA_INST": "X",
                "I_REF_TOKEN": "",
                "I_SERVICO": ""
            }
        )
        if cnpj_data.get("_error"):
            return None
        self.lista_sap = cnpj_data.get("ET_INST", []) or []
        self.sso_guid = cnpj_data.get("E_SSO_GUID")
        return cnpj_data

    def _trocar_contexto_instalacao(self, anlage_solicitada: str, inst: Dict) -> tuple[Dict | None, str | None]:
        anlage_portal = str(inst.get("ANLAGE", anlage_solicitada)).strip()
        change_resp = self._post(
            f"{self.base_url}/api/sap/changeinstallationcorp",
            {
                "I_ANLAGE": anlage_portal,
                "I_BANDEIRA": "X",
                "I_CANAL": "NCOR",
                "I_COD_SERV": "TC",
                "I_FBIDTOKEN": self.tokens["sap_fbid"],
                "I_LISTA_INST": "X",
                "I_REF_TOKEN": "",
                "I_SERVICO": "A"
            },
            delay=8.0,
            tentativas=2,
        )

        if change_resp.get("_error"):
            return None, "changeinstallationcorp"

        if self._resposta_indica_fluxo_ativacao(change_resp):
            return None, "ativacao_cadastro"

        time.sleep(5.0)
        contexto = self._carregar_contexto_portal()
        if contexto is None:
            return None, "refresh_contexto"

        inst_atualizada = self._encontrar_instalacao(anlage_portal) or self._encontrar_instalacao(anlage_solicitada)
        if inst_atualizada is None:
            if self._resposta_confirma_instalacao(change_resp, anlage_portal):
                inst_atualizada = dict(inst)
            else:
                return None, "instalacao_nao_confirmada"

        inst_atualizada = dict(inst_atualizada)
        protocolo_inicial = str(change_resp.get("E_PROTOCOLO") or "").strip()
        if protocolo_inicial:
            inst_atualizada["_PROTOCOLO_INICIAL"] = protocolo_inicial

        return inst_atualizada, None

    def _gerar_protocolo_segunda_via(
        self,
        conta: Dict,
    ) -> Dict | None:
        payload = {
            "I_CANAL": "NCOR",
            "I_COD_PROT": "08",
            "I_COD_SERV": "SV",
        }
        r_prot = self._post(
            f"{self.base_url}/api/sap/validatesegundavia",
            payload,
            tentativas=2,
        )
        if r_prot.get("E_PROTOCOLO"):
            return r_prot
        belnr = str(conta.get("BELNR") or "").strip()
        self.log(
            f"Falha ao gerar protocolo para BELNR {belnr}. Resposta final: {r_prot}",
            "ERROR",
        )
        return None

    def _conta_permite_segunda_via_web(self, conta: Dict) -> bool:
        valor = str(conta.get("O_PODE_GERAR_2_VIA_WEB", "") or "").strip().upper()
        return valor in {"X", "S", "SIM", "1", "TRUE"}

    def _protocolo_inicial_instalacao(self, inst_contexto: Dict) -> str:
        return str(inst_contexto.get("_PROTOCOLO_INICIAL", "") or "").strip()

    def _payload_generatepdf(
        self,
        conta: Dict,
        protocolo: str,
        inst_contexto: Dict | None = None,
        bundle_protocolo: Dict | None = None,
    ) -> Dict:
        payload = {
            "I_BELNR": str(conta.get("BELNR") or "").strip(),
            "I_CANAL": "NCOR",
            "I_COD_SERV": "SV",
            "I_CONTATO": str(protocolo or "").strip(),
            "I_MOTIVO_EXT_SITE": "05",
            "I_ORIGEM_DOC": conta.get("ORIGEM_DOC", "C"),
            "I_TOTEM_MOB": "",
        }
        return payload

    # ============================================================
    # LOG
    # ============================================================

    def log(self, msg: str, level: str = "INFO"):
        ts = datetime.now().strftime("%H:%M:%S")
        sym = {
            "INFO": "→",
            "SUCCESS": "✓",
            "ERROR": "✗",
            "WARNING": "⚠",
            "SKIP": "⏭",
            "PROG": "📊",
            "DUP": "🔁",
            "ALERT": "🚨",
            "FAST": "⚡"
        }.get(level, "•")
        print(f"[{ts}] {sym} {msg}")

    # ============================================================
    # CLASSIFICAÇÃO BT / MT
    # ============================================================

    def _extrair_texto_pdf(self, pdf_path: str, max_paginas: int = 2) -> str:
        partes = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages[:max_paginas]:
                    txt = page.extract_text() or ""
                    partes.append(txt)
        except Exception:
            return ""
        return "\n".join(partes).upper()

    def _classificar_pdf_enel(self, pdf_path: str) -> str:
        texto = self._extrair_texto_pdf(pdf_path, max_paginas=2)

        if not texto.strip():
            return "NAO_IDENTIFICADA"

        # Padrão primário: formato específico das faturas Enel SP
        # "B - B3 - CONVENCIONAL" → BT  |  "A - A4 - HORO-SAZONAL" → MT
        if re.search(r"\bB\s*-\s*B\d", texto):
            return "BT"
        if re.search(r"\bA\s*-\s*A[0-9S]", texto):
            return "MT"

        # Fallback: padrões genéricos (sem os ambíguos A3/A4/AS soltos)
        padroes_mt = [
            r"M[ÉE]DIA\s*TENS[ÃA]O",
            r"MEDIA TENSAO",
            r"SUBGRUPO\s*A\d",
            r"TARIFA\s*HORO",
        ]
        padroes_bt = [
            r"BAIXA\s*TENS[ÃA]O",
            r"SUBGRUPO\s*B\d",
            r"TARIFA\s*CONV",
        ]

        for padrao in padroes_mt:
            if re.search(padrao, texto, re.IGNORECASE):
                return "MT"

        for padrao in padroes_bt:
            if re.search(padrao, texto, re.IGNORECASE):
                return "BT"

        return "NAO_IDENTIFICADA"

    # ============================================================
    # ÍNDICE
    # ============================================================

    def _carregar_indice(self):
        if self._master is not None:
            for chave in self._master._ja_baixados:
                self.memoria_download.add(chave)
            self.indice_fatura = self._master._proximo_num
            self.log(
                f"Master carregado: {len(self._master._ja_baixados)} registros | "
                f"próximo: {self._master.proximo_carimbo}",
                "SUCCESS"
            )

        fontes_existentes = [caminho for caminho in self.index_sources if os.path.exists(caminho)]
        if fontes_existentes:
            self.log("Carregando índice CSV local...", "INFO")
            if len(fontes_existentes) > 1:
                self.log(
                    "Múltiplos índices detectados; unificando histórico do índice principal e de saídas legadas.",
                    "WARNING"
                )
            for caminho_indice in fontes_existentes:
                self.log(f"Fonte de índice: {caminho_indice}", "INFO")
                with open(caminho_indice, mode="r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        indice_str, uc, ref, fatura_id = self._parse_index_row(row)

                        if uc and ref:
                            self.memoria_download.add(_chave_dedup(uc, ref))          # formato antigo
                            self.memoria_download.add(_chave_dedup(uc, ref, "ENEL"))  # formato novo
                            self.meses_por_uc[uc].add(ref)

                        if fatura_id:
                            self.faturas_do_indice.add(fatura_id)

                        if indice_str.startswith("BB_"):
                            try:
                                num = int(indice_str.replace("BB_", ""))
                                if num >= self.indice_fatura:
                                    self.indice_fatura = num + 1
                            except ValueError:
                                pass

            # Proteção cross-execução: BELNRs históricos bloqueiam redownload
            # (mesmo entre execuções distintas, a menos que ignorar_indice=True)
            self._belnrs_historicos = set(self.faturas_do_indice)

            # Quando master está indisponível, sincroniza indice_fatura com
            # next.txt para evitar colisão com carimbos atribuídos pelo watcher.
            if self._master is None:
                _next_txt = Path(r"\\10.10.250.21\Energia\ARQUIVOS ENZO\indice_master_next.txt")
                try:
                    _n = int(_next_txt.read_text(encoding="utf-8").strip())
                    if _n > self.indice_fatura:
                        self.indice_fatura = _n
                except Exception:
                    pass

            proximo_display = (
                self._master.proximo_carimbo
                if self._master is not None
                else f"BB_{self.indice_fatura}"
            )
            self.log(f"Índice CSV: {len(self.faturas_do_indice)} faturas | "
                     f"próximo carimbo: {proximo_display}", "SUCCESS")
        else:
            self.log("Índice CSV não encontrado. Criando novo...", "INFO")
            with open(self.index_file, mode="w", encoding="utf-8-sig", newline="") as f:
                csv.writer(f).writerow([
                    "INDICE",
                    "UC",
                    "CLASSIFICACAO",
                    "MES_REF",
                    "FATURA_ID",
                    "DATA_DOWNLOAD",
                    "ARQUIVO",
                ])

        if not os.path.exists(self.log_duplicadas_file):
            with open(self.log_duplicadas_file, mode="w", encoding="utf-8-sig", newline="") as f:
                csv.writer(f).writerow(["UC", "MES_REF", "FATURA_ID", "DATA_PULO", "MOTIVO"])

    def _registrar_no_indice(
        self,
        indice_bb: str,
        uc: str,
        classificacao: str,
        ref: str,
        fatura_id: str,
        arquivo: str = ""
    ):
        data_atual = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        layout = self._detectar_layout_indice(self.index_file)

        with open(self.index_file, mode="a", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            if layout == "compact":
                writer.writerow([
                    indice_bb,
                    uc,
                    ref,
                    fatura_id,
                    data_atual,
                ])
            else:
                writer.writerow([
                    indice_bb,
                    uc,
                    classificacao,
                    ref,
                    fatura_id,
                    data_atual,
                    arquivo
                ])

        if self._master is not None:
            self._master.registrar(
                indice_bb=indice_bb,
                sistema="ENEL",
                uc=uc,
                mes_ref=_normalizar_ref(ref),
                fatura_id=fatura_id,
                estado="SÃO PAULO",
                arquivo=arquivo,
                # CNPJ e instalacao não disponíveis no fluxo API da ENEL SP
            )

        self.memoria_download.add(_chave_dedup(uc, ref))          # formato antigo
        self.memoria_download.add(_chave_dedup(uc, ref, "ENEL"))  # formato novo
        self.meses_por_uc[uc].add(ref)
        self.faturas_baixadas.add(fatura_id)

    def _marcar_status_busca(self, uc: str, status: str, detalhe: str = ""):
        self.status_busca_por_uc[uc] = {
            "status": status,
            "detalhe": detalhe,
            "data": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        }

    def _salvar_relatorio_busca(self, alvo_ucs: list[str], refs_alvo: list[str], prefixo: str) -> str:
        pasta_relatorios = Path(self.output_base_dir) / "relatorios_enel_sp"
        pasta_relatorios.mkdir(parents=True, exist_ok=True)

        alvo_slug = "_".join(refs_alvo) if refs_alvo else "todos_2026"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        relatorio_path = pasta_relatorios / f"{prefixo}_{alvo_slug}_{ts}.csv"

        with relatorio_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["INSTALACAO", "STATUS", "DETALHE", "DATA"])
            for uc in alvo_ucs:
                item = self.status_busca_por_uc.get(uc, {})
                writer.writerow([
                    uc,
                    item.get("status", "SEM_OCORRENCIA"),
                    item.get("detalhe", ""),
                    item.get("data", ""),
                ])

        return str(relatorio_path)

    # ============================================================
    # HTTP
    # ============================================================

    def _post(
        self,
        url: str,
        payload: Dict,
        delay: float = 2.5,
        tentativas: int = 1,
        timeout: float = 25.0,
    ) -> Dict:
        ultima_resposta = {"_error": True}
        total_tentativas = max(1, int(tentativas or 1))

        for tentativa in range(1, total_tentativas + 1):
            time.sleep(delay if tentativa == 1 else min(3.0, 1.5 * tentativa))
            try:
                self.acn_guid = self._novo_acn_guid()
                self.session.headers.update({"X-ACN-GUID": self.acn_guid})
                resp = self.session.post(url, json=payload, timeout=timeout)
                novo_token = resp.headers.get("Authorization") or resp.headers.get("authorization")
                if novo_token:
                    auth_val = novo_token if "Bearer" in novo_token else f"Bearer {novo_token}"
                    self.session.headers.update({"Authorization": auth_val})
                if resp.status_code >= 400:
                    print(f"[_post] HTTP {resp.status_code} — url={url.split('/')[-1]} — body={resp.text[:300]}")
                return resp.json()
            except BaseException as e:
                print(f"[_post] {type(e).__name__}: {e} — url={url.split('/')[-1]} — tentativa {tentativa}/{total_tentativas}")
                ultima_resposta = {"_error": True}
                if tentativa < total_tentativas:
                    self.log(
                        f"Falha transitória em {url.split('/')[-1]}; repetindo ({tentativa}/{total_tentativas})...",
                        "WARNING",
                    )

        return ultima_resposta

    # ============================================================
    # AUTH
    # ============================================================

    def autenticar(self) -> bool:
        self.log("Iniciando Autenticação...")
        self._seed_portal_cookies()

        res = self._post(
            f"{self.base_url}/api/firebase/login",
            {"I_EMAIL": self.email, "I_PASSWORD": self.password, "I_CANAL": "NCOR"}
        )
        if res.get("_error"):
            return False

        token_firebase = res.get("token")
        if not token_firebase:
            self.log("Login Enel não retornou token Firebase.", "ERROR")
            return False
        try:
            goog = self.session.post(
                f"https://www.googleapis.com/identitytoolkit/v3/relyingparty/verifyCustomToken?key={self.api_key}",
                json={"token": token_firebase, "returnSecureToken": True},
                timeout=25.0,
            ).json()
        except BaseException as e:
            self.log(f"Falha na validação Google Identity: {e}", "ERROR")
            return False

        self.tokens["id"] = goog.get("idToken")
        self.session.headers.update({"Authorization": f"Bearer {self.tokens['id']}"})

        sap = self._post(
            f"{self.base_url}/api/sap/getlogincorp",
            {
                "I_CANAL": "NCOR",
                "I_COD_SERV": "GU",
                "I_ANLAGE": "",
                "I_LISTA_INST": "X",
                "I_BANDEIRA": "X",
                "I_FBIDTOKEN": self.tokens["id"],
                "I_REF_TOKEN": "",
                "I_SERVICO": ""
            }
        )

        self.tokens["sap_fbid"] = sap.get("body", {}).get("I_FBIDTOKEN") or self.tokens["id"]
        self.log("Sessão SAP autorizada.", "SUCCESS")
        return True

    # ============================================================
    # TEMP FILES
    # ============================================================

    def _limpar_temp_enel(self):
        try:
            for arquivo in os.listdir(self.temp_dir):
                path = os.path.join(self.temp_dir, arquivo)
                if os.path.isfile(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass
        except Exception:
            pass

    def _gerar_temp_pdf_path(self, carimbo: str) -> str:
        return os.path.join(self.temp_dir, f"{carimbo}.pdf")

    def _registrar_duplicada_execucao(self, uc: str, ref: str, fatura_id: str):
        self.log(f"DUPLICADA! Fatura ID {fatura_id} já baixada nesta execução. Pulando...", "DUP")
        self.qtd_duplicadas += 1
        self.duplicadas_consecutivas += 1

        with open(self.log_duplicadas_file, mode="a", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerow([
                uc,
                ref,
                fatura_id,
                datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                "Fatura já baixada em outra UC",
            ])

        if self.duplicadas_consecutivas >= 3:
            self.log(
                f"ALERTA: {self.duplicadas_consecutivas} duplicadas consecutivas. Aguardando 10s...",
                "ALERT",
            )
            time.sleep(10)
            self.duplicadas_consecutivas = 0

    def _recarregar_protecao_indices(self):
        faturas_execucao = set(self.faturas_baixadas)

        self.indice_fatura = 2000000
        self.memoria_download = set()
        self.faturas_do_indice = set()
        self._belnrs_historicos = set()
        self.meses_por_uc = defaultdict(set)
        self._master = MasterIndice(MASTER_FILE) if _usar_master else None

        self._carregar_indice()
        self.faturas_baixadas.update(faturas_execucao)

    def _fatura_deve_ser_pulada(
        self,
        uc_original: str,
        uc_portal: str,
        ref: str,
        belnr: str,
        ignorar_indice: bool = False,
    ) -> bool:
        chave_ref = _chave_dedup(uc_portal, ref, "ENEL")
        if chave_ref in self.memoria_download and not ignorar_indice:
            self._marcar_status_busca(uc_original, "JA_NO_INDICE", ref)
            self.log(f"Fatura {ref} já consta no índice.", "SKIP")
            _emit_metric("skipped_existing", uc=uc_portal, ref=ref, belnr=belnr)
            return True

        if chave_ref in self.memoria_download and ignorar_indice:
            self.log(f"Fatura {ref} já consta no índice, mas será redownload forçado.", "WARNING")

        if belnr in self._belnrs_historicos and not ignorar_indice:
            self._marcar_status_busca(uc_original, "JA_NO_INDICE", ref)
            self.log(f"BELNR {belnr} já registrado no índice (histórico). Pulando.", "SKIP")
            _emit_metric("skipped_existing", uc=uc_portal, ref=ref, belnr=belnr)
            return True

        if belnr in self.faturas_baixadas:
            # Duplicata intra-run: downloaded já foi emitido para este belnr neste run.
            # Não emitir nada para não sobrescrever o outcome confirmado.
            self._registrar_duplicada_execucao(uc_original, ref, belnr)
            return True

        return False

    def _persistir_pdf_baixado(self, tmp_path: str, uc_portal: str, ref: str, belnr: str) -> tuple[str, str, str]:
        classificacao = self._classificar_pdf_enel(tmp_path)

        mes_dir = os.path.join(self.output_base_dir, ref, classificacao)
        Path(mes_dir).mkdir(parents=True, exist_ok=True)

        if self._master is not None:
            carimbo = self._master.consumir_carimbo()
            self.indice_fatura = self._master._proximo_num
        else:
            carimbo = f"BB_{self.indice_fatura}"
            self.indice_fatura += 1

        filename = f"{carimbo}.pdf"
        filepath = os.path.join(mes_dir, filename)
        shutil.move(tmp_path, filepath)

        self._registrar_no_indice(
            carimbo,
            uc_portal,
            classificacao,
            ref,
            belnr,
            filepath,
        )

        self.qtd_baixadas_hoje += 1
        self.duplicadas_consecutivas = 0
        _emit_metric("downloaded", uc=uc_portal, ref=ref, belnr=belnr)
        return carimbo, filepath, classificacao

    def _baixar_fatura_via_navegador_seguro(
        self,
        uc_original: str,
        uc_portal: str,
        ref: str,
        belnr: str,
        *,
        ignorar_indice: bool = False,
        headless: bool = False,
    ) -> str:
        from core.downloaders.enel_sp.enel_sp_browser import baixar_fatura_enel_sp_via_navegador

        if not ignorar_indice:
            self._recarregar_protecao_indices()

        if self._fatura_deve_ser_pulada(uc_original, uc_portal, ref, belnr, ignorar_indice=ignorar_indice):
            return "skip"

        runtime_base = Path(self.temp_dir) / "selenium_enel_sp" / f"{uc_portal}_{ref.replace('-', '_')}_{uuid.uuid4().hex[:8]}"
        download_dir = runtime_base / "downloads"
        debug_dir = runtime_base / "debug"

        try:
            pdf_path = baixar_fatura_enel_sp_via_navegador(
                email=self.email,
                senha=self.password,
                uc=uc_portal,
                mes_ref=ref,
                download_dir=download_dir,
                debug_dir=debug_dir,
                headless=headless,
            )
        except Exception as e:
            self._marcar_status_busca(uc_original, "FALHA_DOWNLOAD", f"{ref}: selenium:{e}")
            self.log(f"Falha no fluxo Selenium para {uc_portal}/{ref}: {e}", "ERROR")
            return "error"

        if not ignorar_indice:
            self._recarregar_protecao_indices()
            if self._fatura_deve_ser_pulada(uc_original, uc_portal, ref, belnr, ignorar_indice=ignorar_indice):
                try:
                    if os.path.exists(pdf_path):
                        os.remove(pdf_path)
                except Exception:
                    pass
                return "skip"

        try:
            carimbo, filepath, classificacao = self._persistir_pdf_baixado(str(pdf_path), uc_portal, ref, belnr)
        except Exception as e:
            self._marcar_status_busca(uc_original, "FALHA_DOWNLOAD", f"{ref}: selenium-save:{e}")
            self.log(f"Erro ao salvar/classificar PDF do Selenium: {e}", "ERROR")
            try:
                if os.path.exists(pdf_path):
                    os.remove(pdf_path)
            except Exception:
                pass
            return "error"

        self.log(f"Salvo em: {ref}/{classificacao}/{Path(filepath).name}", "SUCCESS")
        self._marcar_status_busca(uc_original, "BAIXADA", f"{ref}|{carimbo}|selenium")
        return "ok"

    # ============================================================
    # BAIXA LOTE
    # ============================================================

    def baixar_lote(
        self,
        csv_path: str,
        refs_alvo: list[str] | None = None,
        salvar_relatorio: bool = False,
        relatorio_prefixo: str = "relatorio_enel_sp_busca",
        ignorar_indice: bool = False,
        forcar_navegador: bool = False,
        navegador_headless: bool = False,
    ) -> bool:
        if not self.autenticar():
            self.log("Autenticacao falhou. Encerrando downloader com erro.", "ERROR")
            return False

        self._limpar_temp_enel()
        refs_alvo_set = set(refs_alvo or [])

        alvo_ucs = []
        with open(csv_path, mode="r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if uc := row.get("instalacao"):
                    alvo_ucs.append(uc.strip())

        cnpj_data = self._carregar_contexto_portal()
        if cnpj_data is None:
            self.log("Falha ao obter SSO/instalações.", "ERROR")
            return False

        total_ucs = len(alvo_ucs)
        start_time = time.time()
        proxima_marca = 10

        for idx, anlage in enumerate(alvo_ucs, 1):
            percentual = (idx / total_ucs) * 100 if total_ucs else 100
            if percentual >= proxima_marca:
                decorrido = time.time() - start_time
                tempo_por_uc = decorrido / idx if idx else 0
                restante = tempo_por_uc * (total_ucs - idx)
                minutos = int(restante // 60)
                self.log(
                    f"PROGRESSO: {int(percentual)}% ({idx}/{total_ucs}). "
                    f"Faltam ~{minutos}min. Nao encontradas: {self.qtd_ucs_nao_encontradas}",
                    "PROG"
                )
                proxima_marca += 10

            inst = self._encontrar_instalacao(anlage)
            if not inst:
                self.qtd_ucs_nao_encontradas += 1
                self._marcar_status_busca(anlage, "NAO_ENCONTRADA_PORTAL")
                self.log(f"UC nao encontrada no retorno da Enel: {anlage}", "WARNING")
                continue

            anlage_portal = str(inst.get("ANLAGE", anlage)).strip()

            print("-" * 60)
            if anlage_portal != str(anlage).strip():
                self.log(f"Processando UC: {anlage} -> portal {anlage_portal}")
            else:
                self.log(f"Processando UC: {anlage}")

            inst_contexto, motivo_troca = self._trocar_contexto_instalacao(anlage, inst)
            if inst_contexto is None:
                status = "FALHA_ACESSO"
                if motivo_troca == "ativacao_cadastro":
                    status = "ATIVACAO_CADASTRO"
                self._marcar_status_busca(anlage, status, motivo_troca or "changeinstallationcorp")
                self.log("Erro ao trocar instalação. Pulando UC...", "WARNING")
                continue

            self.log(f"Instalação trocada para {anlage_portal}", "INFO")

            fats_resp = self._post(
                f"{self.base_url}/api/sap/getfaturascorp",
                {
                    "I_ANO": "2026",
                    "I_CANAL": "NCOR",
                    "I_COD_SERV": "TC",
                    "I_SSO_GUID": self.sso_guid,
                    "I_PARTNER": inst_contexto.get("PARTNER"),
                    "I_VERTRAG": inst_contexto.get("VERTRAG"),
                    "I_VKONT": inst_contexto.get("VKONT"),
                    "I_DEB_ABERTOS": ""
                },
                delay=3.0,
                tentativas=2,
            )

            if fats_resp.get("_error"):
                self._marcar_status_busca(anlage, "FALHA_ACESSO", "getfaturascorp")
                self.log(f"Falha ao consultar faturas da UC {anlage}.", "ERROR")
                continue

            contas = fats_resp.get("ET_CONTAS", [])
            if not contas:
                detalhe = f"refs_alvo={','.join(sorted(refs_alvo_set))}" if refs_alvo_set else "sem_contas_2026"
                self._marcar_status_busca(anlage, "SEM_FATURA_ALVO", detalhe)
                self.log(f"Nenhuma conta de 2026 para {anlage}.", "INFO")
                continue

            refs_disponiveis = sorted({
                str(c.get("ANO_MES_REF", "SEM_REF")).replace("/", "-")
                for c in contas
            })

            if refs_alvo_set:
                contas = [
                    c for c in contas
                    if str(c.get("ANO_MES_REF", "SEM_REF")).replace("/", "-") in refs_alvo_set
                ]
                if not contas:
                    detalhe = "refs_disponiveis=" + (",".join(refs_disponiveis) if refs_disponiveis else "nenhuma")
                    self._marcar_status_busca(anlage, "SEM_FATURA_ALVO", detalhe)
                    self.log(
                        f"UC {anlage} sem fatura alvo ({', '.join(sorted(refs_alvo_set))}). "
                        f"Disponiveis: {', '.join(refs_disponiveis) if refs_disponiveis else 'nenhuma'}",
                        "WARNING"
                    )
                    continue

            for c in contas:
                belnr = str(c.get("BELNR"))
                situacao = str(c.get("SITUACAO", "")).strip().lower()
                ref = c.get("ANO_MES_REF", "SEM_REF").replace("/", "-")

                # Baixa todas as faturas de 2026, independente do status
                self.log(f"Fatura {ref} — status: '{situacao.capitalize()}'", "INFO")

                bundle_protocolo = self._gerar_protocolo_segunda_via(c)
                protocolo = str(bundle_protocolo.get("E_PROTOCOLO") or "").strip() if bundle_protocolo else ""
                protocolo_inicial = self._protocolo_inicial_instalacao(inst_contexto)
                if not protocolo and protocolo_inicial:
                    self.log(
                        f"validatesegundavia falhou; tentando protocolo inicial da instalação "
                        f"({protocolo_inicial}) para BELNR {belnr}.",
                        "WARNING",
                    )
                    protocolo = protocolo_inicial
                    bundle_protocolo = None
                if not protocolo:
                    self._marcar_status_busca(status="FALHA_ACESSO", uc=anlage, detalhe=f"validatesegundavia:{ref}")
                    continue

                if self._fatura_deve_ser_pulada(anlage, anlage_portal, ref, belnr, ignorar_indice=ignorar_indice):
                    continue

                if forcar_navegador:
                    self.log(f"Baixando ({ref}) via Selenium: {belnr}...", "INFO")
                    status_navegador = self._baixar_fatura_via_navegador_seguro(
                        anlage,
                        anlage_portal,
                        ref,
                        belnr,
                        ignorar_indice=ignorar_indice,
                        headless=navegador_headless,
                    )
                    if status_navegador in {"ok", "skip"}:
                        continue
                    self.log(f"Fluxo Selenium falhou para {anlage_portal}/{ref}.", "ERROR")
                    continue

                self.log(f"Baixando ({ref}) [{situacao.upper()}]: {belnr}...")

                res_pdf = self._post(
                    f"{self.base_url}/api/sap/generatepdf",
                    self._payload_generatepdf(c, protocolo, inst_contexto, bundle_protocolo),
                    delay=3.5,
                    tentativas=2,
                )

                if res_pdf.get("_error"):
                    self.log(f"Erro na requisição PDF ({ref}): falha HTTP. Tentando Selenium...", "WARNING")
                    status_navegador = self._baixar_fatura_via_navegador_seguro(
                        anlage,
                        anlage_portal,
                        ref,
                        belnr,
                        ignorar_indice=ignorar_indice,
                        headless=navegador_headless,
                    )
                    if status_navegador == "ok":
                        continue
                    if status_navegador == "skip":
                        continue
                    self._marcar_status_busca(anlage, "FALHA_DOWNLOAD", ref)
                    continue

                bin64 = res_pdf.get("E_BIN_FAT")
                if not bin64:
                    self.log(
                        f"PDF não retornado ({ref}): {res_pdf.get('E_MSG') or res_pdf}. Tentando Selenium...",
                        "WARNING",
                    )
                    status_navegador = self._baixar_fatura_via_navegador_seguro(
                        anlage,
                        anlage_portal,
                        ref,
                        belnr,
                        ignorar_indice=ignorar_indice,
                        headless=navegador_headless,
                    )
                    if status_navegador == "ok":
                        continue
                    if status_navegador == "skip":
                        continue
                    self._marcar_status_busca(
                        anlage,
                        "FALHA_DOWNLOAD",
                        f"{ref}: {res_pdf.get('E_MSG') or 'PDF nao retornado'}"
                    )
                    continue

                try:
                    pdf_bytes = base64.b64decode(bin64)
                    _tmp_uuid = uuid.uuid4().hex
                    tmp_path = os.path.join(self.temp_dir, f"_tmp_{_tmp_uuid}.pdf")

                    with open(tmp_path, "wb") as f:
                        f.write(pdf_bytes)

                    carimbo, filepath, classificacao = self._persistir_pdf_baixado(
                        tmp_path,
                        anlage_portal,
                        ref,
                        belnr,
                    )
                    self.log(f"Salvo em: {ref}/{classificacao}/{Path(filepath).name}", "SUCCESS")
                    self._marcar_status_busca(anlage, "BAIXADA", f"{ref}|{carimbo}")

                except Exception as e:
                    self.log(f"Erro ao salvar/classificar PDF: {e}", "ERROR")
                    try:
                        if 'tmp_path' in dir() and os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except Exception:
                        pass

        print("=" * 60)
        self.log("EXECUÇÃO CONCLUÍDA!", "SUCCESS")
        print("=" * 60)
        self.log(f"Faturas baixadas: {self.qtd_baixadas_hoje}", "INFO")

        if self.qtd_duplicadas > 0:
            self.log(f"Duplicadas ignoradas: {self.qtd_duplicadas}", "WARNING")

        if self.qtd_ucs_nao_encontradas > 0:
            self.log(f"UCs nao encontradas no portal: {self.qtd_ucs_nao_encontradas}", "WARNING")

        if salvar_relatorio:
            relatorio_path = self._salvar_relatorio_busca(
                alvo_ucs=alvo_ucs,
                refs_alvo=sorted(refs_alvo_set),
                prefixo=relatorio_prefixo,
            )
            totais_por_status = defaultdict(int)
            for item in self.status_busca_por_uc.values():
                totais_por_status[item["status"]] += 1
            self.log(f"Relatorio salvo em: {relatorio_path}", "SUCCESS")
            if totais_por_status:
                resumo = " | ".join(f"{k}: {v}" for k, v in sorted(totais_por_status.items()))
                self.log(f"Resumo da busca: {resumo}", "INFO")

        ultimo = (
            f"BB_{self._master._proximo_num - 1}"
            if self._master is not None
            else f"BB_{self.indice_fatura - 1}"
        )
        self.log(f"Último carimbo: {ultimo}", "INFO")

        print()
        print("  ⚡ MODO: Execução Semanal")
        print("  • Cache DESABILITADO")
        print("  • Todas UCs verificadas a cada execução")
        print("  • Garante captura de faturas novas do mês")
        print("  • Índice protege contra duplicatas")
        print("  • Estrutura: DOWNLOAD ENEL/<MES>/<BT|MT|NAO_IDENTIFICADA>/")
        print("  • Temp local: downloads_temp_enel")
        print("=" * 60 + "\n")
        return True


def main() -> int:
    EMAIL = "bbenergia@acaoenge.com.br"
    PASS = "Acao*2024"
    KEY = "AIzaSyCMiS_wFzups9BdHwwc-x0TinW02rG1peg"

    _SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    ROOT_DIR = "//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD ENEL"
    CSV_FILE = os.path.join(_SCRIPT_DIR, "unidades_consumidoras.csv")

    COOKIES_NAVEGADOR = ""
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    bot = EnelDownloaderArquivista(
        EMAIL,
        PASS,
        KEY,
        COOKIES_NAVEGADOR,
        USER_AGENT,
        ROOT_DIR
    )

    if os.path.exists(CSV_FILE):
        ok = bot.baixar_lote(CSV_FILE)
        return 0 if ok else 1
    else:
        print(f"❌ Erro: Arquivo não encontrado: {CSV_FILE}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
