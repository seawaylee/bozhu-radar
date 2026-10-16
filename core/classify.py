"""AI 分类 + 规则兜底。

模型可配(config.yaml classify 段):默认走智谱 Anthropic 兼容端点,
key 从环境变量读、不落盘;AI 不可用时自动降级关键词规则,分类永不阻塞通知。
每条内容记录 classifier 字段('ai'/'rules'),面板可查——这也是课程
「能力分级+降级兜底」的教学点。
"""
from __future__ import annotations

import os

import requests

from . import config

DEFAULT_TAGSET = ["财经", "科技", "AI", "数码", "美食", "旅行", "娱乐", "知识", "游戏", "健康", "汽车", "其他"]

RULES: dict[str, list[str]] = {
    "财经": ["股票", "基金", "A股", "美股", "港股", "投资", "理财", "市场", "利率", "财报", "估值", "美元", "黄金",
           "行情", "盘面", "复盘", "牛市", "熊市", "仓位"],
    "科技": ["芯片", "半导体", "系统", "开源", "火箭", "卫星", "新能源", "电池", "架构", "程序员", "代码", "互联网",
           "公司", "发布", "实测"],
    "AI": ["AI", "大模型", "GPT", "LLM", "智能体", "agent", "Agent", "DeepSeek", "Claude", "Grok", "训练", "算力",
          "AI绘画", "提示词"],
    "数码": ["手机", "iPhone", "安卓", "iPad", "Mac", "电脑", "耳机", "相机", "镜头", "测评", "评测", "开箱",
           "折叠屏", "充电", "显卡"],
    "美食": ["美食", "吃", "餐厅", "菜", "厨", "探店", "烘焙", "火锅", "烧烤", "食谱"],
    "旅行": ["旅行", "旅游", "游记", "出发", "徒步", "露营", "风景", "城市", "攻略", "签证"],
    "娱乐": ["电影", "剧", "综艺", "明星", "演唱会", "音乐", "游戏直播", "八卦", "吐槽"],
    "知识": ["历史", "物理", "数学", "科普", "解读", "原理", "揭秘", "知识", "课程", "书", "读书", "讲"],
    "游戏": ["游戏", "Switch", "PS5", "Steam", "攻略", "通关", "电竞", "上分"],
    "健康": ["健康", "健身", "减脂", "睡眠", "医生", "营养", "跑步", "养生"],
    "汽车": ["汽车", "试驾", "车", "电动车", "智驾", "特斯拉", "比亚迪", "油耗"],
}


def rules_classify(text: str) -> str:
    for tag, keywords in RULES.items():
        for kw in keywords:
            if kw in text:
                return tag
    return "其他"


def ai_classify(title: str, creator_name: str, cfg: dict) -> str:
    ccfg = cfg.get("classify", {})
    tagset = ccfg.get("tagset") or DEFAULT_TAGSET
    url = ccfg.get("base_url", "https://open.bigmodel.cn/api/anthropic/v1/messages")
    model = ccfg.get("model", "GLM-5.2")
    key = os.environ.get(ccfg.get("key_env", "ANTHROPIC_AUTH_TOKEN"), "")
    if not key:
        raise RuntimeError("分类模型 key 未配置")
    s = requests.Session()
    s.trust_env = False
    resp = s.post(
        url,
        headers={"Authorization": f"Bearer {key}", "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={
            "model": model,
            "max_tokens": 20,
            "system": f"你是内容分类器。从这些标签里选恰好一个输出,除标签外不要输出任何别的字:"
                      f"{'/'.join(tagset)}。若都沾不上,输出「其他」。",
            "messages": [{"role": "user",
                          "content": f"博主:{creator_name or '未知'}\n标题:{title}"}],
        },
        timeout=25,
    )
    resp.raise_for_status()
    data = resp.json()
    answer = "".join(block.get("text", "") for block in data.get("content", [])).strip()
    for tag in tagset:
        if tag in answer:
            return tag
    return "其他"


def classify(title: str, creator_name: str = "") -> tuple[str, str]:
    """返回 (标签, 分类器)。mode=ai 时 AI 失败直接抛错由调用方决定,默认 auto:AI→规则。"""
    cfg = config.load()
    mode = cfg.get("classify", {}).get("mode", "auto")
    if mode == "rules":
        return rules_classify(f"{creator_name} {title}"), "rules"
    try:
        return ai_classify(title, creator_name, cfg), "ai"
    except Exception:
        if mode == "ai":
            return rules_classify(f"{creator_name} {title}"), "rules"
        return rules_classify(f"{creator_name} {title}"), "rules"
