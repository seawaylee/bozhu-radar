"""YouTube 适配器:yt-dlp flat-playlist 枚举频道最新视频(走代理)。

实测:RSS feeds 端点在本机代理出口下 404/500,yt-dlp /videos 页枚举稳定。
flat 模式拿不到发布时间,published_at 置空由入库时间兜底。
"""
from __future__ import annotations

from .. import config, http
from ..model import AdapterError, BaseAdapter


class YoutubeAdapter(BaseAdapter):
    platform = "youtube"
    mode = "auto"

    def fetch(self, creator: dict, limit: int = 5) -> list[dict]:
        proxy = config.load().get("proxy", {}).get("http", "")
        cid = creator["platform_uid"]
        # uid 兼容两种形态:UC 频道ID 或 @handle(关注导入可能带来 handle)
        videos_url = (f"https://www.youtube.com/{cid}/videos" if cid.startswith("@")
                      else f"https://www.youtube.com/channel/{cid}/videos")
        try:
            lines = http.run_ytdlp(
                videos_url,
                use_proxy=proxy or None, playlist_end=limit, print_fmt="%(id)s|%(title)s",
            )
        except Exception as exc:
            raise AdapterError(f"YouTube 枚举失败(检查梯子是否开启):{exc}") from exc
        items = []
        for ln in lines:
            vid, _, title = ln.partition("|")
            title = title.strip()
            if not title or title == "NA":
                title = f"YouTube视频 {vid}"
            items.append({
                "item_id": vid, "title": title,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "published_at": None, "summary": "",
            })
        if not items:
            raise AdapterError("YouTube 枚举为空(频道不存在或代理未开)")
        return items
