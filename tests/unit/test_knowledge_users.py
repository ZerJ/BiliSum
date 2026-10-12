import sqlite3
from fastapi import FastAPI
from fastapi.testclient import TestClient

from video_sum_infra.config import ServiceSettings
from video_sum_service.knowledge.index_service import KnowledgeIndexService
from video_sum_service.repository import SqliteTaskRepository
from video_sum_service.routers.knowledge import router
from video_sum_service.schemas import KnowledgeDocumentRecord, VideoAssetRecord


def create_repository() -> SqliteTaskRepository:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    repository = SqliteTaskRepository(connection)
    repository.initialize()
    return repository


def test_user_content_tags_and_categories_are_isolated_across_restarts() -> None:
    repository = create_repository()
    video = repository.upsert_video_asset(
        VideoAssetRecord(
            canonical_id="user-isolation",
            platform="bilibili",
            title="Shared video",
            source_url="https://example.com/video",
        )
    )
    article = repository.upsert_knowledge_document(
        KnowledgeDocumentRecord(
            source_url="https://example.com/article",
            canonical_url="https://example.com/article",
            title="Shared article",
            content_markdown="# Shared article",
            plain_text="Shared article",
        )
    )
    second = repository.create_knowledge_user("Second")
    second_id = str(second["user_id"])

    assert repository.list_knowledge_content_ids(second_id) == set()
    repository.set_knowledge_content_membership(
        second_id,
        [("video", video.video_id), ("article", article.document_id)],
        True,
    )
    repository.classify_knowledge_content(second_id, [("article", article.document_id)], "Second only")
    repository.add_video_tag(video.video_id, "personal", user_id=second_id)

    assert repository.get_knowledge_document(article.document_id, second_id) is not None
    assert repository.list_all_tags() == []
    assert repository.list_all_tags(second_id)[0]["tag"] == "personal"
    assert next(
        item for item in repository.list_knowledge_content() if item["id"] == article.document_id
    )["category"] == ""

    repository.set_knowledge_content_membership(
        "default", [("article", article.document_id)], False
    )
    repository.initialize()
    assert repository.get_knowledge_document(article.document_id, "default") is None
    assert repository.get_knowledge_document(article.document_id, second_id) is not None
    assert repository.get_knowledge_document_count("default") == 0
    assert repository.get_knowledge_document_count(second_id) == 1


def test_user_conversations_and_jobs_cannot_be_read_by_other_user() -> None:
    repository = create_repository()
    second_id = str(repository.create_knowledge_user("Second")["user_id"])
    conversation = repository.create_knowledge_conversation("Private", second_id)
    conversation_id = str(conversation["conversation_id"])
    message = repository.create_knowledge_message(conversation_id, "assistant", "secret")
    job = repository.create_knowledge_job(
        conversation_id,
        str(message["message_id"]),
        "multi_video_summary",
        "queued",
        "query",
    )

    assert repository.list_knowledge_conversations(user_id="default") == []
    assert repository.get_knowledge_conversation(conversation_id, "default") is None
    assert repository.get_knowledge_job(str(job["job_id"]), "default") is None
    assert repository.update_knowledge_conversation_title(conversation_id, "Changed", "default") is None
    assert repository.delete_knowledge_conversation(conversation_id, "default") is False
    assert repository.get_knowledge_conversation(conversation_id, second_id)["title"] == "Private"

    assert repository.delete_knowledge_user(second_id) is True
    assert repository.get_knowledge_conversation(conversation_id) is None
    assert repository.get_knowledge_job(str(job["job_id"])) is None


class FakeCollection:
    def query(self, *, query_embeddings, n_results, where):
        del query_embeddings, n_results
        self.where = where
        return {
            "ids": [["private-chunk", "shared-chunk"]],
            "documents": [["private", "shared"]],
            "metadatas": [[
                {"source_kind": "article", "source_id": "private", "video_id": ""},
                {"source_kind": "article", "source_id": "shared", "video_id": ""},
            ]],
            "distances": [[0.01, 0.1]],
        }


def test_search_filters_shared_vector_results_before_rag_context() -> None:
    repository = create_repository()
    second_id = str(repository.create_knowledge_user("Second")["user_id"])
    repository.upsert_knowledge_document(
        KnowledgeDocumentRecord(
            document_id="private",
            source_url="https://example.com/private",
            canonical_url="https://example.com/private",
            title="Private",
            content_markdown="private",
            plain_text="private",
        )
    )
    repository.upsert_knowledge_document(
        KnowledgeDocumentRecord(
            document_id="shared",
            source_url="https://example.com/shared",
            canonical_url="https://example.com/shared",
            title="Shared",
            content_markdown="shared",
            plain_text="shared",
        ),
        user_id=second_id,
    )
    collection = FakeCollection()
    service = KnowledgeIndexService(repository, ServiceSettings(), knowledge_user_id=second_id)
    service._embed_texts = lambda _: [[1.0, 0.0]]
    service._get_collection = lambda: collection
    repository.get_knowledge_chunk_count = lambda *args: 1

    chunks = service.search_chunks("anything")

    assert [item["source_id"] for item in chunks] == ["shared"]
    assert collection.where == {
        "$or": [
            {"source_id": {"$in": ["shared"]}},
            {"document_id": {"$in": ["shared"]}},
        ]
    }


def test_user_routes_scope_content_and_conversations() -> None:
    repository = create_repository()
    application = FastAPI()
    application.include_router(router)
    application.state.task_repository = repository
    with TestClient(application) as client:
        created = client.post("/api/v1/knowledge/users", json={"name": "Alex"})
        assert created.status_code == 201
        second_id = created.json()["user_id"]
        assert len(client.get("/api/v1/knowledge/users").json()) == 2
        second_headers = {"X-Knowledge-User-ID": second_id}

        private = client.post(
            "/api/v1/knowledge/conversations",
            json={},
            headers=second_headers,
        )
        conversation_id = private.json()["conversation_id"]
        assert client.get(f"/api/v1/knowledge/conversations/{conversation_id}").status_code == 404
        assert client.get(
            f"/api/v1/knowledge/conversations/{conversation_id}", headers=second_headers
        ).status_code == 200
        assert client.get(
            "/api/v1/knowledge/conversations",
            headers={"X-Knowledge-User-ID": "unknown"},
        ).status_code == 404
