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
- the requested page must exist in the window (limit + offset),
- an active post-filter widens the window to at least 500,
- the window never exceeds 1000 (plus the +1 saturation probe),
- one task is always probed past the window, so a saturated window keeps
  next_offset non-null and paging converges on the true end instead of
  presenting a truncated window as complete.
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
async def test_plain_fetch_covers_requested_page(status):
    # Page 2 must exist inside the fetched window: limit + offset (+1 probe).
    # Fetching only `limit` used to make every page after the first come back
    # empty.
    client = MatrixFakeClient(_matrix_tasks())
    await server.ticktick_list_tasks(
        TaskListInput(status=status, limit=50, offset=50, response_format="json"),
        _ctx(client),
    )
    assert client.fetch_limits == [101]


@pytest.mark.parametrize("status", FETCHED_STATUSES)
async def test_post_filter_widens_fetch_window(status):
    # With a filter active, fetching only `limit` tasks across all projects
    # would let other projects' tasks crowd out the matches. Floor of 500
    # (+1 probe).
    client = MatrixFakeClient(_matrix_tasks())
    await server.ticktick_list_tasks(
        TaskListInput(status=status, project_id=PROJ, limit=5, response_format="json"),
        _ctx(client),
    )
    assert client.fetch_limits == [501]


async def test_fetch_window_capped_at_1000():
    client = MatrixFakeClient(_matrix_tasks())
    await server.ticktick_list_tasks(
        TaskListInput(status="completed", project_id=PROJ, limit=500, offset=900,
                      response_format="json"),
        _ctx(client),
    )
    assert client.fetch_limits == [1001]


class SaturatingFakeClient(MatrixFakeClient):
    """Honors the requested limit like real TickTick: returns at most `limit`
    tasks, so a window smaller than the task set comes back saturated."""

    async def get_completed_tasks(self, days=7, limit=100, from_date=None, to_date=None):
        self.fetch_limits.append(limit)
        return list(self._tasks)[:limit]


def _many_tasks(n: int) -> list[Task]:
    return [
        Task(id=f"{i:024x}", project_id=PROJ, title=f"T{i}") for i in range(n)
    ]


async def test_saturated_window_keeps_next_offset_non_null():
    # Live repro (2026-07-22): 150 tasks in range, limit=5 offset=140 fetched
    # exactly 145 and reported total=145/next_offset=null, presenting the
    # truncated window as complete. The +1 probe returns one extra task, so
    # next_offset stays non-null and the pager knows to keep going.
    client = SaturatingFakeClient(_many_tasks(150))
    out = await server.ticktick_list_tasks(
        TaskListInput(status="completed", limit=5, offset=140, response_format="json"),
        _ctx(client),
    )
    d = json.loads(out)
    assert d["count"] == 5
    assert d["next_offset"] is not None  # pre-probe: null <-- the lie


async def test_paging_a_saturated_window_converges_on_true_end():
    # Walking next_offset grows the window each page and must terminate at
    # the real total with every task seen exactly once.
    client = SaturatingFakeClient(_many_tasks(150))
    seen, offset, guard = [], 0, 0
    while offset is not None and guard < 100:
        out = await server.ticktick_list_tasks(
            TaskListInput(status="completed", limit=50, offset=offset,
                          response_format="json"),
            _ctx(client),
        )
        d = json.loads(out)
        seen.extend(t["id"] for t in d["tasks"])
        offset = d["next_offset"]
        guard += 1
    assert len(seen) == 150
    assert len(set(seen)) == 150
    assert d["total"] == 150  # exact once the window outgrows the data
