"""进程内事件总线:扫描器/手动登记 → SSE 推送到前端。

publish() 从任何线程调用;subscribe() 返回新队列,SSE handler 独占消费。
队列满则丢弃最旧事件——实时流允许丢,不允许堵死扫描线程。
"""
from __future__ import annotations

import queue
import threading

_lock = threading.Lock()
_subs: list[queue.Queue] = []
_MAXQ = 50


def publish(event: dict) -> None:
    with _lock:
        subs = list(_subs)
    for q in subs:
        try:
            q.put_nowait(event)
        except queue.Full:
            try:
                q.get_nowait()
                q.put_nowait(event)
            except Exception:
                pass


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=_MAXQ)
    with _lock:
        _subs.append(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    with _lock:
        if q in _subs:
            _subs.remove(q)
