# BaijiaApis

百家号的纯 HTTP 读取与 Creator 写入接口。所有请求都由 `curl_cffi` 发出，库不启动浏览器、不读取浏览器资料、不保存 Cookie，也不把页面自动化当成登录协议。

## 能力与验收边界

| 能力 | 入口 | 当前状态 |
| --- | --- | --- |
| Cookie 会话校验 | `BaijiaAuth.from_http_login()`、`from_cookie()`、`require_logged_in()` | `GET /builder/app/appinfo` 的 HTTP JSON 契约已实现；调用方必须提供完整 Cookie 请求头 |
| 二维码登录 | `BaijiaAuth.from_qrcode_login()`、`BaijiaQRCodeLogin` | CAS 二维码取图、`qrget` 轮询和确认表单均为纯 HTTP；扫码由百度 App 完成，库只返回挑战状态 |
| 短信、图片验证码和滑块 | `from_http_login()` / challenge 状态 | Passport 安全控件和验证码由百度服务端完成；未猜测动态签名，也不绕过挑战 |
| 公开文章 Item | `BaijiaContentAPI.get_article()` | 匿名 HTTP GET 解析标题、作者、更新时间和正文 |
| 全站文章搜索 | `BaijiaSearchAPI.search_articles()` | 百度网页搜索 `site:` 结果解析；遇安全验证抛 `BaijiaSearchBlocked` |
| 作者动态、互动 | `BaijiaContentAPI.get_user_info/get_user_posts/get_item_metrics()` | 旧版 JSONP 参数和解析器已保留，需有效 Cookie 才能访问动态接口 |
| 网页作品列表 | `BaijiaCreatorAPI.list_web_works()` | 纯 HTTP GET，Cookie 会话即可 |
| 网页图文草稿 | `save_web_draft()`、`delete_web_draft()` | 纯 HTTP 表单；需要 Cookie 和 Creator `token` |
| 网页图文发布 | `publish_web_article()` | 纯 HTTP 表单契约已实现；会创建线上作品，调用前必须自行确认内容 |
| 开放接口图文发布 | `publish_article()`、`query_article_status()` | 需要 App ID / App Token；与网页 Cookie 是两类身份材料 |
| 本地图片上传 | `upload_image()` | 需要 Cookie、Creator `token` 和 App ID；上传后返回托管 URL |

“已实现”表示有明确的 URL、方法、字段和错误校验；是否可用由账号权限和平台实时响应决定。公开发布类方法不会自动重试，遇到超时或无效响应应先查询作品列表再决定后续动作。

## 安装

```powershell
python -m pip install -r requirements.txt
```

依赖只有 `curl_cffi`。Python 3.10+。

## 纯 HTTP 登录态

调用方从自己已经完成登录的同源请求中取得完整 `Cookie` 请求头，并只在内存中交给客户端：

```python
import os
from baijia_apis import BaijiaAuth, BaijiaContentAPI

with BaijiaAuth.from_http_login(
    cookie=os.environ["BAIDU_COOKIES"],
    creator_token=os.environ.get("BAIJIA_CREATOR_TOKEN", ""),
) as auth:
    account = auth.require_logged_in()
    article = BaijiaContentAPI(auth).get_article("1839669810600928968")
    print(account["data"], article["title"])
```

`from_http_login()` 只做一次 `GET https://baijiahao.baidu.com/builder/app/appinfo` 校验；`errno` 不是 `0` 时抛 `BaijiaAuthError`。Cookie 必须包含该请求真正需要的 HttpOnly 字段；`document.cookie` 的值可能不完整。`BaijiaAuth.request()` 限制在 HTTPS 百度域名，带 Cookie 或写请求默认不跟随重定向。

```python
# 已有 Cookie 但暂时不想立即请求时使用
auth = BaijiaAuth.from_cookie(os.environ["BAIDU_COOKIES"])
try:
    auth.require_logged_in()
finally:
    auth.close()
```

### 纯 HTTP 二维码登录

二维码登录不启动浏览器。`common-login` 页面实际使用百度 CAS 的三个请求：

1. `GET https://cas.baidu.com/?action=qrcode&appid=3&t=<毫秒>`，Session 接收 HttpOnly `QGCSSID`，响应是二维码 PNG；
2. 用同一 Session `POST https://cas.baidu.com/?action=qrget`，返回 `errno=30002` 表示等待扫码，`30001/30004` 表示过期或失效，其他状态原样保留；
3. 用户在百度 App 中扫码确认后，提交 CAS 隐藏表单到 `?action=login`。库只接受服务端返回的 `redirecturl`，再用当前内存 Cookie 调用 `to_auth()` 校验 Creator 会话。

```python
from baijia_apis import BaijiaAuth

login = BaijiaAuth.from_qrcode_login()
try:
    challenge = login.start()
    # 把 challenge.image 交给你自己的 UI/二维码查看器；不要写入仓库。
    while True:
        state = login.poll()
        if state.state in {"expired", "invalid", "refresh"}:
            raise RuntimeError(f"二维码失效：{state.errno}")
        if state.state == "approved":
            login.complete()
            auth = login.to_auth()
            break
finally:
    login.close()
```

`from_browser_login()` 仍保留为兼容名称，但会立即抛 `BaijiaLoginProtocolUnavailable`；仓库没有浏览器自动化或浏览器 Cookie 读取。短信密码、动态图片验证码和滑块由百度 Passport/安全控件完成，客户端只报告 `challenge` 或 `BaijiaSearchBlocked`，不伪造参数、不绕过验证。二维码图片和 Cookie 都只存在于进程内，不写日志或文件。

## 搜索与 Item

```python
import os
from baijia_apis import BaijiaAuth, BaijiaContentAPI, BaijiaSearchAPI

with BaijiaAuth.from_http_login(cookie=os.environ["BAIDU_COOKIES"]) as auth:
    search = BaijiaSearchAPI(
        auth,
        # www.baidu.com 的 Cookie 与百家号 Cookie 分开保存、分开发送
        baidu_cookie=os.environ.get("BAIDU_SEARCH_COOKIES", ""),
    )
    found = search.search_articles("咖啡", page=1)
    item = BaijiaContentAPI(auth).get_article(found["items"][0]["id"])
    print(item["title"], len(item["content"]))
```

百度网页搜索可能返回安全验证页；代码会抛 `BaijiaSearchBlocked`，不会自动处理验证码。公开文章读取不要求登录：`BaijiaContentAPI(BaijiaAuth()).get_article(article_id)`。

指定作者时先用 `get_user_info("https://author.baidu.com/home/<id>")`，再调用 `get_user_posts(uk, otherext, ...)`；动态接口需要 Cookie 中的 `Hmery-Time`。

## 网页作品和草稿

```python
import os
from baijia_apis import BaijiaAuth, BaijiaCreatorAPI

with BaijiaAuth.from_http_login(
    cookie=os.environ["BAIDU_COOKIES"],
    creator_token=os.environ["BAIJIA_CREATOR_TOKEN"],
) as auth:
    creator = BaijiaCreatorAPI(auth)
    works = creator.list_web_works(view="draft", page=1)
    draft = creator.save_web_draft("测试草稿标题", "<p>测试正文</p>")
    # 仅在确认是本次创建的 ID 后删除
    creator.delete_web_draft(draft["ret"]["article_id"])
```

草稿保存使用 `POST /pcui/article/save?callback=bjhdraft`，删除使用 `POST /pcui/article/remove`。方法会校验标题、正文、活动字段和数字 ID；不会自动查找或批量删除草稿。

网页图文发布使用 `POST /pcui/article/publish?callback=bjhpublish`。它要求标题、正文和 1–3 个 HTTPS 封面 URL，并校验 `ret.url`；这是有副作用的写请求，默认不会替调用方发布。

## 开放接口图文发布

```python
import os
from baijia_apis import BaijiaAuth, BaijiaCreatorAPI

with BaijiaAuth.from_partner_token(
    os.environ["BAIJIA_APP_ID"], os.environ["BAIJIA_APP_TOKEN"]
) as auth:
    creator = BaijiaCreatorAPI(auth)
    result = creator.publish_article(
        "一篇示例文章",
        "<p>示例正文</p>",
        origin_url="https://example.com/original",
        cover_urls=["https://example.com/cover.jpg"],
    )
    status = creator.query_article_status(result["data"]["article_id"])
```

`origin_url` 必填；标题按公开 SDK 规则为 5–40 字，正文最多 20000 字，封面 1–3 张。未传 `is_original` 时不发送原创声明。没有封面时必须显式传 `allow_draft=True`，否则拒绝请求；草稿不等于公开发布。状态查询一次最多 20 个 ID，并检查 `errno`。

## 兼容旧入口

```python
from baidu_apis import BaiduApis

api = BaiduApis()
# api.get_user_info(user_url, cookies_str)
# api.get_user_posted(uk, otherext, cookies_str)
# api.get_work_info(item, uk, cookies_str)
# api.check_cookies_alive(cookies_strs)
```

## 验证

```powershell
python -m unittest discover -s tests -v
python -m compileall -q baijia_apis tests baidu_apis.py
```

测试只使用假的 HTTP 响应和本地 HTML，不启动浏览器、不读取本机浏览器资料。真实 HTTP 验收应记录脱敏的请求方法、URL、状态码、顶层错误码和必要字段，不保存 Cookie、token 或正文。

## 端点依据

- 旧仓库 `baidu_apis.py`：作者页、动态 JSONP、互动指标。
- [公开 Creator 客户端源码](https://github.com/ai-chen2050/obsidian-wechat-public-platform/blob/master/src/api.ts)：图片上传、网页草稿和网页发布字段。
- [公开百家号 SDK](https://github.com/onlyliu1001/BaiJiaHaoSdk)：App ID / App Token 图文发布和状态查询字段。\n
