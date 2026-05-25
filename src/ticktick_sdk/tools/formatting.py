"""
Response Formatting Utilities for TickTick SDK Tools.

This module provides consistent formatting for tool responses
in both Markdown and JSON formats.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from ticktick_sdk.models import Column, Task, Project, ProjectGroup, Tag, User, UserStatus, UserStatistics
from ticktick_sdk.tools.inputs import ResponseFormat

# Maximum response size in characters
CHARACTER_LIMIT = 25000


def convert_tz(dt: datetime | None, tz_name: str) -> datetime | None:
    """Convert a UTC datetime to the given timezone."""
    if dt is None:
        return None
    try:
        return dt.astimezone(ZoneInfo(tz_name))
    except Exception:
        return dt


def format_datetime(dt: datetime | None, tz_name: str = "UTC") -> str:
    """Format a datetime for human-readable display."""
    if dt is None:
        return "Not set"
    return convert_tz(dt, tz_name).strftime("%Y-%m-%d %H:%M %Z").strip()


def format_date(dt: datetime | None, tz_name: str = "UTC") -> str:
    """Format a date for human-readable display."""
    if dt is None:
        return "Not set"
    return convert_tz(dt, tz_name).strftime("%Y-%m-%d")


def all_day_date(dt: datetime | None, is_end_boundary: bool = False) -> str | None:
    """Extract the logical calendar date from an all-day task datetime.

    TickTick stores all-day dates as UTC midnight boundaries:
      start_date → midnight UTC of the actual day (May 25 00:00Z = May 25)
      due_date   → midnight UTC of the NEXT day   (May 26 00:00Z = May 25)

    For end boundaries (due_date), subtract 1 day. Timezone conversion is
    skipped entirely — the UTC date is canonical for all-day events.
    """
    if dt is None:
        return None
    utc_date = dt.astimezone(dt_timezone.utc).date()
    if is_end_boundary:
        utc_date -= timedelta(days=1)
    return utc_date.isoformat()


def priority_label(priority: int) -> str:
    """Convert priority int to label."""
    labels = {0: "None", 1: "Low", 3: "Medium", 5: "High"}
    return labels.get(priority, "None")


def priority_indicator(priority: int) -> str:
    """Get text indicator for priority level."""
    indicators = {0: "[NONE]", 1: "[LOW]", 3: "[MEDIUM]", 5: "[HIGH]"}
    return indicators.get(priority, "")


def status_label(status: int) -> str:
    """Convert status int to label."""
    labels = {-1: "Abandoned", 0: "Active", 1: "Completed", 2: "Completed"}
    return labels.get(status, "Unknown")


_KNOWN_RRULE_FREQS = {"DAILY", "WEEKLY", "MONTHLY", "YEARLY", "HOURLY", "MINUTELY"}


def repeat_flag_indicator(repeat_flag: str | None) -> str:
    """Compact list-row label for a task's recurrence rule.

    Parses FREQ= out of an iCalendar RRULE (e.g. ``RRULE:FREQ=WEEKLY;BYDAY=MO``)
    and returns ``[WEEKLY] `` so the cadence is visible at a glance. Falls back
    to ``[REPEATS] `` when the rule is set but FREQ is missing or unknown.
    """
    if not repeat_flag:
        return ""
    match = re.search(r"FREQ=(\w+)", repeat_flag, re.IGNORECASE)
    if not match:
        return "[REPEATS] "
    freq = match.group(1).upper()
    if freq in _KNOWN_RRULE_FREQS:
        return f"[{freq}] "
    return "[REPEATS] "


# =============================================================================
# Generic Pagination Helpers
# =============================================================================

# Reserves for header/footer (markdown) and envelope (JSON) so we don't blow
# the budget when adding the surrounding scaffolding.
_MD_HEADER_RESERVE = 150
_MD_FOOTER_RESERVE = 250
_JSON_ENVELOPE_RESERVE = 300


def paginate_markdown(
    items: list,
    title: str,
    offset: int,
    format_item: Callable[[Any], str],
    item_label: str = "items",
    budget: int = CHARACTER_LIMIT,
) -> str:
    """Budget-aware paginated markdown rendering.

    Adds one item at a time until the next would exceed `budget`, then stops
    and appends a footer telling the caller how to fetch the next page. If
    every item fits, the footer is omitted entirely so pagination is
    invisible to the consumer.
    """
    total = len(items)
    if total == 0:
        return f"# {title}\n\nNo {item_label} found."
    if offset >= total:
        return (
            f"# {title}\n\n"
            f"No {item_label} at offset {offset} (total: {total}). "
            f"Use offset=0 to start from the beginning."
        )

    page = items[offset:]
    available = budget - _MD_HEADER_RESERVE - _MD_FOOTER_RESERVE

    rows = []
    used = 0
    for item in page:
        row = format_item(item)
        if used + len(row) + 1 > available:
            break
        rows.append(row)
        used += len(row) + 1

    shown = len(rows)
    next_offset = offset + shown if offset + shown < total else None

    if shown == total:
        summary = f"Found {total} {item_label}:"
    else:
        summary = (
            f"Showing {item_label} {offset + 1}–{offset + shown} of {total} total:"
        )

    out = [f"# {title}", "", summary, ""]
    out.extend(rows)

    if next_offset is not None:
        out.append("")
        out.append("---")
        out.append(
            f"More {item_label} available. Call again with `offset={next_offset}` "
            f"to fetch the next page."
        )

    return "\n".join(out)


def paginate_json(
    items: list,
    offset: int,
    format_item: Callable[[Any], dict],
    budget: int = CHARACTER_LIMIT,
    item_key: str = "items",
) -> dict[str, Any]:
    """Budget-aware paginated JSON rendering.

    Returns `{count, total, offset, next_offset, <item_key>}`. Caller
    serializes with `json.dumps`. When `next_offset` is None there are no
    more pages; otherwise pass it back as `offset` to continue.

    Sizing is exact: after each item we serialize the whole envelope and
    back off if we've gone over budget. O(n²) in page size but n is small.
    """
    total = len(items)
    if total == 0:
        return {
            "count": 0,
            "total": 0,
            "offset": 0,
            "next_offset": None,
            item_key: [],
        }
    if offset >= total:
        return {
            "count": 0,
            "total": total,
            "offset": offset,
            "next_offset": None,
            item_key: [],
            "_hint": f"offset {offset} is past the end (total: {total}).",
        }

    def envelope(items_list: list[dict], next_off: int | None) -> dict[str, Any]:
        return {
            "count": len(items_list),
            "total": total,
            "offset": offset,
            "next_offset": next_off,
            item_key: items_list,
        }

    formatted: list[dict] = []
    for idx, item in enumerate(items[offset:]):
        formatted.append(format_item(item))
        # Worst-case envelope: next_offset present (longer than null)
        provisional_next = offset + len(formatted) + 1
        if len(json.dumps(envelope(formatted, provisional_next), indent=2, default=str)) > budget:
            formatted.pop()
            break

    shown = len(formatted)
    next_offset = offset + shown if offset + shown < total else None
    return envelope(formatted, next_offset)


# =============================================================================
# Task Formatting
# =============================================================================


def format_task_markdown(
    task: Task,
    tz_name: str = "UTC",
    project_names: dict[str, str] | None = None,
) -> str:
    """Format a single task as Markdown.

    When `project_names` is provided and contains an entry for this task's
    `project_id`, the **Project** line shows `Name (\`id\`)` instead of just
    the ID.
    """
    lines = []

    # Title with priority indicator
    priority_str = priority_indicator(task.priority)
    title = task.title or "(No title)"
    lines.append(f"## {priority_str} {title}")
    lines.append("")

    # Key details
    lines.append(f"- **ID**: `{task.id}`")
    if project_names and task.project_id in project_names:
        lines.append(
            f"- **Project**: {project_names[task.project_id]} (`{task.project_id}`)"
        )
    else:
        lines.append(f"- **Project**: `{task.project_id}`")
    if task.parent_id:
        lines.append(f"- **Parent**: `{task.parent_id}`")
    if task.child_ids:
        lines.append("- **Children**:")
        for child_id in task.child_ids:
            lines.append(f"  - `{child_id}`")
    lines.append(f"- **Status**: {status_label(task.status)}")
    lines.append(f"- **Priority**: {priority_label(task.priority)}")

    if task.is_pinned:
        lines.append("- **Pinned**: Yes")

    # Display task kind only if non-default (not TEXT)
    if task.kind and task.kind != "TEXT":
        lines.append(f"- **Type**: {task.kind}")

    if task.due_date:
        if task.is_all_day:
            lines.append(f"- **Due**: {all_day_date(task.due_date, is_end_boundary=True)}")
        else:
            lines.append(f"- **Due**: {format_datetime(task.due_date, tz_name)}")
    if task.start_date:
        if task.is_all_day:
            lines.append(f"- **Start**: {all_day_date(task.start_date, is_end_boundary=False)}")
        else:
            lines.append(f"- **Start**: {format_datetime(task.start_date, tz_name)}")
    if task.is_all_day:
        lines.append("- **All-day**: Yes")
    if task.repeat_flag:
        lines.append(f"- **Repeats**: `{task.repeat_flag}`")
    # Only surface time_zone when it differs from the user's configured TZ —
    # otherwise it's noise on every task.
    if task.time_zone and task.time_zone != tz_name:
        lines.append(f"- **Time zone**: {task.time_zone}")

    if task.tags:
        tags_str = ", ".join(f"`{t}`" for t in task.tags)
        lines.append(f"- **Tags**: {tags_str}")

    if task.content:
        lines.append("")
        lines.append("### Notes")
        lines.append(task.content)

    if task.items:
        # task.items are checklist items (a TODO list *inside* the task),
        # not child tasks. Child tasks are tracked separately via child_ids
        # and listed under **Children** in the key-details block above.
        lines.append("")
        lines.append("### Checklist")
        for item in task.items:
            checkbox = "[x]" if item.is_completed else "[ ]"
            lines.append(f"- {checkbox} {item.title or '(No title)'}")

    return "\n".join(lines)


def format_task_json(
    task: Task,
    tz_name: str = "UTC",
    content_max_chars: int | None = None,
) -> dict[str, Any]:
    """Format a single task as JSON-serializable dict.

    `content_max_chars` is the per-task content cap used in list views to
    keep page sizes manageable. When set and the content is longer, it's
    truncated with an ellipsis and an extra `content_truncated: true` field
    is added — the model should call `ticktick_get_task` for the full text.
    Detail-view callers leave this at None to get the full content.
    """
    # For all-day tasks, output corrected date-only strings (YYYY-MM-DD)
    # instead of full ISO datetimes that carry a misleading time+offset.
    if task.is_all_day:
        start_date_str = all_day_date(task.start_date, is_end_boundary=False)
        due_date_str = all_day_date(task.due_date, is_end_boundary=True)
    else:
        start_date_str = convert_tz(task.start_date, tz_name).isoformat() if task.start_date else None
        due_date_str = convert_tz(task.due_date, tz_name).isoformat() if task.due_date else None
    completed_time = convert_tz(task.completed_time, tz_name)

    content = task.content
    content_truncated = False
    if (
        content_max_chars is not None
        and content is not None
        and len(content) > content_max_chars
    ):
        content = content[:content_max_chars] + "…"
        content_truncated = True

    payload: dict[str, Any] = {
        "id": task.id,
        "project_id": task.project_id,
        "title": task.title,
        "content": content,
        "kind": task.kind,
        "status": task.status,
        "status_label": status_label(task.status),
        "priority": task.priority,
        "priority_label": priority_label(task.priority),
        "is_pinned": task.is_pinned,
        "start_date": start_date_str,
        "due_date": due_date_str,
        "completed_time": completed_time.isoformat() if completed_time else None,
        "tags": task.tags,
        "is_all_day": task.is_all_day,
        "time_zone": task.time_zone,
        "repeat_flag": task.repeat_flag,
        "parent_id": task.parent_id,
        "child_ids": task.child_ids,
        "items": [
            {
                "id": item.id,
                "title": item.title,
                "status": item.status,
                "completed": item.is_completed,
            }
            for item in task.items
        ],
    }
    if content_truncated:
        payload["content_truncated"] = True
    return payload


def format_task_row_markdown(
    task: Task,
    tz_name: str = "UTC",
    project_names: dict[str, str] | None = None,
) -> str:
    """Format a single task as one markdown list row (no leading bullet header)."""
    priority_str = priority_indicator(task.priority)
    pinned_str = "[PINNED] " if task.is_pinned else ""
    # Only flag non-active statuses — [ACTIVE] on every row is noise.
    if task.status == -1:
        status_flag = "[ABANDONED] "
    elif task.status in (1, 2):
        status_flag = "[DONE] "
    else:
        status_flag = ""
    repeat_flag_str = repeat_flag_indicator(task.repeat_flag)
    task_title = task.title or "(No title)"
    if task.due_date:
        if task.is_all_day:
            due_str = f" | Due: {all_day_date(task.due_date, is_end_boundary=True)}"
        else:
            due_str = f" | Due: {format_date(task.due_date, tz_name)}"
    else:
        due_str = ""
    tags_str = f" | Tags: {', '.join(task.tags)}" if task.tags else ""
    parent_str = f" | Child of: `{task.parent_id}`" if task.parent_id else ""
    child_count = len(task.child_ids) if task.child_ids else 0
    children_str = f" | {child_count} children" if child_count else ""
    project_str = ""
    if project_names and task.project_id in project_names:
        project_str = f" | Project: {project_names[task.project_id]}"

    return (
        f"- {priority_str} {pinned_str}{status_flag}{repeat_flag_str}**{task_title}** "
        f"(`{task.id}`){project_str}{due_str}{tags_str}{parent_str}{children_str}"
    )


def format_tasks_markdown(
    tasks: list[Task],
    title: str = "Tasks",
    tz_name: str = "UTC",
    project_names: dict[str, str] | None = None,
) -> str:
    """Format multiple tasks as Markdown (non-paginated convenience wrapper).

    For budget-aware paginated output, use `paginate_tasks_markdown`.
    """
    if not tasks:
        return f"# {title}\n\nNo tasks found."

    lines = [f"# {title}", "", f"Found {len(tasks)} task(s):", ""]
    for task in tasks:
        lines.append(format_task_row_markdown(task, tz_name, project_names))
    return "\n".join(lines)


def format_tasks_json(
    tasks: list[Task],
    tz_name: str = "UTC",
    content_max_chars: int | None = None,
) -> dict[str, Any]:
    """Format multiple tasks as JSON (non-paginated convenience wrapper).

    `content_max_chars` defaults to None for backward compatibility, but
    list-view callers should pass `LIST_CONTENT_MAX_CHARS` so the per-task
    notes don't blow up batch responses with multi-kilobyte content fields.
    For budget-aware paginated output use `paginate_tasks_json` instead.
    """
    formatted = [format_task_json(t, tz_name, content_max_chars=content_max_chars) for t in tasks]
    result: dict[str, Any] = {
        "count": len(tasks),
        "tasks": formatted,
    }
    if content_max_chars is not None and any(t.get("content_truncated") for t in formatted):
        result["_content_hint"] = (
            f"Some content fields are truncated to {content_max_chars} chars. "
            "Use ticktick_get_task(task_id) for the full note."
        )
    return result


# Per-task content cap for list views (~one or two tweets); the model can
# call ticktick_get_task to retrieve the full notes when needed.
LIST_CONTENT_MAX_CHARS = 500


def paginate_tasks_markdown(
    tasks: list[Task],
    title: str,
    offset: int,
    tz_name: str = "UTC",
    project_names: dict[str, str] | None = None,
    budget: int = CHARACTER_LIMIT,
) -> str:
    """Paginated, budget-aware markdown rendering of a task list."""
    return paginate_markdown(
        tasks,
        title=title,
        offset=offset,
        format_item=lambda t: format_task_row_markdown(t, tz_name, project_names),
        item_label="tasks",
        budget=budget,
    )


def paginate_tasks_json(
    tasks: list[Task],
    offset: int,
    tz_name: str = "UTC",
    content_max_chars: int = LIST_CONTENT_MAX_CHARS,
    budget: int = CHARACTER_LIMIT,
) -> dict[str, Any]:
    """Paginated, budget-aware JSON rendering of a task list.

    Each task's `content` is capped at `content_max_chars` (default 500).
    When any task hits the cap, a `_content_hint` is added at the top level
    pointing the caller at `ticktick_get_task` for the full text.
    """
    result = paginate_json(
        tasks,
        offset=offset,
        format_item=lambda t: format_task_json(t, tz_name, content_max_chars=content_max_chars),
        budget=budget,
        item_key="tasks",
    )
    if any(t.get("content_truncated") for t in result["tasks"]):
        result["_content_hint"] = (
            f"Some content fields are truncated to {content_max_chars} chars. "
            "Use ticktick_get_task(task_id) for the full note."
        )
    return result


# =============================================================================
# Project Formatting
# =============================================================================


def format_project_markdown(project: Project) -> str:
    """Format a single project as Markdown."""
    lines = []

    lines.append(f"## {project.name}")
    lines.append("")
    lines.append(f"- **ID**: `{project.id}`")
    lines.append(f"- **Kind**: {project.kind or 'TASK'}")
    lines.append(f"- **View Mode**: {project.view_mode or 'list'}")

    if project.color:
        lines.append(f"- **Color**: {project.color}")
    if project.group_id:
        lines.append(f"- **Folder**: `{project.group_id}`")
    if project.closed:
        lines.append("- **Status**: Archived")

    return "\n".join(lines)


def format_project_json(project: Project) -> dict[str, Any]:
    """Format a single project as JSON."""
    return {
        "id": project.id,
        "name": project.name,
        "color": project.color,
        "kind": project.kind,
        "view_mode": project.view_mode,
        "group_id": project.group_id,
        "closed": project.closed,
        "sort_order": project.sort_order,
    }


def format_project_row_markdown(project: Project) -> str:
    """Format a single project as one markdown list row."""
    color_indicator = f"({project.color})" if project.color else ""
    return f"- **{project.name}** (`{project.id}`) {color_indicator}".rstrip()


def format_projects_markdown(projects: list[Project], title: str = "Projects") -> str:
    """Format multiple projects as Markdown (non-paginated convenience wrapper)."""
    if not projects:
        return f"# {title}\n\nNo projects found."

    lines = [f"# {title}", "", f"Found {len(projects)} project(s):", ""]
    for project in projects:
        lines.append(format_project_row_markdown(project))
    return "\n".join(lines)


def format_projects_json(projects: list[Project]) -> dict[str, Any]:
    """Format multiple projects as JSON."""
    return {
        "count": len(projects),
        "projects": [format_project_json(p) for p in projects],
    }


def paginate_projects_markdown(
    projects: list[Project],
    offset: int,
    title: str = "Projects",
    budget: int = CHARACTER_LIMIT,
) -> str:
    return paginate_markdown(
        projects,
        title=title,
        offset=offset,
        format_item=format_project_row_markdown,
        item_label="projects",
        budget=budget,
    )


def paginate_projects_json(
    projects: list[Project],
    offset: int,
    budget: int = CHARACTER_LIMIT,
) -> dict[str, Any]:
    return paginate_json(
        projects,
        offset=offset,
        format_item=format_project_json,
        budget=budget,
        item_key="projects",
    )


# =============================================================================
# Tag Formatting
# =============================================================================


def format_tag_markdown(tag: Tag) -> str:
    """Format a single tag as Markdown."""
    lines = []

    lines.append(f"## {tag.label}")
    lines.append("")
    lines.append(f"- **Name**: `{tag.name}`")

    if tag.color:
        lines.append(f"- **Color**: {tag.color}")
    if tag.parent:
        lines.append(f"- **Parent**: `{tag.parent}`")

    return "\n".join(lines)


def format_tag_json(tag: Tag) -> dict[str, Any]:
    """Format a single tag as JSON."""
    return {
        "name": tag.name,
        "label": tag.label,
        "color": tag.color,
        "parent": tag.parent,
        "sort_order": tag.sort_order,
    }


def format_tag_row_markdown(tag: Tag) -> str:
    """Format a single tag as one markdown list row."""
    color_indicator = f"({tag.color})" if tag.color else ""
    parent_indicator = f" (in {tag.parent})" if tag.parent else ""
    return f"- **{tag.label}** (`{tag.name}`) {color_indicator}{parent_indicator}".rstrip()


def format_tags_markdown(tags: list[Tag], title: str = "Tags") -> str:
    """Format multiple tags as Markdown (non-paginated convenience wrapper)."""
    if not tags:
        return f"# {title}\n\nNo tags found."

    lines = [f"# {title}", "", f"Found {len(tags)} tag(s):", ""]
    for tag in tags:
        lines.append(format_tag_row_markdown(tag))
    return "\n".join(lines)


def format_tags_json(tags: list[Tag]) -> dict[str, Any]:
    """Format multiple tags as JSON."""
    return {
        "count": len(tags),
        "tags": [format_tag_json(t) for t in tags],
    }


def paginate_tags_markdown(
    tags: list[Tag],
    offset: int,
    title: str = "Tags",
    budget: int = CHARACTER_LIMIT,
) -> str:
    return paginate_markdown(
        tags,
        title=title,
        offset=offset,
        format_item=format_tag_row_markdown,
        item_label="tags",
        budget=budget,
    )


def paginate_tags_json(
    tags: list[Tag],
    offset: int,
    budget: int = CHARACTER_LIMIT,
) -> dict[str, Any]:
    return paginate_json(
        tags,
        offset=offset,
        format_item=format_tag_json,
        budget=budget,
        item_key="tags",
    )


# =============================================================================
# Folder Formatting
# =============================================================================


def format_folder_markdown(folder: ProjectGroup) -> str:
    """Format a single folder as Markdown."""
    return f"- **{folder.name}** (`{folder.id}`)"


def format_folder_json(folder: ProjectGroup) -> dict[str, Any]:
    """Format a single folder as JSON."""
    return {
        "id": folder.id,
        "name": folder.name,
        "sort_order": folder.sort_order,
    }


def format_folders_markdown(folders: list[ProjectGroup], title: str = "Folders") -> str:
    """Format multiple folders as Markdown (non-paginated convenience wrapper)."""
    if not folders:
        return f"# {title}\n\nNo folders found."

    lines = [f"# {title}", "", f"Found {len(folders)} folder(s):", ""]
    for folder in folders:
        lines.append(format_folder_markdown(folder))
    return "\n".join(lines)


def format_folders_json(folders: list[ProjectGroup]) -> dict[str, Any]:
    """Format multiple folders as JSON."""
    return {
        "count": len(folders),
        "folders": [format_folder_json(f) for f in folders],
    }


def paginate_folders_markdown(
    folders: list[ProjectGroup],
    offset: int,
    title: str = "Folders",
    budget: int = CHARACTER_LIMIT,
) -> str:
    return paginate_markdown(
        folders,
        title=title,
        offset=offset,
        format_item=format_folder_markdown,
        item_label="folders",
        budget=budget,
    )


def paginate_folders_json(
    folders: list[ProjectGroup],
    offset: int,
    budget: int = CHARACTER_LIMIT,
) -> dict[str, Any]:
    return paginate_json(
        folders,
        offset=offset,
        format_item=format_folder_json,
        budget=budget,
        item_key="folders",
    )


# =============================================================================
# Column Formatting (Kanban)
# =============================================================================


def format_column_markdown(column: Column) -> str:
    """Format a single column as Markdown."""
    return f"- **{column.name}** (`{column.id}`) - Sort: {column.sort_order or 0}"


def format_column_json(column: Column, tz_name: str = "UTC") -> dict[str, Any]:
    """Format a single column as JSON."""
    created_time = convert_tz(column.created_time, tz_name)
    modified_time = convert_tz(column.modified_time, tz_name)
    return {
        "id": column.id,
        "project_id": column.project_id,
        "name": column.name,
        "sort_order": column.sort_order,
        "created_time": created_time.isoformat() if created_time else None,
        "modified_time": modified_time.isoformat() if modified_time else None,
        "etag": column.etag,
    }


def format_columns_markdown(columns: list[Column], title: str = "Kanban Columns") -> str:
    """Format multiple columns as Markdown (non-paginated convenience wrapper)."""
    if not columns:
        return f"# {title}\n\nNo columns found."

    lines = [f"# {title}", "", f"Found {len(columns)} column(s):", ""]
    sorted_columns = sorted(columns, key=lambda c: c.sort_order or 0)
    for column in sorted_columns:
        lines.append(format_column_markdown(column))
    return "\n".join(lines)


def format_columns_json(columns: list[Column], tz_name: str = "UTC") -> dict[str, Any]:
    """Format multiple columns as JSON."""
    return {
        "count": len(columns),
        "columns": [format_column_json(c, tz_name) for c in columns],
    }


def paginate_columns_markdown(
    columns: list[Column],
    offset: int,
    title: str = "Kanban Columns",
    budget: int = CHARACTER_LIMIT,
) -> str:
    sorted_columns = sorted(columns, key=lambda c: c.sort_order or 0)
    return paginate_markdown(
        sorted_columns,
        title=title,
        offset=offset,
        format_item=format_column_markdown,
        item_label="columns",
        budget=budget,
    )


def paginate_columns_json(
    columns: list[Column],
    offset: int,
    tz_name: str = "UTC",
    budget: int = CHARACTER_LIMIT,
) -> dict[str, Any]:
    sorted_columns = sorted(columns, key=lambda c: c.sort_order or 0)
    return paginate_json(
        sorted_columns,
        offset=offset,
        format_item=lambda c: format_column_json(c, tz_name),
        budget=budget,
        item_key="columns",
    )


# =============================================================================
# User Formatting
# =============================================================================


def format_user_markdown(user: User) -> str:
    """Format user profile as Markdown."""
    lines = ["# User Profile", ""]

    lines.append(f"- **Username**: {user.username}")
    if user.display_name:
        lines.append(f"- **Display Name**: {user.display_name}")
    if user.name:
        lines.append(f"- **Name**: {user.name}")
    if user.email:
        lines.append(f"- **Email**: {user.email}")
    if user.locale:
        lines.append(f"- **Locale**: {user.locale}")
    lines.append(f"- **Verified Email**: {'Yes' if user.verified_email else 'No'}")

    return "\n".join(lines)


def format_user_status_markdown(status: UserStatus) -> str:
    """Format user status as Markdown."""
    lines = ["# Account Status", ""]

    lines.append(f"- **Username**: {status.username}")
    lines.append(f"- **User ID**: {status.user_id}")
    lines.append(f"- **Inbox ID**: {status.inbox_id}")
    lines.append(f"- **Pro Account**: {'Yes' if status.is_pro else 'No'}")

    if status.is_pro and status.pro_end_date:
        lines.append(f"- **Pro Expires**: {status.pro_end_date}")

    lines.append(f"- **Team User**: {'Yes' if status.team_user else 'No'}")

    return "\n".join(lines)


def format_statistics_markdown(stats: UserStatistics) -> str:
    """Format user statistics as Markdown."""
    lines = ["# Productivity Statistics", ""]

    lines.append(f"- **Level**: {stats.level}")
    lines.append(f"- **Score**: {stats.score}")
    lines.append("")

    lines.append("## Task Completion")
    lines.append(f"- Today: {stats.today_completed}")
    lines.append(f"- Yesterday: {stats.yesterday_completed}")
    lines.append(f"- All Time: {stats.total_completed}")
    lines.append("")

    if stats.total_pomo_count > 0:
        lines.append("## Focus/Pomodoro")
        lines.append(f"- Today: {stats.today_pomo_count} pomos ({stats.today_pomo_duration_minutes:.1f} min)")
        lines.append(f"- Yesterday: {stats.yesterday_pomo_count} pomos")
        lines.append(f"- All Time: {stats.total_pomo_count} pomos ({stats.total_pomo_duration_hours:.1f} hours)")

    return "\n".join(lines)


# =============================================================================
# Response Helpers
# =============================================================================


def format_response(
    data: Any,
    response_format: ResponseFormat,
    markdown_formatter: Callable[[Any], str],
    json_formatter: Callable[[Any], dict[str, Any]],
) -> str:
    """
    Format a response based on the requested format.

    Args:
        data: The data to format
        response_format: Desired output format
        markdown_formatter: Function to format as Markdown
        json_formatter: Function to format as JSON dict

    Returns:
        Formatted string response
    """
    if response_format == ResponseFormat.MARKDOWN:
        result = markdown_formatter(data)
    else:
        result = json.dumps(json_formatter(data), indent=2, default=str)

    # Check character limit
    if len(result) > CHARACTER_LIMIT:
        if response_format == ResponseFormat.MARKDOWN:
            return (
                f"{result[:CHARACTER_LIMIT]}\n\n"
                f"---\n"
                f"*Response truncated. Use filters to narrow results.*"
            )
        else:
            return json.dumps({
                "truncated": True,
                "message": "Response truncated due to size. Use filters to narrow results.",
                "partial_data": result[:CHARACTER_LIMIT],
            })

    return result


def success_message(message: str) -> str:
    """Format a success message."""
    return f"**Success**: {message}"


def error_message(error: str, suggestion: str | None = None) -> str:
    """Format an error message with optional suggestion."""
    msg = f"**Error**: {error}"
    if suggestion:
        msg += f"\n\n*Suggestion*: {suggestion}"
    return msg


# =============================================================================
# Batch Operation Formatting
# =============================================================================


def format_batch_create_tasks_markdown(tasks: list[Task], tz_name: str = "UTC") -> str:
    """Format batch task creation results as Markdown."""
    if not tasks:
        return "# Tasks Created\n\nNo tasks were created."

    lines = [f"# {len(tasks)} Task(s) Created", ""]

    for task in tasks:
        priority_str = priority_indicator(task.priority)
        task_title = task.title or "(No title)"
        if task.due_date:
            if task.is_all_day:
                due_str = f" | Due: {all_day_date(task.due_date, is_end_boundary=True)}"
            else:
                due_str = f" | Due: {format_date(task.due_date, tz_name)}"
        else:
            due_str = ""
        lines.append(f"- {priority_str} **{task_title}** (`{task.id}`){due_str}")

    return "\n".join(lines)


def format_batch_create_tasks_json(tasks: list[Task], tz_name: str = "UTC") -> dict[str, Any]:
    """Format batch task creation results as JSON."""
    return {
        "success": True,
        "count": len(tasks),
        "tasks": [format_task_json(t, tz_name) for t in tasks],
    }


def format_batch_update_tasks_markdown(
    results: dict[str, Any],
    update_count: int,
) -> str:
    """Format batch task update results as Markdown."""
    lines = [f"# {update_count} Task(s) Updated", ""]

    if results.get("id2error"):
        lines.append("## Errors")
        for task_id, error in results["id2error"].items():
            lines.append(f"- `{task_id}`: {error}")
        lines.append("")

    if results.get("id2etag"):
        lines.append("## Updated Tasks")
        for task_id in results["id2etag"]:
            lines.append(f"- `{task_id}` updated successfully")

    return "\n".join(lines)


def format_batch_update_tasks_json(
    results: dict[str, Any],
    update_count: int,
) -> dict[str, Any]:
    """Format batch task update results as JSON."""
    return {
        "success": not results.get("id2error"),
        "count": update_count,
        "updated_ids": list(results.get("id2etag", {}).keys()),
        "errors": results.get("id2error", {}),
    }


def format_batch_delete_tasks_markdown(
    count: int,
    task_ids: list[str],
) -> str:
    """Format batch task deletion results as Markdown."""
    lines = [f"# {count} Task(s) Deleted", ""]
    lines.append("Tasks moved to trash:")
    for task_id in task_ids:
        lines.append(f"- `{task_id}`")
    return "\n".join(lines)


def format_batch_delete_tasks_json(
    count: int,
    task_ids: list[str],
) -> dict[str, Any]:
    """Format batch task deletion results as JSON."""
    return {
        "success": True,
        "count": count,
        "deleted_ids": task_ids,
    }


def format_batch_complete_tasks_markdown(
    count: int,
    task_ids: list[str],
) -> str:
    """Format batch task completion results as Markdown."""
    lines = [f"# {count} Task(s) Completed", ""]
    for task_id in task_ids:
        lines.append(f"- `{task_id}` marked as completed")
    return "\n".join(lines)


def format_batch_complete_tasks_json(
    count: int,
    task_ids: list[str],
) -> dict[str, Any]:
    """Format batch task completion results as JSON."""
    return {
        "success": True,
        "count": count,
        "completed_ids": task_ids,
    }


def format_batch_move_tasks_markdown(
    moves: list[dict[str, str]],
) -> str:
    """Format batch task move results as Markdown."""
    if not moves:
        return "# Tasks Moved\n\nNo tasks were moved."

    lines = [f"# {len(moves)} Task(s) Moved", ""]
    for move in moves:
        lines.append(
            f"- `{move['task_id']}`: "
            f"`{move['from_project_id']}` → `{move['to_project_id']}`"
        )
    return "\n".join(lines)


def format_batch_move_tasks_json(
    moves: list[dict[str, str]],
) -> dict[str, Any]:
    """Format batch task move results as JSON."""
    return {
        "success": True,
        "count": len(moves),
        "moves": moves,
    }


def format_batch_set_parents_markdown(
    results: list[dict[str, Any]],
) -> str:
    """Format batch set parent results as Markdown."""
    if not results:
        return "# Subtasks Created\n\nNo parent assignments made."

    lines = [f"# {len(results)} Subtask Assignment(s)", ""]
    for result in results:
        lines.append(
            f"- `{result['task_id']}` → parent `{result['parent_id']}`"
        )
    return "\n".join(lines)


def format_batch_set_parents_json(
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Format batch set parent results as JSON."""
    return {
        "success": True,
        "count": len(results),
        "assignments": results,
    }


def format_batch_unparent_tasks_markdown(
    results: list[dict[str, Any]],
) -> str:
    """Format batch unparent results as Markdown."""
    if not results:
        return "# Tasks Unparented\n\nNo tasks were unparented."

    lines = [f"# {len(results)} Task(s) Made Top-Level", ""]
    for result in results:
        lines.append(f"- `{result['task_id']}` removed from parent")
    return "\n".join(lines)


def format_batch_unparent_tasks_json(
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Format batch unparent results as JSON."""
    return {
        "success": True,
        "count": len(results),
        "unparented": results,
    }


def format_batch_pin_tasks_markdown(
    tasks: list[Task],
) -> str:
    """Format batch pin/unpin results as Markdown."""
    if not tasks:
        return "# Task Pin Status\n\nNo pin operations performed."

    pinned = [t for t in tasks if t.is_pinned]
    unpinned = [t for t in tasks if not t.is_pinned]

    lines = [f"# {len(tasks)} Task Pin Operation(s)", ""]

    if pinned:
        lines.append(f"## Pinned ({len(pinned)})")
        for task in pinned:
            lines.append(f"- **{task.title or '(No title)'}** (`{task.id}`)")
        lines.append("")

    if unpinned:
        lines.append(f"## Unpinned ({len(unpinned)})")
        for task in unpinned:
            lines.append(f"- **{task.title or '(No title)'}** (`{task.id}`)")

    return "\n".join(lines)


def format_batch_pin_tasks_json(
    tasks: list[Task],
) -> dict[str, Any]:
    """Format batch pin/unpin results as JSON."""
    return {
        "success": True,
        "count": len(tasks),
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "is_pinned": t.is_pinned,
            }
            for t in tasks
        ],
    }
