"""出网工具层:浏览器指纹会话(curl_cffi) + yt-dlp 子进程。

实测结论(2026-10-16,本机):
- 裸 requests 的 TLS 指纹会被 B站/微博/YouTube feeds 一律拒绝(-412/432/404);
- curl_cffi impersonate chrome 能过 B站主页、YouTube 频道页;
- 内容枚举最稳的路是 yt-dlp flat-playlist(YouTube /videos 走代理,B站空间直连)。
这层把「直连/代理」的选择收拢成一个参数,适配器不关心代理细节。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from curl_cffi import requests as creq

YTDLP = str(Path(sys.executable).with_name("yt-dlp"))
if not Path(YTDLP).exists():
    YTDLP = shutil.which("yt-dlp") or "yt-dlp"

_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


def new_session(proxies: dict | None = None) -> creq.Session:
    s = creq.Session(impersonate="chrome")
    if proxies:
        s.proxies = proxies
    return s


def run_ytdlp(url: str, *, use_proxy: str | None = None, playlist_end: int = 5,
              print_fmt: str = "%(id)s|%(title)s", timeout: int = 120,
              impersonate: str | None = None, referer: str | None = None) -> list[str]:
    """跑 yt-dlp flat-playlist,返回 print 行。use_proxy 传代理地址则走代理,否则剥离代理直连。
    impersonate/referer:big_v 同款参数(B站空间需要,缺了易被风控拒)。"""
    env = {k: v for k, v in os.environ.items() if k.upper() not in _PROXY_KEYS and k not in _PROXY_KEYS}
    if use_proxy:
        env["HTTPS_PROXY"] = use_proxy
        env["HTTP_PROXY"] = use_proxy
    cmd = [YTDLP, "--flat-playlist", "--playlist-end", str(playlist_end), "--no-warnings",
           "--print", print_fmt]
    if impersonate:
        cmd += ["--impersonate", impersonate]
    if referer:
        cmd += ["--referer", referer]
    cmd.append(url)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    if proc.returncode != 0 and not lines:
        raise RuntimeError((proc.stderr or "yt-dlp 无输出").strip().splitlines()[-1][:200])
    return lines
