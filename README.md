# 博主雷达 · 多平台创作者监控 web 服务

独立 web 服务：7 平台博主统一管理、新内容实时通知（带直达链接）、AI 自动分类、平台健康面板、扫码登录与「我的关注」一键导入。

> 📄 **[docs/项目文档.md](docs/项目文档.md) 是唯一正本**：功能现状、平台通道实测结论、API、配置、已知问题、变更记录（v0.1→v1.5）都在那里，与代码同步维护。面板各版本截图见 `docs/面板截图-*.png`。

## 功能

- **左侧导航七视图面板**：平台健康、内容流、监控列表、我的关注、通知中心、扫描日志、身份与登录
- **内容流 SSE 实时推送**：新内容自动浮出新卡片，不用刷新页面
- **添加向导**：粘贴任意主页链接自动识别平台与身份（支持短链展开），或从「我的关注」勾选批量加监控
- **扫码登录**：抖音 / 微博 / 小红书 / YouTube 弹窗扫码；**B站为面板内嵌二维码·纯 HTTP 不弹浏览器**。登录态存本地 `data/identity/`
- **「我的关注」五平台导入**：抖音（a_bogus 签名接口）/ B站（登录 cookie + relation 接口翻页）/ 微博（桌面 ajax，扫码会话被软风控时回退 config 成熟 cookie）/ 小红书（无头浏览器开关注弹窗拦页内接口）/ YouTube（订阅页滚动抓全）
- **AI 分类**（GLM，标签可配，失败自动降规则）
- **通知**：站内 / macOS / 飞书机器人 / 飞书自建应用，支持静默时段
- **礼貌采集**：默认 600 秒一轮 + 相邻请求间隔；每博主失败隔离，一家抓不到不连累别家

## 平台通道（2026-10-18 实测）

| 平台 | 内容监控 | 关注导入 |
|---|---|---|
| YouTube | 全自动：yt-dlp flat-playlist 枚举频道最新视频（走代理） | 登录后订阅页抓全 |
| B站 | 全自动：curl_cffi 浏览器指纹打免签名空间接口，单次拿齐 BV号/标题/真实发布时间 | 面板内嵌二维码登录（纯 HTTP）→ relation 接口翻页 |
| 微博 | 全自动（需成熟 cookie）：weibo.com 桌面 ajax + 登录 cookie；扫码新会话会被软风控，成熟 cookie 粘 `config.yaml` | 同左（config 成熟 cookie） |
| 抖音 | 全自动：无头浏览器渲染 share 页拦截页内 XHR | 扫码登录 → a_bogus 本地签名接口 |
| 小红书 | 受限（接口带设备盾签名，进阶） | 扫码登录 → 无头浏览器拦关注弹窗接口（登录态易过期，重扫即用） |
| 公众号 | 半自动：面板手动登记 | — |
| 视频号 | 受限置灰：无网页版，如实置灰 | — |

## 运行

```bash
pip install pyyaml requests curl_cffi yt-dlp playwright qrcode pillow

python login_qr.py douyin   # 按需选 douyin/weibo/xhs/youtube，扫码后登录态存 data/identity/
python app.py               # 打开 http://127.0.0.1:18650（B站扫码在面板「身份与登录」里，内嵌二维码）
```

- YouTube 通道需要代理（`config.yaml` 的 `proxy.http`，默认 `127.0.0.1:7890`）；国内平台一律直连
- AI 分类 key 从环境变量 `ANTHROPIC_AUTH_TOKEN` 读取，不落盘；没有 key 自动降级规则分类
- 端口、扫描间隔、通知渠道都在 `config.yaml`，改完重启生效

## 架构

```mermaid
flowchart LR
    subgraph ADAPT["适配器层(每平台独立 · 失败隔离)"]
        direction TB
        YT["YouTube<br/>yt-dlp 走代理"]
        BILI["B站<br/>curl_cffi 免签名接口"]
        WB["微博<br/>桌面 ajax + 登录cookie"]
        DY["抖音<br/>share页无头浏览器 · 登录"]
        LIMIT["小红书/视频号/公众号<br/>受限:登记/置灰"]
    end
    ADAPT --> ING["增量入库(SQLite)<br/>去重指纹 · 首跑基线"]
    ING --> CLS["AI 分类(GLM 可配)<br/>失败自动降规则"]
    CLS --> NOTI["通知层<br/>站内 / macOS / 飞书 · 静默时段"]
    ING --> WEB["Web 面板 + SSE 实时推送<br/>127.0.0.1:18650"]
    NOTI --> WEB
    style ADAPT fill:#f1f3f8,stroke:#8a94a3
    style ING fill:#fdf3e7,stroke:#a06e08
    style WEB fill:#e8f3ee,stroke:#0f766e
```

## 设计原则

- **零框架**：Python 标准库 `http.server` + SQLite 单文件数据库，前端原生 JS，零构建
- **真实数据铁律**：禁演示数据；抓不到的平台如实标红/置灰并写明原因，不拿假数据顶
- **独立自包含**：不依赖外部项目；数据只落 `data/`，删掉该目录即完全卸载
