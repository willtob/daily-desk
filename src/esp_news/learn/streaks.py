"""Streak arithmetic over calendar days.

Deliberately pure and deliberately separate from ``store.py``. Everything here
takes dates and returns numbers — no database, no ``date.today()``, no implicit
timezone. That is not tidiness: the bugs this code can have are all off-by-one
bugs in date arithmetic, and they are only testable if "today" is an argument.

**Timestamps are stored in UTC, streaks are counted in local days.** Those are
different things and conflating them is the classic failure. A session finished
at 23:30 on Tuesday in Madrid is Wednesday 21:30 UTC; counting UTC days would
credit it to Wednesday, and a Tuesday-only week would show as a broken streak.
So every stored instant is converted through :func:`local_day` exactly once,
and no other code in the package is allowed to call ``.date()`` on a timestamp.

**Yesterday does not break the streak.** A streak whose most recent qualifying
day is yesterday is alive, not broken — today is still in progress, and no full
calendar day has passed without a session yet. Anchoring on "today or nothing"
is the off-by-one that makes a streak vanish every morning until you sit down.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAY = timedelta(days=1)


def resolve_zone(name: str = "") -> tzinfo:
    """The zone streaks are counted in.

    An empty name means the system's local zone, which is what ``topics.yaml``
    ships with. An unknown IANA name raises rather than silently falling back:
    a typo in the config quietly reverting to system-local would move day
    boundaries without telling anyone, and the symptom — an occasional missing
    streak day — would be blamed on anything but the config.
    """
    if not name.strip():
        # The current local offset, resolved once. datetime.astimezone() with no
        # argument attaches the system zone.
        return datetime.now().astimezone().tzinfo or timezone.utc
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone in topics.yaml: {name!r}") from exc


def local_day(moment: datetime, zone: tzinfo) -> date:
    """The calendar day ``moment`` falls on in ``zone``.

    A naive datetime is read as UTC, because that is what the store writes —
    SQLite has no datetime type and the ISO strings in it are all UTC. Guessing
    "naive means local" here would be wrong for every row.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(zone).date()


def today_in(zone: tzinfo) -> date:
    """Now, as a calendar day in ``zone``."""
    return datetime.now(timezone.utc).astimezone(zone).date()


def current_streak(qualifying_days: Iterable[date], *, today: date) -> int:
    """Consecutive qualifying days ending today or yesterday.

    Returns 0 when the most recent qualifying day is older than yesterday.
    Days after ``today`` (clock skew, a timezone change mid-week) are ignored
    rather than counted, since the walk only ever goes backwards from an anchor
    that is at most ``today``.
    """
    days = set(qualifying_days)
    if not days:
        return 0

    if today in days:
        cursor = today
    elif (today - DAY) in days:
        cursor = today - DAY
    else:
        return 0

    length = 0
    while cursor in days:
        length += 1
        cursor -= DAY
    return length


def longest_streak(qualifying_days: Iterable[date]) -> int:
    """The longest run of consecutive qualifying days ever recorded.

    Includes the current one, so a personal best set today reads as a personal
    best rather than as the second-longest.
    """
    days = sorted(set(qualifying_days))
    if not days:
        return 0

    longest = best = 1
    for previous, day in zip(days, days[1:]):
        best = best + 1 if day - previous == DAY else 1
        longest = max(longest, best)
    return longest
