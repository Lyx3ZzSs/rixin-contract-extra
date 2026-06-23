"""Auth dependencies for the /api/v1 router."""

from __future__ import annotations

from fastapi import Header, HTTPException

from app.config import settings


def valid_api_keys() -> set[str]:
    """Configured API keys. Empty set means open mode (no key required)."""
    return {k.strip() for k in settings.app_api_keys.split(",") if k.strip()}


async def verify_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    """Require a valid X-API-Key when keys are configured.

    Open mode (APP_API_KEYS unset) lets dev/tests run without a key; production
    must set APP_API_KEYS to lock the API down.
    """
    keys = valid_api_keys()
    if not keys:
        return  # open mode
    if not x_api_key or x_api_key not in keys:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
