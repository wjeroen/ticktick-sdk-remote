"""
Unified Tag Model.

This module provides the canonical Tag model.
Tags are a V2-only feature.
"""

from __future__ import annotations

from typing import Any, Self

from pydantic import Field

from ticktick_sdk.models.base import TickTickModel
from ticktick_sdk.models.project import SortOption


class Tag(TickTickModel):
    """
    Tag model.

    Tags are a V2-only feature for organizing tasks.
    """

    # Identifiers
    name: str  # Lowercase identifier
    label: str  # Display name
    raw_name: str | None = Field(default=None, alias="rawName")
    etag: str | None = None

    # Appearance
    color: str | None = None

    # Hierarchy
    parent: str | None = None

    # Sorting
    sort_option: SortOption | None = Field(default=None, alias="sortOption")
    sort_type: str | None = Field(default=None, alias="sortType")
    sort_order: int | None = Field(default=None, alias="sortOrder")

    # Other
    type: int | None = None

    @classmethod
    def create(
        cls,
        label: str,
        color: str | None = None,
        parent: str | None = None,
    ) -> Self:
        """Create a new tag with auto-generated name."""
        name = label.lower().replace(" ", "")
        return cls(
            name=name,
            label=label,
            color=color,
            parent=parent,
        )

    @classmethod
    def from_v2(cls, data: dict[str, Any]) -> Self:
        """Create from V2 API response."""
        return cls.model_validate(data)
