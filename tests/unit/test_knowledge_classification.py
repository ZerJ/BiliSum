import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from video_sum_service.repository import SqliteTaskRepository
from video_sum_service.routers.knowledge import classify_knowledge_content
from video_sum_service.schemas import KnowledgeClassifyRequest, KnowledgeDocumentRecord, VideoAssetRecord


def test_classification_persists_and_failed_batch_is_atomic():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    repository = SqliteTaskRepository(connection)
    repository.initialize()
    video = repository.upsert_video_asset(VideoAssetRecord(
        canonical_id="classification", platform="bilibili", title="Video", source_url="https://example.com/video",
    ))
    article = repository.upsert_knowledge_document(KnowledgeDocumentRecord(
        source_url="https://example.com/article", canonical_url="https://example.com/article",
        title="Article", content_markdown="Text", plain_text="Text",
    ))
    targets = [{"kind": "video", "id": video.video_id}, {"kind": "article", "id": article.document_id}]
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(task_repository=repository)))
    assert all(item["category"] == "" for item in repository.list_knowledge_content())
    assert classify_knowledge_content(KnowledgeClassifyRequest(items=targets, category="  AI  "), request) == {"updated": 2}
    repository.initialize()
    repository.upsert_knowledge_document(article.model_copy(update={"title": "Updated"}))
    assert {item["category"] for item in repository.list_knowledge_content()} == {"AI"}
    with pytest.raises(HTTPException) as error:
        classify_knowledge_content(KnowledgeClassifyRequest(
            items=[targets[0], {"kind": "article", "id": "missing"}], category="Other",
        ), request)
    assert error.value.status_code == 404
    assert {item["category"] for item in repository.list_knowledge_content()} == {"AI"}
    assert classify_knowledge_content(KnowledgeClassifyRequest(items=targets + targets, category=""), request) == {"updated": 2}
    assert all(item["category"] == "" for item in repository.list_knowledge_content())


@pytest.mark.parametrize("payload", [
    {"items": []},
    {"items": [{"kind": "unknown", "id": "x"}]},
    {"items": [{"kind": "video", "id": "x"}], "category": "x" * 81},
    {"items": [{"kind": "video", "id": "x"}] * 501},
])
def test_classification_rejects_invalid_requests(payload):
    with pytest.raises(ValidationError):
        KnowledgeClassifyRequest.model_validate(payload)
