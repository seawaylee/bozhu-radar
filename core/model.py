"""统一内容模型与平台注册表。

全项目只认一种内容记录(item dict),七个平台各自适配成这个形状:
    item_id      平台内唯一内容ID(BV号/视频ID/微博bid/文章url hash…)
    title        标题或摘要
    url          直达链接(通知里带的就是它)
    published_at 发布时间 ISO 字符串,拿不到就 None(用入库时间兜底)
    summary      可选摘要
"""
from __future__ import annotations

from dataclasses import dataclass

# 平台元数据:mode 决定健康面板的口径展示
#   auto = 全自动适配器(有自动抓取通道)
#   semi = 半自动(手动登记入册)
# 抖音 2026-10-16 升级为全自动(免登录浏览器渲染路线,用户拍板「必须做」)
PLATFORMS = {
    "youtube":  {"label": "YouTube", "color": "#ff4444", "mode": "auto"},
    "bilibili": {"label": "B站",     "color": "#fb7299", "mode": "auto",
                 "note": "免签名空间接口(big_v 同款主通道);对突发多号访问敏感,低频单博主稳定"},
    "weibo":    {"label": "微博",    "color": "#ff7a1a", "mode": "auto",
                 "note": "需登录:面板扫码(推荐)或 config 粘 cookie"},
    "gzh":      {"label": "公众号",  "color": "#07c160", "mode": "auto",
                 "note": "微信读书通道(werss 同款):面板扫码(微信扫一扫)→「我的关注」导入书架公众号 → 文章全自动;每日有读取配额"},
    "douyin":   {"label": "抖音",    "color": "#fe2c55", "mode": "auto",
                 "note": "需扫码登录(站长拍板登录为准);登录后自动抓最新+可导入关注列表"},
    "xhs":      {"label": "小红书",  "color": "#ff2442", "mode": "semi",
                 "note": "需登录且列表接口有设备盾签名,自动通道属进阶;扫码登录后可手动管理"},
    "sph":      {"label": "视频号",  "color": "#3db870", "mode": "semi", "disabled": True,
                 "note": "视频号没有网页版,内容只在微信客户端内——功能置灰(站长拍板);现实做法=跨平台同名覆盖(同一博主盯TA的抖音/B站号)"},
}
# 受阻原因诚实入面板:note 字段直接展示给用户,不藏着
# disabled=True = 功能置灰不可用(视频号)
PLATFORM_ORDER = ["youtube", "bilibili", "weibo", "gzh", "douyin", "xhs", "sph"]


@dataclass
class Creator:
    id: int
    platform: str
    platform_uid: str
    name: str
    url: str = ""
    mode: str = "auto"
    enabled: bool = True
    demo: bool = False


class AdapterError(Exception):
    """适配器可预期失败(风控/接口变更),扫描器记录后继续跑别的平台——失败隔离。"""


class BaseAdapter:
    """所有平台适配器的基类:同一个插座,不同的插头。"""

    platform = ""
    mode = "auto"

    def fetch(self, creator: Creator, limit: int = 5) -> list[dict]:
        """返回该博主最近的内容列表。失败抛 AdapterError,不要返回空列表冒充成功。"""
        raise NotImplementedError

    def resolve(self, raw_input: str) -> dict:
        """把用户随手填的 URL/ID 解析成 {platform_uid, name, url}。解析不了抛 AdapterError。"""
        raise NotImplementedError
