from __future__ import annotations

import ipaddress
import os
import random
import re
import string
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import quote, urlencode, urljoin, urlparse

import httpx

from video_sum_core._vendor.abogus import ABogus, BrowserFingerprintGenerator
from video_sum_core.errors import VideoSumError


DOUYIN_API_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
)
DOUYIN_DETAIL_ENDPOINT = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
DOUYIN_URL_PATTERN = re.compile(
    r"https?://(?:[a-z0-9-]+\.)*(?:douyin\.com|iesdouyin\.com)(?::\d+)?/[^\s<>\"']*",
    re.IGNORECASE,
)
DOUYIN_ID_PATTERN = re.compile(r"/(?:share/)?(?:video|note)/(\d{15,22})(?:[/?#]|$)", re.IGNORECASE)
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_DOUYIN_HOST_SUFFIXES = ("douyin.com", "iesdouyin.com")
_MEDIA_HOST_SUFFIXES = (
    "douyin.com",
    "iesdouyin.com",
    "douyinvod.com",
    "douyinpic.com",
    "bytecdn.cn",
    "bytevcloud.com",
    "byteimg.com",
    "amemv.com",
    "snssdk.com",
    "pstatp.com",
)


class DouyinError(VideoSumError):
    """Raised when Douyin link resolution, detail lookup, or download fails."""


@dataclass(frozen=True, slots=True)
class DouyinVideoVariant:
    width: int
    height: int
    bitrate: int
    fps: int
    codec: str
    urls: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DouyinMetadata:
    aweme_id: str
    title: str
    author: str
    cover_url: str
    duration: float | None
    source_url: str
    variants: tuple[DouyinVideoVariant, ...]
    audio_urls: tuple[str, ...]

    def as_probe_dict(self) -> dict[str, object]:
        return {
            "id": self.aweme_id,
            "title": self.title,
            "uploader": self.author,
            "thumbnail": self.cover_url,
            "duration": self.duration,
            "webpage_url": self.source_url,
            "extractor_key": "Douyin",
        }


def _number(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _address_urls(value: object) -> list[str]:
    urls: list[str] = []
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            urls.append(value)
    elif isinstance(value, list):
        for child in value:
            urls.extend(_address_urls(child))
    elif isinstance(value, dict):
        for key in ("url_list", "urlList", "main_url", "backup_url", "fallback_url", "src", "url"):
            if key in value:
                urls.extend(_address_urls(value[key]))
    return list(dict.fromkeys(urls))


def _codec_name(rate: dict[str, object]) -> str:
    codec = str(rate.get("codec_type") or rate.get("codecType") or "").lower()
    if rate.get("is_h265") or rate.get("isH265") or codec in {"h265", "hevc", "bytevc1"}:
        return "h265"
    return "h264"


def extract_video_variants(video: dict[str, object]) -> list[DouyinVideoVariant]:
    rates = video.get("bit_rate") or video.get("bitRateList") or []
    variants: dict[tuple[int, int, int, str], DouyinVideoVariant] = {}
    if isinstance(rates, list):
        for raw_rate in rates:
            if not isinstance(raw_rate, dict):
                continue
            play = raw_rate.get("play_addr") or raw_rate.get("playAddr") or {}
            if not isinstance(play, dict):
                continue
            urls = tuple(
                url for url in _address_urls(play)
                if "media-audio" not in url and "ies-music" not in url
            )
            if not urls:
                continue
            width = _number(play.get("width") or raw_rate.get("width") or video.get("width"))
            height = _number(play.get("height") or raw_rate.get("height") or video.get("height"))
            bitrate = _number(raw_rate.get("bit_rate") or raw_rate.get("bitRate"))
            fps = _number(raw_rate.get("FPS") or raw_rate.get("fps"))
            codec = _codec_name(raw_rate)
            variant = DouyinVideoVariant(width, height, bitrate, fps, codec, urls)
            key = (width, height, fps, codec)
            previous = variants.get(key)
            if previous is None or bitrate > previous.bitrate:
                variants[key] = variant

    if not variants:
        fallback = tuple(
            url for url in _address_urls(video.get("play_addr") or video.get("playAddr"))
            if "media-audio" not in url and "ies-music" not in url
        )
        if fallback:
            variants[(0, 0, 0, "h264")] = DouyinVideoVariant(
                _number(video.get("width")),
                _number(video.get("height")),
                0,
                0,
                "h264",
                fallback,
            )
    return list(variants.values())


def extract_audio_urls(video: dict[str, object]) -> tuple[str, ...]:
    rates = video.get("bit_rate_audio") or video.get("bitRateAudio") or []
    choices: list[tuple[int, tuple[str, ...]]] = []
    if isinstance(rates, list):
        for raw_rate in rates:
            if not isinstance(raw_rate, dict):
                continue
            meta = raw_rate.get("audio_meta") or raw_rate.get("audioMeta") or raw_rate
            if not isinstance(meta, dict):
                continue
            urls = tuple(_address_urls(meta.get("url_list") or meta.get("urlList") or meta))
            if urls:
                choices.append((_number(meta.get("bitrate") or raw_rate.get("bit_rate")), urls))
    return max(choices, key=lambda item: item[0])[1] if choices else ()


def select_video_variant(
    variants: Iterable[DouyinVideoVariant],
    *,
    max_height: int | None = None,
) -> DouyinVideoVariant:
    choices = list(variants)
    if not choices:
        raise DouyinError("抖音详情中没有可下载的视频地址。")
    if max_height:
        capped = [item for item in choices if min(item.width, item.height) <= max_height]
        if capped:
            choices = capped
    return max(
        choices,
        key=lambda item: (
            item.width * item.height,
            item.codec == "h264",
            item.fps,
            item.bitrate,
        ),
    )


def _host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    normalized = host.rstrip(".").lower()
    try:
        ipaddress.ip_address(normalized)
        return False
    except ValueError:
        pass
    return any(normalized == suffix or normalized.endswith(f".{suffix}") for suffix in suffixes)


def _validated_url(url: str, suffixes: tuple[str, ...], *, label: str) -> str:
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError as exc:
        raise DouyinError(f"{label}地址格式无效。") from exc
    if parsed.scheme not in {"http", "https"} or not _host_matches(host, suffixes):
        raise DouyinError(f"检测到不安全的{label}跳转，已停止请求。")
    if port not in {None, 80, 443}:
        raise DouyinError(f"检测到不安全的{label}端口，已停止请求。")
    return url


def _extract_shared_url(value: str) -> str:
    match = DOUYIN_URL_PATTERN.search(value or "")
    if not match:
        raise DouyinError("没有找到有效的抖音作品链接。")
    return match.group(0).rstrip(".,;:!?)]}>，。；：！？）】》")


def _api_params(item_id: str) -> dict[str, object]:
    token_chars = string.ascii_letters + string.digits + "-_"
    return {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "pc_client_type": 1,
        "publish_video_strategy_type": 2,
        "pc_libra_divert": "Windows",
        "version_code": "290100",
        "version_name": "29.1.0",
        "cookie_enabled": "true",
        "screen_width": 1920,
        "screen_height": 1080,
        "browser_language": "zh-CN",
        "browser_platform": "Win32",
        "browser_name": "Edge",
        "browser_version": "130.0.0.0",
        "browser_online": "true",
        "engine_name": "Blink",
        "engine_version": "130.0.0.0",
        "os_name": "Windows",
        "os_version": "10",
        "cpu_core_num": 12,
        "device_memory": 8,
        "platform": "PC",
        "downlink": 10,
        "effective_type": "4g",
        "round_trip_time": 100,
        "msToken": "".join(random.choice(token_chars) for _ in range(184)),
        "aweme_id": item_id,
    }


def _read_cookie_file(path: Path | None) -> dict[str, str]:
    cookies: dict[str, str] = {}
    if path is None or not path.exists() or not path.is_file():
        return cookies
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise DouyinError(f"无法读取 Cookies 文件：{path}") from exc
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7 and _host_matches(parts[0].lstrip("."), _DOUYIN_HOST_SUFFIXES):
            name, value = parts[5].strip(), parts[6].strip()
            if name and value:
                cookies[name] = value
    return cookies


def _parse_cookie_header(value: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for pair in (value or "").split(";"):
        name, separator, cookie_value = pair.strip().partition("=")
        if separator and name and cookie_value:
            cookies[name] = cookie_value
    return cookies


class DouyinDownloader:
    def __init__(
        self,
        *,
        cookie_file: str | Path | None = None,
        cookie_header: str | None = None,
        client: httpx.Client | None = None,
        ffmpeg_path: str | Path | None = None,
        timeout: float = 30.0,
    ) -> None:
        configured_path = Path(cookie_file).expanduser() if cookie_file else None
        cookies = _read_cookie_file(configured_path)
        env_cookie = os.environ.get("VIDEO_SUM_DOUYIN_COOKIE") or os.environ.get("DOUYIN_COOKIE") or ""
        cookies.update(_parse_cookie_header(env_cookie))
        cookies.update(_parse_cookie_header(cookie_header or ""))
        self.cookie_header = "; ".join(f"{name}={value}" for name, value in cookies.items())
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=False)
        self._owns_client = client is None
        self._ffmpeg_path = Path(ffmpeg_path) if ffmpeg_path else None
        self._timeout = timeout

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "DouyinDownloader":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def resolve(self, value: str) -> tuple[str, str]:
        current = _validated_url(_extract_shared_url(value), _DOUYIN_HOST_SUFFIXES, label="抖音")
        for _ in range(6):
            match = DOUYIN_ID_PATTERN.search(current)
            if match:
                item_id = match.group(1)
                return item_id, f"https://www.douyin.com/video/{item_id}"
            try:
                response = self._client.get(
                    current,
                    headers={"User-Agent": DOUYIN_API_USER_AGENT},
                    follow_redirects=False,
                    timeout=self._timeout,
                )
            except httpx.HTTPError as exc:
                raise DouyinError(f"抖音短链解析失败：{exc}") from exc
            if response.status_code not in _REDIRECT_STATUSES:
                break
            location = response.headers.get("Location")
            if not location:
                break
            current = _validated_url(urljoin(current, location), _DOUYIN_HOST_SUFFIXES, label="抖音")
        raise DouyinError("抖音短链已打开，但没有识别到作品 ID。")

    def probe(self, value: str) -> DouyinMetadata:
        if not self.cookie_header:
            raise DouyinError(
                "抖音详情接口需要登录态。请在设置的视频获取中点击“抖音登录”，"
                "或填写包含 .douyin.com 的 cookies.txt，"
                "或设置 VIDEO_SUM_DOUYIN_COOKIE 后重试。"
            )
        item_id, source_url = self.resolve(value)
        detail = self._fetch_detail(item_id)
        video = detail.get("video") or {}
        if not isinstance(video, dict):
            raise DouyinError("当前抖音作品不是可下载的单条视频。")
        variants = tuple(extract_video_variants(video))
        if not variants:
            raise DouyinError("抖音详情中没有可下载的视频地址。")
        author_value = detail.get("author") or {}
        author = author_value if isinstance(author_value, dict) else {}
        title = str(detail.get("desc") or detail.get("description") or "").strip()
        nickname = str(author.get("nickname") or author.get("name") or "").strip()
        if not title:
            title = f"{nickname}的抖音作品" if nickname else f"抖音作品 {item_id}"
        cover_url = next(iter(_address_urls(
            video.get("cover") or video.get("origin_cover") or video.get("originCover")
        )), "")
        duration_ms = _number(detail.get("duration") or video.get("duration"))
        duration = duration_ms / 1000 if duration_ms else None
        return DouyinMetadata(
            aweme_id=str(detail.get("aweme_id") or detail.get("awemeId") or item_id),
            title=title,
            author=nickname,
            cover_url=cover_url,
            duration=duration,
            source_url=source_url,
            variants=variants,
            audio_urls=extract_audio_urls(video),
        )

    def _fetch_detail(self, item_id: str) -> dict[str, object]:
        params_values = _api_params(item_id)
        uifid = _parse_cookie_header(self.cookie_header).get("UIFID")
        if uifid:
            params_values["uifid"] = uifid
        params_text = urlencode(params_values)
        fingerprint = BrowserFingerprintGenerator.generate_fingerprint("Edge")
        signature = ABogus(fp=fingerprint, user_agent=DOUYIN_API_USER_AGENT).generate_abogus(params_text)[1]
        params = f"{params_text}&a_bogus={quote(signature, safe='')}"
        headers = {
            "User-Agent": DOUYIN_API_USER_AGENT,
            "Referer": f"https://www.douyin.com/video/{item_id}",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Cookie": self.cookie_header,
            "sec-ch-ua": '"Chromium";v="130", "Microsoft Edge";v="130", "Not?A_Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        }
        try:
            response = self._client.get(
                f"{DOUYIN_DETAIL_ENDPOINT}?{params}",
                headers=headers,
                follow_redirects=False,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise DouyinError(f"抖音详情请求失败：{exc}") from exc
        if response.status_code == 403:
            reason = response.text.lower()
            if "uifid not found" in reason:
                raise DouyinError(
                    "抖音详情请求缺少浏览器标识 UIFID（HTTP 403）。"
                    "请从已登录的抖音作品页导出完整 Cookies。"
                )
            if "signature not found" in reason:
                raise DouyinError(
                    "抖音详情请求缺少浏览器请求签名（HTTP 403）。"
                    "Cookies 已发送，但当前接口请求仍未被接受；重复导入相同 Cookies 无法解决。"
                )
            raise DouyinError(
                "抖音拒绝了视频详情请求（HTTP 403）。可能是接口校验或访问限制，"
                "此响应不能确认 Cookies 是否过期。"
            )
        if response.status_code == 401:
            raise DouyinError(
                "抖音详情请求未授权（HTTP 401），请重新登录后导入完整 Cookies。"
            )
        try:
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise DouyinError(f"抖音详情接口返回异常（HTTP {response.status_code}）。") from exc
        detail = payload.get("aweme_detail") if isinstance(payload, dict) else None
        if not isinstance(detail, dict) or payload.get("status_code") not in {0, None}:
            raise DouyinError("抖音详情接口没有返回作品信息，请更新登录态后重试。")
        return detail

    def download_video(
        self,
        value: str,
        target: Path,
        *,
        max_height: int | None = None,
        progress: Callable[[int, int | None], None] | None = None,
    ) -> Path:
        metadata = self.probe(value)
        selected = select_video_variant(metadata.variants, max_height=max_height)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not metadata.audio_urls:
            self._download_candidates(selected.urls, target, progress=progress)
            return target

        video_track = target.with_name(f"{target.stem}.video.mp4")
        audio_track = target.with_name(f"{target.stem}.audio.m4a")
        try:
            self._download_candidates(selected.urls, video_track, progress=progress)
            self._download_candidates(metadata.audio_urls, audio_track)
            self._merge_tracks(video_track, audio_track, target)
        finally:
            video_track.unlink(missing_ok=True)
            audio_track.unlink(missing_ok=True)
        return target

    def _download_candidates(
        self,
        urls: Iterable[str],
        target: Path,
        *,
        progress: Callable[[int, int | None], None] | None = None,
    ) -> None:
        failures: list[str] = []
        for url in urls:
            try:
                self._download_one(url, target, progress=progress)
                return
            except (DouyinError, httpx.HTTPError, OSError) as exc:
                target.unlink(missing_ok=True)
                failures.append(str(exc))
        detail = failures[-1] if failures else "没有可用的 CDN 地址"
        raise DouyinError(f"抖音媒体下载失败：{detail}")

    def _download_one(
        self,
        url: str,
        target: Path,
        *,
        progress: Callable[[int, int | None], None] | None,
    ) -> None:
        current = _validated_url(url, _MEDIA_HOST_SUFFIXES, label="媒体")
        for _ in range(6):
            with self._client.stream(
                "GET",
                current,
                headers={"User-Agent": DOUYIN_API_USER_AGENT, "Referer": "https://www.douyin.com/"},
                follow_redirects=False,
                timeout=self._timeout,
            ) as response:
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("Location")
                    if not location:
                        raise DouyinError("媒体 CDN 返回了无目标跳转。")
                    current = _validated_url(urljoin(current, location), _MEDIA_HOST_SUFFIXES, label="媒体")
                    continue
                response.raise_for_status()
                total = _number(response.headers.get("Content-Length")) or None
                downloaded = 0
                with target.open("wb") as output:
                    for chunk in response.iter_bytes(chunk_size=256 * 1024):
                        if not chunk:
                            continue
                        output.write(chunk)
                        downloaded += len(chunk)
                        if progress:
                            progress(downloaded, total)
                if downloaded <= 0:
                    raise DouyinError("媒体 CDN 返回了空文件。")
                return
        raise DouyinError("媒体 CDN 跳转次数过多。")

    def _merge_tracks(self, video_track: Path, audio_track: Path, target: Path) -> None:
        ffmpeg = self._ffmpeg_path
        if ffmpeg is None:
            from video_sum_infra.runtime import ffmpeg_location

            ffmpeg = ffmpeg_location()
        if ffmpeg is None:
            raise DouyinError("该抖音清晰度使用音视频分轨，当前环境缺少 FFmpeg，无法合并。")
        result = subprocess.run(
            [
                str(ffmpeg), "-y", "-i", str(video_track), "-i", str(audio_track),
                "-c:v", "copy", "-c:a", "copy", "-movflags", "+faststart", str(target),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15 * 60,
            check=False,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
        if result.returncode != 0 or not target.exists():
            target.unlink(missing_ok=True)
            raise DouyinError(f"抖音 DASH 音视频合并失败：{result.stderr.strip()[-500:]}")
