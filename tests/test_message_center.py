"""Contract tests for the Baijia message-center endpoints."""

from __future__ import annotations

import unittest

from baijia_apis.auth import BaijiaAuth, BaijiaAuthError
from baijia_apis.creator import BaijiaCreatorAPI


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class _Auth(BaijiaAuth):
    """Bypass network bootstrap; just carry cookie + creator_token."""

    def __init__(self, *, cookie: str = "BDUSS=x", creator_token: str = "t" * 8):
        self.cookie = cookie
        self.creator_token = creator_token
        self.app_id = ""
        self.app_token = ""
        self.timeout = 5

    def request(self, method, url, *, headers=None, params=None, **kwargs):
        self.last_call = {"method": method, "url": url, "params": params or {}}
        payload = getattr(self, "next_payload", {"errno": 0, "data": {}})
        return _Response(payload)


def _api(**kwargs) -> tuple[BaijiaCreatorAPI, _Auth]:
    auth = _Auth(**kwargs)
    return BaijiaCreatorAPI(auth), auth


class UnreadTests(unittest.TestCase):
    def test_unread_uses_getunreadonly_with_message_referer(self):
        api, auth = _api()
        auth.next_payload = {"errno": 0, "data": {"total": 12, "notice": 0}}
        result = api.unread_message_counts()
        self.assertEqual(result["data"]["total"], 12)
        self.assertIn("/pcui/message/getUnreadNum", auth.last_call["url"])

    def test_unread_requires_cookie_and_token(self):
        api, _ = _api(cookie="", creator_token="")
        with self.assertRaises(BaijiaAuthError):
            api.unread_message_counts()

    def test_unread_nonzero_errno_raises(self):
        api, auth = _api()
        auth.next_payload = {"errno": 10001401}
        with self.assertRaises(Exception):
            api.unread_message_counts()


class MessageListTests(unittest.TestCase):
    def test_list_messages_paginates_with_category(self):
        api, auth = _api()
        auth.next_payload = {"errno": 0, "data": {"list": [{"id": 1}]}}
        api.list_messages(page=2, page_size=10, category="announce")
        self.assertIn("/pcui/message/getMessageList", auth.last_call["url"])
        self.assertEqual(auth.last_call["params"], {"currentPage": 2, "pageSize": 10, "cat": "announce"})

    def test_list_messages_rejects_bad_params(self):
        api, _ = _api()
        with self.assertRaises(ValueError):
            api.list_messages(page=0)
        with self.assertRaises(ValueError):
            api.list_messages(page_size=0)
        with self.assertRaises(ValueError):
            api.list_messages(category="im")

    def test_list_messages_requires_list_shape(self):
        api, auth = _api()
        auth.next_payload = {"errno": 0, "data": {}}
        with self.assertRaises(Exception):
            api.list_messages()


class CommentMessageTests(unittest.TestCase):
    def test_list_comment_messages_uses_comment_endpoint(self):
        api, auth = _api()
        auth.next_payload = {"errno": 0, "data": {"list": [], "page": {}}}
        api.list_comment_messages(page=3, page_size=5)
        self.assertIn("/pcui/comment/message", auth.last_call["url"])
        self.assertEqual(auth.last_call["params"], {"currentPage": 3, "pageSize": 5})

    def test_list_comment_messages_rejects_bad_params(self):
        api, _ = _api()
        with self.assertRaises(ValueError):
            api.list_comment_messages(page=-1)
        with self.assertRaises(ValueError):
            api.list_comment_messages(page_size=101)


if __name__ == "__main__":
    unittest.main()
