"""作者动态、互动指标和公开文章详情。"""

from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from typing import Mapping
from urllib.parse import parse_qs, urlparse

from .auth import BaijiaAuth, BaijiaAuthError, BaijiaParseError


WEBPAGE_URL = "https://mbd.baidu.com/webpage"
ARTICLE_URL = "https://baijiahao.baidu.com/s"
_JSONP_POSTS = "__jsonp_baijia_posts"
_JSONP_METRICS = "__jsonp_baijia_metrics"


def parse_jsonp(text: str, callback: str) -> dict:
    """只接受指定回调，避免依靠固定字符位置切片。"""
    value = text.strip()
    if value.startswith("{"):
        payload = value
    else:
        pattern = rf"^{re.escape(callback)}\s*\((.*)\)\s*;?$"
        match = re.match(pattern, value, re.DOTALL)
        if not match:
            raise BaijiaParseError("JSONP 回调或格式与请求不符")
        payload = match.group(1)
    try:
        result = json.loads(payload)
    except ValueError as exc:
        raise BaijiaParseError("JSONP 内部不是有效 JSON") from exc
    if not isinstance(result, dict):
        raise BaijiaParseError("JSONP 应返回 JSON 对象")
    return result


def article_id_from(value: str) -> str:
    value = str(value).strip()
    if value.isascii() and value.isdigit():
        return value
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != "baijiahao.baidu.com" or parsed.path != "/s":
        raise ValueError("作品需为数字 ID 或 https://baijiahao.baidu.com/s?id=<ID>")
    ids = parse_qs(parsed.query).get("id") or []
    if len(ids) != 1 or not ids[0].isascii() or not ids[0].isdigit():
        raise ValueError("作品 URL 缺少数字 id")
    return ids[0]


class _ArticleHTML(HTMLParser):
    _VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.buffers = {"title": [], "article": [], "author-name": [], "updatetime": []}
        self.depths = {}
        self.date_update = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag not in self._VOID_TAGS:
            for key in self.depths:
                self.depths[key] += 1
        if tag == "title":
            self.depths["title"] = 1
        testid = attrs.get("data-testid")
        if testid in self.buffers:
            self.depths[testid] = 1
        if tag == "meta" and attrs.get("itemprop") == "dateUpdate":
            self.date_update = attrs.get("content", "")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, _tag):
        for key in tuple(self.depths):
            self.depths[key] -= 1
            if self.depths[key] <= 0:
                del self.depths[key]

    def handle_data(self, data):
        for key in self.depths:
            self.buffers[key].append(data)

    def get(self, key):
        return " ".join(" ".join(self.buffers[key]).split())


class BaijiaContentAPI:
    def __init__(self, auth: BaijiaAuth):
        self.auth = auth

    def get_article(self, item: str) -> dict:
        """读取公开文章的 SSR 标题、作者、更新时间和正文文本。"""
        article_id = article_id_from(item)
        response = self.auth.request("GET", ARTICLE_URL, params={"id": article_id}, use_cookie=False)
        parser = _ArticleHTML()
        parser.feed(response.text)
        title = parser.get("title")
        content = parser.get("article")
        if not title or not content:
            raise BaijiaParseError("文章页缺少标题或正文，可能已下线或触发验证")
        return {
            "id": article_id,
            "url": f"{ARTICLE_URL}?id={article_id}",
            "title": title,
            "author": parser.get("author-name"),
            "updated_at": parser.date_update or parser.get("updatetime"),
            "content": content,
        }

    def get_user_info(self, user_url: str) -> dict:
        """旧版作者页 HTML 数据，返回用户对象及分页所需 ``uk``、``otherext``。"""
        parsed = urlparse(user_url)
        if parsed.scheme != "https" or parsed.hostname != "author.baidu.com" or not re.fullmatch(r"/home/\d+", parsed.path):
            raise ValueError("作者 URL 应为 https://author.baidu.com/home/<数字 ID>")
        response = self.auth.request("GET", user_url)
        if "用户信息不存在" in response.text:
            raise BaijiaParseError("用户信息不存在")
        match = re.search(r"window\.runtime\s*=\s*(\{.*\})\s*,\s*window\.runtime\.pageType", response.text, re.DOTALL)
        if not match:
            raise BaijiaParseError("作者页缺少 window.runtime 数据")
        try:
            payload = json.loads(match.group(1))
            user = payload["user"]
            otherext = payload["staticMap"]["version"]
            uk = user["uk"]
        except (ValueError, KeyError, TypeError) as exc:
            raise BaijiaParseError("作者页数据结构与旧版契约不符") from exc
        return {"user": user, "uk": str(uk), "otherext": str(otherext)}

    def _hmery_time(self) -> str:
        value = self.auth.cookies.get("Hmery-Time")
        if not value:
            raise BaijiaAuthError("作者动态接口需要 Cookie 中的 Hmery-Time")
        return value

    def get_user_posts(self, uk: str, otherext: str, *, top_dynamic_id=None, ctime=None) -> dict:
        """按旧版已使用的 `mbd.baidu.com/webpage` JSONP 契约取一页动态。"""
        if bool(top_dynamic_id) != bool(ctime):
            raise ValueError("top_dynamic_id 与 ctime 必须一起传入")
        params = {
            "tab": "main", "num": "10", "uk": str(uk), "source": "pc",
            "type": "newhome", "action": "dynamic", "format": "jsonp",
            "otherext": f"h5_{otherext}", "Tenger-Mhor": self._hmery_time(),
            "callback": _JSONP_POSTS,
        }
        if top_dynamic_id is not None:
            params.update(top_dynamic_id=str(top_dynamic_id), ctime=str(ctime))
        response = self.auth.request(
            "GET", WEBPAGE_URL, params=params,
            headers={"Referer": "https://author.baidu.com/", "Accept": "*/*"},
        )
        return parse_jsonp(response.text, _JSONP_POSTS)

    def get_item_metrics(self, item: Mapping, uk: str) -> dict:
        """按旧版交互接口返回点赞、评论、阅读等指标。"""
        if not isinstance(item, Mapping) or not item:
            raise ValueError("item 必须是非空动态元数据")
        params = {
            "type": "homepage", "action": "interact", "format": "jsonp",
            "Tenger-Mhor": self._hmery_time(),
            "params": json.dumps([dict(item)], ensure_ascii=False, separators=(",", ":")),
            "uk": str(uk), "callback": _JSONP_METRICS,
        }
        response = self.auth.request(
            "GET", WEBPAGE_URL, params=params,
            headers={"Referer": "https://author.baidu.com/", "Accept": "*/*"},
        )
        payload = parse_jsonp(response.text, _JSONP_METRICS)
        user_list = (payload.get("data") or {}).get("user_list")
        if not isinstance(user_list, dict) or not user_list:
            raise BaijiaParseError("互动接口缺少 data.user_list")
        data = next(iter(user_list.values()))
        keys = ("praise_num", "comment_num", "read_num", "forward_num", "live_back_num", "collect", "unread")
        if not isinstance(data, dict) or any(key not in data for key in keys):
            raise BaijiaParseError("互动指标字段不完整")
        return {key: data[key] for key in keys}
