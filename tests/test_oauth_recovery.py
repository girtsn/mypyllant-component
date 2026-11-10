"""Regression coverage for rotating Vaillant OAuth sessions."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aiohttp import ClientResponseError
from homeassistant.exceptions import ConfigEntryAuthFailed
from myPyllant.http_client import AuthenticationFailed
import pytest

from custom_components.mypyllant.auth import (
    VaillantSessionManager,
    classify_api_error,
    is_invalid_grant,
)
from custom_components.mypyllant.connectivity_diagnostics import CloudHealth
from custom_components.mypyllant.const import DOMAIN
from custom_components.mypyllant.coordinator import (
    DailyDataCoordinator,
    SystemCoordinator,
)


def _future():
    return datetime.now(timezone.utc) + timedelta(hours=1)


def _past():
    return datetime.now(timezone.utc) - timedelta(minutes=1)


def _http_error(status, message):
    return ClientResponseError(
        request_info=MagicMock(real_url="https://identity.example.invalid/token"),
        history=(),
        status=status,
        message=message,
    )


def _api():
    return SimpleNamespace(
        username="test@example.invalid",
        oauth_session_expires=_past(),
        refresh_token=AsyncMock(),
        login=AsyncMock(),
    )


async def test_parallel_coordinators_refresh_only_once(hass):
    api = _api()
    entered = asyncio.Event()
    unblock = asyncio.Event()

    async def refresh():
        entered.set()
        await unblock.wait()
        api.oauth_session_expires = _future()

    api.refresh_token.side_effect = refresh
    entry = SimpleNamespace(entry_id="vaillant-test")
    hass.data[DOMAIN] = {entry.entry_id: {}}
    system = SystemCoordinator(hass, api, entry, update_interval=None)
    energy = DailyDataCoordinator(hass, api, entry, update_interval=None)

    first = asyncio.create_task(system._refresh_session())
    await entered.wait()
    second = asyncio.create_task(energy._refresh_session())
    unblock.set()
    await asyncio.gather(first, second)

    api.refresh_token.assert_awaited_once()
    api.login.assert_not_awaited()
    assert hass.data[DOMAIN][entry.entry_id]["oauth_session_manager"].api is api


async def test_stale_token_triggers_one_fresh_login():
    api = _api()

    async def login():
        api.oauth_session_expires = _future()

    api.refresh_token.side_effect = _http_error(
        400,
        'Bad Request, response was: {"error":"invalid_grant",'
        '"error_description":"Stale token"}',
    )
    api.login.side_effect = login
    manager = VaillantSessionManager(api)
    await asyncio.gather(
        manager.refresh_if_needed(),
        manager.refresh_if_needed(),
        manager.refresh_if_needed(),
    )
    api.refresh_token.assert_awaited_once()
    api.login.assert_awaited_once()


async def test_invalid_session_with_rejected_credentials_requests_reauth():
    api = _api()
    api.refresh_token.side_effect = _http_error(
        400,
        '{"error":"invalid_grant",'
        '"error_description":"Session doesn\'t have required client"}',
    )
    api.login.side_effect = AuthenticationFailed("Invalid credentials")
    manager = VaillantSessionManager(api)
    with pytest.raises(ConfigEntryAuthFailed):
        await manager.refresh_if_needed()
    api.login.assert_awaited_once()


async def test_transient_refresh_failure_does_not_trigger_login():
    api = _api()
    api.refresh_token.side_effect = _http_error(503, "Service unavailable")
    with pytest.raises(ClientResponseError):
        await VaillantSessionManager(api).refresh_if_needed()
    api.login.assert_not_awaited()


def test_authentication_classification_and_cloud_sensor_reason(hass):
    invalid = _http_error(
        400, '{"error":"invalid_grant","error_description":"Stale token"}'
    )
    assert is_invalid_grant(invalid)
    assert classify_api_error(invalid) == "authentication"
    assert classify_api_error(_http_error(429, "Too Many Requests")) == "rate_limited"
    assert classify_api_error(_http_error(403, "Forbidden")) == "forbidden_or_quota"
    assert classify_api_error(_http_error(500, "Server Error")) == "http_error"
    assert not is_invalid_grant(_http_error(400, "Malformed request"))

    system = SimpleNamespace(
        last_update_success=False, last_error_reason="authentication"
    )
    energy = SimpleNamespace(last_update_success=False)
    health = CloudHealth(hass, system, energy)
    assert not health.cloud_connected
    assert health.failure_reason == "authentication"
    system.last_update_success = True
    assert health.failure_reason == "none"
