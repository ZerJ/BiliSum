import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from video_sum_infra.config import ServiceSettings
from video_sum_service.knowledge.article_ingestion import (
    ArticleIngestionService,
    _fetch_with_safe_redirects,
    canonicalize_article_url,
    extract_article,
)
from video_sum_service.knowledge.index_service import KnowledgeIndexService
from video_sum_service.repository import SqliteTaskRepository
from video_sum_service.schemas import KnowledgeDocumentRecord


def create_repository() -> SqliteTaskRepository:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    repository = SqliteTaskRepository(connection)
    repository.initialize()
    return repository


def test_repository_adds_summary_column_to_existing_article_table() -> None:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE knowledge_documents (
            document_id TEXT PRIMARY KEY,
            source_url TEXT NOT NULL,
            canonical_url TEXT NOT NULL UNIQUE,
            source_type TEXT NOT NULL DEFAULT 'article',
            title TEXT NOT NULL,
            author TEXT NOT NULL DEFAULT '',
            published_at TEXT,
            cover_url TEXT NOT NULL DEFAULT '',
            content_markdown TEXT NOT NULL,
            plain_text TEXT NOT NULL,
            image_count INTEGER NOT NULL DEFAULT 0,
            analyzed_image_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    repository = SqliteTaskRepository(connection)
    repository.initialize()

    columns = {
        row["name"]: row for row in connection.execute("PRAGMA table_info(knowledge_documents)")
    }
    assert columns["summary_markdown"]["dflt_value"] == "''"


def test_extract_wechat_article_uses_js_content_and_lazy_images() -> None:
    article = extract_article(
        """
        <html><head><meta property="og:title" content="备用标题"></head><body>
          <h1 id="activity-name">图文理解实战</h1>
          <span id="js_name">测试公众号</span>
          <span id="publish_time">2026-09-02</span>
          <div id="js_content">
            <p>第一段介绍文章目标和背景，包含足够长的正文信息用于文章解析，并说明为什么需要同时处理文字与图片。</p>
            <img data-src="https://mmbiz.qpic.cn/example.jpg" alt="架构图">
            <h2>实现步骤</h2>
            <p>第二段详细说明抓取、视觉分析、内容组织和知识库索引的完整过程，以及最终如何在问答中引用文章来源。</p>
          </div>
        </body></html>
        """,
        "https://mp.weixin.qq.com/s/example",
    )

    assert article.title == "图文理解实战"
    assert article.author == "测试公众号"
    assert article.published_at is not None
    assert any(block.kind == "image" and block.alt == "架构图" for block in article.blocks)
    assert any(block.kind == "heading" and block.text == "实现步骤" for block in article.blocks)


def test_extract_wechat_error_page_reports_invalid_link() -> None:
    with pytest.raises(HTTPException) as exc_info:
        extract_article(
            """
            <html><head><title></title></head><body>
              <div class="weui-msg"><p class="weui-msg__desc">参数错误</p></div>
            </body></html>
            """,
            "https://mp.weixin.qq.com/s/expired",
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == (
        "微信公众号返回参数错误。请从浏览器地址栏重新复制完整链接，并注意区分 I、l 和 1。"
    )


def test_extract_image_first_wechat_article_allows_short_text() -> None:
    article = extract_article(
        """
        <html><head><meta property="og:title" content="一图看懂产品更新"></head><body>
          <h1 id="activity-name">一图看懂产品更新</h1>
          <span id="js_name">测试公众号</span>
          <div id="js_content">
            <p>核心内容请查看下方长图。</p>
            <img data-src="https://mmbiz.qpic.cn/example-long-image.png" alt="产品更新长图">
          </div>
        </body></html>
        """,
        "https://mp.weixin.qq.com/s/image-first-article",
    )

    assert article.title == "一图看懂产品更新"
    assert [block.url for block in article.blocks if block.kind == "image"] == [
        "https://mmbiz.qpic.cn/example-long-image.png"
    ]


def test_extract_wechat_picture_message_reads_dynamic_image_list() -> None:
    article = extract_article(
        r"""
        <html><body><div id="img_list"></div><script>
        window.cgiDataNew = {
          title: '图片商业计划书',
          author: '测试作者',
          create_time: '2026-09-04 13:58',
          cdn_url: 'https://mmbiz.qpic.cn/cover.jpg',
          desc: '\x26lt;a href=\x22/topic\x22\x26gt;#商业计划书\x26lt;/a\x26gt;',
          picture_page_info_list: [
            {
              cdn_url: 'https://mmbiz.qpic.cn/page-1.jpg?wx_fmt=jpeg\x26amp;from=appmsg',
              width: '1080' * 1,
              height: '1800' * 1,
              is_qr_code: '0' * 1,
              watermark_info: {cdn_url: 'https://mmbiz.qpic.cn/watermark.jpg'}
            },
            {
              cdn_url: 'https://mmbiz.qpic.cn/qr-code.jpg',
              is_qr_code: '1' * 1
            }
          ]
        };
        </script></body></html>
        """,
        "https://mp.weixin.qq.com/s/picture-message",
    )

    assert article.title == "图片商业计划书"
    assert article.author == "测试作者"
    assert article.published_at is not None
    assert [block.url for block in article.blocks if block.kind == "image"] == [
        "https://mmbiz.qpic.cn/page-1.jpg?wx_fmt=jpeg&from=appmsg"
    ]
    assert article.blocks[0].text == "#商业计划书"


def test_canonicalize_article_url_rejects_non_http_scheme() -> None:
    with pytest.raises(HTTPException) as exc_info:
        canonicalize_article_url("file:///etc/passwd")
    assert exc_info.value.status_code == 400


def test_canonicalize_wechat_token_url_ignores_tracking_parameters() -> None:
    canonical = canonicalize_article_url("https://mp.weixin.qq.com/s/article-token")

    assert canonicalize_article_url(
        "https://mp.weixin.qq.com/s/article-token?nwr_flag=1&scene=1#wechat_redirect"
    ) == canonical


def test_canonicalize_legacy_wechat_url_keeps_only_article_identity() -> None:
    assert canonicalize_article_url(
        "https://mp.weixin.qq.com/s?scene=1&mid=200&sn=token&idx=3&__biz=biz#wechat_redirect"
    ) == "https://mp.weixin.qq.com/s?__biz=biz&mid=200&idx=3&sn=token"


def test_wechat_redirect_fetch_preserves_required_query_parameters(monkeypatch) -> None:
    requested_urls: list[str] = []

    class FakeResponse:
        def __init__(self, status_code: int, *, location: str = "", body: bytes = b"") -> None:
            self.status_code = status_code
            self.headers = {
                "content-type": "text/html",
                **({"location": location} if location else {}),
            }
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def iter_bytes(self):
            yield self.body

        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        def stream(self, _method, url, **_kwargs):
            requested_urls.append(url)
            if url.endswith("?nwr_flag=1"):
                return FakeResponse(200, body=b"<html>article</html>")
            return FakeResponse(302, location="?nwr_flag=1")

    monkeypatch.setattr(
        "video_sum_service.knowledge.article_ingestion._validate_public_url",
        lambda _url: None,
    )

    final_url, payload, _content_type = _fetch_with_safe_redirects(
        FakeClient(),
        "https://mp.weixin.qq.com/s/article-token",
        byte_limit=1024,
    )

    assert final_url == "https://mp.weixin.qq.com/s/article-token?nwr_flag=1"
    assert payload == b"<html>article</html>"
    assert requested_urls == [
        "https://mp.weixin.qq.com/s/article-token",
        "https://mp.weixin.qq.com/s/article-token?nwr_flag=1",
    ]


def test_reimporting_same_wechat_article_overwrites_existing_document(monkeypatch) -> None:
    repository = create_repository()
    payloads = [
        b"""
        <html><head><title>Old article</title></head><body><article>
          <p>This first version contains enough article body text to verify that the
          original knowledge document is persisted before the same URL is imported again.</p>
        </article></body></html>
        """,
        b"""
        <html><head><title>Updated article</title></head><body><article>
          <p>This refreshed version contains enough article body text to verify that the
          existing knowledge document is overwritten instead of creating a duplicate.</p>
        </article></body></html>
        """,
    ]

    def fake_fetch(_client, url, **_kwargs):
        return url, payloads.pop(0), "text/html"

    monkeypatch.setattr(
        "video_sum_service.knowledge.article_ingestion._fetch_with_safe_redirects",
        fake_fetch,
    )
    service = ArticleIngestionService(repository, ServiceSettings())
    first = service.import_url(
        "https://mp.weixin.qq.com/s/article-token?nwr_flag=1",
        analyze_images=False,
        summarize=False,
    )
    second = service.import_url(
        "https://mp.weixin.qq.com/s/article-token#wechat_redirect",
        analyze_images=False,
        summarize=False,
        force_refresh=True,
    )

    assert second.document.document_id == first.document.document_id
    assert second.document.title == "Updated article"
    assert repository.get_knowledge_document_count() == 1


def test_import_consolidates_duplicates_created_by_old_wechat_canonicalization() -> None:
    repository = create_repository()
    now = datetime.now(UTC)
    old_document = KnowledgeDocumentRecord(
        document_id="old-document",
        source_url="https://mp.weixin.qq.com/s/article-token",
        canonical_url="https://mp.weixin.qq.com/s/article-token",
        title="Old duplicate",
        content_markdown="# Old duplicate\n\nOld body",
        plain_text="Old body",
        created_at=now - timedelta(days=1),
        updated_at=now - timedelta(days=1),
    )
    latest_document = old_document.model_copy(
        update={
            "document_id": "latest-document",
            "source_url": "https://mp.weixin.qq.com/s/article-token?nwr_flag=1",
            "canonical_url": "https://mp.weixin.qq.com/s/article-token?nwr_flag=1",
            "title": "Latest duplicate",
            "summary_markdown": "### Summary\n\nKeep this version.",
            "updated_at": now,
        }
    )
    repository.upsert_knowledge_document(old_document)
    repository.upsert_knowledge_document(latest_document)

    result = ArticleIngestionService(repository, ServiceSettings()).import_url(
        "https://mp.weixin.qq.com/s/article-token?scene=1",
        analyze_images=False,
        summarize=False,
    )

    assert result.cached is True
    assert result.document.document_id == "latest-document"
    assert result.document.canonical_url == "https://mp.weixin.qq.com/s/article-token"
    assert result.replaced_document_ids == ["old-document"]
    assert repository.get_knowledge_document_count() == 1


def test_visual_config_falls_back_to_main_llm_when_visual_feature_disabled() -> None:
    settings = ServiceSettings(
        llm_enabled=True,
        llm_provider="openai-compatible",
        llm_base_url="https://llm.example.test/v1",
        llm_model="vision-capable-model",
        llm_api_key="main-key",
        visual_multimodal_enabled=False,
        visual_evidence_use_llm=True,
    )

    assert ArticleIngestionService(create_repository(), settings)._visual_config() == (
        "openai-compatible",
        "https://llm.example.test/v1",
        "vision-capable-model",
        "main-key",
    )


def test_article_import_persists_document_without_using_video_library(monkeypatch) -> None:
    repository = create_repository()
    html = b"""
    <html><head><title>Generic Article</title></head><body><article>
      <h1>Article heading</h1>
      <p>This is a sufficiently long article paragraph explaining retrieval, parsing,
      and indexing behavior.</p>
      <p>A second paragraph makes the extracted article useful enough to enter the
      knowledge base.</p>
    </article></body></html>
    """
    monkeypatch.setattr(
        "video_sum_service.knowledge.article_ingestion._fetch_with_safe_redirects",
        lambda *args, **kwargs: ("https://example.com/post", html, "text/html; charset=utf-8"),
    )

    result = ArticleIngestionService(repository, ServiceSettings()).import_url(
        "https://example.com/post",
        analyze_images=False,
        summarize=False,
    )

    assert result.cached is False
    assert result.document.title == "Generic Article"
    assert repository.get_knowledge_document_count() == 1
    assert repository.list_video_assets() == []


def test_article_import_generates_and_persists_body_summary(monkeypatch) -> None:
    repository = create_repository()
    html = b"""
    <html><head><title>Summary Article</title></head><body><article>
      <p>This article explains how extracted text is summarized after parsing and before
      it is persisted in the local knowledge database.</p>
      <p>The summary must remain separate from the complete article body so readers can
      inspect both the concise result and the original content.</p>
    </article></body></html>
    """
    monkeypatch.setattr(
        "video_sum_service.knowledge.article_ingestion._fetch_with_safe_redirects",
        lambda *args, **kwargs: ("https://example.com/summary", html, "text/html"),
    )
    llm_calls: list[dict[str, object]] = []

    def fake_chat_knowledge_llm(settings, **kwargs):
        llm_calls.append({"settings": settings, **kwargs})
        return "```markdown\n### 核心结论\n\n文章导入后会生成总结。\n\n### 关键要点\n\n- 保留完整正文\n```", None

    monkeypatch.setattr(
        "video_sum_service.knowledge.article_ingestion.chat_knowledge_llm",
        fake_chat_knowledge_llm,
    )
    monkeypatch.setattr(
        "video_sum_service.knowledge.article_ingestion.knowledge_llm_available",
        lambda _settings: True,
    )
    settings = ServiceSettings()

    result = ArticleIngestionService(repository, settings).import_url(
        "https://example.com/summary",
        analyze_images=False,
    )

    assert result.document.summary_markdown.startswith("### 核心结论")
    assert "```" not in result.document.summary_markdown
    assert repository.get_knowledge_document(result.document.document_id).summary_markdown == (
        result.document.summary_markdown
    )
    assert len(llm_calls) == 1
    assert llm_calls[0]["settings"] is settings
    assert "This article explains" in str(llm_calls[0]["user_prompt"])


def test_image_descriptions_are_included_in_article_summary_context(monkeypatch) -> None:
    repository = create_repository()
    html = b"""
    <html><head><meta property="og:title" content="Image First Article"></head><body>
      <article><p>See the visual guide below.</p>
      <img src="https://images.example.test/guide.png" alt="Visual guide"></article>
    </body></html>
    """
    monkeypatch.setattr(
        "video_sum_service.knowledge.article_ingestion._fetch_with_safe_redirects",
        lambda *args, **kwargs: ("https://example.com/image-first", html, "text/html"),
    )

    def fake_describe_images(_service, _client, article, _referer) -> None:
        for block in article.blocks:
            if block.kind == "image":
                block.visual_description = "The diagram explains six stages of startup funding."

    llm_prompts: list[str] = []
    monkeypatch.setattr(ArticleIngestionService, "_describe_images", fake_describe_images)
    monkeypatch.setattr(
        "video_sum_service.knowledge.article_ingestion.knowledge_llm_available",
        lambda _settings: True,
    )
    monkeypatch.setattr(
        "video_sum_service.knowledge.article_ingestion.chat_knowledge_llm",
        lambda _settings, **kwargs: (
            llm_prompts.append(str(kwargs["user_prompt"])) or "### Summary\n\nSix stages.",
            None,
        ),
    )

    result = ArticleIngestionService(repository, ServiceSettings()).import_url(
        "https://example.com/image-first",
        analyze_images=True,
        summarize=True,
    )

    assert "six stages of startup funding" in result.document.plain_text
    assert "six stages of startup funding" in llm_prompts[0]


def test_cached_article_without_summary_is_summarized_without_refetching(monkeypatch) -> None:
    repository = create_repository()
    html = b"""
    <html><head><title>Cached Article</title></head><body><article>
      <p>This cached article has enough extracted body content to be imported before the
      body summary feature is enabled for the existing local document.</p>
      <p>A later import should summarize the stored plain text without downloading the page again.</p>
    </article></body></html>
    """
    fetch_calls: list[str] = []

    def fake_fetch(_client, url, **_kwargs):
        fetch_calls.append(url)
        return "https://example.com/cached", html, "text/html"

    monkeypatch.setattr(
        "video_sum_service.knowledge.article_ingestion._fetch_with_safe_redirects",
        fake_fetch,
    )
    service = ArticleIngestionService(repository, ServiceSettings())
    first = service.import_url(
        "https://example.com/cached",
        analyze_images=False,
        summarize=False,
    )
    monkeypatch.setattr(
        "video_sum_service.knowledge.article_ingestion.chat_knowledge_llm",
        lambda *_args, **_kwargs: (
            "### 核心结论\n\n缓存文章已补充总结。\n\n### 关键要点\n\n- 无需重新抓取",
            None,
        ),
    )
    monkeypatch.setattr(
        "video_sum_service.knowledge.article_ingestion.knowledge_llm_available",
        lambda _settings: True,
    )

    second = service.import_url("https://example.com/cached", analyze_images=False)

    assert first.document.summary_markdown == ""
    assert second.cached is False
    assert "缓存文章已补充总结" in second.document.summary_markdown
    assert len(fetch_calls) == 1


def test_article_summary_requires_configured_knowledge_llm() -> None:
    service = ArticleIngestionService(repository=create_repository(), settings=ServiceSettings())

    with pytest.raises(HTTPException) as exc_info:
        service._summarize_article("未配置模型", "", "这是一段等待总结的文章正文。")

    assert exc_info.value.status_code == 400
    assert "关闭“生成正文总结”" in str(exc_info.value.detail)


class FakeCollection:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, object]] = {}

    def delete(self, *, where=None, ids=None) -> None:
        if ids:
            for item_id in ids:
                self.items.pop(item_id, None)
        if where and "document_id" in where:
            self.items = {
                key: value
                for key, value in self.items.items()
                if value["metadata"].get("document_id") != where["document_id"]
            }

    def add(self, *, ids, documents, embeddings, metadatas) -> None:
        for index, item_id in enumerate(ids):
            self.items[item_id] = {
                "document": documents[index],
                "embedding": embeddings[index],
                "metadata": metadatas[index],
            }

    def query(self, *, query_embeddings, n_results):
        del query_embeddings
        values = list(self.items.items())[:n_results]
        return {
            "ids": [[item_id for item_id, _item in values]],
            "documents": [[str(item["document"]) for _item_id, item in values]],
            "metadatas": [[item["metadata"] for _item_id, item in values]],
            "distances": [[0.1 for _item_id, _item in values]],
        }


class FakeKnowledgeIndexService(KnowledgeIndexService):
    def __init__(self, repository: SqliteTaskRepository) -> None:
        super().__init__(repository, ServiceSettings())
        self.collection = FakeCollection()

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[float(index + 1), 0.5] for index, _text in enumerate(texts)]

    def _get_collection(self):
        return self.collection


def test_index_document_writes_article_chunks_and_metadata(monkeypatch) -> None:
    repository = create_repository()
    html = b"""
    <html><head><title>Indexed Article</title></head><body><article>
      <h2>First section</h2>
      <p>This section contains enough content to verify article chunks and their
      vector metadata.</p>
      <h2>Second section</h2>
      <p>This section contains a different topic and remains separately searchable in the index.</p>
    </article></body></html>
    """
    monkeypatch.setattr(
        "video_sum_service.knowledge.article_ingestion._fetch_with_safe_redirects",
        lambda *args, **kwargs: ("https://example.com/indexed", html, "text/html"),
    )
    imported = ArticleIngestionService(repository, ServiceSettings()).import_url(
        "https://example.com/indexed",
        analyze_images=False,
        summarize=False,
    )
    repository.upsert_knowledge_document(
        imported.document.model_copy(
            update={"summary_markdown": "### 核心结论\n\n文章介绍了向量索引。"}
        )
    )
    index_service = FakeKnowledgeIndexService(repository)

    chunk_count = index_service.index_document(imported.document.document_id)

    assert chunk_count >= 3
    assert (
        repository.get_knowledge_document_chunk_count(imported.document.document_id) == chunk_count
    )
    assert all(
        item["metadata"]["source_kind"] == "article"
        for item in index_service.collection.items.values()
    )
    assert all(
        item["metadata"]["source_url"] == "https://example.com/indexed"
        for item in index_service.collection.items.values()
    )
    assert any(
        item["metadata"]["index_type"] == "article_summary"
        for item in index_service.collection.items.values()
    )
    assert any(
        item_id.endswith(":article_summary:0")
        for item_id in index_service.collection.items
    )

    results = index_service.search("vector metadata", limit=3)

    assert results[0].source_kind == "article"
    assert results[0].source_id == imported.document.document_id
    assert results[0].source_url == "https://example.com/indexed"
    assert results[0].video_id is None


def test_article_search_ignores_orphaned_vector_entries(monkeypatch) -> None:
    repository = create_repository()
    html = b"""
    <html><head><title>Current Article</title></head><body><article>
      <p>This current article contains enough body text to create a valid searchable
      document before an orphaned vector entry is added to the same collection.</p>
    </article></body></html>
    """
    monkeypatch.setattr(
        "video_sum_service.knowledge.article_ingestion._fetch_with_safe_redirects",
        lambda *args, **kwargs: ("https://example.com/current", html, "text/html"),
    )
    imported = ArticleIngestionService(repository, ServiceSettings()).import_url(
        "https://example.com/current",
        analyze_images=False,
        summarize=False,
    )
    index_service = FakeKnowledgeIndexService(repository)
    index_service.index_document(imported.document.document_id)
    index_service.collection.add(
        ids=["document:deleted-document:article_section:1"],
        documents=["Deleted duplicate article body"],
        embeddings=[[1.0, 0.5]],
        metadatas=[
            {
                "source_kind": "article",
                "source_id": "deleted-document",
                "document_id": "deleted-document",
                "title": "Deleted duplicate",
            }
        ],
    )

    results = index_service.search("article", limit=10)

    assert {result.source_id for result in results} == {imported.document.document_id}
