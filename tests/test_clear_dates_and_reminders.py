"""
Tests for removing a task's dates and for task reminders.

``clear_dates`` on ``update_tasks`` removes both dates, like Clear in the
TickTick app's date picker. A repeat rule needs a start date, so a repeating
task also loses its rule, and the tool reply says so. Reminders are shown by
the read tools and can be replaced or removed through ``update_tasks``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from ticktick_sdk import server
from ticktick_sdk.exceptions import TickTickAPIError
from ticktick_sdk.models import Task, TaskReminder
from ticktick_sdk.tools.formatting import format_task_json, format_task_markdown
from ticktick_sdk.tools.inputs import ResponseFormat, TaskUpdateItem, UpdateTasksInput
from ticktick_sdk.unified.api import UnifiedTickTickAPI


pytestmark = [pytest.mark.tasks, pytest.mark.unit]

TASK_ID = "a" * 24
PROJ = "b" * 24


def _task(**fields) -> Task:
    base = {
        "id": TASK_ID,
        "project_id": PROJ,
        "title": "Apply to the accelerator",
        "is_all_day": True,
        "time_zone": "America/Los_Angeles",
        "start_date": datetime(2026, 11, 5, 8, tzinfo=timezone.utc),
        "due_date": datetime(2026, 11, 5, 8, tzinfo=timezone.utc),
    }
    base.update(fields)
    return Task(**base)


def _make_api(*tasks: Task) -> tuple[UnifiedTickTickAPI, AsyncMock]:
    """A UnifiedTickTickAPI with a mocked V2 client and get_task."""
    by_id = {t.id: t for t in tasks}
    api = UnifiedTickTickAPI.__new__(UnifiedTickTickAPI)
    api._initialized = True
    api._router = MagicMock()
    api._router.has_v2 = True
    api._v2_client = MagicMock()
    api._v2_client.batch_tasks = AsyncMock(
        return_value={"id2etag": {t.id: "etag" for t in tasks}, "id2error": {}}
    )
    api.get_task = AsyncMock(side_effect=lambda task_id, project_id=None: by_id[task_id])  # type: ignore[assignment]
    return api, api._v2_client.batch_tasks


def _sent(batch_mock: AsyncMock) -> list[dict]:
    return batch_mock.call_args.kwargs["update"]


class TestClearDates:
    async def test_both_dates_go_out_empty(self):
        api, batch = _make_api(_task())
        await api.batch_update_tasks([
            {"task_id": TASK_ID, "project_id": PROJ, "clear_dates": True}
        ])
        payload = _sent(batch)[0]
        assert payload["startDate"] == ""
        assert payload["dueDate"] == ""

    async def test_all_day_flag_zone_and_title_stay(self):
        """The app keeps both when it clears a date (seen live 2026-11-05)."""
        api, batch = _make_api(_task(tags=["work"]))
        await api.batch_update_tasks([
            {"task_id": TASK_ID, "project_id": PROJ, "clear_dates": True}
        ])
        payload = _sent(batch)[0]
        assert payload["isAllDay"] is True
        assert payload["timeZone"] == "America/Los_Angeles"
        assert payload["title"] == "Apply to the accelerator"
        assert payload["tags"] == ["work"]

    async def test_repeating_task_loses_its_rule_and_is_reported(self):
        api, batch = _make_api(_task(repeat_flag="RRULE:FREQ=DAILY;INTERVAL=1"))
        response = await api.batch_update_tasks([
            {"task_id": TASK_ID, "project_id": PROJ, "clear_dates": True}
        ])
        assert _sent(batch)[0]["repeatFlag"] == ""
        assert response["_removed_repeat"] == {TASK_ID: True}

    async def test_task_without_a_rule_is_not_reported(self):
        api, batch = _make_api(_task())
        response = await api.batch_update_tasks([
            {"task_id": TASK_ID, "project_id": PROJ, "clear_dates": True}
        ])
        assert "repeatFlag" not in _sent(batch)[0]
        assert response["_removed_repeat"] == {}

    async def test_reminders_are_sent_unchanged(self):
        api, batch = _make_api(_task(reminders=[TaskReminder(id="r1", trigger="TRIGGER:P0DT9H0M0S")]))
        await api.batch_update_tasks([
            {"task_id": TASK_ID, "project_id": PROJ, "clear_dates": True}
        ])
        assert _sent(batch)[0]["reminders"] == [{"id": "r1", "trigger": "TRIGGER:P0DT9H0M0S"}]

    async def test_other_fields_in_the_same_update_still_apply(self):
        api, batch = _make_api(_task())
        await api.batch_update_tasks([
            {"task_id": TASK_ID, "project_id": PROJ, "clear_dates": True, "priority": "high"}
        ])
        payload = _sent(batch)[0]
        assert payload["priority"] == 5
        assert payload["dueDate"] == ""

    @pytest.mark.parametrize("field,value", [
        ("start_date", "2026-11-06"),
        ("due_date", "2026-11-06"),
        ("recurrence", "RRULE:FREQ=WEEKLY"),
    ])
    async def test_conflicting_field_is_refused_before_any_fetch(self, field, value):
        api, batch = _make_api(_task())
        with pytest.raises(TickTickAPIError, match=field):
            await api.batch_update_tasks([
                {"task_id": TASK_ID, "project_id": PROJ, "clear_dates": True, field: value}
            ])
        api.get_task.assert_not_called()
        batch.assert_not_called()

    async def test_a_bad_item_later_in_the_batch_sends_nothing(self):
        other = _task(id="c" * 24)
        api, batch = _make_api(_task(), other)
        with pytest.raises(TickTickAPIError):
            await api.batch_update_tasks([
                {"task_id": TASK_ID, "project_id": PROJ, "clear_dates": True},
                {"task_id": "c" * 24, "project_id": PROJ, "clear_dates": True, "due_date": "2026-11-06"},
            ])
        batch.assert_not_called()

    async def test_empty_date_string_points_to_clear_dates(self):
        api, batch = _make_api(_task())
        with pytest.raises(TickTickAPIError, match="clear_dates"):
            await api.batch_update_tasks([
                {"task_id": TASK_ID, "project_id": PROJ, "start_date": ""}
            ])
        batch.assert_not_called()


class TestReminderUpdates:
    async def test_new_reminders_replace_the_old_ones(self):
        api, batch = _make_api(_task(reminders=[TaskReminder(id="r1", trigger="TRIGGER:-PT30M")]))
        await api.batch_update_tasks([
            {"task_id": TASK_ID, "project_id": PROJ, "reminders": ["TRIGGER:PT0S", "TRIGGER:-PT1H"]}
        ])
        assert _sent(batch)[0]["reminders"] == [
            {"trigger": "TRIGGER:PT0S"},
            {"trigger": "TRIGGER:-PT1H"},
        ]

    async def test_empty_list_removes_all_reminders(self):
        api, batch = _make_api(_task(reminders=[TaskReminder(id="r1", trigger="TRIGGER:-PT30M")]))
        await api.batch_update_tasks([
            {"task_id": TASK_ID, "project_id": PROJ, "reminders": []}
        ])
        assert _sent(batch)[0]["reminders"] == []

    async def test_reminders_survive_an_unrelated_update(self):
        api, batch = _make_api(_task(reminders=[TaskReminder(id="r1", trigger="TRIGGER:-PT30M")]))
        await api.batch_update_tasks([
            {"task_id": TASK_ID, "project_id": PROJ, "title": "New title"}
        ])
        assert _sent(batch)[0]["reminders"] == [{"id": "r1", "trigger": "TRIGGER:-PT30M"}]

    def test_create_payload_leaves_out_empty_reminders(self):
        assert "reminders" not in _task().to_v2_dict(for_update=False)


class TestUpdateInput:
    def test_clear_dates_is_accepted(self):
        item = TaskUpdateItem(task_id=TASK_ID, project_id=PROJ, clear_dates=True)
        assert item.clear_dates is True

    def test_more_than_ten_reminders_is_refused(self):
        with pytest.raises(ValidationError):
            TaskUpdateItem(task_id=TASK_ID, project_id=PROJ, reminders=["TRIGGER:PT0S"] * 11)


def _ctx(update_response: dict) -> tuple[SimpleNamespace, AsyncMock]:
    update = AsyncMock(return_value=update_response)
    client = SimpleNamespace(update_tasks=update)
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"client": client}))
    return ctx, update


class TestUpdateTool:
    async def test_clear_dates_reaches_the_client(self):
        ctx, update = _ctx({"id2etag": {}, "id2error": {}, "_in_trash": {}, "_removed_repeat": {}})
        await server.ticktick_update_tasks(
            UpdateTasksInput(tasks=[{"task_id": TASK_ID, "project_id": PROJ, "clear_dates": True}]),
            ctx,
        )
        assert update.call_args.args[0] == [
            {"task_id": TASK_ID, "project_id": PROJ, "clear_dates": True}
        ]

    async def test_empty_reminder_list_reaches_the_client(self):
        ctx, update = _ctx({"id2etag": {}, "id2error": {}, "_in_trash": {}, "_removed_repeat": {}})
        await server.ticktick_update_tasks(
            UpdateTasksInput(tasks=[{"task_id": TASK_ID, "project_id": PROJ, "reminders": []}]),
            ctx,
        )
        assert update.call_args.args[0][0]["reminders"] == []

    async def test_markdown_names_the_tasks_that_stopped_repeating(self):
        ctx, _ = _ctx({"id2etag": {}, "id2error": {}, "_in_trash": {}, "_removed_repeat": {TASK_ID: True}})
        out = await server.ticktick_update_tasks(
            UpdateTasksInput(tasks=[{"task_id": TASK_ID, "project_id": PROJ, "clear_dates": True}]),
            ctx,
        )
        assert f"also removed the repeat rule of: `{TASK_ID}`" in out

    async def test_markdown_is_quiet_when_no_rule_was_removed(self):
        ctx, _ = _ctx({"id2etag": {}, "id2error": {}, "_in_trash": {}, "_removed_repeat": {}})
        out = await server.ticktick_update_tasks(
            UpdateTasksInput(tasks=[{"task_id": TASK_ID, "project_id": PROJ, "clear_dates": True}]),
            ctx,
        )
        assert "repeat" not in out

    async def test_json_flags_removed_repeat_per_task(self):
        ctx, _ = _ctx({"id2etag": {}, "id2error": {}, "_in_trash": {}, "_removed_repeat": {TASK_ID: True}})
        out = await server.ticktick_update_tasks(
            UpdateTasksInput(
                tasks=[
                    {"task_id": TASK_ID, "project_id": PROJ, "clear_dates": True},
                    {"task_id": "c" * 24, "project_id": PROJ, "clear_dates": True},
                ],
                response_format=ResponseFormat.JSON,
            ),
            ctx,
        )
        data = json.loads(out)
        assert data["tasks"] == [
            {"task_id": TASK_ID, "removed_repeat": True},
            {"task_id": "c" * 24},
        ]
        assert "_removed_repeat" not in data["response"]


class TestReminderDisplay:
    def test_json_lists_the_triggers(self):
        task = _task(reminders=[TaskReminder(id="r1", trigger="TRIGGER:-PT30M")])
        assert format_task_json(task)["reminders"] == ["TRIGGER:-PT30M"]

    def test_detail_json_shows_an_empty_list(self):
        assert format_task_json(_task())["reminders"] == []

    def test_list_json_drops_empty_reminders(self):
        assert "reminders" not in format_task_json(_task(), omit_defaults=True)

    def test_list_json_keeps_reminders_when_set(self):
        task = _task(reminders=[TaskReminder(trigger="TRIGGER:PT0S")])
        assert format_task_json(task, omit_defaults=True)["reminders"] == ["TRIGGER:PT0S"]

    def test_markdown_detail_shows_the_triggers(self):
        task = _task(reminders=[
            TaskReminder(trigger="TRIGGER:-PT30M"),
            TaskReminder(trigger="TRIGGER:PT0S"),
        ])
        assert "- **Reminders**: `TRIGGER:-PT30M`, `TRIGGER:PT0S`" in format_task_markdown(task)

    def test_markdown_detail_has_no_line_without_reminders(self):
        assert "Reminders" not in format_task_markdown(_task())
