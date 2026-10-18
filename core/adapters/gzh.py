"""公众号适配器:微信读书通道(2026-10-19 站长点名「从 we-mp-rss 抽取逻辑接上」)。

思路取自本机 we-mp-rss(WeRSS)项目的生产通道(只抄端点结论与坑位,不 import 原项目):
- 文章列表:GET https://weread.qq.com/web/mp/articles?bookId=<MP_WXS_数字>&offset=0
  → reviews[].subReviews[].review{reviewId, createTime, mpInfo{title, originalId, content}}
- 兜底(列表接口不可用时):GET /api/mp/cover?bookId= → 最新一篇,链接由 reviewId 推导
- 原文链接:https://mp.weixin.qq.com/s/<originalId>;⚠️ originalId 里的 '_' 被微信读书写成 '~',
  必须还原,否则微信返回「参数错误」(WeRSS 实测 149 篇全错的教训)
- 登录:微信读书 cookie(面板「身份与登录」→ 公众号扫码,微信扫一扫即可,无需公众号后台);
  errCode=-2012/-2010 = 登录失效;-2041/-2014 = 该账号当日配额耗尽
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from urllib.parse import quote

import requests

from .. import config, identity
from ..model import AdapterError, BaseAdapter

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36")
WEREAD = "https://weread.qq.com"


def _cookie_header() -> str:
    header = identity.cookie_header("gzh")  # 面板扫码登录态优先
    if "wr_skey" not in header:  # 回退 config.yaml 手粘的微信读书 Cookie
        header = str(config.load().get("wechat_mp", {}).get("cookie") or "").strip()
    if "wr_skey" not in header:
        raise AdapterError("公众号通道需要登录:面板「身份与登录」→ 公众号 → 扫码"
                           "(微信读书二维码,微信扫一扫即可);或把 weread.qq.com 的 Cookie 粘到 config.yaml wechat_mp.cookie")
    return header


def _get(path: str, params: dict) -> dict:
    s = requests.Session()
    s.trust_env = False
    r = s.get(f"{WEREAD}{path}", params=params,
              headers={"Cookie": _cookie_header(), "User-Agent": UA,
                       "Accept": "application/json, text/plain, */*",
                       "Origin": WEREAD, "Referer": f"{WEREAD}/"},
              timeout=20)
    if r.status_code in (401, 403):
        raise AdapterError("公众号:微信读书登录已失效(HTTP 401/403),请重新扫码")
    try:
        payload = r.json()
    except Exception as exc:
        raise AdapterError(f"公众号接口非 JSON(HTTP {r.status_code}):登录可能已失效") from exc
    code = payload.get("errCode", payload.get("errcode", 0))
    try:
        code = int(code or 0)
    except (TypeError, ValueError):
        code = -1
    if code in (-2012, -2010):
        raise AdapterError("公众号:微信读书登录已失效,请到「身份与登录」重新扫码")
    if code == -2041 or code == -2014:
        raise AdapterError("公众号:该微信读书账号今日配额耗尽,明早自动恢复(或换号重扫)")
    if code != 0:
        raise AdapterError(f"公众号接口错误 errCode={code}:{str(payload.get('errMsg'))[:80]}")
    return payload


def _mp_link(token: str) -> str:
    """weread 的文章 token → 微信原文短链('~' 还原为 '_')。"""
    return f"https://mp.weixin.qq.com/s/{quote(str(token or '').replace('~', '_'), safe='')}"


def _iso(ts) -> str | None:
    try:
        ts = int(ts)
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S") if ts else None
    except (TypeError, ValueError):
        return None


def search_mp(keyword: str) -> list[dict]:
    """weread 全局搜索里的公众号条目(需登录 cookie;匿名只返回电子书)。"""
    payload = _get("/web/search/global", {"keyword": keyword, "maxIdx": 0, "count": 20})
    out = []
    for item in payload.get("books") or []:
        bi = item.get("bookInfo") or {}
        bid = str(bi.get("bookId") or "")
        if not bid.startswith("MP_"):
            continue
        out.append({"book_id": bid, "title": bi.get("title") or bid,
                    "author": bi.get("author") or "", "intro": (bi.get("intro") or "")[:40]})
    return out


class GzhAdapter(BaseAdapter):
    platform = "gzh"
    mode = "auto"

    def fetch(self, creator: dict, limit: int = 5) -> list[dict]:
        book_id = creator["platform_uid"]
        payload = _get("/web/mp/articles", {"bookId": book_id, "offset": 0})
        items: list[dict] = []
        for group in payload.get("reviews") or []:
            for sub in group.get("subReviews") or []:
                review = sub.get("review") or {}
                info = review.get("mpInfo") or {}
                rid = review.get("reviewId") or sub.get("reviewId")
                if not rid:
                    continue
                items.append({
                    "item_id": str(rid),
                    "title": info.get("title") or "(无标题)",
                    "url": _mp_link(info.get("originalId") or ""),
                    "published_at": _iso(review.get("createTime") or group.get("createTime")),
                    "summary": (info.get("content") or "")[:160],
                })
                if len(items) >= limit:
                    break
            if len(items) >= limit:
                break
        if not items:  # 列表接口空 → cover 兜底拿最新一篇
            cover = _get("/api/mp/cover", {"bookId": book_id})
            rid = cover.get("reviewId")
            if not rid:
                raise AdapterError("公众号没返回文章(可能从未在微信读书发布过)")
            token = str(rid)
            m = re.search(r"MP_WXS_\d+_(.+)$", token)
            if m:  # reviewId = MP_WXS_<bookId>_<token>,链接取末段
                token = m.group(1)
            items.append({"item_id": str(rid), "title": cover.get("title") or "(无标题)",
                          "url": _mp_link(token), "published_at": None, "summary": ""})
        return items

    def creator_name(self, book_id: str) -> str | None:
        try:
            cover = _get("/api/mp/cover", {"bookId": book_id})
            return (cover.get("name") or "").strip() or None
        except Exception:
            return None
