"""百家号图文开放接口，以及 Creator 端图片上传。"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from .auth import BaijiaAuth, BaijiaAPIError, BaijiaAuthError, BaijiaParseError, response_json


OPEN_BASE = "https://baijiahao.baidu.com/builderinner/open/resource"
UPLOAD_URL = "https://baijiahao.baidu.com/pcui/picture/uploadproxy"
CREATOR_REFERER = "https://baijiahao.baidu.com/builder/rc/edit?type=news"


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


class BaijiaCreatorAPI:
    def __init__(self, auth: BaijiaAuth):
        self.auth = auth

    def _require_partner(self) -> None:
        if not self.auth.app_id or not self.auth.app_token:
            raise BaijiaAuthError("开放接口需要 App ID 与 App Token")

    def _require_creator(self) -> None:
        if not self.auth.cookie or not self.auth.creator_token or not self.auth.app_id:
            raise BaijiaAuthError("图片上传需要 Cookie、Creator token 和 App ID")

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
        is_original: bool = True,
    ) -> dict:
        """向 App ID / Token 开放接口提交图文。

        该操作会创建作品。调用者应先确认账号有开放接口权限。
        """
        self._require_partner()
        title = title.strip()
        if not title or not content_html.strip():
            raise ValueError("标题和 HTML 正文不能为空")
        if isinstance(cover_urls, (str, bytes)):
            raise ValueError("cover_urls 应为 URL 列表")
        covers = list(cover_urls)
        if len(covers) > 3:
            raise ValueError("最多传 3 张封面图")
        if origin_url:
            origin_url = _valid_http_url(origin_url, "origin_url")
        payload = {
            "app_id": self.auth.app_id,
            "app_token": self.auth.app_token,
            "title": title,
            "content": content_html,
            "origin_url": origin_url,
            "cover_images": [{"src": _valid_http_url(url, "cover_urls")} for url in covers],
            "is_original": int(is_original),
        }
        response = self.auth.request(
            "POST", f"{OPEN_BASE}/article/publish", json=payload, use_cookie=False,
            headers={"Accept": "application/json"},
        )
        result = response_json(response)
        if result.get("errno") not in (0, "0"):
            raise BaijiaAPIError(f"图文发布失败：errno={result.get('errno')}，{result.get('errmsg', '')}")
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
        response = self.auth.request(
            "POST", f"{OPEN_BASE}/query/status", use_cookie=False,
            json={"app_id": self.auth.app_id, "app_token": self.auth.app_token, "article_id": ",".join(map(str, ids))},
            headers={"Accept": "application/json"},
        )
        return response_json(response)
