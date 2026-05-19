"""Invoke `./run.sh` in a workspace, capture outcome.

Uses a process group so a timeout reliably kills children spawned by run.sh
(JVM, subshells). The returned dict is shallow-merged into the scorer's
top-level JSON record.

If the agent didn't make `run.sh` executable, the scorer self-heals
that bit before invoking — but records `script_was_executable=false`
so the manifest still distinguishes "agent followed the contract" from
"scorer had to fix it after the fact".
"""

from __future__ import annotations

import os
import signal
import stat
import subprocess
import time
from pathlib import Path

_TAIL_BYTES = 4 * 1024


def _tail(b: bytes, limit: int = _TAIL_BYTES) -> str:
    if len(b) > limit:
        b = b[-limit:]
    return b.decode("utf-8", errors="replace")


def run(workspace: Path, timeout_s: int) -> dict:
    workspace = workspace.resolve()
    script = workspace / "run.sh"
    if not script.is_file():
        return {
            "exit_code": -1,
            "wall_time_seconds": 0.0,
            "stdout_tail": "",
            "stderr_tail": f"runner: {script} does not exist",
            "timed_out": False,
            "script_was_executable": False,
        }

    # Self-heal: agents sometimes write run.sh without the executable bit
    # (claude in particular reaches for Python helpers it never executes).
    # Force the bit before invoking so a one-shot run isn't gated on the
    # agent successfully calling chmod.
    script_was_executable = os.access(str(script), os.X_OK)
    if not script_was_executable:
        mode = script.stat().st_mode
        script.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    start = time.monotonic()
    proc = subprocess.Popen(
        ["./run.sh"],
        cwd=str(workspace),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid,
    )
    timed_out = False
    try:
        stdout_b, stderr_b = proc.communicate(timeout=timeout_s)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        stdout_b, stderr_b = proc.communicate()
        exit_code = -1
    finally:
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
    wall = time.monotonic() - start

    return {
        "exit_code": exit_code,
        "wall_time_seconds": round(wall, 3),
        "stdout_tail": _tail(stdout_b),
        "stderr_tail": _tail(stderr_b),
        "timed_out": timed_out,
        "script_was_executable": script_was_executable,
    }
