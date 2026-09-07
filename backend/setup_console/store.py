"""
store.py — JSON 永続化 (アトミック書き込み)
台帳・フロー・履歴は backend/data/*.json (SETUP_CONSOLE_DATA_DIR で変更可)。
一時ファイルに書いて os.replace で置き換える (読みかけの半端な JSON を見せない)。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.getenv("SETUP_CONSOLE_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))


def data_path(name: str) -> Path:
    return DATA_DIR / name


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
