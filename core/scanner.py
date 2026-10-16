"""扫描器:后台线程轮询所有 auto 通道,增量入库 → AI 分类 → 通知。

失败隔离:任何平台/博主挂掉只记 scan_log,绝不拖垮整轮扫描。
首跑基线:第一次抓到的内容只入库不通知,之后的新内容才推。
"""
from __future__ import annotations

import threading
import time
import traceback

from . import classify, config, db, events, notify
from .adapters import AUTO_ADAPTERS
from .model import PLATFORMS


class Scanner(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="scanner")
        self._wake = threading.Event()
        self._busy = False
        self._manual = False
        self.started_at = time.time()
        self.last_scan_finished_at: float | None = None

    # ---- 对外 ----
    def trigger(self, manual: bool = False) -> bool:
        """手动触发一轮扫描;正在扫描时返回 False。"""
        if self._busy:
            return False
        if manual:
            self._manual = True
        self._wake.set()
        return True

    @property
    def busy(self) -> bool:
        return self._busy

    def next_scan_in(self) -> int:
        interval = int(config.load().get("scan", {}).get("interval_seconds", 600))
        if self.last_scan_finished_at is None:
            return 0
        remain = interval - (time.time() - self.last_scan_finished_at)
        return max(0, int(remain))

    # ---- 主循环 ----
    def run(self) -> None:
        interval = int(config.load().get("scan", {}).get("interval_seconds", 600))
        while True:
            try:
                self.scan_once()
            except Exception:
                traceback.print_exc()
            self.last_scan_finished_at = time.time()
            self._wake.wait(interval)
            self._wake.clear()

    def scan_once(self) -> None:
        self._busy = True
        manual, self._manual = self._manual, False
        prefix = "手动·" if manual else ""
        events.publish({"type": "scan_started", "ts": db.now_iso(), "manual": manual})
        try:
            cfg = config.load()
            limit = int(cfg.get("scan", {}).get("fetch_limit", 5))
            gap = float(cfg.get("scan", {}).get("request_gap_seconds", 3))
            for platform, adapter in AUTO_ADAPTERS.items():
                creators = [c for c in db.list_creators(platform) if c["enabled"]]
                if not creators:
                    db.add_scan_log(platform, True, 0, "暂无博主(或全部停用)")
                    events.publish({"type": "adapter_done", "platform": platform, "ok": True,
                                    "new_items": 0, "message": "暂无博主"})
                    continue
                for c in creators:
                    t0 = time.time()
                    try:
                        items = adapter.fetch(c, limit=limit)
                        new_ids, first = db.ingest(platform, c["platform_uid"], items)
                        fresh_items = []
                        for iid in new_ids:
                            it = db.get_item(iid)
                            if not it:
                                continue
                            tag, classifier = classify.classify(it["title"], c["name"])
                            db.set_tag(iid, tag, classifier)
                            view = {**it, "creator_name": c["name"], "tag": tag,
                                    "notified": 0 if it["is_baseline"] else 1}
                            fresh_items.append(view)
                            if not it["is_baseline"]:
                                notify.notify_new(c["name"], PLATFORMS[platform]["label"],
                                                  it["title"], tag, it["url"])
                        if fresh_items:
                            events.publish({"type": "new_items", "items": fresh_items,
                                            "baseline": first})
                        msg = prefix + ("首跑基线,只入库不通知" if first else "")
                        db.add_scan_log(platform, True, len(new_ids),
                                        f"{msg} 耗时{time.time()-t0:.1f}s")
                        events.publish({"type": "adapter_done", "platform": platform, "ok": True,
                                        "new_items": len(new_ids),
                                        "message": ("首跑基线" if first else "") + f" {time.time()-t0:.1f}s",
                                        "creator": c["name"], "manual": manual})
                    except Exception as exc:  # 失败隔离:单个博主挂掉只记日志,绝不拖垮整轮
                        db.add_scan_log(platform, False, 0,
                                        f"{prefix}{exc} 耗时{time.time()-t0:.1f}s")
                        events.publish({"type": "adapter_done", "platform": platform, "ok": False,
                                        "new_items": 0, "message": str(exc), "creator": c["name"],
                                        "manual": manual})
                    time.sleep(gap)
        finally:
            self._busy = False
            events.publish({"type": "scan_round_done", "ts": db.now_iso(), "manual": manual})
