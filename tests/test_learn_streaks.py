"""Streak arithmetic — where the off-by-one bugs live.

Three failures this suite exists to catch, none of which show up in casual use
until they have already cost a streak:

  * **The morning reset.** Anchoring the current streak on "today has a
    qualifying session" reads correct and is wrong: it zeroes a live streak
    every day between midnight and whenever you sit down. The rule is that a
    streak survives until a *full* calendar day passes with nothing in it, so
    a last session of yesterday is alive and a last session of the day before
    is not. That boundary is one line of code and it is tested from both sides.

  * **UTC days versus local days.** Timestamps are stored in UTC and streaks
    are counted locally. An evening session in a UTC+ zone is already the next
    day in UTC, so counting UTC days moves it, and a week of evening sessions
    reads as a broken streak. Tested against a real zone with a real offset
    rather than a fixed one, so DST is included.

  * **Double-counting a day.** Two qualifying sessions on one day is one day of
    streak. Trivially true of a set and trivially false of a list, and the
    difference is invisible until a streak reads 9 after five days.

Everything here takes ``today`` as an argument. A test that called
``date.today()`` would pass in the morning and fail near midnight, which is
precisely the class of bug being tested for.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from esp_news.learn.streaks import (
    current_streak,
    local_day,
    longest_streak,
    resolve_zone,
    today_in,
)

TODAY = date(2026, 8, 10)


def days_back(*offsets: int) -> set[date]:
    """Qualifying days expressed as "n days before TODAY"."""
    return {TODAY - timedelta(days=n) for n in offsets}


# ── current streak: the day boundary ─────────────────────────────────────────


def test_no_sessions_is_no_streak():
    assert current_streak(set(), today=TODAY) == 0


def test_a_session_today_starts_a_streak_of_one():
    assert current_streak(days_back(0), today=TODAY) == 1


def test_yesterday_alone_keeps_the_streak_alive():
    """The morning-reset bug, from the side that must NOT be zero.

    Nothing today yet, one session yesterday: no full calendar day has passed
    without a session, so the streak stands. Getting this wrong means the
    number is 0 every morning until you sit down — which is exactly when a
    streak is doing its job.
    """
    assert current_streak(days_back(1), today=TODAY) == 1


def test_the_day_before_yesterday_is_a_broken_streak():
    """And from the side that must be zero: a full day passed with nothing."""
    assert current_streak(days_back(2), today=TODAY) == 0


def test_a_run_ending_yesterday_counts_every_day_in_it():
    assert current_streak(days_back(1, 2, 3, 4), today=TODAY) == 4


def test_a_run_ending_today_counts_every_day_in_it():
    assert current_streak(days_back(0, 1, 2, 3, 4), today=TODAY) == 5


def test_only_the_run_touching_today_counts():
    """An older run, however long, does not add to the current streak."""
    days = days_back(0, 1) | days_back(5, 6, 7, 8, 9, 10)
    assert current_streak(days, today=TODAY) == 2


def test_a_gap_of_one_day_ends_the_streak_there():
    # 0, 1, then nothing on 2, then more history behind it.
    days = days_back(0, 1, 3, 4, 5)
    assert current_streak(days, today=TODAY) == 2


def test_a_duplicate_day_counts_once():
    """Two sessions in one day is one day of streak, not two."""
    from_list = [TODAY, TODAY, TODAY - timedelta(days=1)]
    assert current_streak(from_list, today=TODAY) == 2


def test_a_future_day_does_not_extend_or_break_the_streak():
    """Clock skew or a timezone move should not manufacture a streak.

    The walk is anchored at today and goes backwards, so a stray future day is
    ignored rather than counted or treated as a gap.
    """
    days = days_back(0, 1) | {TODAY + timedelta(days=1)}
    assert current_streak(days, today=TODAY) == 2


def test_a_streak_across_a_month_boundary():
    """Date arithmetic, not day-number arithmetic."""
    days = {date(2026, 7, 30), date(2026, 7, 31), date(2026, 8, 1)}
    assert current_streak(days, today=date(2026, 8, 1)) == 3


def test_a_streak_across_a_leap_day():
    days = {date(2028, 2, 28), date(2028, 2, 29), date(2028, 3, 1)}
    assert current_streak(days, today=date(2028, 3, 1)) == 3


# ── longest streak ───────────────────────────────────────────────────────────


def test_longest_of_nothing_is_zero():
    assert longest_streak(set()) == 0


def test_longest_of_a_single_day_is_one():
    assert longest_streak(days_back(4)) == 1


def test_longest_picks_the_best_run_not_the_last():
    days = days_back(0, 1) | days_back(10, 11, 12, 13, 14)
    assert longest_streak(days) == 5


def test_longest_includes_the_current_run():
    """A personal best set today has to read as a personal best."""
    days = days_back(0, 1, 2, 3) | days_back(20, 21)
    assert longest_streak(days) == 4


def test_longest_ignores_duplicates():
    days = [TODAY, TODAY, TODAY - timedelta(days=1), TODAY - timedelta(days=1)]
    assert longest_streak(days) == 2


def test_longest_does_not_join_runs_across_a_gap():
    days = days_back(0, 1, 3, 4)
    assert longest_streak(days) == 2


# ── local days versus UTC days ───────────────────────────────────────────────

MADRID = ZoneInfo("Europe/Madrid")


def test_a_late_evening_session_belongs_to_the_local_day():
    """23:30 in Madrid is already tomorrow in UTC. It is still tonight.

    This is the conversion the whole streak rests on: get it wrong and every
    late-evening session lands on the following day, which both breaks the run
    it belonged to and fakes one on a day nothing happened.
    """
    stored = datetime(2026, 8, 10, 21, 30, tzinfo=timezone.utc)  # 23:30 Madrid
    assert stored.date() == date(2026, 8, 10)
    assert local_day(stored, MADRID) == date(2026, 8, 10)

    just_after_midnight = datetime(2026, 8, 10, 22, 30, tzinfo=timezone.utc)
    assert just_after_midnight.date() == date(2026, 8, 10)
    assert local_day(just_after_midnight, MADRID) == date(2026, 8, 11)


def test_an_early_morning_session_in_a_negative_offset_zone():
    """The mirror case: 00:30 in New York is still the previous UTC day."""
    new_york = ZoneInfo("America/New_York")
    stored = datetime(2026, 8, 11, 4, 30, tzinfo=timezone.utc)  # 00:30 EDT
    assert stored.date() == date(2026, 8, 11)
    assert local_day(stored, new_york) == date(2026, 8, 11)

    late = datetime(2026, 8, 11, 3, 30, tzinfo=timezone.utc)  # 23:30 EDT, 10th
    assert local_day(late, new_york) == date(2026, 8, 10)


def test_the_offset_is_not_assumed_constant_across_dst():
    """Madrid is UTC+2 in August and UTC+1 in January.

    A hardcoded offset passes every summer test and quietly moves the day
    boundary in winter. ZoneInfo handles it; this pins that it is being used.
    """
    summer = datetime(2026, 8, 10, 22, 30, tzinfo=timezone.utc)
    winter = datetime(2026, 1, 10, 22, 30, tzinfo=timezone.utc)
    assert local_day(summer, MADRID) == date(2026, 8, 11)  # 00:30 CEST
    assert local_day(winter, MADRID) == date(2026, 1, 10)  # 23:30 CET


def test_a_naive_timestamp_is_read_as_utc():
    """What the store writes is UTC. Reading it as local would shift every row."""
    naive = datetime(2026, 8, 10, 22, 30)
    aware = datetime(2026, 8, 10, 22, 30, tzinfo=timezone.utc)
    assert local_day(naive, MADRID) == local_day(aware, MADRID)


def test_a_streak_of_evening_sessions_survives_the_utc_conversion():
    """The end-to-end version of the bug: seven evenings, one streak.

    Counted in UTC these land on seven different days too — but shifted by one,
    so the run appears to end yesterday-plus-one and the current streak reads 0
    on the day it should read 7.
    """
    evenings = [
        datetime(2026, 8, d, 21, 45, tzinfo=timezone.utc) for d in range(4, 11)
    ]
    days = {local_day(e, MADRID) for e in evenings}
    assert len(days) == 7
    assert current_streak(days, today=date(2026, 8, 10)) == 7


# ── zone resolution ──────────────────────────────────────────────────────────


def test_an_empty_zone_name_means_system_local():
    assert resolve_zone("") is not None
    assert resolve_zone("   ") is not None


def test_a_named_zone_resolves():
    assert resolve_zone("Europe/Madrid") == MADRID
    assert resolve_zone("  Europe/Madrid  ") == MADRID


def test_an_unknown_zone_raises_rather_than_falling_back():
    """A typo must not silently revert to system-local.

    Falling back would move the day boundary without saying so, and the symptom
    — an occasional streak day going missing — would be blamed on anything but
    a misspelled config value.
    """
    with pytest.raises(ValueError, match="unknown timezone"):
        resolve_zone("Europe/Barcelona")


def test_today_in_a_zone_is_that_zones_day():
    assert today_in(timezone.utc) == datetime.now(timezone.utc).date()
