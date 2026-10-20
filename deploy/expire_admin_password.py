#!/usr/bin/env python3
"""Deletes core-api's initial-admin-password.txt once it is 5 days old.

Run from the host, in the directory holding docker-compose.yml. Cron runs it
daily. Once the file is gone (deleted here or by hand), the script removes its
own crontab entry.

    python3 deploy/expire_admin_password.py install    # add the daily cron entry
    python3 deploy/expire_admin_password.py            # what cron runs

The file's contents are never read or printed, only its age.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

MAX_AGE_DAYS = 5
SERVICE = "core-api"
PASSWORD_FILE = "/data/initial-admin-password.txt"
CRON_MARKER = "# wattwise-expire-admin-password"
CRON_SCHEDULE = "17 3 * * *"

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = pathlib.Path(__file__).resolve()

# Runs inside the container. Prints "missing", or "age <seconds>" or "removed".
_INSPECT = """
import os, sys, time
path, max_age = sys.argv[1], float(sys.argv[2])
try:
    age = time.time() - os.stat(path).st_mtime
except FileNotFoundError:
    print("missing"); sys.exit()
if age >= max_age:
    os.remove(path); print("removed")
else:
    print("age", int(age))
"""


def inspect_file() -> str:
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", SERVICE, "python3", "-c", _INSPECT,
         PASSWORD_FILE, str(MAX_AGE_DAYS * 86400)],
        cwd=REPO_DIR, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def read_crontab() -> list[str]:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    # `crontab -l` exits 1 with "no crontab for <user>" when empty.
    return result.stdout.splitlines() if result.returncode == 0 else []


def write_crontab(lines: list[str]) -> None:
    text = "\n".join(lines) + "\n" if lines else ""
    subprocess.run(["crontab", "-"], input=text, text=True, check=True)


def install_cron() -> None:
    lines = [ln for ln in read_crontab() if CRON_MARKER not in ln]
    lines.append(f"{CRON_SCHEDULE} {sys.executable} {SCRIPT} >> /tmp/wattwise-expire-admin-password.log 2>&1 {CRON_MARKER}")
    write_crontab(lines)
    print(f"Installed daily cron entry ({CRON_SCHEDULE}).")


def remove_cron() -> None:
    lines = read_crontab()
    kept = [ln for ln in lines if CRON_MARKER not in ln]
    if len(kept) != len(lines):
        write_crontab(kept)
        print("Removed own cron entry.")


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "install":
        install_cron()
        return 0

    status = inspect_file()
    if status.startswith("age"):
        print(f"{PASSWORD_FILE} is {int(status.split()[1]) // 86400} days old; keeping it.")
        return 0
    print(f"{PASSWORD_FILE}: {status}.")
    remove_cron()
    return 0


if __name__ == "__main__":
    sys.exit(main())
