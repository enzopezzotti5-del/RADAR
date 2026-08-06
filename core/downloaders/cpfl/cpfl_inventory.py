"""Inventario canonico e indice de hashes para o downloader CPFL/RGE."""
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Iterable

RAW_FIELDS = ("TITULAR_ID", "TITULAR_TEXTO", "PERFIL", "UC", "STATUS", "LINHA", "DATA_SCAN")
CANONICAL_FIELDS = ("CANONICAL_KEY",) + RAW_FIELDS
ACTIVE_STATUS = "ATIVA"


def normalize_uc(value: object) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits:
        return ""
    return digits.lstrip("0") or "0"


def _scan_time(value: object) -> datetime:
    try:
        return datetime.strptime(str(value or "").strip(), "%d/%m/%Y %H:%M:%S")
    except ValueError:
        return datetime.min


def build_canonical_rows(rows: Iterable[dict]) -> tuple[list[dict], dict[str, int]]:
    raw_rows = list(rows)
    valid_rows: list[tuple[int, str, dict]] = []
    exact_counter: Counter[tuple[str, ...]] = Counter()
    latest: dict[str, tuple[datetime, int, dict]] = {}

    for index, original in enumerate(raw_rows):
        row = {field: str(original.get(field) or "").strip() for field in RAW_FIELDS}
        key = normalize_uc(row["UC"])
        if not key:
            continue
        row["UC"] = key
        valid_rows.append((index, key, row))
        exact_counter[tuple(row[field] for field in RAW_FIELDS)] += 1
        candidate = (_scan_time(row["DATA_SCAN"]), index, row)
        if key not in latest or candidate[:2] > latest[key][:2]:
            latest[key] = candidate

    canonical_all = []
    for key, (_, _, row) in latest.items():
        canonical_all.append({"CANONICAL_KEY": f"CPFL:{key}", **row})
    canonical_all.sort(key=lambda row: (row["PERFIL"], int(row["UC"])))
    active = [row for row in canonical_all if row["STATUS"].upper() == ACTIVE_STATUS]
    metrics = {
        "RAW_INVENTORY_ROWS": len(raw_rows),
        "VALID_IDENTIFIED_ROWS": len(valid_rows),
        "UNIQUE_UCS": len(canonical_all),
        "ACTIVE_UNIQUE_UCS": len(active),
        "INACTIVE_UCS": len(canonical_all) - len(active),
        "DUPLICATE_ROWS_REMOVED": len(valid_rows) - len(canonical_all),
        "MANUAL_DUPLICATE_ROWS": sum(count - 1 for count in exact_counter.values() if count > 1),
    }
    return active, metrics


def write_canonical_inventory(raw_path: Path, canonical_path: Path, metrics_path: Path) -> tuple[list[dict], dict[str, int]]:
    with raw_path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    canonical, metrics = build_canonical_rows(rows)
    canonical_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = canonical_path.with_suffix(canonical_path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CANONICAL_FIELDS)
        writer.writeheader()
        writer.writerows(canonical)
    temporary.replace(canonical_path)
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return canonical, metrics


def ensure_canonical_inventory(raw_path: Path, canonical_path: Path, metrics_path: Path) -> tuple[list[dict], dict[str, int]]:
    if (not canonical_path.exists() or not metrics_path.exists()
            or canonical_path.stat().st_mtime < raw_path.stat().st_mtime):
        return write_canonical_inventory(raw_path, canonical_path, metrics_path)
    with canonical_path.open(newline="", encoding="utf-8-sig") as handle:
        canonical = list(csv.DictReader(handle))
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    return canonical, {key: int(value) for key, value in metrics.items()}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_pdf_sha_index(root: Path, index_path: Path) -> dict[str, str]:
    rows: list[dict[str, str]] = []
    index: dict[str, str] = {}
    for pdf in sorted(root.rglob("*.pdf")):
        if not pdf.is_file():
            continue
        digest = sha256_file(pdf)
        resolved = str(pdf.resolve())
        index.setdefault(digest, resolved)
        rows.append({"SHA256": digest, "PATH": resolved})
    temporary = index_path.with_suffix(index_path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=("SHA256", "PATH"))
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(index_path)
    return index


def load_pdf_sha_index(index_path: Path) -> dict[str, str]:
    if not index_path.exists():
        return {}
    index: dict[str, str] = {}
    with index_path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            digest = str(row.get("SHA256") or "").strip().lower()
            if digest:
                index.setdefault(digest, str(row.get("PATH") or "").strip())
    return index


def append_pdf_sha(index_path: Path, digest: str, path: Path) -> None:
    new_file = not index_path.exists()
    with index_path.open("a", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=("SHA256", "PATH"))
        if new_file:
            writer.writeheader()
        writer.writerow({"SHA256": digest.lower(), "PATH": str(path.resolve())})
