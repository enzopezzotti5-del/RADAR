"""
extrair.py — Extração de texto de PDFs com cache SHA-256.

Estratégia:
1. Texto embutido via pdfplumber (rápido, sem custo computacional).
2. Se texto insuficiente e tesseract disponível: OCR (marcado como ocr).
3. Cache local por SHA-256 do arquivo em runtime/classificador/cache_texto/.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

_CACHE_DIR = Path(__file__).resolve().parents[2] / "runtime" / "classificador" / "cache_texto"
_MIN_CHARS = 80  # mínimo de caracteres para considerar texto suficiente


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for bloco in iter(lambda: f.read(65536), b""):
            h.update(bloco)
    return h.hexdigest()


def _cache_get(sha: str) -> dict | None:
    arq = _CACHE_DIR / f"{sha}.json"
    if arq.exists():
        try:
            return json.loads(arq.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _cache_set(sha: str, dados: dict) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    arq = _CACHE_DIR / f"{sha}.json"
    arq.write_text(json.dumps(dados, ensure_ascii=False, indent=None), encoding="utf-8")


def extrair_texto(pdf_path: Path, max_paginas: int = 3) -> dict:
    """
    Extrai texto de *pdf_path* e retorna:
        {
          "texto": str,
          "metodo": "pdf_text" | "ocr" | "sem_texto",
          "paginas_lidas": int,
          "sha256": str,
          "erro": str | None,
        }
    Usa cache local para evitar reprocessamento.
    """
    if not pdf_path.exists():
        return {
            "texto": "",
            "metodo": "pdf_nao_localizado",
            "paginas_lidas": 0,
            "sha256": "",
            "erro": "arquivo não encontrado",
        }

    try:
        sha = _sha256(pdf_path)
    except Exception as e:
        return {"texto": "", "metodo": "erro_leitura", "paginas_lidas": 0, "sha256": "", "erro": str(e)}

    cached = _cache_get(sha)
    if cached is not None:
        return cached

    resultado = _extrair_pdfplumber(pdf_path, sha, max_paginas)
    _cache_set(sha, resultado)
    return resultado


def _extrair_pdfplumber(pdf_path: Path, sha: str, max_paginas: int) -> dict:
    try:
        import pdfplumber
    except ImportError:
        return {"texto": "", "metodo": "sem_pdfplumber", "paginas_lidas": 0, "sha256": sha, "erro": "pdfplumber não instalado"}

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            partes = []
            lidas = 0
            for pg in pdf.pages[:max_paginas]:
                t = pg.extract_text() or ""
                partes.append(t)
                lidas += 1
            texto = "\n".join(partes)
    except Exception as e:
        return {"texto": "", "metodo": "erro_pdf", "paginas_lidas": 0, "sha256": sha, "erro": str(e)}

    if len(texto.strip()) >= _MIN_CHARS:
        return {"texto": texto, "metodo": "pdf_text", "paginas_lidas": lidas, "sha256": sha, "erro": None}

    # Texto insuficiente — tentar OCR
    return _tentar_ocr(pdf_path, sha, texto, lidas)


def _tentar_ocr(pdf_path: Path, sha: str, texto_parcial: str, lidas: int) -> dict:
    try:
        import subprocess
        r = subprocess.run(["tesseract", "--version"], capture_output=True, timeout=5)
        if r.returncode != 0:
            raise FileNotFoundError
    except (FileNotFoundError, Exception):
        metodo = "sem_texto" if len(texto_parcial.strip()) < 20 else "pdf_text_parcial"
        return {"texto": texto_parcial, "metodo": metodo, "paginas_lidas": lidas, "sha256": sha, "erro": "tesseract indisponível"}

    try:
        import tempfile
        import subprocess
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        r = subprocess.run(
            ["tesseract", str(pdf_path), str(tmp_path.with_suffix("")), "-l", "por", "--psm", "1"],
            capture_output=True, timeout=120,
        )
        texto_ocr = tmp_path.read_text(encoding="utf-8", errors="replace") if tmp_path.exists() else ""
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass

        if len(texto_ocr.strip()) >= _MIN_CHARS:
            return {"texto": texto_ocr, "metodo": "ocr", "paginas_lidas": lidas, "sha256": sha, "erro": None}

        return {"texto": texto_parcial or texto_ocr, "metodo": "texto_insuficiente", "paginas_lidas": lidas, "sha256": sha, "erro": "texto insuficiente após OCR"}
    except Exception as e:
        return {"texto": texto_parcial, "metodo": "erro_ocr", "paginas_lidas": lidas, "sha256": sha, "erro": str(e)}
