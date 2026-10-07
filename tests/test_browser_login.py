"""官方浏览器登录入口的离线契约；不启动 Chrome 或读取真实 Cookie。"""

import contextlib
import io
import unittest
from unittest.mock import patch

from baijia_apis import BaijiaAuth, BaijiaAuthError, BaijiaLoginTimeout
from baijia_apis.auth import APPINFO_URL, LOGIN_URL


class FakeClock:
    def __init__(self):
        self.seconds = 0.0

    def monotonic(self):
        return self.seconds


class FakePage:
    def __init__(self, clock, *, closed=False):
        self.clock = clock
        self.closed = closed
        self.goto_call = None

    def goto(self, url, **kwargs):
        self.goto_call = (url, kwargs)

    def is_closed(self):
        return self.closed

    def wait_for_timeout(self, milliseconds):
        self.clock.seconds += milliseconds / 1000


class FakeContext:
    def __init__(self, clock, cookie_batches, *, closed=False):
        self.page = FakePage(clock, closed=closed)
        self.cookie_batches = list(cookie_batches)
        self.cookie_urls = []
        self.exited = False

    def new_page(self):
        return self.page

    def cookies(self, urls):
        self.cookie_urls.append(urls)
        return self.cookie_batches.pop(0) if len(self.cookie_batches) > 1 else self.cookie_batches[0]


class FakeResponse:
    status_code = 200

    def __init__(self, errno):
        self.errno = errno

    def json(self):
        return {"errno": self.errno, "data": {"user": {"name": "本人"}} if self.errno == 0 else None}


class FakeSession:
    def __init__(self, *errno):
        self.responses = [FakeResponse(code) for code in errno]
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


@contextlib.contextmanager
def fake_browser(context):
    try:
        yield context
    finally:
        context.exited = True


class BrowserLoginTests(unittest.TestCase):
    def run_with_browser(self, context, clock, session, *, entry="from_browser_login", **kwargs):
        with patch("baijia_apis.auth._visible_login_context", lambda: fake_browser(context)), patch(
            "baijia_apis.auth.time.monotonic", clock.monotonic
        ), contextlib.redirect_stdout(io.StringIO()) as output:
            auth = getattr(BaijiaAuth, entry)(session=session, **kwargs)
        return auth, output.getvalue()

    def test_success_uses_scoped_httponly_cookie_and_validates_session(self):
        clock = FakeClock()
        context = FakeContext(clock, [[], [{"name": "BAIDUID", "value": "a=b", "httpOnly": True}]])
        session = FakeSession(0)

        auth, output = self.run_with_browser(context, clock, session, timeout=3, poll_interval=1)

        self.assertEqual(auth.cookie, "BAIDUID=a=b")
        self.assertEqual(context.cookie_urls, [[APPINFO_URL], [APPINFO_URL]])
        self.assertEqual(context.page.goto_call[0], LOGIN_URL)
        self.assertTrue(context.exited)
        self.assertEqual(session.calls[0][0:2], ("GET", APPINFO_URL))
        self.assertEqual(session.calls[0][2]["headers"]["Cookie"], "BAIDUID=a=b")
        self.assertNotIn("a=b", output)
        self.assertIn("手机号或官方二维码", output)

    def test_qrcode_entry_remains_compatible(self):
        clock = FakeClock()
        context = FakeContext(clock, [[{"name": "BAIDUID", "value": "legacy-session"}]])
        session = FakeSession(0, 0)
        auth, _ = self.run_with_browser(
            context, clock, session, entry="from_qrcode_login", timeout=3,
        )
        self.assertEqual(auth.require_logged_in()["data"]["user"]["name"], "本人")

    def test_timeout_without_login_cookie_is_distinct(self):
        clock = FakeClock()
        context = FakeContext(clock, [[]])
        session = FakeSession()
        with self.assertRaisesRegex(BaijiaLoginTimeout, "超时"):
            self.run_with_browser(context, clock, session, timeout=2, poll_interval=1)
        self.assertTrue(context.exited)
        self.assertEqual(session.calls, [])

    def test_invalid_cookie_reports_verification_failure_without_value(self):
        clock = FakeClock()
        context = FakeContext(clock, [[{"name": "BAIDUID", "value": "private-token", "httpOnly": True}]])
        session = FakeSession(10001401)
        with self.assertRaises(BaijiaAuthError) as raised:
            self.run_with_browser(context, clock, session, timeout=2, poll_interval=1)
        self.assertIn("未通过会话校验", str(raised.exception))
        self.assertIn("10001401", str(raised.exception))
        self.assertNotIn("private-token", str(raised.exception))
        self.assertTrue(context.exited)

    def test_same_cookie_is_rechecked_after_account_confirmation(self):
        clock = FakeClock()
        context = FakeContext(clock, [[{"name": "BAIDUID", "value": "account-session"}]])
        session = FakeSession(10001401, 0)
        auth, _ = self.run_with_browser(context, clock, session, timeout=12, poll_interval=1)
        self.assertEqual(auth.cookie, "BAIDUID=account-session")
        self.assertEqual(len(session.calls), 2)

    def test_closed_window_fails_before_reading_cookie(self):
        clock = FakeClock()
        context = FakeContext(clock, [[]], closed=True)
        with self.assertRaisesRegex(BaijiaAuthError, "窗口已关闭"):
            self.run_with_browser(context, clock, FakeSession(), timeout=2)
        self.assertEqual(context.cookie_urls, [])
        self.assertTrue(context.exited)


if __name__ == "__main__":
    unittest.main()
