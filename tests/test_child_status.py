"""
Children show their own status and trash flag, so a completed or trashed
subtask never reads as active.

Live repro (2026-10-29): `ticktick_get_task` on a parent listed its completed
subtasks as `[MEDIUM] Call the venue (id)` with no `[DONE]` flag, and the JSON
children were only `{id, title, priority_label}`. Fetching the child itself
returned "Status: Completed". The detail view lists every child whatever its
status, and a missing flag means active, so the status has to travel with
each child. These tests drive the real `ticktick_get_task` tool through a fake
client, plus the formatters and the list/search child maps.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ticktick_sdk import server
from ticktick_sdk.models import Task
from ticktick_sdk.tools.formatting import (
    child_meta_entry,
    format_task_json,
    format_task_markdown,
    format_task_row_markdown,
    paginate_tasks_markdown,
    status_flag,
)
from ticktick_sdk.tools.inputs import ResponseFormat, TaskGetInput, TaskListInput

pytestmark = pytest.mark.unit

PROJ = "a" * 24
PARENT = "1" * 24
DONE_CHILD = "2" * 24
OPEN_CHILD = "3" * 24
DROPPED_CHILD = "4" * 24
BINNED_CHILD = "5" * 24


def _family(with_abandoned: bool = False, with_trashed: bool = False) -> dict[str, Task]:
    """A parent with one completed and one active child (and optionally an
    abandoned one and a trashed one), keyed by id."""
    child_ids = (
        [DONE_CHILD, OPEN_CHILD]
        + ([DROPPED_CHILD] if with_abandoned else [])
        + ([BINNED_CHILD] if with_trashed else [])
    )
    tasks = [
        Task(id=PARENT, project_id=PROJ, title="Plan the event",
             priority=5, child_ids=child_ids),
        Task(id=DONE_CHILD, project_id=PROJ, title="Call the venue",
             priority=3, status=2, parent_id=PARENT),
        Task(id=OPEN_CHILD, project_id=PROJ, title="Book a room",
             priority=5, status=0, parent_id=PARENT),
    ]
    if with_abandoned:
        tasks.append(Task(id=DROPPED_CHILD, project_id=PROJ, title="Old idea",
                          priority=1, status=-1, parent_id=PARENT))
    if with_trashed:
        # A trashed task keeps its status (here Active), only `deleted` flips.
        tasks.append(Task(id=BINNED_CHILD, project_id=PROJ, title="Binned step",
                          priority=0, status=0, deleted=1, parent_id=PARENT))
    return {t.id: t for t in tasks}


class FakeClient:
    """Serves `get_task` by id (the detail view fetches each child this way)
    and `get_all_tasks` with only the active tasks, like TickTick does."""

    def __init__(self, tasks: dict[str, Task]):
        self._tasks = tasks

    async def get_task(self, task_id, project_id=None):
        return self._tasks[task_id]

    async def get_project(self, project_id):
        return SimpleNamespace(id=project_id, name="Content")

    async def get_all_tasks(self):
        return [t for t in self._tasks.values() if t.status == 0 and not t.deleted]

    async def get_all_projects(self):
        return []


def _ctx(client: FakeClient) -> SimpleNamespace:
    return SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context={"client": client})
    )


def _child_line(markdown: str, child_id: str) -> str:
    return next(line for line in markdown.splitlines() if f"`{child_id}`" in line)


# =============================================================================
# The reported bug: ticktick_get_task
# =============================================================================


async def test_get_task_markdown_flags_completed_child_only():
    client = FakeClient(_family())
    out = await server.ticktick_get_task(
        TaskGetInput(task_id=PARENT, project_id=PROJ), _ctx(client)
    )
    assert _child_line(out, DONE_CHILD) == f"  - [MEDIUM] [DONE] Call the venue (`{DONE_CHILD}`)"
    assert _child_line(out, OPEN_CHILD) == f"  - [HIGH] Book a room (`{OPEN_CHILD}`)"


async def test_get_task_markdown_flags_abandoned_child():
    client = FakeClient(_family(with_abandoned=True))
    out = await server.ticktick_get_task(
        TaskGetInput(task_id=PARENT, project_id=PROJ), _ctx(client)
    )
    assert _child_line(out, DROPPED_CHILD) == f"  - [LOW] [ABANDONED] Old idea (`{DROPPED_CHILD}`)"


async def test_get_task_json_children_carry_status():
    client = FakeClient(_family(with_abandoned=True))
    out = await server.ticktick_get_task(
        TaskGetInput(task_id=PARENT, project_id=PROJ, response_format=ResponseFormat.JSON),
        _ctx(client),
    )
    children = {c["id"]: c for c in json.loads(out)["children"]}
    assert children[DONE_CHILD] == {
        "id": DONE_CHILD, "title": "Call the venue", "priority_label": "Medium",
        "status": 2, "status_label": "Completed",
    }
    assert children[OPEN_CHILD]["status"] == 0
    assert children[OPEN_CHILD]["status_label"] == "Active"
    assert children[DROPPED_CHILD]["status"] == -1
    assert children[DROPPED_CHILD]["status_label"] == "Abandoned"


async def test_get_task_flags_trashed_child():
    """A trashed child keeps status Active, so only the trash flag tells it
    apart from an open one."""
    client = FakeClient(_family(with_trashed=True))
    md = await server.ticktick_get_task(
        TaskGetInput(task_id=PARENT, project_id=PROJ), _ctx(client)
    )
    assert _child_line(md, BINNED_CHILD) == f"  - [NONE] [TRASH] Binned step (`{BINNED_CHILD}`)"
    js = await server.ticktick_get_task(
        TaskGetInput(task_id=PARENT, project_id=PROJ, response_format=ResponseFormat.JSON),
        _ctx(client),
    )
    children = {c["id"]: c for c in json.loads(js)["children"]}
    assert children[BINNED_CHILD]["in_trash"] is True
    assert children[BINNED_CHILD]["status_label"] == "Active"
    # Emitted only when trashed, like the task-level flag.
    assert "in_trash" not in children[OPEN_CHILD]
    assert "in_trash" not in children[DONE_CHILD]


def test_trashed_and_done_child_shows_both_flags():
    parent = _family()[PARENT]
    meta = {DONE_CHILD: {"title": "Call the venue", "priority": 3, "status": 2, "in_trash": True}}
    assert f"  - [MEDIUM] [TRASH] [DONE] Call the venue (`{DONE_CHILD}`)" in format_task_markdown(
        parent, child_meta=meta
    )


# =============================================================================
# One child_meta shape everywhere
# =============================================================================


def test_child_meta_entry_shape():
    fam = _family(with_trashed=True)
    assert child_meta_entry(fam[DONE_CHILD]) == {
        "title": "Call the venue", "priority": 3, "status": 2, "in_trash": False,
    }
    assert child_meta_entry(fam[BINNED_CHILD])["in_trash"] is True


@pytest.mark.parametrize("status, flag", [(0, ""), (1, "[DONE] "), (2, "[DONE] "), (-1, "[ABANDONED] ")])
def test_status_flag(status, flag):
    assert status_flag(status) == flag


async def test_active_list_children_are_active_and_unflagged():
    """`list_tasks` (active) builds its child map from the active list, so a
    completed child is hidden, never shown as active."""
    client = FakeClient(_family())
    out = await server.ticktick_list_tasks(
        TaskListInput(status="active", response_format=ResponseFormat.JSON), _ctx(client)
    )
    parent = next(t for t in json.loads(out)["tasks"] if t["id"] == PARENT)
    assert parent["children"] == [{
        "id": OPEN_CHILD, "title": "Book a room", "priority_label": "High",
        "status": 0, "status_label": "Active",
    }]
    assert parent["children_hidden"] == 1


def test_closed_list_children_show_done_flag():
    """A completed listing builds its child map from the listed tasks, so a
    completed child under a completed parent carries `[DONE]` like its own row."""
    fam = _family()
    parent = fam[PARENT].model_copy(update={"status": 2, "child_ids": [DONE_CHILD]})
    out = paginate_tasks_markdown([parent, fam[DONE_CHILD]], title="Completed Tasks", offset=0)
    assert f"  - [MEDIUM] [DONE] Call the venue (`{DONE_CHILD}`)" in out


async def test_trash_list_children_show_trash_flag():
    """`list_tasks(status="deleted")` marks every fetched task as trashed
    before rendering, so children in the trash listing get `[TRASH]` too."""
    fam = _family()
    parent = fam[PARENT].model_copy(update={"child_ids": [OPEN_CHILD]})
    child = fam[OPEN_CHILD]

    class TrashClient(FakeClient):
        async def get_deleted_tasks(self, limit=None):
            return [parent.model_copy(), child.model_copy()]

    out = await server.ticktick_list_tasks(
        TaskListInput(status="deleted"), _ctx(TrashClient(fam))
    )
    assert f"  - [HIGH] [TRASH] Book a room (`{OPEN_CHILD}`)" in out


# =============================================================================
# Formatter fallbacks
# =============================================================================


def test_meta_without_status_reads_as_active():
    """A hand-built map without `status` falls back to active, the old output."""
    parent = _family()[PARENT]
    meta = {DONE_CHILD: {"title": "Call the venue", "priority": 3}}
    md = format_task_markdown(parent, child_meta=meta)
    assert f"  - [MEDIUM] Call the venue (`{DONE_CHILD}`)" in md
    row = format_task_row_markdown(parent, child_meta=meta)
    assert f"  - [MEDIUM] Call the venue (`{DONE_CHILD}`)" in row
    js = format_task_json(parent, child_meta=meta)
    assert js["children"][0]["status_label"] == "Active"
