"""Shared OAuth refresh and bounded recovery for Vaillant cloud sessions.

Vaillant rotates refresh tokens. A single lock shared by all coordinators
prevents concurrent refreshes from invalidating the Keycloak session.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging

from aiohttp import ClientResponseError
from homeassistant.exceptions import ConfigEntryAuthFailed
from myPyllant.http_client import AuthenticationFailed, RealmInvalid

_LOGGER = logging.getLogger(__name__)
_REFRESH_MARGIN = timedelta(seconds=180)


def is_invalid_grant(error: ClientResponseError) -> bool:
    """Identify a revoked refresh token, not every generic HTTP 400."""
    if error.status not in (400, 401):
        return False
    message = error.message.lower()
    return (
        "invalid_grant" in message
        or "stale token" in message
        or "session doesn't have required client" in message
    )


def classify_api_error(error: BaseException) -> str:
    """Return a safe diagnostic reason without exposing OAuth data."""
    if isinstance(error, (ConfigEntryAuthFailed, AuthenticationFailed)):
        return "authentication"
    if isinstance(error, ClientResponseError):
        if error.status == 429:
            return "rate_limited"
        if error.status == 401 or is_invalid_grant(error):
            return "authentication"
        if error.status == 403:
            return "forbidden_or_quota"
        return "http_error"
    if isinstance(error, TimeoutError):
        return "timeout"
    return "api_unavailable"


class VaillantSessionManager:
    """Own one OAuth lock for the API session shared by all coordinators."""

    def __init__(self, api) -> None:
        self.api = api
        self._lock = asyncio.Lock()

    async def refresh_if_needed(self) -> None:
        """Re-check expiration inside the lock to prevent double refresh."""
        async with self._lock:
            expires = self.api.oauth_session_expires
            if expires is not None and expires > (
                datetime.now(timezone.utc) + _REFRESH_MARGIN
            ):
                return

            try:
                await self.api.refresh_token()
            except ClientResponseError as error:
                if not is_invalid_grant(error):
                    raise

                # The refresh token is irrecoverable. Use a fresh authorization
                # code login as HA reload does. Keep the lock until it finishes.
                _LOGGER.warning(
                    "Vaillant refresh token was rejected; starting a fresh login"
                )
                try:
                    await self.api.login()
                except (AuthenticationFailed, RealmInvalid) as login_error:
                    raise ConfigEntryAuthFailed(
                        "Vaillant account requires reauthentication"
                    ) from login_error
                _LOGGER.info("Vaillant OAuth session recovered with a fresh login")
