from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from video_sum_core.douyin import (
    DouyinDownloader,
    DouyinError,
    extract_video_variants,
    select_video_variant,
)


def _detail_payload() -> dict[str, object]:
    return {
        "status_code": 0,
        "aweme_detail": {
            "aweme_id": "6961737553342991651",
            "desc": "测试抖音作品",
            "duration": 12_345,
            "author": {"nickname": "测试作者"},
            "video": {
                "cover": {"url_list": ["https://p3.douyinpic.com/cover.jpeg"]},
                "bit_rate": [
                    {
                        "bit_rate": 3_000_000,
                        "is_h265": True,
                        "FPS": 60,
                        "play_addr": {
                            "width": 1080,
                            "height": 1920,
                            "url_list": ["https://v3.douyinvod.com/hevc.mp4"],
                        },
                    },
                    {
                        "bit_rate": 2_000_000,
                        "FPS": 30,
                        "play_addr": {
                            "width": 1080,
                            "height": 1920,
                            "url_list": ["https://v3.douyinvod.com/h264.mp4"],
                        },
                    },
                ],
                "bit_rate_audio": [
                    {
                        "audio_meta": {
                            "bitrate": 128_000,
                            "url_list": ["https://v3.douyinvod.com/audio.m4a"],
                        }
                    }
                ],
            },
        },
    }


def test_detail_request_is_signed_and_normalized() -> None:
    seen_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_request
        seen_request = request
        return httpx.Response(200, json=_detail_payload())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        downloader = DouyinDownloader(client=client, cookie_header="sessionid=test-session")
        metadata = downloader.probe("https://www.douyin.com/video/6961737553342991651")

    assert metadata.aweme_id == "6961737553342991651"
    assert metadata.title == "测试抖音作品"
    assert metadata.duration == pytest.approx(12.345)
    assert metadata.cover_url == "https://p3.douyinpic.com/cover.jpeg"
    assert seen_request is not None
    assert seen_request.url.path == "/aweme/v1/web/aweme/detail/"
    assert seen_request.url.params.get("a_bogus")
    assert "sessionid=test-session" in seen_request.headers["cookie"]


def test_quality_selection_prefers_h264_at_same_resolution() -> None:
    detail = _detail_payload()["aweme_detail"]
    assert isinstance(detail, dict)
    video = detail["video"]
    assert isinstance(video, dict)

    variants = extract_video_variants(video)
    selected = select_video_variant(variants)

    assert selected.codec == "h264"
    assert selected.urls == ("https://v3.douyinvod.com/h264.mp4",)


def test_missing_douyin_cookie_has_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIDEO_SUM_DOUYIN_COOKIE", raising=False)
    monkeypatch.delenv("DOUYIN_COOKIE", raising=False)
    downloader = DouyinDownloader(client=httpx.Client(transport=httpx.MockTransport(lambda _: None)))

    with pytest.raises(DouyinError, match="VIDEO_SUM_DOUYIN_COOKIE"):
        downloader.probe("https://www.douyin.com/video/6961737553342991651")

    downloader.close()


def test_short_link_rejects_redirect_outside_douyin() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        downloader = DouyinDownloader(client=client, cookie_header="sessionid=test")
        with pytest.raises(DouyinError, match="不安全"):
            downloader.resolve("https://v.douyin.com/AbCdEfG/")


def test_reads_douyin_cookie_from_netscape_file(tmp_path: Path) -> None:
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n"
        ".douyin.com\tTRUE\t/\tTRUE\t0\tsessionid\tfile-session\n"
        ".bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tignored\n",
        encoding="utf-8",
    )
    downloader = DouyinDownloader(
        client=httpx.Client(transport=httpx.MockTransport(lambda _: None)),
        cookie_file=cookie_file,
    )

    assert downloader.cookie_header == "sessionid=file-session"
    downloader.close()


def test_download_uses_backup_cdn_url(tmp_path: Path) -> None:
    payload = _detail_payload()
    detail = payload["aweme_detail"]
    assert isinstance(detail, dict)
    video = detail["video"]
    assert isinstance(video, dict)
    video.pop("bit_rate_audio")
    rates = video["bit_rate"]
    assert isinstance(rates, list)
    h264 = rates[1]
    assert isinstance(h264, dict)
    play_addr = h264["play_addr"]
    assert isinstance(play_addr, dict)
    play_addr["url_list"] = [
        "https://v3.douyinvod.com/unavailable.mp4",
        "https://v6.douyinvod.com/working.mp4",
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/aweme/v1/web/aweme/detail/":
            return httpx.Response(200, json=payload)
        if request.url.host == "v3.douyinvod.com":
            return httpx.Response(503)
        return httpx.Response(200, content=b"downloaded-video")

    target = tmp_path / "douyin.mp4"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        downloader = DouyinDownloader(client=client, cookie_header="sessionid=test")
        result = downloader.download_video(
            "https://www.douyin.com/video/6961737553342991651",
            target,
        )

    assert result == target
    assert target.read_bytes() == b"downloaded-video"
