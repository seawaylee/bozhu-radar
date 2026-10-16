"""通知层:四通道可配置——站内通知中心(默认开)/ macOS 系统通知 / 飞书自定义 bot / 飞书自建应用。

设计要点(课程口径):
- 通知文案规范:博主名+平台+标题+分类标签+直达链接,一个不缺;
- 静默时段只记录不外发(deferred),第二天可在通知中心补看;
- 任一外部通道失败不影响入库记录,失败隔离同适配器哲学。
"""
from __future__ import annotations

import subprocess
from datetime import datetime, time as dtime

from . import config, db


def _now_hm() -> dtime:
    return datetime.now().time()


def in_silent_hours(cfg: dict | None = None) -> bool:
    cfg = cfg or config.load()
    start_s, end_s = (cfg.get("notify", {}).get("silent_hours") or ["23:00", "07:00"])
    h1, m1 = map(int, start_s.split(":"))
    h2, m2 = map(int, end_s.split(":"))
    start, end, now = dtime(h1, m1), dtime(h2, m2), _now_hm()
    if start <= end:
        return start <= now < end
    return now >= start or now < end  # 跨午夜窗口


def format_message(creator_name: str, platform_label: str, title: str, tag: str, url: str) -> str:
    lines = [f"🔔 新内容｜{creator_name} · {platform_label}", title]
    if tag:
        lines.append(f"分类 #{tag}")
    if url:
        lines.append(url)
    return "\n".join(lines)


def _channel_cfg(cfg: dict, name: str) -> dict:
    return (cfg.get("notify", {}).get("channels") or {}).get(name) or {}


def _send_macos(content: str) -> None:
    title = content.splitlines()[1] if content.count("\n") >= 1 else "新内容"
    head = content.splitlines()[0]
    safe_title = title.replace('"', "'")[:60]
    safe_head = head.replace("🔔 ", "").replace('"', "'")[:40]
    subprocess.run(
        ["osascript", "-e", f'display notification "{safe_title}" with title "{safe_head}" sound name "Glass"'],
        timeout=10, capture_output=True, check=False,
    )


def _send_feishu_bot(content: str, webhook: str) -> None:
    import requests
    s = requests.Session()
    s.trust_env = False
    s.post(webhook, json={"msg_type": "text", "content": {"text": content}}, timeout=15)


def notify_new(creator_name: str, platform_label: str, title: str, tag: str, url: str) -> dict:
    cfg = config.load()
    content = format_message(creator_name, platform_label, title, tag, url)
    deferred = in_silent_hours(cfg)
    active = ["inapp"]
    errors: list[str] = []
    if not deferred:
        mac = _channel_cfg(cfg, "macos")
        if mac.get("enabled"):
            active.append("macos")
            try:
                _send_macos(content)
            except Exception as exc:
                errors.append(f"macos:{exc}")
        bot = _channel_cfg(cfg, "feishu_bot")
        if bot.get("enabled") and bot.get("webhook"):
            active.append("feishu_bot")
            try:
                _send_feishu_bot(content, bot["webhook"])
            except Exception as exc:
                errors.append(f"feishu_bot:{exc}")
        app = _channel_cfg(cfg, "feishu_app")
        if app.get("enabled") and app.get("app_id") and app.get("open_id"):
            # 自建应用通道:教学版按「注册自建应用→建bot→tenant_access_token→im/v1 发消息」实现,
            # 演示环境未配置则跳过(占位如实展示,不假装发出)。
            errors.append("feishu_app:未配置(app_id/open_id)")
    db.add_notification(content, ",".join(active), deferred)
    return {"channels": active, "deferred": deferred, "errors": errors}


def send_test() -> dict:
    cfg = config.load()
    content = format_message("测试博主", "演示", "这是一条测试通知(演示版通道自检)", "知识", "")
    deferred = in_silent_hours(cfg)
    db.add_notification(content, "inapp,test", deferred)
    mac_err = ""
    if not deferred and _channel_cfg(cfg, "macos").get("enabled"):
        try:
            _send_macos(content)
        except Exception as exc:
            mac_err = str(exc)
    return {"deferred": deferred, "macos_error": mac_err}
