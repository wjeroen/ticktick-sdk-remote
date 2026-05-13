"""
Tests for UnifiedTickTickAPI.batch_update_tasks.

These tests cover the pre-fetch + merge behavior that prevents the V2
/batch/task endpoint from wiping unspecified fields (repeat_flag, is_all_day,
time_zone, etc).

They also cover timezone handling in Task.format_datetime, which normalizes
input datetimes to UTC before serializing — needed because the V2 wire format
hardcodes "+0000" as the offset suffix.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from ticktick_sdk.models import Task
from ticktick_sdk.unified.api import UnifiedTickTickAPI


pytestmark = [pytest.mark.tasks, pytest.mark.unit]


def _make_api(existing_task: Task) -> tuple[UnifiedTickTickAPI, AsyncMock]:
    """Build a UnifiedTickTickAPI with mocked V2 client and get_task."""
    api = UnifiedTickTickAPI.__new__(UnifiedTickTickAPI)
    api._initialized = True
    api._router = MagicMock()
    api._router.has_v2 = True
    api._v2_client = MagicMock()
    api._v2_client.batch_tasks = AsyncMock(
        return_value={"id2etag": {existing_task.id: "etag1"}, "id2error": {}}
    )
    # Patch the unified get_task so the merge step has something to read.
    api.get_task = AsyncMock(return_value=existing_task)  # type: ignore[assignment]
    return api, api._v2_client.batch_tasks


def _sent_payload(batch_mock: AsyncMock) -> dict:
    """Extract the single update payload sent to v2_client.batch_tasks."""
    sent = batch_mock.call_args.kwargs["update"]
    assert len(sent) == 1
    return sent[0]


class TestBatchUpdatePreservesUnspecifiedFields:
    """Updating one field must not wipe other fields on the task."""

    async def test_recurrence_preserved_when_only_dates_change(self):
        """The bug Claude hit: updating start/due wiped repeat_flag."""
        existing = Task(
            id="aaaaaaaaaaaaaaaaaaaaaaaa",
            project_id="bbbbbbbbbbbbbbbbbbbbbbbb",
            title="Take out trash",
            repeat_flag="RRULE:FREQ=WEEKLY;WKST=MO;INTERVAL=1;BYDAY=WE,SU",
            is_all_day=True,
            time_zone="Europe/Brussels",
            start_date=datetime(2026, 5, 13, 0, 0, tzinfo=timezone.utc),
            due_date=datetime(2026, 5, 13, 0, 0, tzinfo=timezone.utc),
        )
        api, batch_mock = _make_api(existing)

        await api.batch_update_tasks([{
            "task_id": existing.id,
            "project_id": existing.project_id,
            "start_date": "2026-05-14T00:00:00+02:00",
            "due_date": "2026-05-14T00:00:00+02:00",
        }])

        payload = _sent_payload(batch_mock)
        assert payload["repeatFlag"] == "RRULE:FREQ=WEEKLY;WKST=MO;INTERVAL=1;BYDAY=WE,SU"
        assert payload["isAllDay"] is True
        assert payload["timeZone"] == "Europe/Brussels"
        assert payload["title"] == "Take out trash"

    async def test_is_all_day_preserved_when_only_title_changes(self):
        existing = Task(
            id="aaaaaaaaaaaaaaaaaaaaaaaa",
            project_id="bbbbbbbbbbbbbbbbbbbbbbbb",
            title="Old title",
            is_all_day=False,
            time_zone="America/New_York",
        )
        api, batch_mock = _make_api(existing)

        await api.batch_update_tasks([{
            "task_id": existing.id,
            "project_id": existing.project_id,
            "title": "New title",
        }])

        payload = _sent_payload(batch_mock)
        assert payload["title"] == "New title"
        assert payload["isAllDay"] is False
        assert payload["timeZone"] == "America/New_York"

    async def test_tags_preserved_when_only_priority_changes(self):
        existing = Task(
            id="aaaaaaaaaaaaaaaaaaaaaaaa",
            project_id="bbbbbbbbbbbbbbbbbbbbbbbb",
            title="Tagged task",
            priority=0,
            tags=["work", "urgent"],
        )
        api, batch_mock = _make_api(existing)

        await api.batch_update_tasks([{
            "task_id": existing.id,
            "project_id": existing.project_id,
            "priority": "high",
        }])

        payload = _sent_payload(batch_mock)
        assert payload["priority"] == 5
        assert set(payload["tags"]) == {"work", "urgent"}


class TestBatchUpdateAppliesDelta:
    """Fields in the delta should overwrite the existing values."""

    async def test_recurrence_can_be_replaced(self):
        existing = Task(
            id="aaaaaaaaaaaaaaaaaaaaaaaa",
            project_id="bbbbbbbbbbbbbbbbbbbbbbbb",
            title="Recurring",
            repeat_flag="RRULE:FREQ=DAILY",
        )
        api, batch_mock = _make_api(existing)

        await api.batch_update_tasks([{
            "task_id": existing.id,
            "project_id": existing.project_id,
            "recurrence": "RRULE:FREQ=WEEKLY;BYDAY=MO",
        }])

        payload = _sent_payload(batch_mock)
        assert payload["repeatFlag"] == "RRULE:FREQ=WEEKLY;BYDAY=MO"

    async def test_all_day_can_be_flipped(self):
        existing = Task(
            id="aaaaaaaaaaaaaaaaaaaaaaaa",
            project_id="bbbbbbbbbbbbbbbbbbbbbbbb",
            title="Was all-day",
            is_all_day=True,
        )
        api, batch_mock = _make_api(existing)

        await api.batch_update_tasks([{
            "task_id": existing.id,
            "project_id": existing.project_id,
            "all_day": False,
        }])

        payload = _sent_payload(batch_mock)
        assert payload["isAllDay"] is False


class TestBatchUpdateDateOffsets:
    """Datetime strings with various UTC offsets should roundtrip correctly."""

    @pytest.mark.parametrize(
        "input_iso,expected_utc_iso",
        [
            # Brussels CEST → 16:00 UTC
            ("2026-05-13T18:00:00+02:00", "2026-05-13T16:00:00.000+0000"),
            # UTC → unchanged
            ("2026-05-13T18:00:00+00:00", "2026-05-13T18:00:00.000+0000"),
            # New York EDT → 22:00 UTC
            ("2026-05-13T18:00:00-04:00", "2026-05-13T22:00:00.000+0000"),
        ],
    )
    async def test_offset_normalized_to_utc(self, input_iso: str, expected_utc_iso: str):
        existing = Task(
            id="aaaaaaaaaaaaaaaaaaaaaaaa",
            project_id="bbbbbbbbbbbbbbbbbbbbbbbb",
            title="Task",
            is_all_day=False,
            time_zone="Europe/Brussels",
        )
        api, batch_mock = _make_api(existing)

        await api.batch_update_tasks([{
            "task_id": existing.id,
            "project_id": existing.project_id,
            "due_date": input_iso,
        }])

        payload = _sent_payload(batch_mock)
        assert payload["dueDate"] == expected_utc_iso
        # is_all_day must survive the date update
        assert payload["isAllDay"] is False

    async def test_all_day_task_keeps_flag_when_date_updated(self):
        existing = Task(
            id="aaaaaaaaaaaaaaaaaaaaaaaa",
            project_id="bbbbbbbbbbbbbbbbbbbbbbbb",
            title="All-day chore",
            is_all_day=True,
            time_zone="Europe/Brussels",
            repeat_flag="RRULE:FREQ=WEEKLY;BYDAY=WE,SU",
            start_date=datetime(2026, 5, 13, 0, 0, tzinfo=timezone.utc),
            due_date=datetime(2026, 5, 13, 0, 0, tzinfo=timezone.utc),
        )
        api, batch_mock = _make_api(existing)

        # User shifts to tomorrow as a pure date — emulates the safer
        # all-day-style update.
        await api.batch_update_tasks([{
            "task_id": existing.id,
            "project_id": existing.project_id,
            "start_date": "2026-05-14T00:00:00+00:00",
            "due_date": "2026-05-14T00:00:00+00:00",
        }])

        payload = _sent_payload(batch_mock)
        assert payload["isAllDay"] is True
        assert payload["repeatFlag"] == "RRULE:FREQ=WEEKLY;BYDAY=WE,SU"
        assert payload["startDate"] == "2026-05-14T00:00:00.000+0000"
        assert payload["dueDate"] == "2026-05-14T00:00:00.000+0000"


class TestFormatDatetimeTimezoneConversion:
    """Task.format_datetime must convert to UTC before applying +0000 suffix."""

    def test_brussels_offset_converts_to_utc(self):
        dt = datetime(2026, 5, 13, 18, 0, 0, tzinfo=timezone(timedelta(hours=2)))
        assert Task.format_datetime(dt, "v2") == "2026-05-13T16:00:00.000+0000"

    def test_naive_datetime_treated_as_utc(self):
        dt = datetime(2026, 5, 13, 18, 0, 0)  # no tzinfo
        assert Task.format_datetime(dt, "v2") == "2026-05-13T18:00:00.000+0000"

    def test_utc_datetime_unchanged(self):
        dt = datetime(2026, 5, 13, 18, 0, 0, tzinfo=timezone.utc)
        assert Task.format_datetime(dt, "v2") == "2026-05-13T18:00:00.000+0000"

    def test_negative_offset_converts_forward(self):
        dt = datetime(2026, 5, 13, 18, 0, 0, tzinfo=timezone(timedelta(hours=-4)))
        assert Task.format_datetime(dt, "v2") == "2026-05-13T22:00:00.000+0000"
