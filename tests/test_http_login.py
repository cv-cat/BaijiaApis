import unittest
from urllib.parse import parse_qsl, urlsplit

from baijia_apis import (
    BaijiaAuth,
    BaijiaAuthError,
    BaijiaQRCodeLogin,
)
from baijia_apis.auth import (
    ALLOC_TK_URL,
    APPINFO_URL,
    BAIJIA_LOGIN_PAGE,
    PASSPORT_QR_INIT_URL,
    PASSPORT_QR_LOGIN_URL,
    PASSPORT_QR_POLL_URL,
)


class FakeResponse:
    status_code = 200

    def __init__(self, data=None, *, content=b"", headers=None, text=None, status_code=200):
        self._data = data
        self.headers = headers or {}
        self.content = content
        self.status_code = status_code
        if text is not None:
            self.text = text
        elif isinstance(data, str):
            self.text = data
        else:
            self.text = "" if data is None else str(data)

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("没有准备 FakeResponse")
        return self.responses.pop(0)

    @property
    def cookies(self):
        return {"BAIDUID": "ephemeral"}


def jsonp(data):
    import json
    return "tangram_guid_test(" + json.dumps(data, separators=(",", ":")) + ")"


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

    def test_qrcode_login_matches_passport_init_poll_contract(self):
        session = FakeSession(
            FakeResponse(text=jsonp({
                "imgurl": "https://passport.baidu.com/v2/api/qrcode?sign=SIGN",
                "errno": 0,
                "sign": "SIGN",
            })),
            FakeResponse(None, content=b"PNG", headers={"content-type": "image/png"}),
            FakeResponse(text=jsonp({"errno": 1})),
        )
        login = BaijiaQRCodeLogin(session=session, bootstrap=False, gid="gid-test", log_page="traceId:pc_loginv4_1,logPage:loginv4")
        challenge = login.start()
        self.assertEqual(challenge.state, "waiting")
        self.assertEqual(challenge.sign, "SIGN")
        status = login.poll()
        self.assertEqual(status.errno, 1)
        self.assertEqual([call[0] for call in session.calls], ["GET", "GET", "GET"])
        init_keys = [key for key, _ in parse_qsl(urlsplit(session.calls[0][1]).query, keep_blank_values=True)]
        poll_keys = [key for key, _ in parse_qsl(urlsplit(session.calls[2][1]).query, keep_blank_values=True)]
        self.assertEqual(init_keys, ["lp", "qrloginfrom", "gid", "oauthLog", "callback", "apiver", "tt", "tpl", "logPage", "_"])
        self.assertEqual(poll_keys, ["channel_id", "gid", "tpl", "_sdkFrom", "callback", "apiver", "tt", "_"])
        self.assertTrue(session.calls[0][1].startswith(PASSPORT_QR_INIT_URL))
        self.assertTrue(session.calls[2][1].startswith(PASSPORT_QR_POLL_URL))

    def test_qrcode_bootstrap_matches_page_and_alloc_tk(self):
        session = FakeSession(
            FakeResponse(text="<html></html>"),
            FakeResponse({"errno": 0}),
            FakeResponse({"errno": 10001401}),
            FakeResponse({"errno": 0}),
            FakeResponse({"errno": 217100002}),
            FakeResponse(text=jsonp({
                "imgurl": "https://passport.baidu.com/v2/api/qrcode?sign=S",
                "errno": 0,
                "sign": "S",
            })),
            FakeResponse(None, content=b"PNG", headers={"content-type": "image/png"}),
        )
        login = BaijiaQRCodeLogin(session=session)
        login.start()
        self.assertEqual([call[0] for call in session.calls], ["GET", "POST", "GET", "POST", "GET", "GET", "GET"])
        self.assertEqual(session.calls[0][1], BAIJIA_LOGIN_PAGE)
        self.assertEqual(session.calls[1][1], ALLOC_TK_URL)
        self.assertEqual(session.calls[1][2]["data"], b"")

    def test_qrcode_complete_uses_observed_channel_login(self):
        session = FakeSession(
            FakeResponse(text=jsonp({
                "imgurl": "https://passport.baidu.com/v2/api/qrcode?sign=S",
                "errno": 0,
                "sign": "S",
            })),
            FakeResponse(None, content=b"PNG", headers={"content-type": "image/png"}),
            FakeResponse(text=jsonp({"errno": 0, "status": 0, "channel_v": {"v": "BDUSS", "u": "u name"}})),
            FakeResponse(text=jsonp({"errInfo": {"no": "0"}, "url": "https://baijiahao.baidu.com/"})),
        )
        login = BaijiaQRCodeLogin(session=session, bootstrap=False)
        login.start()
        self.assertEqual(login.poll().state, "scanned")
        redirect = login.complete()
        self.assertEqual(redirect, "https://baijiahao.baidu.com/")
        method, url, kwargs = session.calls[3]
        self.assertEqual(method, "GET")
        self.assertTrue(url.startswith(PASSPORT_QR_LOGIN_URL))
        keys = [key for key, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)]
        self.assertEqual(keys, ["v", "bduss", "u", "loginVersion", "qrcode", "tpl", "callback", "_"])
        self.assertIn("bduss=BDUSS", url)
        self.assertIn("u=u%2520name", url)

    def test_cookie_parser_still_requires_material(self):
        with self.assertRaises(BaijiaAuthError):
            BaijiaAuth.from_http_login(cookie="")


if __name__ == "__main__":
    unittest.main()
