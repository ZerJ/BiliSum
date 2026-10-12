from __future__ import annotations

import base64
import ipaddress
import json
import re
import socket
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from uuid import uuid4

import httpx
from fastapi import HTTPException
from video_sum_infra.config import ServiceSettings
from video_sum_infra.llm import (
    anthropic_messages_url,
    apply_openai_reasoning_settings,
    build_anthropic_messages_payload,
    extract_llm_message_content,
    normalize_openai_compatible_model_name,
    openai_chat_completions_url,
)

from video_sum_service.knowledge.local_llm import chat_knowledge_llm, knowledge_llm_available
from video_sum_service.repository import DEFAULT_KNOWLEDGE_USER_ID, SqliteTaskRepository
from video_sum_service.schemas import KnowledgeDocumentRecord

_ARTICLE_MAX_BYTES = 5 * 1024 * 1024
_IMAGE_MAX_BYTES = 8 * 1024 * 1024
_MAX_REDIRECTS = 5
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 MicroMessenger/3.9.12"
)


@dataclass(slots=True)
class ArticleBlock:
    kind: str
    text: str = ""
    url: str = ""
    alt: str = ""
    heading_level: int = 0
    visual_description: str = ""


@dataclass(slots=True)
class ExtractedArticle:
    title: str
    author: str
    published_at: datetime | None
    cover_url: str
    blocks: list[ArticleBlock] = field(default_factory=list)


@dataclass(slots=True)
class ArticleImportResult:
    document: KnowledgeDocumentRecord
    cached: bool
    replaced_document_ids: list[str] = field(default_factory=list)


def _normalize_http_url(raw_url: str) -> tuple[str, str, str, str, str]:
    value = str(raw_url or "").strip()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="文章链接格式不正确。") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=400, detail="文章链接必须是有效的 HTTP 或 HTTPS 地址。")
    host = parsed.hostname.encode("idna").decode("ascii").lower()
    netloc = host if port is None else f"{host}:{port}"
    return parsed.scheme.lower(), host, netloc, parsed.path or "/", parsed.query


def canonicalize_article_url(raw_url: str) -> str:
    scheme, host, netloc, path, query = _normalize_http_url(raw_url)
    if host == "mp.weixin.qq.com":
        path = path.rstrip("/") or "/"
        if re.fullmatch(r"/s/[^/]+", path):
            query = ""
        elif path == "/s":
            identity_keys = ("__biz", "mid", "idx", "sn")
            values = dict(parse_qsl(query, keep_blank_values=True))
            identity_query = [(key, values[key]) for key in identity_keys if key in values]
            if identity_query:
                query = urlencode(identity_query)
    return urlunsplit((scheme, netloc, path, query, ""))


def _normalize_fetch_url(raw_url: str) -> str:
    scheme, _host, netloc, path, query = _normalize_http_url(raw_url)
    return urlunsplit((scheme, netloc, path, query, ""))


def _validate_public_url(url: str) -> None:
    parsed = urlsplit(url)
    hostname = parsed.hostname
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise HTTPException(status_code=400, detail="仅支持公开的 HTTP 或 HTTPS 文章链接。")
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
            )
        }
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"无法解析文章域名：{hostname}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise HTTPException(
                status_code=400, detail="文章链接不能指向本机、局域网或保留网络地址。"
            )


def _read_limited(response: httpx.Response, limit: int) -> bytes:
    content_length = response.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > limit:
        raise HTTPException(status_code=413, detail="远程内容体积超过允许上限。")
    payload = bytearray()
    for chunk in response.iter_bytes():
        payload.extend(chunk)
        if len(payload) > limit:
            raise HTTPException(status_code=413, detail="远程内容体积超过允许上限。")
    return bytes(payload)


def _fetch_with_safe_redirects(
    client: httpx.Client,
    url: str,
    *,
    byte_limit: int,
    referer: str | None = None,
) -> tuple[str, bytes, str]:
    current = _normalize_fetch_url(url)
    headers = {"User-Agent": _USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7"}
    if referer:
        headers["Referer"] = referer
    for _ in range(_MAX_REDIRECTS + 1):
        _validate_public_url(current)
        with client.stream("GET", current, headers=headers, follow_redirects=False) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise HTTPException(
                        status_code=502, detail="文章站点返回了无目标地址的重定向。"
                    )
                current = _normalize_fetch_url(urljoin(current, location))
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"文章页面请求失败（HTTP {response.status_code}）。",
                ) from exc
            return (
                current,
                _read_limited(response, byte_limit),
                response.headers.get("content-type", ""),
            )
    raise HTTPException(status_code=502, detail="文章链接重定向次数过多。")


class _ArticleParser(HTMLParser):
    _BLOCK_TAGS = {"p", "li", "blockquote", "pre", "div", "section"}
    _SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "form", "button"}

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.blocks: list[ArticleBlock] = []
        self.meta: dict[str, str] = {}
        self.page_title = ""
        self.special: dict[str, str] = {}
        self._stack: list[str] = []
        self._root_depth: int | None = None
        self._root_priority = 0
        self._skip_depth = 0
        self._buffer: list[str] = []
        self._heading_level = 0
        self._title_depth = 0
        self._title_buffer: list[str] = []
        self._special_stack: list[tuple[int, str]] = []

    def _inside_content(self) -> bool:
        return self._root_depth is not None and len(self._stack) >= self._root_depth

    def _flush_text(self, heading_level: int | None = None) -> None:
        text = re.sub(r"[ \t\r\f\v]+", " ", "".join(self._buffer))
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        self._buffer = []
        if not text:
            return
        level = self._heading_level if heading_level is None else heading_level
        self.blocks.append(
            ArticleBlock(kind="heading" if level else "text", text=text, heading_level=level)
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {key.lower(): str(value or "") for key, value in attrs}
        self._stack.append(tag)
        depth = len(self._stack)

        if tag == "meta":
            key = (
                attributes.get("property")
                or attributes.get("name")
                or attributes.get("itemprop")
                or ""
            ).lower()
            content = unescape(attributes.get("content") or "").strip()
            if key and content:
                self.meta[key] = content
        if tag == "title":
            self._title_depth = depth
            self._title_buffer = []

        element_id = attributes.get("id", "")
        special_key = {
            "activity-name": "title",
            "js_name": "author",
            "publish_time": "published_at",
        }.get(element_id)
        if special_key:
            self._special_stack.append((depth, special_key))

        priority = (
            3
            if element_id == "js_content"
            else 2
            if tag in {"article", "main"}
            else 1
            if tag == "body"
            else 0
        )
        if priority > self._root_priority:
            self._root_priority = priority
            self._root_depth = depth
            self.blocks = []
            self._buffer = []

        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        if not self._inside_content() or self._skip_depth:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._flush_text()
            self._heading_level = int(tag[1])
        elif tag == "br":
            self._buffer.append("\n")
        elif tag == "img":
            self._flush_text()
            image_url = (
                attributes.get("data-src")
                or attributes.get("data-original")
                or attributes.get("data-lazy-src")
                or attributes.get("src")
                or ""
            ).strip()
            if image_url and not image_url.startswith(("data:", "javascript:")):
                self.blocks.append(
                    ArticleBlock(
                        kind="image",
                        url=urljoin(self.base_url, image_url),
                        alt=unescape(attributes.get("alt") or "").strip(),
                    )
                )

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._title_depth:
            self._title_buffer.append(data)
        for _depth, key in self._special_stack:
            self.special[key] = f"{self.special.get(key, '')}{data}"
        if self._inside_content() and not self._skip_depth:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        depth = len(self._stack)
        if self._inside_content() and not self._skip_depth:
            if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                self._flush_text(self._heading_level)
                self._heading_level = 0
            elif tag in self._BLOCK_TAGS:
                self._flush_text()
        if tag == "title" and self._title_depth == depth:
            self.page_title = re.sub(r"\s+", " ", "".join(self._title_buffer)).strip()
            self._title_depth = 0
        self._special_stack = [item for item in self._special_stack if item[0] != depth]
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if self._root_depth == depth:
            self._flush_text()
            self._root_depth = None
        if self._stack:
            self._stack.pop()


def _parse_published_at(value: str) -> datetime | None:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
    if not cleaned:
        return None
    if cleaned.isdigit() and len(cleaned) >= 10:
        try:
            return datetime.fromtimestamp(int(cleaned[:10]), tz=UTC)
        except (OverflowError, ValueError):
            return None
    for candidate in (cleaned, cleaned.replace("年", "-").replace("月", "-").replace("日", "")):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _decode_js_string(value: str) -> str:
    decoded = re.sub(
        r"\\x([0-9a-fA-F]{2})",
        lambda match: chr(int(match.group(1), 16)),
        value,
    )
    decoded = re.sub(
        r"\\u([0-9a-fA-F]{4})",
        lambda match: chr(int(match.group(1), 16)),
        decoded,
    )
    escapes = {"n": "\n", "r": "\r", "t": "\t", "/": "/", "\\": "\\", "'": "'", '"': '"'}
    return re.sub(r"\\(.)", lambda match: escapes.get(match.group(1), match.group(1)), decoded)


def _read_js_quoted_value(source: str, start: int) -> tuple[str, int] | None:
    if start >= len(source) or source[start] not in {"'", '"'}:
        return None
    quote = source[start]
    escaped = False
    index = start + 1
    while index < len(source):
        character = source[index]
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == quote:
            return _decode_js_string(source[start + 1 : index]), index + 1
        index += 1
    return None


def _read_balanced_js_value(
    source: str,
    start: int,
    opening: str,
    closing: str,
) -> tuple[str, int] | None:
    if start >= len(source) or source[start] != opening:
        return None
    depth = 0
    quote = ""
    escaped = False
    for index in range(start, len(source)):
        character = source[index]
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = ""
            continue
        if character in {"'", '"', "`"}:
            quote = character
        elif character == opening:
            depth += 1
        elif character == closing:
            depth -= 1
            if depth == 0:
                return source[start : index + 1], index + 1
    return None


def _top_level_js_property(source: str, property_name: str) -> str:
    property_pattern = re.compile(rf"(?:[\"']?{re.escape(property_name)}[\"']?)\s*:\s*")
    brace_depth = 0
    bracket_depth = 0
    quote = ""
    escaped = False
    index = 0
    while index < len(source):
        character = source[index]
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = ""
            index += 1
            continue
        if character in {"'", '"', "`"}:
            quote = character
            index += 1
            continue
        if character == "{":
            brace_depth += 1
        elif character == "}":
            brace_depth -= 1
        elif character == "[":
            bracket_depth += 1
        elif character == "]":
            bracket_depth -= 1
        elif brace_depth == 1 and bracket_depth == 0:
            match = property_pattern.match(source, index)
            if match:
                value_start = match.end()
                if value_start < len(source) and source[value_start] in {"'", '"'}:
                    parsed = _read_js_quoted_value(source, value_start)
                elif value_start < len(source) and source[value_start] == "[":
                    parsed = _read_balanced_js_value(source, value_start, "[", "]")
                elif value_start < len(source) and source[value_start] == "{":
                    parsed = _read_balanced_js_value(source, value_start, "{", "}")
                else:
                    parsed = None
                return parsed[0] if parsed else ""
        index += 1
    return ""


def _top_level_js_objects(array_source: str) -> list[str]:
    objects: list[str] = []
    index = 1 if array_source.startswith("[") else 0
    while index < len(array_source):
        if array_source[index] == "{":
            parsed = _read_balanced_js_value(array_source, index, "{", "}")
            if parsed is not None:
                objects.append(parsed[0])
                index = parsed[1]
                continue
        index += 1
    return objects


def _extract_wechat_picture_article(html: str, base_url: str) -> ExtractedArticle | None:
    assignment_index = html.find("window.cgiDataNew")
    if assignment_index < 0:
        return None
    object_start = html.find("{", assignment_index)
    cgi_data = _read_balanced_js_value(html, object_start, "{", "}")
    if cgi_data is None:
        return None
    cgi_object = cgi_data[0]
    picture_list = _top_level_js_property(cgi_object, "picture_page_info_list")
    if not picture_list:
        return None

    blocks: list[ArticleBlock] = []
    seen_urls: set[str] = set()
    for item in _top_level_js_objects(picture_list):
        if _top_level_js_property(item, "is_qr_code") == "1":
            continue
        image_url = unescape(_top_level_js_property(item, "cdn_url")).strip()
        if not image_url:
            continue
        image_url = urljoin(base_url, image_url)
        if image_url in seen_urls:
            continue
        seen_urls.add(image_url)
        blocks.append(
            ArticleBlock(
                kind="image",
                url=image_url,
                alt=f"文章图片 {len(blocks) + 1}",
            )
        )
    if not blocks:
        return None

    description = unescape(unescape(_top_level_js_property(cgi_object, "desc")))
    description = re.sub(r"<[^>]+>", " ", description)
    description = re.sub(r"\s+", " ", unescape(description)).strip()
    if description:
        blocks.insert(0, ArticleBlock(kind="text", text=description))
    title = re.sub(r"\s+", " ", _top_level_js_property(cgi_object, "title")).strip()
    author = re.sub(r"\s+", " ", _top_level_js_property(cgi_object, "author")).strip()
    cover_url = unescape(_top_level_js_property(cgi_object, "cdn_url")).strip()
    return ExtractedArticle(
        title=(title or "未命名图片文章")[:300],
        author=author[:200],
        published_at=_parse_published_at(_top_level_js_property(cgi_object, "create_time")),
        cover_url=urljoin(base_url, cover_url) if cover_url else blocks[-1].url,
        blocks=blocks,
    )


def extract_article(html: str, base_url: str) -> ExtractedArticle:
    picture_article = _extract_wechat_picture_article(html, base_url)
    if picture_article is not None:
        return picture_article

    parser = _ArticleParser(base_url)
    parser.feed(html)
    parser.close()
    title = (
        parser.special.get("title")
        or parser.meta.get("og:title")
        or parser.meta.get("twitter:title")
        or parser.page_title
    )
    title = re.sub(r"\s+", " ", unescape(title)).strip()
    author = (
        parser.special.get("author")
        or parser.meta.get("author")
        or parser.meta.get("article:author")
        or parser.meta.get("og:article:author")
        or ""
    )
    author = re.sub(r"\s+", " ", unescape(author)).strip()
    published = (
        parser.special.get("published_at")
        or parser.meta.get("article:published_time")
        or parser.meta.get("og:article:published_time")
        or parser.meta.get("publishdate")
        or ""
    )
    cover_url = parser.meta.get("og:image") or parser.meta.get("twitter:image") or ""
    blocks: list[ArticleBlock] = []
    seen_images: set[str] = set()
    for block in parser.blocks:
        if block.kind == "image":
            canonical_image = block.url.split("#", 1)[0]
            if canonical_image in seen_images:
                continue
            seen_images.add(canonical_image)
        if block.kind != "image" and len(block.text.strip()) < 2:
            continue
        blocks.append(block)
    plain_text = "\n".join(block.text for block in blocks if block.kind != "image")
    hostname = (urlsplit(base_url).hostname or "").lower()
    if hostname == "mp.weixin.qq.com" and "参数错误" in plain_text:
        raise HTTPException(
            status_code=422,
            detail=(
                "微信公众号返回参数错误。请从浏览器地址栏重新复制完整链接，"
                "并注意区分 I、l 和 1。"
            ),
        )
    has_explicit_title = bool(title)
    if not title:
        title = next((block.text for block in blocks if block.kind == "heading"), "未命名文章")[
            :160
        ]
    has_article_images = any(block.kind == "image" for block in blocks)
    if len(re.sub(r"\s+", "", plain_text)) < 80 and not (
        has_article_images and has_explicit_title
    ):
        raise HTTPException(
            status_code=422,
            detail="未能从该链接提取到足够的文章正文或图片，页面可能需要登录或已失效。",
        )
    return ExtractedArticle(
        title=title[:300],
        author=author[:200],
        published_at=_parse_published_at(published),
        cover_url=urljoin(base_url, cover_url) if cover_url else "",
        blocks=blocks,
    )


class ArticleIngestionService:
    def __init__(
        self,
        repository: SqliteTaskRepository,
        settings: ServiceSettings,
        user_id: str = DEFAULT_KNOWLEDGE_USER_ID,
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._user_id = user_id

    def import_url(
        self,
        raw_url: str,
        *,
        analyze_images: bool = True,
        summarize: bool = True,
        force_refresh: bool = False,
    ) -> ArticleImportResult:
        canonical_url = canonicalize_article_url(raw_url)
        equivalent_documents = []
        for document in self._repository.list_knowledge_documents():
            try:
                stored_canonical_url = canonicalize_article_url(document.canonical_url)
            except HTTPException:
                continue
            if stored_canonical_url == canonical_url:
                equivalent_documents.append(document)
        existing, replaced_document_ids = self._repository.consolidate_knowledge_documents(
            canonical_url,
            [document.document_id for document in equivalent_documents],
        )
        if existing is not None and not force_refresh:
            if summarize and not existing.summary_markdown.strip():
                summary_markdown = self._summarize_article(
                    existing.title,
                    existing.author,
                    existing.plain_text,
                )
                refreshed = existing.model_copy(
                    update={
                        "summary_markdown": summary_markdown,
                        "updated_at": datetime.now(UTC),
                    }
                )
                return ArticleImportResult(
                    document=self._repository.upsert_knowledge_document(
                        refreshed,
                        user_id=self._user_id,
                    ),
                    cached=False,
                    replaced_document_ids=replaced_document_ids,
                )
            return ArticleImportResult(
                document=existing,
                cached=True,
                replaced_document_ids=replaced_document_ids,
            )

        timeout = max(15, min(int(self._settings.visual_evidence_timeout_seconds or 120), 300))
        try:
            with httpx.Client(timeout=timeout) as client:
                final_url, payload, content_type = _fetch_with_safe_redirects(
                    client,
                    raw_url,
                    byte_limit=_ARTICLE_MAX_BYTES,
                )
                if "html" not in content_type.lower() and not payload.lstrip().startswith(b"<"):
                    raise HTTPException(status_code=415, detail="该链接返回的不是 HTML 文章页面。")
                html = payload.decode("utf-8", errors="replace")
                article = extract_article(html, final_url)
                images = [block for block in article.blocks if block.kind == "image"]
                if analyze_images and images:
                    self._describe_images(client, article, final_url)
        except HTTPException:
            raise
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="抓取文章或分析图片超时。") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"抓取文章失败：{exc}") from exc

        now = datetime.now(UTC)
        source_type = (
            "wechat_article" if urlsplit(final_url).hostname == "mp.weixin.qq.com" else "article"
        )
        markdown = self._build_markdown(article, final_url)
        plain_text_parts: list[str] = []
        for block in article.blocks:
            if block.kind != "image" and block.text.strip():
                plain_text_parts.append(block.text.strip())
            elif block.kind == "image" and block.visual_description.strip():
                plain_text_parts.append(f"图片理解：{block.visual_description.strip()}")
        plain_text = "\n".join(plain_text_parts).strip()
        summary_markdown = (
            self._summarize_article(article.title, article.author, plain_text)
            if summarize
            else (existing.summary_markdown if existing is not None else "")
        )
        analyzed_count = sum(
            1 for block in article.blocks if block.kind == "image" and block.visual_description
        )
        document = KnowledgeDocumentRecord(
            document_id=existing.document_id if existing is not None else uuid4().hex,
            source_url=final_url,
            canonical_url=canonical_url,
            source_type=source_type,
            title=article.title,
            author=article.author,
            published_at=article.published_at,
            cover_url=article.cover_url
            or next((block.url for block in article.blocks if block.kind == "image"), ""),
            content_markdown=markdown,
            plain_text=plain_text,
            summary_markdown=summary_markdown,
            image_count=sum(1 for block in article.blocks if block.kind == "image"),
            analyzed_image_count=analyzed_count,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        return ArticleImportResult(
            document=self._repository.upsert_knowledge_document(
                document,
                user_id=self._user_id,
            ),
            cached=False,
            replaced_document_ids=replaced_document_ids,
        )

    def _summarize_article(self, title: str, author: str, plain_text: str) -> str:
        if not knowledge_llm_available(self._settings):
            raise HTTPException(
                status_code=400,
                detail=(
                    "生成正文总结需要先配置并启用知识库 LLM（默认跟随主模型），"
                    "也可以在导入时关闭“生成正文总结”。"
                ),
            )
        max_chars = max(
            4_000,
            min(int(self._settings.summary_full_context_max_chars or 18_000), 60_000),
        )
        summary_source = plain_text.strip()
        if len(summary_source) > max_chars:
            head_chars = max_chars * 2 // 3
            tail_chars = max_chars - head_chars
            summary_source = (
                f"{summary_source[:head_chars].rstrip()}\n\n"
                "[正文过长，中间部分已省略]\n\n"
                f"{summary_source[-tail_chars:].lstrip()}"
            )
        response_text, _body = chat_knowledge_llm(
            self._settings,
            system_prompt=(
                "你是严谨的中文文章编辑。请仅依据给定正文生成简洁、准确的 Markdown 摘要，"
                "不得补充正文中不存在的事实。输出必须包含‘### 核心结论’和‘### 关键要点’"
                "两个小节；核心结论使用 3 至 5 句话，关键要点使用 4 至 8 个项目符号。"
                "不要重复文章标题，不要输出代码围栏或额外说明。"
            ),
            user_prompt=(
                f"文章标题：{title}\n"
                f"作者：{author or '未知'}\n\n"
                f"正文：\n---\n{summary_source}\n---"
            ),
            max_tokens=1_200,
            temperature=0.2,
        )
        cleaned = response_text.strip()
        fence_match = re.fullmatch(r"```(?:markdown|md)?\s*(.*?)\s*```", cleaned, re.DOTALL)
        return (fence_match.group(1) if fence_match else cleaned).strip()

    def _visual_config(self) -> tuple[str, str, str, str]:
        use_dedicated_visual = bool(
            self._settings.visual_multimodal_enabled
            and self._settings.visual_evidence_use_llm
        )
        if use_dedicated_visual:
            provider = str(
                self._settings.visual_vlm_provider or "openai-compatible"
            ).strip().lower()
            base_url = str(
                self._settings.visual_evidence_base_url or self._settings.llm_base_url or ""
            ).rstrip("/")
            model = str(
                self._settings.visual_evidence_model or self._settings.llm_model or ""
            ).strip()
            api_key = str(
                self._settings.visual_evidence_api_key or self._settings.llm_api_key or ""
            ).strip()
        elif self._settings.llm_enabled:
            provider = str(self._settings.llm_provider or "openai-compatible").strip().lower()
            base_url = str(self._settings.llm_base_url or "").rstrip("/")
            model = str(self._settings.llm_model or "").strip()
            api_key = str(self._settings.llm_api_key or "").strip()
        else:
            raise HTTPException(
                status_code=400,
                detail="文章包含图片，请先配置视觉模型或启用支持图片理解的主模型。",
            )
        if not (base_url and model and api_key):
            raise HTTPException(
                status_code=400,
                detail="文章图像理解需要完整的模型地址、模型名称和 API Key。",
            )
        return provider, base_url, model, api_key

    def _describe_images(
        self, client: httpx.Client, article: ExtractedArticle, referer: str
    ) -> None:
        provider, base_url, model, api_key = self._visual_config()
        max_images = max(1, min(int(self._settings.visual_evidence_max_frames or 12), 20))
        image_blocks = [block for block in article.blocks if block.kind == "image"][:max_images]
        failures: list[str] = []
        for block in image_blocks:
            try:
                _final_url, image_bytes, content_type = _fetch_with_safe_redirects(
                    client,
                    block.url,
                    byte_limit=_IMAGE_MAX_BYTES,
                    referer=referer,
                )
                media_type = content_type.split(";", 1)[0].strip().lower()
                if not media_type.startswith("image/"):
                    failures.append("远程资源不是图片")
                    continue
                encoded = base64.b64encode(image_bytes).decode("ascii")
                block.visual_description = self._request_image_description(
                    provider,
                    base_url,
                    model,
                    api_key,
                    article.title,
                    block.alt,
                    media_type,
                    encoded,
                )
            except HTTPException as exc:
                failures.append(str(exc.detail))
            except httpx.HTTPError as exc:
                failures.append(str(exc))
        if image_blocks and not any(block.visual_description for block in image_blocks):
            detail = (
                re.sub(r"\s+", " ", failures[0]).strip()[:180] if failures else "视觉模型未返回描述"
            )
            raise HTTPException(status_code=502, detail=f"文章图片理解失败：{detail}")

    def _request_image_description(
        self,
        provider: str,
        base_url: str,
        model: str,
        api_key: str,
        title: str,
        alt: str,
        media_type: str,
        encoded: str,
    ) -> str:
        prompt = (
            "请理解这张文章配图，只输出一段简洁、客观的中文描述。"
            "优先提取图表结论、流程步骤、界面字段、代码含义和可辨认文字；"
            "装饰图只说明其主题，不要猜测看不清的信息。"
            f"\n文章标题：{title}\n原图替代文本：{alt or '无'}"
        )
        payload: dict[str, object] = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_type};base64,{encoded}",
                                "detail": "low",
                            },
                        },
                    ],
                }
            ],
            "temperature": 0,
            "max_tokens": 600,
        }
        use_anthropic = provider == "anthropic" and "api.anthropic.com" in base_url.lower()
        if use_anthropic:
            request_url = anthropic_messages_url(base_url)
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }
            request_payload = build_anthropic_messages_payload(payload)
        else:
            request_url = openai_chat_completions_url(base_url)
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            request_payload = apply_openai_reasoning_settings(
                payload,
                thinking_type=self._settings.llm_thinking_type,
                reasoning_effort=self._settings.llm_reasoning_effort,
                provider=provider,
                model=model,
                base_url=base_url,
            )
            request_payload["model"] = normalize_openai_compatible_model_name(model)
        with httpx.Client(
            timeout=max(15, int(self._settings.visual_evidence_timeout_seconds or 120))
        ) as llm_client:
            response = llm_client.post(request_url, headers=headers, json=request_payload)
            response.raise_for_status()
        content = extract_llm_message_content(response.json())
        if content.startswith("{"):
            try:
                parsed = json.loads(content)
                content = str(
                    parsed.get("description")
                    or parsed.get("caption")
                    or parsed.get("summary")
                    or content
                )
            except json.JSONDecodeError:
                pass
        return re.sub(r"\s+", " ", content).strip()[:1200]

    @staticmethod
    def _build_markdown(article: ExtractedArticle, source_url: str) -> str:
        lines = [f"# {article.title}", ""]
        metadata = [
            item
            for item in [
                article.author,
                article.published_at.date().isoformat() if article.published_at else "",
            ]
            if item
        ]
        if metadata:
            lines.extend([" · ".join(metadata), ""])
        lines.extend([f"来源：{source_url}", ""])
        for block in article.blocks:
            if block.kind == "heading":
                lines.extend([f"{'#' * max(2, min(block.heading_level + 1, 6))} {block.text}", ""])
            elif block.kind == "image":
                lines.extend([f"![{block.alt or '文章配图'}]({block.url})", ""])
                if block.visual_description:
                    lines.extend([f"> 图片理解：{block.visual_description}", ""])
            else:
                lines.extend([block.text, ""])
        return "\n".join(lines).strip() + "\n"
