#!/usr/bin/env python3
"""博主雷达 · 多平台主播监控演示版——零框架 web 服务(标准库 http.server)。

启动: python3 app.py
访问: http://127.0.0.1:18650
原则: 独立项目,零生产引用;数据只落 demo/data/。
"""
from __future__ import annotations

import json
import queue
import re
import subprocess
import sys
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import classify, config, db, events, following, identity, notify, seeds, wizard  # noqa: E402
from core.model import PLATFORMS, PLATFORM_ORDER  # noqa: E402
from core.scanner import Scanner  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
SCANNER = Scanner()
STARTED_AT = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

MODE_LABEL = {"auto": "全自动", "semi": "半自动"}


class Handler(BaseHTTPRequestHandler):
    server_version = "BozhuRadar/1.1"
    protocol_version = "HTTP/1.1"  # SSE 需要 keep-alive

    # ---- 基础工具 ----
    def log_message(self, fmt, *args):  # 静默访问日志
        pass

    def _json(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return {}

    def _error(self, message: str, code: int = 400) -> None:
        self._json({"ok": False, "error": message}, code)

    # ---- GET ----
    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            html = (WEB_DIR / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return
        if url.path == "/api/status":
            self._json(self.api_status())
            return
        if url.path == "/api/items":
            q = parse_qs(url.query)
            self._json(self.api_items(q))
            return
        if url.path == "/api/creators":
            self._json({"ok": True, "creators": db.list_creators()})
            return
        if url.path == "/api/notifications":
            self._json({"ok": True, "notifications": db.list_notifications(30)})
            return
        if url.path == "/api/scanlogs":
            self._json({"ok": True, "logs": db.list_scan_logs(12)})
            return
        if url.path == "/api/identity/status":
            self._json({"ok": True, "identities": identity.info_all()})
            return
        if url.path == "/api/following":
            q = parse_qs(url.query)
            platform = (q.get("platform") or [""])[0]
            keyword = (q.get("q") or [""])[0]
            if platform not in ("douyin", "weibo", "youtube", "bilibili", "xhs"):
                self._error("platform 必须是 douyin / weibo / youtube / bilibili / xhs")
                return
            self._json({"ok": True, "items": following.list_following(platform, keyword),
                        "total": db.following_count(platform)})
            return
        if url.path == "/api/stream":
            self._sse()
            return
        self._error("not found", 404)

    def _sse(self) -> None:
        """Server-Sent Events:新内容/扫描事件实时推给前端,页面零刷新。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        q = events.subscribe()
        try:
            hello = ("data: " + json.dumps({"type": "hello"}, ensure_ascii=False) + "\n\n").encode("utf-8")
            self.wfile.write(hello)
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=20)
                    body = ("data: " + json.dumps(ev, ensure_ascii=False) + "\n\n").encode("utf-8")
                except queue.Empty:
                    body = b": keepalive\n\n"
                self.wfile.write(body)
                self.wfile.flush()
        except (ConnectionError, BrokenPipeError, TimeoutError):
            pass
        finally:
            events.unsubscribe(q)

    # ---- POST ----
    def do_POST(self) -> None:
        url = urlparse(self.path)
        body = self._read_json()
        try:
            if url.path == "/api/wizard/parse":
                self._json(self.api_wizard_parse(body))
            elif url.path == "/api/creators":
                out = self.api_creators_add(body)
                if out.get("ok"):  # 加入即自动拉一轮,新手不用自己找扫描按钮
                    SCANNER.trigger()
                self._json(out)
            elif re.match(r"^/api/creators/(\d+)/toggle$", url.path):
                cid = int(re.match(r"^/api/creators/(\d+)/toggle$", url.path).group(1))
                enabled = db.toggle_creator(cid)
                if enabled is None:
                    self._error("博主不存在")
                else:
                    self._json({"ok": True, "enabled": enabled})
            elif url.path == "/api/scan":
                started = SCANNER.trigger(manual=True)
                self._json({"ok": True, "started": started, "busy": SCANNER.busy})
            elif url.path == "/api/classify/refresh":
                ids = db.untagged_ids()
                for iid in ids:
                    it = db.get_item(iid)
                    if not it:
                        continue
                    creator = None
                    for c in db.list_creators(it["platform"]):
                        if c["platform_uid"] == it["platform_uid"]:
                            creator = c
                            break
                    tag, classifier = classify.classify(it["title"], (creator or {}).get("name", ""))
                    db.set_tag(iid, tag, classifier)
                self._json({"ok": True, "classified": len(ids)})
            elif url.path == "/api/notify/test":
                self._json({"ok": True, **notify.send_test()})
            elif url.path == "/api/items/manual":
                self._json(self.api_items_manual(body))
            elif url.path == "/api/identity/login":
                self._json(self.api_identity_login(body))
            elif url.path == "/api/following/refresh":
                platform = str(body.get("platform") or "")
                try:
                    count = following.refresh(platform)
                    self._json({"ok": True, "count": count})
                except Exception as exc:
                    self._json({"ok": False, "error": str(exc)[:200]})
            elif url.path == "/api/creators/bulk":
                self._json(self.api_creators_bulk(body))
            else:
                self._error("not found", 404)
        except Exception as exc:
            self._error(f"服务内部错误:{exc}", 500)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/notifications":
            self._json({"ok": True, "cleared": db.clear_notifications()})
            return
        m = re.match(r"^/api/creators/(\d+)$", path)
        if not m:
            self._error("not found", 404)
            return
        if db.delete_creator(int(m.group(1))):
            self._json({"ok": True})
        else:
            self._error("删除失败:演示示例博主不可删,或已不存在")

    # ---- 业务组装 ----
    def api_status(self) -> dict:
        last = db.last_scan_by_platform()
        adapters = []
        with db.connect() as conn:
            for p in PLATFORM_ORDER:
                meta = PLATFORMS[p]
                creators = conn.execute("SELECT COUNT(*) c FROM creators WHERE platform=?", (p,)).fetchone()["c"]
                items = conn.execute("SELECT COUNT(*) c FROM items WHERE platform=?", (p,)).fetchone()["c"]
                log = last.get(p)
                adapters.append({
                    "platform": p, "label": meta["label"], "color": meta["color"],
                    "mode": meta["mode"], "mode_label": MODE_LABEL[meta["mode"]],
                    "note": meta.get("note", ""), "disabled": bool(meta.get("disabled")),
                    "creators": creators, "items": items,
                    "last_scan": log,
                })
            today = datetime.now().strftime("%Y-%m-%d")
            stats = {
                "creators": conn.execute("SELECT COUNT(*) c FROM creators").fetchone()["c"],
                "items": conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"],
                "items_today": conn.execute(
                    "SELECT COUNT(*) c FROM items WHERE first_seen_at LIKE ?", (today + "%",)).fetchone()["c"],
                "ai_classified": conn.execute("SELECT COUNT(*) c FROM items WHERE classifier='ai'").fetchone()["c"],
                "simulated": conn.execute("SELECT COUNT(*) c FROM items WHERE simulated=1").fetchone()["c"],
            }
        return {
            "ok": True,
            "server": {"started_at": STARTED_AT, "scanning": SCANNER.busy,
                       "next_scan_in": SCANNER.next_scan_in()},
            "adapters": adapters, "stats": stats, "tag_dist": db.tag_distribution(),
        }

    def api_items(self, q: dict) -> dict:
        platform = (q.get("platform") or [""])[0]
        tag = (q.get("tag") or [""])[0]
        creator_id = (q.get("creator_id") or [""])[0]
        new_only = (q.get("new_only") or ["0"])[0] in ("1", "true")
        limit = min(int((q.get("limit") or ["60"])[0]), 500)
        offset = max(int((q.get("offset") or ["0"])[0]), 0)
        platform_uid = ""
        if creator_id:
            creator = db.get_creator(int(creator_id))
            if creator:
                platform, platform_uid = creator["platform"], creator["platform_uid"]
        kw = dict(platform=platform or None, platform_uid=platform_uid or None,
                  tag=tag or None, new_only=new_only)
        items = db.list_items(limit=limit, offset=offset, **kw)
        total = db.count_items(**kw)
        return {"ok": True, "items": items, "total": total, "limit": limit, "offset": offset}

    def api_wizard_parse(self, body: dict) -> dict:
        text = str(body.get("input") or "").strip()
        if not text:
            return {"ok": False, "error": "输入为空"}
        try:
            suggestion = wizard.parse(text)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        suggestion["mode_label"] = MODE_LABEL[PLATFORMS[suggestion["platform"]]["mode"]]
        suggestion["mode"] = PLATFORMS[suggestion["platform"]]["mode"]
        return {"ok": True, "suggestion": suggestion}

    def api_creators_add(self, body: dict) -> dict:
        platform = str(body.get("platform") or "")
        uid = str(body.get("platform_uid") or "").strip()
        name = str(body.get("name") or "").strip()
        if platform not in PLATFORMS or not uid or not name:
            return {"ok": False, "error": "参数不全(platform/platform_uid/name)"}
        if PLATFORMS[platform].get("disabled"):
            return {"ok": False, "error": "该平台功能已置灰(无可用通道),不可添加"}
        new_id = db.add_creator(platform, uid, name, url=str(body.get("url") or ""),
                                mode=PLATFORMS[platform]["mode"], note=str(body.get("note") or ""))
        if new_id is None:
            return {"ok": False, "error": "该博主已在监控列表里"}
        return {"ok": True, "id": new_id}

    def api_items_manual(self, body: dict) -> dict:
        creator_id = int(body.get("creator_id") or 0)
        title = str(body.get("title") or "").strip()
        url = str(body.get("url") or "").strip()
        if not creator_id or not title:
            return {"ok": False, "error": "缺博主或标题"}
        creator = db.get_creator(creator_id)
        if not creator:
            return {"ok": False, "error": "博主不存在"}
        item = {
            "item_id": f"manual-{int(time.time() * 1000) % 10 ** 9}",
            "title": title, "url": url, "summary": str(body.get("summary") or ""),
            "published_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "simulated": 0,
        }
        iid = db.add_manual_item(creator["platform"], creator["platform_uid"], item)
        if iid is None:
            return {"ok": False, "error": "这条内容已经登记过了"}
        tag, classifier = classify.classify(title, creator["name"])
        db.set_tag(iid, tag, classifier)
        notify.notify_new(creator["name"], PLATFORMS[creator["platform"]]["label"], title, tag, url)
        events.publish({"type": "new_items", "items": [
            {**db.get_item(iid), "creator_name": creator["name"], "tag": tag, "notified": 1}]})
        return {"ok": True, "item_id": iid, "tag": tag, "classifier": classifier}

    def api_identity_login(self, body: dict) -> dict:
        """抖音/小红书/B站:弹真浏览器窗口扫码(交互式,用户当面使用);
        YouTube/微博:打开用户的主 Chrome 登录后导入成熟会话(不弹自动化窗口)。"""
        platform = str(body.get("platform") or "")
        if platform not in identity.SUPPORTED:
            return {"ok": False, "error": "支持的平台:douyin / weibo / xhs / youtube / bilibili"}
        script = BASE_DIR / "login_qr.py"
        log_path = BASE_DIR / "data" / "login_qr.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as lf:
            subprocess.Popen([sys.executable, str(script), platform],
                             stdout=lf, stderr=lf, start_new_session=True)
        tip_by_platform = {
            "youtube": "已打开你的主 Chrome:确认 YouTube 是登录状态即可,不用扫码(240 秒超时)",
            "weibo": "已打开你的主 Chrome:在里面登录微博(扫码/短信都行)——成熟会话比弹窗扫码稳,登录着就行",
        }
        tip = tip_by_platform.get(platform,
                                  "浏览器窗口已打开:在里面点登录→扫码;完成后这里自动变绿(240 秒超时)")
        return {"ok": True, "tip": tip}

    def api_creators_bulk(self, body: dict) -> dict:
        platform = str(body.get("platform") or "")
        if platform not in PLATFORMS:
            return {"ok": False, "error": "平台不合法"}
        if PLATFORMS[platform].get("disabled"):
            return {"ok": False, "error": "该平台功能已置灰(无可用通道),不可添加"}
        creators = body.get("creators") or []
        added = 0
        for c in creators[:200]:
            uid = str((c or {}).get("platform_uid") or "").strip()
            name = str((c or {}).get("name") or "").strip()
            if not uid or not name:
                continue
            if db.add_creator(platform, uid, name, url=str(c.get("url") or ""),
                               mode=PLATFORMS[platform]["mode"]) is not None:
                added += 1
        SCANNER.trigger()
        return {"ok": True, "added": added, "skipped": len(creators) - added}


def main() -> int:
    cfg = config.load()
    db.init()
    seeds.run_if_needed()
    SCANNER.start()
    host = cfg.get("server", {}).get("host", "127.0.0.1")
    port = int(cfg.get("server", {}).get("port", 18650))

    class QuietHTTPServer(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):  # SSE 客户端断开是常态,不刷栈
            exc = sys.exc_info()[1]
            if isinstance(exc, (ConnectionResetError, BrokenPipeError, TimeoutError)):
                return
            super().handle_error(request, client_address)

    httpd = QuietHTTPServer((host, port), Handler)
    print(f"博主雷达演示版已启动 → http://{host}:{port}  (监控线程已在后台轮询,SSE 实时推送已开)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
