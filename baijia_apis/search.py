"""百度站点搜索与作者动态的本地筛选。"""

from __future__ import annotations

from html.parser import HTMLParser
import json
from urllib.parse import urlparse

from .auth import BaijiaAPIError, BaijiaParseError
from .content import BaijiaContentAPI, article_id_from


SEARCH_URL = "https://www.baidu.com/s"


class BaijiaSearchBlocked(BaijiaAPIError):
    """搜索页要求人工完成安全验证。"""


class _SearchLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        attrs = dict(attrs)
        for name in ("href", "data-url", "data-landurl"):
            value = attrs.get(name) or ""
            if urlparse(value).hostname == "baijiahao.baidu.com":
                try:
                    article_id_from(value)
                except ValueError:
                    continue
                self.current = {"url": value, "title": ""}
                break

    def handle_data(self, data):
        if self.current is not None:
            self.current["title"] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            self.current["title"] = " ".join(self.current["title"].split())
            self.links.append(self.current)
            self.current = None


class BaijiaSearchAPI:
    def __init__(self, auth, content: BaijiaContentAPI | None = None):
        self.auth = auth
        self.content = content or BaijiaContentAPI(auth)

    def search_articles(self, query: str, *, page: int = 1) -> dict:
        """百度网页搜索中限定百家号域；这是网页搜索，不是百家号私有 API。

        当前出口会触发百度安全验证，因此此方法的请求与解析契约仅离线测试。
        """
        query = query.strip()
        if not query:
            raise ValueError("query 不能为空")
        if page < 1:
            raise ValueError("page 从 1 开始")
        response = self.auth.request(
            "GET", SEARCH_URL, use_cookie=False,
            params={"wd": f"site:baijiahao.baidu.com/s {query}", "pn": (page - 1) * 10},
            headers={"Referer": "https://www.baidu.com/"},
        )
        html = response.text
        if "百度安全验证" in html or "安全验证" in html or "captcha" in html.lower():
            raise BaijiaSearchBlocked("百度搜索要求人工完成安全验证")
        parser = _SearchLinks()
        parser.feed(html)
        items = []
        seen = set()
        for item in parser.links:
            article_id = article_id_from(item["url"])
            if article_id not in seen:
                seen.add(article_id)
                items.append({"id": article_id, **item})
        if not items and "没有找到" not in html and "无相关结果" not in html:
            raise BaijiaParseError("搜索页没有可解析的百家号直链")
        return {"query": query, "page": page, "items": items}

    def search_user_posts(
        self,
        uk: str,
        otherext: str,
        query: str,
        *,
        max_pages: int = 3,
    ) -> dict:
        """逐页读取指定作者的动态，再按文字进行本地筛选。"""
        query = query.strip()
        if not query or max_pages < 1:
            raise ValueError("query 不能为空且 max_pages 必须大于 0")
        results = []
        top_dynamic_id = None
        ctime = None
        scanned_pages = 0
        for _ in range(max_pages):
            payload = self.content.get_user_posts(uk, otherext, top_dynamic_id=top_dynamic_id, ctime=ctime)
            data = payload.get("data") or {}
            posts = data.get("list")
            if not isinstance(posts, list):
                raise BaijiaParseError("动态接口缺少 data.list")
            scanned_pages += 1
            results.extend(item for item in posts if query in json.dumps(item, ensure_ascii=False))
            if data.get("hasMore") not in (1, True, "1") or not posts:
                break
            next_id = posts[0].get("dynamic_id") if isinstance(posts[0], dict) else None
            next_ctime = (data.get("query") or {}).get("ctime")
            if not next_id or not next_ctime:
                raise BaijiaParseError("动态列表声称有下一页，但缺少游标")
            top_dynamic_id, ctime = next_id, next_ctime
        return {"query": query, "uk": str(uk), "items": results, "scanned_pages": scanned_pages}
