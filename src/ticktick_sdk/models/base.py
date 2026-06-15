"""
Base model functionality for TickTick unified models.

This module provides the base model class with common configuration
and utility methods used by all unified models.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, field_validator

from ticktick_sdk.constants import DATETIME_FORMAT_V1, DATETIME_FORMAT_V2


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
        # the timezone — it just appends the literal suffix. Convert to UTC
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
