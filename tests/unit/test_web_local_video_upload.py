import asyncio
import sqlite3

import pytest
from fastapi import HTTPException
from video_sum_core.models.tasks import InputType, TaskInput
from video_sum_infra.config import ServiceSettings
from video_sum_service.app import app
from video_sum_service.repository import SqliteTaskRepository
from video_sum_service.routers import videos as video_router
from video_sum_service.schemas import VideoAssetRecord


class _FakeStreamRequest:
    def __init__(self, repository: SqliteTaskRepository, chunks: list[bytes]) -> None:
        self.app = app
        self.app.state.task_repository = repository
        self._chunks = chunks

    async def stream(self):
        for chunk in self._chunks:
            yield chunk
        yield b""


def create_repository() -> SqliteTaskRepository:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    repository = SqliteTaskRepository(connection)
    repository.initialize()
    return repository


def test_upload_local_video_uses_uploaded_file_name(monkeypatch, tmp_path) -> None:
    repository = create_repository()
    monkeypatch.setattr(video_router, "LOCAL_MEDIA_UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(video_router, "localize_video_cover", lambda task_store, asset: asset)
    monkeypatch.setattr(
        video_router,
        "probe_local_video_asset",
        lambda file_path, **kwargs: VideoAssetRecord(
            canonical_id=kwargs["canonical_id_override"],
            platform="local",
            title=kwargs["title_override"],
            source_url=str(file_path),
            cover_url="/media/covers/local-upload-demo.jpg",
            duration=12.0,
        ),
    )
    request = _FakeStreamRequest(repository, [b"video-bytes"])

    response = asyncio.run(video_router.upload_local_video(request, filename="web-demo.mp4"))

    assert response.cached is False
    assert response.video.platform == "local"
    assert response.video.title == "web-demo"
    assert response.video.source_url.endswith(".mp4")


def test_get_local_video_media_returns_file_response(tmp_path) -> None:
    repository = create_repository()
    local_file = tmp_path / "playable.mp4"
    local_file.write_bytes(b"video-bytes")
    asset = repository.upsert_video_asset(
        VideoAssetRecord(
            canonical_id="local-playable",
            platform="local",
            title="可播放视频",
            source_url=str(local_file),
            cover_url="/media/covers/local-playable.jpg",
            duration=6.0,
        )
    )
    app.state.task_repository = repository

    response = video_router.get_local_video_media(asset.video_id, type("Request", (), {"app": app})())

    assert str(response.path).endswith("playable.mp4")
    assert response.media_type == "video/mp4"


def test_get_douyin_video_media_returns_cached_task_source(monkeypatch, tmp_path) -> None:
    repository = create_repository()
    source_url = "https://www.douyin.com/video/6961737553342991651"
    asset = repository.upsert_video_asset(
        VideoAssetRecord(
            canonical_id="6961737553342991651",
            platform="douyin",
            title="可定位的抖音视频",
            source_url=source_url,
            duration=12.0,
        )
    )
    task = repository.create_task(
        TaskInput(input_type=InputType.URL, source=source_url, title=asset.title),
        video_id=asset.video_id,
    )
    tasks_dir = tmp_path / "tasks"
    cached_video = tasks_dir / task.task_id / "source.douyin.mp4"
    cached_video.parent.mkdir(parents=True)
    cached_video.write_bytes(b"video-bytes")
    monkeypatch.setattr(
        video_router.settings_manager,
        "_settings",
        ServiceSettings(
            data_dir=tmp_path / "data",
            cache_dir=tmp_path / "cache",
            tasks_dir=tasks_dir,
        ),
    )
    app.state.task_repository = repository

    request = type("Request", (), {"app": app})()
    response = video_router.get_local_video_media(asset.video_id, request)

    assert response.path == cached_video
    assert response.media_type == "video/mp4"


def test_get_douyin_video_media_requires_cached_task_source(monkeypatch, tmp_path) -> None:
    repository = create_repository()
    source_url = "https://www.douyin.com/video/6961737553342991651"
    asset = repository.upsert_video_asset(
        VideoAssetRecord(
            canonical_id="6961737553342991651",
            platform="douyin",
            title="尚未缓存的抖音视频",
            source_url=source_url,
            duration=12.0,
        )
    )
    monkeypatch.setattr(
        video_router.settings_manager,
        "_settings",
        ServiceSettings(
            data_dir=tmp_path / "data",
            cache_dir=tmp_path / "cache",
            tasks_dir=tmp_path / "tasks",
        ),
    )
    app.state.task_repository = repository

    with pytest.raises(HTTPException) as exc_info:
        video_router.get_local_video_media(asset.video_id, type("Request", (), {"app": app})())

    assert exc_info.value.status_code == 404
    assert "缓存尚未生成" in exc_info.value.detail
