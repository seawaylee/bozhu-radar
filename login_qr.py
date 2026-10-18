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
               "tip": "在弹出的窗口里点右上角「登录」→ 扫码(窗口会等到真登录完成才关闭)"},
    "weibo": {"url": "https://weibo.com/", "login_cookie": "SUBP",
              # ⚠️ 游客一打开页面就有 SUBP(2026-10-18 实测),只看 cookie 名会秒误判→窗口提前关+存下游客会话;
              # 必须叠加页面指纹:$CONFIG['uid'] 仅登录后有值(游客='')
              "verify": "() => { try { const u = String((window.$CONFIG||{})['uid']||''); "
                        "return u !== '' && u !== '0'; } catch(e) { return false; } }",
              "tip": "在弹出的窗口里点「扫码登录」,扫完等页面出现你的头像(游客 cookie 也会触发,窗口只认真登录)"},
    "xhs": {"url": "https://www.xiaohongshu.com/", "login_cookie": "web_session",
            # 游客无 web_session,但登录流程中间态可能短暂出现:叠加侧栏头像指纹 + 连续两次确认
            "verify": "() => !!document.querySelector('a[href*=\"/user/profile/\"]')",
            "tip": "在弹出的窗口里点「登录」→ 扫码,扫完等侧栏出现你的头像(窗口只认真登录)"},
    "youtube": {"url": "https://www.youtube.com/", "login_cookie": "SAPISID",
                "tip": "已改走主 Chrome 导入(此条目仅为兜底保留)",
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


def _looks_logged_in(ctx, page, conf) -> bool:
    """cookie 出现 + 平台页面指纹(verify_js)双条件;连续两次确认由调用方负责(防登录中间态)。"""
    try:
        values = {c["name"]: c.get("value") or "" for c in ctx.cookies()}
    except Exception:
        return False
    v = values.get(conf["login_cookie"]) or ""
    if not v or len(v) < 8:  # 空/短值当没有(游客态与中间态)
        return False
    js = conf.get("verify")
    if js:
        try:
            if not page.evaluate(js):
                return False
        except Exception:
            return False
    return True


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
        ok, streak = False, 0
        while time.time() < deadline:
            if _looks_logged_in(ctx, page, conf):
                streak += 1
                if streak >= 2:  # 连续两次(约4秒)才算数,躲登录流程的中间态
                    ok = True
                    break
            else:
                streak = 0
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
