import json
import unittest

from baidu_apis import BaiduApis
from baijia_apis import (
    BaijiaAPIError,
    BaijiaAuth,
    BaijiaAuthError,
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

    def test_browser_cookie_preflight_and_redirect_handling(self):
        session = FakeSession(
            FakeResponse(data={"errno": "0", "data": {"name": "作者"}}),
            FakeResponse(data={"errno": "0", "data": {"name": "作者"}}),
            FakeResponse(data={"errno": 10001401, "data": None}),
            FakeResponse(status_code=302),
        )
        auth = BaijiaAuth.from_cookie("BAIDUID=x", session=session)
        self.assertTrue(auth.is_logged_in())
        self.assertEqual(auth.require_logged_in()["data"]["name"], "作者")
        with self.assertRaisesRegex(BaijiaAuthError, "10001401"):
            auth.require_logged_in()
        with self.assertRaisesRegex(BaijiaAPIError, "HTTP 302"):
            auth.login_state()
        self.assertTrue(all(call[2]["allow_redirects"] is False for call in session.calls))

    def test_creator_token_refresh_uses_existing_browser_credentials(self):
        session = FakeSession(FakeResponse(headers={"token": "new-token"}))
        auth = BaijiaAuth.from_cookie(
            "BAIDUID=x", creator_token="old-token", session=session,
        )
        self.assertEqual(auth.refresh_creator_token(), "new-token")
        method, url, kwargs = session.calls[0]
        self.assertEqual((method, url), ("HEAD", "https://baijiahao.baidu.com/builder/app/appinfo"))
        self.assertEqual(kwargs["headers"]["Cookie"], "BAIDUID=x")
        self.assertEqual(kwargs["headers"]["token"], "old-token")
        self.assertIs(kwargs["allow_redirects"], False)


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

    def test_logged_in_article_uses_baijia_cookie_only(self):
        html = '<html><title>作品</title><div data-testid="article">正文</div></html>'
        session = FakeSession(FakeResponse(text=html))
        auth = BaijiaAuth.from_cookie("BAIDUID=baijia", session=session)
        self.assertEqual(BaijiaContentAPI(auth).get_article("123")["content"], "正文")
        self.assertEqual(session.calls[0][2]["headers"]["Cookie"], "BAIDUID=baijia")

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
        self.assertEqual(kwargs["params"], {"wd": "site:baijiahao.baidu.com AI", "pn": 10})

    def test_site_search_reads_baidu_result_mu_and_title(self):
        html = (
            '<div class="result" mu="https://baijiahao.baidu.com/s?id=123&wfr=spider">'
            '<h3><a href="http://www.baidu.com/link?url=redacted">咖啡<em>入门</em></a></h3>'
            '<p>搜索摘要不应进入标题</p></div>'
        )
        session = FakeSession(FakeResponse(text=html))
        items = BaijiaSearchAPI(BaijiaAuth(session=session)).search_articles("咖啡")["items"]
        self.assertEqual(items, [{"id": "123", "url": "https://baijiahao.baidu.com/s?id=123&wfr=spider", "title": "咖啡入门"}])

    def test_site_search_scopes_optional_baidu_cookie(self):
        html = '<div mu="https://baijiahao.baidu.com/s?id=123"><h3>咖啡</h3></div>'
        session = FakeSession(FakeResponse(text=html), FakeResponse(text=html))
        auth = BaijiaAuth.from_cookie("BAIDUID=baijia", session=session)
        BaijiaSearchAPI(auth).search_articles("咖啡")
        self.assertNotIn("Cookie", session.calls[0][2]["headers"])
        api = BaijiaSearchAPI(auth, baidu_cookie="BDUSS=baidu")
        self.assertEqual(api.search_articles("咖啡")["items"][0]["id"], "123")
        self.assertEqual(session.calls[1][2]["headers"]["Cookie"], "BDUSS=baidu")
        self.assertIs(session.calls[1][2]["allow_redirects"], False)

    def test_site_search_empty_results_are_not_parse_error(self):
        session = FakeSession(FakeResponse(text="<p>抱歉，未找到相关结果。</p>"))
        self.assertEqual(BaijiaSearchAPI(BaijiaAuth(session=session)).search_articles("不存在的文章")["items"], [])

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
    def test_web_work_list_uses_cookie_and_observed_view_parameters(self):
        data = {"errno": 0, "data": {"list": [], "page": {"currentPage": 1, "pageSize": 10, "totalCount": 0, "totalPage": 0}}}
        session = FakeSession(*(FakeResponse(data=data) for _ in range(3)))
        api = BaijiaCreatorAPI(BaijiaAuth.from_cookie("BAIDUID=mine", session=session))
        for view in ("all", "news", "draft"):
            self.assertEqual(api.list_web_works(page=2, view=view)["data"]["list"], [])
        for view, call in zip(("all", "news", "draft"), session.calls):
            method, url, kwargs = call
            self.assertEqual((method, url), ("GET", "https://baijiahao.baidu.com/pcui/article/lists"))
            self.assertEqual(kwargs["headers"]["Cookie"], "BAIDUID=mine")
            self.assertNotIn("token", kwargs["headers"])
            self.assertIs(kwargs["allow_redirects"], False)
            self.assertEqual(kwargs["params"]["currentPage"], 2)
            self.assertEqual(kwargs["params"]["pageSize"], 10)
            self.assertEqual(kwargs["params"]["type"], "" if view == "all" else "news")
            self.assertEqual(kwargs["params"]["collection"], "draft" if view == "draft" else "")
            self.assertEqual("dynamic" in kwargs["params"], view == "all")

    def test_web_work_list_requires_cookie_and_valid_response(self):
        session = FakeSession(
            FakeResponse(data={"errno": 10001401, "data": None}),
            FakeResponse(data={"errno": 0, "data": {"list": {}}}),
        )
        api = BaijiaCreatorAPI(BaijiaAuth(session=session))
        with self.assertRaises(BaijiaAuthError):
            api.list_web_works()
        api = BaijiaCreatorAPI(BaijiaAuth.from_cookie("BAIDUID=mine", session=session))
        for page in (0, True, 1.5):
            with self.assertRaises(ValueError):
                api.list_web_works(page=page)
        with self.assertRaises(ValueError):
            api.list_web_works(view="published")
        self.assertEqual(session.calls, [])
        with self.assertRaisesRegex(BaijiaAPIError, "10001401"):
            api.list_web_works()
        with self.assertRaises(BaijiaParseError):
            api.list_web_works()

    def test_web_draft_form_contract_and_article_id(self):
        session = FakeSession(FakeResponse(data={"errno": 0, "ret": {"article_id": "123", "id": "123"}}))
        auth = BaijiaAuth.from_cookie("BAIDUID=mine", creator_token="creator-token", session=session)
        result = BaijiaCreatorAPI(auth).save_web_draft(
            "临时草稿标题", "<p>测试正文</p>", activities=[("campaign-one", True), ("campaign-two", False)],
        )
        self.assertEqual(result["ret"]["article_id"], "123")
        method, url, kwargs = session.calls[0]
        self.assertEqual((method, url), ("POST", "https://baijiahao.baidu.com/pcui/article/save"))
        self.assertEqual(kwargs["params"], {"callback": "bjhdraft"})
        self.assertEqual(kwargs["headers"]["Cookie"], "BAIDUID=mine")
        self.assertEqual(kwargs["headers"]["token"], "creator-token")
        self.assertEqual(kwargs["headers"]["Referer"], "https://baijiahao.baidu.com/builder/rc/edit?type=news&is_from_cms=1")
        self.assertEqual(kwargs["headers"]["Origin"], "https://baijiahao.baidu.com")
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/x-www-form-urlencoded")
        self.assertIs(kwargs["allow_redirects"], False)
        self.assertEqual(kwargs["data"], {
            "type": "news", "title": "临时草稿标题", "content": "<p>测试正文</p>",
            "len": str(len("<p>测试正文</p>")),
            "source_reprinted_allow": "0", "abstract_from": "1",
            "isBeautify": "false", "usingImgFilter": "false",
            "first_exclusive_publish_v2": "3", "subtitle": "", "bjhtopic_id": "", "bjhtopic_info": "",
            "activity_list[0][id]": "campaign-one", "activity_list[0][is_checked]": "1",
            "activity_list[1][id]": "campaign-two", "activity_list[1][is_checked]": "0",
        })
        self.assertNotIn("app_token", kwargs["data"])

    def test_web_draft_requires_auth_and_never_retries_ambiguous_response(self):
        empty = BaijiaCreatorAPI(BaijiaAuth(session=FakeSession()))
        with self.assertRaises(BaijiaAuthError):
            empty.save_web_draft("临时草稿", "<p>正文</p>")
        session = FakeSession(
            FakeResponse(data={"errno": 10001401}),
            FakeResponse(data={"errno": 0, "ret": {}}),
        )
        auth = BaijiaAuth.from_cookie("BAIDUID=mine", creator_token="creator-token", session=session)
        api = BaijiaCreatorAPI(auth)
        for title, content in (("短", "<p>正文</p>"), ("临时草稿", ""), ("临时\n草稿", "正文"), ("临时草稿", "<p>🙂</p>")):
            with self.assertRaises(ValueError):
                api.save_web_draft(title, content)
        with self.assertRaises(ValueError):
            api.save_web_draft("临时草稿", "正文", activities=[("id", "yes")])
        self.assertEqual(session.calls, [])
        with self.assertRaisesRegex(BaijiaAPIError, "10001401"):
            api.save_web_draft("临时草稿", "<p>正文</p>")
        with self.assertRaisesRegex(BaijiaParseError, "勿直接重试"):
            api.save_web_draft("临时草稿", "<p>正文</p>")
        self.assertEqual(len(session.calls), 2)

    def test_delete_web_draft_requires_explicit_numeric_id_and_uses_observed_form(self):
        session = FakeSession(FakeResponse(data={"errno": 0}))
        auth = BaijiaAuth.from_cookie("BAIDUID=mine", creator_token="creator-token", session=session)
        api = BaijiaCreatorAPI(auth)
        for article_id in ("", "0", 0, " 123", "123 ", "１２３", "123a", -1, 1.5, True, None):
            with self.subTest(article_id=article_id), self.assertRaises(ValueError):
                api.delete_web_draft(article_id)
        self.assertEqual(session.calls, [])
        self.assertEqual(api.delete_web_draft("123"), {"errno": 0})
        self.assertEqual(len(session.calls), 1)
        method, url, kwargs = session.calls[0]
        self.assertEqual((method, url), ("POST", "https://baijiahao.baidu.com/pcui/article/remove"))
        self.assertEqual(kwargs["data"], {"article_id": "123"})
        self.assertEqual(kwargs["headers"]["Cookie"], "BAIDUID=mine")
        self.assertEqual(kwargs["headers"]["token"], "creator-token")
        self.assertIs(kwargs["allow_redirects"], False)
        self.assertNotIn("json", kwargs)
        self.assertNotIn("params", kwargs)

    def test_delete_web_draft_requires_web_auth_and_does_not_retry_error(self):
        empty = BaijiaCreatorAPI(BaijiaAuth(session=FakeSession()))
        with self.assertRaises(BaijiaAuthError):
            empty.delete_web_draft("123")
        session = FakeSession(FakeResponse(data={"errno": 10001401}))
        auth = BaijiaAuth.from_cookie("BAIDUID=mine", creator_token="creator-token", session=session)
        with self.assertRaisesRegex(BaijiaAPIError, "勿直接重试"):
            BaijiaCreatorAPI(auth).delete_web_draft(123)
        self.assertEqual(len(session.calls), 1)

    def test_web_publish_form_contract_and_token_rotation(self):
        session = FakeSession(
            FakeResponse(
                data={"errno": 0, "ret": {"url": "https://baijiahao.baidu.com/s?id=123"}},
                headers={"token": "rotated-token"},
            )
        )
        auth = BaijiaAuth.from_cookie("BAIDUID=mine", creator_token="creator-token", session=session)
        result = BaijiaCreatorAPI(auth).publish_web_article(
            "测试发布标题", "<p>测试发布正文</p>",
            cover_urls=["https://pic.example/cover.jpg"],
            author="测试作者", abstract="测试摘要",
            activities=[("ttv", True), ("reward", False)],
        )
        self.assertEqual(result["ret"]["url"], "https://baijiahao.baidu.com/s?id=123")
        self.assertEqual(auth.creator_token, "rotated-token")
        method, url, kwargs = session.calls[0]
        self.assertEqual((method, url), ("POST", "https://baijiahao.baidu.com/pcui/article/publish"))
        self.assertEqual(kwargs["params"], {"callback": "bjhpublish"})
        self.assertEqual(kwargs["headers"]["Cookie"], "BAIDUID=mine")
        self.assertEqual(kwargs["headers"]["token"], "creator-token")
        self.assertEqual(kwargs["headers"]["Referer"], "https://baijiahao.baidu.com/builder/rc/edit?type=news&is_from_cms=1")
        self.assertIs(kwargs["allow_redirects"], False)
        fields = kwargs["data"]
        self.assertEqual(fields["type"], "news")
        self.assertEqual(fields["len"], str(len("<p>测试发布正文</p>")))
        self.assertEqual(fields["vertical_cover"], "https://pic.example/cover.jpg")
        self.assertEqual(json.loads(fields["_cover_images_map"]), [{"src": "https://pic.example/cover.jpg"}])
        self.assertEqual(json.loads(fields["cover_images"])[0]["src"], "https://pic.example/cover.jpg")
        self.assertEqual(fields["activity_list[0][id]"], "ttv")
        self.assertEqual(fields["activity_list[0][is_checked]"], "1")
        self.assertEqual(fields["activity_list[1][is_checked]"], "0")

    def test_web_publish_requires_cover_and_reconciles_ambiguous_success(self):
        session = FakeSession(FakeResponse(data={"errno": 0, "ret": {}}))
        auth = BaijiaAuth.from_cookie("BAIDUID=mine", creator_token="creator-token", session=session)
        api = BaijiaCreatorAPI(auth)
        with self.assertRaises(ValueError):
            api.publish_web_article("测试发布标题", "<p>正文</p>", cover_urls=[])
        self.assertEqual(session.calls, [])
        with self.assertRaisesRegex(BaijiaParseError, "勿直接重试"):
            api.publish_web_article(
                "测试发布标题", "<p>正文</p>", cover_urls=["https://pic.example/cover.jpg"]
            )
        self.assertEqual(len(session.calls), 1)

    def test_web_publish_requires_web_auth_and_validates_inputs(self):
        empty = BaijiaCreatorAPI(BaijiaAuth(session=FakeSession()))
        with self.assertRaises(BaijiaAuthError):
            empty.publish_web_article("测试发布标题", "<p>正文</p>", cover_urls=["https://pic.example/a.jpg"])
        session = FakeSession()
        auth = BaijiaAuth.from_cookie("BAIDUID=mine", creator_token="creator-token", session=session)
        api = BaijiaCreatorAPI(auth)
        for title, body, covers in (
            ("短", "<p>正文</p>", ["https://pic.example/a.jpg"]),
            ("测试发布标题", "", ["https://pic.example/a.jpg"]),
            ("测试发布标题", "<p>🙂</p>", ["https://pic.example/a.jpg"]),
            ("测试发布标题", "<p>正文</p>", ["ftp://pic.example/a.jpg"]),
        ):
            with self.subTest(title=title, body=body), self.assertRaises(ValueError):
                api.publish_web_article(title, body, cover_urls=covers)
        with self.assertRaises(ValueError):
            api.publish_web_article("测试发布标题", "<p>正文</p>", cover_urls="https://pic.example/a.jpg")
        with self.assertRaises(ValueError):
            api.publish_web_article(
                "测试发布标题", "<p>正文</p>", cover_urls=["https://pic.example/a.jpg"], activities=[("x", "yes")]
            )
        self.assertEqual(session.calls, [])

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
        self.assertIs(kwargs["allow_redirects"], False)
        self.assertEqual(session.calls[1][2]["json"]["article_id"], "123")

    def test_publish_success_without_article_id_requires_manual_reconciliation(self):
        session = FakeSession(FakeResponse(data={"errno": 0, "data": {}}))
        api = BaijiaCreatorAPI(BaijiaAuth.from_partner_token("app-id", "app-token", session=session))
        with self.assertRaisesRegex(BaijiaParseError, "勿直接重试"):
            api.publish_article(
                "测试标题示例", "<p>正文</p>", origin_url="https://example.com/original",
                cover_urls=["https://example.com/cover.jpg"],
            )
        self.assertEqual(len(session.calls), 1)

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
