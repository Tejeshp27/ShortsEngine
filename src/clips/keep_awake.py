# =============================================================
# src/clips/keep_awake.py — stop the Mac sleeping while work is running
#
# A sleeping Mac cannot record a live stream or finish turning a recorded
# hour into clips. macOS has a built-in tool, `caffeinate`, that holds the
# Mac awake while a given process is alive (`-w <pid>`). We start one per
# live recording and per clip-processing job; it ends by itself the moment
# that process ends, so the Mac sleeps normally whenever nothing is running.
# No system setting is changed. On Linux (later server) the tool does not
# exist and this does nothing.
#
# Flags: -i = no idle sleep, -s = no system sleep while on power,
# -w PID = stop when that process exits. Closing the laptop lid still
# sleeps the Mac (unless it is plugged in with an external display).
# =============================================================

import os
import shutil
import subprocess


def keep_awake_while_running(pid=None):
    """
    Keep the Mac awake for as long as a process runs.

    Args:
        pid (int or None): The process to follow (None = this process).

    Returns:
        subprocess.Popen or None: The caffeinate helper, or None when it is not
        available (not macOS) or could not start. It exits on its own.

    Example:
        keep_awake_while_running()   # inside a recording: awake until it finishes
    """
    program = shutil.which("caffeinate")
    if program is None:
        return None
    if pid is None:
        pid = os.getpid()
    try:
        return subprocess.Popen([program, "-i", "-s", "-w", str(pid)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
    except OSError:
        return None
