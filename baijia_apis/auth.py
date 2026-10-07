"""百家号纯 HTTP 会话与百度 Passport challenge。

仓库不启动浏览器、不读取浏览器资料。网页登录态可由 Cookie/Token 提供，
二维码使用浏览器 Network 中核实的百度 Passport JSONP challenge；短信、图片验证码和滑块仍由
百度 Passport/安全控件完成，客户端只报告服务端返回的挑战状态。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from urllib.parse import quote, urlencode, urlparse

from curl_cffi import requests


APPINFO_URL = "https://baijiahao.baidu.com/builder/app/appinfo"
BAIJIA_LOGIN_PAGE = "https://baijiahao.baidu.com/builder/theme/bjh/login"
ALLOC_TK_URL = "https://baijiahao.baidu.com/user-ui/cms/allocTk"
GET_PASSINFO_URL = "https://baijiahao.baidu.com/user-ui/cms/getPassinfo"
LOCK_UC_LOGIN_URL = "https://baijiahao.baidu.com/user-ui/cms/lockUcLogin"
LOGIN_INFO_URL = "https://baijiahao.baidu.com/userb/user/loginInfo"
PASSPORT_BASE_URL = "https://passport.baidu.com"
PASSPORT_QR_INIT_URL = f"{PASSPORT_BASE_URL}/v2/api/getqrcode"
PASSPORT_QR_POLL_URL = f"{PASSPORT_BASE_URL}/channel/unicast"
PASSPORT_QR_LOGIN_URL = f"{PASSPORT_BASE_URL}/v3/login/main/qrbdusslogin"
PASSPORT_CAP_INIT_URL = f"{PASSPORT_BASE_URL}/cap/init"
PASSPORT_PRODUCT = "bjh"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
)


class BaijiaAPIError(RuntimeError):
    """传输或平台响应错误。"""


class BaijiaAuthError(BaijiaAPIError):
    """缺少或失效的身份材料。"""


class BaijiaLoginTimeout(BaijiaAuthError):
    """兼容旧调用方；纯 HTTP 登录不使用等待窗口。"""


class BaijiaLoginProtocolUnavailable(BaijiaAuthError):
    """二维码、短信或验证码登录协议尚无可核实的请求证据。"""


class BaijiaParseError(BaijiaAPIError):
    """平台返回内容与当前解析契约不符。"""


@dataclass(frozen=True)
class BaijiaQRCodeChallenge:
    """一次百度 Passport 二维码挑战。

    ``image`` 只保存在调用方内存中；库不写入二维码文件，因为二维码本身
    带有一次性登录票据。``sign`` 仅用于同一会话的轮询。
    """

    image: bytes
    content_type: str
    state: str = "waiting"
    errno: int = 1
    sign: str = ""
    gid: str = ""


@dataclass(frozen=True)
class BaijiaQRCodePoll:
    """Passport ``channel/unicast`` 的脱敏状态。"""

    state: str
    errno: int
    message: str = ""
    redirect_url: str = ""
    channel_status: str = ""


def parse_cookies(cookie_header: str) -> dict[str, str]:
    """只拆第一个等号，保留 Cookie 值中的 ``=``。"""
    if "\r" in cookie_header or "\n" in cookie_header:
        raise ValueError("Cookie 不能包含换行符")
    result = {}
    for part in cookie_header.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name:
            result[name] = value
    return result


def response_json(response) -> dict:
    try:
        result = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise BaijiaParseError("接口返回的不是 JSON") from exc
    if not isinstance(result, dict):
        raise BaijiaParseError("接口应返回 JSON 对象")
    return result


class BaijiaQRCodeLogin:
    """百度 Passport 二维码登录的纯 HTTP 会话。

    该契约来自百家号登录页 Network：先建立 Creator 会话，再用 Passport
    getqrcode 取得 JSONP 票据，使用同一个 Session 轮询 channel/unicast。
    扫码确认后的 channel_v 通过 v3/login/main/qrbdusslogin 换取登录 Cookie。
    所有 callback、字段顺序和 URL 编码都由本类显式构造；不会执行页面脚本、
    读取浏览器 Cookie 或绕过图片验证码/滑块。
    """

    _ALLOWED_HOSTS = {"passport.baidu.com", "baijiahao.baidu.com"}

    def __init__(
        self,
        *,
        session=None,
        timeout: float = 20,
        gid: str = "",
        product: str = PASSPORT_PRODUCT,
        qrloginfrom: str = "pc",
        oauth_log: str = "",
        log_page: str = "",
        bootstrap: bool = True,
    ):
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0")
        for label, value in (
            ("gid", gid),
            ("product", product),
            ("qrloginfrom", qrloginfrom),
            ("oauth_log", oauth_log),
            ("log_page", log_page),
        ):
            if "\r" in str(value) or "\n" in str(value):
                raise ValueError(f"{label} 不能包含换行符")
        self.session = session if session is not None else requests.Session()
        self._owns_session = session is None
        self.timeout = timeout
        self.gid = gid.strip().upper() or str(uuid.uuid4()).upper()
        self.product = product
        self.qrloginfrom = qrloginfrom
        self.oauth_log = oauth_log
        self.log_page = log_page or f"traceId:pc_loginv4_{int(time.time())},logPage:loginv4"
        self.bootstrap = bool(bootstrap)
        self.callback = f"tangram_guid_{int(time.time() * 1000)}"
        self._started = False
        self._sign = ""
        self._channel_v: dict[str, object] = {}
        self._last_poll: BaijiaQRCodePoll | None = None
        self.last_request: dict[str, object] = {}

    @staticmethod
    def _parse_jsonp(text: str) -> dict:
        if not text:
            raise BaijiaParseError("Passport JSONP 响应为空")
        start = text.find("(")
        end = text.rfind(")")
        if start < 0 or end <= start:
            raise BaijiaParseError("Passport 响应不是 JSONP")
        try:
            value = json.loads(text[start + 1:end])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise BaijiaParseError("Passport JSONP 数据无效") from exc
        if not isinstance(value, dict):
            raise BaijiaParseError("Passport JSONP 应返回对象")
        return value

    def _request(self, method: str, url: str, **kwargs):
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or host not in self._ALLOWED_HOSTS:
            raise ValueError("二维码会话只允许请求 HTTPS 百度 Passport/百家号域名")
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": BAIJIA_LOGIN_PAGE + "?tab=uc",
        }
        headers.update(kwargs.pop("headers", {}) or {})
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("allow_redirects", False)
        kwargs.setdefault("impersonate", "chrome150")
        response = self.session.request(method.upper(), url, headers=headers, **kwargs)
        self.last_request = {"method": method.upper(), "url": url, "status": response.status_code}
        return response

    def _bootstrap_session(self) -> None:
        page = self._request(
            "GET",
            BAIJIA_LOGIN_PAGE,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Upgrade-Insecure-Requests": "1",
            },
            allow_redirects=True,
        )
        if not 200 <= page.status_code < 400:
            raise BaijiaAPIError(f"百家号登录页 HTTP {page.status_code}")
        response = self._request(
            "POST",
            ALLOC_TK_URL,
            data=b"",
            headers={
                "Accept": "application/json, text/plain, */*",
                "Content-Length": "0",
                "Origin": "https://baijiahao.baidu.com",
                "Referer": BAIJIA_LOGIN_PAGE,
            },
        )
        if not 200 <= response.status_code < 300:
            raise BaijiaAPIError(f"allocTk HTTP {response.status_code}")
        json_headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": BAIJIA_LOGIN_PAGE,
            "token": "undefined",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        passinfo = self._request("GET", GET_PASSINFO_URL + "?", headers=json_headers)
        if not 200 <= passinfo.status_code < 300:
            raise BaijiaAPIError(f"getPassinfo HTTP {passinfo.status_code}")
        locked = self._request(
            "POST",
            LOCK_UC_LOGIN_URL,
            data=b"",
            headers={
                **json_headers,
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": "0",
                "Origin": "https://baijiahao.baidu.com",
            },
        )
        if not 200 <= locked.status_code < 300:
            raise BaijiaAPIError(f"lockUcLogin HTTP {locked.status_code}")
        login_info = self._request(
            "GET",
            LOGIN_INFO_URL + "?uc_login=0",
            headers=json_headers,
        )
        if not 200 <= login_info.status_code < 300:
            raise BaijiaAPIError(f"loginInfo HTTP {login_info.status_code}")

    def _jsonp_url(self, base: str, pairs: list[tuple[str, str]]) -> str:
        return base + "?" + urlencode(pairs)

    def start(self) -> BaijiaQRCodeChallenge:
        """按浏览器字段顺序获取 Passport 二维码图片。"""

        if self.bootstrap:
            self._bootstrap_session()
        now = int(time.time() * 1000)
        pairs = [
            ("lp", "pc"),
            ("qrloginfrom", self.qrloginfrom),
            ("gid", self.gid),
            ("oauthLog", self.oauth_log),
            ("callback", self.callback),
            ("apiver", "v3"),
            ("tt", str(now)),
            ("tpl", self.product),
            ("logPage", self.log_page),
            ("_", str(now)),
        ]
        init_url = self._jsonp_url(PASSPORT_QR_INIT_URL, pairs)
        response = self._request(
            "GET",
            init_url,
            headers={
                "Referer": "https://baijiahao.baidu.com/",
                "Sec-Fetch-Dest": "script",
                "Sec-Fetch-Mode": "no-cors",
                "Sec-Fetch-Site": "same-site",
            },
        )
        if not 200 <= response.status_code < 300:
            raise BaijiaAPIError(f"Passport 二维码初始化 HTTP {response.status_code}")
        data = self._parse_jsonp(response.text)
        try:
            errno = int(data.get("errno"))
        except (TypeError, ValueError) as exc:
            raise BaijiaParseError("Passport 二维码响应缺少 errno") from exc
        sign = str(data.get("sign") or "")
        image_url = str(data.get("imgurl") or "")
        if image_url.startswith("//"):
            image_url = "https:" + image_url
        elif "://" not in image_url:
            image_url = "https://" + image_url.lstrip("/")
        if errno != 0 or not sign or not image_url:
            raise BaijiaAuthError(f"Passport 二维码初始化失败：errno={errno}")
        image = self._request(
            "GET",
            image_url,
            headers={
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Referer": "https://baijiahao.baidu.com/",
                "Sec-Fetch-Dest": "image",
                "Sec-Fetch-Mode": "no-cors",
                "Sec-Fetch-Site": "same-site",
            },
        )
        if not 200 <= image.status_code < 300:
            raise BaijiaAPIError(f"Passport 二维码图片 HTTP {image.status_code}")
        content_type = (image.headers.get("content-type") or "").split(";", 1)[0].lower()
        if not image.content or not content_type.startswith("image/"):
            raise BaijiaParseError("Passport 二维码响应不是图片")
        self._sign = sign
        self._started = True
        self._last_poll = BaijiaQRCodePoll(state="waiting", errno=1, message="qrcode inited")
        return BaijiaQRCodeChallenge(
            image=bytes(image.content),
            content_type=content_type,
            state="waiting",
            errno=errno,
            sign=sign,
            gid=self.gid,
        )

    def poll(self) -> BaijiaQRCodePoll:
        """按浏览器 JSONP 查询一次扫码状态。"""

        if not self._started or not self._sign:
            raise BaijiaAuthError("请先调用 BaijiaQRCodeLogin.start() 获取二维码")
        now = int(time.time() * 1000)
        pairs = [
            ("channel_id", self._sign),
            ("gid", self.gid),
            ("tpl", self.product),
            ("_sdkFrom", "1"),
            ("callback", self.callback),
            ("apiver", "v3"),
            ("tt", str(now)),
            ("_", str(now)),
        ]
        response = self._request(
            "GET",
            self._jsonp_url(PASSPORT_QR_POLL_URL, pairs),
            headers={
                "Referer": "https://baijiahao.baidu.com/",
                "Sec-Fetch-Dest": "script",
                "Sec-Fetch-Mode": "no-cors",
                "Sec-Fetch-Site": "same-site",
            },
        )
        if not 200 <= response.status_code < 300:
            raise BaijiaAPIError(f"Passport 二维码状态 HTTP {response.status_code}")
        data = self._parse_jsonp(response.text)
        try:
            errno = int(data.get("errno"))
        except (TypeError, ValueError) as exc:
            raise BaijiaParseError("Passport 二维码状态缺少 errno") from exc
        message = str(data.get("errmsg") or data.get("prompt") or "")
        channel = data.get("channel_v")
        if isinstance(channel, str):
            try:
                channel = json.loads(channel)
            except (TypeError, ValueError, json.JSONDecodeError):
                channel = {}
        if isinstance(channel, dict):
            self._channel_v = dict(channel)
        channel_status = str(data.get("status") or data.get("channel_status") or "")
        if channel_status == "0" and self._channel_v:
            state = "scanned"
        elif errno == 1:
            state = "waiting"
        elif errno == 0:
            state = "scanned" if self._channel_v else "approved"
        elif errno in {2, 3}:
            state = "expired"
        else:
            state = "challenge"
        result = BaijiaQRCodePoll(
            state=state,
            errno=errno,
            message=message,
            redirect_url=str(data.get("url") or data.get("redirecturl") or ""),
            channel_status=channel_status,
        )
        self._last_poll = result
        return result

    def complete(self) -> str:
        """用扫码确认返回的 channel_v 调用 Passport QR 登录接口。"""

        if not self._started:
            raise BaijiaAuthError("请先调用 BaijiaQRCodeLogin.start() 获取二维码")
        current = self._last_poll or self.poll()
        if current.state not in {"scanned", "approved"} or not self._channel_v:
            raise BaijiaAuthError(f"二维码尚未确认：errno={current.errno}")
        bduss = str(self._channel_v.get("v") or "")
        user = str(self._channel_v.get("u") or "")
        if not bduss:
            raise BaijiaParseError("Passport channel_v 缺少 bduss")
        now = int(time.time() * 1000)
        pairs = [
            ("v", str(now)),
            ("bduss", bduss),
            ("u", quote(user, safe="")),
            ("loginVersion", "v4"),
            ("qrcode", "1"),
            ("tpl", self.product),
            ("callback", self.callback),
            ("_", str(now)),
        ]
        response = self._request(
            "GET",
            self._jsonp_url(PASSPORT_QR_LOGIN_URL, pairs),
            headers={
                "Referer": "https://baijiahao.baidu.com/",
                "Sec-Fetch-Dest": "script",
                "Sec-Fetch-Mode": "no-cors",
                "Sec-Fetch-Site": "same-site",
            },
        )
        if not 200 <= response.status_code < 300:
            raise BaijiaAPIError(f"Passport QR 登录 HTTP {response.status_code}")
        data = self._parse_jsonp(response.text)
        err = data.get("errInfo")
        if isinstance(err, dict):
            code = str(err.get("no") or "0")
            message = str(err.get("msg") or err.get("msgDetail") or "")
        else:
            code = str(data.get("errno") or "0")
            message = str(data.get("errmsg") or "")
        if code not in {"0", "200"}:
            raise BaijiaAuthError(f"Passport QR 登录失败：errno={code} {message}".strip())
        return str(data.get("url") or data.get("redirecturl") or data.get("jumpUrl") or "")

    def cookie_header(self) -> str:
        """从当前内存会话生成 Cookie 头，不把值写入日志或文件。"""

        jar = getattr(self.session, "cookies", None)
        if jar is None:
            return ""
        if hasattr(jar, "get_dict"):
            values = jar.get_dict()
        elif isinstance(jar, dict):
            values = jar
        else:
            values = {}
            try:
                for cookie in jar:
                    values[getattr(cookie, "name", "")] = getattr(cookie, "value", "")
            except TypeError:
                values = {}
        return "; ".join(f"{name}={value}" for name, value in values.items() if name)

    def to_auth(self) -> "BaijiaAuth":
        """扫码确认后用当前会话 Cookie 建立并校验 BaijiaAuth。"""

        cookie = self.cookie_header()
        if not cookie:
            raise BaijiaAuthError("Passport 已确认但会话没有返回 Cookie")
        return BaijiaAuth.from_http_login(cookie=cookie, session=self.session, timeout=self.timeout)

    def close(self) -> None:
        if self._owns_session:
            self.session.close()

    def __enter__(self) -> "BaijiaQRCodeLogin":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


class BaijiaAuth:
    """管理 Cookie、Creator token 或开放接口的 App ID / Token。"""

    def __init__(
        self,
        *,
        cookie: str = "",
        creator_token: str = "",
        app_id: str = "",
        app_token: str = "",
        session=None,
        timeout: float = 20,
    ):
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0")
        for label, value in (("creator_token", creator_token), ("app_id", app_id), ("app_token", app_token)):
            if "\r" in value or "\n" in value:
                raise ValueError(f"{label} 不能包含换行符")
        if app_token and not app_id:
            raise ValueError("app_token 需要对应的 app_id")
        self.cookie = cookie.strip()
        self.cookies = parse_cookies(self.cookie)
        self.creator_token = creator_token.strip()
        self.app_id = app_id.strip()
        self.app_token = app_token.strip()
        self.timeout = timeout
        self.session = session if session is not None else requests.Session()
        self._owns_session = session is None

    @classmethod
    def from_cookie(cls, cookie: str, **kwargs) -> "BaijiaAuth":
        if not parse_cookies(cookie):
            raise BaijiaAuthError("需要有效的 Cookie 字符串")
        return cls(cookie=cookie, **kwargs)

    @classmethod
    def from_http_login(
        cls,
        *,
        cookie: str,
        creator_token: str = "",
        app_id: str = "",
        app_token: str = "",
        session=None,
        timeout: float = 20,
    ) -> "BaijiaAuth":
        """从调用方提供的网页登录材料创建并验证纯 HTTP 会话。

        ``cookie`` 必须来自同一个百家号请求的完整 Cookie 头（包括需要的
        HttpOnly 字段）。方法只向 ``builder/app/appinfo`` 发一次验证请求，
        不打开浏览器，也不持久化 Cookie。
        """
        auth = cls.from_cookie(
            cookie,
            creator_token=creator_token,
            app_id=app_id,
            app_token=app_token,
            session=session,
            timeout=timeout,
        )
        try:
            auth.require_logged_in()
        except Exception:
            auth.close()
            raise
        return auth

    @classmethod
    def from_browser_login(cls, **_kwargs) -> "BaijiaAuth":
        """兼容旧名称；浏览器自动化已移除。"""
        raise BaijiaLoginProtocolUnavailable(
            "已移除浏览器登录；请用 from_http_login(cookie=...) 提供已核实的 Cookie。"
        )

    @classmethod
    def start_qrcode_login(cls, **kwargs) -> BaijiaQRCodeLogin:
        """创建纯 HTTP Passport 二维码会话；调用方负责展示图片和提示用户扫码。"""

        return BaijiaQRCodeLogin(**kwargs)

    @classmethod
    def from_qrcode_login(
        cls,
        **_kwargs,
    ) -> BaijiaQRCodeLogin:
        """兼容入口，返回可轮询的纯 HTTP 二维码会话。

        该方法不会阻塞等待，也不会打开浏览器。典型用法是
        ``challenge = BaijiaAuth.from_qrcode_login(); challenge.start()``，
        之后循环 ``poll()``，在 ``approved``/``scanned`` 状态调用
        ``complete()`` 和 ``to_auth()``。
        """

        return cls.start_qrcode_login(**_kwargs)

    @classmethod
    def from_partner_token(cls, app_id: str, app_token: str, **kwargs) -> "BaijiaAuth":
        if not app_id or not app_token:
            raise BaijiaAuthError("需要 App ID 和 App Token")
        return cls(app_id=app_id, app_token=app_token, **kwargs)

    def request(self, method: str, url: str, *, headers=None, use_cookie: bool = True, **kwargs):
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (hostname == "baidu.com" or hostname.endswith(".baidu.com")):
            raise ValueError("只允许请求 HTTPS 百度域名")
        request_headers = {"User-Agent": USER_AGENT}
        if use_cookie and self.cookie:
            request_headers["Cookie"] = self.cookie
        if headers:
            request_headers.update(headers)
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("impersonate", "chrome150")
        # Cookie 与开放接口 token 不应跟随平台跳转，避免把登录页当作接口成功。
        if (self.cookie and use_cookie) or method.upper() != "GET":
            kwargs.setdefault("allow_redirects", False)
        response = self.session.request(method.upper(), url, headers=request_headers, **kwargs)
        if not 200 <= response.status_code < 300:
            raise BaijiaAPIError(f"HTTP {response.status_code}: {parsed.path}")
        return response

    def login_state(self) -> dict:
        """查询 Creator 后台会话；无凭据时返回平台登录错误码。"""
        if not self.cookie:
            raise BaijiaAuthError("未配置 Cookie")
        return response_json(self.request("GET", APPINFO_URL, headers={"Referer": "https://baijiahao.baidu.com/"}))

    def is_logged_in(self) -> bool:
        return str(self.login_state().get("errno")) == "0"

    def require_logged_in(self) -> dict:
        """只读验证 Cookie 会话，返回账号信息或给出明确的失败码。"""
        state = self.login_state()
        code = state.get("errno")
        if str(code) != "0":
            raise BaijiaAuthError(f"Creator Cookie 未通过登录检测：errno={code}")
        return state

    def refresh_creator_token(self) -> str:
        """按 Creator 客户端已观察的 HEAD 请求读取响应头 token。"""
        if not self.cookie or not self.creator_token:
            raise BaijiaAuthError("刷新 Creator token 需要 Cookie 与现有 token")
        response = self.request("HEAD", APPINFO_URL, headers={"token": self.creator_token})
        token = response.headers.get("token")
        if not token:
            raise BaijiaAuthError("响应没有 Creator token；请重新取得 Creator token")
        self.creator_token = token
        return token

    def close(self) -> None:
        if self._owns_session:
            self.session.close()

    def __enter__(self) -> "BaijiaAuth":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
