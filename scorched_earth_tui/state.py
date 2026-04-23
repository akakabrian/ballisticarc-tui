"""Persistence for scorched-earth-tui — high score + sound toggle.

Stored in `$XDG_DATA_HOME/scorched-earth-tui/state.json`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / "scorched-earth-tui"
    return Path.home() / ".local" / "share" / "scorched-earth-tui"


def state_path() -> Path:
    return _data_dir() / "state.json"


def load() -> dict[str, Any]:
    path = state_path()
    if not path.exists():
        return {"high_score": 0, "sound_enabled": False}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        try:
            path.rename(path.with_suffix(".corrupt.json"))
        except OSError:
            pass
        return {"high_score": 0, "sound_enabled": False}
    data.setdefault("high_score", 0)
    data.setdefault("sound_enabled", False)
    return data


def save(data: dict[str, Any]) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp.replace(path)


def record_high_score(data: dict[str, Any], score: int) -> bool:
    cur = int(data.get("high_score", 0))
    if score > cur:
        data["high_score"] = score
        return True
    return False
