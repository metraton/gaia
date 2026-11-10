"""The temp-space check warns only on a small or nearly full volume, naming the platform remedy."""

import sys

import pytest

from gaia import temp_volume

GIB = 1024 ** 3


def _volume(total, free, in_memory):
    return {"role": "host spool", "path": "/tmp", "total": total, "free": free,
            "fstype": "tmpfs" if in_memory else "ext4", "in_memory": in_memory}


@pytest.mark.parametrize("platform", sorted(temp_volume.REMEDIES))
def test_a_small_in_memory_spool_warns_with_the_platform_remedy(monkeypatch, platform):
    monkeypatch.setattr(temp_volume, "volumes", lambda: [_volume(2 * GIB, int(1.9 * GIB), True)])
    monkeypatch.setattr(temp_volume, "platform_name", lambda: platform)

    found = temp_volume.health()
    line = temp_volume.notice_line()

    assert found["severity"] == "warning"
    assert found["fix"] == temp_volume.REMEDIES[platform]
    assert "only 2.0 GiB, in memory" in line and temp_volume.REMEDIES[platform] in line
    assert "\n" not in line


def test_a_nearly_full_disk_warns_and_a_roomy_one_is_silent(monkeypatch):
    monkeypatch.setattr(temp_volume, "volumes", lambda: [_volume(100 * GIB, int(0.5 * GIB), False)])
    assert "0.5 GiB free" in temp_volume.notice_line()

    monkeypatch.setattr(temp_volume, "volumes", lambda: [_volume(100 * GIB, 60 * GIB, False)])
    assert temp_volume.notice_line() == ""
    assert temp_volume.health()["severity"] == "pass"


@pytest.mark.skipif(sys.platform != "linux", reason="the dispatched-shell fallback is the POSIX /tmp")
def test_a_dispatched_shells_tmpdir_is_not_the_host_spool(monkeypatch):
    from gaia.paths import dispatch_tmp_dir

    monkeypatch.delenv("CLAUDE_CODE_TMPDIR", raising=False)
    monkeypatch.setenv("TMPDIR", str(dispatch_tmp_dir("some-agent")))
    assert str(temp_volume.host_spool_dir()) == "/tmp"

    monkeypatch.setenv("CLAUDE_CODE_TMPDIR", "/var/spool/claude")
    assert str(temp_volume.host_spool_dir()) == "/var/spool/claude"
