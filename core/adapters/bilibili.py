"""B站适配器:big_v bilibili_monitor.py 同款两级通道(2026-10-16 站长点名对齐)。

①主通道:免签名空间接口 x/space/arc/search + curl_cffi chrome 指纹
  (big_v 生产验证过的一发流——单次响应自带 bvid/标题/发布时间/作者,无需再查 view);
②兜底:yt-dlp flat-playlist 枚举空间 + view 公开接口补元数据,
  yt-dlp 参数带 --impersonate Chrome-133 --referer(big_v 同款,缺这俩易被风控拒)。
裸 requests 直打会被 -412/-352 拒,勿回退。
"""
from __future__ import annotations

import json
import time

from .. import http
from ..model import AdapterError, BaseAdapter

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 15_3) AppleWebKit/537.36 "
      "Chrome/133 Safari/537.36")


def _iso(ts) -> str | None:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(int(ts))) if ts else None


def _api_recent(mid: str, limit: int) -> list[dict]:
    """big_v 同款:免签名 arc/search,curl_cffi chrome 指纹优先,urllib 兜底。"""
    from curl_cffi import requests as curl_requests
    url = f"https://api.bilibili.com/x/space/arc/search?mid={mid}&pn=1&ps={limit}&order=pubdate"
    headers = {
        "User-Agent": UA,
        "Referer": f"https://space.bilibili.com/{mid}/video",
        "Origin": "https://space.bilibili.com",
        "Accept": "application/json, text/plain, */*",
    }
    try:
        response = curl_requests.get(url, headers=headers, impersonate="chrome", timeout=20)
        payload = response.json()
    except Exception:
        from urllib.request import Request, urlopen
        with urlopen(Request(url, headers=headers), timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    if not isinstance(payload, dict) or int(payload.get("code") or -1) != 0:
        raise RuntimeError(str((payload or {}).get("message") if isinstance(payload, dict) else payload)[:80])
    vlist = ((payload.get("data") or {}).get("list") or {}).get("vlist") or []
    return [v for v in vlist if isinstance(v, dict) and v.get("bvid")]


class BilibiliAdapter(BaseAdapter):
    platform = "bilibili"
    mode = "auto"

    def fetch(self, creator: dict, limit: int = 5) -> list[dict]:
        mid = creator["platform_uid"]
        # ① big_v 主通道:一发拿全(bvid/标题/时间/作者)
        try:
            vlist = _api_recent(mid, limit)
            if vlist:
                return [{
                    "item_id": v["bvid"],
                    "title": v.get("title") or v["bvid"],
                    "url": f"https://www.bilibili.com/video/{v['bvid']}",
                    "published_at": _iso(v.get("created")),
                    "summary": f"UP主 {v.get('author', '')} · 播放 {v.get('play', '?')}",
                } for v in vlist[:limit]]
        except Exception as exc:
            primary_err = str(exc)
        else:
            primary_err = "主通道返回空"

        # ② 兜底:yt-dlp 枚举(big_v 同款参数)+ view 补元数据
        try:
            ids = http.run_ytdlp(
                f"https://space.bilibili.com/{mid}",
                use_proxy=None, playlist_end=limit, print_fmt="%(id)s",
                impersonate="Chrome-133", referer="https://www.bilibili.com/",
            )
        except Exception as exc:
            raise AdapterError(f"B站抓取失败(主通道:{primary_err};兜底:{exc})") from exc
        session = http.new_session()
        items = []
        for i, bvid in enumerate(ids):
            meta = self._view(session, bvid)
            if meta:
                items.append({
                    "item_id": bvid, "title": meta.get("title") or bvid,
                    "url": f"https://www.bilibili.com/video/{bvid}",
                    "published_at": _iso(meta.get("pubdate")),
                    "summary": (meta.get("desc") or "")[:120],
                })
            else:
                items.append({"item_id": bvid, "title": f"B站视频 {bvid}",
                              "url": f"https://www.bilibili.com/video/{bvid}",
                              "published_at": None, "summary": ""})
            if i < len(ids) - 1:
                time.sleep(0.8)
        if not items:
            raise AdapterError(f"B站枚举为空(主通道:{primary_err})")
        return items

    @staticmethod
    def _view(session, bvid: str) -> dict | None:
        r = session.get(
            f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
            headers={"Referer": "https://www.bilibili.com/", "Accept": "application/json",
                     "User-Agent": UA},
            timeout=15,
        )
        payload = r.json()
        if payload.get("code") != 0:
            return None
        return payload.get("data") or {}

    def creator_name(self, mid: str) -> str | None:
        try:
            vlist = _api_recent(mid, 1)
            if vlist:
                return vlist[0].get("author")
        except Exception:
            pass
        try:
            ids = http.run_ytdlp(f"https://space.bilibili.com/{mid}", use_proxy=None,
                                 playlist_end=1, print_fmt="%(id)s",
                                 impersonate="Chrome-133", referer="https://www.bilibili.com/")
            if ids:
                session = http.new_session()
                meta = self._view(session, ids[0])
                return (meta or {}).get("owner", {}).get("name")
        except Exception:
            return None
        return None
