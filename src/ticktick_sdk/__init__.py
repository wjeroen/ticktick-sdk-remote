"""
ticktick-sdk - A comprehensive Python SDK for TickTick with MCP server support.

This package provides:
1. A full-featured async Python client for the TickTick API
2. An MCP (Model Context Protocol) server for AI assistant integration

The library combines both the official V1 (OAuth2) API and the unofficial V2
(Session) API to provide maximum functionality including tags, folders, focus
tracking, and more features not available in the official API alone.

Quick Start (Python Library):
    ```python
    from ticktick_sdk import TickTickClient

    async with TickTickClient.from_settings() as client:
        # Get all tasks
        tasks = await client.get_all_tasks()

        # Create a task
        task = await client.create_task(
            title="Buy groceries",
            due_date="2025-01-20T17:00:00",
            tags=["shopping"],
        )

        # Complete a task
        await client.complete_task(task.id, task.project_id)
    ```

Quick Start (MCP Server):
    ```bash
    # Run the MCP server
    ticktick-sdk
    ```

Architecture:
    ┌─────────────────────────────────────┐
    │  Your Application / MCP Server      │
    └─────────────────┬───────────────────┘
                      │
    ┌─────────────────▼───────────────────┐
    │       TickTickClient                │
    │   (High-level, user-friendly API)   │
    └─────────────────┬───────────────────┘
                      │
    ┌─────────────────▼───────────────────┐
    │        UnifiedTickTickAPI           │
    │    (Version routing & conversion)   │
    └─────────────────┬───────────────────┘
                      │
           ┌──────────┴──────────┐
           ▼                     ▼
    ┌──────────────┐     ┌──────────────┐
    │   V1 API     │     │   V2 API     │
    │  (OAuth2)    │     │  (Session)   │
    └──────────────┘     └──────────────┘

See the README for full documentation.
"""

__version__ = "0.4.3"
__author__ = "dev-mirzabicer"

# Main client - primary entry point for library usage
from ticktick_sdk.client import TickTickClient

# Constants - enums and configuration values
from ticktick_sdk.constants import (
    ProjectKind,
    TaskKind,
    TaskPriority,
    TaskStatus,
    ViewMode,
)

# Exceptions - for error handling
from ticktick_sdk.exceptions import (
    TickTickAPIError,
    TickTickAPIUnavailableError,
    TickTickAuthenticationError,
    TickTickConfigurationError,
    TickTickError,
    TickTickForbiddenError,
    TickTickNotFoundError,
    TickTickOAuthError,
    TickTickQuotaExceededError,
    TickTickRateLimitError,
    TickTickServerError,
    TickTickSessionError,
    TickTickValidationError,
)

# Models - data structures for tasks, projects, tags, habits, etc.
from ticktick_sdk.models import (
    ChecklistItem,
    Column,
    Habit,
    HabitCheckin,
    HabitPreferences,
    HabitSection,
    Project,
    ProjectData,
    ProjectGroup,
    Tag,
    Task,
    TaskReminder,
    User,
    UserStatistics,
    UserStatus,
)

# Settings - configuration management
from ticktick_sdk.settings import TickTickSettings, configure_settings, get_settings

__all__ = [
    # Version
    "__version__",
    # Client
    "TickTickClient",
    # Models
    "Task",
    "ChecklistItem",
    "TaskReminder",
    "Project",
    "ProjectGroup",
    "ProjectData",
    "Column",
    "Tag",
    "User",
    "UserStatus",
    "UserStatistics",
    "Habit",
    "HabitSection",
    "HabitCheckin",
    "HabitPreferences",
    # Exceptions
    "TickTickError",
    "TickTickAuthenticationError",
    "TickTickOAuthError",
    "TickTickSessionError",
    "TickTickAPIError",
    "TickTickValidationError",
    "TickTickRateLimitError",
    "TickTickNotFoundError",
    "TickTickConfigurationError",
    "TickTickForbiddenError",
    "TickTickServerError",
    "TickTickQuotaExceededError",
    "TickTickAPIUnavailableError",
    # Constants
    "TaskStatus",
    "TaskPriority",
    "TaskKind",
    "ProjectKind",
    "ViewMode",
    # Settings
    "TickTickSettings",
    "get_settings",
    "configure_settings",
]
