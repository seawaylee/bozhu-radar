"""半自动/进阶平台适配器(公众号/小红书/视频号/抖音)。

这些平台没有免登录公开出口(或需要浏览器+登录态),课程口径:
- fetch 永远返回空列表、不报错——健康面板显示「无自动通道」,不算故障;
- 新内容走两条路:①用户在面板上手动登记入册;②(进阶课节)扫码登录后
  由无头浏览器通道自动抓——本演示版未激活,面板如实标注。
"""
from __future__ import annotations

from ..model import BaseAdapter


class ManualAdapter(BaseAdapter):
    def __init__(self, platform: str, mode: str):
        self.platform = platform
        self.mode = mode

    def fetch(self, creator: dict, limit: int = 5) -> list[dict]:
        return []
