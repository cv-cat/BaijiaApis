<div align="center">
    <a href="https://www.python.org/">
        <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
    </a>
    <a href="https://github.com/psf/curl-cffi">
        <img src="https://img.shields.io/badge/curl__cffi-latest-orange" alt="curl_cffi">
    </a>
</div>

# 📰 Baidu Platform

**✨ 专业的百度号数据采集解决方案，支持用户信息、发帖列表与作品互动数据抓取**

当你需要让 AI Agent 感知百度内容生态——自动采集用户动态、分析内容数据、驱动内容运营策略——第一道墙往往不是模型能力，而是**平台数据获取能力的缺失**。

本项目做的事很简单：把这道墙拆掉。

**⚠️ 严禁用于爬取用户隐私、违规商业用途！本项目仅供学习与技术研究使用，后果自负。**

## 🌟 功能特性

- ✅ **用户信息采集** — 抓取百度号昵称、粉丝数、点赞数、发布总量等基础信息
- ✅ **发帖列表采集** — 分页获取用户全部动态，支持翻页游标续爬
- ✅ **作品互动数据采集** — 获取单条内容的点赞、评论、阅读、转发、收藏等指标
- 🔐 **浏览器指纹模拟** — 基于 `curl_cffi` 模拟 Chrome 101 TLS 指纹，绕过基础风控
- 🔍 **Cookie 有效性检测** — 批量验证 Cookie 存活状态

## 🛠️ 快速开始

### ⛳ 运行环境

- Python 3.10+

### 🎯 本地安装

```bash
pip install -r requirements.txt
```

### 🚀 运行 Demo

```bash
python baidu_apis.py
```

### 🎨 Cookie 配置

在浏览器中打开 [author.baidu.com](https://author.baidu.com)，**登录账号**后按 `F12` 打开开发者工具，点击「网络」→ 找任意一个请求 → 复制请求头中的 `Cookie` 字段值。

> ⚠️ 注意：Cookie 中必须包含 `Hmery-Time` 字段，否则请求将失败。

将获取到的 Cookie 字符串作为 `cookies_str` 参数传入接口，格式如下：

```
BIDUPSID=xxx; Hmery-Time=xxx; BAIDUID=xxx; ...
```

## 📡 接口说明

### `get_user_info(user_url, cookies_str)`

获取百度号**用户基础信息**。

**参数**

| 参数            | 类型  | 说明                                                 |
|---------------|-----|-----------------------------------------------------|
| `user_url`    | str | 用户主页 URL，格式：`https://author.baidu.com/home/{uid}` |
| `cookies_str` | str | 百度登录 Cookie 字符串                                    |

**返回**

```python
(user_info: dict, uk: str, otherext: str)
# user_info 包含：账号昵称、头像地址URL、账号KEY、粉丝数量、总发布量、点赞数量、采集时间 等
```

---

### `get_user_posted(uk, otherext, cookies_str, top_dynamic_id=None, ctime=None)`

获取用户**发帖动态列表**，每页 10 条，支持翻页游标。

**参数**

| 参数               | 类型       | 说明                              |
|------------------|----------|---------------------------------|
| `uk`             | str      | 用户 uk，由 `get_user_info` 返回      |
| `otherext`       | str      | 版本标识，由 `get_user_info` 返回       |
| `cookies_str`    | str      | 百度登录 Cookie 字符串                 |
| `top_dynamic_id` | str/None | 翻页游标（上一页第一条动态 ID），首页传 `None`    |
| `ctime`          | str/None | 翻页时间戳游标，首页传 `None`              |

**返回**

```python
# 原始 JSON，data.list 为动态列表，data.hasMore 为是否有下一页
{
  "data": {
    "list": [...],
    "hasMore": 1,
    "query": {"ctime": "..."}
  }
}
```

---

### `get_work_info(item, uk, cookies_str)`

获取单条内容的**互动数据**（点赞、评论、阅读、转发、收藏）。

**参数**

| 参数            | 类型  | 说明                                   |
|---------------|-----|--------------------------------------|
| `item`        | dict | 动态元数据，包含 `feed_id`、`dynamic_id` 等字段 |
| `uk`          | str | 用户 uk                                |
| `cookies_str` | str | 百度登录 Cookie 字符串                      |

**返回**

```python
{
  "praise_num": 42,       # 点赞数
  "comment_num": 10,      # 评论数
  "read_num": 1000,       # 阅读数
  "forward_num": 5,       # 转发数
  "live_back_num": 0,     # 直播回放数
  "collect": 8,           # 收藏数
  "unread": 0
}
```

---

### `check_cookies_alive(cookies_strs)`

批量检测 Cookie 列表的**有效性**。

**参数**

| 参数              | 类型        | 说明               |
|-----------------|-----------|------------------|
| `cookies_strs`  | list[str] | Cookie 字符串列表     |

## 🐳 Docker 部署

```bash
docker build -t baidu-platform .
docker run -d baidu-platform
```

## 🍥 日志

| 日期       | 说明                                  |
|----------|-------------------------------------|
| 26/04/11 | 项目初始化，完成用户信息、发帖列表、作品互动数据采集接口封装 |

## 🤝 欢迎贡献 PR

本项目欢迎任何形式的贡献！如果你有新功能想法、Bug 修复或文档改进，欢迎提交 PR。

- Fork 本仓库并在新分支上开发
- 保持代码风格与现有代码一致
- PR 描述中请简要说明改动内容和目的

## 🧸 额外说明
1. 感谢 star⭐ 和 follow📰！不时更新
2. 作者的联系方式在主页里，有问题可以随时联系我
3. 可以关注下作者的其他项目，欢迎 PR 和 issue
4. 感谢赞助！如果此项目对您有帮助，请作者喝一杯奶茶~~ （开心一整天😊😊）
5. thank you~~~

## 🍔 交流群

如果你对爬虫和 AI Agent 感兴趣，可以加入群聊一起讨论~

ps: 请加群，人满或者过期 issue | wx 提醒 | qq提醒

| group-1 | group-2 | group-3 | group-4 (2000人qq群) |
|:--:|:--:|:--:|:--:|
| <img width="280" alt="group1" src="https://cvcat.site/assets/group1.jpg" /> | <img width="280" alt="group2" src="https://cvcat.site/assets/group2.jpg" /> | <img width="280" alt="group3" src="https://cvcat.site/assets/group3.jpg" /> | <img width="280" alt="group3" src="https://cvcat.site/assets/group4.jpg" /> |
