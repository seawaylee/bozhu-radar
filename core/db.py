"""SQLite 存储:博主 / 内容 / 通知 / 扫描日志。

去重指纹 = (platform, platform_uid, item_id) 三元组,靠 UNIQUE 约束兜底。
首跑基线:每个博主第一次成功抓取到的内容标 is_baseline=1,只入库不通知,
防止演示第一天被 100 条历史内容轰炸——这是课程「增量监控」的核心教学点。
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DB_PATH = DATA_DIR / "monitor.db"

_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    with _lock, connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS creators(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                platform_uid TEXT NOT NULL,
                name TEXT NOT NULL,
                url TEXT DEFAULT '',
                mode TEXT DEFAULT 'auto',
                enabled INTEGER DEFAULT 1,
                demo INTEGER DEFAULT 0,
                note TEXT DEFAULT '',
                created_at TEXT,
                UNIQUE(platform, platform_uid)
            );
            CREATE TABLE IF NOT EXISTS items(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                platform_uid TEXT NOT NULL,
                item_id TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT DEFAULT '',
                summary TEXT DEFAULT '',
                published_at TEXT,
                tag TEXT DEFAULT '',
                classifier TEXT DEFAULT '',
                simulated INTEGER DEFAULT 0,
                is_baseline INTEGER DEFAULT 0,
                first_seen_at TEXT,
                UNIQUE(platform, platform_uid, item_id)
            );
            CREATE TABLE IF NOT EXISTS notifications(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT,
                content TEXT,
                channels TEXT DEFAULT 'inapp',
                deferred INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS scan_logs(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                platform TEXT,
                ok INTEGER,
                new_items INTEGER DEFAULT 0,
                message TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS meta(
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS followings(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                platform_uid TEXT NOT NULL,
                nickname TEXT NOT NULL,
                extra TEXT DEFAULT '',
                fetched_at TEXT,
                UNIQUE(platform, platform_uid)
            );
            CREATE INDEX IF NOT EXISTS idx_items_seen ON items(first_seen_at DESC);
            CREATE INDEX IF NOT EXISTS idx_items_platform ON items(platform, platform_uid);
            """
        )


# ---------- meta ----------

def meta_get(key: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


def meta_set(key: str, value: str) -> None:
    with _lock, connect() as conn:
        conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


# ---------- creators ----------

def add_creator(platform: str, platform_uid: str, name: str, url: str = "", mode: str = "auto",
                demo: bool = False, note: str = "") -> int | None:
    with _lock, connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO creators(platform,platform_uid,name,url,mode,demo,note,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (platform, platform_uid, name, url, mode, int(demo), note, now_iso()),
        )
        if cur.rowcount == 0:
            return None
        return cur.lastrowid


def list_creators(platform: str | None = None) -> list[dict]:
    sql = ("SELECT c.*,"
           " (SELECT COUNT(*) FROM items i WHERE i.platform=c.platform AND i.platform_uid=c.platform_uid) AS item_count,"
           " (SELECT MAX(COALESCE(i.published_at, i.first_seen_at)) FROM items i"
           "   WHERE i.platform=c.platform AND i.platform_uid=c.platform_uid) AS last_item_at"
           " FROM creators c" + (" WHERE c.platform=?" if platform else "")
           + " ORDER BY c.platform, c.id")
    with connect() as conn:
        rows = conn.execute(sql, (platform,) if platform else ()).fetchall()
        return [dict(r) for r in rows]


def get_creator(creator_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM creators WHERE id=?", (creator_id,)).fetchone()
        return dict(row) if row else None


def delete_creator(creator_id: int) -> bool:
    with _lock, connect() as conn:
        cur = conn.execute("DELETE FROM creators WHERE id=? AND demo=0", (creator_id,))
        return cur.rowcount > 0


# ---------- items ----------

def ingest(platform: str, platform_uid: str, items: list[dict]) -> tuple[list[int], bool]:
    """入库并挑新。返回 (新入库的 item id 列表, 是否首跑基线)。基线轮只入库不通知。"""
    first = meta_get(f"baseline:{platform}:{platform_uid}") is None
    new_ids: list[int] = []
    with _lock, connect() as conn:
        for it in items:
            cur = conn.execute(
                "INSERT OR IGNORE INTO items(platform,platform_uid,item_id,title,url,summary,published_at,"
                "is_baseline,first_seen_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (platform, platform_uid, it["item_id"], it["title"], it.get("url", ""), it.get("summary", ""),
                 it.get("published_at"), 1 if first else 0, now_iso()),
            )
            if cur.rowcount:
                new_ids.append(cur.lastrowid)
        if first and items:
            # 同一事务直写,不得调 meta_set——那会重入 _lock 造成死锁(2026-10-16 实测踩坑)
            conn.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f"baseline:{platform}:{platform_uid}", now_iso()),
            )
    return new_ids, first


def add_manual_item(platform: str, platform_uid: str, item: dict, baseline: bool = False) -> int | None:
    """半自动平台手动登记内容(或演示模拟内容)。"""
    with _lock, connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO items(platform,platform_uid,item_id,title,url,summary,published_at,"
            "is_baseline,simulated,first_seen_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (platform, platform_uid, item["item_id"], item["title"], item.get("url", ""), item.get("summary", ""),
             item.get("published_at"), int(baseline), int(item.get("simulated", 0)), now_iso()),
        )
        return cur.lastrowid if cur.rowcount else None


def list_items(platform: str | None = None, platform_uid: str | None = None, tag: str | None = None,
               limit: int = 100, offset: int = 0, new_only: bool = False) -> list[dict]:
    """分页内容查询。new_only=True 只看增量内容(非基线,即会触发通知的那批)。"""
    where, args = _items_where(platform, platform_uid, tag, new_only)
    sql = ("SELECT i.*, c.name AS creator_name FROM items i LEFT JOIN creators c"
           " ON i.platform=c.platform AND i.platform_uid=c.platform_uid WHERE 1=1" + where
           + " ORDER BY COALESCE(i.published_at, i.first_seen_at) DESC LIMIT ? OFFSET ?")
    args = args + [int(limit), int(offset)]
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def count_items(platform: str | None = None, platform_uid: str | None = None, tag: str | None = None,
                new_only: bool = False) -> int:
    where, args = _items_where(platform, platform_uid, tag, new_only)
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) c FROM items i WHERE 1=1" + where, args).fetchone()["c"]


def _items_where(platform, platform_uid, tag, new_only) -> tuple[str, list]:
    where, args = "", []
    if platform:
        where += " AND i.platform=?"
        args.append(platform)
    if platform_uid:
        where += " AND i.platform_uid=?"
        args.append(platform_uid)
    if tag:
        where += " AND i.tag=?"
        args.append(tag)
    if new_only:
        where += " AND i.is_baseline=0"
    return where, args


def set_tag(item_id: int, tag: str, classifier: str) -> None:
    with _lock, connect() as conn:
        conn.execute("UPDATE items SET tag=?, classifier=? WHERE id=?", (tag, classifier, item_id))


def get_item(item_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return dict(row) if row else None


def untagged_ids() -> list[int]:
    with connect() as conn:
        return [r["id"] for r in conn.execute("SELECT id FROM items WHERE tag='' OR tag IS NULL").fetchall()]


def tag_distribution() -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT COALESCE(NULLIF(tag,''),'未分类') AS tag, COUNT(*) AS n FROM items GROUP BY tag ORDER BY n DESC"
        ).fetchall()]


# ---------- notifications ----------

def add_notification(content: str, channels: str, deferred: bool = False) -> None:
    with _lock, connect() as conn:
        conn.execute("INSERT INTO notifications(created_at,content,channels,deferred) VALUES(?,?,?,?)",
                     (now_iso(), content, channels, int(deferred)))


def list_notifications(limit: int = 30) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


def clear_notifications() -> int:
    with _lock, connect() as conn:
        cur = conn.execute("DELETE FROM notifications")
        return cur.rowcount


def toggle_creator(creator_id: int) -> bool | None:
    """翻转启停。返回翻转后的 enabled;博主不存在返回 None。"""
    with _lock, connect() as conn:
        row = conn.execute("SELECT enabled FROM creators WHERE id=?", (creator_id,)).fetchone()
        if not row:
            return None
        new_val = 0 if row["enabled"] else 1
        conn.execute("UPDATE creators SET enabled=? WHERE id=?", (new_val, creator_id))
        return bool(new_val)


# ---------- scan logs ----------

def add_scan_log(platform: str, ok: bool, new_items: int, message: str = "") -> None:
    with _lock, connect() as conn:
        conn.execute("INSERT INTO scan_logs(ts,platform,ok,new_items,message) VALUES(?,?,?,?,?)",
                     (now_iso(), platform, int(ok), new_items, message[:300]))


def last_scan_by_platform() -> dict[str, dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT s.* FROM scan_logs s JOIN (SELECT platform, MAX(id) mid FROM scan_logs GROUP BY platform) t"
            " ON s.id=t.mid"
        ).fetchall()
        return {r["platform"]: dict(r) for r in rows}


def list_scan_logs(limit: int = 12) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM scan_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


# ---------- followings(我的关注,用于挑人导入) ----------

def upsert_following(platform: str, platform_uid: str, nickname: str, extra: str = "") -> None:
    with _lock, connect() as conn:
        conn.execute(
            "INSERT INTO followings(platform,platform_uid,nickname,extra,fetched_at) VALUES(?,?,?,?,?)"
            " ON CONFLICT(platform,platform_uid) DO UPDATE SET nickname=excluded.nickname,"
            " extra=excluded.extra, fetched_at=excluded.fetched_at",
            (platform, platform_uid, nickname, extra, now_iso()),
        )


def search_following(platform: str, q: str = "", limit: int = 400) -> list[dict]:
    sql = ("SELECT f.*, CASE WHEN c.id IS NULL THEN 0 ELSE 1 END AS monitored"
           " FROM followings f LEFT JOIN creators c ON f.platform=c.platform"
           " AND f.platform_uid=c.platform_uid WHERE f.platform=?")
    args: list = [platform]
    if q:
        sql += " AND f.nickname LIKE ?"
        args.append(f"%{q}%")
    sql += " ORDER BY f.nickname LIMIT ?"
    args.append(int(limit))
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def following_count(platform: str) -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) c FROM followings WHERE platform=?", (platform,)).fetchone()["c"]
