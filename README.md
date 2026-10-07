# BaijiaApis

百家号内容读取与图文发布的 Python 接口。旧入口 `baidu_apis.BaiduApis` 保留，原有方法和返回结构不变。新代码把会话、内容、搜索和 Creator 写入分层。

## 能力状态

| 能力 | 入口 | 状态与证据 |
| --- | --- | --- |
| Cookie 登录会话 | `BaijiaAuth.from_cookie()`、`login_state()` | `builder/app/appinfo` 无凭据 GET 已返回登录过期 JSON；有凭据会话未实测。扫码与短信登录协议未核实。 |
| 作者资料、动态、互动数据 | `BaijiaContentAPI.get_user_info/get_user_posts/get_item_metrics` | 公开作者主页已匿名 GET 实测；动态和互动沿用旧仓库的 `mbd.baidu.com/webpage` JSONP 契约，新增解析和请求契约测试，尚未用有效账号回放。 |
| 公开文章 Item | `BaijiaContentAPI.get_article()` | 已对公开 `baijiahao.baidu.com/s?id=...` 页面做匿名 GET 实测，解析标题、作者、更新时间与正文。HTML 结构变化可能需要更新解析器。 |
| 指定作者内容搜索 | `BaijiaSearchAPI.search_user_posts()` | 逐页读取作者动态，按文字在本地筛选。依赖上述动态接口。 |
| 全站文章搜索 | `BaijiaSearchAPI.search_articles()` | 使用百度网页搜索的 `site:` 条件。当前网络出口收到“百度安全验证”；解析只做了离线契约测试，未验证正常搜索页。它不是百家号站内私有 API。 |
| 图文发布与状态查询 | `BaijiaCreatorAPI.publish_article/query_article_status` | 百家号 App ID / Token 开放接口路由可达，匿名 GET 返回参数错误；POST 请求格式来自公开实现，**未用具备权限的账号发布或查询**。 |
| 本地图片上传 | `BaijiaCreatorAPI.upload_image()` | `pcui/picture/uploadproxy` 路由可达；表单字段依据公开 Creator 客户端源码，未做账号实测。也可直接向图文发布接口传已托管的 HTTPS 封面 URL。 |
| 视频发布 | — | 未核实端点及上传链路，暂未实现。 |

状态说明：“路由可达”只说明地址返回平台 JSON，不代表凭据、字段或发布结果已经实测。图文发布会创建作品，示例代码只展示调用方式，不会自动运行。

## 项目结构

```text
baijia_apis/
├── auth.py       # Cookie / App Token 会话和登录状态
├── content.py    # 作者动态、互动指标、公开 Item
├── search.py     # 百度网页搜索、作者动态本地筛选
└── creator.py    # 图文开放接口、Creator 图片上传
baidu_apis.py     # 原有 BaiduApis 兼容入口
tests/            # 离线请求契约测试
```

## 安装与登录

Python 3.10+：

```powershell
python -m pip install -r requirements.txt
```

将 `.env.example` 复制为本机 `.env` 并填入自己的凭据。库本身不自动读取 `.env`；可通过环境变量或自己的配置程序传入。`.env` 被 Git 忽略。请勿提交 Cookie、Creator token、App Token 或含凭据的流量记录。

```python
import os
from baijia_apis import BaijiaAuth, BaijiaContentAPI

with BaijiaAuth.from_cookie(os.environ["BAIDU_COOKIES"]) as auth:
    print(auth.login_state())
    item = BaijiaContentAPI(auth).get_article("1839669810600928968")
    print(item["title"], item["author"])
```

`from_cookie` 复用已登录浏览器的 Cookie；不会替用户完成扫码或短信登录。需要 Creator 图片上传时还需提供 `creator_token` 和 `app_id`。`refresh_creator_token()` 对 `builder/app/appinfo` 发 HEAD 请求，从响应头读取更新后的 token；这条刷新流程也未做有凭据实测。

```python
import os
from baijia_apis import BaijiaAuth, BaijiaCreatorAPI

with BaijiaAuth.from_cookie(
    os.environ["BAIDU_COOKIES"],
    creator_token=os.environ["BAIJIA_CREATOR_TOKEN"],
    app_id=os.environ["BAIJIA_APP_ID"],
) as auth:
    # 调用后会把本地图片上传到账号的 Creator 素材库。
    # cover_url = BaijiaCreatorAPI(auth).upload_image("cover.png")
    pass
```

## 搜索与 Item

```python
from baijia_apis import BaijiaAuth, BaijiaContentAPI, BaijiaSearchAPI

with BaijiaAuth() as auth:
    content = BaijiaContentAPI(auth)
    article = content.get_article("https://baijiahao.baidu.com/s?id=1839669810600928968")
    print(article["id"], article["content"][:80])

    search = BaijiaSearchAPI(auth)
    # 网页搜索可能要求人工安全验证；此时抛出 BaijiaSearchBlocked。
    # result = search.search_articles("AI 趋势", page=1)
```

指定作者的本地搜索：先用 `get_user_info()` 得到 `uk` 和 `otherext`，再调用 `search_user_posts(uk, otherext, query, max_pages=3)`。`get_item_metrics(item, uk)` 读取旧版动态的互动统计。公开文章 `get_article()` 与互动统计是两种不同数据源。

## 图文发布

百家号开放接口需账号获得 App ID / App Token。封面传 HTTPS 图片 URL；如需把本地图片上传到 Creator 素材库，可使用 `upload_image()`，它另需 Cookie 和 Creator token。两类身份材料在同一个 `BaijiaAuth` 中可同时提供。

```python
import os
from baijia_apis import BaijiaAuth, BaijiaCreatorAPI

with BaijiaAuth.from_partner_token(
    os.environ["BAIJIA_APP_ID"], os.environ["BAIJIA_APP_TOKEN"]
) as auth:
    creator = BaijiaCreatorAPI(auth)
    # 显式调用以下方法才会提交作品。
    # result = creator.publish_article(
    #     "一篇示例文章", "<p>示例正文</p>",
    #     origin_url="https://example.com/original",
    #     cover_urls=["https://example.com/cover.jpg"],
    # )
    # status = creator.query_article_status(result["data"]["article_id"])
```

`publish_article()` 要求有效的 `origin_url`，标题按公开 Go SDK 的计数规则为 5–40 字，富文本正文最多 20000 字。封面传 1–3 张 HTTP(S) 图片 URL；请求中的 `cover_images` 是 JSON 字符串。封面尺寸至少 218×146，当前只校验 URL，未下载图片验证尺寸。省略 `is_original` 时不发送原创声明；明确传入 `True` / `False` 才发送 `1` / `0`。

无封面内容会进入草稿。需要草稿时显式使用 `cover_urls=[]` 与 `allow_draft=True`；默认拒绝无封面调用，避免误以为作品已发布。`publish_article()` 返回平台原始 JSON，`query_article_status()` 单次最多查询 20 个数字 ID，两者在 `errno` 非零时抛 `BaijiaAPIError`。同一批状态查询请勿混用文章 ID 与 NID。

这些请求格式依据[公开 Go SDK 的图文发布实现](https://github.com/onlyliu1001/BaiJiaHaoSdk/blob/main/BaiJiaHaoSdk/ContentPublish.go)、[字段定义](https://github.com/onlyliu1001/BaiJiaHaoSdk/blob/main/BaiJiaHaoSdk/config.go)和[文章状态实现](https://github.com/onlyliu1001/BaiJiaHaoSdk/blob/main/BaiJiaHaoSdk/ContentManage.go)。开放接口需要账号权限；当前尚未使用有权限账号完成发布或状态查询。当前没有视频发布方法，也不把网页后台的内部字段冒充稳定开放 API。

## 旧版兼容

```python
from baidu_apis import BaiduApis

api = BaiduApis()
# api.get_user_info(user_url, cookies_str)
# api.get_user_posted(uk, otherext, cookies_str)
# api.get_work_info(item, uk, cookies_str)
# api.check_cookies_alive(cookies_strs)
```

旧方法仍使用原有参数与返回结构。新模块不修改旧调用路径；迁移时可逐个换用 `BaijiaContentAPI`。

## 验证

```powershell
python -m unittest discover -s tests -v
```

离线测试验证 Cookie 处理、请求 URL/参数/Body、JSONP 解析、分页游标、公开 Item 解析及发布/上传请求契约。发布、上传、登录和全站搜索尚未完成真实账号端到端验证。

## 端点依据

- 旧仓库源码 `baidu_apis.py`：作者页、动态 JSONP、互动指标。
- [公开 Creator 客户端原始源码](https://github.com/ai-chen2050/obsidian-wechat-public-platform/blob/master/src/api.ts)：Creator token 刷新、图片上传与网页后台发布字段。本文只实现其中图片上传。
- [公开百家号 SDK](https://github.com/onlyliu1001/BaiJiaHaoSdk)：App ID / Token 图文发布与文章状态查询。开放接口路由已单独匿名探测，请求字段仍需账号核对。
