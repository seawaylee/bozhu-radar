"""关注列表拉取:扫码登录后,把「我关注的人」拉回来供搜索筛选、批量加入监控。

- 抖音:登录 cookie + a_bogus 本地签名,走 /aweme/v1/web/user/following/list/
  (签名模块为 Apache-2.0 开源算法副本 _douyin_abogus;纯接口,不碰浏览器)
- B站:登录 cookie(SESSDATA)+ curl_cffi 指纹,走 /x/relation/followings
  (2026-10-18 实测该接口匿名 -101,必须登录;登录走面板内嵌二维码纯 HTTP 通道)
- 微博:登录 cookie + weibo.com 桌面 ajax(wu2198 同款传输层);扫码新会话数据接口
  持续软风控(2026-10-18 复测养两天仍「地址不存在」),config.yaml 成熟 cookie 可回退
- 小红书:登录态起无头浏览器,开自己主页的关注弹窗,拦页内 followeduser 接口
  (浏览器自己带设备盾签名,2026-10-18 登录态失效待重扫,通道待实测)
只拉列表用于「挑人」,监控仍按名单逐人抓——关注几百个不等于全部监控。
"""
from __future__ import annotations

import re
import time
import urllib.parse

import requests

from . import config, db, identity
from .adapters import _douyin_abogus as ab
from .model import AdapterError

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36")


def _webapp_params() -> dict[str, str]:
    return {
        "device_platform": "webapp", "aid": "6383", "channel": "channel_pc_web",
        "pc_client_type": "1", "cookie_enabled": "true", "browser_platform": "MacIntel",
        "browser_name": "Chrome", "browser_version": "139.0.0.0", "os_name": "Mac OS",
        "platform": "PC",
    }


def _douyin_get(path: str, params: dict, cookie_header: str, max_retries: int = 3) -> dict:
    """签名 GET + 重试(新指纹+退避)——抖音风控有时间窗,单发会间歇被拒(big_v 客户端同款 3 次重试)。"""
    backoff = (1.0, 2.5)
    for attempt in range(max_retries):
        query = urllib.parse.urlencode({**_webapp_params(), **params})
        fp = ab.BrowserFingerprintGenerator.generate_fingerprint("Chrome")
        signer = ab.ABogus(fp=fp, user_agent=UA)
        signed, _a, _u, _b = signer.generate_abogus(query, "")
        url = f"https://www.douyin.com{path}?{signed}"
        s = requests.Session()
        s.trust_env = False
        try:
            r = s.get(url, headers={"User-Agent": UA, "Referer": "https://www.douyin.com/",
                                    "Cookie": cookie_header,
                                    "Accept": "application/json, text/plain, */*"}, timeout=15)
            payload = r.json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            if payload.get("status_code") == 2483 or "请先登录" in str(payload.get("status_msg")):
                raise AdapterError("抖音登录态失效,请重新扫码")
            try:
                code = int(payload.get("status_code"))
            except (TypeError, ValueError):
                code = -1
            # ⚠️ 勿写 `int(code or -1)`:成功码 0 是 falsy,0 or -1 = -1 会把成功当失败(2026-10-16 实测踩坑)
            if code == 0:
                return payload
        if attempt < max_retries - 1:
            time.sleep(backoff[min(attempt, len(backoff) - 1)])
    raise AdapterError(f"抖音接口重试 {max_retries} 次仍失败(风控波动窗口,稍后再拉)")


def refresh_douyin() -> int:
    ck = identity.load_cookies("douyin") or {}
    if not ck.get("sessionid"):
        raise AdapterError("请先扫码登录抖音(身份与登录 → 扫码)")
    header = "; ".join(f"{k}={v}" for k, v in ck.items())
    # self 接口参数集(2026-10-16 实测:缺这组参数签名会被拒 status_code!=0)
    self_params = {**_webapp_params(), "verifyFp": "", "type": "0", "need_time_list": "1",
                   "time_list_query": "0", "whale_cut_token": "", "cut_version": "1", "count": "10"}
    self_data = _douyin_get("/aweme/v1/web/user/profile/self/", self_params, header)
    user = (self_data.get("user") or {})
    my_uid = str(user.get("uid") or "")
    if not my_uid:
        raise AdapterError("抖音:拿不到自己的 uid(登录态异常)")
    cursor, pages = 0, 0
    while pages < 50:
        payload = _douyin_get("/aweme/v1/web/user/following/list/",
                              {**_webapp_params(), "user_id": my_uid, "count": "20",
                               "max_time": str(cursor), "min_time": "0"}, header)
        # ⚠️ 抖音把 followings/has_more/max_time 放在顶层(不在 data 下)——2026-10-16 实测踩坑
        followings = payload.get("followings") or (payload.get("data") or {}).get("followings") or []
        if not followings:
            break
        for fu in followings:
            sec = fu.get("sec_uid") or ""
            if not sec:
                continue
            db.upsert_following("douyin", sec, fu.get("nickname") or sec,
                                f"粉丝{fu.get('follower_count', '?')} · {(fu.get('signature') or '')[:40]}")
        new_cursor = payload.get("max_time") or payload.get("min_time") or 0
        if not payload.get("has_more") or new_cursor == cursor:
            break  # 拉完,或游标卡死(重复页)——表里有 UNIQUE 去重,以表内真实数为准
        cursor = new_cursor
        pages += 1
    db.meta_set("following:douyin:fetched_at", db.now_iso())
    return db.following_count("douyin")


def refresh_weibo() -> int:
    header = identity.cookie_header("weibo")
    if "SUBP" not in header:  # 扫码态优先;没有就回退 config.yaml 手粘的成熟 cookie(与内容通道同源)
        header = str(config.load().get("weibo", {}).get("cookie") or "").strip()
    if "SUBP" not in header:
        raise AdapterError("请先扫码登录微博(身份与登录 → 扫码);"
                           "或把浏览器成熟 Cookie 粘到 config.yaml weibo.cookie"
                           "(扫码新会话会被微博软风控,成熟 cookie 不受影响)")
    s = requests.Session()
    s.trust_env = False
    s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                      "Referer": "https://weibo.com/"})
    for chunk in header.split(";"):
        if "=" in chunk:
            name, value = chunk.strip().split("=", 1)
            s.cookies.set(name, value, domain=".weibo.com")
    r = s.get("https://weibo.com/ajax/profile/myinfo", timeout=15)
    try:
        mine = r.json()
    except Exception:
        mine = {}
    my_uid = str((((mine.get("data") or {}).get("user")) or {}).get("id") or "")
    if not my_uid:
        # 2026-10-18 实测:扫码新会话调数据接口返回 {"ok":0,"message":"你访问的地址不存在"}(软风控),
        # 养两天不解;成熟浏览器 cookie(粘 config.yaml weibo.cookie)不受影响
        raise AdapterError("微博接口拒绝服务(扫码新会话被软风控,实测养两天也不解):"
                           "把浏览器里登录微博的成熟 Cookie 粘到 config.yaml weibo.cookie 后再拉")
    total, page = 0, 1
    while page < 50:
        payload = s.get("https://weibo.com/ajax/friendships/friends",
                        params={"page": page, "uid": my_uid}, timeout=15).json()
        data = payload.get("data") or {}
        users = data.get("users") or []
        if not users:
            break
        for u in users:
            uid = str(u.get("id") or "")
            if not uid:
                continue
            db.upsert_following("weibo", uid, u.get("screen_name") or uid,
                                f"粉丝{u.get('followers_count', '?')} · {(u.get('description') or '')[:40]}")
            total += 1
        if data.get("hasmore") is False or not users:
            break
        page += 1
    db.meta_set("following:weibo:fetched_at", db.now_iso())
    return total


def refresh_youtube() -> int:
    ck = identity.load_cookies("youtube") or {}
    if not ck.get("SAPISID"):
        raise AdapterError("请先登录 YouTube(身份与登录 → YouTube,弹窗里完成 Google 登录)")
    proxy = config.load().get("proxy", {}).get("http", "")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        ctx = browser.new_context(
            storage_state=str(identity.state_path("youtube")),
            proxy={"server": proxy} if proxy else None,
            viewport={"width": 1280, "height": 900},
        )
        pg = ctx.new_page()
        pg.goto("https://www.youtube.com/feed/channels", wait_until="domcontentloaded", timeout=60000)
        prev = -1
        for _ in range(40):  # 滚动到底,订阅多也能拉全
            pg.mouse.wheel(0, 4000)
            pg.wait_for_timeout(1100)
            n = pg.evaluate("() => document.querySelectorAll('ytd-channel-renderer').length")
            if n == prev:
                break
            prev = n
        items = pg.evaluate("""() => Array.from(document.querySelectorAll('ytd-channel-renderer')).map(el => {
            const a = el.querySelector('a#main-link, a[href*="/channel/"], a[href*="/@"]')
            const t = el.querySelector('#channel-title, yt-formatted-string#a11y-label')
            return {href: a ? a.getAttribute('href') : '',
                    name: (t ? t.textContent : (a ? a.textContent : '')).trim()}
        })""")
        browser.close()
    total = 0
    for it in items or []:
        href = it.get("href") or ""
        name = (it.get("name") or "").strip()
        m = re.search(r"/channel/(UC[\w-]{20,24})", href)
        uid = m.group(1) if m else (href.strip("/").split("/")[-1] if "/@" in href else "")
        if not uid or not name:
            continue
        if uid.startswith("@") or uid.startswith("UC"):
            db.upsert_following("youtube", uid, name, "")
            total += 1
    db.meta_set("following:youtube:fetched_at", db.now_iso())
    return total


def refresh_bilibili() -> int:
    ck = identity.load_cookies("bilibili") or {}
    if not ck.get("SESSDATA") or not ck.get("DedeUserID"):
        raise AdapterError("请先扫码登录 B站(身份与登录 → 扫码,面板内嵌二维码)")
    mid = ck["DedeUserID"]
    from curl_cffi import requests as creq
    s = creq.Session(impersonate="chrome")
    for name in ("SESSDATA", "DedeUserID", "buvid3", "buvid4", "b_nut"):
        if ck.get(name):
            s.cookies.set(name, ck[name], domain=".bilibili.com")
    page, pages = 1, 0
    while pages < 60:
        r = s.get("https://api.bilibili.com/x/relation/followings",
                  params={"vmid": mid, "pn": page, "ps": 50, "order": "desc",
                          "gaia_source": "main_web"},
                  headers={"Referer": f"https://space.bilibili.com/{mid}/fans/follow",
                           "Origin": "https://space.bilibili.com"}, timeout=15)
        d = r.json()
        if d.get("code") == -101:
            raise AdapterError("B站登录态失效,请重新扫码")
        if d.get("code") != 0:
            raise AdapterError(f"B站接口失败:{d.get('message')}")
        users = (d.get("data") or {}).get("list") or []
        if not users:
            break
        for u in users:
            if u.get("mid"):
                db.upsert_following("bilibili", str(u["mid"]), u.get("uname") or str(u["mid"]),
                                    (u.get("sign") or "")[:50])
        if len(users) < 50:
            break
        page += 1
        pages += 1
        time.sleep(1.2)  # 翻页间隔,礼貌采集(B站风控对连发敏感)
    db.meta_set("following:bilibili:fetched_at", db.now_iso())
    return db.following_count("bilibili")


def refresh_xhs() -> int:
    """小红书:无头浏览器开「我的关注」弹窗,拦页内 followeduser 接口(浏览器自带设备盾签名)。
    ⚠️ 2026-10-18 登录态已失效(guest),通道代码就绪但端到端待重扫后实测。"""
    if not identity.state_path("xhs").exists():
        raise AdapterError("请先扫码登录小红书(身份与登录 → 扫码)")
    from playwright.sync_api import sync_playwright

    me: dict = {}
    payloads: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        ctx = browser.new_context(storage_state=str(identity.state_path("xhs")),
                                  viewport={"width": 1380, "height": 900})
        pg = ctx.new_page()

        def on_resp(r):
            if r.status != 200:
                return
            if "/user/me" in r.url or "followeduser" in r.url:
                try:
                    (payloads if "followeduser" in r.url else me).update(r.json())
                except Exception:
                    pass
        pg.on("response", on_resp)

        pg.goto("https://www.xiaohongshu.com", wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(4500)
        data = me.get("data") or {}
        if data.get("guest") or not data.get("user_id"):
            browser.close()
            raise AdapterError("小红书登录态已失效(服务端判为访客),请到「身份与登录」重新扫码")
        uid = str(data["user_id"])
        pg.goto(f"https://www.xiaohongshu.com/user/profile/{uid}",
                wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(4000)
        clicked = pg.evaluate("""() => {
            const cands = Array.from(document.querySelectorAll('a, span, div'))
                .filter(e => e.childElementCount === 0);
            const el = cands.find(e => /^\\d+\\s*关注$/.test((e.textContent||'').trim()));
            if (el) { el.click(); return true; }
            return false;
        }""")
        if not clicked:
            browser.close()
            raise AdapterError("没找到「关注」入口(页面结构可能变了,重新扫码后再试;仍不行请反馈)")
        prev, stagnant = -1, 0
        for _ in range(40):  # 弹窗内滚动翻页,接口响应连拦 3 轮不涨就认为拉完
            pg.evaluate("""() => { const m = document.querySelector('.user-list, [class*=user-list]');
                if (m) m.scrollTop = m.scrollHeight; }""")
            pg.wait_for_timeout(1300)
            if len(payloads) == prev:
                stagnant += 1
                if stagnant >= 3:
                    break
            else:
                stagnant = 0
                prev = len(payloads)
        browser.close()
    total = 0
    seen: set[str] = set()
    for payload in payloads:
        users = ((payload.get("data") or {}).get("users")
                 or (payload.get("data") or {}).get("followed_users") or [])
        for u in users:
            uid_f = str(u.get("user_id") or "")
            if uid_f and uid_f not in seen:
                seen.add(uid_f)
                db.upsert_following("xhs", uid_f, u.get("nickname") or uid_f,
                                    (u.get("desc") or "")[:50])
                total += 1
    if not total:
        raise AdapterError("拉到了 0 条(弹窗接口没被触发;重新登录后再试,仍不行请反馈)")
    db.meta_set("following:xhs:fetched_at", db.now_iso())
    return total


def refresh(platform: str) -> int:
    if platform == "douyin":
        return refresh_douyin()
    if platform == "weibo":
        return refresh_weibo()
    if platform == "youtube":
        return refresh_youtube()
    if platform == "bilibili":
        return refresh_bilibili()
    if platform == "xhs":
        return refresh_xhs()
    raise AdapterError("该平台暂不支持拉取关注列表")


def list_following(platform: str, q: str = "", limit: int = 400) -> list[dict]:
    return db.search_following(platform, q, limit)
