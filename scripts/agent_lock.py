"""Lock antar-run agent berbasis branch git `agent/lock` (PRD §5.4, §5.11).

Branch tidak pernah dihapus (proxy sesi menolak penghapusan ref). Setiap acquire/release
adalah commit fast-forward bertree kosong; dua run yang berebut akan bertabrakan dan push
kedua ditolak (compare-and-swap). Lihat docs/adr/0002-lock-antar-run-agent.md.

Pemakaian (dari root repo):
    python3 scripts/agent_lock.py status
    python3 scripts/agent_lock.py acquire --run-id 2026-11-25T1807
    python3 scripts/agent_lock.py release --run-id 2026-11-25T1807
Exit code: 0 = berhasil / tidak terkunci, 1 = terkunci oleh run lain / gagal.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

REMOTE = "origin"
BRANCH = "agent/lock"
REF = f"refs/heads/{BRANCH}"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "erp-agent",
    "GIT_AUTHOR_EMAIL": "agent@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "erp-agent",
    "GIT_COMMITTER_EMAIL": "agent@users.noreply.github.com",
}


@dataclass(frozen=True)
class LockState:
    sha: str | None
    locked: bool
    run_id: str | None
    at: datetime | None


def parse_message(sha: str | None, message: str) -> LockState:
    """Format pesan: 'LOCKED <run_id> <iso-utc>' atau 'UNLOCKED <run_id> <iso-utc>'."""
    parts = message.strip().split()
    if len(parts) >= 3 and parts[0] in ("LOCKED", "UNLOCKED"):
        try:
            at = datetime.fromisoformat(parts[2])
        except ValueError:
            at = None
        return LockState(sha, parts[0] == "LOCKED", parts[1], at)
    return LockState(sha, False, None, None)


def is_stale(state: LockState, now: datetime, stale_hours: float) -> bool:
    return state.locked and (state.at is None or now - state.at > timedelta(hours=stale_hours))


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argumen tetap, bukan input pengguna
        ["git", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=check,
        env={**os.environ, **GIT_ENV},
    )


def read_state() -> LockState:
    out = _git("ls-remote", REMOTE, REF).stdout.strip()
    if not out:
        return LockState(None, False, None, None)
    sha = out.split()[0]
    _git("fetch", "--quiet", "--depth=1", REMOTE, f"+{REF}:refs/remotes/{REMOTE}/{BRANCH}")
    message = _git("log", "-1", "--format=%B", sha).stdout
    return parse_message(sha, message)


def _push_state(parent: str | None, label: str, run_id: str) -> bool:
    empty_tree = _git("hash-object", "-t", "tree", "/dev/null").stdout.strip()
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    args = ["commit-tree", empty_tree, "-m", f"{label} {run_id} {stamp}"]
    if parent:
        args += ["-p", parent]
    sha = _git(*args).stdout.strip()
    # Push biasa (tanpa force): ditolak bila tip remote sudah berubah sejak kita baca.
    return _git("push", "--quiet", REMOTE, f"{sha}:{REF}", check=False).returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "acquire", "release"])
    parser.add_argument("--run-id", default="manual")
    parser.add_argument("--stale-hours", type=float, default=6.0)
    args = parser.parse_args(argv)

    state = read_state()
    now = datetime.now(UTC)
    if args.action == "status":
        stale = is_stale(state, now, args.stale_hours)
        print(f"locked={state.locked} run_id={state.run_id} at={state.at} stale={stale}")
        return 1 if state.locked and not is_stale(state, now, args.stale_hours) else 0

    if args.action == "acquire":
        if state.locked and not is_stale(state, now, args.stale_hours):
            print(f"NO-GO terkunci oleh run {state.run_id} sejak {state.at}")
            return 1
        if _push_state(state.sha, "LOCKED", args.run_id):
            print(f"LOCK OK run_id={args.run_id}")
            return 0
        print("NO-GO kalah berebut lock dengan run lain")
        return 1

    # release
    if not state.locked or state.run_id != args.run_id:
        print(f"Tidak ada lock milik run {args.run_id} (locked={state.locked} run={state.run_id})")
        return 0
    if _push_state(state.sha, "UNLOCKED", args.run_id):
        print("UNLOCK OK")
        return 0
    print("Gagal melepas lock — akan dianggap basi setelah batas waktu")
    return 1


if __name__ == "__main__":
    sys.exit(main())
