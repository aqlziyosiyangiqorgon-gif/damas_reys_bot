"""Telegram Mini App initData verification helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


class MiniAppAuthError(ValueError):
    """Raised when Telegram Mini App credentials are invalid or stale."""


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    now: int | None = None,
    max_age_seconds: int = 86_400,
) -> dict:
    """Verify Telegram's HMAC and return the authenticated Telegram user."""
    if not init_data or not bot_token or bot_token == "YOUR_BOT_TOKEN_HERE":
        raise MiniAppAuthError("Missing Telegram credentials")

    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    fields: dict[str, str] = {}
    for key, value in pairs:
        if key in fields:
            raise MiniAppAuthError("Duplicate initData field")
        fields[key] = value

    received_hash = fields.pop("hash", "")
    if len(received_hash) != 64:
        raise MiniAppAuthError("Missing initData hash")

    data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise MiniAppAuthError("Invalid initData signature")

    try:
        auth_date = int(fields["auth_date"])
        user = json.loads(fields["user"])
        user_id = int(user["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MiniAppAuthError("Invalid initData user") from exc

    current_time = int(time.time()) if now is None else int(now)
    age = current_time - auth_date
    if age < -60 or age > max_age_seconds:
        raise MiniAppAuthError("Expired initData")
    if user_id <= 0:
        raise MiniAppAuthError("Invalid Telegram user ID")

    return {"id": user_id, "first_name": str(user.get("first_name", ""))[:128]}
