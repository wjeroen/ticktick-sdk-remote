"""
Base model functionality for TickTick unified models.

This module provides the base model class with common configuration
and utility methods used by all unified models.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timezone
from typing import Any, ClassVar, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, field_validator

from ticktick_sdk.constants import DATETIME_FORMAT_V1, DATETIME_FORMAT_V2


# A bare calendar date, as callers send for all-day tasks.
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class TickTickModel(BaseModel):
    """
    Base model for all TickTick data models.

    Provides common configuration and utility methods.
    """

    model_config = ConfigDict(
        # Allow population by field name or alias
        populate_by_name=True,
        # Use enum values in serialization
        use_enum_values=True,
        # Validate on assignment
        validate_assignment=True,
        # Allow extra fields (V1/V2 may have different fields)
        extra="ignore",
        # Convert to camelCase for JSON
        alias_generator=lambda s: s,
    )

    # Track which API version the data came from
    _source_api: ClassVar[str | None] = None

    @classmethod
    def parse_datetime(cls, value: str | datetime | None) -> datetime | None:
        """Parse a datetime string from either V1 or V2 format."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value

        # Try V2 format first (more common)
        formats = [
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S.000+0000",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S+0000",
            "%Y-%m-%dT%H:%M:%SZ",
        ]

        for fmt in formats:
            try:
                # Handle the +0000 format
                if "+0000" in value and "%z" in fmt:
                    value = value.replace("+0000", "+00:00")
                return datetime.strptime(value, fmt)
            except ValueError:
                continue

        # Try ISO format as fallback
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass

        return None

    @staticmethod
    def zone_or_none(name: str | None) -> ZoneInfo | None:
        """Return the IANA zone for ``name``, or None when it is empty or unknown."""
        if not name:
            return None
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            return None

    @classmethod
    def resolve_datetime(
        cls, value: str | datetime | date | None, home_tz: str | None
    ) -> datetime | None:
        """Turn a caller-supplied date or time into an exact moment.

        TickTick stores every date as an exact moment, so a value without an
        offset needs a zone before it can be sent. ``home_tz`` supplies it:

        - A plain date (``"2026-09-10"`` or a ``date``) becomes midnight of that
          date in ``home_tz``. For an all-day task, pass the task's own zone:
          the TickTick app shows an all-day task on the date its stored moment
          has in the task's own zone, not in the zone of the viewing device.
        - A date and time without an offset is wall-clock time in ``home_tz``.
        - A value with an offset (or a trailing ``Z``) is already exact and is
          returned unchanged.

        An empty or unknown ``home_tz`` falls back to UTC. Returns None when a
        string cannot be read as a date.
        """
        if value is None:
            return None
        zone = cls.zone_or_none(home_tz) or timezone.utc

        if isinstance(value, datetime):
            parsed: datetime = value
        elif isinstance(value, date):
            return datetime.combine(value, time(0), tzinfo=zone)
        else:
            text = value.strip()
            if _DATE_ONLY.match(text):
                return datetime.combine(date.fromisoformat(text), time(0), tzinfo=zone)
            maybe = cls.parse_datetime(text)
            if maybe is None:
                return None
            parsed = maybe

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=zone)
        return parsed

    @classmethod
    def format_datetime(cls, value: datetime | None, for_api: str = "v2") -> str | None:
        """Format a datetime for API submission."""
        if value is None:
            return None

        # Ensure timezone aware
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)

        if for_api == "v1":
            return value.strftime(DATETIME_FORMAT_V1)
        # DATETIME_FORMAT_V2 hardcodes "+0000" but strftime does not convert
        # the timezone, it just appends the literal suffix. Convert to UTC
        # first so the wall-clock time in the string actually matches +0000.
        # Without this, a datetime like 18:00+02:00 would serialize as
        # "18:00.000+0000" and TickTick would read it as 20:00 Brussels.
        return value.astimezone(timezone.utc).strftime(DATETIME_FORMAT_V2)

    @classmethod
    def from_v1(cls, data: dict[str, Any]) -> Self:
        """Create from V1 API response."""
        instance = cls.model_validate(data)
        return instance

    @classmethod
    def from_v2(cls, data: dict[str, Any]) -> Self:
        """Create from V2 API response."""
        instance = cls.model_validate(data)
        return instance
