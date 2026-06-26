"""
Unified TickTick API.

This module provides the UnifiedTickTickAPI class, which is the main
entry point for version-agnostic TickTick operations.

It manages both V1 and V2 clients, routes operations appropriately,
and converts between unified models and API-specific formats.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from types import TracebackType
from typing import Any, TypeVar

from ticktick_sdk.api.v1 import TickTickV1Client
from ticktick_sdk.api.v2 import TickTickV2Client
from ticktick_sdk.api.v2.auth import SessionToken
from ticktick_sdk.constants import TaskStatus
from ticktick_sdk.exceptions import (
    TickTickAPIError,
    TickTickAPIUnavailableError,
    TickTickAuthenticationError,
    TickTickConfigurationError,
    TickTickForbiddenError,
    TickTickNotFoundError,
    TickTickQuotaExceededError,
    TickTickRateLimitError,
)
from ticktick_sdk.models import (
    Column,
    Task,
    Project,
    ProjectGroup,
    ProjectData,
    Tag,
    User,
    UserStatus,
    UserStatistics,
    Habit,
    HabitSection,
    HabitCheckin,
    HabitPreferences,
)
from ticktick_sdk.settings import _generate_object_id
from ticktick_sdk.unified.router import APIRouter

logger = logging.getLogger(__name__)

T = TypeVar("T", bound="UnifiedTickTickAPI")


# Error codes that map to NotFoundError in batch responses
_BATCH_NOT_FOUND_ERRORS = frozenset({
    "TASK_NOT_FOUND",
    "PROJECT_NOT_FOUND",
    "TAG_NOT_FOUND",
    "task not exists",
    "project not found",
})

# Error codes that map to QuotaExceededError
_BATCH_QUOTA_ERRORS = frozenset({
    "EXCEED_QUOTA",
})


def _is_rate_limit_error(error: Exception) -> bool:
    """Was this failure a rate-limit / throttle (HTTP 429) rather than bad auth?

    The V2 password and cookie paths surface 429 differently: the cookie
    verification raises ``TickTickRateLimitError``, while password sign-on wraps
    it as an auth error that still carries ``status_code=429`` in details. We
    treat either as a throttle, because the operator guidance is the opposite of
    a stale session ("wait / stop restarting", not "refresh the cookie").
    """
    if isinstance(error, TickTickRateLimitError):
        return True
    details = getattr(error, "details", None)
    if isinstance(details, dict) and details.get("status_code") == 429:
        return True
    text = str(error).lower()
    return "429" in text or "rate limit" in text or "too many requests" in text


def _check_batch_response_errors(
    response: dict[str, Any],
    operation: str,
    resource_ids: list[str] | None = None,
) -> None:
    """Check a V2 batch response for errors and raise appropriate exceptions.

    V2 batch endpoints return HTTP 200 with errors in the `id2error` field.
    This function checks for those errors and raises semantic exceptions.

    Args:
        response: The batch response dict with 'id2etag' and 'id2error' fields
        operation: Operation name for error messages
        resource_ids: Optional list of resource IDs to check for errors

    Raises:
        TickTickNotFoundError: If a resource was not found
        TickTickQuotaExceededError: If quota was exceeded
        TickTickAPIError: For other errors
    """
    id2error = response.get("id2error", {})
    if not id2error:
        return

    # If specific IDs provided, only check those
    if resource_ids:
        errors_to_check = {k: v for k, v in id2error.items() if k in resource_ids}
    else:
        errors_to_check = id2error

    if not errors_to_check:
        return

    # Check each error and raise the appropriate exception
    for resource_id, error_msg in errors_to_check.items():
        error_upper = error_msg.upper() if error_msg else ""

        # Check for not found errors
        if any(nf in error_upper or nf.upper() in error_upper for nf in _BATCH_NOT_FOUND_ERRORS):
            raise TickTickNotFoundError(
                f"Resource not found: {error_msg}",
                resource_id=resource_id,
            )

        # Check for quota errors
        if any(qe in error_upper for qe in _BATCH_QUOTA_ERRORS):
            raise TickTickQuotaExceededError(
                f"Quota exceeded: {error_msg}",
            )

        # Generic error for anything else
        raise TickTickAPIError(
            f"{operation} failed: {error_msg}",
            details={"resource_id": resource_id, "error": error_msg},
        )


def _calculate_streak_from_checkins(
    checkins: list[HabitCheckin],
    reference_date: date | None = None,
) -> int:
    """
    Calculate current streak from check-in records.

    A streak is the count of consecutive days with completed check-ins (status=2),
    ending at the reference date or the day before if the reference date has no
    check-in.

    This matches the behavior of the TickTick web app, which calculates streaks
    client-side based on check-in records.

    Args:
        checkins: List of HabitCheckin records for the habit
        reference_date: The date to calculate streak from (default: today)

    Returns:
        The current streak count (0 if no streak)

    Example:
        If today is Dec 21 and check-ins exist for Dec 21, 20, 19, 18 (all completed),
        the streak is 4. If Dec 19 was not completed, streak would be 2 (Dec 21, 20).
    """
    if not checkins:
        return 0

    if reference_date is None:
        reference_date = date.today()

    # Build a set of completed check-in dates (status=2 means completed)
    # checkin_stamp is in YYYYMMDD format (int)
    completed_stamps: set[int] = {
        c.checkin_stamp for c in checkins if c.status == 2
    }

    if not completed_stamps:
        return 0

    # Convert reference_date to stamp format
    def date_to_stamp(d: date) -> int:
        return int(d.strftime("%Y%m%d"))

    # Start from reference_date
    current_date = reference_date
    current_stamp = date_to_stamp(current_date)

    # If reference_date is not completed, try the day before
    # (streak can still be valid if yesterday is completed)
    if current_stamp not in completed_stamps:
        current_date = current_date - timedelta(days=1)
        current_stamp = date_to_stamp(current_date)
        if current_stamp not in completed_stamps:
            # Neither today nor yesterday completed - no active streak
            return 0

    # Count consecutive completed days going backwards
    streak = 0
    while current_stamp in completed_stamps:
        streak += 1
        current_date = current_date - timedelta(days=1)
        current_stamp = date_to_stamp(current_date)

    return streak


def _parse_cookie_header(header: str) -> dict[str, str]:
    """Parse a Cookie header string ('k1=v1; k2=v2; ...') into a dict.

    Tolerates extra whitespace, trailing semicolons, and bare names (which
    are skipped). Empty input returns {}.
    """
    out: dict[str, str] = {}
    if not header:
        return out
    for piece in header.split(";"):
        piece = piece.strip()
        if not piece or "=" not in piece:
            continue
        name, _, value = piece.partition("=")
        name = name.strip()
        if name:
            out[name] = value.strip()
    return out


def _count_total_checkins(checkins: list[HabitCheckin]) -> int:
    """
    Count total completed check-ins.

    Args:
        checkins: List of HabitCheckin records

    Returns:
        Count of check-ins with status=2 (completed)
    """
    return sum(1 for c in checkins if c.status == 2)


class UnifiedTickTickAPI:
    """
    Unified TickTick API providing version-agnostic operations.

    This class manages both V1 and V2 API clients and provides
    a single interface for all TickTick operations. It automatically
    routes operations to the appropriate API version.

    Both V1 and V2 authentication are REQUIRED for full functionality.

    Usage:
        async with UnifiedTickTickAPI(
            # V1 OAuth2
            client_id="...",
            client_secret="...",
            v1_access_token="...",
            # V2 Session
            username="...",
            password="...",
        ) as api:
            # Full functionality available
            tasks = await api.list_all_tasks()
            projects = await api.list_projects()
            tags = await api.list_tags()
    """

    def __init__(
        self,
        # V1 OAuth2 credentials
        client_id: str,
        client_secret: str,
        redirect_uri: str = "http://localhost:8080/callback",
        v1_access_token: str | None = None,
        # V2 Session credentials
        username: str | None = None,
        password: str | None = None,
        v2_token: str | None = None,
        v2_cookies: str | None = None,
        # General
        timeout: float = 30.0,
        device_id: str | None = None,
    ) -> None:
        # Store credentials for lazy initialization
        self._v1_credentials = {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "access_token": v1_access_token,
            "timeout": timeout,
        }
        self._v2_credentials = {
            "username": username,
            "password": password,
            "v2_token": v2_token,
            "v2_cookies": v2_cookies,
            "device_id": device_id,
            "timeout": timeout,
        }

        # Clients (lazy initialized)
        self._v1_client: TickTickV1Client | None = None
        self._v2_client: TickTickV2Client | None = None

        # Router
        self._router: APIRouter | None = None

        # State
        self._initialized = False
        self._inbox_id: str | None = None

        # V2 degraded-mode tracking. When V2 sign-on fails (e.g. need_captcha),
        # we record a cooldown timestamp so logs and future code can see when
        # it's reasonable to retry. The server only attempts sign-on at startup
        # today; the cooldown is primarily informational + a guard against any
        # future re-auth path. A redeploy always bypasses the cooldown.
        self._v2_unavailable_until: datetime | None = None
        self._v2_unavailable_reason: str | None = None
        # Which path produced the current V2 session: "password", "cookie",
        # or None when V2 isn't authenticated. Surfaced by get_auth_status().
        self._v2_auth_method: str | None = None

    # =========================================================================
    # Initialization & Lifecycle
    # =========================================================================

    # Cooldown after a failed V2 password sign-on. Long enough that we won't
    # keep poking at TickTick (and re-triggering need_captcha) on incidental
    # retries, but a Railway redeploy always resets in-memory state so the
    # user can manually retry sooner.
    _V2_PASSWORD_COOLDOWN = timedelta(hours=6)

    async def initialize(self) -> None:
        """
        Initialize both API clients.

        Degrades gracefully: if V2 sign-on fails (e.g. captcha-walled) we keep
        running with V1-only and surface a clear log message instead of
        crashing the whole server. V2-only tool calls will raise
        ``TickTickAPIUnavailableError`` from the existing per-method guards.

        Only raises ``TickTickConfigurationError`` if *neither* API is usable
        — which is the only state where the server genuinely cannot do
        anything.
        """
        if self._initialized:
            return

        # --- V1 (OAuth2) ---------------------------------------------------
        v1_error: str | None = None
        try:
            self._v1_client = TickTickV1Client(
                client_id=self._v1_credentials["client_id"],
                client_secret=self._v1_credentials["client_secret"],
                redirect_uri=self._v1_credentials["redirect_uri"],
                access_token=self._v1_credentials["access_token"],
                timeout=self._v1_credentials["timeout"],
            )
            logger.info("V1 client initialized")
        except Exception as e:
            v1_error = f"V1 initialization failed: {e}"
            logger.error("Failed to initialize V1 client: %s", e)

        # --- V2 (Session) --------------------------------------------------
        # Always create the HTTP wrapper; auth is a separate step that can
        # fail without us losing the wrapper.
        try:
            self._v2_client = TickTickV2Client(
                device_id=self._v2_credentials["device_id"],
                timeout=self._v2_credentials["timeout"],
            )
        except Exception as e:
            logger.error("Failed to construct V2 client: %s", e)
            self._v2_client = None

        await self._authenticate_v2()

        # Surface *why* V2 is down on the client itself, so the degraded-mode
        # error raised on each V2 call can be specific (rate-limited vs stale
        # session) instead of listing every possible cause. None on success.
        if self._v2_client is not None:
            self._v2_client.degraded_reason = self._v2_unavailable_reason

        # --- Router + verification ----------------------------------------
        self._router = APIRouter(
            v1_client=self._v1_client,
            v2_client=self._v2_client,
        )

        verification = await self._router.verify_clients()

        if not verification.get("v1") and self._v1_client is not None and self._v1_client.is_authenticated:
            # The V1 client had a token but verify_clients() reported it
            # unhealthy — almost always means the OAuth access token has
            # expired or been revoked.
            logger.error(
                "V1 OAuth verification failed. Your TICKTICK_ACCESS_TOKEN is "
                "probably expired or revoked. Run `ticktick-sdk auth` to mint "
                "a fresh token and update TICKTICK_ACCESS_TOKEN in Railway. "
                "Until then, all V1-routed tools (e.g. get_project_with_data) "
                "will fail."
            )

        # --- Final state ---------------------------------------------------
        has_v1 = self._router.has_v1
        has_v2 = self._router.has_v2

        if not has_v1 and not has_v2:
            raise TickTickConfigurationError(
                "Both V1 and V2 APIs failed to initialize, server cannot start. "
                + (v1_error or "V1 not configured")
                + "; "
                + (self._v2_unavailable_reason or "V2 not configured")
            )

        if has_v1 and has_v2:
            logger.info("Unified API initialized successfully (V1 + V2)")
        elif has_v1:
            logger.warning(
                "Unified API initialized in DEGRADED mode: V1 only. "
                "V2-routed tools (tags, folders, habits, focus, subtasks, "
                "full task listings) will return a friendly error until V2 "
                "recovers. Reason: %s",
                self._v2_unavailable_reason or "unknown",
            )
        else:
            logger.warning(
                "Unified API initialized in DEGRADED mode: V2 only. "
                "V1-routed tools (get_project_with_data) will return a "
                "friendly error. Reason: %s",
                v1_error or "V1 not configured",
            )

        self._initialized = True

    async def _authenticate_v2(self) -> None:
        """Authenticate the V2 client.

        **Cookie-first.** If a pre-obtained session is configured
        (``TICKTICK_V2_COOKIES`` / ``TICKTICK_V2_TOKEN``) we try it *before*
        password sign-on, because the cookie path makes **no** login call and so
        cannot trip TickTick's anti-bot (captcha / 429). Password sign-on from a
        datacenter IP is the fragile, anti-bot-magnet path, so it's only used as
        a fallback when no cookie is configured or the cookie is stale. We also
        deliberately **skip** password sign-on when the cookie check was
        rate-limited (429), so we don't pour more failed logins onto a throttle.

        Never raises; records ``_v2_unavailable_reason`` /
        ``_v2_unavailable_until`` and lets the caller decide what to do.
        """
        if self._v2_client is None:
            self._v2_unavailable_reason = "V2 client not constructed"
            return

        creds = self._v2_credentials
        token = creds.get("v2_token")
        cookie_header = creds.get("v2_cookies")
        cookie_configured = bool(token or cookie_header)

        # --- Step 1: cookie / pre-obtained session (preferred) ------------
        cookie_failed_reason: str | None = None
        if cookie_configured:
            ok, cookie_failed_reason, rate_limited = await self._try_v2_cookie(
                token, cookie_header
            )
            if ok:
                return
            if rate_limited:
                # Do NOT fall through to /user/signon: a password login would
                # only add more failed-login fuel to the throttle (and would be
                # 429'd too). Stay degraded and let it cool off.
                self._v2_unavailable_reason = (
                    f"V2 is rate-limited (HTTP 429): the session cookie couldn't "
                    f"be verified on /user/status ({cookie_failed_reason}). The "
                    f"cookie itself may be fine — this is a throttle, usually from "
                    f"too many sign-on attempts. Skipping password sign-on so we "
                    f"don't add more. Stop restarting/redeploying and let the "
                    f"throttle clear; do NOT refresh the cookie yet."
                )
                return
            # else: cookie is stale/unusable → fall through to password.

        # --- Step 2: password sign-on (no cookie, or cookie stale) --------
        ok, password_failed_reason = await self._try_v2_password()
        if ok:
            return

        # --- Both paths failed (or password not configured) ---------------
        if cookie_configured and cookie_failed_reason:
            self._v2_unavailable_reason = (
                f"cookie fallback failed ({cookie_failed_reason}) and password "
                f"sign-on failed ({password_failed_reason})"
            )
        else:
            self._v2_unavailable_reason = (
                password_failed_reason or "V2 credentials not provided"
            )

    async def _try_v2_cookie(
        self, token: str | None, cookie_header: str | None
    ) -> tuple[bool, str | None, bool]:
        """Bring V2 up from a pre-obtained cookie/token (no login call).

        Returns ``(ok, failure_reason, was_rate_limited)``. On success, marks
        ``_v2_auth_method = "cookie"`` and clears the unavailable reason. On
        failure, clears the partial session so ``router.has_v2`` is False.
        """
        creds = self._v2_credentials
        cookies = _parse_cookie_header(cookie_header) if cookie_header else {}
        # Token precedence: explicit env var, else the `t` cookie.
        token = token or cookies.get("t")
        if not token:
            logger.error(
                "TICKTICK_V2_COOKIES is set but contains no `t=` cookie, and "
                "TICKTICK_V2_TOKEN is unset — cannot build a V2 session. Make "
                "sure you pasted the FULL Cookie header (it must include the "
                "`t=...` entry)."
            )
            return False, "no `t` cookie found in TICKTICK_V2_COOKIES", False

        logger.info(
            "Attempting V2 auth via pre-obtained session cookie "
            "(from TICKTICK_V2_COOKIES%s)",
            " + TICKTICK_V2_TOKEN" if creds.get("v2_token") else "",
        )
        try:
            # Always include the bare token as the 't' cookie too, since that's
            # what the V2 API actually checks.
            cookies.setdefault("t", token)
            session = SessionToken(
                token=token,
                user_id="",
                username=creds["username"] or "",
                inbox_id="",
                cookies=cookies,
            )
            self._v2_client.set_session(session)

            # Verify by hitting /user/status — this also gives us the real
            # inbox_id / user_id the hand-built SessionToken didn't have. A
            # stale cookie 401s here; a throttle 429s here.
            status = await self._v2_client.get_user_status()
            session.inbox_id = str(status.get("inboxId", "")) if isinstance(status, dict) else ""
            session.user_id = str(status.get("userId", "")) if isinstance(status, dict) else ""
            self._inbox_id = session.inbox_id or self._inbox_id

            self._v2_unavailable_reason = None
            self._v2_unavailable_until = None
            self._v2_auth_method = "cookie"
            logger.info("V2 authenticated via pre-obtained session cookie.")
            return True, None, False
        except Exception as e:
            rate_limited = _is_rate_limit_error(e)
            if rate_limited:
                logger.error(
                    "V2 cookie could not be verified: rate-limited (HTTP 429) on "
                    "/user/status (%s). This is a THROTTLE, not proof of a stale "
                    "cookie — the session may still be valid, we just can't "
                    "confirm it while throttled. Usual cause: too many sign-on "
                    "attempts (the server re-authenticating on every connection). "
                    "Refreshing the cookie will NOT help while throttled.",
                    e,
                )
            else:
                logger.error(
                    "V2 cookie verification failed: %s. The session in "
                    "TICKTICK_V2_COOKIES is probably stale — refresh it from a "
                    "logged-in TickTick browser tab.",
                    e,
                )
            # Clear the partial session so router.has_v2 = False.
            try:
                self._v2_client._session_handler.clear_session()
            except Exception:
                pass
            return False, str(e), rate_limited

    async def _try_v2_password(self) -> tuple[bool, str | None]:
        """Sign on to V2 with username/password (the anti-bot-prone path).

        Returns ``(ok, failure_reason)``. On success, marks
        ``_v2_auth_method = "password"``. On failure, records a 6h informational
        cooldown and logs actionable guidance.
        """
        creds = self._v2_credentials
        if not (creds["username"] and creds["password"]):
            return False, "no username/password configured"
        try:
            session = await self._v2_client.authenticate(
                creds["username"],
                creds["password"],
            )
            self._inbox_id = session.inbox_id
            self._v2_unavailable_reason = None
            self._v2_unavailable_until = None
            self._v2_auth_method = "password"
            logger.info("V2 authenticated via username/password")
            return True, None
        except Exception as e:
            # Includes TickTickSessionError for need_captcha, 2FA, wrong
            # password, etc., plus any network failure.
            if _is_rate_limit_error(e):
                reason = (
                    f"rate-limited (HTTP 429) on /user/signon — TickTick is "
                    f"throttling logins, usually from too many sign-on attempts "
                    f"(e.g. re-authenticating on every connection) ({e})"
                )
            else:
                reason = str(e)
            cooldown_until = datetime.now(timezone.utc) + self._V2_PASSWORD_COOLDOWN
            self._v2_unavailable_until = cooldown_until
            logger.error("V2 password sign-on failed: %s", e)
            logger.error(
                "V2 will be unavailable until ~%s UTC (6h cooldown). You can "
                "REDEPLOY at any time to retry sooner — the cooldown is in-memory "
                "only and resets on restart. If this keeps happening, TickTick is "
                "anti-bot flagging your password login (need_captcha / 429). Best "
                "fix: set TICKTICK_V2_COOKIES from a logged-in browser so the "
                "server skips /user/signon entirely (it's tried first now).",
                cooldown_until.isoformat(timespec="seconds"),
            )
            return False, reason

    async def get_auth_status(self) -> dict[str, Any]:
        """Live auth health snapshot for diagnostics (no secrets).

        Performs two lightweight read pings (V1 `/project`, V2 `/user/status`)
        to test the *current* validity of each session — so it catches a
        token/cookie that expired after startup, not just the boot-time state.
        Never returns credential values; only booleans + derived facts.
        """
        # --- V1 live check ---
        v1_has_token = self._v1_client is not None and self._v1_client.is_authenticated
        v1_ok = False
        v1_error: str | None = None
        if v1_has_token:
            try:
                await self._v1_client.get_projects()
                v1_ok = True
            except Exception as e:
                v1_error = str(e)

        # --- V2 live check ---
        v2_has_session = self._v2_client is not None and self._v2_client.is_authenticated
        v2_ok = False
        v2_error: str | None = None
        if v2_has_session:
            try:
                await self._v2_client.get_user_status()
                v2_ok = True
            except Exception as e:
                v2_error = str(e)

        return {
            "v1_has_credentials": v1_has_token,
            "v1_ok": v1_ok,
            "v1_error": v1_error,
            "v2_has_session": v2_has_session,
            "v2_ok": v2_ok,
            "v2_error": v2_error,
            "v2_auth_method": self._v2_auth_method,
            "v2_unavailable_reason": self._v2_unavailable_reason,
            "v2_cooldown_until": (
                self._v2_unavailable_until.isoformat(timespec="seconds")
                if self._v2_unavailable_until
                else None
            ),
        }

    async def close(self) -> None:
        """Close all API clients."""
        if self._v1_client:
            await self._v1_client.close()
        if self._v2_client:
            await self._v2_client.close()
        self._initialized = False

    async def __aenter__(self: T) -> T:
        """Enter async context manager."""
        await self.initialize()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit async context manager."""
        await self.close()

    def _ensure_initialized(self) -> None:
        """Ensure the API is initialized."""
        if not self._initialized:
            raise TickTickConfigurationError(
                "API not initialized. Use 'await api.initialize()' or async context manager."
            )

    @property
    def inbox_id(self) -> str | None:
        """Get the user's inbox ID."""
        return self._inbox_id

    @property
    def router(self) -> APIRouter:
        """Get the API router."""
        self._ensure_initialized()
        return self._router  # type: ignore

    # =========================================================================
    # Sync
    # =========================================================================

    async def sync_all(self) -> dict[str, Any]:
        """
        Sync all data from TickTick.

        Returns complete state including projects, tasks, tags, etc.
        This is a V2-only operation.

        Returns:
            Complete sync state dictionary
        """
        self._ensure_initialized()
        return await self._v2_client.sync()  # type: ignore

    # =========================================================================
    # Task Operations
    # =========================================================================

    async def list_all_tasks(self) -> list[Task]:
        """
        List all active tasks.

        Returns:
            List of all active tasks
        """
        self._ensure_initialized()
        state = await self._v2_client.sync()  # type: ignore
        tasks_data = state.get("syncTaskBean", {}).get("update", [])
        return [Task.from_v2(t) for t in tasks_data]

    async def get_task(self, task_id: str, project_id: str | None = None) -> Task:
        """
        Get a single task by ID.

        Args:
            task_id: Task identifier
            project_id: Project ID (required for V1 if V2 unavailable)

        Returns:
            Task object

        Raises:
            TickTickNotFoundError: If the task does not exist
            TickTickForbiddenError: If access to the task is forbidden
            TickTickAPIUnavailableError: If no API is available for this operation
        """
        self._ensure_initialized()

        # Use V2 (primary) - doesn't need project_id
        if self._router.has_v2:
            # Let resource-level errors propagate - they are definitive answers
            data = await self._v2_client.get_task(task_id)  # type: ignore
            return Task.from_v2(data)

        # Use V1 if V2 unavailable (requires project_id)
        if self._router.has_v1 and project_id:
            data = await self._v1_client.get_task(project_id, task_id)  # type: ignore
            return Task.from_v1(data)

        # No API available for this operation
        raise TickTickAPIUnavailableError(
            "Could not get task: V2 unavailable and V1 requires project_id",
            operation="get_task",
        )

    async def create_task(
        self,
        title: str,
        project_id: str | None = None,
        *,
        content: str | None = None,
        desc: str | None = None,
        kind: str | None = None,
        priority: int | None = None,
        start_date: datetime | None = None,
        due_date: datetime | None = None,
        time_zone: str | None = None,
        is_all_day: bool | None = None,
        reminders: list[str] | None = None,
        repeat_flag: str | None = None,
        tags: list[str] | None = None,
        parent_id: str | None = None,
    ) -> Task:
        """
        Create a new task.

        Uses V2 API primarily for richer features (tags, parent_id).

        Args:
            title: Task title
            project_id: Project ID (defaults to inbox)
            content: Task content
            desc: Checklist description
            kind: Task type (TEXT, NOTE, CHECKLIST)
            priority: Priority (0, 1, 3, 5)
            start_date: Start date
            due_date: Due date
            time_zone: Timezone
            is_all_day: All-day flag
            reminders: List of reminder triggers
            repeat_flag: Recurrence rule
            tags: List of tags (V2 only)
            parent_id: Parent task ID for subtasks (V2 only)

        Returns:
            Created task

        Raises:
            TickTickConfigurationError: If recurrence given without start_date
            TickTickAPIUnavailableError: If V2 API is not available
        """
        self._ensure_initialized()

        # Validate: recurrence requires start_date (TickTick silently ignores it otherwise)
        if repeat_flag and not start_date:
            raise TickTickConfigurationError(
                "Recurrence (repeat_flag) requires start_date. "
                "TickTick silently ignores recurrence without a start date."
            )

        # Default to inbox if no project specified
        if project_id is None:
            project_id = self._inbox_id
        if project_id is None:
            raise TickTickConfigurationError("No project ID provided and inbox ID unknown")

        # Format dates
        start_str = Task.format_datetime(start_date, "v2") if start_date else None
        due_str = Task.format_datetime(due_date, "v2") if due_date else None

        # V2 is REQUIRED (not optional fallback)
        if not self._router.has_v2:
            raise TickTickAPIUnavailableError(
                "V2 API is required for create_task but not available",
                operation="create_task",
            )

        response = await self._v2_client.create_task(  # type: ignore
            title=title,
            project_id=project_id,
            content=content,
            desc=desc,
            kind=kind,
            priority=priority,
            start_date=start_str,
            due_date=due_str,
            time_zone=time_zone,
            is_all_day=is_all_day,
            reminders=[{"trigger": r} for r in reminders] if reminders else None,
            repeat_flag=repeat_flag,
            tags=tags,
            # Note: parent_id is NOT passed here - V2 API ignores it during creation
            # We set it separately below via set_task_parent
        )

        # Get the created task ID from response
        task_id = next(iter(response.get("id2etag", {}).keys()), None)
        if not task_id:
            raise TickTickAPIError(
                "V2 create_task succeeded but returned no task ID",
                details={"response": response},
            )

        # If parent_id provided, set parent-child relationship separately
        # (V2 API ignores parentId during task creation)
        if parent_id:
            await self._v2_client.set_task_parent(task_id, project_id, parent_id)  # type: ignore

        return await self.get_task(task_id, project_id)

    async def update_task(
        self,
        task: Task,
    ) -> Task:
        """
        Update a task.

        Args:
            task: Task object with updated fields

        Returns:
            Updated task

        Raises:
            TickTickNotFoundError: If the task does not exist
            TickTickAPIError: On other API errors
        """
        self._ensure_initialized()

        # Use V2 (primary)
        if self._router.has_v2:
            data = task.to_v2_dict(for_update=True)
            response = await self._v2_client.batch_tasks(update=[data])  # type: ignore

            # Check for errors in batch response
            _check_batch_response_errors(response, "update_task", [task.id])

            # Return updated task
            return await self.get_task(task.id, task.project_id)

        # Use V1 if V2 unavailable
        if self._router.has_v1:
            data = await self._v1_client.update_task(  # type: ignore
                task_id=task.id,
                project_id=task.project_id,
                title=task.title,
                content=task.content,
                desc=task.desc,
                is_all_day=task.is_all_day,
                start_date=task.format_datetime(task.start_date, "v1"),
                due_date=task.format_datetime(task.due_date, "v1"),
                time_zone=task.time_zone,
                reminders=[r.trigger for r in task.reminders] if task.reminders else None,
                repeat_flag=task.repeat_flag,
                priority=task.priority,
                sort_order=task.sort_order,
            )
            return Task.from_v1(data)

        raise TickTickAPIUnavailableError(
            "Could not update task",
            operation="update_task",
        )

    async def complete_task(self, task_id: str, project_id: str) -> None:
        """
        Mark a task as complete.

        Uses V2 API primarily (better error handling).
        Falls back to V1 only if V2 unavailable.

        Note: V2 batch operations silently accept updates to nonexistent tasks,
        so we verify the task exists first to provide proper error handling.

        Args:
            task_id: Task ID
            project_id: Project ID

        Raises:
            TickTickNotFoundError: If the task does not exist
        """
        self._ensure_initialized()

        # Use V2 (primary) - better error handling
        if self._router.has_v2:
            # V2 batch API silently accepts updates to nonexistent tasks (returns
            # empty etag but no error). Verify task exists first for proper errors.
            await self._v2_client.get_task(task_id)  # type: ignore  # Raises NotFoundError if missing

            response = await self._v2_client.batch_tasks(  # type: ignore
                update=[{
                    "id": task_id,
                    "projectId": project_id,
                    "status": TaskStatus.COMPLETED,
                    "completedTime": Task.format_datetime(datetime.now(), "v2"),
                }]
            )
            # Check for errors in batch response (shouldn't happen after verify)
            _check_batch_response_errors(response, "complete_task", [task_id])
            return

        # Fallback to V1 only if V2 unavailable
        if self._router.has_v1:
            await self._v1_client.complete_task(project_id, task_id)  # type: ignore
            return

        raise TickTickAPIUnavailableError(
            "Could not complete task",
            operation="complete_task",
        )

    async def delete_task(self, task_id: str, project_id: str) -> None:
        """
        Delete a task.

        Note: V2 batch operations silently ignore nonexistent tasks,
        so we verify the task exists first to provide proper error handling.

        Args:
            task_id: Task ID
            project_id: Project ID

        Raises:
            TickTickNotFoundError: If the task does not exist
        """
        self._ensure_initialized()

        # Use V2 (primary)
        if self._router.has_v2:
            # V2 delete silently ignores nonexistent tasks. Verify first.
            await self._v2_client.get_task(task_id)  # type: ignore  # Raises NotFoundError if missing

            await self._v2_client.delete_task(project_id, task_id)  # type: ignore
            return

        # Fallback to V1
        if self._router.has_v1:
            await self._v1_client.delete_task(project_id, task_id)  # type: ignore
            return

        raise TickTickAPIUnavailableError(
            "Could not delete task",
            operation="delete_task",
        )

    async def list_completed_tasks(
        self,
        from_date: datetime,
        to_date: datetime,
        limit: int = 100,
    ) -> list[Task]:
        """
        List completed tasks in a date range.

        V2-only operation.

        Args:
            from_date: Start date
            to_date: End date
            limit: Maximum results

        Returns:
            List of completed tasks
        """
        self._ensure_initialized()
        data = await self._v2_client.get_completed_tasks(from_date, to_date, limit)  # type: ignore
        return [Task.from_v2(t) for t in data]

    async def list_abandoned_tasks(
        self,
        from_date: datetime,
        to_date: datetime,
        limit: int = 100,
    ) -> list[Task]:
        """
        List abandoned ("won't do") tasks in a date range.

        V2-only operation.

        Args:
            from_date: Start date
            to_date: End date
            limit: Maximum results

        Returns:
            List of abandoned tasks
        """
        self._ensure_initialized()
        data = await self._v2_client.get_abandoned_tasks(from_date, to_date, limit)  # type: ignore
        return [Task.from_v2(t) for t in data]

    async def list_deleted_tasks(
        self,
        start: int = 0,
        limit: int = 100,
    ) -> list[Task]:
        """
        List deleted tasks (in trash).

        V2-only operation.

        Args:
            start: Pagination offset
            limit: Maximum results

        Returns:
            List of deleted tasks
        """
        self._ensure_initialized()
        data = await self._v2_client.get_deleted_tasks(start, limit)  # type: ignore
        return [Task.from_v2(t) for t in data.get("tasks", [])]

    async def move_task(
        self,
        task_id: str,
        from_project_id: str,
        to_project_id: str,
    ) -> None:
        """
        Move a task to a different project.

        V2-only operation.

        Note: V2 move operation silently ignores nonexistent tasks,
        so we verify the task exists first to provide proper error handling.

        Args:
            task_id: Task ID
            from_project_id: Source project ID
            to_project_id: Destination project ID

        Raises:
            TickTickNotFoundError: If the task does not exist
        """
        self._ensure_initialized()
        # V2 move silently ignores nonexistent tasks. Verify first.
        await self._v2_client.get_task(task_id)  # type: ignore  # Raises NotFoundError if missing
        await self._v2_client.move_task(task_id, from_project_id, to_project_id)  # type: ignore

    async def _verify_tasks_exist(self, task_ids: set[str]) -> None:
        """Verify each (deduped) task exists, raising ``TickTickNotFoundError`` if not.

        V2 batch complete/delete/move silently no-op against tasks that no longer
        exist (empty result, no error), so we fetch each unique id first to turn a
        vanished task into a real 404 — the same guard the singular
        ``complete_task`` / ``delete_task`` / ``move_task`` already apply.

        Raises:
            TickTickNotFoundError: If any task does not exist.
        """
        for task_id in task_ids:
            await self._v2_client.get_task(task_id)  # type: ignore  # Raises NotFoundError if missing

    async def _verify_parent_exists(self, parent_id: str) -> None:
        """Verify a reparent target (the parent task) still exists.

        V2's ``set_parent`` silently accepts a deleted ``parent_id`` and does
        nothing, which leaves the would-be subtask orphaned. Fetching the parent
        forces a clear 404 that names the *parent* (so callers know which side
        vanished) instead of a silent no-op.

        Raises:
            TickTickNotFoundError: If the parent task does not exist.
        """
        try:
            await self._v2_client.get_task(parent_id)  # type: ignore  # Raises NotFoundError if missing
        except TickTickNotFoundError as exc:
            raise TickTickNotFoundError(
                f"Parent task {parent_id} not found — it may have been deleted. "
                "Subtasks cannot be attached to a nonexistent parent.",
                resource_type="task",
                resource_id=parent_id,
            ) from exc

    async def set_task_parent(
        self,
        task_id: str,
        project_id: str,
        parent_id: str,
    ) -> None:
        """
        Make a task a subtask of another task.

        V2-only operation.

        Note: V2 set_parent operation silently ignores nonexistent tasks, so we
        verify BOTH the child and the parent exist first. Attaching to a deleted
        parent otherwise looks like success but silently orphans the child.

        Args:
            task_id: Task to make a subtask
            project_id: Project ID
            parent_id: Parent task ID

        Raises:
            TickTickNotFoundError: If the child task or the parent task does not exist
        """
        self._ensure_initialized()
        # V2 set_parent silently ignores nonexistent tasks. Verify the child
        # exists, then the parent (see _verify_parent_exists).
        await self._v2_client.get_task(task_id)  # type: ignore  # Raises NotFoundError if child missing
        await self._verify_parent_exists(parent_id)
        await self._v2_client.set_task_parent(task_id, project_id, parent_id)  # type: ignore

    async def unset_task_parent(
        self,
        task_id: str,
        project_id: str,
    ) -> None:
        """
        Remove a task from being a subtask (make it top-level).

        V2-only operation.

        Note: V2 unset_parent operation silently ignores nonexistent tasks,
        so we verify the task exists first to provide proper error handling.

        Args:
            task_id: Task to unparent
            project_id: Project ID

        Raises:
            TickTickNotFoundError: If the task does not exist
            TickTickAPIError: If the task is not a subtask
        """
        self._ensure_initialized()
        # V2 unset_parent silently ignores nonexistent tasks. Verify first.
        task = await self._v2_client.get_task(task_id)  # type: ignore  # Raises NotFoundError if missing

        # Check if it's actually a subtask
        parent_id = task.get("parentId")
        if not parent_id:
            raise TickTickAPIError(
                f"Task {task_id} is not a subtask (has no parent)",
                details={"task_id": task_id},
            )

        await self._v2_client.unset_task_parent(task_id, project_id, parent_id)  # type: ignore

    # =========================================================================
    # Task Pinning Operations (V2 only)
    # =========================================================================

    async def pin_task(self, task_id: str, project_id: str) -> Task:
        """
        Pin a task to the top.

        Pinned tasks appear at the top of task lists in TickTick.

        Args:
            task_id: Task ID
            project_id: Project ID

        Returns:
            Updated task with pinned_time set
        """
        self._ensure_initialized()
        if not self._router.has_v2:  # type: ignore
            raise TickTickAPIUnavailableError(
                "Task pinning requires V2 API",
                details={"operation": "pin_task"},
            )

        # Get current task to ensure it exists
        task = await self.get_task(task_id, project_id)

        # Set pinned_time to current timestamp in TickTick format
        now = datetime.now(timezone.utc)
        pinned_time_str = now.strftime("%Y-%m-%dT%H:%M:%S.000+0000")

        await self._v2_client.update_task(  # type: ignore
            task_id=task_id,
            project_id=project_id,
            pinned_time=pinned_time_str,
        )

        # Update task object
        task.pinned_time = now
        return task

    async def unpin_task(self, task_id: str, project_id: str) -> Task:
        """
        Unpin a task.

        Args:
            task_id: Task ID
            project_id: Project ID

        Returns:
            Updated task with pinned_time cleared
        """
        self._ensure_initialized()
        if not self._router.has_v2:  # type: ignore
            raise TickTickAPIUnavailableError(
                "Task unpinning requires V2 API",
                details={"operation": "unpin_task"},
            )

        # Get current task to ensure it exists
        task = await self.get_task(task_id, project_id)

        # Clear pinned_time by sending empty string
        await self._v2_client.update_task(  # type: ignore
            task_id=task_id,
            project_id=project_id,
            pinned_time="",  # Empty string signals "clear"
        )

        # Update task object
        task.pinned_time = None
        return task

    # =========================================================================
    # Batch Task Operations (V2 only)
    # =========================================================================

    async def batch_create_tasks(
        self,
        tasks: list[dict[str, Any]],
    ) -> list[Task]:
        """
        Create multiple tasks in a batch operation.

        V2-only operation. Each task in the list will be created, and if
        parent_id is specified, the parent-child relationship will be set
        after creation.

        Args:
            tasks: List of task specifications. Each dict should contain:
                - title (required): Task title
                - project_id (optional): Project ID (defaults to inbox)
                - content (optional): Task notes/content
                - priority (optional): Priority (0, 1, 3, 5)
                - start_date (optional): Start date (datetime or ISO string)
                - due_date (optional): Due date (datetime or ISO string)
                - time_zone (optional): Timezone
                - all_day (optional): All-day flag
                - reminders (optional): List of reminder triggers
                - recurrence (optional): RRULE recurrence pattern
                - tags (optional): List of tag names
                - parent_id (optional): Parent task ID for subtasks

        Returns:
            List of created Task objects

        Raises:
            TickTickAPIUnavailableError: If V2 API is not available
            TickTickAPIError: On other API errors
        """
        self._ensure_initialized()

        if not self._router.has_v2:
            raise TickTickAPIUnavailableError(
                "V2 API is required for batch_create_tasks",
                operation="batch_create_tasks",
            )

        results: list[Task] = []

        # Process each task (V2 batch create doesn't support parent_id directly)
        for task_spec in tasks:
            title = task_spec.get("title")
            if not title:
                raise TickTickAPIError(
                    "Each task requires a 'title' field",
                    details={"task_spec": task_spec},
                )

            project_id = task_spec.get("project_id") or self._inbox_id
            parent_id = task_spec.get("parent_id")

            # Format dates if provided
            start_date = task_spec.get("start_date")
            due_date = task_spec.get("due_date")
            if start_date and isinstance(start_date, datetime):
                start_date = Task.format_datetime(start_date, "v2")
            if due_date and isinstance(due_date, datetime):
                due_date = Task.format_datetime(due_date, "v2")

            # Prepare reminders
            reminders = task_spec.get("reminders")
            if reminders:
                reminders = [{"trigger": r} for r in reminders]

            # Map priority string to int if needed
            priority = task_spec.get("priority")
            if isinstance(priority, str):
                priority_map = {"none": 0, "low": 1, "medium": 3, "high": 5}
                priority = priority_map.get(priority.lower(), priority)
                if isinstance(priority, str):
                    priority = int(priority)

            # Create the task
            response = await self._v2_client.create_task(  # type: ignore
                title=title,
                project_id=project_id,
                content=task_spec.get("content"),
                desc=task_spec.get("description"),
                kind=task_spec.get("kind"),
                priority=priority,
                start_date=start_date,
                due_date=due_date,
                time_zone=task_spec.get("time_zone"),
                is_all_day=task_spec.get("all_day"),
                reminders=reminders,
                repeat_flag=task_spec.get("recurrence"),
                tags=task_spec.get("tags"),
            )

            # Get the created task ID
            task_id = next(iter(response.get("id2etag", {}).keys()), None)
            if not task_id:
                raise TickTickAPIError(
                    "batch_create_tasks succeeded but returned no task ID",
                    details={"response": response, "title": title},
                )

            # Set parent if requested
            if parent_id:
                await self._v2_client.set_task_parent(task_id, project_id, parent_id)  # type: ignore

            # Fetch the created task
            results.append(await self.get_task(task_id, project_id))

        return results

    async def batch_update_tasks(
        self,
        updates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Update multiple tasks in a batch operation.

        V2-only operation.

        Each update preserves unspecified fields: the existing task is fetched,
        the user-supplied delta is merged into it, and the full task is sent
        back. This is required because TickTick's V2 /batch/task endpoint
        treats the update payload as the new task representation — any field
        not present in the body is reset to its default (e.g. repeatFlag
        becomes null, isAllDay flips to false, timeZone is wiped).

        Args:
            updates: List of update specifications. Each dict must contain:
                - task_id (required): Task ID to update
                - project_id (required): Project ID containing the task
                And any of these optional fields:
                - title: New title
                - content: New content
                - priority: New priority (0, 1, 3, 5 or 'none', 'low', 'medium', 'high')
                - start_date: New start date (datetime or ISO string)
                - due_date: New due date (datetime or ISO string)
                - time_zone: New timezone
                - all_day: All-day flag
                - tags: New tags (replaces existing)
                - recurrence: New recurrence rule
                - column_id: Kanban column ID (empty string to remove from column)
                - kind: TEXT / NOTE / CHECKLIST

        Returns:
            Batch response with id2etag and id2error

        Raises:
            TickTickAPIUnavailableError: If V2 API is not available
            TickTickAPIError: On other API errors
        """
        self._ensure_initialized()

        if not self._router.has_v2:
            raise TickTickAPIUnavailableError(
                "V2 API is required for batch_update_tasks",
                operation="batch_update_tasks",
            )

        priority_map = {"none": 0, "low": 1, "medium": 3, "high": 5}
        v2_updates: list[dict[str, Any]] = []

        for update in updates:
            task_id = update.get("task_id")
            project_id = update.get("project_id")

            if not task_id or not project_id:
                raise TickTickAPIError(
                    "Each update requires 'task_id' and 'project_id'",
                    details={"update": update},
                )

            # Pre-fetch so we can send the full task representation. Without
            # this, fields not in the delta would be wiped server-side.
            existing = await self.get_task(task_id, project_id)

            if "title" in update and update["title"] is not None:
                existing.title = update["title"]
            if "content" in update and update["content"] is not None:
                existing.content = update["content"]
            if "kind" in update and update["kind"] is not None:
                existing.kind = update["kind"]
            if "priority" in update and update["priority"] is not None:
                priority = update["priority"]
                if isinstance(priority, str):
                    key = priority.lower()
                    priority = priority_map[key] if key in priority_map else int(priority)
                existing.priority = priority
            if "start_date" in update and update["start_date"] is not None:
                start_date = update["start_date"]
                if isinstance(start_date, str):
                    start_date = Task.parse_datetime(start_date)
                existing.start_date = start_date
            if "due_date" in update and update["due_date"] is not None:
                due_date = update["due_date"]
                if isinstance(due_date, str):
                    due_date = Task.parse_datetime(due_date)
                existing.due_date = due_date
            if "time_zone" in update and update["time_zone"] is not None:
                existing.time_zone = update["time_zone"]
            if "all_day" in update and update["all_day"] is not None:
                existing.is_all_day = update["all_day"]
            if "tags" in update and update["tags"] is not None:
                existing.tags = update["tags"]
            if "recurrence" in update and update["recurrence"] is not None:
                existing.repeat_flag = update["recurrence"]

            v2_update = existing.to_v2_dict(for_update=True)

            # column_id is not serialized by to_v2_dict; pass through if the
            # caller explicitly set it (empty string removes from column).
            if "column_id" in update:
                v2_update["columnId"] = update["column_id"] if update["column_id"] else ""

            v2_updates.append(v2_update)

        response = await self._v2_client.batch_tasks(update=v2_updates)  # type: ignore
        _check_batch_response_errors(response, "batch_update_tasks", [u["id"] for u in v2_updates])
        return response

    async def batch_delete_tasks(
        self,
        task_ids: list[tuple[str, str]],
    ) -> dict[str, Any]:
        """
        Delete multiple tasks in a batch operation.

        V2-only operation.

        Args:
            task_ids: List of (task_id, project_id) tuples

        Returns:
            Batch response with id2etag and id2error

        Raises:
            TickTickAPIUnavailableError: If V2 API is not available
            TickTickNotFoundError: If any task does not exist
        """
        self._ensure_initialized()

        if not self._router.has_v2:
            raise TickTickAPIUnavailableError(
                "V2 API is required for batch_delete_tasks",
                operation="batch_delete_tasks",
            )

        # V2 batch delete silently ignores tasks that no longer exist. Verify
        # first so a wrong/stale id surfaces as a 404 (matches delete_task).
        await self._verify_tasks_exist({tid for tid, _ in task_ids})

        deletes = [{"taskId": tid, "projectId": pid} for tid, pid in task_ids]
        response = await self._v2_client.batch_tasks(delete=deletes)  # type: ignore
        return response

    async def batch_complete_tasks(
        self,
        task_ids: list[tuple[str, str]],
    ) -> dict[str, Any]:
        """
        Complete multiple tasks in a batch operation.

        V2-only operation.

        Args:
            task_ids: List of (task_id, project_id) tuples

        Returns:
            Batch response with id2etag and id2error

        Raises:
            TickTickAPIUnavailableError: If V2 API is not available
            TickTickNotFoundError: If any task does not exist
        """
        self._ensure_initialized()

        if not self._router.has_v2:
            raise TickTickAPIUnavailableError(
                "V2 API is required for batch_complete_tasks",
                operation="batch_complete_tasks",
            )

        # V2 batch update silently accepts completes for tasks that no longer
        # exist (empty etag, no id2error), so _check_batch_response_errors can't
        # see them. Verify first to surface a real 404 (matches complete_task).
        await self._verify_tasks_exist({tid for tid, _ in task_ids})

        updates = [{
            "id": tid,
            "projectId": pid,
            "status": TaskStatus.COMPLETED,
            "completedTime": Task.format_datetime(datetime.now(), "v2"),
        } for tid, pid in task_ids]

        response = await self._v2_client.batch_tasks(update=updates)  # type: ignore
        _check_batch_response_errors(response, "batch_complete_tasks", [tid for tid, _ in task_ids])
        return response

    async def batch_move_tasks(
        self,
        moves: list[dict[str, str]],
    ) -> Any:
        """
        Move multiple tasks between projects in a batch operation.

        V2-only operation.

        Args:
            moves: List of move specifications. Each dict must contain:
                - task_id: Task ID to move
                - from_project_id: Current project ID
                - to_project_id: Destination project ID

        Returns:
            Response from the move operation

        Raises:
            TickTickAPIUnavailableError: If V2 API is not available
            TickTickNotFoundError: If any task does not exist
        """
        self._ensure_initialized()

        if not self._router.has_v2:
            raise TickTickAPIUnavailableError(
                "V2 API is required for batch_move_tasks",
                operation="batch_move_tasks",
            )

        # V2 batch move silently ignores tasks that no longer exist. Verify
        # first so a wrong/stale id surfaces as a 404 (matches move_task).
        await self._verify_tasks_exist({m["task_id"] for m in moves})

        v2_moves = [{
            "taskId": m["task_id"],
            "fromProjectId": m["from_project_id"],
            "toProjectId": m["to_project_id"],
        } for m in moves]

        return await self._v2_client.move_tasks(v2_moves)  # type: ignore

    async def batch_set_task_parents(
        self,
        assignments: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """
        Make multiple tasks into subtasks in a batch operation.

        V2-only operation.

        Args:
            assignments: List of parent assignments. Each dict must contain:
                - task_id: Task ID to make a subtask
                - project_id: Project ID containing both tasks
                - parent_id: Parent task ID

        Returns:
            List of responses for each operation

        Raises:
            TickTickAPIUnavailableError: If V2 API is not available
            TickTickNotFoundError: If any child or parent task does not exist
        """
        self._ensure_initialized()

        if not self._router.has_v2:
            raise TickTickAPIUnavailableError(
                "V2 API is required for batch_set_task_parents",
                operation="batch_set_task_parents",
            )

        # V2 set_parent silently "succeeds" against deleted tasks/parents
        # (returns an etag, never an error). Attaching a subtask to a parent
        # that was deleted out from under us therefore looks like success but
        # does nothing — the child ends up orphaned (the failure that motivated
        # this check). Verify every referenced child AND parent exists first,
        # deduped so a shared parent is only fetched once, so a vanished task
        # surfaces as a clear 404 instead of a silent no-op.
        child_ids = {a["task_id"] for a in assignments}
        parent_ids = {a["parent_id"] for a in assignments}
        await self._verify_tasks_exist(child_ids)
        for parent_id in parent_ids - child_ids:
            await self._verify_parent_exists(parent_id)

        results: list[dict[str, Any]] = []
        for assignment in assignments:
            response = await self._v2_client.set_task_parent(  # type: ignore
                task_id=assignment["task_id"],
                project_id=assignment["project_id"],
                parent_id=assignment["parent_id"],
            )
            results.append(response)

        return results

    async def batch_unparent_tasks(
        self,
        tasks: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """
        Remove multiple tasks from their parents in a batch operation.

        V2-only operation.

        Args:
            tasks: List of unparent specifications. Each dict must contain:
                - task_id: Task ID to unparent
                - project_id: Project ID containing the task

        Returns:
            List of responses for each operation

        Raises:
            TickTickAPIUnavailableError: If V2 API is not available
            TickTickAPIError: If a task is not a subtask
        """
        self._ensure_initialized()

        if not self._router.has_v2:
            raise TickTickAPIUnavailableError(
                "V2 API is required for batch_unparent_tasks",
                operation="batch_unparent_tasks",
            )

        results: list[dict[str, Any]] = []
        for task_spec in tasks:
            task_id = task_spec["task_id"]
            project_id = task_spec["project_id"]

            # Get task to find parent_id
            task = await self._v2_client.get_task(task_id)  # type: ignore
            parent_id = task.get("parentId")

            if not parent_id:
                raise TickTickAPIError(
                    f"Task {task_id} is not a subtask (has no parent)",
                    details={"task_id": task_id},
                )

            response = await self._v2_client.unset_task_parent(  # type: ignore
                task_id=task_id,
                project_id=project_id,
                old_parent_id=parent_id,
            )
            results.append(response)

        return results

    async def batch_pin_tasks(
        self,
        pin_operations: list[dict[str, Any]],
    ) -> list[Task]:
        """
        Pin or unpin multiple tasks in a batch operation.

        V2-only operation.

        Args:
            pin_operations: List of pin specifications. Each dict must contain:
                - task_id: Task ID
                - project_id: Project ID
                - pin: True to pin, False to unpin

        Returns:
            List of updated Task objects

        Raises:
            TickTickAPIUnavailableError: If V2 API is not available
        """
        self._ensure_initialized()

        if not self._router.has_v2:
            raise TickTickAPIUnavailableError(
                "V2 API is required for batch_pin_tasks",
                operation="batch_pin_tasks",
            )

        results: list[Task] = []
        for op in pin_operations:
            task_id = op["task_id"]
            project_id = op["project_id"]
            pin = op.get("pin", True)

            if pin:
                task = await self.pin_task(task_id, project_id)
            else:
                task = await self.unpin_task(task_id, project_id)
            results.append(task)

        return results

    # =========================================================================
    # Column Operations (Kanban, V2 only)
    # =========================================================================

    async def list_columns(self, project_id: str) -> list[Column]:
        """
        Get all columns for a project.

        Args:
            project_id: Project ID

        Returns:
            List of Column objects
        """
        self._ensure_initialized()
        if not self._router.has_v2:  # type: ignore
            raise TickTickAPIUnavailableError(
                "Column operations require V2 API",
                details={"operation": "list_columns"},
            )

        columns_data = await self._v2_client.get_columns(project_id)  # type: ignore
        return [Column.from_v2(c) for c in columns_data]

    async def create_column(
        self,
        project_id: str,
        name: str,
        *,
        sort_order: int | None = None,
    ) -> Column:
        """
        Create a kanban column.

        Args:
            project_id: Project ID
            name: Column name
            sort_order: Display order (lower = earlier)

        Returns:
            Created column
        """
        self._ensure_initialized()
        if not self._router.has_v2:  # type: ignore
            raise TickTickAPIUnavailableError(
                "Column operations require V2 API",
                details={"operation": "create_column"},
            )

        response = await self._v2_client.create_column(  # type: ignore
            project_id=project_id,
            name=name,
            sort_order=sort_order,
        )

        # Get the created column ID from response
        id2etag = response.get("id2etag", {})
        if not id2etag:
            raise TickTickAPIError(
                "Failed to create column: no ID returned",
                details={"project_id": project_id, "name": name},
            )

        column_id = list(id2etag.keys())[0]

        # Fetch the full column data
        columns = await self.list_columns(project_id)
        for col in columns:
            if col.id == column_id:
                return col

        # Fallback: construct from known data
        return Column(
            id=column_id,
            project_id=project_id,
            name=name,
            sort_order=sort_order,
        )

    async def update_column(
        self,
        column_id: str,
        project_id: str,
        *,
        name: str | None = None,
        sort_order: int | None = None,
    ) -> Column:
        """
        Update a kanban column.

        Args:
            column_id: Column ID
            project_id: Project ID
            name: New name
            sort_order: New sort order

        Returns:
            Updated column
        """
        self._ensure_initialized()
        if not self._router.has_v2:  # type: ignore
            raise TickTickAPIUnavailableError(
                "Column operations require V2 API",
                details={"operation": "update_column"},
            )

        await self._v2_client.update_column(  # type: ignore
            column_id=column_id,
            project_id=project_id,
            name=name,
            sort_order=sort_order,
        )

        # Fetch updated column
        columns = await self.list_columns(project_id)
        for col in columns:
            if col.id == column_id:
                return col

        raise TickTickNotFoundError(
            f"Column not found after update: {column_id}",
            details={"column_id": column_id, "project_id": project_id},
        )

    async def delete_column(self, column_id: str, project_id: str) -> None:
        """
        Delete a kanban column.

        Note: Tasks in this column will become unassigned to any column.

        Args:
            column_id: Column ID
            project_id: Project ID (for validation)
        """
        self._ensure_initialized()
        if not self._router.has_v2:  # type: ignore
            raise TickTickAPIUnavailableError(
                "Column operations require V2 API",
                details={"operation": "delete_column"},
            )

        await self._v2_client.delete_column(column_id, project_id)  # type: ignore

    async def move_task_to_column(
        self,
        task_id: str,
        project_id: str,
        column_id: str | None,
    ) -> Task:
        """
        Move a task to a kanban column.

        Args:
            task_id: Task ID
            project_id: Project ID
            column_id: Target column ID (None to remove from column)

        Returns:
            Updated task
        """
        self._ensure_initialized()
        if not self._router.has_v2:  # type: ignore
            raise TickTickAPIUnavailableError(
                "Column operations require V2 API",
                details={"operation": "move_task_to_column"},
            )

        # Get current task
        task = await self.get_task(task_id, project_id)

        # Update column_id
        await self._v2_client.update_task(  # type: ignore
            task_id=task_id,
            project_id=project_id,
            column_id=column_id if column_id else "",  # Empty string to clear
        )

        task.column_id = column_id
        return task

    # =========================================================================
    # Project Operations
    # =========================================================================

    async def list_projects(self) -> list[Project]:
        """
        List all projects.

        Returns:
            List of projects
        """
        self._ensure_initialized()

        # Use V2 (primary) for more metadata
        if self._router.has_v2:
            state = await self._v2_client.sync()  # type: ignore
            projects_data = state.get("projectProfiles", [])
            return [Project.from_v2(p) for p in projects_data]

        # Fallback to V1
        if self._router.has_v1:
            data = await self._v1_client.get_projects()  # type: ignore
            return [Project.from_v1(p) for p in data]

        raise TickTickAPIUnavailableError(
            "Could not list projects",
            operation="list_projects",
        )

    async def get_project(self, project_id: str) -> Project:
        """
        Get a project by ID.

        Args:
            project_id: Project ID

        Returns:
            Project object

        Raises:
            TickTickNotFoundError: If the project does not exist
        """
        self._ensure_initialized()

        # Use V2 (primary) - requires sync to get project list
        if self._router.has_v2:
            state = await self._v2_client.sync()  # type: ignore
            for p in state.get("projectProfiles", []):
                if p.get("id") == project_id:
                    return Project.from_v2(p)
            # Project not found in V2 sync response
            raise TickTickNotFoundError(
                f"Project not found: {project_id}",
                resource_id=project_id,
            )

        # Use V1 if V2 unavailable - has dedicated endpoint
        if self._router.has_v1:
            data = await self._v1_client.get_project(project_id)  # type: ignore
            return Project.from_v1(data)

        raise TickTickAPIUnavailableError(
            "Could not get project",
            operation="get_project",
        )

    async def get_project_with_data(self, project_id: str) -> ProjectData:
        """
        Get a project with its tasks and columns.

        Tries V2 API first (using sync data to get tasks), then falls back to V1.
        Note: V2 doesn't provide kanban column data, so columns will be empty.

        Args:
            project_id: Project ID

        Returns:
            ProjectData with project, tasks, and columns

        Raises:
            TickTickNotFoundError: If the project does not exist
        """
        self._ensure_initialized()

        # Try V2 first (more reliable for projects created via V2)
        if self._router.has_v2:
            try:
                # Get all data from sync
                state = await self._v2_client.sync()  # type: ignore

                # Find the project
                project_data = None
                for p in state.get("projectProfiles", []):
                    if p.get("id") == project_id:
                        project_data = p
                        break

                if project_data is None:
                    raise TickTickNotFoundError(
                        f"Project not found: {project_id}",
                        resource_id=project_id,
                    )

                project = Project.from_v2(project_data)

                # Get tasks for this project from sync data
                all_tasks_data = state.get("syncTaskBean", {}).get("update", [])
                project_tasks = [
                    Task.from_v2(t)
                    for t in all_tasks_data
                    if t.get("projectId") == project_id
                ]

                return ProjectData.from_v2(project, project_tasks)

            except TickTickNotFoundError:
                raise
            except Exception as e:
                logger.warning("V2 get_project_with_data failed, trying V1: %s", e)

        # Fall back to V1 if available
        if self._router.has_v1:
            try:
                data = await self._v1_client.get_project_with_data(project_id)  # type: ignore

                # V1 returns empty dict {} for nonexistent projects
                if not data or not data.get("project"):
                    raise TickTickNotFoundError(
                        f"Project not found: {project_id}",
                        resource_id=project_id,
                    )

                return ProjectData.from_v1(data)
            except TickTickNotFoundError:
                raise
            except Exception as e:
                logger.warning("V1 get_project_with_data also failed: %s", e)

        raise TickTickAPIUnavailableError(
            "Could not get project with data: both V1 and V2 failed",
            operation="get_project_with_data",
        )

    async def create_project(
        self,
        name: str,
        *,
        color: str | None = None,
        kind: str | None = None,
        view_mode: str | None = None,
        group_id: str | None = None,
    ) -> Project:
        """
        Create a new project.

        Args:
            name: Project name
            color: Hex color
            kind: Project kind (TASK, NOTE)
            view_mode: View mode (list, kanban, timeline)
            group_id: Parent folder ID

        Returns:
            Created project

        Raises:
            TickTickAPIUnavailableError: If V2 API is not available
        """
        self._ensure_initialized()

        # V2 is REQUIRED
        if not self._router.has_v2:
            raise TickTickAPIUnavailableError(
                "V2 API is required for create_project but not available",
                operation="create_project",
            )

        response = await self._v2_client.create_project(  # type: ignore
            name=name,
            color=color,
            kind=kind,
            view_mode=view_mode,
            group_id=group_id,
        )
        project_id = next(iter(response.get("id2etag", {}).keys()), None)
        if not project_id:
            raise TickTickAPIError(
                "V2 create_project succeeded but returned no project ID",
                details={"response": response},
            )

        return await self.get_project(project_id)

    async def update_project(
        self,
        project_id: str,
        *,
        name: str | None = None,
        color: str | None = None,
        folder_id: str | None = None,
    ) -> Project:
        """
        Update a project.

        V2-only operation.

        Args:
            project_id: Project ID
            name: New name (required)
            color: New hex color
            folder_id: New folder ID (use "NONE" to ungroup)

        Returns:
            Updated project

        Raises:
            TickTickNotFoundError: If the project does not exist
        """
        self._ensure_initialized()

        # Verify project exists first
        existing = await self.get_project(project_id)

        # Use existing name if not provided
        project_name = name if name is not None else existing.name

        await self._v2_client.update_project(  # type: ignore
            project_id=project_id,
            name=project_name,
            color=color,
            group_id=folder_id,
        )

        return await self.get_project(project_id)

    async def delete_project(self, project_id: str) -> None:
        """
        Delete a project.

        Note: V2 delete silently ignores nonexistent projects,
        so we verify the project exists first to provide proper error handling.

        Args:
            project_id: Project ID

        Raises:
            TickTickNotFoundError: If the project does not exist
        """
        self._ensure_initialized()

        # Use V2 (primary)
        if self._router.has_v2:
            # V2 delete silently ignores nonexistent projects. Verify first.
            await self.get_project(project_id)  # Raises NotFoundError if missing
            await self._v2_client.delete_project(project_id)  # type: ignore
            return

        # Fallback to V1
        if self._router.has_v1:
            await self._v1_client.delete_project(project_id)  # type: ignore
            return

        raise TickTickAPIUnavailableError(
            "Could not delete project",
            operation="delete_project",
        )

    # =========================================================================
    # Project Group Operations (V2 Only)
    # =========================================================================

    async def list_project_groups(self) -> list[ProjectGroup]:
        """
        List all project groups/folders.

        V2-only operation.

        Returns:
            List of project groups
        """
        self._ensure_initialized()
        state = await self._v2_client.sync()  # type: ignore
        groups_data = state.get("projectGroups") or []  # Handle None values
        return [ProjectGroup.from_v2(g) for g in groups_data]

    async def create_project_group(self, name: str) -> ProjectGroup:
        """
        Create a project group/folder.

        V2-only operation.

        Args:
            name: Group name

        Returns:
            Created group
        """
        self._ensure_initialized()
        response = await self._v2_client.create_project_group(name)  # type: ignore
        group_id = next(iter(response.get("id2etag", {}).keys()), None)

        # Get from sync to return full object
        groups = await self.list_project_groups()
        for group in groups:
            if group.id == group_id:
                return group

        # Return minimal if not found
        return ProjectGroup(id=group_id or "", name=name)

    async def update_project_group(
        self,
        group_id: str,
        name: str,
    ) -> ProjectGroup:
        """
        Update a project group/folder (rename).

        V2-only operation.

        Args:
            group_id: Group ID
            name: New name

        Returns:
            Updated group

        Raises:
            TickTickNotFoundError: If the group does not exist
        """
        self._ensure_initialized()

        # Verify group exists first
        groups = await self.list_project_groups()
        existing = next((g for g in groups if g.id == group_id), None)
        if not existing:
            raise TickTickNotFoundError(
                f"Project group not found: {group_id}",
                resource_id=group_id,
            )

        await self._v2_client.update_project_group(group_id, name)  # type: ignore

        # Get updated group
        groups = await self.list_project_groups()
        for group in groups:
            if group.id == group_id:
                return group

        # Return with new name if not found (shouldn't happen)
        return ProjectGroup(id=group_id, name=name)

    async def delete_project_group(self, group_id: str) -> None:
        """
        Delete a project group/folder.

        V2-only operation.

        Note: V2 delete silently ignores nonexistent groups,
        so we verify the group exists first to provide proper error handling.

        Args:
            group_id: Group ID

        Raises:
            TickTickNotFoundError: If the group does not exist
        """
        self._ensure_initialized()

        # V2 delete silently ignores nonexistent groups. Verify first.
        groups = await self.list_project_groups()
        if not any(g.id == group_id for g in groups):
            raise TickTickNotFoundError(
                f"Project group not found: {group_id}",
                resource_id=group_id,
            )

        await self._v2_client.delete_project_group(group_id)  # type: ignore

    # =========================================================================
    # Tag Operations (V2 Only)
    # =========================================================================

    async def list_tags(self) -> list[Tag]:
        """
        List all tags.

        V2-only operation.

        Returns:
            List of tags
        """
        self._ensure_initialized()
        state = await self._v2_client.sync()  # type: ignore
        tags_data = state.get("tags", [])
        return [Tag.from_v2(t) for t in tags_data]

    async def create_tag(
        self,
        label: str,
        *,
        color: str | None = None,
        parent: str | None = None,
    ) -> Tag:
        """
        Create a tag.

        V2-only operation.

        Args:
            label: Tag display name
            color: Hex color
            parent: Parent tag name

        Returns:
            Created tag
        """
        self._ensure_initialized()
        await self._v2_client.create_tag(  # type: ignore
            label=label,
            color=color,
            parent=parent,
        )
        return Tag.create(label, color, parent)

    async def update_tag(
        self,
        name: str,
        *,
        color: str | None = None,
        parent: str | None = None,
    ) -> Tag:
        """
        Update a tag's properties (color, parent).

        V2-only operation.

        Args:
            name: Tag name (lowercase identifier)
            color: New hex color
            parent: New parent tag name (or None to remove parent)

        Returns:
            Updated tag

        Raises:
            TickTickNotFoundError: If the tag does not exist
        """
        self._ensure_initialized()

        # Verify tag exists first
        tags = await self.list_tags()
        existing = next((t for t in tags if t.name == name), None)
        if not existing:
            raise TickTickNotFoundError(
                f"Tag not found: {name}",
                resource_id=name,
            )

        # Use existing label
        await self._v2_client.update_tag(  # type: ignore
            name=name,
            label=existing.label,
            color=color,
            parent=parent,
        )

        # Get updated tag
        tags = await self.list_tags()
        for tag in tags:
            if tag.name == name:
                return tag

        # Return existing with updated fields if not found
        return Tag(
            name=name,
            label=existing.label,
            color=color or existing.color,
            parent=parent if parent is not None else existing.parent,
        )

    async def delete_tag(self, name: str) -> None:
        """
        Delete a tag.

        V2-only operation.

        Note: V2 delete silently ignores nonexistent tags,
        so we verify the tag exists first to provide proper error handling.

        Args:
            name: Tag name (lowercase identifier)

        Raises:
            TickTickNotFoundError: If the tag does not exist
        """
        self._ensure_initialized()

        # V2 delete silently ignores nonexistent tags. Verify first.
        tags = await self.list_tags()
        if not any(t.name == name for t in tags):
            raise TickTickNotFoundError(
                f"Tag not found: {name}",
                resource_id=name,
            )

        await self._v2_client.delete_tag(name)  # type: ignore

    async def rename_tag(self, old_name: str, new_label: str) -> None:
        """
        Rename a tag.

        V2-only operation.

        Args:
            old_name: Current tag name
            new_label: New tag label
        """
        self._ensure_initialized()
        await self._v2_client.rename_tag(old_name, new_label)  # type: ignore

    async def merge_tags(self, source_name: str, target_name: str) -> None:
        """
        Merge one tag into another.

        V2-only operation.

        Args:
            source_name: Tag to merge from (will be deleted)
            target_name: Tag to merge into
        """
        self._ensure_initialized()
        await self._v2_client.merge_tags(source_name, target_name)  # type: ignore

    # =========================================================================
    # User Operations (V2 Only)
    # =========================================================================

    async def get_user_profile(self) -> User:
        """
        Get user profile.

        V2-only operation.

        Returns:
            User profile
        """
        self._ensure_initialized()
        data = await self._v2_client.get_user_profile()  # type: ignore
        return User.from_v2(data)

    async def get_user_status(self) -> UserStatus:
        """
        Get user subscription status.

        V2-only operation.

        Returns:
            User status
        """
        self._ensure_initialized()
        data = await self._v2_client.get_user_status()  # type: ignore
        return UserStatus.from_v2(data)

    async def get_user_statistics(self) -> UserStatistics:
        """
        Get user productivity statistics.

        V2-only operation.

        Returns:
            User statistics
        """
        self._ensure_initialized()
        data = await self._v2_client.get_user_statistics()  # type: ignore
        return UserStatistics.from_v2(data)

    async def get_user_preferences(self) -> dict[str, Any]:
        """
        Get user preferences and settings.

        V2-only operation.

        Returns:
            User preferences dictionary containing settings like:
            - timeZone: User's timezone
            - weekStartDay: First day of week (0=Sunday, 1=Monday, etc.)
            - startOfDay: Hour when day starts
            - dateFormat: Date display format
            - timeFormat: Time display format (12h/24h)
            - defaultReminder: Default reminder setting
            - And many more user-configurable options
        """
        self._ensure_initialized()
        return await self._v2_client.get_user_preferences()  # type: ignore

    # =========================================================================
    # Focus/Pomodoro Operations (V2 Only)
    # =========================================================================

    async def get_focus_heatmap(
        self,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        """
        Get focus/pomodoro heatmap.

        V2-only operation.

        Args:
            start_date: Start date
            end_date: End date

        Returns:
            Heatmap data
        """
        self._ensure_initialized()
        return await self._v2_client.get_focus_heatmap(start_date, end_date)  # type: ignore

    async def get_focus_by_tag(
        self,
        start_date: date,
        end_date: date,
    ) -> dict[str, int]:
        """
        Get focus time by tag.

        V2-only operation.

        Args:
            start_date: Start date
            end_date: End date

        Returns:
            Dict of tag -> duration in seconds
        """
        self._ensure_initialized()
        data = await self._v2_client.get_focus_by_tag(start_date, end_date)  # type: ignore
        return data.get("tagDurations", {})

    # =========================================================================
    # Habit Operations (V2 Only)
    # =========================================================================

    async def list_habits(self) -> list[Habit]:
        """
        List all habits.

        V2-only operation.

        Returns:
            List of habits
        """
        self._ensure_initialized()
        data = await self._v2_client.get_habits()  # type: ignore
        return [Habit.from_v2(h) for h in data]

    async def get_habit(self, habit_id: str) -> Habit:
        """
        Get a habit by ID.

        V2-only operation.

        Args:
            habit_id: Habit ID

        Returns:
            Habit object

        Raises:
            TickTickNotFoundError: If habit not found
        """
        self._ensure_initialized()
        habits = await self.list_habits()
        for habit in habits:
            if habit.id == habit_id:
                return habit
        raise TickTickNotFoundError(
            f"Habit not found: {habit_id}",
            resource_id=habit_id,
        )

    async def list_habit_sections(self) -> list[HabitSection]:
        """
        List habit sections (time-of-day groupings).

        V2-only operation.

        Returns:
            List of habit sections (_morning, _afternoon, _night)
        """
        self._ensure_initialized()
        data = await self._v2_client.get_habit_sections()  # type: ignore
        return [HabitSection.from_v2(s) for s in data]

    async def get_habit_preferences(self) -> HabitPreferences:
        """
        Get habit preferences/settings.

        V2-only operation.

        Returns:
            Habit preferences (showInCalendar, showInToday, enabled, etc.)
        """
        self._ensure_initialized()
        data = await self._v2_client.get_habit_preferences()  # type: ignore
        return HabitPreferences.from_v2(data)

    async def create_habit(
        self,
        name: str,
        *,
        habit_type: str = "Boolean",
        goal: float = 1.0,
        step: float = 0.0,
        unit: str = "Count",
        icon: str = "habit_daily_check_in",
        color: str = "#97E38B",
        section_id: str | None = None,
        repeat_rule: str = "RRULE:FREQ=WEEKLY;BYDAY=SU,MO,TU,WE,TH,FR,SA",
        reminders: list[str] | None = None,
        target_days: int = 0,
        encouragement: str = "",
    ) -> Habit:
        """
        Create a new habit.

        V2-only operation.

        Args:
            name: Habit name
            habit_type: "Boolean" for yes/no, "Real" for numeric
            goal: Target goal value (1.0 for boolean)
            step: Increment step for numeric habits
            unit: Unit of measurement
            icon: Icon resource name
            color: Hex color
            section_id: Time-of-day section ID
            repeat_rule: RRULE recurrence pattern
            reminders: List of reminder times ("HH:MM")
            target_days: Goal in days (0 = no target)
            encouragement: Motivational message

        Returns:
            Created habit
        """
        import secrets
        from datetime import datetime

        self._ensure_initialized()

        # Generate a 24-character hex ID (MongoDB ObjectId format)
        habit_id = secrets.token_hex(12)

        # Calculate target start date if target_days > 0
        target_start_date = None
        if target_days > 0:
            target_start_date = int(datetime.now().strftime("%Y%m%d"))

        # Determine if record_enable should be true (for numeric habits)
        record_enable = habit_type == "Real"

        response = await self._v2_client.create_habit(  # type: ignore
            habit_id=habit_id,
            name=name,
            habit_type=habit_type,
            goal=goal,
            step=step,
            unit=unit,
            icon=icon,
            color=color,
            section_id=section_id,
            repeat_rule=repeat_rule,
            reminders=reminders,
            target_days=target_days,
            target_start_date=target_start_date,
            encouragement=encouragement,
            record_enable=record_enable,
        )

        # Check for errors
        _check_batch_response_errors(response, "create_habit", [habit_id])

        # Return the created habit
        return await self.get_habit(habit_id)

    async def update_habit(
        self,
        habit_id: str,
        *,
        name: str | None = None,
        goal: float | None = None,
        step: float | None = None,
        unit: str | None = None,
        icon: str | None = None,
        color: str | None = None,
        section_id: str | None = None,
        repeat_rule: str | None = None,
        reminders: list[str] | None = None,
        target_days: int | None = None,
        encouragement: str | None = None,
    ) -> Habit:
        """
        Update a habit.

        V2-only operation.

        Args:
            habit_id: Habit ID
            name: New name
            goal: New goal
            step: New step
            unit: New unit
            icon: New icon
            color: New color
            section_id: New section ID
            repeat_rule: New repeat rule
            reminders: New reminders
            target_days: New target days
            encouragement: New encouragement

        Returns:
            Updated habit

        Raises:
            TickTickNotFoundError: If habit not found
        """
        self._ensure_initialized()

        # Verify habit exists
        await self.get_habit(habit_id)  # Raises NotFoundError if missing

        response = await self._v2_client.update_habit(  # type: ignore
            habit_id=habit_id,
            name=name,
            goal=goal,
            step=step,
            unit=unit,
            icon=icon,
            color=color,
            section_id=section_id,
            repeat_rule=repeat_rule,
            reminders=reminders,
            target_days=target_days,
            encouragement=encouragement,
        )

        _check_batch_response_errors(response, "update_habit", [habit_id])

        return await self.get_habit(habit_id)

    async def delete_habit(self, habit_id: str) -> None:
        """
        Delete a habit.

        V2-only operation.

        Args:
            habit_id: Habit ID

        Raises:
            TickTickNotFoundError: If habit not found
        """
        self._ensure_initialized()

        # Verify habit exists
        await self.get_habit(habit_id)  # Raises NotFoundError if missing

        response = await self._v2_client.delete_habit(habit_id)  # type: ignore
        _check_batch_response_errors(response, "delete_habit", [habit_id])

    async def checkin_habit(
        self,
        habit_id: str,
        value: float = 1.0,
        checkin_date: date | None = None,
    ) -> Habit:
        """
        Check in a habit for a specific date.

        V2-only operation.

        This method properly calculates streaks by:
        1. Creating a check-in record for the specified date
        2. Fetching all check-in records for the habit
        3. Calculating the streak from the actual records
        4. Updating the habit with the calculated values

        This matches the behavior of the TickTick web app, which calculates
        streaks client-side based on check-in records.

        Args:
            habit_id: Habit ID
            value: Check-in value (1.0 for boolean habits)
            checkin_date: Date to check in for (None = today).
                          Backdating (past dates) is fully supported.

        Returns:
            Updated habit with correctly calculated streak and total

        Raises:
            TickTickNotFoundError: If habit not found
        """
        self._ensure_initialized()

        # Determine the target date
        today = date.today()
        target_date = checkin_date if checkin_date is not None else today

        # Get current habit to preserve its data
        # (The API may return habits with null names after update operations)
        original_habit = await self.get_habit(habit_id)

        # Step 1: Create the check-in record
        checkin_stamp = int(target_date.strftime("%Y%m%d"))
        checkin_id = _generate_object_id()

        await self._v2_client.create_habit_checkin(  # type: ignore
            checkin_id=checkin_id,
            habit_id=habit_id,
            checkin_stamp=checkin_stamp,
            value=value,
            goal=original_habit.goal,
        )

        # Step 2: Fetch ALL check-in records for this habit
        # We need all records to calculate the streak correctly
        checkins_data = await self.get_habit_checkins([habit_id], after_stamp=0)
        all_checkins = checkins_data.get(habit_id, [])

        # Step 3: Calculate streak and total from the actual records
        # This ensures accuracy even when backdating extends a streak
        calculated_streak = _calculate_streak_from_checkins(all_checkins, today)
        calculated_total = _count_total_checkins(all_checkins)

        # Step 4: Update habit with calculated values
        response = await self._v2_client.update_habit(  # type: ignore
            habit_id=habit_id,
            name=original_habit.name,  # Preserve name!
            total_checkins=calculated_total,
            current_streak=calculated_streak,
        )

        _check_batch_response_errors(response, "checkin_habit", [habit_id])

        # Return habit with calculated counters but preserved original data
        # This avoids the issue where the API returns null for name after updates
        return Habit(
            id=original_habit.id,
            name=original_habit.name,
            icon=original_habit.icon,
            color=original_habit.color,
            sort_order=original_habit.sort_order,
            status=original_habit.status,
            encouragement=original_habit.encouragement,
            total_checkins=calculated_total,
            created_time=original_habit.created_time,
            modified_time=original_habit.modified_time,
            archived_time=original_habit.archived_time,
            habit_type=original_habit.habit_type,
            goal=original_habit.goal,
            step=original_habit.step,
            unit=original_habit.unit,
            etag=original_habit.etag,
            repeat_rule=original_habit.repeat_rule,
            reminders=original_habit.reminders,
            record_enable=original_habit.record_enable,
            section_id=original_habit.section_id,
            target_days=original_habit.target_days,
            target_start_date=original_habit.target_start_date,
            completed_cycles=original_habit.completed_cycles,
            ex_dates=original_habit.ex_dates,
            current_streak=calculated_streak,
            style=original_habit.style,
        )

    async def archive_habit(self, habit_id: str) -> Habit:
        """
        Archive a habit.

        V2-only operation.

        Args:
            habit_id: Habit ID

        Returns:
            Updated habit with preserved data and archived status

        Raises:
            TickTickNotFoundError: If habit not found
        """
        from datetime import datetime

        self._ensure_initialized()

        # Get original habit data to preserve
        original_habit = await self.get_habit(habit_id)

        # Use update_habit directly instead of archive_habit to preserve the name
        # (The API nullifies fields that aren't sent in update operations)
        response = await self._v2_client.update_habit(  # type: ignore
            habit_id=habit_id,
            name=original_habit.name,  # Preserve name!
            status=2,  # Archived
        )
        _check_batch_response_errors(response, "archive_habit", [habit_id])

        # Return habit with preserved data and updated status
        return Habit(
            id=original_habit.id,
            name=original_habit.name,
            icon=original_habit.icon,
            color=original_habit.color,
            sort_order=original_habit.sort_order,
            status=2,  # Archived status
            encouragement=original_habit.encouragement,
            total_checkins=original_habit.total_checkins,
            created_time=original_habit.created_time,
            modified_time=datetime.now(),
            archived_time=datetime.now(),
            habit_type=original_habit.habit_type,
            goal=original_habit.goal,
            step=original_habit.step,
            unit=original_habit.unit,
            etag=original_habit.etag,
            repeat_rule=original_habit.repeat_rule,
            reminders=original_habit.reminders,
            record_enable=original_habit.record_enable,
            section_id=original_habit.section_id,
            target_days=original_habit.target_days,
            target_start_date=original_habit.target_start_date,
            completed_cycles=original_habit.completed_cycles,
            ex_dates=original_habit.ex_dates,
            current_streak=original_habit.current_streak,
            style=original_habit.style,
        )

    async def unarchive_habit(self, habit_id: str) -> Habit:
        """
        Unarchive a habit.

        V2-only operation.

        Args:
            habit_id: Habit ID

        Returns:
            Updated habit with preserved data and active status

        Raises:
            TickTickNotFoundError: If habit not found
        """
        from datetime import datetime

        self._ensure_initialized()

        # Get original habit data to preserve
        original_habit = await self.get_habit(habit_id)

        # Use update_habit directly instead of unarchive_habit to preserve the name
        # (The API nullifies fields that aren't sent in update operations)
        response = await self._v2_client.update_habit(  # type: ignore
            habit_id=habit_id,
            name=original_habit.name,  # Preserve name!
            status=0,  # Active
        )
        _check_batch_response_errors(response, "unarchive_habit", [habit_id])

        # Return habit with preserved data and active status
        return Habit(
            id=original_habit.id,
            name=original_habit.name,
            icon=original_habit.icon,
            color=original_habit.color,
            sort_order=original_habit.sort_order,
            status=0,  # Active status
            encouragement=original_habit.encouragement,
            total_checkins=original_habit.total_checkins,
            created_time=original_habit.created_time,
            modified_time=datetime.now(),
            archived_time=None,  # Clear archived time
            habit_type=original_habit.habit_type,
            goal=original_habit.goal,
            step=original_habit.step,
            unit=original_habit.unit,
            etag=original_habit.etag,
            repeat_rule=original_habit.repeat_rule,
            reminders=original_habit.reminders,
            record_enable=original_habit.record_enable,
            section_id=original_habit.section_id,
            target_days=original_habit.target_days,
            target_start_date=original_habit.target_start_date,
            completed_cycles=original_habit.completed_cycles,
            ex_dates=original_habit.ex_dates,
            current_streak=original_habit.current_streak,
            style=original_habit.style,
        )

    async def get_habit_checkins(
        self,
        habit_ids: list[str],
        after_stamp: int = 0,
    ) -> dict[str, list[HabitCheckin]]:
        """
        Get habit check-in data.

        V2-only operation.

        Args:
            habit_ids: List of habit IDs to query
            after_stamp: Date stamp (YYYYMMDD) to get check-ins after (0 for all)

        Returns:
            Dict mapping habit IDs to lists of check-in records
        """
        self._ensure_initialized()
        data = await self._v2_client.get_habit_checkins(habit_ids, after_stamp)  # type: ignore

        result: dict[str, list[HabitCheckin]] = {}
        checkins_data = data.get("checkins", {})

        for habit_id, checkins in checkins_data.items():
            result[habit_id] = [HabitCheckin.from_v2(c) for c in checkins]

        return result

    async def batch_checkin_habits(
        self,
        checkins: list[dict[str, Any]],
    ) -> dict[str, Habit]:
        """
        Record multiple habit check-ins in a batch operation.

        This method is ideal for backdating multiple days of habit completions.
        Each check-in properly updates the habit's streak and total.

        V2-only operation.

        Args:
            checkins: List of check-in specifications. Each dict must contain:
                - habit_id (required): Habit ID to check in
                - value (optional): Check-in value (default 1.0 for boolean habits)
                - checkin_date (optional): Date to check in for (date object or
                  YYYY-MM-DD string). Defaults to today.

        Returns:
            Dict mapping habit_id to updated Habit object

        Raises:
            TickTickAPIUnavailableError: If V2 API is not available
            TickTickNotFoundError: If a habit is not found
        """
        self._ensure_initialized()

        if not self._router.has_v2:
            raise TickTickAPIUnavailableError(
                "V2 API is required for batch_checkin_habits",
                operation="batch_checkin_habits",
            )

        # Group checkins by habit for efficient processing
        habit_checkins: dict[str, list[dict[str, Any]]] = {}
        for checkin in checkins:
            habit_id = checkin.get("habit_id")
            if not habit_id:
                raise TickTickAPIError(
                    "Each check-in requires a 'habit_id' field",
                    details={"checkin": checkin},
                )
            if habit_id not in habit_checkins:
                habit_checkins[habit_id] = []
            habit_checkins[habit_id].append(checkin)

        results: dict[str, Habit] = {}
        today = date.today()

        for habit_id, habit_checkin_list in habit_checkins.items():
            # Get original habit to preserve data
            original_habit = await self.get_habit(habit_id)

            # Create check-in records for each date
            for checkin in habit_checkin_list:
                value = checkin.get("value", 1.0)
                checkin_date = checkin.get("checkin_date")

                # Parse date if provided as string
                if checkin_date is None:
                    target_date = today
                elif isinstance(checkin_date, str):
                    target_date = date.fromisoformat(checkin_date)
                else:
                    target_date = checkin_date

                checkin_stamp = int(target_date.strftime("%Y%m%d"))
                checkin_id = _generate_object_id()

                await self._v2_client.create_habit_checkin(  # type: ignore
                    checkin_id=checkin_id,
                    habit_id=habit_id,
                    checkin_stamp=checkin_stamp,
                    value=value,
                    goal=original_habit.goal,
                )

            # After all check-ins for this habit, recalculate streak
            checkins_data = await self.get_habit_checkins([habit_id], after_stamp=0)
            all_checkins = checkins_data.get(habit_id, [])

            calculated_streak = _calculate_streak_from_checkins(all_checkins, today)
            calculated_total = _count_total_checkins(all_checkins)

            # Update habit with calculated values
            response = await self._v2_client.update_habit(  # type: ignore
                habit_id=habit_id,
                name=original_habit.name,
                total_checkins=calculated_total,
                current_streak=calculated_streak,
            )
            _check_batch_response_errors(response, "batch_checkin_habits", [habit_id])

            # Build result habit with calculated values
            results[habit_id] = Habit(
                id=original_habit.id,
                name=original_habit.name,
                icon=original_habit.icon,
                color=original_habit.color,
                sort_order=original_habit.sort_order,
                status=original_habit.status,
                encouragement=original_habit.encouragement,
                total_checkins=calculated_total,
                created_time=original_habit.created_time,
                modified_time=original_habit.modified_time,
                archived_time=original_habit.archived_time,
                habit_type=original_habit.habit_type,
                goal=original_habit.goal,
                step=original_habit.step,
                unit=original_habit.unit,
                etag=original_habit.etag,
                repeat_rule=original_habit.repeat_rule,
                reminders=original_habit.reminders,
                record_enable=original_habit.record_enable,
                section_id=original_habit.section_id,
                target_days=original_habit.target_days,
                target_start_date=original_habit.target_start_date,
                completed_cycles=original_habit.completed_cycles,
                ex_dates=original_habit.ex_dates,
                current_streak=calculated_streak,
                style=original_habit.style,
            )

        return results
