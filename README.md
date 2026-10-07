# BaijiaApis

百家号内容读取与图文发布的 Python 接口。旧入口 `baidu_apis.BaiduApis` 保留，原有方法和返回结构不变。新代码把会话、内容、搜索和 Creator 写入分层。

## 能力状态

| 能力 | 入口 | 状态与证据 |
| --- | --- | --- |
| Cookie 登录会话 | `BaijiaAuth.from_browser_login()`、`from_qrcode_login()`、`from_cookie()`、`require_logged_in()` | 当前 Chrome 使用手机号登录后，同源只读 GET `builder/app/appinfo` 已实测 HTTP 200、`errno=0` 且返回 `data.user`；当前会话 Cookie 在库的 `from_cookie().require_logged_in()` 也通过在线校验。独立 Playwright 新窗口登录流程尚未实测。 |
| 本人网页作品列表 | `BaijiaCreatorAPI.list_web_works()` | 当前登录会话的 `GET pcui/article/lists` 已实测 HTTP 200、`errno=0`，无需伙伴 App Token；全部、图文、图文草稿视图与分页参数已核对。当前账号列表为空。库中独立 Cookie 请求尚未实测。 |
| 网页图文草稿 | `BaijiaCreatorAPI.save_web_draft()`、`delete_web_draft()` | 当前 Chrome 官方编辑器已实测保存与删除临时草稿：均用 Cookie 与 Creator `token` 头；删除后草稿列表为空。库中独立 HTTP 调用尚未实测。 |
| 作者资料、动态、互动数据 | `BaijiaContentAPI.get_user_info/get_user_posts/get_item_metrics` | 公开作者主页已匿名 GET 实测；动态和互动沿用旧仓库的 `mbd.baidu.com/webpage` JSONP 契约，新增解析和请求契约测试，尚未用有效账号回放。 |
| 公开文章 Item | `BaijiaContentAPI.get_article()` | 已对公开 `baijiahao.baidu.com/s?id=...` 页面做匿名 GET 实测，解析标题、作者、更新时间与正文；登录闭环中也用百家号 Cookie 读回首篇 2491 字正文。HTML 结构变化可能需要更新解析器。 |
| 指定作者内容搜索 | `BaijiaSearchAPI.search_user_posts()` | 逐页读取作者动态，按文字在本地筛选。依赖上述动态接口。 |
| 全站文章搜索 | `BaijiaSearchAPI.search_articles()` | 使用百度网页搜索的 `site:` 条件，按结果卡片 `mu` 解析文章直链。**同一 Python 进程的 `require_logged_in() → search_articles('咖啡') → get_article()` 已在线通过**：登录 `errno=0`、9 条结果、首篇 2491 字正文。搜索使用浏览器 `www.baidu.com/s` 请求的独立 Cookie，百家号 Cookie 只用于账号与 Item；两者不串用。重复请求仍可能遇百度安全验证，届时抛 `BaijiaSearchBlocked`，需要在官方网页处理。它不是百家号站内私有 API。 |
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

依赖 `playwright` 和本机 Google Chrome。`from_browser_login()` 会另开一个可见 Chrome 临时会话，不连接正在使用的 Chrome，也不使用持久化用户资料或保存 `storage_state`。在新窗口点击“登录/注册”，自行选择手机号或百家号官方二维码完成登录。浏览器关闭后，Cookie 只留在返回的 `BaijiaAuth` 内存对象中。若等待超时会抛 `BaijiaLoginTimeout`；已取得候选 Cookie 但登录检测未通过会抛 `BaijiaAuthError`，错误消息不含 Cookie 值。该独立 Playwright 登录流程尚未用真实账号验收。

```python
from baijia_apis import BaijiaAuth, BaijiaContentAPI

with BaijiaAuth.from_browser_login(timeout=300) as auth:
    account = auth.require_logged_in()  # 再次只读核对当前账号
    print(account["data"])
    item = BaijiaContentAPI(auth).get_article("1839669810600928968")
    print(item["title"])
```

已有本人 Cookie 时也可直接复用。将 `.env.example` 复制为本机 `.env` 并填入自己的凭据；库本身不自动读取 `.env`。`.env` 被 Git 忽略。请勿提交 Cookie、Creator token、App Token 或含凭据的流量记录。

```python
import os
from baijia_apis import BaijiaAuth, BaijiaContentAPI

with BaijiaAuth.from_cookie(os.environ["BAIDU_COOKIES"]) as auth:
    account = auth.require_logged_in()  # errno 非 0 时抛 BaijiaAuthError
    item = BaijiaContentAPI(auth).get_article("1839669810600928968")
    print(item["title"], item["author"])
```

`from_cookie` 复用已登录浏览器的百家号 Cookie；应从本人浏览器 `builder/app/appinfo` 请求的 `Cookie` 请求头复制完整值，`document.cookie` 可能缺少 HttpOnly Cookie。百度网页搜索使用另一个 Cookie：从同一浏览器 `www.baidu.com/s` 请求头取得，交给 `BaijiaSearchAPI(baidu_cookie=...)`。两者只保存在内存中，不要提交到仓库。`from_browser_login` 从临时浏览器会话读取包括 HttpOnly 在内、适用于百家号登录检测 URL 的 Cookie；它**尚未自动提供百度搜索 Cookie**。`from_qrcode_login` 保持兼容，内部调用同一浏览器登录流程，用户仍可自行选择手机号方式。登录成功只依据 `require_logged_in()` 对 `builder/app/appinfo` 的只读响应 `errno=0`，不依据页面文案或跳转地址。当前 Chrome 手机号登录后的该检查已实测 HTTP 200、`errno=0`、`data.user` 存在；独立 Playwright 会话尚未实测。平台返回失败 JSON 时给出 `errno`，发生跳转时直接报 HTTP 错误。携带 Cookie 或提交数据的请求不跟随重定向，避免把登录页当作接口成功。需要 Creator 图片上传时还需提供 `creator_token` 和 `app_id`。`refresh_creator_token()` 对 `builder/app/appinfo` 发 HEAD 请求，从响应头读取更新后的 token；**它需要已有 token，不能仅凭 Cookie 生成初始 token**。初始 token 应从本人已登录 Creator 页面发送的 `token` 请求头核对，刷新流程尚未做有凭据实测。

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

当前 Chrome 会话的在线验收方式（两个 Cookie 均取自本人浏览器对应域名的请求，示例中从环境变量读取）：

```python
import os
from baijia_apis import BaijiaAuth, BaijiaContentAPI, BaijiaSearchAPI

with BaijiaAuth.from_cookie(os.environ["BAIDU_COOKIES"]) as auth:
    auth.require_logged_in()
    found = BaijiaSearchAPI(
        auth, baidu_cookie=os.environ["BAIDU_SEARCH_COOKIES"]
    ).search_articles("咖啡")
    item = BaijiaContentAPI(auth).get_article(found["items"][0]["id"])
    print(item["title"], len(item["content"]))
```

百度搜索仍可能要求人工完成安全验证，库会抛出 `BaijiaSearchBlocked`，不会自动解验证码。以下是无需登录的公开文章读取示例：

```python
from baijia_apis import BaijiaAuth, BaijiaContentAPI, BaijiaSearchAPI

with BaijiaAuth() as auth:
    content = BaijiaContentAPI(auth)
    article = content.get_article("https://baijiahao.baidu.com/s?id=1839669810600928968")
    print(article["id"], article["content"][:80])

    search = BaijiaSearchAPI(auth)
    # 网页搜索可能要求人工安全验证；此时抛出 BaijiaSearchBlocked。
    result = search.search_articles("咖啡", page=1)
    item = content.get_article(result["items"][0]["id"])
    print(item["title"], len(item["content"]))
```

指定作者的本地搜索：先用 `get_user_info()` 得到 `uk` 和 `otherext`，再调用 `search_user_posts(uk, otherext, query, max_pages=3)`。`get_item_metrics(item, uk)` 读取旧版动态的互动统计。公开文章 `get_article()` 与互动统计是两种不同数据源。

### 本人网页作品列表

作品管理页 `/builder/rc/content` 的全部、图文、图文草稿视图会请求 `GET /pcui/article/lists`。当前 Chrome 登录会话中，这三个视图和第 2 页均返回 HTTP 200、`errno=0`；`data` 含 `list` 与 `page`，后者含 `currentPage`、`pageSize`、`totalCount`、`totalPage`。该账号当前显示 0 篇，因此尚无作品详情样本。`list_web_works()` 只支持上述三个已核对视图，每页 10 条；它使用网页登录 Cookie，不使用 App ID / App Token。

```python
from baijia_apis import BaijiaAuth, BaijiaCreatorAPI

with BaijiaAuth.from_browser_login() as auth:
    drafts = BaijiaCreatorAPI(auth).list_web_works(view="draft", page=1)
    print(drafts["data"]["page"])
```

上述浏览器会话 API 路由已在当前 Chrome 中验证；独立 Playwright 登录及 `curl_cffi` 使用同一 Cookie 调用此路由尚未做账号实测。

### 网页图文草稿

已在本人登录的官方编辑器中用中性测试内容点击一次“存草稿”：`POST /pcui/article/save?callback=bjhdraft`，请求为 URL 编码表单，携带 Cookie 与 Creator `token` 头；响应 `errno=0`，`ret.article_id` 为数字 ID。该 ID 随后出现在图文草稿列表。删除入口 `POST /pcui/article/remove` 只提交表单 `article_id`，使用同一类 Cookie 与 Creator `token`，返回 `errno=0`；再次查询草稿列表为 0。没有点击公开“发布”。网页草稿方法不接受伙伴 App Token 代替 Creator token。

```python
import os
from baijia_apis import BaijiaAuth, BaijiaCreatorAPI

with BaijiaAuth.from_cookie(
    os.environ["BAIDU_COOKIES"], creator_token=os.environ["BAIJIA_CREATOR_TOKEN"]
) as auth:
    creator = BaijiaCreatorAPI(auth)
    # 显式调用后会在本人账号创建草稿；不要对未知结果直接重试。
    # draft = creator.save_web_draft("测试草稿标题", "<p>测试正文</p>")
    # 确认草稿 ID 后，显式调用才会永久删除该草稿。
    # creator.delete_web_draft(draft["ret"]["article_id"])
    pass
```

网页实测保存请求还含当前账号页面给出的活动列表及选中状态；方法可用 `activities=[("活动 ID", True), ...]` 传入。默认省略活动字段尚未通过真实账号回放。`from_browser_login()` 只返回 Cookie，不提取 Creator token；调用草稿方法前需从本人 Creator 会话取得该 token 并只保存在本机内存。`save_web_draft()` 要求标题 2–64 字、非空 HTML 正文。此次样本 `len=29`，与提交的 HTML 29 字符相符；纯文本为 22 字、UTF-8 为 73 字节。方法按 Python HTML 字符数填 `len`；非 BMP 字符（如 emoji）的长度规则尚未实测，因此在提交前拒绝。若平台报告 `errno=0` 却没有 `ret.article_id`，先查草稿列表，勿直接重试。`delete_web_draft(article_id)` 只接受调用者显式传入的正整数数字 ID，不查找、不批量删除、不自动重试；调用前请在草稿列表确认目标 ID。当前尚未用 `curl_cffi` 复现真实保存或删除。

## 图文发布

百家号开放接口需账号获得 App ID / App Token。**浏览器登录 Cookie 可读取本人网页作品列表，但不能代替 App Token 调用此处的 `publish_article()` 或 `query_article_status()`。**封面传 HTTPS 图片 URL；如需把本地图片上传到 Creator 素材库，可使用 `upload_image()`，它另需 Cookie 和 Creator token。两类身份材料在同一个 `BaijiaAuth` 中可同时提供；对来源不同的 App ID，请分别构建会话并按账号资料核对。

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

无封面内容会进入草稿。需要草稿时显式使用 `cover_urls=[]` 与 `allow_draft=True`；默认拒绝无封面调用，避免误以为作品已发布。`publish_article()` 返回平台原始 JSON，`query_article_status()` 单次最多查询 20 个数字 ID，两者在 `errno` 非零时抛 `BaijiaAPIError`。若平台报告发布成功却未返回有效 `data.article_id`，发布调用抛 `BaijiaParseError`，此时应先在后台核对，勿直接重试。发布响应结构参考公开 SDK 的 `article_id` 字段；同一批状态查询请勿混用文章 ID 与 NID。

这些请求格式依据[公开 Go SDK 的图文发布实现](https://github.com/onlyliu1001/BaiJiaHaoSdk/blob/main/BaiJiaHaoSdk/ContentPublish.go)、[字段定义](https://github.com/onlyliu1001/BaiJiaHaoSdk/blob/main/BaiJiaHaoSdk/config.go)和[文章状态实现](https://github.com/onlyliu1001/BaiJiaHaoSdk/blob/main/BaiJiaHaoSdk/ContentManage.go)。开放接口需要账号权限；当前尚未使用有权限账号完成发布或状态查询。当前没有视频发布方法，也不把网页后台的内部字段冒充稳定开放 API。

### 真实账号最小验收

1. 当前 Chrome 的手机号登录已使同源 `builder/app/appinfo` 返回 HTTP 200、`errno=0` 和 `data.user`。下一步由本人调用 `BaijiaAuth.from_browser_login()`，在新窗口选择手机号或官方二维码登录，确认返回对象的 `require_logged_in()` 仍为 `errno=0` 且账号信息对应本人。若需要人工复用已有浏览器登录态，再走 `from_cookie()`。记录结果码，不保存 Cookie 到报告或仓库。
2. 若只验证图文开放接口，用账号已开通的 App ID / App Token；先用一篇已知的**该开放接口文章 ID**调用 `query_article_status()` 验证读权限。若没有该类 ID，跳过这一步，不以 Cookie 验证成功代替 App Token 验证成功。使用已托管的 HTTPS 封面可跳过 Creator 图片上传及 `creator_token`。
3. 准备一篇测试文章、本人可控且唯一的 `origin_url`，以及符合尺寸要求的封面。明确调用一次 `publish_article()`，保存返回的 `data.article_id`，再用 `query_article_status(article_id)` 确认审核/发布状态与可访问 URL。无封面加 `allow_draft=True` 只验草稿提交，公开发布仍需单独有封面的实测；公开 SDK 提醒草稿也占用当日发文次数。
4. 如需本地图片链路，再从本人 Creator 请求核对初始 `token` 与 `app_id`，先用 `refresh_creator_token()` 验证 HEAD 响应，然后上传一张测试 JPEG/PNG 并确认返回 HTTPS URL。此步骤会写入账号素材库。

当前 Chrome 的手机号登录校验只证明该浏览器已有有效账号会话；独立 Playwright 登录辅助、开放接口发布和上传仍需按第 1–4 步验收。登录辅助依赖官方页面交互，没有模拟短信或扫码回调。网页草稿保存已有一次真实操作样本；网页公开发布仍没有请求契约。

网页图文编辑器路由为 `/builder/rc/edit?type=news&is_from_cms=1`，页面含标题、正文、封面，以及“存草稿”“预览”“定时发布”“发布”按钮。一次中性测试草稿的保存和删除已完成；当前账号草稿列表恢复为 0。`GET /pcui/article/edit?type=news` 路由曾由页面请求，但未取得可复用的详情请求契约；本仓库没有网页公开发布方法。要确认公开发布，仍需本人明确准备测试作品并触发一次“发布”，核对写请求、审核状态及作品 URL。

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

离线测试验证浏览器登录辅助与兼容别名的成功/超时/校验失败路径、本人网页作品列表和网页草稿的请求参数、Cookie 处理、登录态预检、跳转处理、请求 URL/参数/Body、JSONP 解析、分页游标、公开 Item 解析及发布/上传请求契约。测试会模拟浏览器，不启动真实 Chrome。当前 Chrome 手机号登录态、网页作品列表路由和一次草稿保存/删除已实测；独立 Playwright 登录、库内草稿 HTTP 调用、公开发布、上传和全站搜索尚未完成真实账号端到端验证。

## 端点依据

- 旧仓库源码 `baidu_apis.py`：作者页、动态 JSONP、互动指标。
- [公开 Creator 客户端原始源码](https://github.com/ai-chen2050/obsidian-wechat-public-platform/blob/master/src/api.ts)：Creator token 刷新、图片上传与网页后台发布字段。本文只实现其中图片上传。
- [公开百家号 SDK](https://github.com/onlyliu1001/BaiJiaHaoSdk)：App ID / Token 图文发布与文章状态查询。开放接口路由已单独匿名探测，请求字段仍需账号核对。
