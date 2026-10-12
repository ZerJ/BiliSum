from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from video_sum_infra.config import ServiceSettings
from video_sum_service.context import settings_manager
from video_sum_service.routers.system import router
from video_sum_service.site_cookies import import_douyin_cookies, write_site_cookies


@pytest.fixture
def cookie_settings(monkeypatch, tmp_path: Path):
    settings = ServiceSettings(
        data_dir=tmp_path / "data", cache_dir=tmp_path / "cache",
        tasks_dir=tmp_path / "tasks", ytdlp_cookies_file="",
    )
    monkeypatch.setattr(settings_manager, "_settings", settings)
    return settings


def row(domain: str, name: str, value: str, expires: str = "0") -> list[str]:
    return [domain, "TRUE", "/", "TRUE", expires, name, value]


def test_douyin_import_merges_bilibili_and_replaces_only_douyin(cookie_settings):
    path = Path(write_site_cookies([row(".bilibili.com", "SESSDATA", "bili")], "bilibili.com"))
    result = import_douyin_cookies("Cookie: sessionid=douyin; sid_tt=sid=a=b")
    assert Path(result["cookiesFile"]) == path
    assert result["cookieCount"] == 2
    assert "SESSDATA\tbili" in path.read_text(encoding="utf-8")
    assert "sid_tt\tsid=a=b" in path.read_text(encoding="utf-8")
    import_douyin_cookies("sessionid=updated")
    content = path.read_text(encoding="utf-8")
    assert "SESSDATA\tbili" in content and "sessionid\tupdated" in content
    assert "sid_tt" not in content
    write_site_cookies([row(".bilibili.com", "SESSDATA", "new-bili")], "bilibili.com")
    content = path.read_text(encoding="utf-8")
    assert "sessionid\tupdated" in content and "SESSDATA\tnew-bili" in content


def test_import_preserves_configured_file_and_legacy_bilibili(cookie_settings, tmp_path):
    legacy = cookie_settings.data_dir / "cookies" / "bilibili.txt"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("\t".join(row(".bilibili.com", "SESSDATA", "legacy")), encoding="utf-8")
    configured = tmp_path / "configured.txt"
    configured.write_text("\t".join(row(".bilibili.com", "SESSDATA", "configured")), encoding="utf-8")
    cookie_settings.ytdlp_cookies_file = str(configured)
    result = import_douyin_cookies("sessionid=douyin")
    content = Path(result["cookiesFile"]).read_text(encoding="utf-8")
    assert "SESSDATA\tconfigured" in content
    assert configured.read_text(encoding="utf-8").endswith("configured")


def test_netscape_import_accepts_httponly_filters_unrelated_and_expired(cookie_settings):
    text = "\n".join([
        "# Netscape HTTP Cookie File",
        "\t".join(row("#HttpOnly_.douyin.com", "sessionid", "login")),
        "\t".join(row(".douyin.com", "expired", "stale", "1")),
        "\t".join(row(".douyin.com.attacker.test", "sid_tt", "unrelated")),
        "\t".join(row(".douyin.com", "empty-value", "")),
    ])
    result = import_douyin_cookies(text)
    assert result["cookieCount"] == 1
    content = Path(result["cookiesFile"]).read_text(encoding="utf-8")
    assert "#HttpOnly_.douyin.com" in content
    assert "stale" not in content and "unrelated" not in content


@pytest.mark.parametrize("text", [
    "", "uid_tt=user", "invalid", "sessionid=secret\x00injected",
    "\t".join(row(".douyin.com.attacker.test", "sessionid", "fake")),
    "\t".join(row(".douyin.com", "sessionid", "expired", "1")),
    "sessionid=" + "x" * 1024 * 1024,
], ids=["empty", "no-session", "malformed", "control-character", "foreign-domain", "expired-session", "oversized"])
def test_invalid_import_does_not_modify_existing_login(cookie_settings, text):
    path = Path(write_site_cookies([row(".bilibili.com", "SESSDATA", "bili")], "bilibili.com"))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        import_douyin_cookies(text)
    assert path.read_bytes() == before


def test_cookie_import_api_returns_path_and_count_without_credentials(cookie_settings):
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.post("/api/v1/douyin/cookies/import", json={"content": "sessionid=private-login"})
        assert response.status_code == 200
        assert response.json()["cookieCount"] == 1
        assert "private-login" not in response.text
        assert client.post("/api/v1/douyin/cookies/import", json={"content": "uid_tt=user"}).status_code == 400
