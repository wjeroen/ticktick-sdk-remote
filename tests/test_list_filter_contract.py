"""
Contract-test matrix: every filter that ticktick_list_tasks documents as
status-agnostic must actually filter on EVERY status.

Born from a live bug (2026-07-22): project_id was silently ignored for
status='completed' (and abandoned/deleted). The tool schema promised
"Filter by project ID" with no caveat, but only the 'active' branch applied
it, so a per-project completed query returned all projects' tasks. tag and
priority had the same gap. Tests only catch what they encode, so this file
encodes the cross-status filter contract as a parametrized matrix; any
future filter that claims to be status-agnostic should be added here.

Also covers the fetch-window rules for the capped closed/trash endpoints:
- every call fetches the same window (CLOSED_FETCH_WINDOW plus a +1
  saturation probe), whatever the limit, offset, or filters,
- so walking next_offset visits every task exactly once, even when the sort
  order differs from the order TickTick returns tasks in,
- and a saturated window marks every page incomplete (`complete: false`
  plus `_window_hint` in JSON, an "Incomplete:" line in markdown), even
  when a filter drops the probe task.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ticktick_sdk import server
from ticktick_sdk.models import Task
from ticktick_sdk.tools.inputs import TaskListInput

pytestmark = pytest.mark.unit

PROJ = "a" * 24
OTHER_PROJ = "b" * 24

ALL_STATUSES = ["active", "completed", "abandoned", "deleted"]
FETCHED_STATUSES = ["completed", "abandoned", "deleted"]  # capped-window endpoints


class MatrixFakeClient:
    """Serves the same task set through every status-specific fetch method and
    records the `limit` each capped fetch was called with (`fetch_limits`)."""

    def __init__(self, tasks, projects=None):
        self._tasks = tasks
        self._projects = projects or []
        self.fetch_limits: list[int] = []

    async def get_all_tasks(self):
        return list(self._tasks)

    async def get_all_projects(self):
        return list(self._projects)

    async def get_completed_tasks(self, days=7, limit=100, from_date=None, to_date=None):
        self.fetch_limits.append(limit)
        return list(self._tasks)

    async def get_abandoned_tasks(self, days=7, limit=100, from_date=None, to_date=None):
        self.fetch_limits.append(limit)
        return list(self._tasks)

    async def get_deleted_tasks(self, limit=100):
        self.fetch_limits.append(limit)
        return list(self._tasks)


def _ctx(client: MatrixFakeClient) -> SimpleNamespace:
    return SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context={"client": client})
    )


def _matrix_tasks() -> list[Task]:
    return [
        Task(id="a" * 24, project_id=PROJ, title="A: work, high, text",
             tags=["work"], priority=5, kind="TEXT"),
        Task(id="b" * 24, project_id=OTHER_PROJ, title="B: home, none, note",
             tags=["home"], priority=0, kind="NOTE"),
        Task(id="c" * 24, project_id=PROJ, title="C: untagged, medium, checklist",
             priority=3, kind="CHECKLIST"),
    ]


async def _list_ids(status: str, **filters) -> set[str]:
    out = await server.ticktick_list_tasks(
        TaskListInput(status=status, response_format="json", **filters),
        _ctx(MatrixFakeClient(_matrix_tasks())),
    )
    return {t["id"] for t in json.loads(out)["tasks"]}


# =============================================================================
# The matrix: each status-agnostic filter x each status
# =============================================================================


@pytest.mark.parametrize("status", ALL_STATUSES)
async def test_no_filter_returns_everything(status):
    assert await _list_ids(status) == {"a" * 24, "b" * 24, "c" * 24}


@pytest.mark.parametrize("status", ALL_STATUSES)
async def test_project_id_filters_every_status(status):
    # The live repro: status='completed' + project_id used to return ALL
    # projects' tasks.
    assert await _list_ids(status, project_id=PROJ) == {"a" * 24, "c" * 24}


@pytest.mark.parametrize("status", ALL_STATUSES)
async def test_tag_filters_every_status(status):
    # Tag matching is case-insensitive, as on the active branch historically.
    assert await _list_ids(status, tag="Work") == {"a" * 24}


@pytest.mark.parametrize("status", ALL_STATUSES)
async def test_priority_filters_every_status(status):
    assert await _list_ids(status, priority="medium") == {"c" * 24}


@pytest.mark.parametrize("status", ALL_STATUSES)
async def test_kind_filters_every_status(status):
    assert await _list_ids(status, kind=["TEXT", "CHECKLIST"]) == {"a" * 24, "c" * 24}


@pytest.mark.parametrize("status", ALL_STATUSES)
async def test_filters_combine(status):
    assert await _list_ids(status, project_id=PROJ, priority="high") == {"a" * 24}


# =============================================================================
# Fetch-window rules for the capped endpoints
# =============================================================================


@pytest.mark.parametrize("status", FETCHED_STATUSES)
@pytest.mark.parametrize(
    "limit,offset,filters",
    [
        (50, 0, {}),
        (50, 50, {}),
        (5, 0, {"project_id": PROJ}),
        (500, 900, {"project_id": PROJ}),
    ],
)
async def test_fetch_window_is_the_same_on_every_call(status, limit, offset, filters):
    # A window sized by limit + offset changed with every page, so each page
    # sorted a different set of tasks and paging repeated and skipped tasks.
    client = MatrixFakeClient(_matrix_tasks())
    await server.ticktick_list_tasks(
        TaskListInput(status=status, limit=limit, offset=offset,
                      response_format="json", **filters),
        _ctx(client),
    )
    assert client.fetch_limits == [server.CLOSED_FETCH_WINDOW + 1]


class TickTickOrderFakeClient(MatrixFakeClient):
    """Honors the requested limit like real TickTick and returns the highest
    ids first, the reverse of the default sort for these statuses. A bigger
    window then adds tasks that sort to the front, as on the live trash."""

    def _window(self, limit):
        self.fetch_limits.append(limit)
        return sorted(self._tasks, key=lambda t: t.id, reverse=True)[:limit]

    async def get_completed_tasks(self, days=7, limit=100, from_date=None, to_date=None):
        return self._window(limit)

    async def get_abandoned_tasks(self, days=7, limit=100, from_date=None, to_date=None):
        return self._window(limit)

    async def get_deleted_tasks(self, limit=100):
        return self._window(limit)


def _many_tasks(n: int) -> list[Task]:
    return [
        Task(id=f"{i:024x}", project_id=PROJ, title=f"T{i}") for i in range(n)
    ]


async def _walk(client, **params) -> tuple[list[str], dict]:
    """Follow next_offset from offset 0 and collect every task id shown."""
    seen, offset, guard, page = [], 0, 0, {}
    while offset is not None and guard < 100:
        out = await server.ticktick_list_tasks(
            TaskListInput(offset=offset, response_format="json", **params),
            _ctx(client),
        )
        page = json.loads(out)
        seen.extend(t["id"] for t in page["tasks"])
        offset = page["next_offset"]
        guard += 1
    return seen, page


@pytest.mark.parametrize("status", FETCHED_STATUSES)
@pytest.mark.parametrize("sort", [None, "created_asc"])
async def test_paging_visits_every_task_once(status, sort):
    # Live repro (2026-10-09): status='deleted', limit=10 returned the same 10
    # tasks at offsets 10 and 20, with total reading 11, 21, 31 while the
    # trash held 491. status='completed' with sort='created_asc' repeated
    # rows the same way.
    client = TickTickOrderFakeClient(_many_tasks(150))
    seen, last = await _walk(client, status=status, limit=10, sort=sort)
    assert len(seen) == 150
    assert len(set(seen)) == 150
    assert last["total"] == 150


def _mixed_tasks(n: int) -> list[Task]:
    """n tasks, alternating between PROJ and OTHER_PROJ."""
    return [
        Task(id=f"{i:024x}", project_id=PROJ if i % 2 else OTHER_PROJ, title=f"T{i}")
        for i in range(n)
    ]


@pytest.mark.parametrize("status", FETCHED_STATUSES)
@pytest.mark.parametrize("filters", [{}, {"project_id": PROJ}])
async def test_saturated_window_marks_every_page_incomplete(monkeypatch, status, filters):
    # Live repro (2026-07-27): completed + one project over a wide range
    # returned total=20, next_offset=null while older matches existed. The
    # filter dropped the probe task, so nothing showed the window was full.
    monkeypatch.setattr(server, "CLOSED_FETCH_WINDOW", 20)
    client = TickTickOrderFakeClient(_mixed_tasks(150))
    pages, offset = [], 0
    while offset is not None:
        out = await server.ticktick_list_tasks(
            TaskListInput(status=status, limit=5, offset=offset,
                          response_format="json", **filters),
            _ctx(client),
        )
        pages.append(json.loads(out))
        offset = pages[-1]["next_offset"]
    assert len(pages) > 1
    for page in pages:
        assert page["complete"] is False
        assert "21" in page["_window_hint"]
    assert pages[-1]["next_offset"] is None  # the last page still warns

    md = await server.ticktick_list_tasks(
        TaskListInput(status=status, response_format="markdown", **filters),
        _ctx(client),
    )
    assert md.splitlines()[2].startswith("**Incomplete:**")


@pytest.mark.parametrize("status", FETCHED_STATUSES)
async def test_window_with_room_left_is_not_flagged(status):
    client = MatrixFakeClient(_matrix_tasks())
    out = await server.ticktick_list_tasks(
        TaskListInput(status=status, response_format="json"), _ctx(client),
    )
    d = json.loads(out)
    assert "complete" not in d and "_window_hint" not in d
    md = await server.ticktick_list_tasks(
        TaskListInput(status=status, response_format="markdown"), _ctx(client),
    )
    assert "Incomplete" not in md


async def test_active_list_is_never_flagged(monkeypatch):
    # The active list is fetched whole, so a long list is still complete.
    monkeypatch.setattr(server, "CLOSED_FETCH_WINDOW", 20)
    out = await server.ticktick_list_tasks(
        TaskListInput(status="active", response_format="json"),
        _ctx(MatrixFakeClient(_mixed_tasks(150))),
    )
    assert "complete" not in json.loads(out)
