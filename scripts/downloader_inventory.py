"""Read-only inventory of catalog, schedules and recent downloader runs."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

import yaml


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    catalog = yaml.safe_load((args.root / "radar_v2/config/tasks.yaml").read_text(encoding="utf-8"))
    tasks = [row for row in catalog["tasks"] if row["task_id"].startswith("dl_")]
    with sqlite3.connect(f"file:{args.db.as_posix()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        schedules = [dict(row) for row in conn.execute("SELECT * FROM schedules")]
        runs = [dict(row) for row in conn.execute(
            "SELECT id,task_id,status,exit_code,started_at,finished_at,duration_s,command "
            "FROM runs WHERE task_id LIKE 'dl_%' ORDER BY id DESC"
        )]
    result = []
    for task in tasks:
        script = args.root / task["script"]
        task_runs = [row for row in runs if row["task_id"] == task["task_id"]]
        task_schedules = [row for row in schedules if row["task_id"] == task["task_id"]]
        result.append({
            "task_id": task["task_id"], "task_name": task["name"],
            "entrypoint": task["script"], "entrypoint_exists": script.is_file(),
            "entrypoint_sha256": hashlib.sha256(script.read_bytes()).hexdigest() if script.is_file() else None,
            "schedules": task_schedules, "last_run": task_runs[0] if task_runs else None,
            "last_success": next((row for row in task_runs if row["status"] == "success"), None),
        })
    print(json.dumps({"total_downloaders": len(result), "downloaders": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
