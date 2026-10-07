"""百家号会话。扫码/短信流程尚无核实的请求契约，使用现有 Cookie。"""

from __future__ import annotations

import json
from urllib.parse import urlparse

from curl_cffi import requests


APPINFO_URL = "https://baijiahao.baidu.com/builder/app/appinfo"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class BaijiaAPIError(RuntimeError):
    """传输或平台响应错误。"""


class BaijiaAuthError(BaijiaAPIError):
    """缺少或失效的身份材料。"""


class BaijiaParseError(BaijiaAPIError):
    """平台返回内容与当前解析契约不符。"""


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
        response = self.session.request(method.upper(), url, headers=request_headers, **kwargs)
        if not 200 <= response.status_code < 400:
            raise BaijiaAPIError(f"HTTP {response.status_code}: {parsed.path}")
        return response

    def login_state(self) -> dict:
        """查询 Creator 后台会话；无凭据时实测返回 ``errno=10001401``。"""
        if not self.cookie:
            raise BaijiaAuthError("未配置 Cookie")
        return response_json(self.request("GET", APPINFO_URL, headers={"Referer": "https://baijiahao.baidu.com/"}))

    def is_logged_in(self) -> bool:
        return self.login_state().get("errno") == 0

    def refresh_creator_token(self) -> str:
        """按 Creator 客户端已观察的 HEAD 请求读取响应头 token。"""
        if not self.cookie or not self.creator_token:
            raise BaijiaAuthError("刷新 Creator token 需要 Cookie 与现有 token")
        response = self.request("HEAD", APPINFO_URL, headers={"token": self.creator_token})
        token = response.headers.get("token")
        if not token:
            raise BaijiaAuthError("响应没有 Creator token；请在浏览器重新登录")
        self.creator_token = token
        return token

    def close(self) -> None:
        if self._owns_session:
            self.session.close()

    def __enter__(self) -> "BaijiaAuth":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
