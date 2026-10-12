from __future__ import annotations

import hmac
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from time import monotonic

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

ACCESS_TOKEN_ENV = "VIDEO_SUM_ACCESS_TOKEN"
SESSION_COOKIE_NAME = "bilisum_session"
ACCOUNT_COOKIE_NAME = "bilisum_account"
ACCOUNT_SESSION_DAYS = 14
PASSWORD_ITERATIONS = 600_000
_login_failures: dict[tuple[str, str], list[float]] = {}
_login_lock = Lock()


class AccessTokenManager:
    def __init__(self, data_dir: Path, *, env: dict[str, str] | None = None) -> None:
        self._data_dir = Path(data_dir)
        self._env = env if env is not None else os.environ
        self._token: str | None = None

    @property
    def token_file(self) -> Path:
        return self._data_dir / "auth.json"

    def get_token(self) -> str:
        env_token = str(self._env.get(ACCESS_TOKEN_ENV) or "").strip()
        if env_token:
            return env_token
        if self._token is None:
            self._token = self._load_or_create_token()
        return self._token

    def is_configured_from_env(self) -> bool:
        return bool(str(self._env.get(ACCESS_TOKEN_ENV) or "").strip())

    def verify(self, candidate: str | None) -> bool:
        expected = self.get_token()
        provided = str(candidate or "").strip()
        return bool(provided) and hmac.compare_digest(provided, expected)

    def _load_or_create_token(self) -> str:
        token_path = self.token_file
        try:
            if token_path.exists():
                payload = json.loads(token_path.read_text(encoding="utf-8"))
                token = str(payload.get("access_token") or "").strip()
                if token:
                    return token
        except (OSError, ValueError, TypeError):
            pass

        token = secrets.token_urlsafe(32)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(
            json.dumps(
                {
                    "access_token": token,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return token


def extract_bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return None


def is_auth_exempt_path(path: str, method: str = "GET") -> bool:
    if path in {"/health", "/api/v1/auth/status", "/api/v1/auth/session", "/api/v1/auth/setup", "/api/v1/auth/login", "/api/v1/auth/logout"}:
        return True
    if path == "/" or path.startswith("/static/") or path.startswith("/media/"):
        return True
    if method.upper() == "GET" and path.startswith("/api/v1/tasks/") and "/visual-evidence/media/" in path:
        return True
    if method.upper() == "GET" and (
        path.startswith("/videos")
        or path.startswith("/settings")
        or path.startswith("/knowledge")
    ):
        return True
    return False


def request_is_authorized(request: Request, manager: AccessTokenManager) -> bool:
    repository = getattr(request.app.state, "task_repository", None)
    if repository is not None and repository.accounts_enabled():
        return account_from_request(request) is not None
    bearer = extract_bearer_token(request)
    if manager.verify(bearer):
        return True
    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    return manager.verify(cookie_token)


def unauthorized_response() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": "请登录 BiliSum 账户。"},
        headers={"WWW-Authenticate": "Bearer"},
    )


def set_session_cookie(response: JSONResponse, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="strict",
        secure=False,
        path="/",
        max_age=60 * 60 * 24 * 30,
    )


def require_access_token(request: Request, manager: AccessTokenManager) -> None:
    if not request_is_authorized(request, manager):
        raise HTTPException(status_code=401, detail="访问密钥无效。")


def describe_token_source(manager: AccessTokenManager) -> dict[str, object]:
    return {
        "required": True,
        "configuredFromEnv": manager.is_configured_from_env(),
        "tokenFile": str(manager.token_file),
    }


def hash_password(password: str) -> str:
    if not 8 <= len(password) <= 256:
        raise ValueError("密码长度需为 8 到 256 个字符。")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def login_attempt_allowed(ip: str, name: str) -> bool:
    key = (ip, name.casefold())
    now = monotonic()
    with _login_lock:
        recent = [attempt for attempt in _login_failures.get(key, []) if now - attempt < 300]
        if recent:
            _login_failures[key] = recent
        else:
            _login_failures.pop(key, None)
        return len(recent) < 10


def record_login_attempt(ip: str, name: str, *, succeeded: bool) -> None:
    key = (ip, name.casefold())
    with _login_lock:
        if succeeded:
            _login_failures.pop(key, None)
        else:
            if len(_login_failures) > 1000:
                cutoff = monotonic() - 300
                for old_key in list(_login_failures):
                    if not any(attempt >= cutoff for attempt in _login_failures[old_key]):
                        _login_failures.pop(old_key, None)
            _login_failures.setdefault(key, []).append(monotonic())


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or not 1 <= int(iterations) <= 1_000_000:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(digest, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def session_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def account_from_request(request: Request) -> dict[str, object] | None:
    repository = getattr(request.app.state, "task_repository", None)
    token = request.cookies.get(ACCOUNT_COOKIE_NAME)
    if repository is None or not token:
        return None
    return repository.get_account_session(session_hash(token))


def require_account(request: Request, *, admin: bool = False) -> dict[str, object]:
    account = account_from_request(request)
    if account is None:
        raise HTTPException(status_code=401, detail="请登录 BiliSum 账户。")
    if admin and not account["is_admin"]:
        raise HTTPException(status_code=403, detail="仅管理员可以管理账户。")
    return account


def create_account_session(request: Request, response: JSONResponse, user_id: str) -> None:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=ACCOUNT_SESSION_DAYS)).isoformat()
    request.app.state.task_repository.create_account_session(user_id, session_hash(token), expires)
    response.set_cookie(
        ACCOUNT_COOKIE_NAME, token, httponly=True, samesite="strict",
        secure=request.url.scheme == "https", path="/", max_age=ACCOUNT_SESSION_DAYS * 86400,
    )
