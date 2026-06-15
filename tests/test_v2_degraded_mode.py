"""
Tests for graceful V2 degradation in UnifiedTickTickAPI.initialize().

When V2 password sign-on fails (e.g. TickTick returns need_captcha from
its anti-bot system), the server must NOT crash — it should keep V1
running, surface a clear log message with a cooldown timestamp, and
optionally fall back to a pre-obtained session token from env vars.

These tests pin that behavior so the crash-loop regression can't sneak
back in.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ticktick_sdk.exceptions import (
    TickTickConfigurationError,
    TickTickSessionError,
)
from ticktick_sdk.unified.api import UnifiedTickTickAPI, _parse_cookie_header


pytestmark = [pytest.mark.unit]


# =============================================================================
# Cookie header parser
# =============================================================================


class TestParseCookieHeader:
    def test_empty(self):
        assert _parse_cookie_header("") == {}

    def test_single(self):
        assert _parse_cookie_header("t=abc") == {"t": "abc"}

    def test_multiple(self):
        result = _parse_cookie_header("t=abc; AWSALB=xyz; foo=bar")
        assert result == {"t": "abc", "AWSALB": "xyz", "foo": "bar"}

    def test_extra_whitespace_and_trailing_semicolon(self):
        result = _parse_cookie_header("  t = abc ;   AWSALB=xyz ;")
        assert result == {"t": "abc", "AWSALB": "xyz"}

    def test_bare_name_skipped(self):
        result = _parse_cookie_header("t=abc; bogus; AWSALB=xyz")
        assert result == {"t": "abc", "AWSALB": "xyz"}


# =============================================================================
# initialize() degraded-mode behavior
# =============================================================================


def _make_v1_client_mock(authenticated: bool = True, verify_ok: bool = True) -> MagicMock:
    v1 = MagicMock()
    v1.is_authenticated = authenticated
    v1.verify_authentication = AsyncMock(return_value=verify_ok)
    v1.close = AsyncMock()
    return v1


def _make_v2_client_mock(authenticated: bool = False) -> MagicMock:
    v2 = MagicMock()
    v2.is_authenticated = authenticated
    v2.authenticate = AsyncMock()
    v2.verify_authentication = AsyncMock(return_value=authenticated)
    v2.set_session = MagicMock()
    v2.get_user_status = AsyncMock(return_value={"inboxId": "inbox123", "userId": "u1"})
    v2._session_handler = MagicMock()
    v2._session_handler.clear_session = MagicMock()
    v2.close = AsyncMock()
    return v2


@pytest.fixture
def patched_clients():
    """Patch the V1 and V2 client constructors to return mocks we control."""
    v1 = _make_v1_client_mock()
    v2 = _make_v2_client_mock()

    with patch("ticktick_sdk.unified.api.TickTickV1Client", return_value=v1), patch(
        "ticktick_sdk.unified.api.TickTickV2Client", return_value=v2
    ):
        yield v1, v2


async def test_initialize_succeeds_when_v2_password_fails_and_v1_works(patched_clients):
    """V2 captcha-walled at sign-on → server still starts in V1-only mode."""
    v1, v2 = patched_clients
    v2.authenticate.side_effect = TickTickSessionError(
        "Authentication failed: need_captcha",
        details={"errorCode": "need_captcha"},
    )

    api = UnifiedTickTickAPI(
        client_id="cid",
        client_secret="sec",
        v1_access_token="v1tok",
        username="user@example.com",
        password="pw",
    )

    await api.initialize()  # MUST NOT RAISE

    assert api._initialized is True
    assert api._router.has_v1 is True
    assert api._router.has_v2 is False
    assert api._v2_unavailable_reason is not None
    assert api._v2_unavailable_until is not None


async def test_initialize_raises_only_when_both_apis_dead(patched_clients):
    """V1 unauth AND V2 sign-on fails → raise (server has nothing to do)."""
    v1, v2 = patched_clients
    v1.is_authenticated = False
    v1.verify_authentication = AsyncMock(return_value=False)
    v2.authenticate.side_effect = TickTickSessionError("need_captcha")

    api = UnifiedTickTickAPI(
        client_id="cid",
        client_secret="sec",
        v1_access_token=None,
        username="user@example.com",
        password="pw",
    )

    with pytest.raises(TickTickConfigurationError):
        await api.initialize()


async def test_initialize_falls_back_to_session_token_when_password_fails(patched_clients):
    """Password sign-on fails → token+cookie env vars → V2 recovers."""
    v1, v2 = patched_clients
    v2.authenticate.side_effect = TickTickSessionError("need_captcha")
    # After set_session is called, treat the V2 client as authenticated so
    # the router sees has_v2 = True.
    def _mark_authed(_session):
        v2.is_authenticated = True
        v2.verify_authentication = AsyncMock(return_value=True)
    v2.set_session.side_effect = _mark_authed

    api = UnifiedTickTickAPI(
        client_id="cid",
        client_secret="sec",
        v1_access_token="v1tok",
        username="user@example.com",
        password="pw",
        v2_token="THE_TOKEN",
        v2_cookies="t=THE_TOKEN; AWSALB=xyz",
    )

    await api.initialize()

    assert v2.set_session.called
    assert v2.get_user_status.called  # used to verify + populate inbox_id
    assert api._router.has_v2 is True
    assert api._v2_unavailable_reason is None
    assert api.inbox_id == "inbox123"


async def test_initialize_token_fallback_failure_keeps_v2_unavailable(patched_clients):
    """Password fails AND token fallback fails → V2 stays unavailable, server still starts on V1."""
    v1, v2 = patched_clients
    v2.authenticate.side_effect = TickTickSessionError("need_captcha")
    v2.get_user_status.side_effect = TickTickSessionError("token stale (401)")
    def _mark_authed(_session):
        v2.is_authenticated = True
    v2.set_session.side_effect = _mark_authed

    api = UnifiedTickTickAPI(
        client_id="cid",
        client_secret="sec",
        v1_access_token="v1tok",
        username="user@example.com",
        password="pw",
        v2_token="STALE",
        v2_cookies="t=STALE",
    )

    await api.initialize()

    assert api._router.has_v1 is True
    assert v2._session_handler.clear_session.called
    assert "token fallback failed" in (api._v2_unavailable_reason or "")
