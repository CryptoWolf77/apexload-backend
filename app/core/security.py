from fastapi import Header

from app.core.config import get_settings


async def get_optional_api_key(x_api_key: str | None = Header(default=None)) -> str | None:
    """Read an API key without enforcing it yet.

    TODO: Enforce API key protection for production clients after the Flutter
    app has a secure key exchange / auth flow. For Version 1.2A this stays open
    so the frontend can connect during VPS testing.
    """

    _ = get_settings().api_key
    return x_api_key

