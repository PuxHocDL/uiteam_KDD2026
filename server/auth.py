"""Minimal real authentication: file-backed users + signed tokens.

Passwords are never stored in clear — each is salted and stretched with
PBKDF2-HMAC-SHA256. Login issues a stateless HMAC-signed token
(``username.expiry.signature``) that the server can verify without a session
table; logout is simply the client dropping the token. All stdlib (hashlib,
hmac, secrets) so there are no new dependencies.

This is intended for a single-tenant studio deployment, not a public multi-user
service: there is no rate-limiting, email verification, or password reset.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from threading import Lock
from typing import Any

_PBKDF2_ROUNDS = 200_000
_TOKEN_TTL_SECONDS = 7 * 24 * 3600  # a week
_MIN_PASSWORD = 6
_MAX_FIELD = 128


class AuthError(Exception):
    """Raised for any auth failure; carries an HTTP-ish status code."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def normalize_username(username: str) -> str:
    name = (username or "").strip()
    if not name:
        raise AuthError("Username is required.")
    if len(name) > _MAX_FIELD:
        raise AuthError("Username is too long.")
    return name


class AuthStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._users_path = self.root / "users.json"
        self._secret_path = self.root / "secret.key"
        self._lock = Lock()
        self._secret = self._load_or_create_secret()

    # -- secret / persistence ------------------------------------------------
    def _load_or_create_secret(self) -> bytes:
        if self._secret_path.exists():
            return self._secret_path.read_bytes()
        secret = secrets.token_bytes(32)
        self._secret_path.write_bytes(secret)
        return secret

    def _load_users(self) -> dict[str, Any]:
        if not self._users_path.exists():
            return {}
        try:
            return json.loads(self._users_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - corrupt file → treat as empty
            return {}

    def _save_users(self, users: dict[str, Any]) -> None:
        self._users_path.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")

    # -- password hashing ----------------------------------------------------
    @staticmethod
    def _hash(password: str, salt: bytes) -> str:
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
        return _b64(dk)

    # -- public API ----------------------------------------------------------
    def register(self, username: str, password: str) -> dict[str, Any]:
        name = normalize_username(username)
        if not password or len(password) < _MIN_PASSWORD:
            raise AuthError(f"Password must be at least {_MIN_PASSWORD} characters.")
        if len(password) > _MAX_FIELD:
            raise AuthError("Password is too long.")
        with self._lock:
            users = self._load_users()
            if name.lower() in {u.lower() for u in users}:
                raise AuthError("That username is already taken.", status=409)
            salt = secrets.token_bytes(16)
            users[name] = {
                "salt": _b64(salt),
                "hash": self._hash(password, salt),
                "created": int(time.time()),
            }
            self._save_users(users)
        return {"username": name}

    def verify(self, username: str, password: str) -> str:
        name = normalize_username(username)
        users = self._load_users()
        # case-insensitive lookup but return the stored canonical name
        record = None
        canonical = name
        for stored_name, rec in users.items():
            if stored_name.lower() == name.lower():
                record = rec
                canonical = stored_name
                break
        if record is None:
            raise AuthError("Invalid username or password.", status=401)
        salt = _b64decode(record["salt"])
        if not hmac.compare_digest(self._hash(password, salt), record["hash"]):
            raise AuthError("Invalid username or password.", status=401)
        return canonical

    # -- tokens (stateless, HMAC-signed) ------------------------------------
    def issue_token(self, username: str) -> str:
        expiry = int(time.time()) + _TOKEN_TTL_SECONDS
        payload = f"{username}.{expiry}"
        sig = hmac.new(self._secret, payload.encode("utf-8"), hashlib.sha256).digest()
        return f"{_b64(payload.encode('utf-8'))}.{_b64(sig)}"

    def validate_token(self, token: str | None) -> str | None:
        if not token:
            return None
        try:
            payload_b64, sig_b64 = token.split(".")
            payload = _b64decode(payload_b64).decode("utf-8")
            username, expiry_str = payload.rsplit(".", 1)
            expected = hmac.new(self._secret, payload.encode("utf-8"), hashlib.sha256).digest()
            if not hmac.compare_digest(_b64decode(sig_b64), expected):
                return None
            if int(expiry_str) < int(time.time()):
                return None
            return username
        except Exception:  # noqa: BLE001 - any malformed token is just invalid
            return None
