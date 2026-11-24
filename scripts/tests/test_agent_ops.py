import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_lock
import agent_preflight as pf

WIB = ZoneInfo("Asia/Jakarta")
CONTROL = pf.load_control()


def at(y: int, mo: int, d: int, h: int, mi: int) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=WIB)


# 2026-11-24 = Selasa, 2026-11-28 = Sabtu, 2026-11-30 = Senin


def test_blocked_during_weekday_daytime() -> None:
    d = pf.decide(at(2026, 11, 24, 10, 0), CONTROL)
    assert not d.go and "blocked_window" in d.reason


def test_blocked_starts_at_0500_weekday() -> None:
    assert not pf.decide(at(2026, 11, 25, 5, 0), CONTROL).go
    assert pf.in_blocked_window(at(2026, 11, 25, 17, 59), CONTROL)
    assert not pf.in_blocked_window(at(2026, 11, 25, 18, 0), CONTROL)


def test_evening_run_deadline_before_midnight_run() -> None:
    d = pf.decide(at(2026, 11, 24, 18, 7), CONTROL)
    assert d.go
    assert d.deadline == at(2026, 11, 24, 23, 52)
    assert d.stop_new_tasks == at(2026, 11, 24, 23, 22)


def test_midnight_weekday_run_hard_stop_0500() -> None:
    d = pf.decide(at(2026, 11, 25, 0, 7), CONTROL)
    assert d.go
    assert d.deadline == at(2026, 11, 25, 5, 0)
    assert d.stop_new_tasks == at(2026, 11, 25, 4, 30)


def test_monday_midnight_run_stops_at_0500() -> None:
    d = pf.decide(at(2026, 11, 30, 0, 7), CONTROL)
    assert d.go and d.deadline == at(2026, 11, 30, 5, 0)


def test_weekend_daytime_allowed() -> None:
    d = pf.decide(at(2026, 11, 28, 10, 7), CONTROL)
    assert d.go and d.deadline == at(2026, 11, 28, 14, 52)


def test_too_close_to_deadline_is_nogo() -> None:
    assert not pf.decide(at(2026, 11, 25, 4, 40), CONTROL).go


def test_paused_and_stopped() -> None:
    for mode in ("paused", "stopped"):
        d = pf.decide(at(2026, 11, 28, 10, 7), {**CONTROL, "mode": mode})
        assert not d.go and mode in d.reason


def test_override_ignores_window_but_not_stop() -> None:
    assert pf.decide(at(2026, 11, 24, 10, 0), CONTROL, override=True).go
    assert not pf.decide(at(2026, 11, 24, 10, 0), {**CONTROL, "mode": "stopped"}, override=True).go


def test_public_holiday_is_free_day() -> None:
    control = {**CONTROL, "public_holidays": ["2026-12-25"], "work_on_public_holidays": True}
    assert not pf.in_blocked_window(at(2026, 12, 25, 10, 0), control)


def test_lock_message_parsing_and_staleness() -> None:
    now = datetime(2026, 11, 25, tzinfo=UTC)
    state = agent_lock.parse_message("abc", f"LOCKED run1 {(now - timedelta(hours=1)).isoformat()}")
    assert state.locked and state.run_id == "run1"
    assert not agent_lock.is_stale(state, now, 6)
    old = agent_lock.parse_message("abc", f"LOCKED run1 {(now - timedelta(hours=7)).isoformat()}")
    assert agent_lock.is_stale(old, now, 6)
    assert not agent_lock.parse_message("abc", "lock probe").locked
    assert not agent_lock.parse_message("abc", f"UNLOCKED run1 {now.isoformat()}").locked
