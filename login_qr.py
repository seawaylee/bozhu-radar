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
            # ⚠️ 头像链接指纹会误判(2026-10-19 实锤:点「登录」弹窗后页面就有 /user/profile/ 链接,
            # 两次秒判"登录成功"关窗,用户还没扫码)。地面真值=页内问 /user/me:游客必返 guest:true。
            "verify": "async () => { try { const r = await fetch('/api/sns/web/v2/user/me', "
                      "{headers: {'Accept': 'application/json'}}); const d = await r.json(); "
                      "const u = d.data || {}; return u.guest === false && !!u.user_id; "
                      "} catch(e) { return false } }",
            "tip": "在弹出的窗口里点「登录」→ 扫码并在手机上确认,窗口只认真登录"},
    "bilibili": {"url": "https://www.bilibili.com/", "login_cookie": "SESSDATA",
                 # SESSDATA/DedeUserID 都是登录后才有的 cookie(游客没有);双 cookie 互为指纹
                 "also_cookie": "DedeUserID",
                 "tip": "在弹出的窗口里点右上角「登录」→ 扫码,扫完等右上角出现你的头像"},
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
    """cookie 出现(+附加登录专属 cookie)+ 平台页面指纹(verify_js);连续两次确认由调用方负责。"""
    try:
        values = {c["name"]: c.get("value") or "" for c in ctx.cookies()}
    except Exception:
        return False
    v = values.get(conf["login_cookie"]) or ""
    if not v or len(v) < 8:  # 空/短值当没有(游客态与中间态)
        return False
    also = conf.get("also_cookie")
    if also and not values.get(also):  # 登录专属 cookie(如 B站 DedeUserID),游客没有
        return False
    js = conf.get("verify")
    if js:
        try:
            if not page.evaluate(js):
                return False
        except Exception:
            return False
    return True


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


def weibo_from_chrome() -> int:
    """微博专用:扫码新会话会被软风控(实测养两天不解),走主 Chrome 登录导入成熟会话
    (wu2198 同款;游客 cookie 判不了登录,起无头真浏览器验 $CONFIG.uid 坐实)。"""
    sys.path.insert(0, str(BASE))
    from core import chrome_import
    write_status("weibo", "waiting",
                 "已打开你的主 Chrome:在里面登录微博(账号密码/短信验证码都行),登录着就行——成熟会话比扫码稳")
    print("[weibo] 用主 Chrome 导入登录态:已打开 weibo.com,请在里面登录微博…")
    ok, n, uid = chrome_import.weibo_wait_and_import(TIMEOUT_SEC)
    if ok:
        write_status("weibo", "ok", f"从主 Chrome 导入成熟登录态({n} 个 cookie,uid={uid})")
        print(f"[weibo] ✓ 导入成功({n} 个 cookie,uid={uid}) → data/identity/weibo.json")
        return 0
    write_status("weibo", "timeout", "超时没在 Chrome 里登录微博(登录后重点「扫码登录」重新导入)")
    print("[weibo] ✗ 超时")
    return 1


def _safe_goto(page, url: str) -> bool:
    """页面打开带降级:先等 domcontentloaded(45s),超时就降级只等导航开始(commit)+8s 静置。
    小红书等站风控会挂起页面让 domcontentloaded 永不触发(2026-10-19 实测闪退根因)。"""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        return True
    except Exception:
        pass
    try:
        page.goto(url, wait_until="commit", timeout=20000)
        page.wait_for_timeout(8000)  # 页面往往已渲染出内容,够用户操作
        return True
    except Exception as exc:
        print(f"[goto] {url} 打不开:{str(exc)[:100]}")
        return False


def _qr_flow(platform: str, conf: dict) -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--no-proxy-server"])
        ctx = browser.new_context(viewport={"width": 1100, "height": 800})
        page = ctx.new_page()
        if not _safe_goto(page, conf["url"]):
            browser.close()
            write_status(platform, "error", "页面打不开(网络波动或平台风控),稍后再试一次")
            print(f"[{platform}] ✗ 页面打不开")
            return 1
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


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("douyin", "weibo", "xhs", "youtube", "bilibili"):
        print("用法: python login_qr.py <douyin|weibo|xhs|youtube|bilibili>")
        return 2
    platform = sys.argv[1]
    if platform == "youtube":
        return youtube_from_chrome()
    if platform == "weibo":
        return weibo_from_chrome()
    conf = PLATFORMS[platform]
    write_status(platform, "waiting", conf["tip"])
    print(f"[{platform}] 打开浏览器:{conf['url']}  → {conf['tip']}")
    try:
        return _qr_flow(platform, conf)
    except Exception as exc:  # 任何异常都不能让窗口无声消失+状态永挂 waiting
        write_status(platform, "error", f"登录窗口异常退出:{str(exc)[:60]}")
        print(f"[{platform}] ✗ 异常:{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
