#!/usr/bin/env python3
"""扫码登录:弹一个真浏览器窗口,用户点登录→扫码,成功后存登录态。

用法: python login_qr.py <douyin|weibo|xhs>
产物: data/identity/<platform>.json(Playwright storage_state,含 cookies)
状态: data/identity/login_status_<platform>.json(waiting/ok/timeout)
说明: 这是交互式操作(人坐在屏幕前扫码),不属于「定时任务弹窗」禁区;
      存的是学员自己的身份,只用于「以谁的名义抓」,抓谁由监控列表决定。
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = Path(__file__).resolve().parent
IDENT_DIR = BASE / "data" / "identity"

PLATFORMS = {
    "douyin": {"url": "https://www.douyin.com/", "login_cookie": "sessionid",
               "tip": "在弹出的窗口里点右上角「登录」→ 扫码"},
    "weibo": {"url": "https://weibo.com/", "login_cookie": "SUBP",
              "tip": "在弹出的窗口里点「扫码登录」(弹窗默认就是二维码)"},
    "xhs": {"url": "https://www.xiaohongshu.com/", "login_cookie": "web_session",
            "tip": "在弹出的窗口里点「登录」→ 扫码"},
    "youtube": {"url": "https://www.youtube.com/", "login_cookie": "SAPISID",
                "tip": "在弹出的窗口里登录你的 Google 账号(邮箱密码/手机一键登录,不一定是二维码)",
                "proxy": True},
}
TIMEOUT_SEC = 240


def write_status(platform: str, state: str, detail: str = "") -> None:
    IDENT_DIR.mkdir(parents=True, exist_ok=True)
    (IDENT_DIR / f"login_status_{platform}.json").write_text(
        json.dumps({"state": state, "detail": detail, "ts": datetime.now().isoformat(timespec="seconds")},
                   ensure_ascii=False), encoding="utf-8")


def youtube_from_chrome() -> int:
    """YouTube 专用:不用弹窗——Google 登录页拦自动化浏览器(「此浏览器或应用可能不安全」),
    直接从用户主 Chrome 读现成的 youtube.com cookie(2026-10-18 站长拍板)。"""
    sys.path.insert(0, str(BASE))
    from core import chrome_import
    write_status("youtube", "waiting",
                 "已打开你的主 Chrome:确认 YouTube 是登录状态即可(通常已登录;首次可能要允许一次钥匙串)")
    print("[youtube] 用主 Chrome 导入登录态:已打开 youtube.com,登录着就行,什么都不用点…")
    ok, n = chrome_import.wait_and_import(TIMEOUT_SEC)
    if ok:
        write_status("youtube", "ok", f"从主 Chrome 导入登录态({n} 个 cookie)")
        print(f"[youtube] ✓ 导入成功({n} 个 cookie) → data/identity/youtube.json")
        return 0
    write_status("youtube", "timeout", "超时没读到登录 cookie(在 Chrome 里登录 YouTube 后重试)")
    print("[youtube] ✗ 超时")
    return 1


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in PLATFORMS:
        print("用法: python login_qr.py <douyin|weibo|xhs|youtube>")
        return 2
    platform = sys.argv[1]
    if platform == "youtube":
        return youtube_from_chrome()
    conf = PLATFORMS[platform]
    write_status(platform, "waiting", conf["tip"])
    print(f"[{platform}] 打开浏览器:{conf['url']}  → {conf['tip']}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--no-proxy-server"])
        ctx_kwargs = {"viewport": {"width": 1100, "height": 800}}
        if conf.get("proxy"):  # YouTube 需要走梯子(本机 Clash)
            import yaml
            cfgp = BASE / "config.yaml"
            try:
                proxy = (yaml.safe_load(cfgp.read_text(encoding="utf-8")).get("proxy") or {}).get("http", "")
            except Exception:
                proxy = ""
            if proxy:
                ctx_kwargs["proxy"] = {"server": proxy}
        ctx = browser.new_context(**ctx_kwargs)
        page = ctx.new_page()
        page.goto(conf["url"], wait_until="domcontentloaded", timeout=45000)
        deadline = time.time() + TIMEOUT_SEC
        ok = False
        while time.time() < deadline:
            try:
                names = {c["name"] for c in ctx.cookies()}
            except Exception:
                names = set()
            if conf["login_cookie"] in names:
                ok = True
                break
            time.sleep(2)
        if ok:
            # 暖场:登录成功后访问一次平台主页,让主域 cookie 落全、会话成熟(防"新会话软风控")
            try:
                page.goto(conf["url"], wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(4000)
            except Exception:
                pass
            IDENT_DIR.mkdir(parents=True, exist_ok=True)
            ctx.storage_state(path=str(IDENT_DIR / f"{platform}.json"))
            write_status(platform, "ok", "登录成功,身份已保存")
            print(f"[{platform}] ✓ 登录成功,身份已保存 → data/identity/{platform}.json")
        else:
            write_status(platform, "timeout", "超时未扫码,可重试")
            print(f"[{platform}] ✗ 超时未扫码")
        browser.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
