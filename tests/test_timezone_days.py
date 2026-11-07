"""
Regression tests for which calendar day a task is on, and how dates are written.

TickTick stores every date as an exact moment plus the task's own time zone.
The TickTick app shows an all-day task on the date that moment has in the
task's OWN zone, not in the zone of the phone. Verified against the app on
2026-09-10 with the phone in San Francisco:

  task (zone)                      stored (UTC)        app shows
  Frame Fellowship (LA)            2026-09-10 07:00    Sept 10
  Join Frame retreat (Brussels)    2026-09-11 00:00    Sept 11
  Get tickets for Odyssey (Bxl)    2026-09-11 22:00    Sept 12

Before the fix the server read every task in TICKTICK_TIMEZONE, so while the
user was in San Francisco every Brussels all-day task showed one day early,
and due_today listed tomorrow's Brussels tasks as today. The report that
triggered this (2026-09-10) is the source of the timestamps below.

Writes had the mirror problem: a plain date such as "2026-09-10" was saved as
midnight UTC, which is the previous evening for anyone west of UTC, and a
time without an offset such as "17:00" was saved as 17:00 UTC.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from ticktick_sdk import server
from ticktick_sdk.client.client import TickTickClient
from ticktick_sdk.exceptions import TickTickAPIError
from ticktick_sdk.models import Task
from ticktick_sdk.tools.formatting import (
    format_task_json,
    format_task_markdown,
    format_task_row_markdown,
    task_sort_key,
)
from ticktick_sdk.tools.inputs import TaskListInput
from ticktick_sdk.unified.api import UnifiedTickTickAPI

pytestmark = pytest.mark.unit

LA = "America/Los_Angeles"
BXL = "Europe/Brussels"
PROJ = "p" * 24


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def _task(tid: str, title: str, due: datetime | None, zone: str | None, *,
          all_day: bool | None = True, floating: bool = False, kind: str = "TEXT",
          repeat: str | None = None) -> Task:
    return Task(
        id=tid * 24 if len(tid) == 1 else tid,
        project_id=PROJ,
        title=title,
        kind=kind,
        start_date=due,
        due_date=due,
        time_zone=zone,
        is_all_day=all_day,
        is_floating=floating,
        repeat_flag=repeat,
    )


# Exact stored values from the 2026-09-10 report and the app check.
ADD_MUSIC = _task("a", "9. Add music", _utc(2026, 9, 11, 0), BXL)
DGPH = _task("b", "Appeal handicap decision DGPH", _utc(2026, 9, 10, 0), BXL)
DRAMA = _task("c", "09/10 inschrijven toneellessen?", _utc(2026, 9, 10, 0), BXL)
TRAZZI = _task("d", "Send Michael Trazzi your best...", _utc(2026, 9, 10, 22), BXL)
FULL_CAL = _task("e", "Full Calendar", _utc(2026, 9, 10, 7), LA, kind="NOTE")
LEFTOVERS = _task("f", "Grab leftover food", _utc(2026, 9, 10, 9), LA)
ODYSSEY = _task("g", "Get tickets for Odyssey", _utc(2026, 9, 11, 22), BXL)
WHATSAPP = _task("h", "Join Frame retreat WhatsApp group", _utc(2026, 9, 11, 0), BXL)
FRAME = _task("i", "Frame Fellowship (FF)", _utc(2026, 9, 10, 7), LA)
SLEEP_NEXT = _task("j", "Sleep / Bearable / GW", _utc(2026, 9, 11, 7), LA,
                   repeat="RRULE:FREQ=DAILY;INTERVAL=1")
# App-written Brussels midnight, seen live on 2026-08-28 (for Aug 28).
MELATONIN = _task("k", "Take melatonin", _utc(2026, 8, 27, 22), BXL,
                  repeat="RRULE:FREQ=DAILY")
# An overdue Brussels task: midnight UTC on Sept 9 is Sept 9 in Brussels.
OLD_BXL = _task("l", "Overdue Brussels task", _utc(2026, 9, 9, 0), BXL)


# =============================================================================
# The day rule
# =============================================================================


class TestDueDayMatchesTheApp:
    @pytest.mark.parametrize(
        ("task", "expected"),
        [
            (FRAME, date(2026, 9, 10)),      # app check
            (WHATSAPP, date(2026, 9, 11)),   # app check
            (ODYSSEY, date(2026, 9, 12)),    # app check
            (ADD_MUSIC, date(2026, 9, 11)),
            (DGPH, date(2026, 9, 10)),
            (DRAMA, date(2026, 9, 10)),
            (TRAZZI, date(2026, 9, 11)),
            (FULL_CAL, date(2026, 9, 10)),
            (LEFTOVERS, date(2026, 9, 10)),
            (MELATONIN, date(2026, 8, 28)),
        ],
    )
    def test_all_day_tasks_use_their_own_zone(self, task, expected):
        # The reader's zone must not matter for all-day tasks.
        for reader in (LA, BXL, "Asia/Tokyo", "UTC"):
            assert task.due_day(reader) == expected

    def test_timed_task_follows_the_readers_zone(self):
        timed = _task("m", "Call", _utc(2026, 9, 11, 0), BXL, all_day=False)
        assert timed.due_day(LA) == date(2026, 9, 10)   # 17:00 in San Francisco
        assert timed.due_day(BXL) == date(2026, 9, 11)  # 02:00 in Brussels

    def test_floating_task_keeps_its_own_clock(self):
        # 05:00 UTC is 07:00 in Brussels. Floating time stays 07:00 anywhere.
        run = _task("n", "Morning run", _utc(2026, 9, 11, 5), BXL,
                    all_day=False, floating=True)
        assert run.local(run.due_date, LA).strftime("%Y-%m-%d %H:%M") == "2026-09-11 07:00"
        assert run.due_day(LA) == date(2026, 9, 11)

    @pytest.mark.parametrize("zone", [None, "", "Not/AZone"])
    def test_all_day_without_a_usable_zone_falls_back_to_the_reader(self, zone):
        task = _task("o", "No zone", _utc(2026, 9, 11, 0), zone)
        assert task.home_zone(LA) == LA
        assert task.due_day(LA) == date(2026, 9, 10)


# =============================================================================
# list_tasks filters: one shared day definition
# =============================================================================


class _FakeClient:
    def __init__(self, tasks):
        self._tasks = tasks

    async def get_all_tasks(self):
        return list(self._tasks)

    async def get_all_projects(self):
        return []


def _ctx(tasks):
    return SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context={"client": _FakeClient(tasks)})
    )


ALL = [ADD_MUSIC, DGPH, DRAMA, FULL_CAL, LEFTOVERS, ODYSSEY, WHATSAPP, FRAME,
       SLEEP_NEXT, OLD_BXL]


@pytest.fixture
def in_san_francisco(monkeypatch):
    """TICKTICK_TIMEZONE is Los Angeles and it is 2026-09-10 19:49 there."""
    monkeypatch.setattr(server, "USER_TIMEZONE", LA)
    monkeypatch.setattr(server, "_today", lambda: date(2026, 9, 10))


async def _titles(**filters) -> list[str]:
    out = await server.ticktick_list_tasks(
        TaskListInput(response_format="json", limit=100, **filters), _ctx(ALL)
    )
    return [t["title"] for t in json.loads(out)["tasks"]]


@pytest.mark.usefixtures("in_san_francisco")
class TestListFilters:
    async def test_due_today_is_what_the_app_shows_today(self):
        assert set(await _titles(due_today=True)) == {
            "Appeal handicap decision DGPH",
            "09/10 inschrijven toneellessen?",
            "Full Calendar",
            "Grab leftover food",
            "Frame Fellowship (FF)",
        }

    async def test_tomorrows_brussels_tasks_are_not_today(self):
        today = await _titles(due_today=True)
        for title in ("9. Add music", "Join Frame retreat WhatsApp group",
                      "Get tickets for Odyssey"):
            assert title not in today

    async def test_a_one_day_window_equals_due_today(self):
        window = await _titles(due_after="2026-09-10", due_before="2026-09-10")
        assert set(window) == set(await _titles(due_today=True))

    async def test_overdue_uses_the_same_day(self):
        assert await _titles(overdue=True) == ["Overdue Brussels task"]

    async def test_due_after_tomorrow(self):
        assert set(await _titles(due_after="2026-09-12")) == {"Get tickets for Odyssey"}

    async def test_next_recurring_instance_is_not_today(self):
        # The completed Sept 10 instance is gone. The active one is Sept 11.
        assert "Sleep / Bearable / GW" not in await _titles(due_today=True)
        # due_before is "on or before", so Sept 11 includes it, as designed.
        assert "Sleep / Bearable / GW" in await _titles(due_before="2026-09-11")

    async def test_json_shows_all_day_dates_as_plain_dates(self):
        out = await server.ticktick_list_tasks(
            TaskListInput(response_format="json", due_after="2026-09-12"), _ctx(ALL)
        )
        task = json.loads(out)["tasks"][0]
        assert task["title"] == "Get tickets for Odyssey"
        assert task["due_date"] == "2026-09-12"
        assert task["start_date"] == "2026-09-12"

    async def test_markdown_shows_the_apps_day(self):
        out = await server.ticktick_list_tasks(TaskListInput(due_today=True), _ctx(ALL))
        assert "Due: 2026-09-10" in out
        assert "2026-09-09" not in out
        assert "9. Add music" not in out


# =============================================================================
# Display
# =============================================================================


class TestDisplay:
    def test_row_all_day_is_a_plain_date_in_the_tasks_zone(self):
        row = format_task_row_markdown(ODYSSEY, LA)
        assert "| Due: 2026-09-12" in row
        assert "2026-09-12 " not in row  # no clock time on all-day rows

    def test_row_timed_task_shows_the_time_where_the_user_is(self):
        timed = _task("m", "Call", _utc(2026, 9, 11, 0), BXL, all_day=False)
        assert "| Due: 2026-09-10 17:00 PDT" in format_task_row_markdown(timed, LA)

    def test_row_marks_notes(self):
        assert "[NOTE] **Full Calendar**" in format_task_row_markdown(FULL_CAL, LA)
        assert "[NOTE]" not in format_task_row_markdown(FRAME, LA)

    def test_detail_view_all_day_date(self):
        md = format_task_markdown(ODYSSEY, LA)
        assert "- **Due**: 2026-09-12\n" in md
        assert "- **All-day**: Yes" in md

    def test_detail_view_timed_task_keeps_the_zone_label(self):
        timed = _task("m", "Call", _utc(2026, 9, 11, 0), BXL, all_day=False)
        assert "- **Due**: 2026-09-10 17:00 PDT" in format_task_markdown(timed, LA)

    def test_floating_task_display(self):
        run = _task("n", "Morning run", _utc(2026, 9, 11, 5), BXL,
                    all_day=False, floating=True)
        assert "| Due: 2026-09-11 07:00 (floating)" in format_task_row_markdown(run, LA)
        assert "2026-09-11 07:00 (floating)" in format_task_markdown(run, LA)
        payload = format_task_json(run, LA, omit_defaults=True)
        assert payload["due_date"] == "2026-09-11T07:00:00+02:00"
        assert payload["is_floating"] is True

    def test_is_floating_is_absent_unless_true(self):
        assert "is_floating" not in format_task_json(FRAME, LA)

    def test_json_timed_task_is_an_iso_timestamp_where_the_user_is(self):
        timed = _task("m", "Call", _utc(2026, 9, 11, 0), BXL, all_day=False)
        assert format_task_json(timed, LA)["due_date"] == "2026-09-10T17:00:00-07:00"

    def test_due_sort_orders_by_the_shown_day(self):
        # Sept 10 at 18:00 in SF is 01:00 UTC on Sept 11, a later moment than
        # an app-written Brussels all-day task for Sept 11 (22:00 UTC Sept 10).
        # The shown days are Sept 10 and Sept 11, so Sept 10 must come first.
        evening = _task("q", "Evening call", _utc(2026, 9, 11, 1), LA, all_day=False)
        bxl_sept_11 = _task("r", "Brussels Sept 11", _utc(2026, 9, 10, 22), BXL)
        ordered = sorted([bxl_sept_11, evening], key=task_sort_key("due_asc", LA))
        assert [t.title for t in ordered] == ["Evening call", "Brussels Sept 11"]
        ordered = sorted([evening, bxl_sept_11], key=task_sort_key("due_desc", LA))
        assert [t.title for t in ordered] == ["Brussels Sept 11", "Evening call"]


# =============================================================================
# Writes
# =============================================================================


def _api(existing: Task | None = None) -> UnifiedTickTickAPI:
    """A real UnifiedTickTickAPI with a mocked V2 client, default zone LA."""
    api = UnifiedTickTickAPI.__new__(UnifiedTickTickAPI)
    api._initialized = True
    api._default_timezone = LA
    api._inbox_id = "inbox123"
    api._router = MagicMock()
    api._router.has_v2 = True
    api._v2_client = MagicMock()
    api._v2_client.batch_tasks = AsyncMock(return_value={"id2etag": {}, "id2error": {}})
    api._v2_client.create_task = AsyncMock(return_value={"id2etag": {"n" * 24: "e"}})
    api.get_task = AsyncMock(return_value=existing)  # type: ignore[assignment]
    return api


def _sent(api: UnifiedTickTickAPI) -> dict:
    sent = api._v2_client.batch_tasks.call_args.kwargs["update"]
    assert len(sent) == 1
    return sent[0]


async def _update(existing: Task, **fields) -> dict:
    api = _api(existing.model_copy(deep=True))
    await api.batch_update_tasks([{"task_id": existing.id, "project_id": PROJ, **fields}])
    return _sent(api)


class TestUpdateWrites:
    async def test_plain_date_on_a_brussels_all_day_task(self):
        # The report's DGPH move to 09/10: midnight in the task's own zone.
        sent = await _update(DGPH, due_date="2026-09-10", start_date="2026-09-10")
        assert sent["dueDate"] == "2026-09-09T22:00:00.000+0000"
        assert sent["startDate"] == "2026-09-09T22:00:00.000+0000"
        assert sent["timeZone"] == BXL  # the task keeps its zone

    async def test_plain_date_on_an_la_all_day_task(self):
        # Used to be midnight UTC, which is Sept 11 17:00 in LA: a day early.
        sent = await _update(FRAME, due_date="2026-09-12")
        assert sent["dueDate"] == "2026-09-12T07:00:00.000+0000"
        assert sent["timeZone"] == LA

    async def test_time_without_offset_is_where_the_user_is(self):
        timed = _task("m", "Call", _utc(2026, 9, 11, 0), BXL, all_day=False)
        sent = await _update(timed, due_date="2026-09-10T17:00:00")
        assert sent["dueDate"] == "2026-09-11T00:00:00.000+0000"  # 17:00 in SF
        assert sent["timeZone"] == BXL

    async def test_value_with_offset_is_exact(self):
        sent = await _update(DGPH, due_date="2026-09-10T17:00:00-07:00")
        assert sent["dueDate"] == "2026-09-11T00:00:00.000+0000"

    async def test_zone_in_the_same_request_wins(self):
        sent = await _update(DGPH, due_date="2026-09-10", time_zone="Asia/Tokyo")
        assert sent["dueDate"] == "2026-09-09T15:00:00.000+0000"
        assert sent["timeZone"] == "Asia/Tokyo"

    async def test_all_day_task_without_a_zone_gets_the_default(self):
        no_zone = _task("o", "No zone", _utc(2026, 9, 11, 0), "")
        sent = await _update(no_zone, due_date="2026-09-10")
        assert sent["dueDate"] == "2026-09-10T07:00:00.000+0000"
        assert sent["timeZone"] == LA

    async def test_plain_date_from_json_round_trips(self):
        # A caller copies the JSON "due_date" back into an update.
        shown = format_task_json(ODYSSEY, LA)["due_date"]
        sent = await _update(ODYSSEY, due_date=shown)
        written = Task(id="x" * 24, project_id=PROJ, due_date=sent["dueDate"],
                       time_zone=sent["timeZone"], is_all_day=True)
        assert written.due_day(LA) == ODYSSEY.due_day(LA) == date(2026, 9, 12)

    async def test_unreadable_date_raises_instead_of_clearing(self):
        api = _api(DGPH.model_copy(deep=True))
        with pytest.raises(TickTickAPIError, match="Could not read due_date"):
            await api.batch_update_tasks(
                [{"task_id": DGPH.id, "project_id": PROJ, "due_date": "next friday"}]
            )
        api._v2_client.batch_tasks.assert_not_called()


class TestCreateWrites:
    async def _create(self, *specs) -> AsyncMock:
        api = _api(FRAME)
        await api.batch_create_tasks(list(specs))
        return api._v2_client.create_task

    async def test_all_day_without_zone_uses_the_default_and_sets_it(self):
        create = await self._create({"title": "T", "due_date": "2026-09-11", "all_day": True})
        kwargs = create.call_args.kwargs
        assert kwargs["due_date"] == "2026-09-11T07:00:00.000+0000"
        assert kwargs["time_zone"] == LA

    async def test_all_day_with_an_explicit_zone(self):
        create = await self._create(
            {"title": "T", "due_date": "2026-09-11", "all_day": True, "time_zone": BXL}
        )
        kwargs = create.call_args.kwargs
        assert kwargs["due_date"] == "2026-09-10T22:00:00.000+0000"
        assert kwargs["time_zone"] == BXL

    async def test_timed_without_offset_is_where_the_user_is(self):
        create = await self._create({"title": "T", "due_date": "2026-09-11T09:30:00"})
        assert create.call_args.kwargs["due_date"] == "2026-09-11T16:30:00.000+0000"

    async def test_dateless_task_sends_no_zone(self):
        create = await self._create({"title": "T"})
        assert create.call_args.kwargs["time_zone"] is None

    async def test_one_unreadable_date_creates_nothing(self):
        api = _api(FRAME)
        with pytest.raises(TickTickAPIError, match="Could not read due_date"):
            await api.batch_create_tasks([
                {"title": "fine", "due_date": "2026-09-11"},
                {"title": "broken", "due_date": "someday"},
            ])
        api._v2_client.create_task.assert_not_called()


# =============================================================================
# TICKTICK_TIMEZONE reaches the write path
# =============================================================================


def test_default_zone_is_passed_down_to_the_unified_api():
    client = TickTickClient(client_id="x", client_secret="y", default_timezone=LA)
    assert client._default_timezone == LA
    assert client._api._default_tz() == LA


def test_from_settings_uses_ticktick_timezone():
    settings = SimpleNamespace(
        validate_all_ready=lambda: None,
        client_id="x",
        client_secret=SimpleNamespace(get_secret_value=lambda: "y"),
        redirect_uri="http://localhost:8080/callback",
        get_v1_access_token=lambda: None,
        username=None,
        get_v2_password=lambda: None,
        get_v2_token=lambda: None,
        get_v2_cookies=lambda: None,
        timeout=30.0,
        device_id=None,
        timezone=LA,
    )
    client = TickTickClient.from_settings(settings)  # type: ignore[arg-type]
    assert client._api._default_tz() == LA


# =============================================================================
# "Today" follows the user, not the server clock
# =============================================================================

# 18:00 on Sept 10 in San Francisco, which is already Sept 11 in UTC. Railway's
# clock is UTC, so a server date here used to be one day ahead of the user.
_SF_EVENING = datetime(2026, 9, 11, 1, 0, tzinfo=timezone.utc)


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return _SF_EVENING.astimezone(tz) if tz else _SF_EVENING.replace(tzinfo=None)


class _UtcServerDate(date):
    """date.today() on a server whose clock is UTC, like Railway."""

    @classmethod
    def today(cls):  # type: ignore[override]
        return _SF_EVENING.date()


@pytest.fixture
def frozen_clock(monkeypatch):
    import ticktick_sdk.unified.api as unified_api

    monkeypatch.setattr(unified_api, "datetime", _FrozenDateTime)
    monkeypatch.setattr(server, "datetime", _FrozenDateTime)
    monkeypatch.setattr(unified_api, "date", _UtcServerDate)


@pytest.mark.usefixtures("frozen_clock")
class TestTodayFollowsTheUser:
    def test_unified_api_today(self):
        api = _api()
        assert api._today() == date(2026, 9, 10)
        api._default_timezone = "UTC"
        assert api._today() == date(2026, 9, 11)

    def test_server_today(self, monkeypatch):
        monkeypatch.setattr(server, "USER_TIMEZONE", LA)
        assert server._today() == date(2026, 9, 10)

    async def test_habit_checkin_without_a_date_lands_on_the_users_today(self):
        from ticktick_sdk.models import Habit

        api = _api()
        api.get_habit = AsyncMock(return_value=Habit(id="h" * 24, name="Water"))  # type: ignore[assignment]
        api.get_habit_checkins = AsyncMock(return_value={})  # type: ignore[assignment]
        api._v2_client.create_habit_checkin = AsyncMock(return_value={})
        api._v2_client.update_habit = AsyncMock(
            return_value={"id2etag": {"h" * 24: "e"}, "id2error": {}}
        )

        await api.checkin_habit("h" * 24)

        stamp = api._v2_client.create_habit_checkin.call_args.kwargs["checkin_stamp"]
        assert stamp == 20260910  # the server clock would have said 20260911



# =============================================================================
# Tool descriptions name the zone and say not to convert all-day dates
# =============================================================================


async def test_tool_descriptions_name_the_current_zone():
    from ticktick_sdk.settings import get_settings

    label = f"TICKTICK_TIMEZONE (now {get_settings().timezone})"
    tools = {t.name: t for t in await server.mcp.list_tools()}
    for name in ("ticktick_list_tasks", "ticktick_search_tasks", "ticktick_get_task",
                 "ticktick_create_tasks", "ticktick_update_tasks",
                 "ticktick_checkin_habits"):
        # Once per description, so it does not clutter the text.
        assert tools[name].description.count(label) == 1, name
    assert label in str(tools["ticktick_update_tasks"].inputSchema)
    assert label in str(tools["ticktick_list_tasks"].inputSchema)


async def test_read_tools_say_not_to_convert_all_day_dates():
    tools = {t.name: t for t in await server.mcp.list_tools()}
    for name in ("ticktick_list_tasks", "ticktick_search_tasks", "ticktick_get_task"):
        assert "do not convert" in tools[name].description, name
