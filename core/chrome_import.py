"""从用户主 Chrome 导入 YouTube 登录态(2026-10-18 站长拍板「为什么不用我的主chrome」)。

背景:Google 登录页拦自动化浏览器——Playwright 弹窗里登录 Google 会报
「此浏览器或应用可能不安全」,弹窗通道对 YouTube 走不通。
改法:直接读主 Chrome 里现成的 youtube.com cookie(通常本来就登录着):
- macOS Chrome 的 cookie 值用 Keychain「Chrome Safe Storage」密钥 AES-128-CBC 加密(v10 前缀);
- 全程本机解密,只写 data/identity/youtube.json,不外发不进日志。
前置:运行进程要有 Chrome 目录的磁盘访问权限(macOS 隐私与安全性);
首次读 Keychain 系统可能弹一次「允许」。
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
        # 老版 Chrome 明文存 utf-16 / 未加密
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
    # 部分新版本解密后带 32 字节 SHA256 摘要前缀:值不是合法 utf-8 就剥掉重试
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


def extract_youtube_cookies(want_names: set[str] | None = None) -> list[dict]:
    """读主 Chrome 全部 profile 的 youtube.com cookie(解密),返回 storage_state 的 cookies 列表。"""
    dbs = _cookie_dbs()
    if not dbs:
        raise RuntimeError("找不到 Chrome 的 Cookies 数据库(装的是别的浏览器?)")
    key = _aes_key()
    names = want_names or None
    cookies: dict[str, dict] = {}  # name+domain 去重,多 profile 取后见的
    for db in dbs:
        try:
            with tempfile.TemporaryDirectory() as td:
                copy = Path(td) / "Cookies"
                shutil.copy2(db, copy)  # Chrome 运行中也能读副本,避开库锁
                conn = sqlite3.connect(f"file:{copy}?immutable=1", uri=True)
                rows = conn.execute(
                    "SELECT host_key,name,encrypted_value,expires_utc,is_secure,is_httponly,path "
                    "FROM cookies WHERE host_key LIKE '%youtube.com'").fetchall()
                conn.close()
        except PermissionError as exc:
            raise RuntimeError("没有权限读 Chrome 目录:系统设置 → 隐私与安全性 → 磁盘访问权限,"
                               "给运行本服务的终端(ZCode/Terminal)打开后重试") from exc
        for host, name, enc, exp, secure, httponly, path in rows:
            if names and name not in names:
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


def save_youtube_state(cookies: list[dict]) -> None:
    IDENT_DIR.mkdir(parents=True, exist_ok=True)
    (IDENT_DIR / "youtube.json").write_text(
        json.dumps({"cookies": cookies, "origins": []}, ensure_ascii=False, indent=2),
        encoding="utf-8")


def wait_and_import(timeout_sec: int = 240) -> tuple[bool, int]:
    """打开主 Chrome 到 YouTube(登录着就什么都不用做),轮询读 cookie 直到出现 SAPISID。"""
    subprocess.Popen(["open", "-a", "Google Chrome", "https://www.youtube.com/"])
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            cookies = extract_youtube_cookies()
        except RuntimeError as exc:
            print(f"[youtube] {exc}")
            return False, 0
        if any(c["name"] == "SAPISID" for c in cookies):
            save_youtube_state(cookies)
            return True, len(cookies)
        time.sleep(3)
    return False, 0
