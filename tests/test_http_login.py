import unittest

from baijia_apis import (
    BaijiaAuth,
    BaijiaAuthError,
    BaijiaQRCodeLogin,
)
from baijia_apis.auth import APPINFO_URL


class FakeResponse:
    status_code = 200

    def __init__(self, data, *, content=b"", headers=None, text=None, status_code=200):
        self._data = data
        self.headers = headers or {}
        self.content = content
        self.status_code = status_code
        self.text = text if text is not None else ("" if data is None else str(data))

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    @property
    def cookies(self):
        return {"QGCSSID": "ephemeral"}


class HttpLoginTests(unittest.TestCase):
    def test_from_http_login_validates_cookie_with_one_http_request(self):
        session = FakeSession(FakeResponse({"errno": 0, "data": {"user": {"name": "本人"}}}))
        auth = BaijiaAuth.from_http_login(
            cookie="BAIDUID=a=b; Hmery-Time=123",
            session=session,
        )
        self.assertEqual(auth.cookie, "BAIDUID=a=b; Hmery-Time=123")
        self.assertEqual(auth.require_logged_in.__self__, auth)
        self.assertEqual(len(session.calls), 1)
        method, url, kwargs = session.calls[0]
        self.assertEqual((method, url), ("GET", APPINFO_URL))
        self.assertEqual(kwargs["headers"]["Cookie"], "BAIDUID=a=b; Hmery-Time=123")
        self.assertFalse(kwargs["allow_redirects"])

    def test_from_http_login_rejects_invalid_session_without_leaking_cookie(self):
        session = FakeSession(FakeResponse({"errno": 10001401, "data": None}))
        with self.assertRaisesRegex(BaijiaAuthError, "10001401") as raised:
            BaijiaAuth.from_http_login(cookie="BAIDUID=private-token", session=session)
        self.assertNotIn("private-token", str(raised.exception))

    def test_legacy_browser_entry_has_no_automation_side_effect(self):
        with self.assertRaises(Exception) as raised:
            BaijiaAuth.from_browser_login()
        self.assertIn("浏览器", str(raised.exception))

    def test_qrcode_login_is_http_challenge(self):
        session = FakeSession(
            FakeResponse(
                None,
                content=b"PNG",
                headers={"content-type": "image/png"},
            ),
            FakeResponse({"errno": 30002, "errmsg": "qrcode inited"}),
        )
        login = BaijiaAuth.from_qrcode_login(session=session)
        challenge = login.start()
        self.assertEqual(challenge.state, "waiting")
        self.assertEqual(challenge.image, b"PNG")
        status = login.poll()
        self.assertEqual(status.errno, 30002)
        self.assertEqual([call[0] for call in session.calls], ["GET", "POST"])
        self.assertIn("action=qrcode", session.calls[0][1])
        self.assertIn("action=qrget", session.calls[1][1])

    def test_qrcode_complete_uses_observed_hidden_form(self):
        session = FakeSession(
            FakeResponse(None, content=b"PNG", headers={"content-type": "image/png"}),
            FakeResponse({"errno": 0}),
            FakeResponse(
                None,
                text='{"redirecturl":"https://baijiahao.baidu.com/builder/fe-react/casV3Jump.html?castk=opaque"}',
            ),
        )
        login = BaijiaQRCodeLogin(session=session)
        login.start()
        self.assertEqual(login.poll().state, "approved")
        redirect = login.complete()
        self.assertIn("casV3Jump.html", redirect)
        method, url, kwargs = session.calls[2]
        self.assertEqual(method, "POST")
        self.assertIn("action=login", url)
        self.assertEqual(
            kwargs["data"],
            {
                "appid": "647",
                "specialFlag": "qrcode",
                "senderr": "1",
                "fromu": "https://baijiahao.baidu.com/builder/theme/bjh/login?tab=uc",
                "selfu": "https://baijiahao.baidu.com/builder/fe-react/casV3Jump.html",
                "jumppage": "https://baijiahao.baidu.com/builder/fe-react/casV3Jump.html",
                "isajax": "1",
                "version": "2.3.0",
            },
        )

    def test_cookie_parser_still_requires_material(self):
        with self.assertRaises(BaijiaAuthError):
            BaijiaAuth.from_http_login(cookie="")


if __name__ == "__main__":
    unittest.main()
