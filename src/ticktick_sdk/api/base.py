"""
Base HTTP Client for TickTick API.

This module provides a base async HTTP client with common functionality
shared between V1 and V2 API clients.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from types import TracebackType
from typing import Any, TypeVar

import httpx

from ticktick_sdk.constants import APIVersion, DEFAULT_TIMEOUT, DEFAULT_USER_AGENT
from ticktick_sdk.exceptions import (
    TickTickAPIError,
    TickTickAuthenticationError,
    TickTickForbiddenError,
    TickTickNotFoundError,
    TickTickQuotaExceededError,
    TickTickRateLimitError,
    TickTickServerError,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound="BaseTickTickClient")


class BaseTickTickClient(ABC):
    """
    Abstract base class for TickTick API clients.

    Provides common HTTP functionality, error handling, and lifecycle management.
    """

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._timeout = timeout
        self._user_agent = user_agent
        self._client: httpx.AsyncClient | None = None
        self._is_authenticated = False
        # When this client can't authenticate, the unified layer records a
        # human-readable reason here so the degraded-mode error raised on each
        # call can say *why* (rate-limited vs stale session vs captcha) instead
        # of guessing. None when authenticated or when no reason was recorded.
        self.degraded_reason: str | None = None

    # =========================================================================
    # Abstract Properties
    # =========================================================================

    @property
    @abstractmethod
    def api_version(self) -> APIVersion:
        """Return the API version this client implements."""
        ...

    @property
    @abstractmethod
    def base_url(self) -> str:
        """Return the base URL for API requests."""
        ...

    @property
    @abstractmethod
    def is_authenticated(self) -> bool:
        """Check if the client is authenticated."""
        ...

    # =========================================================================
    # HTTP Client Management
    # =========================================================================

    def _get_base_headers(self) -> dict[str, str]:
        """Get base headers for all requests."""
        return {
            "User-Agent": self._user_agent,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @abstractmethod
    def _get_auth_headers(self) -> dict[str, str]:
        """Get authentication headers. Must be implemented by subclasses."""
        ...

    def _get_headers(self) -> dict[str, str]:
        """Get all headers for requests."""
        headers = self._get_base_headers()
        if self.is_authenticated:
            headers.update(self._get_auth_headers())
        return headers

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Ensure the HTTP client is initialized."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self._timeout),
                headers=self._get_base_headers(),
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # =========================================================================
    # Context Manager Support
    # =========================================================================

    async def __aenter__(self: T) -> T:
        """Enter async context manager."""
        await self._ensure_client()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit async context manager."""
        await self.close()

    # =========================================================================
    # Error Handling
    # =========================================================================

    # TickTick API error codes that map to specific exceptions
    # Note: TickTick often returns HTTP 500 with semantic error codes in the body
    _NOT_FOUND_ERROR_CODES = frozenset({
        "task_not_found",
        "project_not_found",
        "tag_not_found",
        "tag_not_exist",  # V2 API uses this for tag operations
        "folder_not_found",
        "group_not_found",
        "resource_not_found",
        "not_found",
    })

    _FORBIDDEN_ERROR_CODES = frozenset({
        "access_forbidden",
        "forbidden",
        "permission_denied",
    })

    _AUTH_ERROR_CODES = frozenset({
        "unauthorized",
        "invalid_token",
        "token_expired",
        "username_password_not_match",
        "incorrect_password_too_many_times",
    })

    def _auth_error_message(self, raw_message: str, endpoint: str) -> str:
        """Build an actionable auth-failure message for the MCP consumer.

        This text is surfaced all the way up to whoever is *using* the MCP
        (a model or a person), so it must explain what broke and how to fix
        it WITHOUT requiring access to the server's repo or logs.
        """
        if self.api_version == APIVersion.V1:
            return (
                f"TickTick V1 (OAuth) authentication failed at {endpoint} — the "
                "TICKTICK_ACCESS_TOKEN has likely expired or been revoked. The "
                "person hosting this server needs to mint a new token "
                "(`ticktick-sdk auth`), update TICKTICK_ACCESS_TOKEN in the "
                "hosting environment (e.g. Railway), and redeploy. If you are "
                "not the host, relay this to whoever is. "
                f"(TickTick said: {raw_message})"
            )
        return (
            f"TickTick V2 (session) authentication failed at {endpoint} — the "
            "session has expired or been invalidated. The person hosting this "
            "server needs to refresh the TICKTICK_V2_COOKIES env var from a "
            "logged-in TickTick browser tab (see the README section 'Grabbing "
            "TICKTICK_V2_COOKIES') and redeploy. If you are not the host, relay "
            f"this to whoever is. (TickTick said: {raw_message})"
        )

    def _handle_error_response(
        self,
        response: httpx.Response,
        endpoint: str,
    ) -> None:
        """Handle error responses and raise appropriate exceptions.

        TickTick's API often returns HTTP 500 for semantic errors like "not found".
        We check both the HTTP status code AND the errorCode in the response body
        to determine the actual error type.
        """
        status_code = response.status_code

        # Try to parse error body
        try:
            error_body = response.json()
            error_message = error_body.get("errorMessage") or error_body.get("message", response.text)
            error_code = error_body.get("errorCode", "").lower()
        except (json.JSONDecodeError, ValueError):
            error_body = None
            error_message = response.text
            error_code = ""

        # First, check error codes in response body (takes precedence over HTTP status)
        # TickTick often returns HTTP 500 with semantic error codes
        if error_code:
            if error_code in self._NOT_FOUND_ERROR_CODES:
                raise TickTickNotFoundError(
                    f"Resource not found: {error_message}",
                    endpoint=endpoint,
                    api_version=self.api_version.value,
                )
            elif error_code in self._FORBIDDEN_ERROR_CODES:
                raise TickTickForbiddenError(
                    f"Access forbidden: {error_message}",
                    endpoint=endpoint,
                    response_body=response.text,
                    api_version=self.api_version.value,
                )
            elif error_code in self._AUTH_ERROR_CODES:
                logger.error(
                    "%s auth failure at %s (errorCode=%s): %s",
                    self.api_version.value, endpoint, error_code, error_message,
                )
                raise TickTickAuthenticationError(
                    self._auth_error_message(error_message, endpoint),
                    details={
                        "endpoint": endpoint,
                        "response": error_body,
                        "error_code": error_code,
                        "api_version": self.api_version.value,
                    },
                )

        # Fall back to HTTP status code based handling
        if status_code == 401:
            # 401 mid-session means the token/session expired or was revoked.
            # Log for the operator AND return an actionable message to the
            # MCP consumer (who may not have repo/log access).
            logger.error(
                "%s auth failure (HTTP 401) at %s: %s",
                self.api_version.value, endpoint, error_message,
            )
            raise TickTickAuthenticationError(
                self._auth_error_message(error_message, endpoint),
                details={"endpoint": endpoint, "response": error_body, "api_version": self.api_version.value},
            )
        elif status_code == 403:
            raise TickTickForbiddenError(
                f"Access forbidden: {error_message}",
                endpoint=endpoint,
                response_body=response.text,
                api_version=self.api_version.value,
            )
        elif status_code == 404:
            raise TickTickNotFoundError(
                f"Resource not found: {error_message}",
                endpoint=endpoint,
                api_version=self.api_version.value,
            )
        elif status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise TickTickRateLimitError(
                "Rate limit exceeded",
                retry_after=int(retry_after) if retry_after else None,
                endpoint=endpoint,
                api_version=self.api_version.value,
            )
        elif status_code >= 500:
            # Check for quota exceeded in response body
            if error_body and error_body.get("id2error"):
                for error in error_body["id2error"].values():
                    if error == "EXCEED_QUOTA":
                        raise TickTickQuotaExceededError(
                            "Account quota exceeded",
                            endpoint=endpoint,
                            api_version=self.api_version.value,
                        )

            raise TickTickServerError(
                f"Server error: {error_message}",
                status_code=status_code,
                response_body=response.text,
                endpoint=endpoint,
                api_version=self.api_version.value,
            )
        else:
            # Check for quota exceeded in response body
            if error_body and error_body.get("id2error"):
                for error in error_body["id2error"].values():
                    if error == "EXCEED_QUOTA":
                        raise TickTickQuotaExceededError(
                            "Account quota exceeded",
                            endpoint=endpoint,
                            api_version=self.api_version.value,
                        )

            raise TickTickAPIError(
                f"API error: {error_message}",
                status_code=status_code,
                response_body=response.text,
                endpoint=endpoint,
                api_version=self.api_version.value,
            )

    # =========================================================================
    # HTTP Methods
    # =========================================================================

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | list[Any] | None = None,
        headers: dict[str, str] | None = None,
        require_auth: bool = True,
    ) -> httpx.Response:
        """
        Make an HTTP request.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            endpoint: API endpoint (relative to base URL)
            params: Query parameters
            json_data: JSON body data
            headers: Additional headers
            require_auth: Whether authentication is required

        Returns:
            httpx.Response object

        Raises:
            TickTickAuthenticationError: If auth required but not authenticated
            TickTickAPIError: On API errors
        """
        if require_auth and not self.is_authenticated:
            if self.api_version == APIVersion.V2:
                if self.degraded_reason:
                    # Quote the raw reason verbatim, then add hedged,
                    # non-prescriptive commentary rather than asserting a single
                    # cause/fix (the V2 anti-bot masks itself behind several
                    # error codes, so over-specific advice misleads).
                    message = (
                        f"TickTick V2 is currently unavailable. Reason recorded at "
                        f"auth time: {self.degraded_reason} "
                        "The server is running in V1-only degraded mode, so "
                        "V2-routed tools (task search/listing, account status, tags, "
                        "folders, habits, focus, subtasks) won't work until V2 "
                        "recovers. Common causes: an expired or revoked session "
                        "cookie, a TickTick rate-limit/throttle (HTTP 429), no cookie "
                        "configured (so password sign-on was attempted and anti-bot "
                        "blocked), or a different reason entirely. Read the reason "
                        "above to tell which. The most reliable fix is usually to set "
                        "or refresh TICKTICK_V2_COOKIES from a logged-in browser tab "
                        "(see README) and redeploy. If you are not the host, relay "
                        "this to whoever is."
                    )
                else:
                    message = (
                        "TickTick V2 is currently unavailable and no specific reason "
                        "was recorded, so the server is running in V1-only degraded "
                        "mode. Possible causes: an anti-bot/captcha block on password "
                        "sign-on, an invalid TICKTICK_DEVICE_ID, an expired session, "
                        "or a different reason entirely. The most reliable fix is "
                        "usually to set TICKTICK_V2_COOKIES (from a logged-in browser, "
                        "see README) so the server skips password sign-on, then "
                        "redeploy. If you are not the host, relay this to whoever is."
                    )
                raise TickTickAuthenticationError(
                    message,
                    details={"endpoint": endpoint, "api_version": "v2", "degraded": True},
                )
            raise TickTickAuthenticationError(
                f"Authentication required for {self.api_version.value} API",
                details={"endpoint": endpoint},
            )

        # Merge headers
        request_headers = self._get_headers()
        if headers:
            request_headers.update(headers)

        logger.debug(
            "Making %s request to %s%s",
            method,
            self.base_url,
            endpoint,
        )

        response = await self._send_http(
            method, endpoint, params, json_data, request_headers
        )

        # Handle errors. A status-code check works for both httpx and curl_cffi
        # responses (curl_cffi has no `.is_success`).
        if not (200 <= response.status_code < 300):
            self._handle_error_response(response, endpoint)

        return response

    async def _send_http(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None,
        json_data: Any,
        request_headers: dict[str, str],
    ) -> Any:
        """Send one HTTP request and return the response object.

        The default transport is httpx (used by V1, and by V2 as a fallback).
        Subclasses may override this to use a different transport: the V2 client
        swaps in curl_cffi browser impersonation to get past TickTick's V2
        anti-bot, which fingerprints plain Python HTTP clients. The returned
        object must expose ``status_code``, ``json()``, ``text``, ``content``,
        and ``headers.get()`` (both httpx and curl_cffi responses do).
        """
        client = await self._ensure_client()
        try:
            return await client.request(
                method=method,
                url=endpoint,
                params=params,
                json=json_data,
                headers=request_headers,
            )
        except httpx.TimeoutException as e:
            raise TickTickAPIError(
                f"Request timeout: {endpoint}",
                endpoint=endpoint,
                api_version=self.api_version.value,
                details={"timeout": self._timeout},
            ) from e
        except httpx.RequestError as e:
            raise TickTickAPIError(
                f"Request failed: {e}",
                endpoint=endpoint,
                api_version=self.api_version.value,
            ) from e

    async def _get(
        self,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        require_auth: bool = True,
    ) -> httpx.Response:
        """Make a GET request."""
        return await self._request(
            "GET",
            endpoint,
            params=params,
            headers=headers,
            require_auth=require_auth,
        )

    async def _post(
        self,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | list[Any] | None = None,
        headers: dict[str, str] | None = None,
        require_auth: bool = True,
    ) -> httpx.Response:
        """Make a POST request."""
        return await self._request(
            "POST",
            endpoint,
            params=params,
            json_data=json_data,
            headers=headers,
            require_auth=require_auth,
        )

    async def _put(
        self,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | list[Any] | None = None,
        headers: dict[str, str] | None = None,
        require_auth: bool = True,
    ) -> httpx.Response:
        """Make a PUT request."""
        return await self._request(
            "PUT",
            endpoint,
            params=params,
            json_data=json_data,
            headers=headers,
            require_auth=require_auth,
        )

    async def _delete(
        self,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        require_auth: bool = True,
    ) -> httpx.Response:
        """Make a DELETE request."""
        return await self._request(
            "DELETE",
            endpoint,
            params=params,
            headers=headers,
            require_auth=require_auth,
        )

    async def _get_json(
        self,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        require_auth: bool = True,
    ) -> Any:
        """Make a GET request and return JSON response.

        Note: V1 API returns HTTP 200 with empty body for nonexistent resources.
        We handle this by raising TickTickNotFoundError.
        """
        response = await self._get(
            endpoint,
            params=params,
            headers=headers,
            require_auth=require_auth,
        )
        # Handle empty response body (V1 returns HTTP 200 with empty body for nonexistent resources)
        if not response.content or response.content.strip() == b"":
            raise TickTickNotFoundError(
                f"Resource not found (empty response)",
                endpoint=endpoint,
                api_version=self.api_version.value,
            )
        return response.json()

    async def _post_json(
        self,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | list[Any] | None = None,
        headers: dict[str, str] | None = None,
        require_auth: bool = True,
    ) -> Any:
        """Make a POST request and return JSON response."""
        response = await self._post(
            endpoint,
            params=params,
            json_data=json_data,
            headers=headers,
            require_auth=require_auth,
        )
        return response.json()
