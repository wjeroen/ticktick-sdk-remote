"""
Which day a completion counts for, in listings and in statistics.

Live repro (2026-11-02, TICKTICK_TIMEZONE=America/Los_Angeles): `list_tasks`
with status=completed and from_date=to_date=2026-11-02 returned two tasks
completed on Nov 1 at 20:51 Los Angeles time, and to_date=2026-11-03 returned
tasks completed on Nov 2 from 16:08. The server built the right Los Angeles
day, but the V2 client wrote it as a plain `YYYY-MM-DD hh:mm:ss` without its
zone, and TickTick reads that as UTC.

Statistics are counted by TickTick itself, in the account time zone set in
the app (Europe/Paris on that account), so the server names that zone.
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from ticktick_sdk import server
from ticktick_sdk.api.v2.client import TickTickV2Client, _closed_range_param
from ticktick_sdk.models.user import UserStatistics
from ticktick_sdk.tools.formatting import (
    format_statistics_json,
    format_statistics_markdown,
)
from ticktick_sdk.tools.inputs import ResponseFormat, TaskListInput

pytestmark = pytest.mark.unit

LA = ZoneInfo("America/Los_Angeles")
BRUSSELS = ZoneInfo("Europe/Brussels")


# =============================================================================
# The closed-task range is sent in UTC
# =============================================================================


@pytest.mark.parametrize(
    "value, sent",
    [
        # Los Angeles in winter (PST, UTC-8), the reported case.
        (datetime(2026, 11, 2, 0, 0, 0, tzinfo=LA), "2026-11-02 08:00:00"),
        (datetime(2026, 11, 2, 23, 59, 59, tzinfo=LA), "2026-11-03 07:59:59"),
        # Los Angeles in summer (PDT, UTC-7).
        (datetime(2026, 7, 1, 0, 0, 0, tzinfo=LA), "2026-07-01 07:00:00"),
        # East of UTC the day starts the evening before.
        (datetime(2026, 7, 1, 0, 0, 0, tzinfo=BRUSSELS), "2026-06-30 22:00:00"),
        # A value without a zone already means UTC and is sent as it is.
        (datetime(2026, 11, 2, 0, 0, 0), "2026-11-02 00:00:00"),
    ],
)
def test_closed_range_param_is_utc(value, sent):
    assert _closed_range_param(value) == sent


@pytest.mark.parametrize(
    "method, status",
    [("get_completed_tasks", "Completed"), ("get_abandoned_tasks", "Abandoned")],
)
async def test_v2_client_sends_the_range_in_utc(method, status):
    fake = SimpleNamespace(_get_json=AsyncMock(return_value=[]))
    await getattr(TickTickV2Client, method)(
        fake,
        datetime(2026, 11, 2, tzinfo=LA),
        datetime(2026, 11, 2, 23, 59, 59, tzinfo=LA),
        50,
    )
    path = fake._get_json.await_args.args[0]
    params = fake._get_json.await_args.kwargs["params"]
    assert path == "/project/all/closed"
    assert params == {
        "from": "2026-11-02 08:00:00",
        "to": "2026-11-03 07:59:59",
        "status": status,
        "limit": "50",
    }


class RecordingClient:
    """Records the range `list_tasks` asks for, and returns no tasks."""

    def __init__(self):
        self.ranges: list[tuple[datetime, datetime]] = []

    async def get_completed_tasks(self, days=7, limit=100, from_date=None, to_date=None):
        self.ranges.append((from_date, to_date))
        return []

    async def get_abandoned_tasks(self, days=7, limit=100, from_date=None, to_date=None):
        self.ranges.append((from_date, to_date))
        return []

    async def get_all_projects(self):
        return []


def _ctx(client) -> SimpleNamespace:
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"client": client}))


@pytest.mark.parametrize("status", ["completed", "abandoned"])
async def test_list_tasks_day_is_a_local_day_in_utc(monkeypatch, status):
    """The reported case end to end: Nov 2 in Los Angeles, as TickTick gets it."""
    monkeypatch.setattr(server, "USER_TIMEZONE", "America/Los_Angeles")
    client = RecordingClient()
    await server.ticktick_list_tasks(
        TaskListInput(status=status, from_date="2026-11-02", to_date="2026-11-02"),
        _ctx(client),
    )
    from_dt, to_dt = client.ranges[0]
    assert _closed_range_param(from_dt) == "2026-11-02 08:00:00"
    assert _closed_range_param(to_dt) == "2026-11-03 07:59:59"


# =============================================================================
# Statistics name the zone TickTick counts days in
# =============================================================================


def test_markdown_names_the_account_zone_and_the_difference():
    out = format_statistics_markdown(
        UserStatistics(), day_zone="Europe/Paris", tool_zone="America/Los_Angeles"
    )
    note = out.splitlines()[2]
    assert note.startswith("> TickTick counts these days in your TickTick account time zone, Europe/Paris")
    assert "not in TICKTICK_TIMEZONE (America/Los_Angeles)" in note


def test_markdown_same_zone_has_no_warning():
    out = format_statistics_markdown(
        UserStatistics(), day_zone="Europe/Paris", tool_zone="Europe/Paris"
    )
    assert "account time zone, Europe/Paris." in out
    assert "not in TICKTICK_TIMEZONE" not in out


def test_markdown_without_a_zone_has_no_note():
    assert "time zone" not in format_statistics_markdown(UserStatistics())


def test_json_names_the_account_zone():
    out = format_statistics_json(
        UserStatistics(), day_zone="Europe/Paris", tool_zone="America/Los_Angeles"
    )
    assert out["day_time_zone"] == "Europe/Paris"
    assert "not in TICKTICK_TIMEZONE (America/Los_Angeles)" in out["_day_zone_hint"]
    assert "day_time_zone" not in format_statistics_json(UserStatistics())


async def test_statistics_tool_reads_the_account_zone(monkeypatch):
    monkeypatch.setattr(server, "USER_TIMEZONE", "America/Los_Angeles")
    client = SimpleNamespace(
        get_statistics=AsyncMock(return_value=UserStatistics(todayCompleted=6)),
        get_preferences=AsyncMock(return_value={"timeZone": "Europe/Paris"}),
    )
    out = json.loads(await server.ticktick_get_statistics(
        _ctx(client), response_format=ResponseFormat.JSON
    ))
    assert out["day_time_zone"] == "Europe/Paris"
    assert out["completions"]["today"] == 6
    markdown = await server.ticktick_get_statistics(_ctx(client))
    assert "account time zone, Europe/Paris, not in TICKTICK_TIMEZONE" in markdown


async def test_statistics_still_show_when_preferences_fail():
    client = SimpleNamespace(
        get_statistics=AsyncMock(return_value=UserStatistics(todayCompleted=6)),
        get_preferences=AsyncMock(side_effect=RuntimeError("boom")),
    )
    out = await server.ticktick_get_statistics(_ctx(client))
    assert "- Today: 6" in out
    assert "time zone" not in out


def test_statistics_description_names_the_current_zone():
    assert "TICKTICK_TIMEZONE (now " in server.ticktick_get_statistics.__doc__
