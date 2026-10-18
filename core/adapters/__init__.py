"""适配器注册表:平台 → 适配器实例。扫描器只对 auto 通道出网。"""
from __future__ import annotations

from .bilibili import BilibiliAdapter
from .douyin import DouyinAdapter
from .gzh import GzhAdapter
from .manual import ManualAdapter
from .weibo import WeiboAdapter
from .youtube import YoutubeAdapter
from ..model import PLATFORMS

ADAPTERS: dict[str, BaseAdapter] = {
    "youtube": YoutubeAdapter(),
    "bilibili": BilibiliAdapter(),
    "weibo": WeiboAdapter(),
    "douyin": DouyinAdapter(),
    "gzh": GzhAdapter(),
    "xhs": ManualAdapter("xhs", "semi"),
    "sph": ManualAdapter("sph", "semi"),
}
AUTO_ADAPTERS = {k: v for k, v in ADAPTERS.items() if PLATFORMS[k]["mode"] == "auto"}

__all__ = ["ADAPTERS", "AUTO_ADAPTERS"]
