"""百家号纯 HTTP 会话与 CAS challenge。

仓库不启动浏览器、不读取浏览器资料。网页登录态可由 Cookie/Token 提供，
二维码使用已核实的百度 CAS HTTP challenge；短信、图片验证码和滑块仍由
百度 Passport/安全控件完成，客户端只报告服务端返回的挑战状态。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from urllib.parse import quote, urlparse

from curl_cffi import requests


APPINFO_URL = "https://baijiahao.baidu.com/builder/app/appinfo"
CAS_BASE_URL = "https://cas.baidu.com/"
CAS_QR_IMAGE_URL = f"{CAS_BASE_URL}?action=qrcode&appid=3"
CAS_QR_STATUS_URL = f"{CAS_BASE_URL}?action=qrget"
CAS_LOGIN_URL = f"{CAS_BASE_URL}?action=login"
CAS_APP_ID = "647"
CAS_LOGIN_FROM = "https://baijiahao.baidu.com/builder/theme/bjh/login?tab=uc"
CAS_STATIC_PAGE = "https://baijiahao.baidu.com/builder/fe-react/casV3Jump.html"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
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
    """一次百度 CAS 二维码挑战。

    ``image`` 只保存在调用方内存中；库不写入二维码文件，因为二维码本身
    带有一次性登录票据。``errno`` 和 ``state`` 来自 CAS 的原始状态。
    """

    image: bytes
    content_type: str
    state: str = "waiting"
    errno: int = 30002


@dataclass(frozen=True)
class BaijiaQRCodePoll:
    """CAS ``qrget`` 的脱敏状态。"""

    state: str
    errno: int
    message: str = ""
    redirect_url: str = ""


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
    """百度 CAS 二维码登录的纯 HTTP 会话。

    页面使用 ``common-login`` SDK 调用三个已核实的请求：取二维码、轮询
    ``qrget``、在扫码确认后提交隐藏表单。这里保留同一个 ``curl_cffi``
    Session，使 CAS 的 HttpOnly ``QGCSSID`` 自动随请求发送。二维码、扫码
    确认和图片验证码由百度客户端/服务端完成；库只报告状态，不尝试伪造
    或绕过挑战。
    """

    _ALLOWED_HOSTS = {"cas.baidu.com"}

    def __init__(
        self,
        *,
        session=None,
        timeout: float = 20,
        app_id: str = CAS_APP_ID,
        fromu: str = CAS_LOGIN_FROM,
        selfu: str = CAS_STATIC_PAGE,
        jumppage: str = CAS_STATIC_PAGE,
        acs_token: str = "",
    ):
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0")
        for label, value in (
            ("app_id", app_id),
            ("fromu", fromu),
            ("selfu", selfu),
            ("jumppage", jumppage),
            ("acs_token", acs_token),
        ):
            if "\r" in value or "\n" in value:
                raise ValueError(f"{label} 不能包含换行符")
        for label, value in (("fromu", fromu), ("selfu", selfu), ("jumppage", jumppage)):
            parsed = urlparse(value)
            if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith("baidu.com"):
                raise ValueError(f"{label} 必须是 HTTPS 百度地址")
        self.session = session if session is not None else requests.Session()
        self._owns_session = session is None
        self.timeout = timeout
        self.app_id = str(app_id)
        self.fromu = fromu
        self.selfu = selfu
        self.jumppage = jumppage
        self.acs_token = acs_token.strip()
        self._started = False
        self._last_poll: BaijiaQRCodePoll | None = None

    @staticmethod
    def _with_acs_token(url: str, acs_token: str) -> str:
        if not acs_token:
            return url
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}acs-token={quote(acs_token, safe='')}"

    def _request(self, method: str, url: str, **kwargs):
        parsed = urlparse(url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in self._ALLOWED_HOSTS:
            raise ValueError("二维码会话只允许请求 HTTPS cas.baidu.com")
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/html, */*",
            "Referer": CAS_LOGIN_FROM,
            "Origin": "https://baijiahao.baidu.com",
        }
        headers.update(kwargs.pop("headers", {}) or {})
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("allow_redirects", False)
        kwargs.setdefault("impersonate", "chrome131")
        return self.session.request(method.upper(), url, headers=headers, **kwargs)

    def start(self) -> BaijiaQRCodeChallenge:
        """获取二维码图片并建立 CAS ``QGCSSID`` 会话。"""

        url = self._with_acs_token(CAS_QR_IMAGE_URL, self.acs_token)
        response = self._request("GET", url, params={"t": str(int(time.time() * 1000))})
        if not 200 <= response.status_code < 300:
            raise BaijiaAPIError(f"CAS 二维码 HTTP {response.status_code}")
        content_type = (response.headers.get("content-type") or "").split(";", 1)[0].lower()
        if not response.content or not content_type.startswith("image/"):
            raise BaijiaParseError("CAS 二维码响应不是图片")
        self._started = True
        self._last_poll = BaijiaQRCodePoll(state="waiting", errno=30002, message="qrcode inited")
        return BaijiaQRCodeChallenge(
            image=bytes(response.content),
            content_type=content_type,
            state="waiting",
            errno=30002,
        )

    def poll(self) -> BaijiaQRCodePoll:
        """轮询一次扫码状态；不自动重试，也不修改挑战。"""

        if not self._started:
            raise BaijiaAuthError("请先调用 BaijiaQRCodeLogin.start() 获取二维码")
        response = self._request("POST", self._with_acs_token(CAS_QR_STATUS_URL, self.acs_token))
        if not 200 <= response.status_code < 300:
            raise BaijiaAPIError(f"CAS 二维码状态 HTTP {response.status_code}")
        data = response_json(response)
        try:
            errno = int(data.get("errno"))
        except (TypeError, ValueError) as exc:
            raise BaijiaParseError("CAS 二维码状态缺少 errno") from exc
        message = str(data.get("errmsg") or data.get("e") or "")
        if errno == 30002:
            state = "waiting"
        elif errno == 30003:
            state = "scanned"
        elif errno == 30001:
            state = "expired"
        elif errno == 30004:
            state = "invalid"
        elif errno == 30005:
            state = "refresh"
        elif errno == 0:
            state = "approved"
        else:
            state = "challenge"
        result = BaijiaQRCodePoll(state=state, errno=errno, message=message)
        self._last_poll = result
        return result

    def _login_form(self) -> dict[str, str]:
        # 与 common-login/main.js 的 uc-qrcode-form 保持字段和取值一致。
        return {
            "appid": self.app_id,
            "specialFlag": "qrcode",
            "senderr": "1",
            "fromu": self.fromu,
            "selfu": self.selfu,
            "jumppage": self.jumppage,
            "isajax": "1",
            "version": "2.3.0",
        }

    @staticmethod
    def _extract_redirect(text: str) -> str:
        if not text:
            return ""
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            data = None
        if isinstance(data, dict):
            for key in ("redirecturl", "redirect_url", "url"):
                value = data.get(key)
                if value:
                    return str(value)
        # isajax 的 iframe 回调通常是 JSON；这里仅提取服务端明确返回的 URL，
        # 不执行页面脚本，也不拼接未知参数。
        match = re.search(r'"redirecturl"\s*:\s*"([^"]+)"', text)
        return match.group(1) if match else ""

    def complete(self) -> str:
        """用扫码确认后的 CAS 票据提交隐藏表单，返回服务端重定向地址。

        只有 ``errno=0`` 才会提交。若仍是 ``waiting``/``scanned``，调用方
        应继续轮询，让用户在百度 App 中完成确认。
        """

        if not self._started:
            raise BaijiaAuthError("请先调用 BaijiaQRCodeLogin.start() 获取二维码")
        current = self._last_poll or self.poll()
        if current.state != "approved":
            raise BaijiaAuthError(f"二维码尚未确认：errno={current.errno}")
        response = self._request(
            "POST",
            self._with_acs_token(CAS_LOGIN_URL, self.acs_token),
            data=self._login_form(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code in {301, 302, 303, 307, 308}:
            redirect = response.headers.get("location", "")
        else:
            if not 200 <= response.status_code < 300:
                raise BaijiaAPIError(f"CAS 二维码确认 HTTP {response.status_code}")
            redirect = self._extract_redirect(response.text)
        if not redirect:
            data = None
            try:
                data = response_json(response)
            except BaijiaParseError:
                pass
            errno = data.get("errno") if isinstance(data, dict) else "unknown"
            message = data.get("e") or data.get("errmsg") if isinstance(data, dict) else ""
            raise BaijiaAuthError(f"CAS 二维码确认未返回重定向：errno={errno} {message}".strip())
        return redirect

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
            raise BaijiaAuthError("CAS 已确认但会话没有返回 Cookie")
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
        """创建纯 HTTP CAS 二维码会话；调用方负责展示图片和提示用户扫码。"""

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
        kwargs.setdefault("impersonate", "chrome101")
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
