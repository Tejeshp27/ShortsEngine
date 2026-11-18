# =============================================================
# Install the independent clip runtime and private dashboard as Mac
# login services. launchd restarts them after crashes and next login.
# Plists contain paths and safe flags only; secrets stay in local .env.
# =============================================================

import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
LABELS = {"clips": "com.shortsengine.clips", "dashboard": "com.shortsengine.dashboard"}


def definitions(root=ROOT):
    """
    Build two launchd job dictionaries from a project root; return labels and safe process settings.

    Args:
        root: The installed project directory containing .venv, src and .env.

    Returns:
        A list of property-list dictionaries. No API keys or tokens are embedded.
    """
    root = Path(root).resolve()
    jobs = []
    for kind, label in LABELS.items():
        module = "src.clips.runtime" if kind == "clips" else "src.dashboard.app"
        environment = {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                       "PYTHONUNBUFFERED": "1", "DASHBOARD_HOST": "127.0.0.1"}
        if kind == "dashboard":
            # The runtime owns scheduling; the dashboard keeps account/review pages and analytics.
            environment.update({"DASHBOARD_AUTO_CLIPS": "false", "DASHBOARD_AUTO_PUBLISH": "false"})
        jobs.append({"Label": label, "ProgramArguments": [str(root / ".venv/bin/python"), "-m", module],
                     "WorkingDirectory": str(root), "EnvironmentVariables": environment,
                     "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 30, "ExitTimeOut": 30,
                     "StandardOutPath": str(root / f"output/runtime/{kind}.stdout.log"),
                     "StandardErrorPath": str(root / f"output/runtime/{kind}.stderr.log")})
    return jobs


def install():
    """Write and load this user's two launchd agents; return nothing, without changing channel publishing settings."""
    if sys.platform != "darwin":
        raise SystemExit("launchd installation requires macOS; use Docker Compose on a Linux host.")
    folder = Path.home() / "Library/LaunchAgents"
    folder.mkdir(parents=True, exist_ok=True)
    (ROOT / "output/runtime").mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    for job in definitions():
        path = folder / (job["Label"] + ".plist")
        path.write_bytes(plistlib.dumps(job))
        path.chmod(0o600)
        subprocess.run(["plutil", "-lint", str(path)], check=True)
        # bootout can report "not loaded" on first installation; bootstrap must succeed.
        subprocess.run(["launchctl", "bootout", domain + "/" + job["Label"]], capture_output=True)
        subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True)
        print("Installed", job["Label"])


def main():
    """Install the services or print their launchd status from CLI options; return nothing."""
    parser = argparse.ArgumentParser(description="Mac login services for ShortsEngine")
    parser.add_argument("command", choices=["install", "status"])
    args = parser.parse_args()
    if args.command == "install":
        install()
    else:
        for label in LABELS.values():
            subprocess.run(["launchctl", "list", label], check=False)


if __name__ == "__main__":
    main()
