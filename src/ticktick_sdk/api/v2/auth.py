"""
Session-Based Authentication Handler for TickTick V2 API.

This module implements username/password authentication for TickTick's
unofficial V2 API.

Authentication Flow:
    1. POST /api/v2/user/signon with credentials
    2. Receive token and session cookies
    3. Use token in Authorization header and cookies in subsequent requests

Based on working implementation from pyticktick:
https://github.com/pretzelm/pyticktick
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

# Same browser-fingerprint impersonation as the V2 client (see api/v2/client.py):
# sign-on is the most anti-bot-scrutinized V2 endpoint, so route it through
# curl_cffi too when available. Optional, falls back to httpx.
try:
    from curl_cffi.requests import AsyncSession as _CurlAsyncSession
except Exception:  # pragma: no cover - optional dependency
    _CurlAsyncSession = None

from ticktick_sdk.constants import (
    DEFAULT_TIMEOUT,
    V2_DEVICE_VERSION,
    V2_USER_AGENT,
    get_api_base_v2,
)
from ticktick_sdk.exceptions import TickTickSessionError

logger = logging.getLogger(__name__)


def _generate_object_id() -> str:
    """Generate a MongoDB-style ObjectId (24 hex characters).

    Format: 4-byte timestamp + 5-byte random + 3-byte counter
    This mimics bson.ObjectId() without requiring the bson dependency.
    """
    # 4 bytes: timestamp (seconds since epoch)
    timestamp = int(time.time()).to_bytes(4, "big")
    # 5 bytes: random value (machine id + process id equivalent)
    random_bytes = os.urandom(5)
    # 3 bytes: counter (random for simplicity)
    counter = os.urandom(3)

    return (timestamp + random_bytes + counter).hex()


@dataclass
class SessionToken:
    """V2 API session token data."""

    token: str
    user_id: str
    username: str
    inbox_id: str
    user_code: str | None = None
    is_pro: bool = False
    pro_start_date: str | None = None
    pro_end_date: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Cookies captured from the response
    cookies: dict[str, str] = field(default_factory=dict)

    @property
    def authorization_header(self) -> str:
        """Get the Authorization header value."""
        return f"Bearer {self.token}"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "token": self.token,
            "user_id": self.user_id,
            "username": self.username,
            "inbox_id": self.inbox_id,
            "user_code": self.user_code,
            "is_pro": self.is_pro,
            "pro_start_date": self.pro_start_date,
            "pro_end_date": self.pro_end_date,
            "created_at": self.created_at.isoformat(),
            "cookies": self.cookies,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionToken:
        """Create from dictionary."""
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        elif created_at is None:
            created_at = datetime.now(timezone.utc)

        return cls(
            token=data["token"],
            user_id=data["user_id"],
            username=data["username"],
            inbox_id=data["inbox_id"],
            user_code=data.get("user_code"),
            is_pro=data.get("is_pro", False),
            pro_start_date=data.get("pro_start_date"),
            pro_end_date=data.get("pro_end_date"),
            created_at=created_at,
            cookies=data.get("cookies", {}),
        )


class SessionHandler:
    """
    Handles session-based authentication for TickTick V2 API.

    Usage:
        handler = SessionHandler(device_id="unique_id")

        # Authenticate
        session = await handler.authenticate("user@example.com", "password")

        # Use session
        access_token = session.token
        cookies = session.cookies
    """

    def __init__(
        self,
        device_id: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        # Use proper ObjectId format (24 hex chars) like pyticktick does
        self.device_id = device_id or _generate_object_id()
        self.timeout = timeout

        self._session: SessionToken | None = None

        # Browser-impersonation transport (see module note). Profile from
        # TICKTICK_V2_IMPERSONATE (default "chrome"); "off"/"none" or a missing
        # curl_cffi falls back to httpx.
        profile = os.getenv("TICKTICK_V2_IMPERSONATE", "chrome").strip()
        if profile.lower() in ("", "off", "none", "false", "0") or _CurlAsyncSession is None:
            self._impersonate = ""
        else:
            self._impersonate = profile
        self._curl_session_cls = _CurlAsyncSession

    @property
    def session(self) -> SessionToken | None:
        """Get the current session."""
        return self._session

    @session.setter
    def session(self, value: SessionToken | None) -> None:
        """Set the current session."""
        self._session = value

    @property
    def is_authenticated(self) -> bool:
        """Check if we have a valid session."""
        return self._session is not None

    @property
    def token(self) -> str | None:
        """Get the session token if available."""
        if self._session:
            return self._session.token
        return None

    @property
    def inbox_id(self) -> str | None:
        """Get the inbox ID if available."""
        if self._session:
            return self._session.inbox_id
        return None

    def _get_x_device_header(self) -> str:
        """Get the x-device header JSON string.

        Uses minimal format that works (based on pyticktick).
        Only 3 fields: platform, version, id
        """
        import json

        return json.dumps({
            "platform": "web",
            "version": V2_DEVICE_VERSION,
            "id": self.device_id,
        })

    def _get_headers(self) -> dict[str, str]:
        """Get headers for authentication requests.

        Minimal headers - don't over-engineer with browser-exact headers.
        The API just needs User-Agent and X-Device.
        Content-Type is added automatically by httpx when using json=.
        """
        return {
            "User-Agent": V2_USER_AGENT,
            "X-Device": self._get_x_device_header(),
        }

    async def _post(
        self,
        url: str,
        params: dict[str, Any] | None,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> Any:
        """POST to a V2 auth endpoint, via curl_cffi impersonation if enabled.

        Returns the raw response (httpx or curl_cffi). Raises
        TickTickSessionError on a transport-level failure.
        """
        if self._impersonate and self._curl_session_cls is not None:
            # Drop our User-Agent so the impersonation profile's matching UA wins.
            send_headers = {
                k: v for k, v in headers.items() if k.lower() != "user-agent"
            }
            try:
                async with self._curl_session_cls() as session:
                    return await session.request(
                        "POST",
                        url,
                        params=params,
                        json=payload,
                        headers=send_headers,
                        impersonate=self._impersonate,
                        timeout=self.timeout,
                    )
            except Exception as e:
                raise TickTickSessionError(
                    f"Authentication request failed: {e}",
                ) from e

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                return await client.post(
                    url, params=params, json=payload, headers=headers
                )
            except httpx.RequestError as e:
                raise TickTickSessionError(
                    f"Authentication request failed: {e}",
                ) from e

    @staticmethod
    def _extract_cookies(response: Any) -> dict[str, str]:
        """Pull cookies out of an httpx OR curl_cffi response."""
        cookies: dict[str, str] = {}
        raw = getattr(response, "cookies", None)
        if raw is None:
            return cookies
        jar = getattr(raw, "jar", None)
        if jar is not None:  # httpx
            for cookie in jar:
                cookies[cookie.name] = cookie.value
            return cookies
        try:  # curl_cffi cookies are dict-like
            for k, v in raw.items():
                cookies[k] = v
        except Exception:
            pass
        return cookies

    async def authenticate(
        self,
        username: str,
        password: str,
    ) -> SessionToken:
        """
        Authenticate with username and password.

        Args:
            username: TickTick account username/email
            password: TickTick account password

        Returns:
            SessionToken with authentication credentials

        Raises:
            TickTickSessionError: If authentication fails
        """
        url = f"{get_api_base_v2()}/user/signon"
        params = {"wc": "true", "remember": "true"}
        payload = {"username": username, "password": password}
        headers = self._get_headers()

        logger.debug("Authenticating user: %s", username)

        response = await self._post(url, params, payload, headers)

        if not (200 <= response.status_code < 300):
            self._handle_auth_error(response)

        data = response.json()

        # Check for 2FA requirement
        if "authId" in data and "token" not in data:
            raise TickTickSessionError(
                "Two-factor authentication required",
                requires_2fa=True,
                auth_id=data.get("authId"),
                details={"expire_time": data.get("expireTime")},
            )

        # Extract cookies (handles both httpx and curl_cffi responses)
        cookies = self._extract_cookies(response)

        # Also add the token as a cookie (required by V2 API)
        if "t" not in cookies and "token" in data:
            cookies["t"] = data["token"]

        self._session = SessionToken(
            token=data["token"],
            user_id=str(data.get("userId", "")),
            username=data.get("username", username),
            inbox_id=data.get("inboxId", ""),
            user_code=data.get("userCode"),
            is_pro=data.get("pro", False),
            pro_start_date=data.get("proStartDate"),
            pro_end_date=data.get("proEndDate"),
            cookies=cookies,
        )

        logger.info("Successfully authenticated user: %s", username)
        return self._session

    async def authenticate_2fa(
        self,
        auth_id: str,
        totp_code: str,
    ) -> SessionToken:
        """
        Complete 2FA authentication.

        Scaffolding for the planned 2FA support (see TODO.md) — not yet wired
        into the sign-on flow. Holds the reverse-engineered MFA endpoint +
        payload so a future TOTP path doesn't have to rederive them.

        Args:
            auth_id: The authId from the initial sign-on response
            totp_code: The TOTP code from the authenticator app

        Returns:
            SessionToken with authentication credentials

        Raises:
            TickTickSessionError: If 2FA verification fails
        """
        url = f"{get_api_base_v2()}/user/sign/mfa/code/verify"
        payload = {
            "code": totp_code,
            "method": "app",
        }
        headers = self._get_headers()
        headers["x-verify-id"] = auth_id

        logger.debug("Completing 2FA authentication")

        response = await self._post(url, None, payload, headers)

        if not (200 <= response.status_code < 300):
            self._handle_auth_error(response)

        data = response.json()

        # Extract cookies (handles both httpx and curl_cffi responses)
        cookies = self._extract_cookies(response)

        if "t" not in cookies and "token" in data:
            cookies["t"] = data["token"]

        self._session = SessionToken(
            token=data["token"],
            user_id=str(data.get("userId", "")),
            username=data.get("username", ""),
            inbox_id=data.get("inboxId", ""),
            user_code=data.get("userCode"),
            is_pro=data.get("pro", False),
            pro_start_date=data.get("proStartDate"),
            pro_end_date=data.get("proEndDate"),
            cookies=cookies,
        )

        logger.info("Successfully completed 2FA authentication")
        return self._session

    def set_session(self, session: SessionToken) -> None:
        """Set an existing session directly."""
        self._session = session

    def clear_session(self) -> None:
        """Clear the current session."""
        self._session = None

    def _handle_auth_error(self, response: httpx.Response) -> None:
        """Handle error response from auth endpoints."""
        try:
            error_data = response.json()
            error_message = error_data.get("message", response.text)
        except Exception:
            error_data = None
            error_message = response.text

        raise TickTickSessionError(
            f"Authentication failed: {error_message}",
            details={
                "status_code": response.status_code,
                "response": error_data or response.text,
            },
        )
