from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qsl

import httpx
import pytest

import video_sum_core.douyin as douyin
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


@pytest.mark.parametrize("cookie_name", ["UIFID", "UIFID_TEMP", "UIFIDTEMP"])
def test_detail_request_passes_imported_uifid_before_signing(
    monkeypatch: pytest.MonkeyPatch, cookie_name: str,
) -> None:
    signed_params: list[str] = []

    def sign(self, params: str):
        signed_params.append(params)
        return (params, "test-signature", "", "")

    monkeypatch.setattr("video_sum_core.douyin.ABogus.generate_abogus", sign)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("uifid") != "browser-device-id":
            return httpx.Response(403, text="Blocked by ArgusSecurityPlugin Uifid Not Found")
        return httpx.Response(200, json=_detail_payload())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        downloader = DouyinDownloader(
            client=client, cookie_header=f"sessionid=test; {cookie_name}=browser-device-id",
        )
        assert downloader.probe("https://www.douyin.com/video/6961737553342991651").aweme_id == "6961737553342991651"
    assert "uifid=browser-device-id" in signed_params[0]


def test_web_signature_matches_browser_sdk_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(douyin, "time", lambda: 1791795817)
    query = (
        "aweme_id=7695291560069483814&uifid=test-device"
        "&a_bogus=a%2Bb%2Fc%3D&empty=&title=%E6%B5%8B%E8%AF%95+video"
    )
    signed, headers = douyin._web_sign_query(query, "test-device")
    assert signed == (
        "aweme_id=7695291560069483814&uifid=test-device"
        "&a_bogus=a%2Bb%2Fc%3D&empty=&title=%E6%B5%8B%E8%AF%95%20video"
        "&timestamp=1791795817&x-secsdk-web-signature=caa33fc595c8c65bd6d9d12cf70a1bb2"
    )
    assert headers == {
        "uifid": "test-device",
        "x-secsdk-web-signature": "caa33fc595c8c65bd6d9d12cf70a1bb2",
        "x-secsdk-web-expire": "1791795817",
    }


def test_probe_generates_fresh_web_signature_for_each_request(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter([1791795817, 1791795818])
    monkeypatch.setattr(douyin, "time", lambda: next(clock))
    signatures: list[str] = []
    timestamps: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(parse_qsl(request.url.query.decode(), keep_blank_values=True))
        if not params.get("x-secsdk-web-signature") or not params.get("timestamp"):
            return httpx.Response(403, text="Blocked by ArgusSecurityPlugin Signature Not Found")
        assert params["uifid"] == request.headers["uifid"] == "browser-id"
        assert params["x-secsdk-web-signature"] == request.headers["x-secsdk-web-signature"]
        assert params["timestamp"] == request.headers["x-secsdk-web-expire"]
        signatures.append(params["x-secsdk-web-signature"])
        timestamps.append(params["timestamp"])
        return httpx.Response(200, json=_detail_payload())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        downloader = DouyinDownloader(client=client, cookie_header="sessionid=test; UIFID=browser-id")
        for _ in range(2):
            assert downloader.probe("https://www.douyin.com/video/6961737553342991651").title == "测试抖音作品"
    assert timestamps == ["1791795817", "1791795818"]
    assert signatures[0] != signatures[1]


@pytest.mark.parametrize("reason, expected", [
    ("Uifid Not Found", "浏览器标识 UIFID"),
    ("Signature Not Found", "浏览器请求签名"),
])
def test_security_plugin_errors_do_not_misreport_cookie_expiry(reason: str, expected: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=f"Blocked by ArgusSecurityPlugin {reason}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        downloader = DouyinDownloader(client=client, cookie_header="sessionid=private-session")
        with pytest.raises(DouyinError) as caught:
            downloader.probe("https://www.douyin.com/video/6961737553342991651")
    assert expected in str(caught.value)
    assert "登录态已失效" not in str(caught.value)
    assert "private-session" not in str(caught.value)


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
