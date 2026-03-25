"""Credential, session-secret and API-key management stored in the data volume."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path

DATA_DIR = Path(os.getenv("PORSCHE_TOKEN_FILE", "/app/data/token.json")).parent
CREDS_FILE = DATA_DIR / "credentials.json"

_PLACEHOLDER_EMAIL = "your-my-porsche-email@example.com"
_PLACEHOLDER_PASSWORD = "your-password"
_DEFAULT_DASHBOARD_PASSWORD = "porsche"


# ── internal helpers ──────────────────────────────────────────────────────────

def _load() -> dict:
    if CREDS_FILE.exists():
        try:
            return json.loads(CREDS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save(data: dict) -> None:
    try:
        CREDS_FILE.parent.mkdir(parents=True, exist_ok=True)
        CREDS_FILE.write_text(
            json.dumps(data, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except (OSError, PermissionError):
        pass


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# ── session secret ────────────────────────────────────────────────────────────

def get_session_secret() -> str:
    data = _load()
    secret = data.get("session_secret")
    if not secret:
        secret = secrets.token_hex(32)
        data["session_secret"] = secret
        _save(data)
    return secret


# ── API key (for Shelly / programmatic access) ────────────────────────────────

def get_api_key() -> str:
    """Return the stable API key, generating one on first call."""
    data = _load()
    key = data.get("api_key")
    if not key:
        key = secrets.token_urlsafe(32)
        data["api_key"] = key
        _save(data)
    return key


def rotate_api_key() -> str:
    """Generate a new API key and persist it."""
    data = _load()
    key = secrets.token_urlsafe(32)
    data["api_key"] = key
    _save(data)
    return key


# ── Porsche credentials ───────────────────────────────────────────────────────

def get_porsche_credentials() -> tuple[str | None, str | None]:
    env_email = os.getenv("PORSCHE_EMAIL", "")
    env_password = os.getenv("PORSCHE_PASSWORD", "")
    placeholders = {"", _PLACEHOLDER_EMAIL, _PLACEHOLDER_PASSWORD}
    if env_email not in placeholders and env_password not in placeholders:
        return env_email, env_password
    data = _load()
    return data.get("porsche_email") or None, data.get("porsche_password") or None


def save_porsche_credentials(email: str, password: str) -> None:
    data = _load()
    data["porsche_email"] = email.strip()
    data["porsche_password"] = password
    _save(data)


def credentials_configured() -> bool:
    email, password = get_porsche_credentials()
    return bool(email and password)


# ── dashboard password ────────────────────────────────────────────────────────

def check_dashboard_password(password: str) -> bool:
    data = _load()
    stored = data.get("dashboard_password_hash")
    if not stored:
        return _sha256(_DEFAULT_DASHBOARD_PASSWORD) == _sha256(password)
    return stored == _sha256(password)


def save_dashboard_password(password: str) -> None:
    data = _load()
    data["dashboard_password_hash"] = _sha256(password)
    _save(data)


def is_default_password() -> bool:
    data = _load()
    return "dashboard_password_hash" not in data
