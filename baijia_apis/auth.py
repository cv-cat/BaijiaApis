"""百家号会话。扫码登录交给官方网页，Cookie 仅保留在内存中。"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from urllib.parse import urlparse

from curl_cffi import requests


APPINFO_URL = "https://baijiahao.baidu.com/builder/app/appinfo"
LOGIN_URL = "https://baijiahao.baidu.com/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class BaijiaAPIError(RuntimeError):
    """传输或平台响应错误。"""


class BaijiaAuthError(BaijiaAPIError):
    """缺少或失效的身份材料。"""


class BaijiaLoginTimeout(BaijiaAuthError):
    """等待用户完成官方扫码登录超时。"""


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


@contextmanager
def _visible_login_context():
    """启动独立的临时 Chrome 会话；不复用或保存现有浏览器资料。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise BaijiaAuthError("扫码登录需要安装 playwright：python -m pip install playwright") from None

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chrome", headless=False)
        except Exception:
            raise BaijiaAuthError("无法启动可见 Chrome；请确认本机已安装 Chrome") from None
        try:
            context = browser.new_context()
            try:
                yield context
            finally:
                context.close()
        finally:
            browser.close()


def _cookie_header_for_appinfo(context) -> str:
    """仅读取会发送给百家号会话检查 URL 的 Cookie，包括 HttpOnly。"""
    pairs = []
    for cookie in context.cookies([APPINFO_URL]):
        name = cookie.get("name", "")
        value = cookie.get("value", "")
        if not name or any(char in name for char in "=;\r\n"):
            continue
        if any(char in value for char in ";\r\n"):
            continue
        pairs.append(f"{name}={value}")
    return "; ".join(pairs)


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
    def from_qrcode_login(
        cls,
        *,
        timeout: float = 300,
        poll_interval: float = 2,
        request_timeout: float = 20,
        session=None,
    ) -> "BaijiaAuth":
        """显示百家号官方登录页，等待扫码并校验内存中的浏览器 Cookie。

        使用独立的临时 Chrome 会话；用户在可见页面自行打开官方二维码并扫码。
        不调用未核实的二维码协议，也不读写浏览器资料、storage_state 或 Cookie 文件。
        """
        if timeout <= 0 or poll_interval <= 0 or request_timeout <= 0:
            raise ValueError("timeout、poll_interval、request_timeout 必须大于 0")

        last_cookie = ""
        last_error = ""
        next_probe = 0.0
        with _visible_login_context() as context:
            page = context.new_page()
            try:
                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=min(timeout, 30) * 1000)
            except Exception:
                raise BaijiaAuthError("无法打开百家号官方登录页；请检查网络后重试") from None
            print("请在新打开的百家号页面点击登录/注册，使用百度 App 扫描官方二维码并确认。")
            deadline = time.monotonic() + timeout

            while time.monotonic() < deadline:
                try:
                    if page.is_closed():
                        raise BaijiaAuthError("登录窗口已关闭，扫码登录未完成")
                    cookie_header = _cookie_header_for_appinfo(context)
                except BaijiaAuthError:
                    raise
                except Exception:
                    raise BaijiaAuthError("登录窗口已关闭或 Cookie 读取失败") from None

                now = time.monotonic()
                if cookie_header and (cookie_header != last_cookie or now >= next_probe):
                    last_cookie = cookie_header
                    next_probe = now + max(10, poll_interval)
                    candidate = cls.from_cookie(cookie_header, session=session, timeout=request_timeout)
                    try:
                        candidate.require_logged_in()
                    except BaijiaAPIError as exc:
                        # 只记异常类型或平台状态，不记录 Cookie 或响应正文。
                        last_error = str(exc)
                        candidate.close()
                    except Exception:
                        candidate.close()
                        raise BaijiaAuthError("扫码后会话校验请求失败") from None
                    else:
                        return candidate

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    page.wait_for_timeout(min(poll_interval, remaining) * 1000)
                except Exception:
                    raise BaijiaAuthError("登录窗口已关闭，扫码登录未完成") from None

        if last_error:
            raise BaijiaAuthError(f"候选 Cookie 未通过会话校验，等待扫码超时：{last_error}")
        raise BaijiaLoginTimeout("等待百家号官方二维码扫码登录超时")

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
        """查询 Creator 后台会话；无凭据时实测返回 ``errno=10001401``。"""
        if not self.cookie:
            raise BaijiaAuthError("未配置 Cookie")
        return response_json(self.request("GET", APPINFO_URL, headers={"Referer": "https://baijiahao.baidu.com/"}))

    def is_logged_in(self) -> bool:
        return str(self.login_state().get("errno")) == "0"

    def require_logged_in(self) -> dict:
        """只读验证浏览器 Cookie，返回账号信息或给出明确的失败码。"""
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
