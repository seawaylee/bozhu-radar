"""统一添加向导:随便填——主页 URL / 平台 ID / @handle / 昆淆的用户输入,自动识别平台并解析。

这是「加博主=发一句话」体验的后端:parse() 只解析不入库,
前端展示解析结果让用户确认名字后,再调 db.add_creator 落册。
"""
from __future__ import annotations

import re
import time

from . import http
from .model import AdapterError, PLATFORMS


def _proxy() -> str:
    from . import config
    return config.load().get("proxy", {}).get("http", "")


def detect_platform(text: str) -> str | None:
    t = text.strip()
    if not t:
        return None
    if re.search(r"youtube\.com|youtu\.be", t) or t.startswith("@") or re.fullmatch(r"UC[\w-]{20,24}", t):
        return "youtube"
    if re.search(r"bilibili\.com|b23\.tv", t):
        return "bilibili"
    if re.search(r"weibo\.(com|cn)", t):
        return "weibo"
    if re.search(r"douyin\.com", t):
        return "douyin"
    if re.search(r"xiaohongshu\.com|xhslink", t):
        return "xhs"
    if re.search(r"mp\.weixin\.qq\.com", t) or t.startswith(("公众号", "微信公众号")):
        return "gzh"
    if t.startswith("视频号"):
        return "sph"
    if re.fullmatch(r"\d{3,12}", t):
        return "bilibili"  # 裸数字 ID:B站 mid 最短,先猜 B站,解析时让用户确认
    return None


def _resolve_youtube(text: str) -> dict:
    m = re.search(r"(UC[\w-]{20,24})", text)
    if not m:
        handle = None
        hm = re.search(r"youtube\.com/(@[\w.\-\u4e00-\u9fff]+)", text)
        if hm:
            handle = hm.group(1)
        elif text.strip().startswith("@"):
            handle = text.strip()
        if not handle:
            raise AdapterError("YouTube 输入认不出:要主页链接或 @handle 或 UC 开头的频道ID")
        s = http.new_session(proxies={"http": _proxy(), "https": _proxy()} if _proxy() else None)
        page = s.get(f"https://www.youtube.com/{handle}", timeout=25)
        m = re.search(r'"externalId":"(UC[\w-]{20,24})"', page.text)
        if not m:
            m = re.search(r'"channelId":"(UC[\w-]{20,24})"', page.text)
        if not m:
            raise AdapterError(f"YouTube 频道页解析失败(可能风控/网络),handle={handle}")
    cid = m.group(1)
    name = ""
    try:
        s = http.new_session(proxies={"http": _proxy(), "https": _proxy()} if _proxy() else None)
        page = s.get(f"https://www.youtube.com/channel/{cid}", timeout=25)
        tm = re.search(r"<title>(.*?)</title>", page.text)
        if tm:
            name = re.sub(r"\s*-\s*YouTube\s*$", "", tm.group(1)).strip()
    except Exception:
        pass
    return {"platform": "youtube", "platform_uid": cid,
            "suggested_name": name or f"YouTube频道{cid[:8]}", "url": f"https://www.youtube.com/channel/{cid}"}


def _resolve_bilibili(text: str) -> dict:
    m = re.search(r"(?:space\.bilibili\.com/|bilibili\.com/)(\d+)", text) or re.fullmatch(r"(\d{3,12})", text.strip())
    if not m:
        raise AdapterError("B站输入认不出:要空间链接(space.bilibili.com/数字)或纯数字 UID")
    mid = m.group(1)
    name = f"UP主{mid}"
    try:
        from .adapters.bilibili import BilibiliAdapter
        name = BilibiliAdapter().creator_name(mid) or name
    except Exception:
        pass
    return {"platform": "bilibili", "platform_uid": mid, "suggested_name": name,
            "url": f"https://space.bilibili.com/{mid}"}


def _resolve_weibo(text: str) -> dict:
    m = re.search(r"weibo\.(?:com|cn)/(?:u/)?(\d+)", text) or re.fullmatch(r"(\d{8,12})", text.strip())
    if not m:
        raise AdapterError("微博输入认不出:要 weibo.com/u/数字 链接或纯数字 uid")
    uid = m.group(1)
    name = f"微博用户{uid}"
    try:
        from .adapters.weibo import WeiboAdapter
        fetched_name = WeiboAdapter().creator_name(uid)
        if fetched_name:
            name = fetched_name
    except Exception:
        pass
    return {"platform": "weibo", "platform_uid": uid, "suggested_name": name,
            "url": f"https://m.weibo.cn/u/{uid}"}


def _resolve_douyin(text: str) -> dict:
    m = re.search(r"douyin\.com/(?:user|share/user)/([\w.-]+)", text)
    if not m:
        raise AdapterError("抖音输入认不出:要 douyin.com/user/ 开头的主页链接(含 MS4w… 的 sec_uid)")
    sec = m.group(1)
    name = f"抖音博主{sec[:10]}"
    try:
        from .adapters.douyin import DouyinAdapter
        fetched = DouyinAdapter().creator_name(sec)
        if fetched:
            name = fetched
    except Exception:
        pass
    return {"platform": "douyin", "platform_uid": sec, "suggested_name": name,
            "url": f"https://www.douyin.com/user/{sec}"}


def _resolve_xhs(text: str) -> dict:
    m = re.search(r"xiaohongshu\.com/user/profile/([\w]+)", text)
    if not m:
        raise AdapterError("小红书输入认不出:要个人主页链接(xiaohongshu.com/user/profile/…)")
    uid = m.group(1)
    return {"platform": "xhs", "platform_uid": uid, "suggested_name": f"小红书博主{uid[:8]}",
            "url": text.strip()}


def _resolve_manual(text: str, platform: str) -> dict:
    label = PLATFORMS[platform]["label"]
    name = ""
    m = re.search(rf"{label}[:：]\s*(.+)", text)
    if m:
        name = m.group(1).strip()
    elif re.search(r"mp\.weixin\.qq\.com", text):
        name = ""
    if not name:
        raise AdapterError(f"请按「{label}:博主名」的格式填写(半自动平台没有公开主页可解析)")
    return {"platform": platform, "platform_uid": f"manual:{name}", "suggested_name": name,
            "url": "", "note": "半自动平台:新内容由你在面板上手动登记入册"}


def _expand_short_link(text: str) -> str:
    """v.douyin.com / b23.tv 分享短链 → 跟随跳转拿真实主页 URL。"""
    import re as _re
    if not _re.search(r"https?://(v\.douyin\.com|b23\.tv)/", text):
        return text
    try:
        s = http.new_session()
        r = s.head(text.strip(), timeout=10, allow_redirects=True)
        return str(r.url) if r.url else text
    except Exception:
        return text


def parse(text: str) -> dict:
    text = _expand_short_link(text.strip())
    platform = detect_platform(text)
    if not platform:
        raise AdapterError(
            "没认出这是哪个平台。支持:YouTube/B站/微博/抖音/小红书主页链接,"
            "或「公众号:名字」「视频号:名字」"
        )
    if platform == "youtube":
        return _resolve_youtube(text)
    if platform == "bilibili":
        return _resolve_bilibili(text)
    if platform == "weibo":
        return _resolve_weibo(text)
    if platform == "sph":
        raise AdapterError("视频号没有网页版,功能已置灰;建议:同一博主通常全平台同名,"
                           "把TA的抖音/B站/微博号加进监控即可覆盖")
    if platform == "douyin":
        return _resolve_douyin(text)
    if platform == "xhs":
        return _resolve_xhs(text)
    return _resolve_manual(text, platform)


def time_beijing_iso(ts: int | float | None) -> str | None:
    if not ts:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))
