"""
Tests for the markdown list-row recurrence label.

The label shows the recurrence rule itself, not just its frequency, so two
tasks with different cadences never render identically. Covers:

  - Every RRULE frequency, with and without INTERVAL.
  - BYDAY, BYMONTHDAY (including the negative "last day" form), BYMONTH,
    BYSETPOS, COUNT, UNTIL, and TickTick's TT_TIMES extension.
  - WKST is dropped, because it only names the first day of the week and
    never changes which dates the rule produces.
  - Non-RRULE rules are shown in full, and unusable rules degrade to
    [REPEATS] or to no label at all.
"""

from __future__ import annotations

import pytest

from ticktick_sdk.tools.formatting import repeat_flag_indicator


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        # Frequencies, bare.
        ("RRULE:FREQ=MINUTELY", "[FREQ=MINUTELY] "),
        ("RRULE:FREQ=HOURLY", "[FREQ=HOURLY] "),
        ("RRULE:FREQ=DAILY", "[FREQ=DAILY] "),
        ("RRULE:FREQ=WEEKLY", "[FREQ=WEEKLY] "),
        ("RRULE:FREQ=MONTHLY", "[FREQ=MONTHLY] "),
        ("RRULE:FREQ=YEARLY", "[FREQ=YEARLY] "),
        # Intervals: the case the old FREQ-only label collapsed.
        ("RRULE:FREQ=MINUTELY;INTERVAL=30", "[FREQ=MINUTELY;INTERVAL=30] "),
        ("RRULE:FREQ=HOURLY;INTERVAL=6", "[FREQ=HOURLY;INTERVAL=6] "),
        ("RRULE:FREQ=DAILY;INTERVAL=8", "[FREQ=DAILY;INTERVAL=8] "),
        ("RRULE:FREQ=YEARLY;INTERVAL=2", "[FREQ=YEARLY;INTERVAL=2] "),
        # Weekly by weekday.
        ("RRULE:FREQ=WEEKLY;BYDAY=MO", "[FREQ=WEEKLY;BYDAY=MO] "),
        ("RRULE:FREQ=WEEKLY;BYDAY=WE,SU", "[FREQ=WEEKLY;BYDAY=WE,SU] "),
        # Monthly by day-of-month, including the "last day" form.
        ("RRULE:FREQ=MONTHLY;BYMONTHDAY=27", "[FREQ=MONTHLY;BYMONTHDAY=27] "),
        (
            "RRULE:FREQ=MONTHLY;INTERVAL=1;BYMONTHDAY=6,20,29",
            "[FREQ=MONTHLY;INTERVAL=1;BYMONTHDAY=6,20,29] ",
        ),
        ("RRULE:FREQ=MONTHLY;BYMONTHDAY=-1", "[FREQ=MONTHLY;BYMONTHDAY=-1] "),
        # Monthly by ordinal weekday, and by set position.
        ("RRULE:FREQ=MONTHLY;BYDAY=2TU", "[FREQ=MONTHLY;BYDAY=2TU] "),
        ("RRULE:FREQ=MONTHLY;BYDAY=-1FR", "[FREQ=MONTHLY;BYDAY=-1FR] "),
        (
            "RRULE:FREQ=MONTHLY;BYSETPOS=3;BYDAY=MO,TU,WE,TH,FR",
            "[FREQ=MONTHLY;BYSETPOS=3;BYDAY=MO,TU,WE,TH,FR] ",
        ),
        # Yearly on a fixed calendar date.
        (
            "RRULE:FREQ=YEARLY;BYMONTH=3;BYMONTHDAY=15",
            "[FREQ=YEARLY;BYMONTH=3;BYMONTHDAY=15] ",
        ),
        # Series that end.
        ("RRULE:FREQ=DAILY;COUNT=5", "[FREQ=DAILY;COUNT=5] "),
        (
            "RRULE:FREQ=DAILY;UNTIL=20261231T235959Z",
            "[FREQ=DAILY;UNTIL=20261231T235959Z] ",
        ),
        # TickTick's own extension for habit-style "X times per week".
        ("RRULE:FREQ=WEEKLY;TT_TIMES=5", "[FREQ=WEEKLY;TT_TIMES=5] "),
    ],
)
def test_rule_is_shown_verbatim(rule: str, expected: str) -> None:
    assert repeat_flag_indicator(rule) == expected


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        ("RRULE:FREQ=WEEKLY;WKST=MO;INTERVAL=2", "[FREQ=WEEKLY;INTERVAL=2] "),
        (
            "RRULE:FREQ=WEEKLY;WKST=MO;INTERVAL=1;BYDAY=SA,SU",
            "[FREQ=WEEKLY;INTERVAL=1;BYDAY=SA,SU] ",
        ),
        ("RRULE:WKST=SU;FREQ=DAILY", "[FREQ=DAILY] "),
    ],
)
def test_wkst_is_dropped(rule: str, expected: str) -> None:
    assert repeat_flag_indicator(rule) == expected


def test_intervals_no_longer_collide() -> None:
    """Every-day and every-8-days used to both render as [DAILY]."""
    assert repeat_flag_indicator("RRULE:FREQ=DAILY") != repeat_flag_indicator(
        "RRULE:FREQ=DAILY;INTERVAL=8"
    )


def test_non_rrule_forms_are_shown_in_full() -> None:
    assert repeat_flag_indicator("ERULE:NAME=CUSTOM") == "[ERULE:NAME=CUSTOM] "


@pytest.mark.parametrize("rule", ["RRULE:", "RRULE:WKST=MO", ";;;"])
def test_unusable_rules_fall_back(rule: str) -> None:
    assert repeat_flag_indicator(rule) == "[REPEATS] "


@pytest.mark.parametrize("rule", [None, "", "   "])
def test_absent_rule_emits_no_label(rule: str | None) -> None:
    assert repeat_flag_indicator(rule) == ""
