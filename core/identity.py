"""登录态管理:读 data/identity/<platform>.json(扫码登录产物)。

身份语义(课程口径):扫码只解决「以谁的名义去抓」(过风控),
抓谁永远由监控列表决定;登录态只存本机 demo/data/identity/,不进日志不外发。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

IDENT_DIR = Path(__file__).resolve().parents[1] / "data" / "identity"
SUPPORTED = ("douyin", "weibo", "xhs", "youtube")


def state_path(platform: str) -> Path:
    return IDENT_DIR / f"{platform}.json"


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
    out = {"platform": platform, "logged_in": logged}
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
