"""百家号 Creator 网页列表/发布、图片上传和图文开放接口。"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from .auth import BaijiaAuth, BaijiaAPIError, BaijiaAuthError, BaijiaParseError, response_json


OPEN_BASE = "https://baijiahao.baidu.com/builderinner/open/resource"
WEB_LIST_URL = "https://baijiahao.baidu.com/pcui/article/lists"
WEB_SAVE_URL = "https://baijiahao.baidu.com/pcui/article/save"
WEB_PUBLISH_URL = "https://baijiahao.baidu.com/pcui/article/publish"
WEB_REMOVE_URL = "https://baijiahao.baidu.com/pcui/article/remove"
UPLOAD_URL = "https://baijiahao.baidu.com/pcui/picture/uploadproxy"
CREATOR_REFERER = "https://baijiahao.baidu.com/builder/rc/edit?type=news"
WEB_EDITOR_REFERER = "https://baijiahao.baidu.com/builder/rc/edit?type=news&is_from_cms=1"
CONTENT_REFERER = "https://baijiahao.baidu.com/builder/rc/content"


def _valid_http_url(value: str, label: str) -> str:
    parsed = urlparse(str(value))
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"{label} 必须是 HTTP(S) URL")
    return str(value)


def _image_type(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    raise ValueError("仅支持 JPEG 或 PNG 图片")


def _sdk_char_count(value: str) -> int:
    """按参考 SDK 的规则计字数：汉字/中文标点 1，其他字符 0.5，向上取整。"""
    full_width_punctuation = set("·，。《》‘’”“；：〖〗？（）、")
    half_units = 0
    for char in value:
        name = unicodedata.name(char, "")
        is_han = name.startswith(("CJK UNIFIED IDEOGRAPH", "CJK COMPATIBILITY IDEOGRAPH"))
        half_units += 2 if is_han or char in full_width_punctuation else 1
    return (half_units + 1) // 2


class BaijiaCreatorAPI:
    def __init__(self, auth: BaijiaAuth):
        self.auth = auth

    def _require_partner(self) -> None:
        if not self.auth.app_id or not self.auth.app_token:
            raise BaijiaAuthError("开放接口需要 App ID 与 App Token")

    def _require_creator(self) -> None:
        if not self.auth.cookie or not self.auth.creator_token or not self.auth.app_id:
            raise BaijiaAuthError("图片上传需要 Cookie、Creator token 和 App ID")

    def _require_web_editor(self) -> None:
        if not self.auth.cookie or not self.auth.creator_token:
            raise BaijiaAuthError("网页草稿需要 Cookie 和 Creator token")

    def list_web_works(self, *, page: int = 1, view: str = "all") -> dict:
        """读取当前登录账号的作品列表；无需开放接口 App Token。

        view 仅支持已在 Creator 页面核对的全部、图文和图文草稿视图。
        """
        if not self.auth.cookie:
            raise BaijiaAuthError("Web 作品列表需要登录 Cookie")
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise ValueError("page 必须是从 1 开始的整数")
        if view not in ("all", "news", "draft"):
            raise ValueError("view 仅支持 all、news、draft")
        params = {
            "currentPage": page,
            "pageSize": 10,
            "search": "",
            "type": "" if view == "all" else "news",
            "collection": "draft" if view == "draft" else "",
            "startDate": "",
            "endDate": "",
            "clearBeforeFetch": "false",
        }
        if view == "all":
            params["dynamic"] = "1"
        response = self.auth.request("GET", WEB_LIST_URL, params=params, headers={"Referer": CONTENT_REFERER})
        result = response_json(response)
        if str(result.get("errno")) != "0":
            raise BaijiaAPIError(f"Web 作品列表读取失败：errno={result.get('errno')}")
        data = result.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("list"), list) or not isinstance(data.get("page"), dict):
            raise BaijiaParseError("Web 作品列表缺少 data.list 或 data.page")
        return result

    def save_web_draft(
        self,
        title: str,
        content_html: str,
        *,
        activities: Iterable[tuple[str, bool]] = (),
    ) -> dict:
        """按已观察的网页编辑器请求新建图文草稿，返回平台响应。

        activities 是当前页面展示的活动 ID 与是否选中；不提供时不发送活动字段。
        本方法只调用 callback=bjhdraft，不调用公开发布端点。
        """
        self._require_web_editor()
        title = title.strip()
        if not 2 <= len(title) <= 64:
            raise ValueError("草稿标题需为 2–64 字")
        if not content_html.strip():
            raise ValueError("草稿正文不能为空")
        if "\r" in title or "\n" in title:
            raise ValueError("草稿标题不能包含换行符")
        if any(ord(char) > 0xFFFF for char in title + content_html):
            raise ValueError("emoji 等非 BMP 字符的草稿长度规则尚未验证")
        fields = {
            "type": "news",
            "title": title,
            "content": content_html,
            "len": str(len(content_html)),
            "source_reprinted_allow": "0",
            "abstract_from": "1",
            "isBeautify": "false",
            "usingImgFilter": "false",
            "first_exclusive_publish_v2": "3",
            "subtitle": "",
            "bjhtopic_id": "",
            "bjhtopic_info": "",
        }
        if isinstance(activities, (str, bytes)):
            raise ValueError("activities 应为 (活动 ID, 是否选中) 列表")
        for index, activity in enumerate(activities):
            try:
                activity_id, checked = activity
            except (TypeError, ValueError):
                raise ValueError("每项活动应为 (活动 ID, bool)") from None
            if not isinstance(activity_id, str) or not activity_id.strip() or any(c in activity_id for c in "\r\n;="):
                raise ValueError("活动 ID 格式无效")
            if not isinstance(checked, bool):
                raise ValueError("活动选中状态必须是 bool")
            fields[f"activity_list[{index}][id]"] = activity_id
            fields[f"activity_list[{index}][is_checked]"] = "1" if checked else "0"
        response = self.auth.request(
            "POST", WEB_SAVE_URL, params={"callback": "bjhdraft"}, data=fields,
            headers={
                "token": self.auth.creator_token,
                "Referer": WEB_EDITOR_REFERER,
                "Origin": "https://baijiahao.baidu.com",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        result = response_json(response)
        if str(result.get("errno")) != "0":
            raise BaijiaAPIError(f"Web 草稿保存失败：errno={result.get('errno')}")
        ret = result.get("ret")
        article_id = ret.get("article_id") if isinstance(ret, dict) else None
        if not str(article_id or "").isascii() or not str(article_id or "").isdigit():
            raise BaijiaParseError("草稿请求可能已成功，但缺少 ret.article_id；请先检查草稿列表，勿直接重试")
        return result

    def delete_web_draft(self, article_id: str | int) -> dict:
        """显式删除一个已确认的网页草稿；不会查找、批量删除或自动重试。"""
        self._require_web_editor()
        if isinstance(article_id, bool) or not isinstance(article_id, (str, int)):
            raise ValueError("article_id 必须是正整数数字 ID")
        article_id_text = str(article_id)
        if not article_id_text.isascii() or not article_id_text.isdigit() or not article_id_text.strip("0"):
            raise ValueError("article_id 必须是正整数数字 ID")
        response = self.auth.request(
            "POST", WEB_REMOVE_URL, data={"article_id": article_id_text},
            headers={"token": self.auth.creator_token},
        )
        result = response_json(response)
        if str(result.get("errno")) != "0":
            raise BaijiaAPIError(
                f"Web 草稿删除结果未确认：errno={result.get('errno')}；请先检查草稿列表，勿直接重试"
            )
        return result

    def publish_web_article(
        self,
        title: str,
        content_html: str,
        *,
        cover_urls: Iterable[str],
        author: str = "",
        abstract: str = "",
        activities: Iterable[tuple[str, bool]] = (),
    ) -> dict:
        """提交网页编辑器的图文公开发布请求。

        该方法复现 Creator 编辑器已公开客户端观察到的
        ``POST /pcui/article/publish?callback=bjhpublish`` 契约。它会创建
        线上作品，不保存草稿，也不会自动重试。调用方应先确认标题、正文、
        封面和账号发布权限；响应未返回作品 URL 时会抛出异常，调用方应先
        在后台核对作品状态后再决定下一步。

        ``activities`` 需要传入当前编辑器展示的活动 ID 与勾选状态。默认不
        猜测活动 ID，避免使用已过期或不属于当前账号的活动配置。
        """
        self._require_web_editor()
        title = title.strip()
        if not 2 <= len(title) <= 64:
            raise ValueError("发布标题需为 2–64 字")
        if not content_html.strip():
            raise ValueError("发布正文不能为空")
        if "\r" in title or "\n" in title:
            raise ValueError("发布标题不能包含换行符")
        if any(ord(char) > 0xFFFF for char in title + content_html):
            raise ValueError("emoji 等非 BMP 字符的网页长度规则尚未验证")
        if not isinstance(author, str) or not isinstance(abstract, str):
            raise ValueError("author 与 abstract 必须是字符串")
        if any(char in author or char in abstract for char in "\r\n"):
            raise ValueError("author 与 abstract 不能包含换行符")
        if isinstance(cover_urls, (str, bytes)):
            raise ValueError("cover_urls 应为 URL 列表")
        covers = list(cover_urls)
        if not 1 <= len(covers) <= 3:
            raise ValueError("网页公开发布需传 1–3 张封面图")
        for url in covers:
            _valid_http_url(url, "cover_urls")

        cover_images = [
            {
                "src": url,
                "cropData": {"x": 0, "y": 0, "width": 2048, "height": 1365},
                "machine_chooseimg": 0,
                "isLegal": 1,
            }
            for url in covers
        ]
        fields = {
            "type": "news",
            "title": title,
            "author": author,
            "abstract": abstract,
            "content": content_html,
            "auto_mount_goods": "1",
            "len": str(len(content_html)),
            "vertical_cover": covers[0],
            "cover_images": json.dumps(cover_images, ensure_ascii=False, separators=(",", ":")),
            "_cover_images_map": json.dumps(
                [{"src": url} for url in covers], ensure_ascii=False, separators=(",", ":")
            ),
            "source": "upload",
            "cover_source": "upload",
            "subtitle": "",
            "bjhtopic_id": "",
            "bjhtopic_info": "",
            "clue": "1",
            "bjhmt": "",
            "order_id": "",
            "aigc_rebuild": "",
            "image_edit_point": (
                '[{"img_type":"cover","img_num":{"template":0,"font":0,"filter":0,"paster":0,"cut":0,"any":0}},'
                '{"img_type":"body","img_num":{"template":0,"font":0,"filter":0,"paster":0,"cut":0,"any":0}}]'
            ),
            "source_reprinted_allow": "0",
            "abstract_from": "2",
            "isBeautify": "false",
            "usingImgFilter": "false",
            "cover_layout": "one",
        }
        if isinstance(activities, (str, bytes)):
            raise ValueError("activities 应为 (活动 ID, bool) 列表")
        for index, activity in enumerate(activities):
            try:
                activity_id, checked = activity
            except (TypeError, ValueError):
                raise ValueError("每项活动应为 (活动 ID, bool)") from None
            if not isinstance(activity_id, str) or not activity_id.strip() or any(
                char in activity_id for char in "\r\n;="
            ):
                raise ValueError("活动 ID 格式无效")
            if not isinstance(checked, bool):
                raise ValueError("活动选中状态必须是 bool")
            fields[f"activity_list[{index}][id]"] = activity_id
            fields[f"activity_list[{index}][is_checked]"] = "1" if checked else "0"

        response = self.auth.request(
            "POST",
            WEB_PUBLISH_URL,
            params={"callback": "bjhpublish"},
            data=fields,
            headers={
                "token": self.auth.creator_token,
                "Referer": WEB_EDITOR_REFERER,
                "Origin": "https://baijiahao.baidu.com",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        refreshed_token = response.headers.get("token")
        if refreshed_token:
            self.auth.creator_token = refreshed_token
        result = response_json(response)
        if str(result.get("errno")) != "0":
            raise BaijiaAPIError(f"Web 图文发布失败：errno={result.get('errno')}")
        ret = result.get("ret")
        publish_url = ret.get("url") if isinstance(ret, dict) else None
        if not isinstance(publish_url, str) or not publish_url.strip():
            raise BaijiaParseError("网页发布响应缺少 ret.url；请先在后台核对作品状态，勿直接重试")
        return result

    def upload_image(self, path_or_bytes, *, filename: str | None = None) -> str:
        """上传本地 JPEG/PNG；返回 Creator 图片 HTTPS URL。

        表单字段依据公开客户端源码，路由可达；真实账号响应尚未验证。
        """
        self._require_creator()
        if isinstance(path_or_bytes, (str, Path)):
            path = Path(path_or_bytes)
            data = path.read_bytes()
            name = filename or path.name
        elif isinstance(path_or_bytes, (bytes, bytearray, memoryview)):
            data = bytes(path_or_bytes)
            name = filename or "image"
        else:
            raise TypeError("图片需为本地文件路径或 bytes")
        if not data:
            raise ValueError("图片为空")
        mime, suffix = _image_type(data)
        name = Path(name).name
        if "\r" in name or "\n" in name:
            raise ValueError("文件名不能包含换行符")
        name = (Path(name).stem or "image") + suffix
        fields = {
            "type": "image",
            "app_id": self.auth.app_id,
            "is_waterlog": "1",
            "save_material": "1",
            "no_compress": "0",
            "is_events": "",
            "article_type": "news",
        }
        response = self.auth.request(
            "POST", UPLOAD_URL, data=fields, files={"media": (name, data, mime)},
            headers={"token": self.auth.creator_token, "Referer": CREATOR_REFERER},
        )
        payload = response_json(response)
        if payload.get("errno") not in (0, "0"):
            raise BaijiaAPIError(f"图片上传失败：errno={payload.get('errno')}，{payload.get('errmsg', '')}")
        image_url = (payload.get("ret") or {}).get("https_url")
        if not isinstance(image_url, str) or not image_url.startswith("https://"):
            raise BaijiaParseError("上传响应缺少 ret.https_url")
        return image_url

    def publish_article(
        self,
        title: str,
        content_html: str,
        *,
        origin_url: str = "",
        cover_urls: Iterable[str] = (),
        is_original: bool | None = None,
        allow_draft: bool = False,
    ) -> dict:
        """向 App ID / Token 开放接口提交图文。

        该操作会创建作品。调用者应先确认账号有开放接口权限。
        """
        self._require_partner()
        title = title.strip()
        if not 5 <= _sdk_char_count(title) <= 40:
            raise ValueError("标题需为 5–40 字（英文字符按半字计算）")
        if not content_html.strip() or _sdk_char_count(content_html) > 20_000:
            raise ValueError("HTML 正文需在 1–20000 字以内（英文字符按半字计算）")
        if not origin_url.strip():
            raise ValueError("origin_url 原文地址不能为空")
        origin_url = _valid_http_url(origin_url, "origin_url")
        if isinstance(cover_urls, (str, bytes)):
            raise ValueError("cover_urls 应为 URL 列表")
        covers = list(cover_urls)
        if len(covers) > 3:
            raise ValueError("最多传 3 张封面图")
        if not covers and not allow_draft:
            raise ValueError("无封面会进入草稿；如需草稿请传 allow_draft=True")
        if is_original is not None and not isinstance(is_original, bool):
            raise ValueError("is_original 必须是 bool 或 None")
        payload = {
            "app_id": self.auth.app_id,
            "app_token": self.auth.app_token,
            "title": title,
            "content": content_html,
            "origin_url": origin_url,
        }
        if covers:
            payload["cover_images"] = json.dumps(
                [{"src": _valid_http_url(url, "cover_urls")} for url in covers],
                ensure_ascii=False, separators=(",", ":"),
            )
        if is_original is not None:
            payload["is_original"] = int(is_original)
        response = self.auth.request(
            "POST", f"{OPEN_BASE}/article/publish", json=payload, use_cookie=False,
            headers={"Accept": "application/json"},
        )
        result = response_json(response)
        if result.get("errno") not in (0, "0"):
            raise BaijiaAPIError(f"图文发布失败：errno={result.get('errno')}，{result.get('errmsg', '')}")
        article_id = (result.get("data") or {}).get("article_id") if isinstance(result.get("data"), dict) else None
        if not str(article_id or "").isascii() or not str(article_id or "").isdigit():
            raise BaijiaParseError("平台返回发布成功，但缺少有效的 data.article_id；请先在后台核对，勿直接重试")
        return result

    def query_article_status(self, article_ids: str | Iterable[str]) -> dict:
        """查询已提交文章的审核/发布状态。"""
        self._require_partner()
        if isinstance(article_ids, str):
            ids = [article_ids]
        else:
            ids = list(article_ids)
        if not ids or any(not str(value).isascii() or not str(value).isdigit() for value in ids):
            raise ValueError("article_ids 必须是数字 ID")
        if len(ids) > 20:
            raise ValueError("单次最多查询 20 篇文章状态")
        response = self.auth.request(
            "POST", f"{OPEN_BASE}/query/status", use_cookie=False,
            json={"app_id": self.auth.app_id, "app_token": self.auth.app_token, "article_id": ",".join(map(str, ids))},
            headers={"Accept": "application/json"},
        )
        result = response_json(response)
        if result.get("errno") not in (0, "0"):
            raise BaijiaAPIError(f"状态查询失败：errno={result.get('errno')}，{result.get('errmsg', '')}")
        return result
