"""
Completing and abandoning tasks, above all recurring ones.

The historic bug: `ticktick_complete_tasks` sent a sparse V2 update, and because
V2 /batch/task replaces the task, TickTick wiped its start date, due date and
repeat rule, and no next occurrence was made. Live (2026-10-29): a daily task
came back Completed with no dates and no rule.

What TickTick does, checked live on 2026-11-01 with throwaway tasks:
  - V1 /task/completeTasks keeps every field. On a recurring task the same id
    stays active with new dates, and the finished occurrence becomes a new
    completed task with a new id and no repeat rule.
  - V1 /task/batch merges an update, so `{id, projectId, status}` keeps the
    other fields. It sets completedTime itself.
  - Status -1 on a recurring task closes it and ends the series. So abandoning
    one means completing it, then changing the completed copy to -1. The copy
    then leaves the completed list and the completion stats.
  - /task/completeTasks leaves an unknown id out of its reply.

These tests drive the real UnifiedTickTickAPI against `FakeTickTick`, which
mimics those rules, and inspect what is sent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import json

import pytest

from ticktick_sdk import server
from ticktick_sdk.constants import TaskStatus
from ticktick_sdk.exceptions import (
    TickTickAPIError,
    TickTickAPIUnavailableError,
    TickTickAuthenticationError,
    TickTickNotFoundError,
)
from ticktick_sdk.models import Task
from ticktick_sdk.tools.inputs import (
    AbandonTasksInput,
    CompleteTasksInput,
    DeleteTasksInput,
    MoveTasksInput,
    ResponseFormat,
    SetTaskParentsInput,
    UnparentTasksInput,
)
from ticktick_sdk.unified import api as api_module
from ticktick_sdk.unified.api import UnifiedTickTickAPI
from ticktick_sdk.unified.router import APIRouter

pytestmark = pytest.mark.unit

PROJ = "a" * 24
OTHER_PROJ = "b" * 24
DAY_ONE = "2026-10-31T07:00:00.000+0000"
DAY_TWO = "2026-11-01T07:00:00.000+0000"
DAILY = "RRULE:FREQ=DAILY;INTERVAL=1"


def _raw(task_id: str, *, project: str = PROJ, rule: str | None = None, **fields) -> dict:
    data = {
        "id": task_id,
        "projectId": project,
        "title": f"Task {task_id}",
        "status": 0,
        "startDate": DAY_ONE,
        "dueDate": DAY_ONE,
        "isAllDay": True,
        "timeZone": "America/Los_Angeles",
    }
    if rule:
        data["repeatFlag"] = rule
    data.update(fields)
    return data


class FakeTickTick:
    """Stored tasks plus mocked V1 and V2 clients that follow TickTick's rules."""

    def __init__(self, *tasks: dict, v1: bool = True, v2: bool = True):
        self.tasks = {t["id"]: dict(t) for t in tasks}
        self.next_copy = 0
        self.hide_copies = False

        self.v2 = MagicMock()
        self.v2.is_authenticated = True
        self.v2.get_task = AsyncMock(side_effect=self._get_v2)
        self.v2.batch_tasks = AsyncMock(return_value={"id2etag": {}, "id2error": {}})

        self.v1 = MagicMock()
        self.v1.is_authenticated = True
        self.v1.get_task = AsyncMock(side_effect=self._get_v1)
        self.v1.complete_tasks = AsyncMock(side_effect=self._complete)
        self.v1.batch_update_tasks = AsyncMock(side_effect=self._update)
        self.v1.get_completed_tasks = AsyncMock(side_effect=self._list_completed)

        self.api = UnifiedTickTickAPI(
            client_id="cid",
            client_secret="sec",
            v1_access_token="v1tok",
            username="user@example.com",
            password="pw",
        )
        self.api._v1_client = self.v1 if v1 else None
        self.api._v2_client = self.v2 if v2 else None
        self.api._router = APIRouter(
            v1_client=self.v1 if v1 else None,
            v2_client=self.v2 if v2 else None,
        )
        self.api._initialized = True

    async def _get_v2(self, task_id: str) -> dict:
        if task_id not in self.tasks:
            raise TickTickNotFoundError(f"Task not found: {task_id}")
        return dict(self.tasks[task_id])

    async def _get_v1(self, project_id: str, task_id: str) -> dict:
        return await self._get_v2(task_id)

    async def _complete(self, project_id: str, task_ids: list[str]) -> list[str]:
        done = []
        for task_id in task_ids:
            task = self.tasks.get(task_id)
            if task is None:
                continue  # TickTick leaves an unknown id out of its reply
            if task.get("repeatFlag"):
                self.next_copy += 1
                copy = {**task, "id": f"{self.next_copy:024x}", "status": 2}
                copy.pop("repeatFlag")
                copy["completedTime"] = "2026-11-01T00:24:09.207+0000"
                self.tasks[copy["id"]] = copy
                task["startDate"] = task["dueDate"] = DAY_TWO
            else:
                task["status"] = 2
            done.append(task_id)
        return done

    async def _update(self, updates: list[dict]) -> dict:
        for update in updates:
            self.tasks[update["id"]].update(update)
        return {"id2etag": {u["id"]: "e" for u in updates}, "id2error": {}}

    async def _list_completed(self, project_ids, start, end) -> list[dict]:
        if self.hide_copies:
            return []
        return [
            dict(t) for t in self.tasks.values()
            if t["projectId"] in project_ids and t["status"] == 2
        ]

    def v1_login_fails(self) -> None:
        error = TickTickAuthenticationError("401 from V1")
        self.v1.complete_tasks.side_effect = error
        self.v1.batch_update_tasks.side_effect = error

    def v1_login_fails_on_second_call(self) -> None:
        """The first V1 complete call works, every later one gets a 401."""
        calls = []

        async def first_call_only(project_id: str, task_ids: list[str]) -> list[str]:
            calls.append(project_id)
            if len(calls) > 1:
                raise TickTickAuthenticationError("401 from V1")
            return await self._complete(project_id, task_ids)

        self.v1.complete_tasks.side_effect = first_call_only

    @property
    def copies(self) -> list[dict]:
        return [t for t in self.tasks.values() if t["id"].startswith("0" * 10)]


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr(api_module.asyncio, "sleep", AsyncMock())


# =============================================================================
# Complete
# =============================================================================


class TestComplete:
    async def test_normal_task_goes_through_v1_only(self):
        tt = FakeTickTick(_raw("t1"))
        result = await tt.api.batch_complete_tasks([("t1", PROJ)])
        tt.v1.complete_tasks.assert_awaited_once_with(PROJ, ["t1"])
        tt.v2.batch_tasks.assert_not_called()
        assert result == {"completed": ["t1"], "already_completed": [], "next_occurrences": []}

    async def test_recurring_task_reports_its_next_occurrence(self):
        """The reported bug: the series must survive a completion."""
        tt = FakeTickTick(_raw("r1", rule=DAILY))
        result = await tt.api.batch_complete_tasks([("r1", PROJ)])
        tt.v2.batch_tasks.assert_not_called()
        assert result["completed"] == ["r1"]
        next_task = result["next_occurrences"][0]
        assert next_task.id == "r1"
        assert next_task.repeat_flag == DAILY
        assert next_task.due_date == datetime(2026, 11, 1, 7, tzinfo=timezone.utc)

    async def test_series_that_ended_is_not_a_next_occurrence(self):
        tt = FakeTickTick(_raw("r1", rule="RRULE:FREQ=DAILY;COUNT=1"))
        async def close_for_good(project_id, task_ids):
            tt.tasks["r1"]["status"] = 2
            return task_ids
        tt.v1.complete_tasks.side_effect = close_for_good
        result = await tt.api.batch_complete_tasks([("r1", PROJ)])
        assert result["next_occurrences"] == []

    async def test_one_call_per_project_and_per_fifty_tasks(self):
        here = [_raw(f"h{i}") for i in range(60)]
        there = [_raw(f"t{i}", project=OTHER_PROJ) for i in range(2)]
        tt = FakeTickTick(*here, *there)
        pairs = [(t["id"], t["projectId"]) for t in here + there]
        result = await tt.api.batch_complete_tasks(pairs)
        sizes = [(c.args[0], len(c.args[1])) for c in tt.v1.complete_tasks.await_args_list]
        assert sizes == [(PROJ, 50), (PROJ, 10), (OTHER_PROJ, 2)]
        assert len(result["completed"]) == 62

    async def test_task_left_out_of_the_reply_raises_and_names_it(self):
        tt = FakeTickTick(_raw("t1"), _raw("t2"))
        tt.v1.complete_tasks.side_effect = AsyncMock(return_value=["t1"])
        with pytest.raises(TickTickAPIError, match="t2"):
            await tt.api.batch_complete_tasks([("t1", PROJ), ("t2", PROJ)])

    async def test_already_completed_is_left_alone(self):
        tt = FakeTickTick(_raw("done", status=2), _raw("open"))
        result = await tt.api.batch_complete_tasks([("done", PROJ), ("open", PROJ)])
        tt.v1.complete_tasks.assert_awaited_once_with(PROJ, ["open"])
        assert result["completed"] == ["open"]
        assert result["already_completed"] == ["done"]

    async def test_missing_task_raises_before_any_write(self):
        tt = FakeTickTick(_raw("t1"))
        with pytest.raises(TickTickNotFoundError):
            await tt.api.batch_complete_tasks([("t1", PROJ), ("gone", PROJ)])
        tt.v1.complete_tasks.assert_not_called()
        tt.v2.batch_tasks.assert_not_called()

    async def test_duplicate_ids_are_completed_once(self):
        tt = FakeTickTick(_raw("t1"))
        await tt.api.batch_complete_tasks([("t1", PROJ), ("t1", PROJ)])
        tt.v1.complete_tasks.assert_awaited_once_with(PROJ, ["t1"])

    async def test_single_complete_task_uses_the_same_path(self):
        tt = FakeTickTick(_raw("r1", rule=DAILY))
        await tt.api.complete_task("r1", PROJ)
        tt.v2.batch_tasks.assert_not_called()
        assert tt.tasks["r1"]["dueDate"] == DAY_TWO

    async def test_works_with_only_v1(self):
        tt = FakeTickTick(_raw("r1", rule=DAILY), v2=False)
        result = await tt.api.batch_complete_tasks([("r1", PROJ)])
        assert result["next_occurrences"][0].id == "r1"


class TestCompleteFallback:
    async def test_v1_login_failure_sends_the_full_task_through_v2(self):
        """A sparse body here would wipe the dates, the original bug."""
        tt = FakeTickTick(_raw("t1", title="Keep me", tags=["x"]))
        tt.v1_login_fails()
        await tt.api.batch_complete_tasks([("t1", PROJ)])
        sent = tt.v2.batch_tasks.call_args.kwargs["update"][0]
        assert sent["status"] == TaskStatus.COMPLETED
        assert sent["completedTime"]
        assert sent["startDate"] == DAY_ONE
        assert sent["dueDate"] == DAY_ONE
        assert sent["title"] == "Keep me"
        assert sent["tags"] == ["x"]
        assert sent["timeZone"] == "America/Los_Angeles"
        assert sent["isAllDay"] is True

    async def test_v1_login_failure_refuses_recurring_tasks(self):
        tt = FakeTickTick(_raw("t1"), _raw("r1", rule=DAILY))
        tt.v1_login_fails()
        with pytest.raises(TickTickAPIUnavailableError, match="r1"):
            await tt.api.batch_complete_tasks([("t1", PROJ), ("r1", PROJ)])
        tt.v2.batch_tasks.assert_not_called()

    async def test_v1_login_failure_midway_skips_what_v1_finished(self):
        """V1 moved r1 on, so V2 must close only the task V1 did not reach."""
        tt = FakeTickTick(_raw("r1", rule=DAILY), _raw("t2", project=OTHER_PROJ))
        tt.v1_login_fails_on_second_call()
        result = await tt.api.batch_complete_tasks([("r1", PROJ), ("t2", OTHER_PROJ)])
        sent = tt.v2.batch_tasks.call_args.kwargs["update"]
        assert [task["id"] for task in sent] == ["t2"]
        assert result["completed"] == ["r1", "t2"]
        assert result["next_occurrences"][0].id == "r1"

    async def test_v1_login_failure_midway_names_what_v1_finished(self):
        """A retry of r1 would complete its next occurrence too, so the error
        must not say that nothing happened to it."""
        tt = FakeTickTick(
            _raw("r1", rule=DAILY),
            _raw("r2", project=OTHER_PROJ, rule=DAILY),
        )
        tt.v1_login_fails_on_second_call()
        with pytest.raises(TickTickAPIUnavailableError) as caught:
            await tt.api.batch_complete_tasks([("r1", PROJ), ("r2", OTHER_PROJ)])
        message = str(caught.value)
        assert "repeating tasks r2:" in message
        assert "do not send them again: r1." in message
        tt.v2.batch_tasks.assert_not_called()

    async def test_v1_login_failure_midway_without_v2_names_what_v1_finished(self):
        tt = FakeTickTick(
            _raw("r1", rule=DAILY),
            _raw("t2", project=OTHER_PROJ),
            v2=False,
        )
        tt.v1_login_fails_on_second_call()
        with pytest.raises(TickTickAPIUnavailableError, match="do not send them again: r1."):
            await tt.api.batch_complete_tasks([("r1", PROJ), ("t2", OTHER_PROJ)])

    async def test_without_v1_a_normal_task_uses_v2(self):
        tt = FakeTickTick(_raw("t1"), v1=False)
        await tt.api.batch_complete_tasks([("t1", PROJ)])
        tt.v2.batch_tasks.assert_awaited_once()

    async def test_without_v1_a_recurring_task_is_refused(self):
        tt = FakeTickTick(_raw("r1", rule=DAILY), v1=False)
        with pytest.raises(TickTickAPIUnavailableError):
            await tt.api.batch_complete_tasks([("r1", PROJ)])
        tt.v2.batch_tasks.assert_not_called()

    async def test_v2_close_keeps_the_kanban_column(self):
        tt = FakeTickTick(_raw("t1", columnId="c1"), v1=False)
        await tt.api.batch_complete_tasks([("t1", PROJ)])
        assert tt.v2.batch_tasks.call_args.kwargs["update"][0]["columnId"] == "c1"


# =============================================================================
# Abandon
# =============================================================================


class TestAbandonNormal:
    async def test_sends_only_the_status_through_v1(self):
        tt = FakeTickTick(_raw("t1"))
        result = await tt.api.batch_abandon_tasks([("t1", PROJ)])
        tt.v1.batch_update_tasks.assert_awaited_once_with(
            [{"id": "t1", "projectId": PROJ, "status": -1}]
        )
        tt.v1.complete_tasks.assert_not_called()
        assert tt.tasks["t1"]["status"] == -1
        assert tt.tasks["t1"]["dueDate"] == DAY_ONE
        assert result == {
            "abandoned": ["t1"],
            "already_abandoned": [],
            "completed_instead": [],
            "next_occurrences": [],
        }

    async def test_updates_go_in_chunks_of_fifty(self):
        tt = FakeTickTick(*[_raw(f"t{i}") for i in range(60)])
        await tt.api.batch_abandon_tasks([(f"t{i}", PROJ) for i in range(60)])
        sizes = [len(c.args[0]) for c in tt.v1.batch_update_tasks.await_args_list]
        assert sizes == [50, 10]

    async def test_already_abandoned_is_left_alone(self):
        tt = FakeTickTick(_raw("t1", status=-1))
        result = await tt.api.batch_abandon_tasks([("t1", PROJ)])
        tt.v1.batch_update_tasks.assert_not_called()
        assert result["abandoned"] == []
        assert result["already_abandoned"] == ["t1"]

    async def test_error_from_ticktick_is_raised(self):
        tt = FakeTickTick(_raw("t1"))
        tt.v1.batch_update_tasks.side_effect = AsyncMock(
            return_value={"id2etag": {}, "id2error": {"t1": "UNKNOWN"}}
        )
        with pytest.raises(TickTickAPIError):
            await tt.api.batch_abandon_tasks([("t1", PROJ)])

    async def test_v1_login_failure_sends_the_full_task_through_v2(self):
        tt = FakeTickTick(_raw("t1", title="Keep me"))
        tt.v1_login_fails()
        await tt.api.batch_abandon_tasks([("t1", PROJ)])
        sent = tt.v2.batch_tasks.call_args.kwargs["update"][0]
        assert sent["status"] == TaskStatus.ABANDONED
        assert sent["completedTime"]
        assert sent["dueDate"] == DAY_ONE
        assert sent["title"] == "Keep me"

    async def test_missing_task_raises_before_any_write(self):
        tt = FakeTickTick(_raw("t1"))
        with pytest.raises(TickTickNotFoundError):
            await tt.api.batch_abandon_tasks([("t1", PROJ), ("gone", PROJ)])
        tt.v1.batch_update_tasks.assert_not_called()


class TestAbandonRecurring:
    async def test_series_stays_and_the_copy_is_abandoned(self):
        tt = FakeTickTick(_raw("r1", rule=DAILY))
        result = await tt.api.batch_abandon_tasks([("r1", PROJ)])

        # Never status -1 on the series itself, that ends it.
        sent = tt.v1.batch_update_tasks.await_args.args[0]
        assert [u["id"] for u in sent] == [tt.copies[0]["id"]]
        assert tt.tasks["r1"]["status"] == 0
        assert tt.tasks["r1"]["repeatFlag"] == DAILY
        assert tt.tasks["r1"]["dueDate"] == DAY_TWO
        assert tt.copies[0]["status"] == -1

        assert result["abandoned"] == ["r1"]
        assert result["completed_instead"] == []
        assert result["next_occurrences"][0].id == "r1"

    async def test_copy_is_matched_on_title_and_dates(self):
        """Other completed tasks in the project must stay completed."""
        same_title_other_day = _raw("c1", title="Task r1", status=2, dueDate=DAY_TWO)
        other_title_same_day = _raw("c2", title="Something else", status=2)
        same_title_other_start = _raw("c3", title="Task r1", status=2, startDate=DAY_TWO)
        tt = FakeTickTick(
            _raw("r1", rule=DAILY),
            same_title_other_day,
            other_title_same_day,
            same_title_other_start,
        )
        await tt.api.batch_abandon_tasks([("r1", PROJ)])
        assert tt.tasks["c1"]["status"] == 2
        assert tt.tasks["c2"]["status"] == 2
        assert tt.tasks["c3"]["status"] == 2
        assert tt.copies[0]["status"] == -1

    async def test_copy_not_found_is_reported_not_hidden(self):
        tt = FakeTickTick(_raw("r1", rule=DAILY))
        tt.hide_copies = True
        result = await tt.api.batch_abandon_tasks([("r1", PROJ)])
        assert result["abandoned"] == []
        assert result["completed_instead"] == ["r1"]
        assert result["next_occurrences"][0].id == "r1"
        assert tt.v1.get_completed_tasks.await_count == api_module._COPY_LOOKUP_ATTEMPTS

    async def test_late_copy_is_found_on_a_later_attempt(self):
        tt = FakeTickTick(_raw("r1", rule=DAILY))
        real_list = tt._list_completed
        calls = []
        async def late(project_ids, start, end):
            calls.append(1)
            return [] if len(calls) == 1 else await real_list(project_ids, start, end)
        tt.v1.get_completed_tasks.side_effect = late
        result = await tt.api.batch_abandon_tasks([("r1", PROJ)])
        assert result["abandoned"] == ["r1"]
        assert len(calls) == 2

    async def test_error_on_the_copy_is_reported_not_hidden(self):
        tt = FakeTickTick(_raw("r1", rule=DAILY))
        async def refuse(updates):
            return {"id2etag": {}, "id2error": {u["id"]: "UNKNOWN" for u in updates}}
        tt.v1.batch_update_tasks.side_effect = refuse
        result = await tt.api.batch_abandon_tasks([("r1", PROJ)])
        assert result["completed_instead"] == ["r1"]

    async def test_without_v1_nothing_is_written(self):
        tt = FakeTickTick(_raw("t1"), _raw("r1", rule=DAILY), v1=False)
        with pytest.raises(TickTickAPIUnavailableError, match="r1"):
            await tt.api.batch_abandon_tasks([("t1", PROJ), ("r1", PROJ)])
        tt.v2.batch_tasks.assert_not_called()

    async def test_v1_login_failure_writes_nothing(self):
        """Recurring tasks go first, so the failure stops the whole call."""
        tt = FakeTickTick(_raw("t1"), _raw("r1", rule=DAILY))
        tt.v1_login_fails()
        with pytest.raises(TickTickAPIUnavailableError, match="r1"):
            await tt.api.batch_abandon_tasks([("t1", PROJ), ("r1", PROJ)])
        tt.v2.batch_tasks.assert_not_called()

    async def test_v1_login_failure_midway_names_what_v1_finished(self):
        tt = FakeTickTick(
            _raw("r1", rule=DAILY),
            _raw("r2", project=OTHER_PROJ, rule=DAILY),
        )
        tt.v1_login_fails_on_second_call()
        with pytest.raises(TickTickAPIUnavailableError) as caught:
            await tt.api.batch_abandon_tasks([("r1", PROJ), ("r2", OTHER_PROJ)])
        message = str(caught.value)
        assert "repeating tasks r2:" in message
        assert "do not send them again: r1." in message

    async def test_mixed_batch(self):
        tt = FakeTickTick(_raw("t1"), _raw("r1", rule=DAILY))
        result = await tt.api.batch_abandon_tasks([("t1", PROJ), ("r1", PROJ)])
        assert sorted(result["abandoned"]) == ["r1", "t1"]
        assert tt.tasks["t1"]["status"] == -1
        assert tt.tasks["r1"]["status"] == 0

    async def test_lookup_window_covers_the_call(self):
        tt = FakeTickTick(_raw("r1", rule=DAILY))
        before = datetime.now(timezone.utc)
        await tt.api.batch_abandon_tasks([("r1", PROJ)])
        _, start, end = tt.v1.get_completed_tasks.await_args.args
        parse = lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.000+0000").replace(tzinfo=timezone.utc)
        assert parse(start) <= before - timedelta(minutes=4)
        assert parse(end) >= before + timedelta(minutes=4)


# =============================================================================
# Tool responses
# =============================================================================


def _task(task_id: str = "1" * 24) -> Task:
    return Task(
        id=task_id,
        project_id=PROJ,
        title="T",
        is_all_day=True,
        time_zone="America/Los_Angeles",
        due_date=datetime(2026, 11, 1, 7, tzinfo=timezone.utc),
        start_date=datetime(2026, 11, 1, 7, tzinfo=timezone.utc),
    )


def _ctx(**methods) -> SimpleNamespace:
    client = SimpleNamespace(**{k: AsyncMock(return_value=v) for k, v in methods.items()})
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"client": client}))


def _identifier(task_id: str = "1" * 24) -> dict:
    return {"task_id": task_id, "project_id": PROJ}


async def test_complete_tool_names_the_next_date():
    ctx = _ctx(complete_tasks={
        "completed": ["1" * 24],
        "already_completed": [],
        "next_occurrences": [_task()],
    })
    out = await server.ticktick_complete_tasks(CompleteTasksInput(tasks=[_identifier()]), ctx)
    assert f"Task `{'1' * 24}` marked as complete." in out
    assert f"- `{'1' * 24}`: 2026-11-01" in out


async def test_complete_tool_counts_a_batch_and_lists_skipped_tasks():
    ctx = _ctx(complete_tasks={
        "completed": ["1" * 24, "2" * 24],
        "already_completed": ["3" * 24],
        "next_occurrences": [],
    })
    out = await server.ticktick_complete_tasks(
        CompleteTasksInput(tasks=[_identifier("1" * 24), _identifier("2" * 24), _identifier("3" * 24)]),
        ctx,
    )
    assert "2 tasks marked as complete." in out
    assert f"Already complete, left as they were: `{'3' * 24}`." in out
    assert "Repeating" not in out


async def test_abandon_tool_says_when_an_occurrence_stayed_completed():
    ctx = _ctx(abandon_tasks={
        "abandoned": [],
        "already_abandoned": [],
        "completed_instead": ["1" * 24],
        "next_occurrences": [_task()],
    })
    out = await server.ticktick_abandon_tasks(AbandonTasksInput(tasks=[_identifier()]), ctx)
    assert out.startswith("**Partly done**: ")
    assert "marked as abandoned" not in out.split("Could not")[0]
    assert "stays completed" in out
    assert "2026-11-01" in out


async def test_abandon_tool_success():
    ctx = _ctx(abandon_tasks={
        "abandoned": ["1" * 24],
        "already_abandoned": [],
        "completed_instead": [],
        "next_occurrences": [],
    })
    out = await server.ticktick_abandon_tasks(AbandonTasksInput(tasks=[_identifier()]), ctx)
    assert f"Task `{'1' * 24}` marked as abandoned." in out


async def test_complete_tool_json():
    ctx = _ctx(complete_tasks={
        "completed": ["1" * 24],
        "already_completed": [],
        "next_occurrences": [_task()],
    })
    out = json.loads(await server.ticktick_complete_tasks(
        CompleteTasksInput(tasks=[_identifier()], response_format=ResponseFormat.JSON), ctx
    ))
    assert out == {
        "success": True,
        "completed": ["1" * 24],
        "already_completed": [],
        "next_occurrences": [
            {"id": "1" * 24, "start_date": "2026-11-01", "due_date": "2026-11-01"}
        ],
    }


async def test_abandon_tool_json_is_not_a_success_when_an_occurrence_stayed_completed():
    ctx = _ctx(abandon_tasks={
        "abandoned": [],
        "already_abandoned": [],
        "completed_instead": ["1" * 24],
        "next_occurrences": [],
    })
    out = json.loads(await server.ticktick_abandon_tasks(
        AbandonTasksInput(tasks=[_identifier()], response_format=ResponseFormat.JSON), ctx
    ))
    assert out["success"] is False
    assert out["completed_instead"] == ["1" * 24]


async def test_delete_tool_json():
    ctx = _ctx(delete_tasks={})
    out = json.loads(await server.ticktick_delete_tasks(
        DeleteTasksInput(tasks=[_identifier()], response_format=ResponseFormat.JSON), ctx
    ))
    assert out == {"success": True, "count": 1, "deleted": ["1" * 24]}


async def test_move_tool_json():
    ctx = _ctx(move_tasks={})
    move = {"task_id": "1" * 24, "from_project_id": PROJ, "to_project_id": OTHER_PROJ}
    out = json.loads(await server.ticktick_move_tasks(
        MoveTasksInput(moves=[move], response_format=ResponseFormat.JSON), ctx
    ))
    assert out == {
        "success": True,
        "count": 1,
        "moved": [{"task_id": "1" * 24, "to_project_id": OTHER_PROJ}],
    }


async def test_set_task_parents_tool_json():
    ctx = _ctx(set_task_parents={})
    item = {"task_id": "1" * 24, "project_id": PROJ, "parent_id": "2" * 24}
    out = json.loads(await server.ticktick_set_task_parents(
        SetTaskParentsInput(tasks=[item], response_format=ResponseFormat.JSON), ctx
    ))
    assert out == {
        "success": True,
        "count": 1,
        "assigned": [{"task_id": "1" * 24, "parent_id": "2" * 24}],
    }


async def test_unparent_tool_json():
    ctx = _ctx(unparent_tasks={})
    out = json.loads(await server.ticktick_unparent_tasks(
        UnparentTasksInput(tasks=[_identifier()], response_format=ResponseFormat.JSON), ctx
    ))
    assert out == {"success": True, "count": 1, "unparented": ["1" * 24]}


def test_abandon_tool_is_listed_in_the_tasks_module():
    from ticktick_sdk.cli import TOOL_MODULES

    assert "ticktick_abandon_tasks" in TOOL_MODULES["tasks"]


async def test_abandon_tool_is_registered_and_tagged_v1_capable():
    names = {t.name: t for t in await server.mcp.list_tools()}
    assert "ticktick_abandon_tasks" in names
    assert "ticktick_abandon_tasks" in server._V1_CAPABLE_TOOLS
    assert "ticktick_complete_tasks" in server._V1_CAPABLE_TOOLS
