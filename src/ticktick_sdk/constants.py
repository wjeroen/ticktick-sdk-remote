"""
TickTick SDK Constants and Enumerations.

This module defines the constants and enumerations used throughout the SDK.
"""

from __future__ import annotations

import os
from enum import IntEnum, StrEnum
from typing import Literal


# =============================================================================
# API Host Configuration
# =============================================================================

# Supported hosts
# - ticktick.com: International version
# - dida365.com: Chinese version (滴答清单)
TickTickHost = Literal["ticktick.com", "dida365.com"]

# Default host (can be overridden via TICKTICK_HOST environment variable)
DEFAULT_HOST: TickTickHost = "ticktick.com"


def get_api_host() -> TickTickHost:
    """
    Get the configured API host.

    Reads from TICKTICK_HOST environment variable.
    Defaults to "ticktick.com" if not set or invalid.

    Returns:
        The API host domain ("ticktick.com" or "dida365.com").
    """
    host = os.environ.get("TICKTICK_HOST", DEFAULT_HOST).lower().strip()
    if host in ("ticktick.com", "dida365.com"):
        return host  # type: ignore[return-value]
    # Invalid host, return default
    return DEFAULT_HOST


def get_api_base_v1(host: TickTickHost | None = None) -> str:
    """Get the V1 API base URL for the specified host."""
    h = host or get_api_host()
    return f"https://api.{h}/open/v1"


def get_api_base_v2(host: TickTickHost | None = None) -> str:
    """Get the V2 API base URL for the specified host."""
    h = host or get_api_host()
    return f"https://api.{h}/api/v2"


def get_oauth_base(host: TickTickHost | None = None) -> str:
    """Get the OAuth base URL for the specified host."""
    h = host or get_api_host()
    return f"https://{h}/oauth"


# =============================================================================
# API Configuration
# =============================================================================

# Default request timeout in seconds
DEFAULT_TIMEOUT = 30.0

# OAuth2 scopes
OAUTH_SCOPES = ["tasks:read", "tasks:write"]


# =============================================================================
# Task Enumerations
# =============================================================================


class TaskStatus(IntEnum):
    """Task completion status values."""

    ABANDONED = -1  # Won't do (V2 only)
    ACTIVE = 0  # Open/In progress
    COMPLETED_ALT = 1  # Completed (alternative, V2)
    COMPLETED = 2  # Completed (standard)

    @classmethod
    def is_completed(cls, status: int) -> bool:
        """Check if a status value indicates completion."""
        return status in (cls.COMPLETED, cls.COMPLETED_ALT)

    @classmethod
    def is_closed(cls, status: int) -> bool:
        """Check if a status value indicates the task is closed (completed or abandoned)."""
        return status in (cls.ABANDONED, cls.COMPLETED, cls.COMPLETED_ALT)


class TaskPriority(IntEnum):
    """Task priority levels."""

    NONE = 0
    LOW = 1
    MEDIUM = 3
    HIGH = 5

    @classmethod
    def from_string(cls, priority: str) -> TaskPriority:
        """Convert a string priority to TaskPriority."""
        mapping = {
            "none": cls.NONE,
            "low": cls.LOW,
            "medium": cls.MEDIUM,
            "high": cls.HIGH,
        }
        return mapping.get(priority.lower(), cls.NONE)

    def to_string(self) -> str:
        """Convert TaskPriority to a human-readable string."""
        return self.name.lower()


class TaskKind(StrEnum):
    """Task type/kind values."""

    TEXT = "TEXT"  # Standard task
    NOTE = "NOTE"  # Note
    CHECKLIST = "CHECKLIST"  # Checklist task


# =============================================================================
# Project Enumerations
# =============================================================================


class ProjectKind(StrEnum):
    """Project type values."""

    TASK = "TASK"
    NOTE = "NOTE"


class ViewMode(StrEnum):
    """Project view mode values."""

    LIST = "list"
    KANBAN = "kanban"
    TIMELINE = "timeline"


# =============================================================================
# Subtask Status
# =============================================================================


class SubtaskStatus(IntEnum):
    """Subtask/checklist item status values."""

    NORMAL = 0
    COMPLETED = 1  # Note: Different from TaskStatus.COMPLETED which is 2


# =============================================================================
# Date/Time Formats
# =============================================================================

# ISO 8601 with milliseconds and hardcoded +0000 (V2 task dates)
DATETIME_FORMAT_V2 = "%Y-%m-%dT%H:%M:%S.000+0000"

# ISO 8601 with timezone offset (V1)
DATETIME_FORMAT_V1 = "%Y-%m-%dT%H:%M:%S%z"


# =============================================================================
# HTTP Headers
# =============================================================================

# User-Agent that works with the V2 API (based on pyticktick)
V2_USER_AGENT = "Mozilla/5.0 (rv:145.0) Firefox/145.0"

# X-Device "version" value the V2 API accepts
V2_DEVICE_VERSION = 6430

# Default User-Agent for the base HTTP client (V2 client overrides with V2_USER_AGENT)
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:95.0) "
    "Gecko/20100101 Firefox/95.0"
)


# =============================================================================
# API Version Enum
# =============================================================================


class APIVersion(StrEnum):
    """API version identifiers."""

    V1 = "v1"
    V2 = "v2"
