"""
Tests for graceful V2 degradation in UnifiedTickTickAPI.initialize().

When V2 password sign-on fails (e.g. TickTick returns need_captcha from
its anti-bot system), the server must NOT crash. It should keep V1
running, record a clear (verbatim) failure reason, and optionally fall
back to a pre-obtained session token from env vars.

These tests pin that behavior so the crash-loop regression can't sneak
back in.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ticktick_sdk.exceptions import (
    TickTickConfigurationError,
    TickTickRateLimitError,
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
    """Cookie-first: token+cookie env vars bring V2 up (password never needed)."""
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
    """Stale cookie AND password sign-on fails → V2 stays unavailable, server still starts on V1."""
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
    # Cookie-first: a stale (401) cookie falls through to password sign-on, and
    # the combined reason names both failures.
    reason = api._v2_unavailable_reason or ""
    assert "cookie fallback failed" in reason
    assert "password sign-on failed" in reason


async def test_cookie_is_tried_before_password_and_password_is_skipped(patched_clients):
    """Cookie-first: when the cookie works, /user/signon is never called."""
    v1, v2 = patched_clients
    # If password sign-on were attempted it would "succeed" here — prove it
    # isn't called at all because the cookie path wins first.
    v2.authenticate = AsyncMock(side_effect=AssertionError("password must not be tried"))

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
        v2_cookies="t=GOOD_TOKEN; AWSALB=z",
    )

    await api.initialize()

    assert api._router.has_v2 is True
    assert api._v2_auth_method == "cookie"
    v2.authenticate.assert_not_called()  # password sign-on skipped entirely


async def test_cookie_rate_limited_does_not_trigger_password_signon(patched_clients):
    """A 429 verifying the cookie must NOT fall through to /user/signon.

    Otherwise we pour more failed logins onto the throttle that's already
    blocking us. V2 stays degraded with a rate-limit reason, password untouched.
    """
    from ticktick_sdk.exceptions import TickTickRateLimitError

    v1, v2 = patched_clients
    v2.authenticate = AsyncMock(side_effect=AssertionError("password must not be tried"))
    v2.get_user_status.side_effect = TickTickRateLimitError(
        "Rate limit exceeded", details={"api_version": "v2", "endpoint": "/user/status"}
    )

    # set_session marks the client authed; clear_session (called when the 429
    # fails verification) must flip it back, like the real session handler does.
    def _mark_authed(_session):
        v2.is_authenticated = True
    v2.set_session.side_effect = _mark_authed

    def _clear():
        v2.is_authenticated = False
    v2._session_handler.clear_session.side_effect = _clear

    api = UnifiedTickTickAPI(
        client_id="cid",
        client_secret="sec",
        v1_access_token="v1tok",
        username="user@example.com",
        password="pw",
        v2_cookies="t=MAYBE_FINE; AWSALB=z",
    )

    await api.initialize()

    assert api._router.has_v1 is True
    assert api._router.has_v2 is False
    v2.authenticate.assert_not_called()  # the whole point: no signon fuel
    reason = (api._v2_unavailable_reason or "").lower()
    assert "rate-limited" in reason or "429" in reason


# =============================================================================
# ensure_v2_fresh() — backoff-gated on-demand re-auth (recovery without redeploy)
# =============================================================================


def _degraded_cookie_api(patched_clients, status_side_effect):
    """Build (not yet initialized) an api whose cookie verification fails per
    `status_side_effect`. set_session marks authed; clear_session unmarks it."""
    v1, v2 = patched_clients
    v2.get_user_status.side_effect = status_side_effect

    def _mark_authed(_session):
        v2.is_authenticated = True
    v2.set_session.side_effect = _mark_authed

    def _clear():
        v2.is_authenticated = False
    v2._session_handler.clear_session.side_effect = _clear

    api = UnifiedTickTickAPI(
        client_id="cid",
        client_secret="sec",
        v1_access_token="v1tok",
        username="user@example.com",
        password="pw",
        v2_cookies="t=COOKIE",
    )
    return api, v1, v2


async def test_ensure_v2_fresh_is_noop_when_healthy(patched_clients):
    """When V2 is up, ensure_v2_fresh must not re-authenticate or ping."""
    api, v1, v2 = await _init_cookie_authed_api(patched_clients)
    assert api._router.has_v2 is True
    v2.set_session.reset_mock()
    v2.get_user_status.reset_mock()

    await api.ensure_v2_fresh()

    v2.set_session.assert_not_called()
    v2.get_user_status.assert_not_called()


async def test_ensure_v2_fresh_respects_backoff(patched_clients):
    """A second attempt within the backoff window must not hit TickTick again."""
    api, v1, v2 = _degraded_cookie_api(
        patched_clients, TickTickRateLimitError("Rate limit exceeded")
    )
    await api.initialize()
    assert api._router.has_v2 is False
    calls_after_init = v2.get_user_status.call_count  # the one init attempt

    await api.ensure_v2_fresh()  # immediately after → inside backoff → skip

    assert v2.get_user_status.call_count == calls_after_init
    assert api._router.has_v2 is False


async def test_ensure_v2_fresh_recovers_when_throttle_clears(patched_clients):
    """Once the backoff elapses and the throttle is gone, V2 comes back, no redeploy."""
    # First /user/status 429 (init, degraded); second succeeds (throttle cleared).
    api, v1, v2 = _degraded_cookie_api(
        patched_clients,
        [
            TickTickRateLimitError("Rate limit exceeded"),
            {"inboxId": "inbox123", "userId": "u1"},
        ],
    )
    await api.initialize()
    assert api._router.has_v2 is False

    # Simulate the backoff window having elapsed since the init attempt.
    api._last_v2_reauth = datetime(2000, 1, 1, tzinfo=timezone.utc)

    await api.ensure_v2_fresh()

    assert api._router.has_v2 is True
    assert api._v2_auth_method == "cookie"
    assert api.inbox_id == "inbox123"


async def test_ensure_v2_fresh_never_signs_on_with_password(patched_clients):
    """The health tick is COOKIE-ONLY. With no cookie configured, a degraded
    password-only api must NOT re-attempt /user/signon on ensure_v2_fresh()
    (that would hammer the anti-bot endpoint). Password sign-on is boot-only."""
    v1, v2 = patched_clients
    # Password sign-on fails at init (anti-bot), so V2 starts degraded.
    v2.authenticate.side_effect = TickTickSessionError(
        "Authentication failed: need_captcha", details={"errorCode": "need_captcha"}
    )
    api = UnifiedTickTickAPI(
        client_id="cid",
        client_secret="sec",
        v1_access_token="v1tok",
        username="user@example.com",
        password="pw",
        # deliberately no v2_cookies — password is the only V2 path
    )
    await api.initialize()
    assert api._router.has_v2 is False
    assert v2.authenticate.call_count == 1  # exactly the one boot attempt

    # Pretend the backoff elapsed so ensure_v2_fresh runs its body, not skips.
    api._last_v2_reauth = datetime(2000, 1, 1, tzinfo=timezone.utc)
    await api.ensure_v2_fresh()

    # The health tick must NOT have signed on again.
    assert v2.authenticate.call_count == 1
    assert api._router.has_v2 is False


# =============================================================================
# server: client is built once per process, not per MCP session
# =============================================================================


async def test_server_builds_client_once_per_process(monkeypatch):
    """_get_or_create_client reuses one client across calls (the per-session
    lifespan must NOT re-build/re-auth on every connection)."""
    import ticktick_sdk.server as server

    built = {"count": 0}

    def _fake_from_settings(settings):
        built["count"] += 1
        c = MagicMock()
        c.is_connected = False

        async def _connect():
            c.is_connected = True

        c.connect = AsyncMock(side_effect=_connect)
        c.ensure_v2_fresh = AsyncMock()
        return c

    fake_settings = MagicMock()
    fake_settings.device_id_looks_valid = True
    fake_settings.device_id_is_ephemeral = False

    monkeypatch.setattr(server, "_shared_client", None)
    monkeypatch.setattr(server, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(
        server.TickTickClient, "from_settings", staticmethod(_fake_from_settings)
    )

    c1 = await server._get_or_create_client()
    c2 = await server._get_or_create_client()

    assert c1 is c2
    assert built["count"] == 1  # built + connected only once
    c1.connect.assert_awaited_once()


# =============================================================================
# V2 browser-impersonation transport (curl_cffi)
# =============================================================================


async def test_v2_send_http_uses_impersonation_when_enabled():
    """When impersonation is on, _send_http routes through the curl_cffi session,
    builds an absolute URL, passes the profile, and drops our User-Agent so the
    profile's matching UA is used. Keeps Cookie/X-Device."""
    from ticktick_sdk.api.v2.client import TickTickV2Client

    calls = {}

    class _FakeResp:
        status_code = 200
        content = b'{"ok": true}'
        text = '{"ok": true}'
        headers: dict = {}

        def json(self):
            return {"ok": True}

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, **kwargs):
            calls.update(method=method, url=url, **kwargs)
            return _FakeResp()

    c = TickTickV2Client(device_id="a" * 24)
    c._impersonate = "chrome"
    c._curl_session_cls = _FakeSession

    resp = await c._send_http(
        "GET",
        "/user/status",
        None,
        None,
        {"User-Agent": "Firefox", "Cookie": "t=x", "X-Device": "{}"},
    )

    assert resp.status_code == 200
    assert calls["impersonate"] == "chrome"
    assert calls["url"].endswith("/api/v2/user/status")
    sent_headers = calls["headers"]
    assert all(k.lower() != "user-agent" for k in sent_headers)  # UA stripped
    assert sent_headers["Cookie"] == "t=x"
    assert sent_headers["X-Device"] == "{}"


async def test_v2_send_http_falls_back_to_httpx_when_impersonation_off(monkeypatch):
    """With impersonation disabled, _send_http defers to the base (httpx) path."""
    from ticktick_sdk.api.v2.client import TickTickV2Client
    from ticktick_sdk.api import base as base_mod

    c = TickTickV2Client(device_id="a" * 24)
    c._impersonate = ""  # disabled

    called = {}

    async def _fake_base_send(self, method, endpoint, params, json_data, headers):
        called["used_base"] = True
        return MagicMock(status_code=200)

    monkeypatch.setattr(base_mod.BaseTickTickClient, "_send_http", _fake_base_send)
    await c._send_http("GET", "/user/status", None, None, {})
    assert called.get("used_base") is True


async def test_signon_post_uses_impersonation_when_enabled():
    """Sign-on (SessionHandler) also routes through curl_cffi when enabled, and
    drops our User-Agent so the profile's UA is used."""
    from ticktick_sdk.api.v2.auth import SessionHandler

    calls = {}

    class _FakeResp:
        status_code = 200

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, **kwargs):
            calls.update(method=method, url=url, **kwargs)
            return _FakeResp()

    h = SessionHandler(device_id="a" * 24)
    h._impersonate = "chrome"
    h._curl_session_cls = _FakeSession

    resp = await h._post(
        "https://api.ticktick.com/api/v2/user/signon",
        {"wc": "true"},
        {"username": "u", "password": "p"},
        {"User-Agent": "Firefox", "X-Device": "{}"},
    )

    assert resp.status_code == 200
    assert calls["method"] == "POST"
    assert calls["impersonate"] == "chrome"
    assert all(k.lower() != "user-agent" for k in calls["headers"])
    assert calls["headers"]["X-Device"] == "{}"


def test_extract_cookies_handles_httpx_and_curl_shapes():
    from ticktick_sdk.api.v2.auth import SessionHandler

    # curl_cffi style: .cookies is dict-like (no .jar)
    class _CurlResp:
        cookies = {"t": "TOK", "AWSALB": "x"}

    assert SessionHandler._extract_cookies(_CurlResp())["t"] == "TOK"

    # httpx style: .cookies.jar yields objects with .name/.value
    class _C:
        def __init__(self, n, v):
            self.name, self.value = n, v

    class _Jar:
        def __iter__(self):
            return iter([_C("t", "TOK2")])

    class _Cookies:
        jar = _Jar()

    class _HttpxResp:
        cookies = _Cookies()

    assert SessionHandler._extract_cookies(_HttpxResp())["t"] == "TOK2"
