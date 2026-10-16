"""微博适配器:weibo.com 桌面 ajax 通道(wu2198 monitor 同款)。

2026-10-16 实测:
- m.weibo.cn getIndex 本机出口持续 432(弃用);
- weibo.com/ajax/statuses/mymblog 匿名访客(转正 SUB)→ 403(不够);
- **带 cookie(浏览器里登录微博后的 Cookie)→ wu2198 同款稳定通道**。
课程口径:用学员自己的 cookie「以谁的名义抓」,抓谁仍由 creators 表决定。
cookie 配在 config.yaml weibo.cookie,不落库不进日志。
"""
from __future__ import annotations

import re
import time
from datetime import datetime

import requests

from .. import config, identity
from ..model import AdapterError, BaseAdapter

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36")


def _session_with_cookie() -> requests.Session:
    # 优先用扫码登录态(面板「身份与登录」),其次 config.yaml 手粘 cookie
    header = identity.cookie_header("weibo")
    if "SUBP=" not in header:
        header = str(config.load().get("weibo", {}).get("cookie") or "").strip()
    if not header:
        raise AdapterError("微博通道需要登录:面板「身份与登录」→ 扫码登录微博(推荐),"
                           "或在 config.yaml weibo.cookie 粘浏览器 Cookie(wu2198 同款通道)")
    s = requests.Session()
    s.trust_env = False
    s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                      "X-Requested-With": "XMLHttpRequest"})
    for chunk in header.split(";"):
        chunk = chunk.strip()
        if "=" in chunk:
            name, value = chunk.split("=", 1)
            s.cookies.set(name.strip(), value.strip(), domain=".weibo.com")
    return s


def _parse_time(text: str) -> str | None:
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%dT%H:%M:%S")
        except Exception:
            continue
    return None


class WeiboAdapter(BaseAdapter):
    platform = "weibo"
    mode = "auto"

    def _mymblog(self, uid: str, page: int = 1) -> list[dict]:
        s = _session_with_cookie()
        r = s.get(
            f"https://weibo.com/ajax/statuses/mymblog",
            params={"uid": uid, "page": page, "feature": 0},
            headers={"Referer": f"https://weibo.com/u/{uid}"},
            timeout=15, allow_redirects=False,
        )
        if r.status_code in (301, 302):
            raise AdapterError("微博 cookie 已失效(跳登录页),请更新 config.yaml weibo.cookie")
        try:
            payload = r.json()
        except Exception as exc:
            raise AdapterError(f"微博接口非 JSON(http {r.status_code}):cookie 可能失效") from exc
        if payload.get("ok") == -100:
            raise AdapterError("微博 cookie 已失效(ok=-100),请更新 config.yaml weibo.cookie")
        if payload.get("ok") != 1:
            raise AdapterError(f"微博接口失败:{str(payload)[:120]}")
        return (payload.get("data") or {}).get("list") or []

    def fetch(self, creator: dict, limit: int = 5) -> list[dict]:
        uid = creator["platform_uid"]
        posts = self._mymblog(uid)
        items = []
        for mb in posts:
            text = (mb.get("text_raw") or re.sub(r"<[^>]+>", "", mb.get("text") or "")).strip()
            if not text:
                continue
            bid = mb.get("mblogid") or mb.get("bid")
            items.append({
                "item_id": str(bid),
                "title": text[:80],
                "url": f"https://weibo.com/{uid}/{bid}",
                "published_at": _parse_time(mb.get("created_at") or ""),
                "summary": text[:160],
            })
            if len(items) >= limit:
                break
        if not items:
            raise AdapterError("微博返回空(该账号可能设置仅粉丝可见)")
        return items

    def creator_name(self, uid: str) -> str | None:
        try:
            posts = self._mymblog(uid)
            for mb in posts:
                user = mb.get("user") or {}
                name = (user.get("screen_name") or "").strip()
                if name:
                    return name
        except Exception:
            return None
        return None
