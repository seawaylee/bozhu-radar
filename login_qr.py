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
    "bilibili": {"url": "https://www.bilibili.com/", "login_cookie": "SESSDATA",
                 # SESSDATA/DedeUserID 都是登录后才有的 cookie(游客没有);双 cookie 互为指纹
                 "also_cookie": "DedeUserID",
                 "tip": "在弹出的窗口里点右上角「登录」→ 扫码,扫完等右上角出现你的头像"},
}
# weibo/xhs/youtube 不走弹窗:统一「主 Chrome 登录导入」(chrome_import)
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


def xhs_from_chrome() -> int:
    """小红书终定性(2026-10-19 深夜实测穷举):①弹窗扫码——手机显示成功,web 会话不下发;
    ②Chrome cookie 导入——会话与设备指纹绑定,带进 Playwright 即「安全限制」页+guest
    (丢掉 a1/webId 重铸设备 id 也一样)。平台级风控,自动登录暂不可用,属进阶内容。"""
    write_status("xhs", "stale",
                 "平台风控受限:扫码会话不下发、Chrome 导入被设备指纹拦(实测穷举)——自动登录暂不可用,属进阶内容")
    print("[xhs] 平台风控受限,自动登录暂不可用(扫码不下发/Chrome 导入被指纹拦)")
    return 1


def gzh_qr_flow() -> int:
    """公众号(微信读书通道)扫码·自驱动版(2026-10-19 重做)。
    旧版开 weread.qq.com 页面等它自己登录:站长扫完码,240s 判定全程不过且零诊断
    信号。新版按 werss 成熟配方自己驱动:getLoginUid → 本地二维码(编码
    /web/confirm?uid=) → 长轮询 getLoginInfo → 多候选组合 + /web/login/renewal
    用 wr_rt 换有效 wr_skey(新版 8 字符短 skey 常被 -2012 拒) → shelf/sync
    地面真值收口。每步都有服务端回执,失败能说清死在哪。"""
    import base64
    from io import BytesIO
    from urllib.parse import quote

    import requests as rq
    import qrcode

    ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36")
    s = rq.Session()
    s.trust_env = False  # weread 国内直连,不走系统代理
    s.headers.update({"User-Agent": ua, "Accept": "application/json, text/plain, */*",
                      "Accept-Language": "zh-CN,zh;q=0.9",
                      "Origin": "https://weread.qq.com", "Referer": "https://weread.qq.com/"})

    write_status("gzh", "waiting", "已弹出二维码:微信扫一扫登录微信读书(不需要公众号后台),完成后自动收窗")
    print("[gzh] 自驱动扫码:取登录码…")

    # 1) 登录 uid
    try:
        d = s.get("https://weread.qq.com/api/auth/getLoginUid", timeout=20).json()
    except Exception as exc:
        write_status("gzh", "error", f"微信读书接口不通:{str(exc)[:60]}")
        print(f"[gzh] ✗ getLoginUid 异常:{exc}")
        return 1
    uid = str(d.get("uid") or (d.get("data") or {}).get("uid") or "")
    if not uid:
        write_status("gzh", "error", "微信读书没下发登录码,稍后再试一次")
        print(f"[gzh] ✗ getLoginUid 无 uid:{str(d)[:100]}")
        return 1
    print(f"[gzh] uid={uid}")

    # 2) 本地生成二维码(编码手机确认页;不依赖外部页面,永不白屏)
    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(f"https://weread.qq.com/web/confirm?uid={uid}")
    qr.make(fit=True)
    buf = BytesIO()
    qr.make_image().save(buf, format="PNG")
    qr_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    html = """<!doctype html><html><head><meta charset="utf-8"><title>公众号登录</title>
<style>
body{font-family:-apple-system,'PingFang SC',sans-serif;margin:0;display:flex;align-items:center;
justify-content:center;height:100vh;background:#f6f7f9}
.card{background:#fff;border-radius:14px;padding:26px 30px;box-shadow:0 2px 12px rgba(0,0,0,.08);text-align:center}
h1{font-size:16px;margin:0 0 6px;color:#24160f}
p{color:#8a7a6a;font-size:13px;margin:4px 0 12px}
img{width:240px;height:240px}
#st{margin-top:12px;font-size:14px;color:#666;font-weight:600}
</style></head><body><div class="card">
<h1>公众号登录 · 微信扫一扫</h1>
<p>登录的是「微信读书」(用你的微信),不需要公众号后台</p>
<img src="__QR__"><div id="st">等待扫码…(完成后窗口自动关闭)</div>
</div></body></html>""".replace("__QR__", qr_uri)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--no-proxy-server"])
        page = browser.new_page(viewport={"width": 480, "height": 580})
        page.set_content(html)

        def set_st(text: str, color: str = "#666"):
            try:
                page.evaluate("([t,c])=>{const e=document.getElementById('st');e.textContent=t;e.style.color=c}",
                              [text, color])
            except Exception:
                pass  # 用户提前关窗:页面没了不打紧,轮询照常走到超时

        # 3) 长轮询登录结果(接口单次可挂起 ~70s 属正常)
        print("[gzh] 等待扫码(240s)…")
        deadline = time.time() + TIMEOUT_SEC
        login = None
        while time.time() < deadline:
            try:
                r = s.get("https://weread.qq.com/api/auth/getLoginInfo",
                          params={"uid": uid, "otp": ""}, timeout=70)
                d = r.json()
            except Exception:
                time.sleep(1)  # 长轮询超时/网络抖动,接着问
                continue
            inner = d.get("data") or {}
            if d.get("succeed") or inner.get("succeed"):
                login = {
                    "vid": str(d.get("webLoginVid") or d.get("vid") or d.get("userVid")
                              or inner.get("webLoginVid") or inner.get("vid") or inner.get("userVid") or ""),
                    "access": str(d.get("accessToken") or d.get("token")
                                  or inner.get("accessToken") or inner.get("token") or ""),
                    "refresh": str(d.get("refreshToken") or inner.get("refreshToken") or ""),
                }
                break
            # logicCode == LOGIN_TIMEOUT = 还没扫,继续等(papers3-weread 同款语义)
            time.sleep(1)

        if not login:
            write_status("gzh", "timeout", "超时未扫码,可重试")
            print("[gzh] ✗ 超时未扫码")
            set_st("✗ 超时未扫码", "#c0392b")
            page.wait_for_timeout(3000)
            browser.close()
            return 1

        print(f"[gzh] 扫码确认成功 vid={login['vid']} access_len={len(login['access'])} rt_len={len(login['refresh'])}")
        set_st("已确认,正在铸登录态…", "#07c160")

        # 4) 构造多候选 cookie 组合(werss v5 配方:短 skey 常被拒,要靠 wr_rt 续期)
        def _verify(ck: dict) -> bool:
            hdr = {"Cookie": "; ".join(f"{k}={v}" for k, v in ck.items()),
                   "User-Agent": ua, "Accept": "application/json, text/plain, */*",
                   "Origin": "https://weread.qq.com", "Referer": "https://weread.qq.com/"}
            try:
                j = rq.get("https://weread.qq.com/web/shelf/sync",
                           params={"userVid": "", "synckey": 0, "lectureSynckey": 0},
                           headers=hdr, timeout=20, proxies={"http": None, "https": None}).json()
                print(f"[gzh] shelf/sync errCode={j.get('errCode')}")
                return j.get("errCode") == 0
            except Exception as exc:
                print(f"[gzh] shelf/sync 异常:{str(exc)[:60]}")
                return False

        def _renew(ck: dict) -> dict | None:
            """用 wr_rt 换新 wr_skey。cookie 必须注入 jar(domain=weread.qq.com),
            否则服务端当游客处理返回 -2013(werss 生产踩过)。"""
            rs = rq.Session()
            rs.trust_env = False
            rs.headers.update({"User-Agent": ua, "Accept": "application/json, text/plain, */*",
                               "Accept-Language": "zh-CN,zh;q=0.9",
                               "Origin": "https://weread.qq.com", "Referer": "https://weread.qq.com/",
                               "Content-Type": "application/json"})
            for k, v in ck.items():
                rs.cookies.set(k, v, domain="weread.qq.com", path="/")
            try:
                resp = rs.post("https://weread.qq.com/web/login/renewal",
                               data=json.dumps({"rq": "%2Fweb%2Fbook%2Fread", "ql": True},
                                               separators=(",", ":")), timeout=20)
            except Exception as exc:
                print(f"[gzh] renewal 异常:{str(exc)[:60]}")
                return None
            upd, got = dict(ck), False
            for c in rs.cookies:  # requests 已把 Set-Cookie 并进 jar
                if c.name in ("wr_skey", "wr_vid", "wr_rt") and c.value:
                    upd[c.name] = c.value
                    got = True
            if not got:
                try:
                    j = resp.json()
                    for f in ("wr_skey", "skey", "token"):
                        if isinstance(j.get(f), str) and j[f]:
                            upd["wr_skey"] = j[f]
                            got = True
                            break
                except Exception:
                    pass
            print(f"[gzh] renewal {'✓' if got else '✗'} http={resp.status_code}")
            return upd if got else None

        base = {c.name: c.value or "" for c in s.cookies}
        if login["vid"] and not base.get("wr_vid"):
            base["wr_vid"] = login["vid"]
        rt = login["refresh"]
        rt_q = quote(rt, safe="") if rt else ""
        skey_cands = [x for x in dict.fromkeys([login["access"], rt, base.get("wr_skey") or ""]) if x]
        rt_cands = [x for x in dict.fromkeys([rt_q, rt, base.get("wr_rt") or ""]) if x]
        base_nt = {k: v for k, v in base.items() if k not in ("wr_skey", "wr_rt")}
        combos = [dict(base)]  # 原始 Set-Cookie jar 恒第一(werss 实测常直接可用)
        for sk in skey_cands:
            combos.append({**base_nt, "wr_skey": sk})
            for rk in rt_cands:
                combos.append({**base_nt, "wr_skey": sk, "wr_rt": rk})
        for rk in rt_cands:
            combos.append({**base_nt, "wr_rt": rk})
        seen, cands = set(), []
        for c in combos:
            key = tuple(sorted(c.items()))
            if key not in seen:
                seen.add(key)
                cands.append(c)

        # 5) 逐组合:直接验证 → 失败且带 wr_rt 就续期再验
        final = None
        for i, cand in enumerate(cands):
            if _verify(cand):
                final = cand
                break
            if cand.get("wr_rt"):
                renewed = _renew(cand)
                if renewed and _verify(renewed):
                    final = renewed
                    break
            print(f"[gzh] 组合{i + 1}/{len(cands)} 未过,试下一组…")

        if not final:
            write_status("gzh", "error", "扫码成功但登录态没铸成(微信读书拒绝了令牌),稍后重扫一次")
            print("[gzh] ✗ 全部组合验证失败")
            set_st("✗ 登录态未铸成,请重试", "#c0392b")
            page.wait_for_timeout(4000)
            browser.close()
            return 1

        state = {"cookies": [{"name": k, "value": v, "domain": ".weread.qq.com", "path": "/",
                              "expires": -1, "httpOnly": False, "secure": True, "sameSite": "Lax"}
                             for k, v in final.items()],
                 "origins": []}
        IDENT_DIR.mkdir(parents=True, exist_ok=True)
        (IDENT_DIR / "gzh.json").write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        write_status("gzh", "ok", f"登录成功,身份已保存(微信读书 vid={login['vid'] or '?'})")
        print(f"[gzh] ✓ 登录成功 vid={login['vid']} cookies={len(final)} → data/identity/gzh.json")
        set_st("✓ 登录成功,窗口即将自动关闭", "#07c160")
        page.wait_for_timeout(2500)
        browser.close()
    return 0


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
        if platform == "bilibili":  # buvid3/4 暖场:裸指纹首访 bilibili 首页会被风控挂起→白屏(2026-10-19)
            try:
                from curl_cffi import requests as creq
                spi = creq.Session(impersonate="chrome").get(
                    "https://api.bilibili.com/x/frontend/finger/spi",
                    headers={"Referer": "https://www.bilibili.com/"}, timeout=10).json()
                d = spi.get("data") or {}
                for src, name in (("b_3", "buvid3"), ("b_4", "buvid4")):
                    if d.get(src):
                        ctx.add_cookies([{"name": name, "value": d[src],
                                          "domain": ".bilibili.com", "path": "/"}])
            except Exception:
                pass
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
    if len(sys.argv) != 2 or sys.argv[1] not in ("douyin", "weibo", "xhs", "youtube", "bilibili", "gzh"):
        print("用法: python login_qr.py <douyin|weibo|xhs|youtube|bilibili|gzh>")
        return 2
    platform = sys.argv[1]
    if platform == "youtube":
        return youtube_from_chrome()
    if platform == "weibo":
        return weibo_from_chrome()
    if platform == "xhs":
        return xhs_from_chrome()
    conf = PLATFORMS[platform]
    write_status(platform, "waiting", conf["tip"])
    print(f"[{platform}] 打开浏览器:{conf['url']}  → {conf['tip']}")
    try:
        if platform == "gzh":
            return gzh_qr_flow()
        return _qr_flow(platform, conf)
    except Exception as exc:  # 任何异常都不能让窗口无声消失+状态永挂 waiting
        write_status(platform, "error", f"登录窗口异常退出:{str(exc)[:60]}")
        print(f"[{platform}] ✗ 异常:{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
