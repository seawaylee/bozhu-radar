"""首次启动种子:只放真实公开博主(全自动平台),零演示数据。

半自动平台(公众号/小红书/视频号)不给假种子——面板如实显示
「无自动通道」及原因;真实内容由用户扫码登录后从自己的关注导入,
或手动登记。
"""
from __future__ import annotations

from . import db

AUTO_SEEDS = [
    ("youtube", "UCBJycsmduvYEL83R_U4JriQ", "MKBHD", "https://www.youtube.com/channel/UCBJycsmduvYEL83R_U4JriQ"),
    ("youtube", "UCHnyfMqiRRG1u-2MsSQLbXA", "Veritasium", "https://www.youtube.com/channel/UCHnyfMqiRRG1u-2MsSQLbXA"),
    # B站:低频单博主(big_v 生产同款口径,arc/search 对突发多号访问敏感)
    ("bilibili", "946974", "影视飓风", "https://space.bilibili.com/946974"),
    ("weibo", "2656274875", "央视新闻", "https://weibo.com/u/2656274875"),
    ("weibo", "2803301701", "人民日报", "https://weibo.com/u/2803301701"),
    ("douyin", "MS4wLjABAAAAgq8cb7cn9ByhZbmx-XQDdRTvFzmJeBBXOUO4QflP96M", "央视新闻",
     "https://www.douyin.com/user/MS4wLjABAAAAgq8cb7cn9ByhZbmx-XQDdRTvFzmJeBBXOUO4QflP96M"),
    ("douyin", "MS4wLjABAAAA4vgRHGrSG6rPlffm3RvwHWL8TBq7O4YnM5jHUNXz0-s", "央视网",
     "https://www.douyin.com/user/MS4wLjABAAAA4vgRHGrSG6rPlffm3RvwHWL8TBq7O4YnM5jHUNXz0-s"),
]


def run_if_needed() -> None:
    if db.meta_get("seeded_v3"):
        return
    for platform, uid, name, url in AUTO_SEEDS:
        db.add_creator(platform, uid, name, url=url, mode="auto")
    db.meta_set("seeded_v3", "1")
