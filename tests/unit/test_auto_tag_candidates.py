import sqlite3

from video_sum_infra.config import ServiceSettings
from video_sum_service.knowledge.tag_service import TagService
from video_sum_service.repository import SqliteTaskRepository
from video_sum_service.schemas import VideoAssetRecord


def test_batch_auto_tag_skips_video_without_summary():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    repository = SqliteTaskRepository(connection)
    repository.initialize()
    repository.upsert_video_asset(VideoAssetRecord(
        canonical_id="no-summary", platform="bilibili", title="尚未总结",
        source_url="https://example.com/video",
    ))
    assert TagService(repository, ServiceSettings()).batch_auto_tag().items == []


def test_explicit_empty_batch_does_not_process_all_videos():
    class Repository:
        def list_untagged_video_ids(self):
            raise AssertionError("Empty selection must not select the entire library")

    assert TagService(Repository(), ServiceSettings()).batch_auto_tag([]).items == []
