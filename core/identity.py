"""登录态管理:读 data/identity/<platform>.json(扫码登录产物)。

身份语义(课程口径):扫码只解决「以谁的名义去抓」(过风控),
抓谁永远由监控列表决定;登录态只存本机 demo/data/identity/,不进日志不外发。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import db

IDENT_DIR = Path(__file__).resolve().parents[1] / "data" / "identity"
SUPPORTED = ("douyin", "weibo", "xhs", "youtube", "bilibili")


def state_path(platform: str) -> Path:
    return IDENT_DIR / f"{platform}.json"


def write_status(platform: str, state: str, detail: str = "") -> None:
    """login_status_<platform>.json:面板「身份与登录」的等待/成功/超时状态(login_qr 同格式)。"""
    IDENT_DIR.mkdir(parents=True, exist_ok=True)
    (IDENT_DIR / f"login_status_{platform}.json").write_text(
        json.dumps({"state": state, "detail": detail,
                    "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}, ensure_ascii=False),
        encoding="utf-8")


def load_cookies(platform: str) -> dict[str, str] | None:
    path = IDENT_DIR / f"{platform}.json"
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        cookies = state.get("cookies") or []
        return {c["name"]: c["value"] for c in cookies if c.get("name") and c.get("value")}
    except Exception:
        return None


def cookie_header(platform: str) -> str:
    ck = load_cookies(platform) or {}
    return "; ".join(f"{k}={v}" for k, v in ck.items())


def info(platform: str) -> dict:
    path = state_path(platform)
    logged = path.exists()
    out = {"platform": platform, "logged_in": logged,
           "following_count": db.following_count(platform)}
    if logged:
        out["saved_at"] = datetime.fromtimestamp(path.stat().st_mtime).strftime("%m-%d %H:%M")
    status_file = IDENT_DIR / f"login_status_{platform}.json"
    if status_file.exists():
        try:
            st = json.loads(status_file.read_text(encoding="utf-8"))
            out["login_state"] = st.get("state")
            out["login_detail"] = st.get("detail", "")
            out["login_ts"] = st.get("ts", "")
        except Exception:
            pass
    return out


def info_all() -> list[dict]:
    return [info(p) for p in SUPPORTED]


# ---- B站扫码登录(纯 HTTP,二维码内嵌面板,不弹浏览器窗) ----
# 通道(2026-10-18 实测):api.bilibili.com/x/relation/followings 匿名 -101(必须登录),
# 走 passport.bilibili.com 网页版二维码协议:finger/spi 拿 buvid3 暖场 → generate 签发 → poll 轮询。
_BILI_POLL_MSG = {86101: "等待扫码", 86090: "已扫码,请在手机上确认登录",
                  86038: "二维码已过期,请重新生成"}


def _bili_session():
    from curl_cffi import requests as creq
    return creq.Session(impersonate="chrome")


def bilibili_qr_start() -> dict:
    """签发登录二维码。返回 {qrcode_key, qr_png(base64 data url)};qr_png 面板直接 <img>。"""
    import base64
    import io

    import qrcode
    s = _bili_session()
    spi = s.get("https://api.bilibili.com/x/frontend/finger/spi",
                headers={"Referer": "https://www.bilibili.com/"}, timeout=15).json()
    for name in ("b_3", "b_4"):  # buvid3/buvid4 指纹 cookie:缺它后续接口容易被风控拒
        v = (spi.get("data") or {}).get(name) or ""
        if v:
            s.cookies.set("buvid3" if name == "b_3" else "buvid4", v, domain=".bilibili.com")
    gen = s.get("https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
                headers={"Referer": "https://www.bilibili.com/"}, timeout=15).json()
    data = gen.get("data") or {}
    qr_url, key = data.get("url") or "", data.get("qrcode_key") or ""
    if not qr_url or not key:
        raise RuntimeError(f"B站二维码签发失败:{str(gen)[:120]}")
    write_status("bilibili", "waiting", "二维码已出,等待扫码")
    buf = io.BytesIO()
    qrcode.make(qr_url).save(buf, format="PNG")
    return {"qrcode_key": key,
            "qr_png": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}


def bilibili_qr_poll(qrcode_key: str) -> dict:
    """轮询扫码状态;成功后 cookie 存 data/identity/bilibili.json(storage_state 同构)。"""
    from urllib.parse import parse_qs, urlparse

    s = _bili_session()
    payload = s.get("https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
                    params={"qrcode_key": qrcode_key, "device_environment": "web"},
                    headers={"Referer": "https://www.bilibili.com/"}, timeout=15).json()
    data = payload.get("data") or {}
    code = data.get("code")
    if code != 0:
        return {"ok": False, "code": code, "message": _BILI_POLL_MSG.get(code, f"状态码 {code}")}
    cookie_url = data.get("url") or ""  # 成功时登录 cookie 全拼在这个 url 的查询参数里
    query = parse_qs(urlparse(cookie_url).query)
    cookies = [{"name": k, "value": v[0], "domain": ".bilibili.com", "path": "/"}
               for k, v in query.items() if v and v[0] and k != "gourl"]
    for name, value in s.cookies.items():  # 补 buvid3/4 暖场指纹 cookie,后续关注接口带上更稳
        if name in ("buvid3", "buvid4", "b_nut") and not any(x["name"] == name for x in cookies):
            cookies.append({"name": name, "value": value, "domain": ".bilibili.com", "path": "/"})
    if not any(c["name"] == "SESSDATA" for c in cookies):
        return {"ok": False, "code": -1, "message": "登录响应里没有 SESSDATA(异常,请重试)"}
    IDENT_DIR.mkdir(parents=True, exist_ok=True)
    state_path("bilibili").write_text(
        json.dumps({"cookies": cookies, "origins": []}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    write_status("bilibili", "ok", "扫码登录成功(纯 HTTP 通道)")
    return {"ok": True, "message": "登录成功"}
