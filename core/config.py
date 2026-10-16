"""配置读取:config.yaml 单一来源。"""
from __future__ import annotations

from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"

_cache: dict | None = None


def load(force: bool = False) -> dict:
    global _cache
    if _cache is None or force:
        _cache = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    return _cache
