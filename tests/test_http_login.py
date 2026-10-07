import unittest

from baijia_apis import (
    BaijiaAuth,
    BaijiaAuthError,
    BaijiaLoginProtocolUnavailable,
)
from baijia_apis.auth import APPINFO_URL


class FakeResponse:
    status_code = 200

    def __init__(self, data):
        self._data = data
        self.headers = {}

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


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

    def test_legacy_login_entries_have_no_automation_side_effect(self):
        with self.assertRaises(BaijiaLoginProtocolUnavailable):
            BaijiaAuth.from_browser_login()
        with self.assertRaises(BaijiaLoginProtocolUnavailable):
            BaijiaAuth.from_qrcode_login()

    def test_cookie_parser_still_requires_material(self):
        with self.assertRaises(BaijiaAuthError):
            BaijiaAuth.from_http_login(cookie="")


if __name__ == "__main__":
    unittest.main()
