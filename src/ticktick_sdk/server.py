#!/usr/bin/env python3
"""
TickTick MCP Server - Comprehensive Task Management Integration.

This MCP server provides a complete interface for interacting with TickTick,
combining both V1 (OAuth2) and V2 (Session) APIs for maximum functionality.
It enables AI assistants to manage tasks, projects, tags, and track productivity.

=== CAPABILITIES ===

Task Management:
    - Create tasks with titles, due dates, priorities, tags, reminders, and recurrence
    - Create subtasks (parent-child relationships)
    - Update, complete, delete, and move tasks between projects
    - List active, completed, and overdue tasks
    - Search tasks by title or content

Project Management:
    - Create, read, update, and delete projects
    - Organize projects into folders
    - Get project details with all tasks

Tag Management:
    - Create, rename, merge, and delete tags
    - Tags support hierarchical nesting
    - Apply tags to tasks for organization

User Information:
    - Get user profile and account status
    - Access productivity statistics (completion rates, scores, levels)
    - Track focus/pomodoro sessions

=== TICKTICK API BEHAVIORS ===

IMPORTANT: TickTick has several unique API behaviors that tools account for:

1. SOFT DELETE: Deleting tasks moves them to trash (deleted=1) rather than
   permanently removing them. Deleted tasks remain accessible via get_task.

2. RECURRENCE REQUIRES START_DATE: Creating recurring tasks without a start_date
   silently ignores the recurrence rule. Always provide start_date with recurrence.

3. PARENT-CHILD RELATIONSHIPS: Setting parent_id during task creation is ignored
   by the API. Use the make_subtask tool to establish parent-child relationships.

4. DATE CLEARING: To clear a task's due_date or start_date, you must also clear
   both dates together (TickTick restores due_date from start_date otherwise).

5. TAG ORDER: The API does not preserve tag order - tags may be returned in
   any order regardless of how they were provided.

6. INBOX: The inbox is a special project that cannot be deleted. Its ID is
   available via get_status (inbox_id field).

=== AUTHENTICATION ===

This server requires BOTH V1 and V2 authentication for full functionality:

V1 (OAuth2) - Required for get_project_with_data:
    TICKTICK_CLIENT_ID      - OAuth2 client ID from developer portal
    TICKTICK_CLIENT_SECRET  - OAuth2 client secret
    TICKTICK_ACCESS_TOKEN   - Access token from OAuth2 flow

V2 (Session) - Required for most operations:
    TICKTICK_USERNAME       - TickTick account email
    TICKTICK_PASSWORD       - TickTick account password

Optional:
    TICKTICK_REDIRECT_URI   - OAuth2 redirect URI (default: http://localhost:8080/callback)
    TICKTICK_TIMEOUT        - Request timeout in seconds (default: 30)
    TICKTICK_DEVICE_ID      - Device identifier. STRONGLY RECOMMENDED to set
                              this. If unset, a fresh random id is generated
                              every redeploy, which makes each redeploy look
                              like a new device to TickTick's anti-bot.

=== RESPONSE FORMATS ===

All tools support two response formats via the `response_format` parameter:

- "markdown" (default): Human-readable formatted text with headers, lists, and
  timestamps in readable format. Best for displaying results to users.

- "json": Machine-readable structured data with all available fields.
  Best for programmatic processing or when specific field values are needed.

=== ERROR HANDLING ===

Tools return clear, actionable error messages:
- Authentication errors: Check credentials configuration
- Not found errors: Verify resource ID exists
- Validation errors: Check input parameters
- Rate limit errors: Wait before retrying
- Server errors: Retry or contact support
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any, AsyncIterator
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP, Context
from starlette.requests import Request
from starlette.responses import JSONResponse

from ticktick_sdk.client import TickTickClient
from ticktick_sdk.settings import get_settings
from ticktick_sdk.tools.inputs import (
    ResponseFormat,
    StatisticsSection,
    # Task inputs - list-based for batch operations
    CreateTasksInput,
    TaskGetInput,
    UpdateTasksInput,
    CompleteTasksInput,
    DeleteTasksInput,
    MoveTasksInput,
    SetTaskParentsInput,
    UnparentTasksInput,
    TaskListInput,
    PinTasksInput,
    SearchInput,
    # Project inputs
    ProjectCreateInput,
    ProjectGetInput,
    ProjectDeleteInput,
    ProjectUpdateInput,
    # Folder inputs
    FolderCreateInput,
    FolderDeleteInput,
    FolderRenameInput,
    # Column inputs
    ColumnListInput,
    ColumnCreateInput,
    ColumnUpdateInput,
    ColumnDeleteInput,
    # Tag inputs
    TagCreateInput,
    TagDeleteInput,
    TagMergeInput,
    TagUpdateInput,
    # Focus inputs
    FocusStatsInput,
    # Habit inputs
    HabitListInput,
    HabitGetInput,
    HabitCreateInput,
    HabitUpdateInput,
    HabitDeleteInput,
    CheckinHabitsInput,
    HabitCheckinsInput,
)
from ticktick_sdk.models import Habit, HabitSection
from ticktick_sdk.tools.formatting import (
    format_task_markdown,
    format_task_json,
    format_tasks_markdown,
    format_tasks_json,
    paginate_tasks_markdown,
    paginate_tasks_json,
    paginate_markdown,
    paginate_json,
    paginate_projects_markdown,
    paginate_projects_json,
    paginate_tags_markdown,
    paginate_tags_json,
    paginate_folders_markdown,
    paginate_folders_json,
    paginate_columns_markdown,
    paginate_columns_json,
    LIST_CONTENT_MAX_CHARS,
    format_project_markdown,
    format_project_json,
    format_projects_markdown,
    format_projects_json,
    format_tag_markdown,
    format_tag_json,
    format_tags_markdown,
    format_tags_json,
    format_folders_markdown,
    format_folders_json,
    format_column_markdown,
    format_column_json,
    format_columns_markdown,
    format_columns_json,
    format_user_markdown,
    format_user_status_markdown,
    format_statistics_markdown,
    format_statistics_json,
    format_response,
    success_message,
    error_message,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Load user timezone from settings (set TICKTICK_TIMEZONE env var, e.g. "Europe/Brussels")
USER_TIMEZONE = get_settings().timezone

# =============================================================================
# Constants
# =============================================================================

# Maximum response size in characters to prevent overwhelming context
CHARACTER_LIMIT = 25000

# Default pagination limits
DEFAULT_TASK_LIMIT = 50
DEFAULT_PROJECT_LIMIT = 100
MAX_TASK_LIMIT = 200


# =============================================================================
# Truncation Helper
# =============================================================================


def truncate_response(
    result: str,
    items_count: int,
    truncated_count: int | None = None,
) -> str:
    """
    Truncate response if it exceeds CHARACTER_LIMIT.

    Args:
        result: The formatted response string
        items_count: Total number of items before truncation
        truncated_count: Number of items after truncation (if different)

    Returns:
        Truncated response with guidance message if needed
    """
    if len(result) <= CHARACTER_LIMIT:
        return result

    # Find a good truncation point (after a complete item)
    truncate_at = CHARACTER_LIMIT - 500  # Leave room for message
    truncate_point = result.rfind("\n\n", 0, truncate_at)
    if truncate_point == -1:
        truncate_point = result.rfind("\n", 0, truncate_at)
    if truncate_point == -1:
        truncate_point = truncate_at

    truncated = result[:truncate_point]

    # Add truncation message
    message = (
        f"\n\n---\n"
        f"⚠️ **Response truncated** (exceeded {CHARACTER_LIMIT:,} characters)\n\n"
        f"Showing partial results. To see more:\n"
        f"- Use filters (project_id, tag, priority, due_before/after) to narrow results\n"
        f"- Use the 'limit' parameter to reduce the number of items"
    )

    return truncated + message


# =============================================================================
# Stable Sort Helpers
# =============================================================================

# TickTick's task-list endpoints don't guarantee a stable order between calls,
# so offset-based pagination would otherwise risk duplicates or gaps. We apply
# an explicit sort before paginating.

def _active_sort_key(task) -> tuple:
    """Active tasks: by due_date ascending (None last), then by id."""
    if task.due_date is not None:
        return (0, task.due_date, task.id or "")
    return (1, datetime.max.replace(tzinfo=timezone.utc), task.id or "")


def _completed_sort_key(task) -> tuple:
    """Completed/abandoned tasks: by completed_time descending (None last), then id."""
    if task.completed_time is not None:
        return (0, -task.completed_time.timestamp(), task.id or "")
    return (1, 0.0, task.id or "")


def _id_sort_key(task) -> tuple:
    """Fallback: by id only."""
    return (task.id or "",)


# =============================================================================
# Lifespan Management
# =============================================================================


# A single shared TickTick client per process. With streamable-http, the MCP
# SDK runs the server lifespan once *per session*, not once per process, so
# building the client in the lifespan would re-authenticate on every connection
# — the bug that turned a flaky login into a rate-limit ban. We build it once,
# behind a lock, and reuse it across all sessions.
_shared_client: TickTickClient | None = None
_shared_client_lock = asyncio.Lock()


async def _get_or_create_client() -> TickTickClient:
    """Return the process-wide TickTick client, building + connecting it once."""
    global _shared_client
    async with _shared_client_lock:
        if _shared_client is not None and _shared_client.is_connected:
            return _shared_client

        settings = get_settings()
        # Startup warnings — logged once, on the single build (not per session).
        # TickTick expects a 24-char lowercase-hex ObjectId; a malformed value
        # can make V2 sign-on fail with misleading errors.
        if not settings.device_id_looks_valid:
            logger.warning(
                "TICKTICK_DEVICE_ID=%r does NOT look like a valid 24-char hex "
                "ObjectId (length=%d). TickTick V2 sign-on may reject it. Use a "
                "24-character lowercase-hex value, generate one with: "
                "python -c \"import os; print(os.urandom(12).hex())\".",
                settings.device_id,
                len(settings.device_id),
            )
        else:
            logger.info("TICKTICK_DEVICE_ID format looks valid (24-char hex).")
        if settings.device_id_is_ephemeral:
            logger.warning(
                "TICKTICK_DEVICE_ID is not set. A new device id was auto-generated "
                "for this process: %s. Every redeploy will produce a different id, "
                "which makes TickTick's anti-bot system more likely to flag your "
                "logins. Set TICKTICK_DEVICE_ID in Railway to this value (or any "
                "stable 24-char hex string) to look like one consistent device.",
                settings.device_id,
            )

        client = TickTickClient.from_settings(settings)
        await client.connect()
        _shared_client = client
        logger.info("TickTick client connected (shared, built once per process)")
        return _shared_client


@asynccontextmanager
async def lifespan(mcp: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Provide the shared TickTick client to each MCP session.

    The MCP streamable-http manager enters this lifespan once *per session*. We
    therefore do NOT build or tear down the client here (that would re-auth on
    every connection); we reuse the process-wide client, and if V2 is currently
    degraded we make a single backoff-gated re-auth attempt so a long-running
    process self-heals when a throttle clears.
    """
    logger.info("Initializing TickTick MCP session...")
    client = await _get_or_create_client()
    # Health tick: cheap no-op when V2 is healthy or within the backoff window.
    try:
        await client.ensure_v2_fresh()
    except Exception as e:  # never let a recovery attempt break a session
        logger.warning("V2 re-auth attempt errored (continuing degraded): %s", e)
    yield {"client": client}
    # Intentionally no disconnect: the shared client lives for the process and is
    # reused by later sessions; it is reclaimed when the process exits.


# Initialize FastMCP server
mcp = FastMCP(
    "ticktick_sdk",
    lifespan=lifespan,
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8000")),
    streamable_http_path="/mcp",
)


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    """Health check endpoint for Railway and other deployment platforms."""
    return JSONResponse({"status": "ok"})


def get_client(ctx: Context) -> TickTickClient:
    """Get the TickTick client from context."""
    return ctx.request_context.lifespan_context["client"]


async def build_project_name_map(
    client: TickTickClient, tasks: list
) -> dict[str, str] | None:
    """Return {project_id: name} for the formatter when tasks span >1 project.

    Returns None when all tasks share a project (the per-row Project badge would
    be redundant noise) or the list is empty. One extra API call per render —
    `format_tasks_markdown` will use this to add a `| Project: <name>` suffix.
    """
    distinct = {t.project_id for t in tasks if t.project_id}
    if len(distinct) <= 1:
        return None
    projects = await client.get_all_projects()
    return {p.id: p.name for p in projects}


async def build_child_meta_for_task(
    client: TickTickClient, task
) -> dict[str, dict[str, Any]] | None:
    """Detail-view helper: fetch each child task in parallel so the
    formatter can show title + priority for every subtask.

    Returns None when the task has no children. Failed fetches are skipped
    silently — the formatter will fall back to bare IDs for those.
    """
    if not task.child_ids:
        return None
    pid = task.project_id
    results = await asyncio.gather(
        *[client.get_task(cid, pid) for cid in task.child_ids],
        return_exceptions=True,
    )
    meta: dict[str, dict[str, Any]] = {}
    for cid, child in zip(task.child_ids, results):
        if isinstance(child, Exception):
            continue
        meta[cid] = {"title": child.title, "priority": child.priority}
    return meta


async def build_project_name_for_task(
    client: TickTickClient, task
) -> dict[str, str] | None:
    """Single-project lookup for detail-view rendering.

    Returns {project_id: name} for the one project the task belongs to, or
    None if lookup fails / no project_id. `format_task_markdown` falls back
    to ID-only display when the map is None, so failures here are benign.
    """
    if not task.project_id:
        return None
    try:
        project = await client.get_project(task.project_id)
        return {project.id: project.name}
    except Exception:
        return None


# =============================================================================
# Error Handling
# =============================================================================


def handle_error(e: Exception, operation: str) -> str:
    """
    Handle exceptions and return user-friendly, actionable error messages.

    Error messages include:
    1. What went wrong
    2. Why it might have happened
    3. Specific steps to resolve the issue
    """
    logger.exception("Error in %s: %s", operation, e)

    error_type = type(e).__name__
    error_str = str(e)

    if "Authentication" in error_type:
        return error_message(
            "Authentication failed",
            "NEXT STEPS:\n"
            "1. Verify environment variables are set:\n"
            "   - TICKTICK_CLIENT_ID (OAuth2 client ID)\n"
            "   - TICKTICK_CLIENT_SECRET (OAuth2 client secret)\n"
            "   - TICKTICK_ACCESS_TOKEN (OAuth2 access token)\n"
            "   - TICKTICK_USERNAME (TickTick account email)\n"
            "   - TICKTICK_PASSWORD (TickTick account password)\n"
            "2. Check that credentials are not expired\n"
            "3. Re-run OAuth2 flow if access token is invalid"
        )
    elif "NotFound" in error_type:
        resource_hint = ""
        if "task" in error_str.lower():
            resource_hint = (
                "HINTS:\n"
                "- Task may have been permanently deleted (not just trashed)\n"
                "- Use ticktick_list_tasks to see available tasks\n"
                "- Check if the task ID is correct"
            )
        elif "project" in error_str.lower():
            resource_hint = (
                "HINTS:\n"
                "- Use ticktick_list_projects to see available projects\n"
                "- The inbox project ID can be obtained from ticktick_get_status"
            )
        elif "tag" in error_str.lower():
            resource_hint = (
                "HINTS:\n"
                "- Use ticktick_list_tags to see available tags\n"
                "- Tag names are case-insensitive"
            )
        elif "folder" in error_str.lower() or "group" in error_str.lower():
            resource_hint = (
                "HINTS:\n"
                "- Use ticktick_list_folders to see available folders"
            )
        return error_message(
            f"Resource not found: {error_str}",
            resource_hint or "Verify the ID is correct and the resource exists."
        )
    elif "Validation" in error_type:
        return error_message(
            f"Invalid input: {error_str}",
            "Check the parameter types and constraints in the tool documentation."
        )
    elif "Configuration" in error_type:
        if "recurrence" in error_str.lower() and "start_date" in error_str.lower():
            return error_message(
                f"Configuration error: {error_str}",
                "TICKTICK REQUIREMENT: Recurring tasks require a start_date.\n"
                "Add a start_date parameter when setting recurrence rules."
            )
        return error_message(
            f"Configuration error: {error_str}",
            "Check your environment variables and tool parameters."
        )
    elif "RateLimit" in error_type:
        return error_message(
            "Rate limit exceeded",
            "NEXT STEPS:\n"
            "1. Wait 30-60 seconds before retrying\n"
            "2. Reduce the frequency of API calls\n"
            "3. Batch operations where possible"
        )
    elif "Quota" in error_type:
        return error_message(
            "Account quota exceeded",
            "HINTS:\n"
            "- Free accounts have limited projects/tasks\n"
            "- Delete unused projects or upgrade to Pro"
        )
    elif "Forbidden" in error_type:
        return error_message(
            f"Access denied: {error_str}",
            "You don't have permission to access this resource.\n"
            "Check if you're the owner or have appropriate sharing permissions."
        )
    elif "Server" in error_type:
        return error_message(
            f"TickTick server error: {error_str}",
            "NEXT STEPS:\n"
            "1. Wait a moment and retry the operation\n"
            "2. Check if TickTick service is operational\n"
            "3. Try with different parameters if the issue persists"
        )
    else:
        return error_message(
            f"Unexpected error: {error_str}",
            f"Error type: {error_type}\n"
            "If this persists, check the server logs for more details."
        )


# =============================================================================
# Task Tools
# =============================================================================


@mcp.tool(
    name="ticktick_create_tasks",
    annotations={
        "title": "Create Tasks",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def ticktick_create_tasks(params: CreateTasksInput, ctx: Context) -> str:
    """
    Create one or more tasks in TickTick.

    Creates tasks with specified properties. Supports batch creation (1-50 tasks).

    IMPORTANT BEHAVIORS:
    - If no project_id is specified, tasks are created in the inbox
    - RECURRENCE REQUIRES start_date: Recurrence rules without start_date are ignored
    - parent_id is supported - the SDK handles parent assignment after creation
    - Tags are created automatically if they don't exist

    Args:
        params: Task creation parameters:
            - tasks (list, required): List of task specifications (1-50 tasks)
              Each task must contain:
                - title (str, required): Task title
              Optional fields:
                - project_id (str): Project ID (defaults to inbox)
                - content (str): Task notes/description
                - description (str): Checklist description (for CHECKLIST kind)
                - kind (str): Task type - 'TEXT' (standard task, default), 'NOTE' (note),
                  or 'CHECKLIST' (checklist with subtask items)
                - priority (str): 'none', 'low', 'medium', 'high'
                - start_date (str): Start date in ISO format (REQUIRED for recurrence)
                - due_date (str): Due date in ISO format
                - time_zone (str): IANA timezone (e.g., 'America/New_York')
                - all_day (bool): Whether task is all-day (no specific time)
                - tags (list[str]): Tag names to apply
                - reminders (list[str]): Reminder triggers in iCal format (e.g., 'TRIGGER:-PT30M')
                - recurrence (str): RRULE format (e.g., 'RRULE:FREQ=DAILY')
                - parent_id (str): Parent task ID to make this a subtask
            - response_format (str): 'markdown' (default) or 'json'

    Returns:
        On success: Summary of created tasks with details
        On error: Error message with hints for resolution

    Examples:
        Simple task:
            tasks=[{"title": "Buy groceries"}]

        Task with priority and tags:
            tasks=[{"title": "Review PR", "priority": "high", "tags": ["work", "urgent"]}]

        Note task (different from standard task):
            tasks=[{"title": "Meeting notes", "kind": "NOTE", "content": "Discussion points..."}]

        Checklist task:
            tasks=[{"title": "Packing list", "kind": "CHECKLIST"}]

        Recurring task (requires start_date):
            tasks=[{"title": "Daily standup", "start_date": "2026-01-20", "recurrence": "RRULE:FREQ=DAILY"}]

        Subtask:
            tasks=[{"title": "Subtask", "parent_id": "parent_task_id", "project_id": "proj_id"}]
    """
    try:
        client = get_client(ctx)

        # Build task specifications
        task_specs = []
        for task_item in params.tasks:
            spec: dict[str, Any] = {"title": task_item.title}

            if task_item.project_id:
                spec["project_id"] = task_item.project_id
            if task_item.content:
                spec["content"] = task_item.content
            if task_item.description:
                spec["description"] = task_item.description
            if task_item.priority:
                spec["priority"] = task_item.priority
            if task_item.start_date:
                spec["start_date"] = task_item.start_date
            if task_item.due_date:
                spec["due_date"] = task_item.due_date
            if task_item.time_zone:
                spec["time_zone"] = task_item.time_zone
            if task_item.all_day is not None:
                spec["all_day"] = task_item.all_day
            if task_item.tags:
                spec["tags"] = task_item.tags
            if task_item.reminders:
                spec["reminders"] = task_item.reminders
            if task_item.recurrence:
                spec["recurrence"] = task_item.recurrence
            if task_item.parent_id:
                spec["parent_id"] = task_item.parent_id
            if task_item.kind:
                spec["kind"] = task_item.kind

            task_specs.append(spec)

        created_tasks = await client.create_tasks(task_specs)

        if params.response_format == ResponseFormat.MARKDOWN:
            if len(created_tasks) == 1:
                project_names = await build_project_name_for_task(client, created_tasks[0])
                return (
                    f"# Task Created\n\n"
                    f"{format_task_markdown(created_tasks[0], USER_TIMEZONE, project_names=project_names)}"
                )
            else:
                project_names = await build_project_name_map(client, created_tasks)
                return (
                    f"# {len(created_tasks)} Tasks Created\n\n"
                    f"{format_tasks_markdown(created_tasks, 'Created Tasks', USER_TIMEZONE, project_names=project_names)}"
                )
        else:
            return json.dumps({
                "success": True,
                "count": len(created_tasks),
                "tasks": [
                    format_task_json(t, USER_TIMEZONE, content_max_chars=LIST_CONTENT_MAX_CHARS)
                    for t in created_tasks
                ],
            }, indent=2)

    except Exception as e:
        return handle_error(e, "create_tasks")


@mcp.tool(
    name="ticktick_get_task",
    annotations={
        "title": "Get Task",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_get_task(params: TaskGetInput, ctx: Context) -> str:
    """
    Get a task by its ID.

    Retrieves full details of a specific task including title, content,
    kind (TEXT/NOTE/CHECKLIST), due date, priority, tags, subtasks, and status.

    Args:
        params: Query parameters:
            - task_id (str, required): Task identifier
            - project_id (str): Project ID (optional, used for V1 fallback)
            - response_format (str): 'markdown' (default) or 'json'

    Returns:
        Task details including: id, project_id, title, content, kind, status,
        priority, dates, tags, parent_id, child_ids, and checklist items.
    """
    try:
        client = get_client(ctx)
        task = await client.get_task(params.task_id, params.project_id)

        # Fetch child meta and project name concurrently — the children
        # call is N parallel get_tasks; both are independent of each other.
        child_meta, project_names = await asyncio.gather(
            build_child_meta_for_task(client, task),
            build_project_name_for_task(client, task),
        )

        if params.response_format == ResponseFormat.MARKDOWN:
            return format_task_markdown(
                task,
                USER_TIMEZONE,
                project_names=project_names,
                child_meta=child_meta,
            )
        else:
            return json.dumps(
                format_task_json(task, USER_TIMEZONE, child_meta=child_meta),
                indent=2,
            )

    except Exception as e:
        return handle_error(e, "get_task")


@mcp.tool(
    name="ticktick_list_tasks",
    annotations={
        "title": "List Tasks",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_list_tasks(params: TaskListInput, ctx: Context) -> str:
    """
    List tasks with flexible filtering.

    This unified tool handles all task listing scenarios:
    - Active tasks (default)
    - Completed tasks (with date range)
    - Abandoned tasks (with date range)
    - Deleted/trashed tasks

    Args:
        params: Filter parameters:
            - status (str): 'active' (default), 'completed', 'abandoned', 'deleted'
            - project_id (str): Filter by project
            - column_id (str): Filter by kanban column (active only, use with project_id)
            - tag (str): Filter by tag name
            - priority (str): Filter by priority level
            - due_today (bool): Only tasks due today (active only, uses TICKTICK_TIMEZONE)
            - overdue (bool): Only overdue tasks (active only, uses TICKTICK_TIMEZONE)
            - due_before (str): Active tasks due on or before this date, e.g. '2026-03-16' (uses TICKTICK_TIMEZONE)
            - due_after (str): Active tasks due on or after this date, e.g. '2026-03-16' (uses TICKTICK_TIMEZONE).
              Combine with due_before for a date range (e.g. due_after='2026-03-16' + due_before='2026-03-20'
              returns tasks due March 16-20 inclusive).
            - has_due_date (bool): If true, only tasks with a due date. If false, only tasks without one
              (good for finding ad-hoc/unscheduled tasks). Omit for no filtering.
            - from_date (str): Start date for completed/abandoned (YYYY-MM-DD)
            - to_date (str): End date for completed/abandoned (YYYY-MM-DD)
            - days (int): Days to look back for completed/abandoned (default 7)
            - limit (int): Maximum results (default 50)

    Returns:
        Formatted list of tasks or error message.

    Examples:
        - Active tasks: status="active" (or just omit status)
        - Completed last 7 days: status="completed"
        - Completed in range: status="completed", from_date="2026-01-01", to_date="2026-01-15"
        - Abandoned tasks: status="abandoned", days=30
        - Deleted tasks: status="deleted"
        - Active + project: status="active", project_id="..."
        - Tasks in column: project_id="...", column_id="..." (kanban workflow)
        - Due in next 3 days: status="active", due_before="2026-03-16"
        - Due from a specific date: status="active", due_after="2026-03-16"
        - Due in a date range: status="active", due_after="2026-03-16", due_before="2026-03-20"
        - Unscheduled tasks: status="active", has_due_date=False
        - Only tasks with a due date: status="active", has_due_date=True
    """
    try:
        client = get_client(ctx)

        # Handle different status types
        all_child_meta: dict[str, dict[str, Any]] | None = None
        if params.status == "active":
            tasks = await client.get_all_tasks()
            # Capture {id: {title, priority}} from the FULL list before
            # filtering, so subtasks resolve to title + priority even when
            # filtered out (e.g. subtasks with no due date won't appear in a
            # "due today" result, but the parent's children field still
            # shows them).
            all_child_meta = {
                t.id: {"title": t.title, "priority": t.priority}
                for t in tasks if t.id
            }

            # Apply active-only filters
            if params.project_id:
                tasks = [t for t in tasks if t.project_id == params.project_id]

            if params.column_id:
                tasks = [t for t in tasks if t.column_id == params.column_id]

            if params.tag:
                tag_lower = params.tag.lower()
                tasks = [t for t in tasks if any(tag.lower() == tag_lower for tag in t.tags)]

            if params.priority:
                priority_map = {"none": 0, "low": 1, "medium": 3, "high": 5}
                target_priority = priority_map.get(params.priority, 0)
                tasks = [t for t in tasks if t.priority == target_priority]

            if params.due_today:
                today = datetime.now(ZoneInfo(USER_TIMEZONE)).date()
                tasks = [t for t in tasks if t.due_date and t.due_date.astimezone(ZoneInfo(USER_TIMEZONE)).date() == today]

            if params.overdue:
                today = datetime.now(ZoneInfo(USER_TIMEZONE)).date()
                tasks = [t for t in tasks if t.due_date and t.due_date.astimezone(ZoneInfo(USER_TIMEZONE)).date() < today and not t.is_completed]

            if params.due_before:
                due_before_date = date.fromisoformat(params.due_before)
                tasks = [t for t in tasks if t.due_date and t.due_date.astimezone(ZoneInfo(USER_TIMEZONE)).date() <= due_before_date]

            if params.due_after:
                due_after_date = date.fromisoformat(params.due_after)
                tasks = [t for t in tasks if t.due_date and t.due_date.astimezone(ZoneInfo(USER_TIMEZONE)).date() >= due_after_date]

            if params.has_due_date is True:
                tasks = [t for t in tasks if t.due_date is not None]
            elif params.has_due_date is False:
                tasks = [t for t in tasks if t.due_date is None]

        elif params.status == "completed":
            if params.from_date and params.to_date:
                # Interpret naive YYYY-MM-DD strings as full local-TZ days:
                # from_date starts at 00:00, to_date ends at 23:59:59.
                tz = ZoneInfo(USER_TIMEZONE)
                from_dt = datetime.fromisoformat(params.from_date).replace(tzinfo=tz)
                to_dt = datetime.fromisoformat(params.to_date).replace(
                    hour=23, minute=59, second=59, tzinfo=tz,
                )
                tasks = await client.get_completed_tasks(
                    limit=params.limit, from_date=from_dt, to_date=to_dt,
                )
            else:
                tasks = await client.get_completed_tasks(days=params.days, limit=params.limit)

        elif params.status == "abandoned":
            if params.from_date and params.to_date:
                tz = ZoneInfo(USER_TIMEZONE)
                from_dt = datetime.fromisoformat(params.from_date).replace(tzinfo=tz)
                to_dt = datetime.fromisoformat(params.to_date).replace(
                    hour=23, minute=59, second=59, tzinfo=tz,
                )
                tasks = await client.get_abandoned_tasks(
                    limit=params.limit, from_date=from_dt, to_date=to_dt,
                )
            else:
                tasks = await client.get_abandoned_tasks(days=params.days, limit=params.limit)

        elif params.status == "deleted":
            tasks = await client.get_deleted_tasks(limit=params.limit)

        else:
            tasks = await client.get_all_tasks()

        # Deterministic sort so paginated calls return a stable order
        # (TickTick's list endpoints don't guarantee one).
        if params.status == "active":
            tasks.sort(key=_active_sort_key)
        elif params.status in ("completed", "abandoned"):
            tasks.sort(key=_completed_sort_key)
        else:
            tasks.sort(key=_id_sort_key)

        # Cap consideration to offset + limit so the requested offset can
        # always reach into the window. (Earlier `tasks[:limit]` was buggy:
        # with limit=50 and offset=60, the slice threw away the very tasks
        # the offset was asking for, yielding 0 results.)
        tasks = tasks[: params.offset + params.limit]

        if params.response_format == ResponseFormat.MARKDOWN:
            title = f"{params.status.capitalize()} Tasks" if params.status else "Tasks"
            project_names = await build_project_name_map(client, tasks)
            return paginate_tasks_markdown(
                tasks,
                title=title,
                offset=params.offset,
                tz_name=USER_TIMEZONE,
                project_names=project_names,
                child_meta=all_child_meta,
            )
        else:
            return json.dumps(
                paginate_tasks_json(
                    tasks,
                    offset=params.offset,
                    tz_name=USER_TIMEZONE,
                    child_meta=all_child_meta,
                ),
                indent=2,
            )

    except Exception as e:
        return handle_error(e, "list_tasks")


@mcp.tool(
    name="ticktick_update_tasks",
    annotations={
        "title": "Update Tasks",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_update_tasks(params: UpdateTasksInput, ctx: Context) -> str:
    """
    Update one or more tasks.

    Updates specified fields of tasks. Supports batch updates (1-100 tasks).
    Each update preserves unspecified fields (only specified fields are changed).

    Args:
        params: Update parameters:
            - tasks (list, required): List of update specifications (1-100 tasks)
              Each update must contain:
                - task_id (str, required): Task to update
                - project_id (str, required): Project containing the task
              Optional update fields:
                - title (str): New title
                - content (str): New content/notes
                - kind (str): Change task type - 'TEXT', 'NOTE', or 'CHECKLIST'
                - priority (str): 'none', 'low', 'medium', 'high'
                - start_date (str): Start date in ISO format
                - due_date (str): Due date in ISO format
                - time_zone (str): IANA timezone (e.g., 'America/New_York')
                - all_day (bool): Whether task is all-day
                - tags (list[str]): New tags (replaces existing tags)
                - recurrence (str): RRULE format for recurring tasks
                - column_id (str): Kanban column ID for board assignment
                  (use empty string '' to remove from column)
            - response_format (str): 'markdown' (default) or 'json'

    Returns:
        Summary of updated tasks or error message.

    Examples:
        Update priority:
            tasks=[{"task_id": "abc123", "project_id": "proj1", "priority": "high"}]

        Convert task to note:
            tasks=[{"task_id": "abc123", "project_id": "proj1", "kind": "NOTE"}]

        Assign to kanban column:
            tasks=[{"task_id": "abc1", "project_id": "proj1", "column_id": "col123"}]

        Remove from kanban column:
            tasks=[{"task_id": "abc1", "project_id": "proj1", "column_id": ""}]

        Batch update multiple tasks:
            tasks=[
                {"task_id": "abc1", "project_id": "proj1", "title": "Updated 1"},
                {"task_id": "abc2", "project_id": "proj1", "priority": "low"}
            ]
    """
    try:
        client = get_client(ctx)

        # Build update specifications
        update_specs = []
        for task_item in params.tasks:
            spec: dict[str, Any] = {
                "task_id": task_item.task_id,
                "project_id": task_item.project_id,
            }

            if task_item.title is not None:
                spec["title"] = task_item.title
            if task_item.content is not None:
                spec["content"] = task_item.content
            if task_item.priority is not None:
                spec["priority"] = task_item.priority
            if task_item.start_date is not None:
                spec["start_date"] = task_item.start_date
            if task_item.due_date is not None:
                spec["due_date"] = task_item.due_date
            if task_item.all_day is not None:
                spec["all_day"] = task_item.all_day
            if task_item.time_zone is not None:
                spec["time_zone"] = task_item.time_zone
            if task_item.tags is not None:
                spec["tags"] = task_item.tags
            if task_item.recurrence is not None:
                spec["recurrence"] = task_item.recurrence
            if task_item.column_id is not None:
                spec["column_id"] = task_item.column_id
            if task_item.kind is not None:
                spec["kind"] = task_item.kind

            update_specs.append(spec)

        response = await client.update_tasks(update_specs)

        if params.response_format == ResponseFormat.MARKDOWN:
            count = len(update_specs)
            if count == 1:
                return f"# Task Updated\n\nSuccessfully updated task `{update_specs[0]['task_id']}`"
            else:
                return f"# {count} Tasks Updated\n\nSuccessfully updated {count} tasks."
        else:
            return json.dumps({
                "success": True,
                "count": len(update_specs),
                "response": response
            }, indent=2)

    except Exception as e:
        return handle_error(e, "update_tasks")


@mcp.tool(
    name="ticktick_complete_tasks",
    annotations={
        "title": "Complete Tasks",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_complete_tasks(params: CompleteTasksInput, ctx: Context) -> str:
    """
    Complete one or more tasks.

    Changes task status to completed and records completion time.
    Supports batch completion (1-100 tasks).

    Args:
        params: Completion parameters:
            - tasks (list, required): List of task identifiers (1-100 tasks)
              Each task must contain:
                - task_id (str): Task to complete
                - project_id (str): Project containing the task
            - response_format (str): 'markdown' (default) or 'json'

    Returns:
        Success confirmation or error message.

    Examples:
        Single task:
            tasks=[{"task_id": "abc123", "project_id": "proj1"}]

        Multiple tasks:
            tasks=[
                {"task_id": "abc1", "project_id": "proj1"},
                {"task_id": "abc2", "project_id": "proj1"}
            ]
    """
    try:
        client = get_client(ctx)
        task_ids = [(t.task_id, t.project_id) for t in params.tasks]
        await client.complete_tasks(task_ids)

        count = len(task_ids)
        if count == 1:
            return success_message(f"Task `{task_ids[0][0]}` marked as complete.")
        else:
            return success_message(f"{count} tasks marked as complete.")

    except Exception as e:
        return handle_error(e, "complete_tasks")


@mcp.tool(
    name="ticktick_delete_tasks",
    annotations={
        "title": "Delete Tasks",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_delete_tasks(params: DeleteTasksInput, ctx: Context) -> str:
    """
    Delete one or more tasks.

    Moves tasks to trash. Supports batch deletion (1-100 tasks).
    Can be undone from the TickTick trash.

    Args:
        params: Deletion parameters:
            - tasks (list, required): List of task identifiers (1-100 tasks)
              Each task must contain:
                - task_id (str): Task to delete
                - project_id (str): Project containing the task
            - response_format (str): 'markdown' (default) or 'json'

    Returns:
        Success confirmation or error message.
    """
    try:
        client = get_client(ctx)
        task_ids = [(t.task_id, t.project_id) for t in params.tasks]
        await client.delete_tasks(task_ids)

        count = len(task_ids)
        if count == 1:
            return success_message(f"Task `{task_ids[0][0]}` deleted.")
        else:
            return success_message(f"{count} tasks deleted.")

    except Exception as e:
        return handle_error(e, "delete_tasks")


@mcp.tool(
    name="ticktick_move_tasks",
    annotations={
        "title": "Move Tasks",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_move_tasks(params: MoveTasksInput, ctx: Context) -> str:
    """
    Move one or more tasks to different projects.

    Transfers tasks between projects while preserving all properties.
    Supports batch moves (1-100 tasks).

    Args:
        params: Move parameters:
            - moves (list, required): List of move specifications (1-100)
              Each move must contain:
                - task_id (str): Task to move
                - from_project_id (str): Source project
                - to_project_id (str): Destination project
            - response_format (str): 'markdown' (default) or 'json'

    Returns:
        Success confirmation or error message.

    Examples:
        Single task:
            moves=[{"task_id": "abc123", "from_project_id": "proj1", "to_project_id": "proj2"}]

        Multiple tasks to same project:
            moves=[
                {"task_id": "abc1", "from_project_id": "proj1", "to_project_id": "proj2"},
                {"task_id": "abc2", "from_project_id": "proj1", "to_project_id": "proj2"}
            ]
    """
    try:
        client = get_client(ctx)
        move_specs = [{
            "task_id": m.task_id,
            "from_project_id": m.from_project_id,
            "to_project_id": m.to_project_id,
        } for m in params.moves]
        await client.move_tasks(move_specs)

        count = len(move_specs)
        if count == 1:
            return success_message(f"Task `{move_specs[0]['task_id']}` moved to project `{move_specs[0]['to_project_id']}`.")
        else:
            return success_message(f"{count} tasks moved.")

    except Exception as e:
        return handle_error(e, "move_tasks")


@mcp.tool(
    name="ticktick_set_task_parents",
    annotations={
        "title": "Set Task Parents",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_set_task_parents(params: SetTaskParentsInput, ctx: Context) -> str:
    """
    Make one or more tasks into subtasks.

    Creates parent-child relationships between tasks. Child tasks will
    appear nested under their parent. Supports batch operations (1-50 tasks).

    Args:
        params: Parent assignment parameters:
            - tasks (list, required): List of parent assignments (1-50)
              Each assignment must contain:
                - task_id (str): Task to make a subtask
                - project_id (str): Project containing both tasks
                - parent_id (str): Parent task ID
            - response_format (str): 'markdown' (default) or 'json'

    Returns:
        Success confirmation or error message.

    Examples:
        Single subtask:
            tasks=[{"task_id": "child1", "project_id": "proj1", "parent_id": "parent1"}]

        Multiple subtasks under same parent:
            tasks=[
                {"task_id": "child1", "project_id": "proj1", "parent_id": "parent1"},
                {"task_id": "child2", "project_id": "proj1", "parent_id": "parent1"}
            ]
    """
    try:
        client = get_client(ctx)
        assignments = [{
            "task_id": t.task_id,
            "project_id": t.project_id,
            "parent_id": t.parent_id,
        } for t in params.tasks]
        await client.set_task_parents(assignments)

        count = len(assignments)
        if count == 1:
            return success_message(f"Task `{assignments[0]['task_id']}` is now a subtask of `{assignments[0]['parent_id']}`.")
        else:
            return success_message(f"{count} tasks assigned as subtasks.")

    except Exception as e:
        return handle_error(e, "set_task_parents")


@mcp.tool(
    name="ticktick_unparent_tasks",
    annotations={
        "title": "Unparent Tasks",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_unparent_tasks(params: UnparentTasksInput, ctx: Context) -> str:
    """
    Remove one or more tasks from their parents.

    Converts subtasks back into top-level tasks. Supports batch
    operations (1-50 tasks).

    Args:
        params: Unparent parameters:
            - tasks (list, required): List of tasks to unparent (1-50)
              Each task must contain:
                - task_id (str): Subtask to unparent
                - project_id (str): Project containing the task
            - response_format (str): 'markdown' (default) or 'json'

    Returns:
        Success confirmation or error message.

    Raises:
        Error if a task is not a subtask (has no parent).
    """
    try:
        client = get_client(ctx)
        unparent_specs = [{
            "task_id": t.task_id,
            "project_id": t.project_id,
        } for t in params.tasks]
        await client.unparent_tasks(unparent_specs)

        count = len(unparent_specs)
        if count == 1:
            return success_message(f"Task `{unparent_specs[0]['task_id']}` is now a top-level task.")
        else:
            return success_message(f"{count} tasks are now top-level tasks.")

    except Exception as e:
        return handle_error(e, "unparent_tasks")


@mcp.tool(
    name="ticktick_search_tasks",
    annotations={
        "title": "Search Tasks",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_search_tasks(params: SearchInput, ctx: Context) -> str:
    """
    Search for tasks by title or content.

    Performs a text search across all active tasks, matching the query
    against task titles and content.

    Args:
        params: Search parameters:
            - query (str): Search query (required)
            - limit (int): Maximum results (default 20)

    Returns:
        Formatted list of matching tasks or error message.

    Examples:
        - Search by keyword: query="meeting"
        - Search by phrase: query="quarterly report"
    """
    try:
        client = get_client(ctx)
        tasks = await client.search_tasks(params.query)
        tasks.sort(key=_active_sort_key)
        tasks = tasks[: params.offset + params.limit]

        title = f"Search Results: '{params.query}'"

        if params.response_format == ResponseFormat.MARKDOWN:
            project_names = await build_project_name_map(client, tasks)
            return paginate_tasks_markdown(
                tasks,
                title=title,
                offset=params.offset,
                tz_name=USER_TIMEZONE,
                project_names=project_names,
            )
        else:
            return json.dumps(
                paginate_tasks_json(tasks, offset=params.offset, tz_name=USER_TIMEZONE),
                indent=2,
            )

    except Exception as e:
        return handle_error(e, "search_tasks")


# =============================================================================
# Task Pinning Tools
# =============================================================================


@mcp.tool(
    name="ticktick_pin_tasks",
    annotations={
        "title": "Pin/Unpin Tasks",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ticktick_pin_tasks(params: PinTasksInput, ctx: Context) -> str:
    """
    Pin or unpin one or more tasks.

    Pinned tasks appear at the top of task lists in TickTick.
    Supports batch operations (1-50 tasks).

    Args:
        params: Pin operation parameters:
            - tasks (list, required): List of pin operations (1-50 tasks)
              Each operation must contain:
                - task_id (str): Task to pin/unpin
                - project_id (str): Project containing the task
                - pin (bool): True to pin, False to unpin (default True)
            - response_format (str): 'markdown' (default) or 'json'

    Returns:
        Success confirmation with updated task details.

    Examples:
        Pin single task:
            tasks=[{"task_id": "abc123", "project_id": "proj1", "pin": true}]

        Unpin task:
            tasks=[{"task_id": "abc123", "project_id": "proj1", "pin": false}]

        Pin multiple:
            tasks=[
                {"task_id": "abc1", "project_id": "proj1", "pin": true},
                {"task_id": "abc2", "project_id": "proj1", "pin": true}
            ]
    """
    try:
        client = get_client(ctx)
        pin_specs = [{
            "task_id": t.task_id,
            "project_id": t.project_id,
            "pin": t.pin,
        } for t in params.tasks]

        updated_tasks = await client.pin_tasks(pin_specs)

        count = len(updated_tasks)
        if count == 1:
            task = updated_tasks[0]
            action = "pinned" if params.tasks[0].pin else "unpinned"
            if params.response_format == ResponseFormat.MARKDOWN:
                project_names = await build_project_name_for_task(client, task)
                return (
                    f"**Success**: Task '{task.title}' has been {action}.\n\n"
                    + format_task_markdown(task, USER_TIMEZONE, project_names=project_names)
                )
            else:
                result = format_task_json(task, USER_TIMEZONE)
                result["action"] = action
                return json.dumps(result, indent=2, default=str)
        else:
            if params.response_format == ResponseFormat.MARKDOWN:
                project_names = await build_project_name_map(client, updated_tasks)
                return (
                    f"**Success**: {count} tasks updated.\n\n"
                    f"{format_tasks_markdown(updated_tasks, tz_name=USER_TIMEZONE, project_names=project_names)}"
                )
            else:
                return json.dumps({
                    "success": True,
                    "count": count,
                    "tasks": [
                        format_task_json(t, USER_TIMEZONE, content_max_chars=LIST_CONTENT_MAX_CHARS)
                        for t in updated_tasks
                    ],
                }, indent=2, default=str)

    except Exception as e:
        return handle_error(e, "pin_tasks")


# =============================================================================
# Kanban Column Tools
# =============================================================================


@mcp.tool(
    name="ticktick_list_columns",
    annotations={
        "title": "List Kanban Columns",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ticktick_list_columns(params: ColumnListInput, ctx: Context) -> str:
    """
    List all kanban columns for a project.

    Returns the columns in a kanban-view project, sorted by display order.
    Only projects with view_mode='kanban' have columns.

    Args:
        params: Query parameters:
            - project_id (str, required): Project ID (must be a kanban project)
            - response_format (str): 'markdown' (default) or 'json'

    Returns:
        List of columns with id, name, and sort_order. Use column_id in
        update_tasks to assign tasks to columns.
    """
    try:
        client = get_client(ctx)
        columns = await client.get_columns(params.project_id)

        if params.response_format == ResponseFormat.MARKDOWN:
            return paginate_columns_markdown(columns, offset=params.offset)
        else:
            return json.dumps(
                paginate_columns_json(columns, offset=params.offset, tz_name=USER_TIMEZONE),
                indent=2,
                default=str,
            )

    except Exception as e:
        return handle_error(e, "list_columns")


@mcp.tool(
    name="ticktick_create_column",
    annotations={
        "title": "Create Kanban Column",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def ticktick_create_column(params: ColumnCreateInput, ctx: Context) -> str:
    """
    Create a new kanban column in a project.

    Columns organize tasks in kanban-view projects. Common column names
    include "To Do", "In Progress", "Review", and "Done".

    Args:
        params: Column create input with project_id, name, and optional sort_order

    Returns:
        Created column details
    """
    try:
        client = get_client(ctx)
        column = await client.create_column(
            project_id=params.project_id,
            name=params.name,
            sort_order=params.sort_order,
        )

        if params.response_format == ResponseFormat.MARKDOWN:
            return f"**Success**: Created column '{column.name}'\n\n" + format_column_markdown(column)
        else:
            result = format_column_json(column, USER_TIMEZONE)
            result["action"] = "created"
            return json.dumps(result, indent=2, default=str)

    except Exception as e:
        return handle_error(e, "create_column")


@mcp.tool(
    name="ticktick_update_column",
    annotations={
        "title": "Update Kanban Column",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ticktick_update_column(params: ColumnUpdateInput, ctx: Context) -> str:
    """
    Update a kanban column's name or sort order.

    Args:
        params: Column update input with column_id, project_id, and optional name/sort_order

    Returns:
        Updated column details
    """
    try:
        client = get_client(ctx)
        column = await client.update_column(
            column_id=params.column_id,
            project_id=params.project_id,
            name=params.name,
            sort_order=params.sort_order,
        )

        if params.response_format == ResponseFormat.MARKDOWN:
            return f"**Success**: Updated column '{column.name}'\n\n" + format_column_markdown(column)
        else:
            result = format_column_json(column, USER_TIMEZONE)
            result["action"] = "updated"
            return json.dumps(result, indent=2, default=str)

    except Exception as e:
        return handle_error(e, "update_column")


@mcp.tool(
    name="ticktick_delete_column",
    annotations={
        "title": "Delete Kanban Column",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def ticktick_delete_column(params: ColumnDeleteInput, ctx: Context) -> str:
    """
    Delete a kanban column.

    Warning: Tasks in this column will become unassigned to any column.
    This operation cannot be undone.

    Args:
        params: Column delete input with column_id and project_id

    Returns:
        Confirmation message
    """
    try:
        client = get_client(ctx)
        await client.delete_column(params.column_id, params.project_id)
        return f"**Success**: Deleted column `{params.column_id}`"

    except Exception as e:
        return handle_error(e, "delete_column")


# =============================================================================
# Project Tools
# =============================================================================


@mcp.tool(
    name="ticktick_list_projects",
    annotations={
        "title": "List Projects",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_list_projects(
    ctx: Context,
    offset: int = 0,
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> str:
    """
    List all projects.

    Retrieves all user projects with their details. Paginated: response
    includes `next_offset` (JSON) or a footer (markdown) when more
    projects remain.

    Args:
        offset: Zero-based offset for paging (default 0). Pass the
            `next_offset` value from the previous call to continue.

    Returns:
        List of projects with: id, name, kind (TASK/NOTE), view_mode (list/kanban/timeline),
        color, folder_id, and metadata. Use list_tasks with project_id to get tasks.
    """
    try:
        client = get_client(ctx)
        projects = await client.get_all_projects()

        if response_format == ResponseFormat.MARKDOWN:
            return paginate_projects_markdown(projects, offset=offset)
        else:
            return json.dumps(paginate_projects_json(projects, offset=offset), indent=2)

    except Exception as e:
        return handle_error(e, "list_projects")


@mcp.tool(
    name="ticktick_get_project",
    annotations={
        "title": "Get Project",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_get_project(params: ProjectGetInput, ctx: Context) -> str:
    """
    Get a project by ID, optionally with its tasks.

    Retrieves project details and optionally all tasks within the project.

    Args:
        params: Query parameters:
            - project_id (str, required): Project identifier
            - include_tasks (bool): Include all project tasks (default False)
            - response_format (str): 'markdown' (default) or 'json'

    Returns:
        Project details: id, name, kind (TASK/NOTE), view_mode (list/kanban/timeline),
        color, folder_id. If include_tasks=true, also returns all tasks in the project.
    """
    try:
        client = get_client(ctx)

        if params.include_tasks:
            project_data = await client.get_project_tasks(params.project_id)

            if params.response_format == ResponseFormat.MARKDOWN:
                lines = [format_project_markdown(project_data.project)]
                lines.append("")
                lines.append(format_tasks_markdown(project_data.tasks, "Tasks", USER_TIMEZONE))
                return "\n".join(lines)
            else:
                return json.dumps({
                    "project": format_project_json(project_data.project),
                    "tasks": format_tasks_json(
                        project_data.tasks,
                        USER_TIMEZONE,
                        content_max_chars=LIST_CONTENT_MAX_CHARS,
                    ),
                }, indent=2)
        else:
            project = await client.get_project(params.project_id)

            if params.response_format == ResponseFormat.MARKDOWN:
                return format_project_markdown(project)
            else:
                return json.dumps(format_project_json(project), indent=2)

    except Exception as e:
        return handle_error(e, "get_project")


@mcp.tool(
    name="ticktick_create_project",
    annotations={
        "title": "Create Project",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def ticktick_create_project(params: ProjectCreateInput, ctx: Context) -> str:
    """
    Create a new project.

    Creates a new project/list for organizing tasks. Projects support different
    types, view modes, and can be organized in folders.

    Args:
        params: Project creation parameters:
            - name (str, required): Project name
            - kind (str): Project type:
              - 'TASK' (default): Standard task list
              - 'NOTE': Note-based project (for notes rather than tasks)
            - view_mode (str): How tasks are displayed:
              - 'list' (default): Traditional list view
              - 'kanban': Board view with columns (requires creating columns after)
              - 'timeline': Gantt-style timeline view for scheduling
            - color (str): Hex color code (e.g., '#F18181', '#4CAFF6')
            - folder_id (str): Parent folder ID to organize project in a folder
            - response_format (str): 'markdown' (default) or 'json'

    Returns:
        Formatted project details or error message.

    Examples:
        Simple task list:
            name="Work Tasks"

        Kanban board for project management:
            name="Sprint Board", view_mode="kanban"

        Timeline for scheduling:
            name="Project Timeline", view_mode="timeline"

        Note project:
            name="Meeting Notes", kind="NOTE"

        Project in a folder:
            name="Q1 Goals", folder_id="folder123", color="#4CAFF6"
    """
    try:
        client = get_client(ctx)

        project = await client.create_project(
            name=params.name,
            color=params.color,
            kind=params.kind,
            view_mode=params.view_mode,
            folder_id=params.folder_id,
        )

        if params.response_format == ResponseFormat.MARKDOWN:
            return f"# Project Created\n\n{format_project_markdown(project)}"
        else:
            return json.dumps({"success": True, "project": format_project_json(project)}, indent=2)

    except Exception as e:
        return handle_error(e, "create_project")


@mcp.tool(
    name="ticktick_update_project",
    annotations={
        "title": "Update Project",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_update_project(params: ProjectUpdateInput, ctx: Context) -> str:
    """
    Update a project's properties.

    Updates project name, color, or folder assignment.

    Args:
        params: Update parameters:
            - project_id (str): Project to update (required)
            - name (str): New project name
            - color (str): New hex color code (e.g., '#F18181')
            - folder_id (str): New folder ID (use 'NONE' to remove from folder)

    Returns:
        Formatted updated project or error message.
    """
    try:
        client = get_client(ctx)

        # Handle "NONE" to remove from folder
        folder_id = params.folder_id
        if folder_id and folder_id.upper() == "NONE":
            folder_id = ""  # Empty string removes from folder

        project = await client.update_project(
            project_id=params.project_id,
            name=params.name,
            color=params.color,
            folder_id=folder_id,
        )

        if params.response_format == ResponseFormat.MARKDOWN:
            return f"# Project Updated\n\n{format_project_markdown(project)}"
        else:
            return json.dumps({"success": True, "project": format_project_json(project)}, indent=2)

    except Exception as e:
        return handle_error(e, "update_project")


@mcp.tool(
    name="ticktick_delete_project",
    annotations={
        "title": "Delete Project",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_delete_project(params: ProjectDeleteInput, ctx: Context) -> str:
    """
    Delete a project.

    Permanently deletes a project and all its tasks. This is a destructive
    operation that cannot be undone.

    Args:
        params: Deletion parameters:
            - project_id (str): Project to delete (required)

    Returns:
        Success confirmation or error message.
    """
    try:
        client = get_client(ctx)
        await client.delete_project(params.project_id)
        return success_message(f"Project `{params.project_id}` deleted.")

    except Exception as e:
        return handle_error(e, "delete_project")


# =============================================================================
# Folder Tools
# =============================================================================


@mcp.tool(
    name="ticktick_list_folders",
    annotations={
        "title": "List Folders",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_list_folders(
    ctx: Context,
    offset: int = 0,
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> str:
    """
    List all folders (project groups). Paginated.

    Args:
        offset: Zero-based offset for paging (default 0).

    Returns:
        Formatted list of folders or error message.
    """
    try:
        client = get_client(ctx)
        folders = await client.get_all_folders()

        if response_format == ResponseFormat.MARKDOWN:
            return paginate_folders_markdown(folders, offset=offset)
        else:
            return json.dumps(paginate_folders_json(folders, offset=offset), indent=2)

    except Exception as e:
        return handle_error(e, "list_folders")


@mcp.tool(
    name="ticktick_create_folder",
    annotations={
        "title": "Create Folder",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def ticktick_create_folder(params: FolderCreateInput, ctx: Context) -> str:
    """
    Create a new folder for organizing projects.

    Args:
        params: Folder creation parameters:
            - name (str): Folder name (required)

    Returns:
        Formatted folder details or error message.
    """
    try:
        client = get_client(ctx)
        folder = await client.create_folder(params.name)

        if params.response_format == ResponseFormat.MARKDOWN:
            return f"# Folder Created\n\n- **{folder.name}** (`{folder.id}`)"
        else:
            return json.dumps({"success": True, "folder": {"id": folder.id, "name": folder.name}}, indent=2)

    except Exception as e:
        return handle_error(e, "create_folder")


@mcp.tool(
    name="ticktick_rename_folder",
    annotations={
        "title": "Rename Folder",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_rename_folder(params: FolderRenameInput, ctx: Context) -> str:
    """
    Rename a folder.

    Args:
        params: Rename parameters:
            - folder_id (str): Folder to rename (required)
            - name (str): New folder name (required)

    Returns:
        Formatted updated folder or error message.
    """
    try:
        client = get_client(ctx)
        folder = await client.rename_folder(params.folder_id, params.name)

        if params.response_format == ResponseFormat.MARKDOWN:
            return f"# Folder Renamed\n\n- **{folder.name}** (`{folder.id}`)"
        else:
            return json.dumps({"success": True, "folder": {"id": folder.id, "name": folder.name}}, indent=2)

    except Exception as e:
        return handle_error(e, "rename_folder")


@mcp.tool(
    name="ticktick_delete_folder",
    annotations={
        "title": "Delete Folder",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_delete_folder(params: FolderDeleteInput, ctx: Context) -> str:
    """
    Delete a folder.

    Deletes a folder. Projects in the folder are not deleted but become
    ungrouped.

    Args:
        params: Deletion parameters:
            - folder_id (str): Folder to delete (required)

    Returns:
        Success confirmation or error message.
    """
    try:
        client = get_client(ctx)
        await client.delete_folder(params.folder_id)
        return success_message(f"Folder `{params.folder_id}` deleted.")

    except Exception as e:
        return handle_error(e, "delete_folder")


# =============================================================================
# Tag Tools
# =============================================================================


@mcp.tool(
    name="ticktick_list_tags",
    annotations={
        "title": "List Tags",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_list_tags(
    ctx: Context,
    offset: int = 0,
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> str:
    """
    List all tags. Paginated.

    Args:
        offset: Zero-based offset for paging (default 0).

    Returns:
        Formatted list of tags or error message.
    """
    try:
        client = get_client(ctx)
        tags = await client.get_all_tags()

        if response_format == ResponseFormat.MARKDOWN:
            return paginate_tags_markdown(tags, offset=offset)
        else:
            return json.dumps(paginate_tags_json(tags, offset=offset), indent=2)

    except Exception as e:
        return handle_error(e, "list_tags")


@mcp.tool(
    name="ticktick_create_tag",
    annotations={
        "title": "Create Tag",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def ticktick_create_tag(params: TagCreateInput, ctx: Context) -> str:
    """
    Create a new tag.

    Tags are used to categorize and filter tasks across projects.

    Args:
        params: Tag creation parameters:
            - name (str): Tag name (required)
            - color (str): Hex color code
            - parent (str): Parent tag name for nesting

    Returns:
        Formatted tag details or error message.
    """
    try:
        client = get_client(ctx)
        tag = await client.create_tag(params.name, color=params.color, parent=params.parent)

        if params.response_format == ResponseFormat.MARKDOWN:
            return f"# Tag Created\n\n{format_tag_markdown(tag)}"
        else:
            return json.dumps({"success": True, "tag": format_tag_json(tag)}, indent=2)

    except Exception as e:
        return handle_error(e, "create_tag")


@mcp.tool(
    name="ticktick_update_tag",
    annotations={
        "title": "Update Tag",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_update_tag(params: TagUpdateInput, ctx: Context) -> str:
    """
    Update a tag's properties.

    Updates tag color, parent, or label (rename). If label is provided,
    the tag is renamed first, then other updates are applied.

    Args:
        params: Update parameters:
            - name (str): Current tag name to update (required)
            - color (str): New hex color code (e.g., '#F18181')
            - parent (str): New parent tag name (empty string to remove parent)
            - label (str): New display name/label for the tag (rename)

    Returns:
        Formatted updated tag or error message.

    Examples:
        Change color:
            name="work", color="#F18181"

        Rename tag:
            name="old-name", label="new-name"

        Rename and change color:
            name="old-name", label="new-name", color="#FF0000"
    """
    try:
        client = get_client(ctx)

        # Handle rename if label is provided
        if params.label:
            await client.rename_tag(params.name, params.label)
            # After rename, update the name we use for subsequent operations
            tag_name = params.label
        else:
            tag_name = params.name

        # Handle empty string as None to remove parent
        parent = params.parent
        if parent == "":
            parent = None

        # Apply other updates if any
        if params.color or parent is not None:
            tag = await client.update_tag(tag_name, color=params.color, parent=parent)
        else:
            # If only renamed, get the tag to return it
            tags = await client.get_all_tags()
            tag = next((t for t in tags if t.name.lower() == tag_name.lower()), None)
            if not tag:
                return success_message(f"Tag renamed to `{tag_name}`")

        if params.response_format == ResponseFormat.MARKDOWN:
            return f"# Tag Updated\n\n{format_tag_markdown(tag)}"
        else:
            return json.dumps({"success": True, "tag": format_tag_json(tag)}, indent=2)

    except Exception as e:
        return handle_error(e, "update_tag")


@mcp.tool(
    name="ticktick_delete_tag",
    annotations={
        "title": "Delete Tag",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_delete_tag(params: TagDeleteInput, ctx: Context) -> str:
    """
    Delete a tag.

    Removes the tag. Tasks with this tag will no longer have it.

    Args:
        params: Deletion parameters:
            - name (str): Tag name to delete (required)

    Returns:
        Success confirmation or error message.
    """
    try:
        client = get_client(ctx)
        await client.delete_tag(params.name)
        return success_message(f"Tag `{params.name}` deleted.")

    except Exception as e:
        return handle_error(e, "delete_tag")


@mcp.tool(
    name="ticktick_merge_tags",
    annotations={
        "title": "Merge Tags",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_merge_tags(params: TagMergeInput, ctx: Context) -> str:
    """
    Merge one tag into another.

    Moves all tasks from the source tag to the target tag, then deletes
    the source tag.

    Args:
        params: Merge parameters:
            - source (str): Tag to merge from (will be deleted)
            - target (str): Tag to merge into (will remain)

    Returns:
        Success confirmation or error message.
    """
    try:
        client = get_client(ctx)
        await client.merge_tags(params.source, params.target)
        return success_message(f"Tag `{params.source}` merged into `{params.target}`.")

    except Exception as e:
        return handle_error(e, "merge_tags")


# =============================================================================
# User Tools
# =============================================================================


@mcp.tool(
    name="ticktick_get_profile",
    annotations={
        "title": "Get User Profile",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_get_profile(ctx: Context, response_format: ResponseFormat = ResponseFormat.MARKDOWN) -> str:
    """
    Get user profile information.

    Retrieves the current user's profile including username, display name,
    and account settings.

    Returns:
        Formatted user profile or error message.
    """
    try:
        client = get_client(ctx)
        user = await client.get_profile()

        if response_format == ResponseFormat.MARKDOWN:
            return format_user_markdown(user)
        else:
            return json.dumps({
                "username": user.username,
                "display_name": user.display_name,
                "name": user.name,
                "email": user.email,
                "locale": user.locale,
                "verified_email": user.verified_email,
            }, indent=2)

    except Exception as e:
        return handle_error(e, "get_profile")


@mcp.tool(
    name="ticktick_get_status",
    annotations={
        "title": "Get Account Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_get_status(ctx: Context, response_format: ResponseFormat = ResponseFormat.MARKDOWN) -> str:
    """
    Get account status and subscription information.

    Retrieves subscription status, Pro account details, and team membership.

    Returns:
        Formatted account status or error message.
    """
    try:
        client = get_client(ctx)
        status = await client.get_status()

        if response_format == ResponseFormat.MARKDOWN:
            return format_user_status_markdown(status)
        else:
            return json.dumps({
                "user_id": status.user_id,
                "username": status.username,
                "inbox_id": status.inbox_id,
                "is_pro": status.is_pro,
                "pro_end_date": status.pro_end_date,
                "team_user": status.team_user,
            }, indent=2)

    except Exception as e:
        return handle_error(e, "get_status")


def _mask_secret(value: str | None) -> str:
    """Mask a sensitive-ish value for safe display — never the full thing."""
    if not value:
        return "(not set)"
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}…{value[-4:]}"


def _build_auth_verdict(
    *,
    v1_ok: bool,
    v2_ok: bool,
    v2_auth_method: str | None,
    v2_cookies_configured: bool,
    v2_reason: str | None,
    v2_error: str | None,
    device_id_valid: bool,
    device_id_ephemeral: bool,
) -> str:
    """One-line, plain-English summary + next step for the current state."""
    parts: list[str] = []
    if v1_ok and v2_ok:
        parts.append(f"All good — V1 and V2 both authenticated (V2 via {v2_auth_method}).")
    elif v1_ok and not v2_ok:
        detail = v2_error or v2_reason or "no specific error was recorded"
        detail_l = detail.lower()
        looks_rate_limited = (
            "429" in detail
            or "rate-limited" in detail_l
            or "rate limit" in detail_l
            or "too many" in detail_l
        )
        looks_expired = "401" in detail or "expired" in detail_l or "stale" in detail_l
        # Lead with the RAW error verbatim, then give hedged, multi-cause
        # commentary instead of one confident diagnosis. The V2 anti-bot masks
        # itself behind several error codes, so an over-specific verdict tends to
        # send the reader chasing the wrong fix.
        parts.append(f"DEGRADED (V1-only): V2 is not working. Raw error: {detail}")
        causes: list[str] = []
        if not v2_cookies_configured:
            causes.append(
                "no session cookie is configured (TICKTICK_V2_COOKIES is unset), so the "
                "server fell back to password sign-on, which TickTick's anti-bot usually "
                "blocks from a server/datacenter IP (often masked as need_captcha / "
                "username_password_not_match / 429 even when the password is correct)"
            )
        if looks_rate_limited:
            causes.append(
                "the error looks like a rate-limit/throttle (429), in which case waiting "
                "it out usually helps more than refreshing the cookie"
            )
        if looks_expired:
            causes.append(
                "the error looks like an expired/stale session (401), which a cookie "
                "refresh usually fixes"
            )
        if v2_cookies_configured and not looks_rate_limited and not looks_expired:
            causes.append("the configured cookie may have expired or been revoked")
        causes.append("or it could be a TickTick-side issue or a different reason entirely")
        parts.append("Possible causes: " + "; ".join(causes) + ".")
        parts.append(
            "Most reliable next step: set or refresh TICKTICK_V2_COOKIES from a "
            "logged-in TickTick browser tab (see README) and redeploy. Tasks and "
            "projects keep working meanwhile; tags/folders/habits/focus/subtasks need V2."
        )
    elif v2_ok and not v1_ok:
        parts.append(
            "DEGRADED (V2-only): V1 (OAuth) is down — refresh TICKTICK_ACCESS_TOKEN "
            "via `ticktick-sdk auth` and redeploy. get_project_with_data won't work "
            "until then."
        )
    else:
        parts.append(
            "BOTH V1 and V2 are failing right now — check credentials in the hosting "
            "env (Railway) and redeploy."
        )

    if not device_id_valid:
        parts.append(
            "Also: TICKTICK_DEVICE_ID is not a valid 24-char hex value, which can "
            "break the password login — set it to a valid hex id."
        )
    elif device_id_ephemeral:
        parts.append(
            "Also: TICKTICK_DEVICE_ID isn't set (auto-generated per deploy) — set a "
            "stable 24-char hex id to look like one consistent device."
        )
    return " ".join(parts)


@mcp.tool(
    name="ticktick_auth_status",
    annotations={
        "title": "Check TickTick Auth Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_auth_status(ctx: Context, response_format: ResponseFormat = ResponseFormat.MARKDOWN) -> str:
    """
    Diagnose TickTick authentication health (live check) without exposing secrets.

    Performs lightweight read pings to test whether the V1 (OAuth) and V2
    (session) connections are valid RIGHT NOW — so it catches a token or cookie
    that expired after the server started. Use this when TickTick tools start
    failing with auth errors, to understand what's wrong and how to fix it.

    The result NEVER contains credential values (password, cookies, tokens) —
    only booleans, a masked device id, and a plain-English verdict that the
    person hosting the server can act on.

    Returns:
        A status report with a verdict and the exact env var to fix, if any.
    """
    try:
        client = get_client(ctx)
        settings = get_settings()
        status = await client.get_auth_status()

        v2_cookies_configured = settings.get_v2_cookies() is not None
        device_id_valid = settings.device_id_looks_valid
        device_id_ephemeral = settings.device_id_is_ephemeral

        verdict = _build_auth_verdict(
            v1_ok=status["v1_ok"],
            v2_ok=status["v2_ok"],
            v2_auth_method=status["v2_auth_method"],
            v2_cookies_configured=v2_cookies_configured,
            v2_reason=status["v2_unavailable_reason"],
            v2_error=status["v2_error"],
            device_id_valid=device_id_valid,
            device_id_ephemeral=device_id_ephemeral,
        )

        report = {
            "v1": {
                "configured": status["v1_has_credentials"],
                "ok": status["v1_ok"],
            },
            "v2": {
                "configured": settings.has_v2_credentials or v2_cookies_configured,
                "has_session": status["v2_has_session"],
                "ok": status["v2_ok"],
                "auth_method": status["v2_auth_method"],
                "cookie_fallback_configured": v2_cookies_configured,
                "unavailable_reason": status["v2_unavailable_reason"],
            },
            "device_id": {
                "configured": not device_id_ephemeral,
                "valid_24char_hex": device_id_valid,
                "length": len(settings.device_id),
                "masked": _mask_secret(settings.device_id),
            },
            "degraded_mode": not (status["v1_ok"] and status["v2_ok"]),
            "verdict": verdict,
        }

        if response_format == ResponseFormat.MARKDOWN:
            v2 = report["v2"]
            lines = [
                "## TickTick Auth Status",
                "",
                f"**Verdict:** {verdict}",
                "",
                f"- **V1 (OAuth, tasks/projects):** {'✅ OK' if report['v1']['ok'] else '❌ failing'}"
                f" (configured: {report['v1']['configured']})",
                f"- **V2 (session, tags/folders/habits/etc.):** {'✅ OK' if v2['ok'] else '❌ failing'}"
                f" (auth method: {v2['auth_method'] or 'none'}; cookie fallback configured: "
                f"{v2['cookie_fallback_configured']})",
                f"- **Degraded mode:** {report['degraded_mode']}",
                f"- **Device ID:** valid 24-char hex: {report['device_id']['valid_24char_hex']} "
                f"(length {report['device_id']['length']}, value {report['device_id']['masked']}, "
                f"configured: {report['device_id']['configured']})",
            ]
            if v2["unavailable_reason"]:
                lines.append(f"- **Last V2 failure reason:** {v2['unavailable_reason']}")
            lines.append("")
            lines.append(
                "_No credential values are shown. Fixes require the server host's "
                "Railway access, not this chat._"
            )
            return "\n".join(lines)
        else:
            return json.dumps(report, indent=2)

    except Exception as e:
        return handle_error(e, "auth_status")


@mcp.tool(
    name="ticktick_get_statistics",
    annotations={
        "title": "Get Productivity Statistics",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_get_statistics(
    ctx: Context,
    section: StatisticsSection = StatisticsSection.ALL,
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> str:
    """
    Get productivity statistics (all from one `/statistics/general` call — no task fetching).

    Use `section` to focus the output:
    - `all` (default): score/level, the task-completion overview + per-day/week/month
      breakdown (total, daily average, completion rate), and a pomodoro summary.
    - `completions`: task completions only — today/yesterday/all-time plus the per-day,
      per-week and per-month breakdown with total, average and completion rate.
    - `score`: score, level, and the per-day score history.
    - `pomodoros`: focus/pomodoro counts, durations, daily goal, and per-day/week/month history.

    Note: the per-day/week/month window is fixed by TickTick (this endpoint takes no date
    range). For completions in a date range *you* choose, use a date-filtered task query.

    Returns:
        Formatted statistics or error message.
    """
    try:
        client = get_client(ctx)
        stats = await client.get_statistics()

        if response_format == ResponseFormat.MARKDOWN:
            return format_statistics_markdown(stats, section=section.value)
        else:
            return json.dumps(
                format_statistics_json(stats, section=section.value),
                indent=2,
                default=str,
            )

    except Exception as e:
        return handle_error(e, "get_statistics")


@mcp.tool(
    name="ticktick_get_preferences",
    annotations={
        "title": "Get User Preferences",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_get_preferences(ctx: Context) -> str:
    """
    Get user preferences and settings.

    Retrieves user-configurable settings including:
    - timeZone: User's timezone
    - weekStartDay: First day of week (0=Sunday, 1=Monday)
    - startOfDay: Hour when day starts
    - dateFormat: Date display format
    - timeFormat: Time display format (12h/24h)
    - defaultReminder: Default reminder setting
    - And other user preferences

    Returns:
        JSON object with all user preferences.
    """
    try:
        client = get_client(ctx)
        preferences = await client.get_preferences()
        return json.dumps(preferences, indent=2)

    except Exception as e:
        return handle_error(e, "get_preferences")


# =============================================================================
# Focus Tools
# =============================================================================


@mcp.tool(
    name="ticktick_focus_heatmap",
    annotations={
        "title": "Get Focus Heatmap",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_focus_heatmap(params: FocusStatsInput, ctx: Context) -> str:
    """
    Get focus/pomodoro heatmap data.

    Retrieves focus time data for visualization as a heatmap.

    Args:
        params: Query parameters:
            - start_date (str): Start date (YYYY-MM-DD)
            - end_date (str): End date (YYYY-MM-DD)
            - days (int): Days to look back if dates not specified

    Returns:
        Focus heatmap data or error message.
    """
    try:
        client = get_client(ctx)

        end_date = date.fromisoformat(params.end_date) if params.end_date else date.today()
        start_date = date.fromisoformat(params.start_date) if params.start_date else end_date - timedelta(days=params.days)

        data = await client.get_focus_heatmap(start_date, end_date)

        if params.response_format == ResponseFormat.MARKDOWN:
            lines = ["# Focus Heatmap", "", f"Period: {start_date} to {end_date}", ""]
            total_duration = sum(d.get("duration", 0) for d in data)
            hours = total_duration / 3600
            lines.append(f"Total Focus Time: {hours:.1f} hours")
            return "\n".join(lines)
        else:
            return json.dumps({
                "start_date": str(start_date),
                "end_date": str(end_date),
                "data": data,
            }, indent=2)

    except Exception as e:
        return handle_error(e, "focus_heatmap")


@mcp.tool(
    name="ticktick_focus_by_tag",
    annotations={
        "title": "Get Focus Time by Tag",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_focus_by_tag(params: FocusStatsInput, ctx: Context) -> str:
    """
    Get focus time distribution by tag.

    Shows how focus time is distributed across different tags.

    Args:
        params: Query parameters:
            - start_date (str): Start date (YYYY-MM-DD)
            - end_date (str): End date (YYYY-MM-DD)
            - days (int): Days to look back if dates not specified

    Returns:
        Focus distribution by tag or error message.
    """
    try:
        client = get_client(ctx)

        end_date = date.fromisoformat(params.end_date) if params.end_date else date.today()
        start_date = date.fromisoformat(params.start_date) if params.start_date else end_date - timedelta(days=params.days)

        data = await client.get_focus_by_tag(start_date, end_date)

        if params.response_format == ResponseFormat.MARKDOWN:
            lines = ["# Focus Time by Tag", "", f"Period: {start_date} to {end_date}", ""]

            if not data:
                lines.append("No focus data for this period.")
            else:
                for tag, seconds in sorted(data.items(), key=lambda x: x[1], reverse=True):
                    hours = seconds / 3600
                    lines.append(f"- **{tag}**: {hours:.1f} hours")

            return "\n".join(lines)
        else:
            return json.dumps({
                "start_date": str(start_date),
                "end_date": str(end_date),
                "tag_durations": data,
            }, indent=2)

    except Exception as e:
        return handle_error(e, "focus_by_tag")


# =============================================================================
# Habit Tools
# =============================================================================


def format_habit_markdown(habit: Habit) -> str:
    """Format a habit for markdown display."""
    lines = [
        f"## {habit.name}",
        f"- **ID**: `{habit.id}`",
        f"- **Type**: {habit.habit_type}",
        f"- **Goal**: {habit.goal} {habit.unit}",
    ]

    if habit.is_numeric:
        lines.append(f"- **Step**: +{habit.step}")

    lines.append(f"- **Status**: {'Archived' if habit.is_archived else 'Active'}")
    lines.append(f"- **Total Check-ins**: {habit.total_checkins}")
    lines.append(f"- **Current Streak**: {habit.current_streak}")

    if habit.target_days > 0:
        lines.append(f"- **Target**: {habit.target_days} days")

    if habit.color:
        lines.append(f"- **Color**: {habit.color}")

    if habit.repeat_rule:
        lines.append(f"- **Repeat**: `{habit.repeat_rule}`")

    if habit.reminders:
        lines.append(f"- **Reminders**: {', '.join(habit.reminders)}")

    if habit.encouragement:
        lines.append(f"- **Encouragement**: {habit.encouragement}")

    return "\n".join(lines)


def format_habit_json(habit: Habit) -> dict[str, Any]:
    """Format a habit for JSON output."""
    return {
        "id": habit.id,
        "name": habit.name,
        "type": habit.habit_type,
        "goal": habit.goal,
        "step": habit.step,
        "unit": habit.unit,
        "status": "archived" if habit.is_archived else "active",
        "total_checkins": habit.total_checkins,
        "current_streak": habit.current_streak,
        "target_days": habit.target_days,
        "color": habit.color,
        "icon": habit.icon,
        "repeat_rule": habit.repeat_rule,
        "reminders": habit.reminders,
        "section_id": habit.section_id,
        "encouragement": habit.encouragement,
        "created_time": habit.created_time.isoformat() if habit.created_time else None,
        "modified_time": habit.modified_time.isoformat() if habit.modified_time else None,
    }


def format_habit_row_markdown(habit: Habit) -> str:
    """Format a single habit as a multi-line list block."""
    status = "📦" if habit.is_archived else "✅" if habit.current_streak > 0 else "⏳"
    parts = [
        f"### {status} {habit.name}",
        f"- **ID**: `{habit.id}`",
        f"- **Type**: {habit.habit_type}",
        f"- **Streak**: {habit.current_streak} | Total: {habit.total_checkins}",
    ]
    if habit.target_days > 0:
        parts.append(f"- **Target**: {habit.target_days} days")
    parts.append("")  # trailing blank line so consecutive habits read as separate blocks
    return "\n".join(parts)


def format_habits_markdown(habits: list[Habit], title: str = "Habits") -> str:
    """Format multiple habits for markdown (non-paginated convenience wrapper)."""
    if not habits:
        return f"# {title}\n\nNo habits found."

    lines = [f"# {title}", f"*{len(habits)} habits*", ""]
    for habit in habits:
        lines.append(format_habit_row_markdown(habit))
    return "\n".join(lines)


def format_habits_json(habits: list[Habit]) -> list[dict[str, Any]]:
    """Format multiple habits for JSON output (legacy bare-list shape)."""
    return [format_habit_json(h) for h in habits]


def format_section_markdown(section: HabitSection) -> str:
    """Format a habit section for markdown display."""
    return f"- **{section.display_name}** (`{section.id}`)"


def format_sections_json(sections: list[HabitSection]) -> list[dict[str, Any]]:
    """Format habit sections for JSON output."""
    return [
        {"id": s.id, "name": s.name, "display_name": s.display_name}
        for s in sections
    ]


@mcp.tool(
    name="ticktick_habits",
    annotations={
        "title": "List Habits",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_habits(params: HabitListInput, ctx: Context) -> str:
    """
    List all habits.

    Retrieves all habits including their status, streaks, and goals.

    Args:
        params: Query parameters:
            - include_archived (bool): Include archived habits (default: False)
            - response_format (str): Output format ("markdown" or "json")

    Returns:
        List of habits with their details.
    """
    try:
        client = get_client(ctx)
        habits = await client.get_all_habits()

        if not params.include_archived:
            habits = [h for h in habits if h.is_active]

        if params.response_format == ResponseFormat.MARKDOWN:
            return paginate_markdown(
                habits,
                title="Habits",
                offset=params.offset,
                format_item=format_habit_row_markdown,
                item_label="habits",
            )
        else:
            return json.dumps(
                paginate_json(
                    habits,
                    offset=params.offset,
                    format_item=format_habit_json,
                    item_key="habits",
                ),
                indent=2,
            )

    except Exception as e:
        return handle_error(e, "list_habits")


@mcp.tool(
    name="ticktick_habit",
    annotations={
        "title": "Get Habit",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_habit(params: HabitGetInput, ctx: Context) -> str:
    """
    Get a specific habit by ID.

    Args:
        params: Query parameters:
            - habit_id (str): Habit ID (required)
            - response_format (str): Output format

    Returns:
        Habit details.
    """
    try:
        client = get_client(ctx)
        habit = await client.get_habit(params.habit_id)

        if params.response_format == ResponseFormat.MARKDOWN:
            return format_habit_markdown(habit)
        else:
            return json.dumps(format_habit_json(habit), indent=2)

    except Exception as e:
        return handle_error(e, "get_habit")


@mcp.tool(
    name="ticktick_habit_sections",
    annotations={
        "title": "List Habit Sections",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_habit_sections(ctx: Context, response_format: ResponseFormat = ResponseFormat.MARKDOWN) -> str:
    """
    List habit sections (time-of-day groupings).

    Sections organize habits by time of day: Morning, Afternoon, Night.

    Returns:
        List of habit sections with their IDs.
    """
    try:
        client = get_client(ctx)
        sections = await client.get_habit_sections()

        if response_format == ResponseFormat.MARKDOWN:
            lines = ["# Habit Sections", ""]
            for section in sections:
                lines.append(format_section_markdown(section))
            return "\n".join(lines)
        else:
            return json.dumps(format_sections_json(sections), indent=2)

    except Exception as e:
        return handle_error(e, "habit_sections")


@mcp.tool(
    name="ticktick_create_habit",
    annotations={
        "title": "Create Habit",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def ticktick_create_habit(params: HabitCreateInput, ctx: Context) -> str:
    """
    Create a new habit.

    Creates a habit with the specified configuration. Habits can be boolean
    (yes/no) or numeric (count/measure).

    Args:
        params: Habit parameters:
            - name (str): Habit name (required)
            - habit_type (str): "Boolean" or "Real" (default: Boolean)
            - goal (float): Target value (default: 1.0)
            - step (float): Increment for numeric (default: 1.0)
            - unit (str): Unit of measurement (default: Count)
            - color (str): Hex color (optional)
            - section_id (str): Time-of-day section (optional)
            - repeat_rule (str): RRULE pattern (default: daily)
            - reminders (list[str]): Times in HH:MM format
            - target_days (int): Goal in days (0 = no target)
            - encouragement (str): Motivational message

    Returns:
        Created habit details.
    """
    try:
        client = get_client(ctx)
        habit = await client.create_habit(
            name=params.name,
            habit_type=params.habit_type,
            goal=params.goal,
            step=params.step,
            unit=params.unit,
            color=params.color or "#97E38B",
            section_id=params.section_id,
            repeat_rule=params.repeat_rule,
            reminders=params.reminders,
            target_days=params.target_days,
            encouragement=params.encouragement,
        )

        if params.response_format == ResponseFormat.MARKDOWN:
            return f"# Habit Created\n\n{format_habit_markdown(habit)}"
        else:
            return json.dumps({"success": True, "habit": format_habit_json(habit)}, indent=2)

    except Exception as e:
        return handle_error(e, "create_habit")


@mcp.tool(
    name="ticktick_update_habit",
    annotations={
        "title": "Update Habit",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_update_habit(params: HabitUpdateInput, ctx: Context) -> str:
    """
    Update a habit's properties.

    Includes archive/unarchive functionality via the archived field.

    Args:
        params: Update parameters:
            - habit_id (str): Habit ID (required)
            - name (str): New name
            - goal (float): New goal
            - step (float): New step
            - unit (str): New unit
            - color (str): New hex color
            - section_id (str): New section ID
            - repeat_rule (str): New RRULE pattern
            - reminders (list[str]): New reminders
            - target_days (int): New target days
            - encouragement (str): New message
            - archived (bool): Set true to archive, false to unarchive

    Returns:
        Updated habit details.

    Examples:
        Update name:
            habit_id="abc123", name="New Habit Name"

        Archive habit:
            habit_id="abc123", archived=true

        Unarchive habit:
            habit_id="abc123", archived=false
    """
    try:
        client = get_client(ctx)

        # Handle archive/unarchive via the archived field
        if params.archived is not None:
            if params.archived:
                habit = await client.archive_habit(params.habit_id)
                action = "archived"
            else:
                habit = await client.unarchive_habit(params.habit_id)
                action = "unarchived"

            # If only archiving/unarchiving (no other updates), return early
            if not any([params.name, params.goal, params.step, params.unit, params.color,
                       params.section_id, params.repeat_rule, params.reminders,
                       params.target_days, params.encouragement]):
                if params.response_format == ResponseFormat.MARKDOWN:
                    return f"# Habit {action.capitalize()}\n\n**{habit.name}** has been {action}."
                else:
                    return json.dumps({"success": True, "action": action, "habit": format_habit_json(habit)}, indent=2)

        # Apply other updates
        habit = await client.update_habit(
            habit_id=params.habit_id,
            name=params.name,
            goal=params.goal,
            step=params.step,
            unit=params.unit,
            color=params.color,
            section_id=params.section_id,
            repeat_rule=params.repeat_rule,
            reminders=params.reminders,
            target_days=params.target_days,
            encouragement=params.encouragement,
        )

        if params.response_format == ResponseFormat.MARKDOWN:
            return f"# Habit Updated\n\n{format_habit_markdown(habit)}"
        else:
            return json.dumps({"success": True, "habit": format_habit_json(habit)}, indent=2)

    except Exception as e:
        return handle_error(e, "update_habit")


@mcp.tool(
    name="ticktick_delete_habit",
    annotations={
        "title": "Delete Habit",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_delete_habit(params: HabitDeleteInput, ctx: Context) -> str:
    """
    Delete a habit.

    Permanently removes the habit and all its check-in history.

    Args:
        params: Delete parameters:
            - habit_id (str): Habit ID to delete (required)

    Returns:
        Confirmation message.
    """
    try:
        client = get_client(ctx)
        await client.delete_habit(params.habit_id)
        return success_message(f"Habit `{params.habit_id}` deleted successfully.")

    except Exception as e:
        return handle_error(e, "delete_habit")


@mcp.tool(
    name="ticktick_checkin_habits",
    annotations={
        "title": "Check In Habits",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def ticktick_checkin_habits(params: CheckinHabitsInput, ctx: Context) -> str:
    """
    Check in one or more habits for today or past dates.

    Records check-ins for habits. For each check-in:
    - If no date is provided, checks in for today (increments both total and streak)
    - If a past date is provided (backdating), only increments total (streak unaffected)

    This is useful for:
    - Checking in multiple habits at once
    - Migrating habit history from another app
    - Backdating missed check-ins

    Args:
        params: Check-in parameters:
            - checkins (list): List of check-ins, each containing:
                - habit_id (str): Habit ID (required)
                - value (float): Check-in value (default: 1.0)
                - checkin_date (str): Date to check in for (YYYY-MM-DD, optional)

    Returns:
        Updated habits with new totals.
    """
    try:
        client = get_client(ctx)

        # Build checkins list for batch operation
        checkin_data = []
        for checkin in params.checkins:
            checkin_data.append({
                "habit_id": checkin.habit_id,
                "value": checkin.value,
                "checkin_date": checkin.checkin_date,
            })

        # Call batch method
        results = await client.checkin_habits(checkin_data)

        if params.response_format == ResponseFormat.MARKDOWN:
            lines = [f"# {len(results)} Habit Check-in(s) Recorded", ""]
            today_str = date.today().isoformat()

            for habit_id, habit in results.items():
                # Find the corresponding checkin data
                checkin_info = next(
                    (c for c in params.checkins if c.habit_id == habit_id),
                    None
                )
                date_str = (
                    checkin_info.checkin_date
                    if checkin_info and checkin_info.checkin_date
                    else "today"
                )

                lines.append(f"## {habit.name}")
                lines.append(f"- **Date**: {date_str}")
                lines.append(f"- **Total Check-ins**: {habit.total_checkins}")
                lines.append(f"- **Current Streak**: {habit.current_streak}")
                lines.append("")

            # Add backdating note if any past dates
            has_backdated = any(
                c.checkin_date and c.checkin_date < today_str
                for c in params.checkins
            )
            if has_backdated:
                lines.append("*Note: Backdated check-ins don't affect the current streak.*")

            return "\n".join(lines)
        else:
            return json.dumps({
                "success": True,
                "count": len(results),
                "habits": {
                    habit_id: format_habit_json(habit)
                    for habit_id, habit in results.items()
                },
            }, indent=2)

    except Exception as e:
        return handle_error(e, "checkin_habits")


@mcp.tool(
    name="ticktick_habit_checkins",
    annotations={
        "title": "Get Habit Check-in History",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ticktick_habit_checkins(params: HabitCheckinsInput, ctx: Context) -> str:
    """
    Get habit check-in history.

    Retrieves check-in records for the specified habits.

    Args:
        params: Query parameters:
            - habit_ids (list[str]): List of habit IDs to query (required)
            - after_stamp (int): Date stamp (YYYYMMDD) to get check-ins after

    Returns:
        Check-in history for each habit.
    """
    try:
        client = get_client(ctx)
        data = await client.get_habit_checkins(
            habit_ids=params.habit_ids,
            after_stamp=params.after_stamp,
        )

        if params.response_format == ResponseFormat.MARKDOWN:
            lines = ["# Habit Check-in History", ""]
            for habit_id, checkins in data.items():
                lines.append(f"## Habit `{habit_id}`")
                if not checkins:
                    lines.append("No check-ins found.")
                else:
                    for checkin in checkins:
                        lines.append(f"- {checkin.checkin_stamp}: {checkin.value}")
                lines.append("")
            return "\n".join(lines)
        else:
            # Convert HabitCheckin objects to dicts
            result = {}
            for habit_id, checkins in data.items():
                result[habit_id] = [
                    {
                        "checkin_stamp": c.checkin_stamp,
                        "value": c.value,
                        "goal": c.goal,
                        "status": c.status,
                    }
                    for c in checkins
                ]
            return json.dumps(result, indent=2)

    except Exception as e:
        return handle_error(e, "habit_checkins")


# =============================================================================
# Main Entry Point
# =============================================================================


# Tools that have a V1 (OAuth) path and therefore still work when V2 (session
# auth) is degraded. Everything else is V2-only. Keep this in sync with the
# routing in unified/api.py (see ARCHITECTURE.md §5).
_V1_CAPABLE_TOOLS = frozenset({
    "ticktick_list_projects",
    "ticktick_get_project",
    "ticktick_get_task",  # V1 path works only when a project_id is supplied
})
# Diagnostic tool that is meant to work even while everything else is degraded.
_ALWAYS_AVAILABLE_TOOLS = frozenset({
    "ticktick_auth_status",
})


def _annotate_tool_apis() -> None:
    """Tag each tool's description with which API backs it.

    Lets an MCP client (e.g. Claude) tell *before calling* whether a tool needs
    V2 (session) auth, or whether it also works in V1-only degraded mode. The
    classification mirrors the routing in ``unified/api.py`` (ARCHITECTURE.md §5).
    Idempotent: re-running won't double-tag.
    """
    try:
        tools = mcp._tool_manager.list_tools()
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Could not annotate tool APIs: %s", e)
        return

    for tool in tools:
        if tool.description and "[API:" in tool.description:
            continue  # already tagged
        if tool.name in _ALWAYS_AVAILABLE_TOOLS:
            tag = "[API: diagnostic, always available]"
        elif tool.name in _V1_CAPABLE_TOOLS:
            tag = "[API: V1+V2, works even in V1-only degraded mode]"
        else:
            tag = "[API: V2-only, unavailable in V1-only degraded mode]"
        tool.description = f"{tool.description.rstrip()}\n\n{tag}" if tool.description else tag


def _apply_tool_filtering():
    """
    Apply tool filtering based on TICKTICK_ENABLED_TOOLS environment variable.

    This removes tools that are not in the enabled list, reducing context window
    usage when using the MCP server with AI assistants.
    """
    import os

    enabled_tools_env = os.environ.get("TICKTICK_ENABLED_TOOLS")
    if not enabled_tools_env:
        return  # No filtering, all tools enabled

    enabled_tools = set(enabled_tools_env.split(","))

    # Get all registered tools
    all_tools = mcp._tool_manager.list_tools()
    tools_to_remove = []

    for tool in all_tools:
        if tool.name not in enabled_tools:
            tools_to_remove.append(tool.name)

    # Remove disabled tools
    for tool_name in tools_to_remove:
        try:
            mcp._tool_manager.remove_tool(tool_name)
        except Exception as e:
            logger.warning("Failed to remove tool %s: %s", tool_name, e)

    remaining = len(all_tools) - len(tools_to_remove)
    logger.info(
        "Tool filtering applied: %d of %d tools enabled",
        remaining,
        len(all_tools),
    )


def main():
    """Main entry point for the TickTick MCP server."""
    _annotate_tool_apis()
    _apply_tool_filtering()

    bearer_token = os.environ.get("MCP_BEARER_TOKEN")

    import uvicorn
    from starlette.types import ASGIApp, Receive, Scope, Send

    starlette_app: ASGIApp = mcp.streamable_http_app()

    if bearer_token:
        logger.info("Bearer token authentication enabled")

        class BearerTokenMiddleware:
            """Simple ASGI middleware that checks for a static bearer token."""

            def __init__(self, app: ASGIApp, token: str):
                self.app = app
                self.token = token

            async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
                if scope["type"] == "http":
                    path = scope.get("path", "")
                    # Allow health check without auth
                    if path == "/health":
                        await self.app(scope, receive, send)
                        return
                    headers = dict(scope.get("headers", []))
                    auth = headers.get(b"authorization", b"").decode()
                    if auth != f"Bearer {self.token}":
                        response = JSONResponse(
                            {"error": "unauthorized"}, status_code=401
                        )
                        await response(scope, receive, send)
                        return
                await self.app(scope, receive, send)

        starlette_app = BearerTokenMiddleware(starlette_app, bearer_token)

    port = int(os.environ.get("PORT", "8000"))
    logger.info("Starting TickTick MCP server on 0.0.0.0:%d/mcp", port)

    config = uvicorn.Config(
        starlette_app,
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)

    import anyio

    anyio.run(server.serve)


def main_stdio():
    """Run the MCP server over stdio (for local clients like Claude Desktop).

    Unlike main() (streamable-HTTP for Railway), this speaks MCP over
    stdin/stdout, which is how Claude Desktop launches a local server. Logs go to
    stderr (see basicConfig above), so stdout stays clean for the JSON-RPC
    protocol. The lifespan still runs (building the shared client once), and
    cookie-first auth means a local/residential IP sidesteps the datacenter
    throttle.
    """
    _annotate_tool_apis()
    _apply_tool_filtering()
    logger.info("Starting TickTick MCP server over stdio")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
