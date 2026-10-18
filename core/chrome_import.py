"""从用户主 Chrome 导入登录态(2026-10-18 站长拍板「为什么不用我的主 Chrome」)。

背景:①Google 登录页拦自动化浏览器(Playwright 窗口报「此浏览器或应用可能不安全」);
②微博扫码新会话被软风控——日常浏览器里的成熟登录态才是稳路(wu2198 同款)。
所以 YouTube/微博 都走「主 Chrome 导入」:直接读 Chrome 里现成的站点 cookie。
- macOS Chrome 的 cookie 值用 Keychain「Chrome Safe Storage」密钥 AES-128-CBC 加密(v10 前缀);
- 全程本机解密,只写 data/identity/<platform>.json,不外发不进日志。
前置:运行进程要有 Chrome 目录的磁盘访问权限;首次读 Keychain 可能要点一次「允许」。
注意:Chrome 里的微博若是**未登录**的游客态,导出来也只是成熟游客 cookie(2026-10-18 实测
$CONFIG.uid 为空)——需要用户先在自己的 Chrome 里登录微博,再导入。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
IDENT_DIR = BASE / "data" / "identity"
CHROME_ROOT = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"


def _cookie_dbs() -> list[Path]:
    out = []
    if CHROME_ROOT.exists():
        default = CHROME_ROOT / "Default" / "Cookies"
        if default.exists():
            out.append(default)
        for p in sorted(CHROME_ROOT.glob("Profile */Cookies")):
            out.append(p)
    return out


def _keychain_password() -> str:
    r = subprocess.run(["security", "find-generic-password", "-w", "-s", "Chrome Safe Storage"],
                       capture_output=True, text=True, timeout=10)
    pw = (r.stdout or "").strip()
    if not pw:
        raise RuntimeError("读不到 Chrome Safe Storage(钥匙串):请点系统弹窗的「允许」,或先解锁钥匙串")
    return pw


def _aes_key() -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    return PBKDF2HMAC(algorithm=hashes.SHA1(), length=16, salt=b"saltysalt",
                      iterations=1003).derive(_keychain_password().encode("utf-8"))


def _decrypt(blob: bytes, key: bytes) -> str:
    if not blob:
        return ""
    prefix = blob[:3]
    if prefix not in (b"v10", b"v11"):
        try:
            return blob.decode("utf-8")
        except Exception:
            return ""
    if prefix != b"v10":
        raise RuntimeError(f"Chrome cookie 加密版本 {prefix.decode(errors='replace')},本模块只处理 v10")
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    dec = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
    plain = dec.update(blob[3:]) + dec.finalize()
    pad = plain[-1]
    if 1 <= pad <= 16:
        plain = plain[:-pad]
    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError:
        return plain[32:].decode("utf-8", errors="replace")


def _chrome_epoch_to_unix(v) -> int:
    try:
        v = int(v)
    except (TypeError, ValueError):
        return -1
    if v <= 0:
        return -1
    return int(v / 1_000_000) - 11644473600


def extract_domain_cookies(host_suffix: str = "youtube.com",
                           skip_names: tuple[str, ...] = ("XSRF-TOKEN", "X-CSRF-TOKEN")) -> list[dict]:
    """读主 Chrome 全部 profile 指定域的 cookie(解密),返回 storage_state 的 cookies 列表。
    XSRF 类 token 与请求绑定,拼旧值反而坏事,默认跳过。"""
    dbs = _cookie_dbs()
    if not dbs:
        raise RuntimeError("找不到 Chrome 的 Cookies 数据库(装的是别的浏览器?)")
    key = _aes_key()
    cookies: dict[str, dict] = {}
    for db in dbs:
        try:
            with tempfile.TemporaryDirectory() as td:
                copy = Path(td) / "Cookies"
                shutil.copy2(db, copy)
                conn = sqlite3.connect(f"file:{copy}?immutable=1", uri=True)
                rows = conn.execute(
                    "SELECT host_key,name,encrypted_value,expires_utc,is_secure,is_httponly,path "
                    "FROM cookies WHERE host_key LIKE ?", (f"%{host_suffix}",)).fetchall()
                conn.close()
        except PermissionError as exc:
            raise RuntimeError("没有权限读 Chrome 目录:系统设置 → 隐私与安全性 → 磁盘访问权限,"
                               "给运行本服务的终端(ZCode/Terminal)打开后重试") from exc
        for host, name, enc, exp, secure, httponly, path in rows:
            if name in skip_names:
                continue
            try:
                value = _decrypt(bytes(enc), key)
            except RuntimeError:
                raise
            except Exception:
                continue
            if not value:
                continue
            cookies[f"{host}|{name}"] = {
                "name": name, "value": value,
                "domain": host if host.startswith(".") else f".{host}",
                "path": path or "/", "secure": bool(secure), "httpOnly": bool(httponly),
                "expires": _chrome_epoch_to_unix(exp),
            }
    return list(cookies.values())


def save_state(platform: str, cookies: list[dict]) -> None:
    IDENT_DIR.mkdir(parents=True, exist_ok=True)
    (IDENT_DIR / f"{platform}.json").write_text(
        json.dumps({"cookies": cookies, "origins": []}, ensure_ascii=False, indent=2),
        encoding="utf-8")


def extract_youtube_cookies() -> list[dict]:
    return extract_domain_cookies("youtube.com")


def verify_weibo_uid(cookies: list[dict]) -> str:
    """起无头真浏览器灌入候选 cookie,读 weibo.com 的 $CONFIG.uid——游客为空串,登录非空。
    (cookie 名判断不可靠:游客也有 SUB/SUBP/SCF 全套,2026-10-18 实测)"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        ctx = b.new_context(storage_state={"cookies": cookies, "origins": []},
                            viewport={"width": 1280, "height": 850})
        pg = ctx.new_page()
        pg.goto("https://weibo.com/", wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(7000)
        uid = pg.evaluate("() => { try { return String((window.$CONFIG||{})['uid']||'') } catch(e) { return '' } }")
        b.close()
    return uid or ""


def wait_and_import(timeout_sec: int = 240) -> tuple[bool, int]:
    """YouTube:打开主 Chrome 到 YouTube(登录着就什么都不用做),轮询读 cookie 直到出现 SAPISID。"""
    subprocess.Popen(["open", "-a", "Google Chrome", "https://www.youtube.com/"])
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            cookies = extract_youtube_cookies()
        except RuntimeError as exc:
            print(f"[youtube] {exc}")
            return False, 0
        if any(c["name"] == "SAPISID" for c in cookies):
            save_state("youtube", cookies)
            return True, len(cookies)
        time.sleep(3)
    return False, 0


def weibo_wait_and_import(timeout_sec: int = 240) -> tuple[bool, int, str]:
    """微博:打开主 Chrome 到 weibo.com,用户在里面登录(比扫码会话成熟);
    轮询读 cookie + 无头真浏览器验 uid,登录态坐实才存。"""
    subprocess.Popen(["open", "-a", "Google Chrome", "https://weibo.com/"])
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            cookies = extract_domain_cookies("weibo.com")
        except RuntimeError as exc:
            print(f"[weibo] {exc}")
            return False, 0, ""
        if cookies and any(c["name"] == "SUB" for c in cookies):
            uid = verify_weibo_uid(cookies)
            if uid:
                save_state("weibo", cookies)
                return True, len(cookies), uid
        time.sleep(4)
    return False, 0, ""
