import json
import unittest

from baidu_apis import BaiduApis
from baijia_apis import (
    BaijiaAPIError,
    BaijiaAuth,
    BaijiaContentAPI,
    BaijiaCreatorAPI,
    BaijiaParseError,
    BaijiaSearchAPI,
    BaijiaSearchBlocked,
)
from baijia_apis.auth import parse_cookies
from baijia_apis.content import parse_jsonp


class FakeResponse:
    def __init__(self, *, text="", data=None, status_code=200, headers=None):
        self.text = text
        self.data = data
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        if self.data is None:
            raise ValueError("not JSON")
        return self.data


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


class AuthTests(unittest.TestCase):
    def test_cookie_parser_and_login_probe(self):
        self.assertEqual(parse_cookies("BAIDUID=a=b; Hmery-Time=123"), {"BAIDUID": "a=b", "Hmery-Time": "123"})
        session = FakeSession(FakeResponse(data={"errno": 10001401, "data": None}))
        auth = BaijiaAuth.from_cookie("BAIDUID=a=b; Hmery-Time=123", session=session)
        self.assertFalse(auth.is_logged_in())
        method, url, kwargs = session.calls[0]
        self.assertEqual((method, url), ("GET", "https://baijiahao.baidu.com/builder/app/appinfo"))
        self.assertEqual(kwargs["headers"]["Cookie"], "BAIDUID=a=b; Hmery-Time=123")

    def test_request_rejects_non_baidu_destination_before_network(self):
        session = FakeSession()
        auth = BaijiaAuth.from_cookie("BAIDUID=x", session=session)
        with self.assertRaises(ValueError):
            auth.request("GET", "https://example.com/collect")
        self.assertEqual(session.calls, [])


class ContentTests(unittest.TestCase):
    def test_public_article_parses_item_fields(self):
        html = (
            '<html><head><title>测试作品</title><meta itemprop="dateUpdate" content="2026-01-02"></head>'
            '<body><div data-testid="author-name">作者甲</div>'
            '<div data-testid="article"><p>第一段</p><p>第二段</p></div></body></html>'
        )
        session = FakeSession(FakeResponse(text=html))
        api = BaijiaContentAPI(BaijiaAuth(session=session))
        item = api.get_article("https://baijiahao.baidu.com/s?id=123")
        self.assertEqual((item["id"], item["title"], item["author"]), ("123", "测试作品", "作者甲"))
        self.assertEqual(item["content"], "第一段 第二段")
        self.assertEqual(session.calls[0][2]["params"], {"id": "123"})
        self.assertNotIn("Cookie", session.calls[0][2]["headers"])

    def test_posts_jsonp_and_interaction_request_contract(self):
        metrics = {
            "praise_num": 1, "comment_num": 2, "read_num": 3,
            "forward_num": 4, "live_back_num": 5, "collect": 6, "unread": 7,
        }
        session = FakeSession(
            FakeResponse(text='__jsonp_baijia_posts({"data":{"list":[],"hasMore":0}});'),
            FakeResponse(text='__jsonp_baijia_metrics(' + json.dumps({"data": {"user_list": {"one": metrics}}}) + ');'),
        )
        api = BaijiaContentAPI(BaijiaAuth.from_cookie("Hmery-Time=987; BIDUPSID=x", session=session))
        self.assertEqual(api.get_user_posts("uk", "v1")["data"]["list"], [])
        self.assertEqual(api.get_item_metrics({"feed_id": "f"}, "uk"), metrics)
        post_params = session.calls[0][2]["params"]
        self.assertEqual((post_params["action"], post_params["Tenger-Mhor"], post_params["otherext"]), ("dynamic", "987", "h5_v1"))
        metrics_params = session.calls[1][2]["params"]
        self.assertEqual(metrics_params["action"], "interact")
        self.assertEqual(json.loads(metrics_params["params"]), [{"feed_id": "f"}])
        with self.assertRaises(ValueError):
            api.get_user_posts("uk", "v1", top_dynamic_id="x")
        with self.assertRaises(BaijiaParseError):
            parse_jsonp('other({"ok":true})', "expected")


class SearchTests(unittest.TestCase):
    def test_site_search_query_and_captcha(self):
        session = FakeSession(FakeResponse(text="<title>百度安全验证</title>"))
        api = BaijiaSearchAPI(BaijiaAuth(session=session))
        with self.assertRaises(BaijiaSearchBlocked):
            api.search_articles("AI", page=2)
        method, url, kwargs = session.calls[0]
        self.assertEqual((method, url), ("GET", "https://www.baidu.com/s"))
        self.assertEqual(kwargs["params"], {"wd": "site:baijiahao.baidu.com/s AI", "pn": 10})

    def test_author_search_uses_response_cursor(self):
        first = {"data": {"list": [{"dynamic_id": "first", "title": "猫"}], "hasMore": 1, "query": {"ctime": "100"}}}
        second = {"data": {"list": [{"dynamic_id": "second", "title": "猫咪"}], "hasMore": 0}}
        session = FakeSession(
            FakeResponse(text="__jsonp_baijia_posts(" + json.dumps(first, ensure_ascii=False) + ");"),
            FakeResponse(text="__jsonp_baijia_posts(" + json.dumps(second, ensure_ascii=False) + ");"),
        )
        auth = BaijiaAuth.from_cookie("Hmery-Time=987", session=session)
        result = BaijiaSearchAPI(auth).search_user_posts("uk", "v1", "猫", max_pages=2)
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["scanned_pages"], 2)
        self.assertEqual(session.calls[1][2]["params"]["top_dynamic_id"], "first")
        self.assertEqual(session.calls[1][2]["params"]["ctime"], "100")


class CreatorTests(unittest.TestCase):
    def test_partner_publish_body_and_status_contract(self):
        session = FakeSession(
            FakeResponse(data={"errno": 0, "data": {"article_id": "123"}}),
            FakeResponse(data={"errno": 0, "data": {"123": "published"}}),
        )
        auth = BaijiaAuth.from_partner_token("app-id", "app-token", session=session)
        api = BaijiaCreatorAPI(auth)
        self.assertEqual(api.publish_article(
            "测试标题示例", "<p>正文</p>", origin_url="https://example.com/original",
            cover_urls=["https://example.com/a.jpg"],
        )["data"]["article_id"], "123")
        self.assertEqual(api.query_article_status("123")["errno"], 0)
        method, url, kwargs = session.calls[0]
        self.assertEqual((method, url), ("POST", "https://baijiahao.baidu.com/builderinner/open/resource/article/publish"))
        self.assertEqual(kwargs["json"], {
            "app_id": "app-id", "app_token": "app-token", "title": "测试标题示例",
            "content": "<p>正文</p>", "origin_url": "https://example.com/original",
            "cover_images": '[{"src":"https://example.com/a.jpg"}]',
        })
        self.assertNotIn("Cookie", kwargs["headers"])
        self.assertEqual(session.calls[1][2]["json"]["article_id"], "123")

    def test_publish_requires_origin_and_explicit_draft_choice(self):
        session = FakeSession(FakeResponse(data={"errno": 0, "data": {"article_id": "456"}}))
        api = BaijiaCreatorAPI(BaijiaAuth.from_partner_token("app-id", "app-token", session=session))
        with self.assertRaises(ValueError):
            api.publish_article("测试标题示例", "<p>正文</p>", cover_urls=["https://example.com/a.jpg"])
        with self.assertRaises(ValueError):
            api.publish_article("测试标题示例", "<p>正文</p>", origin_url="https://example.com/original")
        self.assertEqual(session.calls, [])
        api.publish_article(
            "测试标题示例", "<p>正文</p>", origin_url="https://example.com/original",
            allow_draft=True, is_original=False,
        )
        payload = session.calls[0][2]["json"]
        self.assertNotIn("cover_images", payload)
        self.assertEqual(payload["is_original"], 0)

    def test_publish_validates_sdk_length_before_network(self):
        session = FakeSession()
        api = BaijiaCreatorAPI(BaijiaAuth.from_partner_token("app-id", "app-token", session=session))
        options = {"origin_url": "https://example.com/original", "cover_urls": ["https://example.com/a.jpg"]}
        for title, content in (("标题", "<p>正文</p>"), ("A" * 81, "<p>正文</p>"), ("测试标题示例", "汉" * 20_001)):
            with self.subTest(title=title[:10], content_length=len(content)), self.assertRaises(ValueError):
                api.publish_article(title, content, **options)
        self.assertEqual(session.calls, [])

    def test_status_limit_and_platform_error(self):
        session = FakeSession(
            FakeResponse(data={"errno": 0, "data": {}}),
            FakeResponse(data={"errno": 1001, "errmsg": "invalid app token"}),
        )
        api = BaijiaCreatorAPI(BaijiaAuth.from_partner_token("app-id", "app-token", session=session))
        api.query_article_status([str(i) for i in range(20)])
        self.assertEqual(len(session.calls[0][2]["json"]["article_id"].split(",")), 20)
        with self.assertRaises(ValueError):
            api.query_article_status([str(i) for i in range(21)])
        self.assertEqual(len(session.calls), 1)
        with self.assertRaises(BaijiaAPIError):
            api.query_article_status("123")

    def test_creator_upload_is_multipart_and_returns_https_url(self):
        session = FakeSession(FakeResponse(data={"errno": 0, "ret": {"https_url": "https://pic.example/a.png"}}))
        auth = BaijiaAuth.from_cookie("BAIDUID=x", creator_token="jwt", app_id="app-id", session=session)
        url = BaijiaCreatorAPI(auth).upload_image(b"\x89PNG\r\n\x1a\nbody", filename="cover.png")
        self.assertEqual(url, "https://pic.example/a.png")
        method, endpoint, kwargs = session.calls[0]
        self.assertEqual((method, endpoint), ("POST", "https://baijiahao.baidu.com/pcui/picture/uploadproxy"))
        self.assertEqual(kwargs["data"]["article_type"], "news")
        self.assertEqual(kwargs["files"]["media"][2], "image/png")
        self.assertEqual(kwargs["headers"]["token"], "jwt")


class LegacyCompatibilityTests(unittest.TestCase):
    def test_legacy_entry_and_methods_remain_available(self):
        api = BaiduApis()
        for name in ("get_user_info", "get_user_posted", "get_work_info", "check_cookies_alive"):
            self.assertTrue(callable(getattr(api, name)))


if __name__ == "__main__":
    unittest.main()
