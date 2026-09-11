"""
Tool-level tests for ticktick_search_tasks / ticktick_list_tasks pagination,
sorting, and filtering.

These drive the actual MCP tool functions (not just the paginators) through a
lightweight fake Context, so they catch the *tool-level* pre-slice bug that the
paginator-only tests can't see: the headline regression is that a small `limit`
must report the true `total` and a non-null `next_offset`.

Mirrors the live repro: search "Daily brief" with limit=5 used to return
total=5, next_offset=null while 47 notes actually matched.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from ticktick_sdk import server
from ticktick_sdk.models import Task
from ticktick_sdk.tools.inputs import SearchInput, TaskListInput

pytestmark = pytest.mark.unit

UTC = timezone.utc
PROJ = "a" * 24          # valid 24-hex project id (SearchInput validates the pattern)
OTHER_PROJ = "b" * 24


class FakeClient:
    """Minimal stand-in for TickTickClient.

    The JSON render path only calls `get_all_tasks()`; markdown additionally
    calls `get_all_projects()`. That's all these tools need from the client.
    """

    def __init__(self, tasks, projects=None):
        self._tasks = tasks
        self._projects = projects or []

    async def get_all_tasks(self):
        return list(self._tasks)

    async def get_all_projects(self):
        return list(self._projects)


def _ctx(client: FakeClient) -> SimpleNamespace:
    return SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context={"client": client})
    )


def make_briefs(n: int, project_id: str = PROJ) -> list[Task]:
    """n NOTE tasks titled 'Daily brief NN' with strictly increasing created_time
    (index 0 = oldest, index n-1 = newest)."""
    base = datetime(2026, 4, 1, tzinfo=UTC)
    return [
        Task(
            id=f"{i:024x}",
            project_id=project_id,
            title=f"Daily brief {i:02d}",
            kind="NOTE",
            created_time=base + timedelta(days=i),
            due_date=base + timedelta(days=i),
        )
        for i in range(n)
    ]


# =============================================================================
# search_tasks — the headline regression
# =============================================================================


async def test_search_small_limit_reports_true_total():
    client = FakeClient(make_briefs(47))
    out = await server.ticktick_search_tasks(
        SearchInput(query="Daily brief", limit=5, response_format="json"), _ctx(client)
    )
    d = json.loads(out)
    assert d["total"] == 47        # pre-fix: 5  <-- the bug
    assert d["count"] == 5
    assert d["next_offset"] == 5   # pre-fix: None <-- the bug


async def test_search_markdown_small_limit_shows_true_total():
    client = FakeClient(make_briefs(47))
    out = await server.ticktick_search_tasks(
        SearchInput(query="Daily brief", limit=5, response_format="markdown"), _ctx(client)
    )
    assert "of 47 total" in out
    assert "offset=5" in out


async def test_search_paging_walks_all_matches():
    client = FakeClient(make_briefs(47))
    seen, offset, guard = [], 0, 0
    while offset is not None and guard < 100:
        out = await server.ticktick_search_tasks(
            SearchInput(query="Daily brief", limit=10, offset=offset, response_format="json"),
            _ctx(client),
        )
        d = json.loads(out)
        seen.extend(t["id"] for t in d["tasks"])
        offset = d["next_offset"]
        guard += 1
    assert len(seen) == 47
    assert len(set(seen)) == 47


# =============================================================================
# search_tasks — sorting
# =============================================================================


async def test_search_default_sort_is_newest_first():
    client = FakeClient(make_briefs(47))
    out = await server.ticktick_search_tasks(
        SearchInput(query="Daily brief", limit=3, response_format="json"), _ctx(client)
    )
    titles = [t["title"] for t in json.loads(out)["tasks"]]
    assert titles == ["Daily brief 46", "Daily brief 45", "Daily brief 44"]


async def test_search_sort_created_asc_oldest_first():
    client = FakeClient(make_briefs(47))
    out = await server.ticktick_search_tasks(
        SearchInput(query="Daily brief", limit=3, sort="created_asc", response_format="json"),
        _ctx(client),
    )
    titles = [t["title"] for t in json.loads(out)["tasks"]]
    assert titles == ["Daily brief 00", "Daily brief 01", "Daily brief 02"]


# =============================================================================
# search_tasks — filters + optional query
# =============================================================================


async def test_search_optional_query_filter_only_latest_note():
    """The 'latest NOTE in project X' use case: no text query, just filters."""
    notes = make_briefs(5, project_id=PROJ)
    other = [
        Task(id="f" * 24, project_id=OTHER_PROJ, title="unrelated", kind="TEXT",
             created_time=datetime(2026, 1, 1, tzinfo=UTC))
    ]
    client = FakeClient(notes + other)
    out = await server.ticktick_search_tasks(
        SearchInput(project_id=PROJ, kind="NOTE", sort="created_desc", limit=1,
                    response_format="json"),
        _ctx(client),
    )
    d = json.loads(out)
    assert d["total"] == 5                     # only the 5 notes in PROJ
    assert d["count"] == 1
    assert d["tasks"][0]["title"] == "Daily brief 04"   # newest


async def test_search_kind_filter_excludes_other_kinds():
    notes = make_briefs(3, project_id=PROJ)
    text_task = Task(id="c" * 24, project_id=PROJ, title="Daily brief text", kind="TEXT",
                     created_time=datetime(2026, 9, 9, tzinfo=UTC))
    client = FakeClient(notes + [text_task])
    out = await server.ticktick_search_tasks(
        SearchInput(query="Daily brief", kind="NOTE", response_format="json"), _ctx(client)
    )
    d = json.loads(out)
    assert d["total"] == 3
    assert all(t["kind"] == "NOTE" for t in d["tasks"])


async def test_search_kind_list_includes_multiple_kinds():
    """kind accepts a list: ['TEXT','CHECKLIST'] returns both but drops NOTE."""
    tasks = [
        Task(id="a" * 24, project_id=PROJ, title="Daily brief note", kind="NOTE",
             created_time=datetime(2026, 1, 1, tzinfo=UTC)),
        Task(id="b" * 24, project_id=PROJ, title="Daily brief text", kind="TEXT",
             created_time=datetime(2026, 1, 2, tzinfo=UTC)),
        Task(id="c" * 24, project_id=PROJ, title="Daily brief list", kind="CHECKLIST",
             created_time=datetime(2026, 1, 3, tzinfo=UTC)),
    ]
    out = await server.ticktick_search_tasks(
        SearchInput(query="Daily brief", kind=["TEXT", "CHECKLIST"], response_format="json"),
        _ctx(FakeClient(tasks)),
    )
    d = json.loads(out)
    assert d["total"] == 2
    assert {t["kind"] for t in d["tasks"]} == {"TEXT", "CHECKLIST"}


async def test_search_kind_rejects_invalid_value():
    """A bogus kind is rejected by the Literal validation."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        SearchInput(kind=["BOGUS"])


async def test_search_no_matches_is_clean_zero():
    client = FakeClient(make_briefs(5))
    out = await server.ticktick_search_tasks(
        SearchInput(query="nothing-matches-this", response_format="json"), _ctx(client)
    )
    d = json.loads(out)
    assert d["total"] == 0
    assert d["count"] == 0
    assert d["next_offset"] is None


# =============================================================================
# list_tasks — same total fix + sort override
# =============================================================================


async def test_list_tasks_small_limit_reports_true_total():
    tasks = [
        Task(id=f"{i:024x}", project_id=PROJ, title=f"T{i}",
             due_date=datetime(2026, 5, 1, tzinfo=UTC) + timedelta(days=i))
        for i in range(30)
    ]
    out = await server.ticktick_list_tasks(
        TaskListInput(status="active", limit=5, response_format="json"), _ctx(FakeClient(tasks))
    )
    d = json.loads(out)
    assert d["total"] == 30        # pre-fix: 5
    assert d["count"] == 5
    assert d["next_offset"] == 5   # pre-fix: None


async def test_list_tasks_default_active_order_is_due_ascending():
    # Without an explicit sort, active tasks keep the historical due-asc order.
    tasks = [
        Task(id="a" * 24, project_id=PROJ, title="late", due_date=datetime(2026, 12, 1, tzinfo=UTC)),
        Task(id="b" * 24, project_id=PROJ, title="early", due_date=datetime(2026, 1, 1, tzinfo=UTC)),
    ]
    out = await server.ticktick_list_tasks(
        TaskListInput(status="active", response_format="json"), _ctx(FakeClient(tasks))
    )
    titles = [t["title"] for t in json.loads(out)["tasks"]]
    assert titles == ["early", "late"]


async def test_list_tasks_sort_override_priority_desc():
    tasks = [
        Task(id=f"{i:024x}", project_id=PROJ, title=f"T{i}", priority=p)
        for i, p in enumerate([0, 5, 3, 1])
    ]
    out = await server.ticktick_list_tasks(
        TaskListInput(status="active", sort="priority_desc", response_format="json"),
        _ctx(FakeClient(tasks)),
    )
    prios = [t["priority"] for t in json.loads(out)["tasks"]]
    assert prios == [5, 3, 1, 0]


async def test_list_tasks_kind_list_excludes_notes():
    """list_tasks gained a kind filter; ['TEXT','CHECKLIST'] drops NOTE tasks."""
    tasks = [
        Task(id="a" * 24, project_id=PROJ, title="note", kind="NOTE"),
        Task(id="b" * 24, project_id=PROJ, title="text", kind="TEXT"),
        Task(id="c" * 24, project_id=PROJ, title="list", kind="CHECKLIST"),
    ]
    out = await server.ticktick_list_tasks(
        TaskListInput(status="active", kind=["TEXT", "CHECKLIST"], response_format="json"),
        _ctx(FakeClient(tasks)),
    )
    d = json.loads(out)
    assert d["total"] == 2
    assert {t["kind"] for t in d["tasks"]} == {"TEXT", "CHECKLIST"}


async def test_list_tasks_kind_scalar_coerced_to_list():
    """A single string still works: kind='NOTE' behaves like ['NOTE']."""
    tasks = [
        Task(id="a" * 24, project_id=PROJ, title="note", kind="NOTE"),
        Task(id="b" * 24, project_id=PROJ, title="text", kind="TEXT"),
    ]
    out = await server.ticktick_list_tasks(
        TaskListInput(status="active", kind="NOTE", response_format="json"),
        _ctx(FakeClient(tasks)),
    )
    d = json.loads(out)
    assert d["total"] == 1
    assert d["tasks"][0]["kind"] == "NOTE"


# =============================================================================
# in_trash — trashed tasks are flagged (the `deleted` axis is separate from
# `status`, so a trashed task otherwise reads as "Active")
# =============================================================================

from ticktick_sdk.tools.formatting import (  # noqa: E402
    format_task_json as _fmt_json,
    format_task_markdown as _fmt_md,
    format_task_row_markdown as _fmt_row,
)


def test_format_task_json_in_trash_only_when_trashed():
    trashed = Task(id="a" * 24, project_id=PROJ, title="binned", status=0, deleted=1)
    live = Task(id="b" * 24, project_id=PROJ, title="alive", status=0, deleted=0)
    # in_trash: true only when trashed; omitted otherwise, in BOTH the detail
    # view and the compact list view (no in_trash:false clutter anywhere).
    assert _fmt_json(trashed)["in_trash"] is True
    assert "in_trash" not in _fmt_json(live)
    assert _fmt_json(trashed, omit_defaults=True)["in_trash"] is True
    assert "in_trash" not in _fmt_json(live, omit_defaults=True)


def test_trashed_task_still_reads_active_status():
    # The whole point: a trashed task keeps status "Active"; in_trash is the
    # only signal that it's binned.
    trashed = Task(id="a" * 24, project_id=PROJ, title="binned", status=0, deleted=1)
    payload = _fmt_json(trashed)
    assert payload["status_label"] == "Active"
    assert payload["in_trash"] is True


def test_markdown_detail_and_row_flag_trash():
    trashed = Task(id="a" * 24, project_id=PROJ, title="binned", status=0, deleted=1)
    live = Task(id="b" * 24, project_id=PROJ, title="alive", status=0, deleted=0)
    assert "In trash" in _fmt_md(trashed)
    assert "[TRASH]" in _fmt_row(trashed)
    assert "In trash" not in _fmt_md(live)
    assert "[TRASH]" not in _fmt_row(live)


class _TrashFakeClient(FakeClient):
    """FakeClient that also serves a trash list (for status='deleted')."""

    def __init__(self, deleted_tasks):
        super().__init__(tasks=[], projects=[])
        self._deleted = deleted_tasks

    async def get_deleted_tasks(self, limit=100):
        return list(self._deleted)


async def test_list_tasks_deleted_forces_in_trash():
    # The trash endpoint may hand back deleted=0; the tool must still flag these
    # as in_trash because they came from the trash listing.
    binned = [Task(id="a" * 24, project_id=PROJ, title="binned note", status=0, deleted=0)]
    out = await server.ticktick_list_tasks(
        TaskListInput(status="deleted", response_format="json"),
        _ctx(_TrashFakeClient(binned)),
    )
    d = json.loads(out)
    assert d["total"] == 1
    assert d["tasks"][0]["in_trash"] is True


async def test_active_list_never_flags_in_trash():
    # Active tasks are never trashed (verified live: no leak), so in_trash is
    # never set on the active listing.
    out = await server.ticktick_list_tasks(
        TaskListInput(status="active", response_format="json"),
        _ctx(FakeClient([Task(id="b" * 24, project_id=PROJ, title="alive", status=0)])),
    )
    assert "in_trash" not in json.loads(out)["tasks"][0]


class _UpdateFakeClient(FakeClient):
    """FakeClient whose update_tasks echoes a pre-edit trash map like the real
    batch_update_tasks does (via the `_in_trash` key)."""

    def __init__(self, in_trash_by_id):
        super().__init__(tasks=[], projects=[])
        self._in_trash = in_trash_by_id

    async def update_tasks(self, specs):
        return {
            "id2etag": {s["task_id"]: "etag" for s in specs},
            "id2error": {},
            "_in_trash": dict(self._in_trash),
        }


async def test_update_tasks_response_flags_trashed_task():
    tid = "a" * 24
    from ticktick_sdk.tools.inputs import UpdateTasksInput
    out = await server.ticktick_update_tasks(
        UpdateTasksInput(
            tasks=[{"task_id": tid, "project_id": PROJ, "title": "edited"}],
            response_format="json",
        ),
        _ctx(_UpdateFakeClient({tid: True})),
    )
    d = json.loads(out)
    assert d["tasks"] == [{"task_id": tid, "in_trash": True}]
    # The internal derived key is stripped from the echoed raw response.
    assert "_in_trash" not in d["response"]


async def test_update_tasks_omits_in_trash_for_live_task():
    tid = "a" * 24
    from ticktick_sdk.tools.inputs import UpdateTasksInput
    out = await server.ticktick_update_tasks(
        UpdateTasksInput(
            tasks=[{"task_id": tid, "project_id": PROJ, "title": "edited"}],
            response_format="json",
        ),
        _ctx(_UpdateFakeClient({tid: False})),
    )
    # Not trashed -> no in_trash key at all (no false clutter).
    assert json.loads(out)["tasks"] == [{"task_id": tid}]


# =============================================================================
# Compact output + omit-defaults (search/list density)
# =============================================================================


async def test_search_output_is_compact_and_omits_defaults():
    out = await server.ticktick_search_tasks(
        SearchInput(query="Daily brief", response_format="json"), _ctx(FakeClient(make_briefs(3)))
    )
    # Compact JSON: no pretty-print indentation.
    assert "\n  " not in out
    t = json.loads(out)["tasks"][0]
    # Always-present identity + priority/status survive omission.
    assert {"id", "project_id", "title", "kind", "priority", "status"} <= set(t)
    # Briefs have no tags, aren't pinned, are top-level -> those defaults dropped.
    assert "tags" not in t
    assert "is_pinned" not in t
    assert "parent_id" not in t


async def test_list_tasks_omits_defaults_but_get_task_stays_full():
    # A bare active task through list_tasks drops default fields...
    bare = Task(id="a" * 24, project_id=PROJ, title="bare", status=0, priority=0)
    out = await server.ticktick_list_tasks(
        TaskListInput(status="active", response_format="json"), _ctx(FakeClient([bare]))
    )
    t = json.loads(out)["tasks"][0]
    assert "is_pinned" not in t and "tags" not in t and "items" not in t
    # ...while the detail view (get_task path, omit_defaults=False) keeps them.
    from ticktick_sdk.tools.formatting import format_task_json
    full = format_task_json(bare, omit_defaults=False)
    assert "is_pinned" in full and "tags" in full and "items" in full
