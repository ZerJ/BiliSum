import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from video_sum_service.app import require_api_access_token
from video_sum_service.auth import (
    ACCESS_TOKEN_ENV, ACCOUNT_COOKIE_NAME, AccessTokenManager,
    login_attempt_allowed, record_login_attempt,
)
from video_sum_service.repository import SqliteTaskRepository
from video_sum_service.routers import system
from video_sum_service.routers.knowledge import router as knowledge_router


def test_account_setup_login_authorization_and_revocation(monkeypatch, tmp_path: Path) -> None:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    repository = SqliteTaskRepository(connection)
    repository.initialize()
    old_user = repository.create_knowledge_user("Legacy")
    old_conversation = repository.create_knowledge_conversation("Legacy private", str(old_user["user_id"]))
    manager = AccessTokenManager(tmp_path, env={ACCESS_TOKEN_ENV: "setup-key"})
    monkeypatch.setattr(system, "access_token_manager", manager)

    app = FastAPI()
    app.state.task_repository = repository
    app.middleware("http")(require_api_access_token)
    app.include_router(system.router)
    app.include_router(knowledge_router)

    with TestClient(app) as admin, TestClient(app) as member, TestClient(app) as stranger:
        assert admin.get("/api/v1/auth/status").json()["setupRequired"] is True
        assert stranger.post("/api/v1/auth/session", headers={"Authorization": "Bearer setup-key"}).status_code == 200
        assert admin.post("/api/v1/auth/setup", json={"name": "Owner", "password": "password-123"}).status_code == 401
        setup = admin.post(
            "/api/v1/auth/setup", json={"name": "Owner", "password": "password-123"},
            headers={"Authorization": "Bearer setup-key"},
        )
        assert setup.status_code == 200
        assert setup.json()["account"]["user_id"] == "default"
        assert setup.cookies.get(ACCOUNT_COOKIE_NAME)
        assert admin.post("/api/v1/auth/setup", json={"name": "Other", "password": "password-123"},
                          headers={"Authorization": "Bearer setup-key"}).status_code == 409
        assert stranger.get("/api/v1/knowledge/users", headers={"Authorization": "Bearer setup-key"}).status_code == 401
        assert stranger.get("/api/v1/knowledge/conversations").status_code == 401
        assert stranger.post("/api/v1/auth/session", headers={"Authorization": "Bearer setup-key"}).status_code == 403
        assert stranger.get("/api/v1/auth/status", headers={"Authorization": "Bearer setup-key"}).json()["authenticated"] is False

        assert admin.get("/api/v1/knowledge/users").status_code == 200
        assert admin.put(
            f"/api/v1/knowledge/users/{old_user['user_id']}/credentials", json={"password": "legacy-pass"}
        ).status_code == 200
        assert member.post("/api/v1/auth/login", json={"name": "Legacy", "password": "wrong"}).status_code == 401
        assert member.post("/api/v1/auth/login", json={"name": "Legacy", "password": "legacy-pass"}).status_code == 200
        repository.initialize()
        assert member.get("/api/v1/auth/status").json()["account"]["name"] == "Legacy"
        assert member.get("/api/v1/knowledge/users").status_code == 403
        assert member.get(f"/api/v1/knowledge/conversations/{old_conversation['conversation_id']}").status_code == 200
        assert admin.get(f"/api/v1/knowledge/conversations/{old_conversation['conversation_id']}").status_code == 404
        assert member.get("/api/v1/knowledge/conversations", headers={"X-Knowledge-User-ID": "default"}).status_code == 403
        assert member.get("/api/v1/knowledge/conversations", headers={"Authorization": "Bearer setup-key"}).status_code == 200
        assert member.put("/api/v1/settings", json={}).status_code == 403
        assert member.get("/api/v1/system/logs").status_code == 403
        assert member.post("/api/v1/bilibili/cookies/qrcode").status_code == 403
        assert member.post("/api/v1/douyin/cookies/import", json={"content": "sessionid=test"}).status_code == 403
        assert stranger.post("/api/v1/douyin/cookies/import", json={"content": "sessionid=test"}).status_code == 401
        assert member.post("/api/v1/knowledge/conversations", json={}, headers={"Origin": "http://attacker.invalid"}).status_code == 403

        created = admin.post("/api/v1/knowledge/users", json={"name": "New", "password": "new-pass-123"})
        assert created.status_code == 201
        assert admin.delete(f"/api/v1/knowledge/users/{created.json()['user_id']}").status_code == 200
        assert stranger.post("/api/v1/auth/login", json={"name": "New", "password": "new-pass-123"}).status_code == 401
        assert member.post("/api/v1/knowledge/users", json={"name": "Denied", "password": "password-123"}).status_code == 403
        assert member.post("/api/v1/auth/password", json={"current_password": "legacy-pass", "new_password": "changed-123"}).status_code == 200
        assert member.get("/api/v1/knowledge/conversations").status_code == 401
        assert member.post("/api/v1/auth/login", json={"name": "Legacy", "password": "changed-123"}).status_code == 200
        assert admin.put(f"/api/v1/knowledge/users/{old_user['user_id']}/credentials", json={"password": "reset-pass-123"}).status_code == 200
        assert member.get("/api/v1/knowledge/conversations").status_code == 401
        assert member.post("/api/v1/auth/login", json={"name": "Legacy", "password": "reset-pass-123"}).status_code == 200
        assert member.post("/api/v1/auth/logout").status_code == 200
        assert member.get("/api/v1/knowledge/conversations").status_code == 401
        assert admin.post("/api/v1/auth/logout").status_code == 200
        assert admin.get("/api/v1/knowledge/users").status_code == 401

    connection.close()


def test_login_attempts_are_limited_per_client_and_name() -> None:
    ip, name = "192.0.2.123", "rate-limit-example"
    for _ in range(10):
        assert login_attempt_allowed(ip, name)
        record_login_attempt(ip, name, succeeded=False)
    assert not login_attempt_allowed(ip, name)
    assert login_attempt_allowed(ip, "another-user")
    record_login_attempt(ip, name, succeeded=True)
    assert login_attempt_allowed(ip, name)
