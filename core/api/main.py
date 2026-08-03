#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API OCR Dados Financeiros
=========================

FastAPI + APScheduler.

Roda automaticamente uma vez por dia, lê a pasta da data corrente,
executa o OCR e envia os dados em JSON ao endpoint do Consen.

Rotas:
    GET  /status                → healthcheck + próximo agendamento
    POST /processar/{ddmmaaaa}  → disparo manual para uma data específica
    GET  /log/{ddmmaaaa}        → resultado do processamento de uma data

Configuração (variáveis de ambiente ou config.py):
    PASTA_BASE   — pasta raiz onde ficam as subpastas diárias (ddmmaaaa)
    CONSEN_URL   — URL do endpoint do Consen (a definir)
    CONSEN_TOKEN — token de autenticação do Consen (a definir)
    HORA_JOB     — hora do disparo diário (padrão: 06:00)
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, date
from pathlib import Path

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

# ── garante que o módulo ocr seja encontrado ──────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))
from ocr.ocr_dados_financeiros import extrair_dados

# ── Configuração ──────────────────────────────────────────────────────────────

PASTA_BASE   = Path(os.getenv("PASTA_BASE",   r"\\10.10.250.21\Energia\CONTROLE BB\DIGITADOS\CARIMBOS DIGITADOS"))
CONSEN_URL   = os.getenv("CONSEN_URL",   "https://api.consen.com.br/faturas/itens")   # TODO: confirmar
CONSEN_TOKEN = os.getenv("CONSEN_TOKEN", "")                                           # TODO: preencher
HORA_JOB     = os.getenv("HORA_JOB",    "06:00")

_hora, _minuto = HORA_JOB.split(":")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("api_ocr")

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="OCR Dados Financeiros",
    description="Extrai itens de fatura de contas de energia e envia ao Consen.",
    version="1.0.0",
)

scheduler = AsyncIOScheduler(timezone="America/Sao_Paulo")


# ── Lógica de processamento ───────────────────────────────────────────────────

def _pasta_do_dia(data: date) -> Path:
    return PASTA_BASE / data.strftime("%d%m%Y")


def _arquivo_log(pasta: Path) -> Path:
    return pasta / "_ocr_log.json"


def _ler_log(pasta: Path) -> dict:
    arq = _arquivo_log(pasta)
    if arq.exists():
        return json.loads(arq.read_text(encoding="utf-8"))
    return {}


def _gravar_log(pasta: Path, log_data: dict) -> None:
    _arquivo_log(pasta).write_text(
        json.dumps(log_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _montar_payload(dados: list[dict]) -> list[dict]:
    """
    Monta o array de objetos que será enviado ao Consen.

    Formato atual (ajustar quando chegar a documentação):
    [
      {
        "carimbo": "BB_2004345",
        "concessionaria": "CEMIG",
        "itens": [
          { "descricao": "Energia Elétrica",    "valor": 112.56  },
          { "descricao": "Energia SCEE ISENTA", "valor": 1238.91 },
          ...
        ]
      },
      ...
    ]
    """
    payload = []
    for d in dados:
        if d.get("erro"):
            continue
        payload.append({
            "carimbo":       d["carimbo"],
            "concessionaria": d["concessionaria"],
            "itens": [
                {"descricao": descricao, "valor": valor}
                for descricao, valor in d["itens"]
            ],
        })
    return payload


async def _enviar_consen(payload: list[dict]) -> tuple[bool, str]:
    """
    Envia o payload ao Consen via POST.
    Retorna (sucesso, mensagem).

    TODO: ajustar headers/formato conforme documentação do Consen.
    """
    if not CONSEN_URL or not CONSEN_TOKEN:
        return False, "CONSEN_URL ou CONSEN_TOKEN não configurados"

    headers = {
        "Authorization": f"Bearer {CONSEN_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(CONSEN_URL, json=payload, headers=headers)
            response.raise_for_status()
            return True, f"HTTP {response.status_code}"
    except httpx.HTTPStatusError as e:
        return False, f"HTTP {e.response.status_code}: {e.response.text[:200]}"
    except Exception as e:
        return False, str(e)


async def processar_dia(data: date) -> dict:
    """
    Pipeline completo para uma data:
      1. Lê a pasta do dia
      2. OCR em todos os PDFs ainda não processados
      3. Monta o payload e envia ao Consen
      4. Grava log de controle
    """
    pasta = _pasta_do_dia(data)
    log.info("=== Processando %s → %s ===", data.strftime("%d/%m/%Y"), pasta)

    if not pasta.exists():
        msg = f"Pasta não encontrada: {pasta}"
        log.error(msg)
        return {"status": "erro", "mensagem": msg, "data": data.isoformat()}

    log_atual = _ler_log(pasta)
    pdfs = sorted(pasta.glob("*.pdf"))

    if not pdfs:
        msg = "Nenhum PDF encontrado na pasta."
        log.warning(msg)
        return {"status": "vazio", "mensagem": msg, "data": data.isoformat()}

    # OCR — processa todos (ou os que ainda falharam anteriormente)
    dados: list[dict] = []
    for pdf in pdfs:
        carimbo = pdf.stem
        entrada_anterior = log_atual.get(carimbo, {})

        # Não reprocessa o que já foi enviado com sucesso
        if entrada_anterior.get("enviado") is True:
            log.info("  %-30s  já enviado, pulando.", carimbo)
            dados.append({**entrada_anterior["dados"], "carimbo": carimbo})
            continue

        log.info("  Extraindo %s ...", pdf.name)
        d = extrair_dados(pdf)
        dados.append(d)

        log_atual[carimbo] = {
            "dados": {
                "concessionaria": d["concessionaria"],
                "itens": d["itens"],
                "erro": d.get("erro", ""),
            },
            "enviado": False,
            "enviado_em": None,
            "consen_resposta": None,
        }

    # Monta payload e envia
    payload = _montar_payload(dados)
    total = len(payload)
    enviados = 0
    erros_envio = 0

    if payload:
        sucesso, resposta = await _enviar_consen(payload)
        agora = datetime.now().isoformat()

        if sucesso:
            log.info("  Consen: OK — %s faturas enviadas. (%s)", total, resposta)
            enviados = total
            for carimbo in [d["carimbo"] for d in payload]:
                if carimbo in log_atual:
                    log_atual[carimbo]["enviado"] = True
                    log_atual[carimbo]["enviado_em"] = agora
                    log_atual[carimbo]["consen_resposta"] = resposta
        else:
            log.error("  Consen: FALHA — %s", resposta)
            erros_envio = total
            for carimbo in [d["carimbo"] for d in payload]:
                if carimbo in log_atual:
                    log_atual[carimbo]["consen_resposta"] = resposta
    else:
        log.warning("  Nenhum item para enviar (todos com erro de OCR?).")

    _gravar_log(pasta, log_atual)

    resultado = {
        "status": "ok" if erros_envio == 0 else "parcial",
        "data": data.isoformat(),
        "total_pdfs": len(pdfs),
        "enviados": enviados,
        "erros_ocr": sum(1 for d in dados if d.get("erro")),
        "erros_envio": erros_envio,
    }
    log.info("  Resultado: %s", resultado)
    return resultado


# ── Job agendado ──────────────────────────────────────────────────────────────

async def _job_diario():
    await processar_dia(date.today())


# ── Rotas ─────────────────────────────────────────────────────────────────────

@app.get("/status", summary="Healthcheck e próximo agendamento")
async def status():
    job = scheduler.get_job("job_diario")
    proximo = job.next_run_time.isoformat() if job and job.next_run_time else None
    return {
        "status": "ok",
        "scheduler": "rodando" if scheduler.running else "parado",
        "proximo_processamento": proximo,
        "hora_configurada": HORA_JOB,
        "consen_url": CONSEN_URL or "(não configurado)",
    }


@app.post("/processar/{ddmmaaaa}", summary="Disparo manual para uma data específica")
async def processar_manual(ddmmaaaa: str):
    try:
        data = datetime.strptime(ddmmaaaa, "%d%m%Y").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Formato inválido. Use ddmmaaaa. Ex: 14042026")

    resultado = await processar_dia(data)
    return JSONResponse(content=resultado)


@app.get("/log/{ddmmaaaa}", summary="Log de processamento de uma data")
async def ver_log(ddmmaaaa: str):
    try:
        data = datetime.strptime(ddmmaaaa, "%d%m%Y").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Formato inválido. Use ddmmaaaa. Ex: 14042026")

    pasta = _pasta_do_dia(data)
    log_data = _ler_log(pasta)

    if not log_data:
        raise HTTPException(status_code=404, detail=f"Sem log para {ddmmaaaa}.")

    total = len(log_data)
    enviados = sum(1 for v in log_data.values() if v.get("enviado"))
    return {
        "data": data.isoformat(),
        "total": total,
        "enviados": enviados,
        "pendentes": total - enviados,
        "detalhes": log_data,
    }


# ── Startup / Shutdown ────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    scheduler.add_job(
        _job_diario,
        trigger=CronTrigger(hour=int(_hora), minute=int(_minuto), timezone="America/Sao_Paulo"),
        id="job_diario",
        replace_existing=True,
    )
    scheduler.start()
    log.info("Scheduler iniciado — job diário às %s", HORA_JOB)


@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown(wait=False)
    log.info("Scheduler encerrado.")
