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
    v1.get_projects = AsyncMock(return_value=[])
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


# =============================================================================
# get_auth_status() live snapshot
# =============================================================================


async def _init_cookie_authed_api(patched_clients, **status_side_effect):
    """Helper: bring an API up authenticated via the cookie fallback."""
    v1, v2 = patched_clients
    v2.authenticate.side_effect = TickTickSessionError("need_captcha")

    def _mark_authed(session):
        v2.is_authenticated = True
        v2.verify_authentication = AsyncMock(return_value=True)
    v2.set_session.side_effect = _mark_authed

    api = UnifiedTickTickAPI(
        client_id="cid",
        client_secret="sec",
        v1_access_token="v1tok",
        username="user@example.com",
        password="pw",
        v2_cookies="t=SECRET_T; AWSALB=z",
    )
    await api.initialize()
    return api, v1, v2


async def test_get_auth_status_reports_live_ok_and_cookie_method(patched_clients):
    api, v1, v2 = await _init_cookie_authed_api(patched_clients)

    status = await api.get_auth_status()

    assert status["v1_ok"] is True
    assert status["v2_ok"] is True
    assert status["v2_auth_method"] == "cookie"
    assert v1.get_projects.called  # live V1 ping happened
    # the raw token must never appear anywhere in the status payload
    assert "SECRET_T" not in str(status)


async def test_get_auth_status_detects_session_expired_after_startup(patched_clients):
    api, v1, v2 = await _init_cookie_authed_api(patched_clients)
    # Session was fine at startup, but the live ping now 401s.
    v2.get_user_status = AsyncMock(side_effect=TickTickSessionError("session expired (401)"))

    status = await api.get_auth_status()

    assert status["v1_ok"] is True
    assert status["v2_ok"] is False
    assert "session expired" in (status["v2_error"] or "")


# =============================================================================
# server-side formatting helpers (no secrets, actionable verdict)
# =============================================================================


def test_mask_secret_never_reveals_full_value():
    from ticktick_sdk.server import _mask_secret

    full = "IvMRTShR414qyyCSzUE6h3Kn"
    masked = _mask_secret(full)
    assert masked == "IvMR…h3Kn"
    assert full not in masked
    assert _mask_secret(None) == "(not set)"
    assert _mask_secret("short") == "****"


def test_build_auth_verdict_points_at_cookie_fix_when_v2_down_no_fallback():
    from ticktick_sdk.server import _build_auth_verdict

    verdict = _build_auth_verdict(
        v1_ok=True,
        v2_ok=False,
        v2_auth_method=None,
        v2_cookies_configured=False,
        v2_reason="need_captcha",
        v2_error=None,
        device_id_valid=True,
        device_id_ephemeral=False,
    )
    assert "TICKTICK_V2_COOKIES" in verdict
    assert "DEGRADED" in verdict


def test_build_auth_verdict_flags_invalid_device_id():
    from ticktick_sdk.server import _build_auth_verdict

    verdict = _build_auth_verdict(
        v1_ok=True,
        v2_ok=True,
        v2_auth_method="cookie",
        v2_cookies_configured=True,
        v2_reason=None,
        v2_error=None,
        device_id_valid=False,
        device_id_ephemeral=False,
    )
    assert "DEVICE_ID" in verdict.upper()


def test_auth_error_message_v2_tells_consumer_to_refresh_cookies():
    """A V2 auth failure must explain the fix (refresh cookies) to the MCP user."""
    from ticktick_sdk.api.v2.client import TickTickV2Client

    c = TickTickV2Client(device_id="a" * 24)
    msg = c._auth_error_message("token expired", "/batch/check/0")
    assert "TICKTICK_V2_COOKIES" in msg
    assert "/batch/check/0" in msg
    assert "token expired" in msg  # raw TickTick detail preserved
    assert "redeploy" in msg.lower()


def test_auth_error_message_v1_tells_consumer_to_refresh_access_token():
    """A V1 auth failure must point at the OAuth token refresh."""
    from ticktick_sdk.api.v1.client import TickTickV1Client

    c = TickTickV1Client(
        client_id="x",
        client_secret="y",
        redirect_uri="http://localhost:8080/callback",
        access_token="z",
    )
    msg = c._auth_error_message("unauthorized", "/project")
    assert "TICKTICK_ACCESS_TOKEN" in msg
    assert "ticktick-sdk auth" in msg


async def test_initialize_falls_back_with_cookies_only_extracting_t(patched_clients):
    """Only TICKTICK_V2_COOKIES set (no explicit token) → `t` is auto-extracted."""
    v1, v2 = patched_clients
    v2.authenticate.side_effect = TickTickSessionError("need_captcha")
    captured = {}

    def _mark_authed(session):
        captured["session"] = session
        v2.is_authenticated = True
        v2.verify_authentication = AsyncMock(return_value=True)
    v2.set_session.side_effect = _mark_authed

    api = UnifiedTickTickAPI(
        client_id="cid",
        client_secret="sec",
        v1_access_token="v1tok",
        username="user@example.com",
        password="pw",
        v2_cookies="tt_distid=abc; t=COOKIE_T_VALUE; AWSALB=xyz",
        # no v2_token
    )

    await api.initialize()

    assert api._router.has_v2 is True
    # token was pulled from the `t` cookie
    assert captured["session"].token == "COOKIE_T_VALUE"
    assert captured["session"].cookies["t"] == "COOKIE_T_VALUE"
    assert captured["session"].cookies["tt_distid"] == "abc"


async def test_initialize_cookies_without_t_fails_gracefully(patched_clients):
    """TICKTICK_V2_COOKIES set but missing `t=` and no token → V2 unavailable, V1 still up."""
    v1, v2 = patched_clients
    v2.authenticate.side_effect = TickTickSessionError("need_captcha")

    api = UnifiedTickTickAPI(
        client_id="cid",
        client_secret="sec",
        v1_access_token="v1tok",
        username="user@example.com",
        password="pw",
        v2_cookies="tt_distid=abc; AWSALB=xyz",  # no t=
    )

    await api.initialize()

    assert api._router.has_v1 is True
    assert api._router.has_v2 is False
    assert not v2.set_session.called  # never tried to build a session
    assert "no `t` cookie" in (api._v2_unavailable_reason or "")


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
