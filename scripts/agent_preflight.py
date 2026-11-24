"""Pre-flight run agent (PRD §5.4): GO / NO-GO + deadline run.

Pemakaian (dari root repo):
    uv run --project backend python scripts/agent_preflight.py [--override] [--at ISO8601]

Keluaran baris pertama: "GO" atau "NO-GO <alasan>". Exit code 0 = GO, 1 = NO-GO.
Skrip ini TIDAK mengambil lock; jalankan scripts/agent_lock.py acquire setelah GO.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
CONTROL_FILE = Path(__file__).resolve().parent.parent / ".agent" / "control.yaml"


@dataclass(frozen=True)
class Decision:
    go: bool
    reason: str
    now: datetime
    deadline: datetime | None = None
    stop_new_tasks: datetime | None = None


def _hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def _is_free_day(day: date, control: dict[str, Any]) -> bool:
    holidays = {str(d) for d in control.get("public_holidays") or []}
    return bool(control.get("work_on_public_holidays")) and day.isoformat() in holidays


def in_blocked_window(moment: datetime, control: dict[str, Any]) -> bool:
    window = control["blocked_window"]
    if DAYS[moment.weekday()] not in window["days"] or _is_free_day(moment.date(), control):
        return False
    return _hhmm(window["start"]) <= moment.time() < _hhmm(window["end"])


def _scheduled_runs(control: dict[str, Any], start: datetime, days: int = 3) -> list[datetime]:
    runs: list[datetime] = []
    for offset in range(days + 1):
        day = (start + timedelta(days=offset)).date()
        for entry in control["schedule"]:
            if DAYS[day.weekday()] in entry["days"]:
                for t in entry["times"]:
                    runs.append(datetime.combine(day, _hhmm(t), tzinfo=start.tzinfo))
    return sorted(r for r in runs if r > start)


def _next_blocked_start(control: dict[str, Any], start: datetime, days: int = 7) -> datetime | None:
    window = control["blocked_window"]
    for offset in range(days + 1):
        day = (start + timedelta(days=offset)).date()
        if DAYS[day.weekday()] in window["days"] and not _is_free_day(day, control):
            candidate = datetime.combine(day, _hhmm(window["start"]), tzinfo=start.tzinfo)
            if candidate > start:
                return candidate
    return None


def compute_deadline(now: datetime, control: dict[str, Any]) -> tuple[datetime, datetime]:
    """Deadline = min(run berikutnya - buffer, awal blocked_window berikutnya)."""
    buffer = timedelta(minutes=int(control.get("deadline_buffer_minutes", 15)))
    # Run yang baru saja dimulai (≤ 10 menit lalu) tidak dihitung sebagai "run berikutnya".
    candidates = [r - buffer for r in _scheduled_runs(control, now + timedelta(minutes=10))]
    blocked = _next_blocked_start(control, now)
    if blocked:
        candidates.append(blocked)
    deadline = min(candidates) if candidates else now + timedelta(hours=4)
    stop_new = deadline - timedelta(minutes=int(control.get("stop_new_tasks_minutes", 30)))
    return deadline, stop_new


def decide(now: datetime, control: dict[str, Any], override: bool = False) -> Decision:
    mode = control.get("mode", "active")
    if mode in ("paused", "stopped"):
        return Decision(False, f"mode={mode}", now)
    if not override and in_blocked_window(now, control):
        return Decision(False, "di dalam jam terlarang (blocked_window)", now)
    deadline, stop_new = compute_deadline(now, control)
    if not override and now >= stop_new:
        return Decision(False, "terlalu dekat deadline, tidak ada waktu untuk tugas baru", now)
    return Decision(True, f"mode={mode}", now, deadline, stop_new)


def load_control(path: Path = CONTROL_FILE) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh)
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--override", action="store_true", help="Perintah PO: abaikan jam terlarang"
    )
    parser.add_argument("--at", help="Waktu uji (ISO 8601), default: sekarang")
    args = parser.parse_args(argv)

    control = load_control()
    tz = ZoneInfo(control.get("timezone", "Asia/Jakarta"))
    now = datetime.fromisoformat(args.at).astimezone(tz) if args.at else datetime.now(tz)
    decision = decide(now, control, override=args.override)

    if not decision.go:
        print(f"NO-GO {decision.reason}")
        print(f"now={now.isoformat(timespec='minutes')}")
        return 1
    assert decision.deadline and decision.stop_new_tasks
    print("GO")
    print(f"now={now.isoformat(timespec='minutes')}")
    print(f"stop_new_tasks={decision.stop_new_tasks.isoformat(timespec='minutes')}")
    print(f"deadline={decision.deadline.isoformat(timespec='minutes')}")
    mode = control.get("mode", "active")
    hemat = mode == "hemat"  # MODE HEMAT: 1 tugas, tanpa sub-agent paralel (PRD §5.3)
    print(f"mode={mode}")
    print(f"max_tasks_per_run={1 if hemat else control.get('max_tasks_per_run', 3)}")
    print(f"max_parallel_subagents={1 if hemat else control.get('max_parallel_subagents', 2)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
