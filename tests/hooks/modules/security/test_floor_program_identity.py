"""The permanent floor judges the program that runs, never the spelling of its name."""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.blocked_commands import is_blocked_command  # noqa: E402

WIPE = "if=/dev/zero of=/dev/sda"

FLOORED_SPELLINGS = [
    "newtool rm -rf /",
    "rm -rf /",
    f"/opt/bin/dd {WIPE}",
    f"./dd {WIPE}",
    f"busybox dd {WIPE}",
    f"/bin/busybox dd {WIPE}",
    f"env dd {WIPE}",
    f"/usr/bin/env /opt/bin/dd {WIPE}",
    f"sudo /usr/local/bin/dd {WIPE}",
    f"echo $(/opt/bin/dd {WIPE})",
    f"find /dev -name sda -exec /opt/bin/dd {WIPE} \\;",
    "/sbin/mkfs /dev/sda1",
    "/usr/sbin/mkfs.ext4 /dev/sda1",
    "/usr/sbin/mkfs.xfs -f /dev/sda1",
    "busybox mkfs.vfat /dev/sdb1",
    "/sbin/fdisk /dev/sda",
    "C:/Windows/System32/diskpart.exe /s wipe.txt",
]


@pytest.mark.parametrize("command", FLOORED_SPELLINGS)
def test_floored_program_is_refused_in_every_spelling(command):
    assert is_blocked_command(command).is_blocked, command


@pytest.mark.parametrize("command", [
    "/usr/bin/git log --format=%H",
    "/opt/bin/ddrescue /dev/sda out.img",
    "grep -rn 'dd if=/dev/zero of=/dev/sda' /var/log",
])
def test_identity_reading_does_not_floor_other_programs(command):
    assert not is_blocked_command(command).is_blocked, command
