"""Robot speaker volume via the daemon REST API (0-100)."""

from __future__ import annotations

import os

import requests


def _base() -> str:
    host = os.getenv("REACHY_HOST", "reachy-mini.local")
    return f"http://{host}:8000"


def get_volume() -> int:
    r = requests.get(f"{_base()}/api/volume/current", timeout=5)
    r.raise_for_status()
    return int(r.json()["volume"])


def set_volume(level: int) -> int:
    level = max(0, min(100, int(level)))
    r = requests.post(f"{_base()}/api/volume/set", json={"volume": level}, timeout=10)
    r.raise_for_status()
    return level
