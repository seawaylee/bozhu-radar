"""抖音适配器(全自动·免登录):匿名 ttwid + 无头浏览器渲染 share 页。

2026-10-16 实测定稿的路线(全程无登录、无签名逆向):
1. ttwid 匿名引导:POST ttwid.bytedance.com/ttwid/union/register(西瓜接口签发,
   对 douyin.com 有效);缓存于 data/douyin_state.json,7 天续期。
2. 无头 Chromium(移动端 UA+ttwid cookie)打开 m.douyin.com/share/user/<sec_uid>:
   用户资料由服务端渲染进 _ROUTER_DATA;页面自身会发
   /web/api/v2/aweme/post/ 同源 XHR(在真实浏览器上下文里**不需要 a_bogus 签名**,
   纯 requests 打 www 接口会被 ArgusSecurityPlugin 403「Uifid Not Found」)。
3. 拦截该 XHR 响应体 → aweme_list 即最新视频;滚动戳一下催它出请求。

礼貌约束:每博主每轮一次页面访问,headless,绝不复用任何生产登录态。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

from .. import identity
from ..model import AdapterError, BaseAdapter

UA_PC = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36")
UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"
             " Version/17.0 Mobile/15E148 Safari/604.1")

_STATE_PATH = Path(__file__).resolve().parents[2] / "data" / "douyin_state.json"


def _get_ttwid(force_refresh: bool = False) -> str:
    """匿名 ttwid,带缓存。"""
    if not force_refresh and _STATE_PATH.exists():
        try:
            state = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
            ts = datetime.fromisoformat(state["fetched_at"])
            if datetime.now() - ts < timedelta(days=7) and state.get("ttwid"):
                return state["ttwid"]
        except Exception:
            pass
    s = requests.Session()
    s.trust_env = False
    r = s.post(
        "https://ttwid.bytedance.com/ttwid/union/register/",
        json={"region": "cn", "aid": 1768, "needFid": False, "service": "www.ixigua.com",
              "migrate_info": {"ticket": "", "source": "node"}, "cbUrlProtocol": "https",
              "union": True},
        headers={"User-Agent": UA_PC}, timeout=15,
    )
    ttwid = next((c.value for c in s.cookies if c.name == "ttwid"), "")
    if not ttwid:
        raise AdapterError("ttwid 匿名引导失败(抖音通道暂不可用)")
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps({"ttwid": ttwid, "fetched_at": datetime.now().isoformat(timespec="seconds")}),
                           encoding="utf-8")
    return ttwid


def _context_cookies(ttwid: str) -> list[dict]:
    """浏览器上下文 cookie:优先用扫码登录态(更稳),没有再匿名 ttwid。"""
    cookies = [{"name": k, "value": v, "domain": ".douyin.com", "path": "/"}
               for k, v in (identity.load_cookies("douyin") or {}).items()]
    if ttwid and "ttwid" not in {c["name"] for c in cookies}:
        cookies.append({"name": "ttwid", "value": ttwid, "domain": ".douyin.com", "path": "/"})
    return cookies


def _load_page_json(sec_uid: str, want_body: bool = False) -> tuple[dict | None, str | None]:
    """打开 share 页,返回 (ROUTER_DATA, aweme XHR 响应 JSON 文本)。任一可缺。"""
    from playwright.sync_api import sync_playwright

    ttwid = _get_ttwid()
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-proxy-server", "--disable-blink-features=AutomationControlled"],
        )
        try:
            ctx = browser.new_context(user_agent=UA_MOBILE, viewport={"width": 390, "height": 844},
                                      is_mobile=True, has_touch=True)
            ctx.add_cookies(_context_cookies(ttwid))
            pg = ctx.new_page()
            holder: dict = {}
            if want_body:
                def on_resp(r):
                    if "aweme/post" in r.url and "body" not in holder:
                        try:
                            holder["body"] = r.text()
                        except Exception:
                            pass
                pg.on("response", on_resp)
            pg.goto(f"https://m.douyin.com/share/user/{sec_uid}",
                    wait_until="domcontentloaded", timeout=45000)
            router = None
            body = None
            for _ in range(12):
                router = pg.evaluate("() => window._ROUTER_DATA || null")
                if want_body and "body" in holder:
                    break
                if router and not want_body:
                    break
                pg.wait_for_timeout(2000)
                pg.mouse.wheel(0, 800)  # 滚动戳一下,催页面发 aweme XHR
            if want_body:
                body = holder.get("body")
            return router, body
        finally:
            browser.close()


def _iso(ts) -> str | None:
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None


class DouyinAdapter(BaseAdapter):
    platform = "douyin"
    mode = "auto"

    def fetch(self, creator: dict, limit: int = 5) -> list[dict]:
        # 站长拍板(2026-10-16):抖音登录为准——未扫码=明确受阻提示,不赌免登录波动
        ck = identity.load_cookies("douyin") or {}
        if not ck.get("sessionid"):
            raise AdapterError("抖音需要扫码登录:右栏「身份与登录」→ 抖音 → 扫码登录,登录后自动抓最新")
        sec = creator["platform_uid"]
        try:
            _, body = _load_page_json(sec, want_body=True)
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(f"抖音页面渲染失败:{str(exc)[:120]}") from exc
        if not body:
            raise AdapterError("抖音 share 页未发出视频列表请求(风控波动,下一轮自动重试)")
        try:
            payload = json.loads(body)
        except Exception as exc:
            raise AdapterError(f"抖音响应解析失败:{str(exc)[:80]}") from exc
        awemes = payload.get("aweme_list") or []
        items = []
        for a in awemes[:limit]:
            desc = (a.get("desc") or "").strip() or f"抖音视频 {a.get('aweme_id')}"
            items.append({
                "item_id": str(a.get("aweme_id")),
                "title": desc[:100],
                "url": f"https://www.douyin.com/video/{a.get('aweme_id')}",
                "published_at": _iso(a.get("create_time")),
                "summary": "",
            })
        if not items:
            raise AdapterError("抖音返回空列表(该账号可能限制展示)")
        return items

    def creator_name(self, sec_uid: str) -> str | None:
        try:
            router, _ = _load_page_json(sec_uid, want_body=False)
            info = (((router or {}).get("loaderData") or {})
                    .get("user_(id)/page") or {}).get("userInfoRes") or {}
            name = ((info.get("user_info") or {}).get("nickname") or "").strip()
            return name or None
        except Exception:
            return None
