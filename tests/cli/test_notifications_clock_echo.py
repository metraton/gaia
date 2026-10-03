"""Every reminder read and write shows the machine's clock, and an --at without a date is refused."""

from __future__ import annotations

import json
import re

import pytest

from tests.test_notifications_due import gaia_env, gaia_notifications  # noqa: F401

CLOCK_LINE = "now: Thu 2026-10-01 10:00 -03:00 (America/Santiago)"


def _clock_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("now: ")]


def _add_reminder(*extra: str) -> tuple[int, str, str]:
    return gaia_notifications(
        "add", "--kind", "reminder", "--at", "2026-10-01T16:00", "--headline", "Review X", *extra
    )


def test_add_snooze_list_and_show_each_print_the_clock_once(gaia_env):
    gaia_env.at(2026, 10, 1, 10, 0)

    rc, out, err = _add_reminder()
    assert rc == 0, out + err
    assert _clock_lines(out) == [CLOCK_LINE], out
    reminder = int(re.search(r"#(\d+)", out).group(1))

    for argv in (
        ("snooze", str(reminder), "--for", "2h"),
        ("list", "--upcoming"),
        ("show", str(reminder)),
    ):
        rc, out, err = gaia_notifications(*argv)
        assert rc == 0, out + err
        assert _clock_lines(out) == [CLOCK_LINE], (argv, out)


@pytest.mark.parametrize("value", ["30m", "16:00"])
def test_an_at_without_a_date_is_refused_with_the_clock_and_gaia_now(gaia_env, value):
    gaia_env.at(2026, 10, 1, 10, 0)

    rc, out, err = gaia_notifications(
        "add", "--kind", "reminder", "--at", value, "--headline", "X"
    )

    assert rc != 0
    assert "needs a date" in err, err
    assert "Thu 2026-10-01 10:00 -03:00 (America/Santiago)" in err, err
    assert "gaia now" in err, err
    assert "--in" not in err, err


def test_in_is_not_a_reminder_flag(gaia_env, capsys):
    with pytest.raises(SystemExit) as exc:
        gaia_notifications("add", "--kind", "reminder", "--in", "30m", "--headline", "X")

    assert exc.value.code == 2
    assert "unrecognized arguments: --in" in capsys.readouterr().err


def test_list_and_show_json_carry_due_local_and_now_local_next_to_due_at(gaia_env):
    gaia_env.at(2026, 10, 1, 10, 0)
    rc, out, err = _add_reminder("--json")
    assert rc == 0, out + err
    reminder = json.loads(out)["id"]

    rc, out, err = gaia_notifications("list", "--upcoming", "--json")
    assert rc == 0, out + err
    listed = next(row for row in json.loads(out) if row["id"] == reminder)
    rc, out, err = gaia_notifications("show", str(reminder), "--json")
    assert rc == 0, out + err
    shown = json.loads(out)

    for row in (listed, shown):
        assert row["due_at"] == "2026-10-01T19:00:00Z"
        assert row["due_local"].startswith("Thu 2026-10-01 16:00"), row
        assert row["now_local"].startswith("Thu 2026-10-01 10:00"), row
