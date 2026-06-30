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
