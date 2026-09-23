#!/usr/bin/env python3
"""Claude attribution never leaves through a publishing command.

Drives ``BashValidator.validate`` end to end, so each case proves the property
the user cares about -- what would actually be published -- rather than a
helper's return value. A command passes only if the text it would publish,
after the footer stripper's rewrite, carries no attribution.
"""

import re
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.tools.bash_validator import BashValidator  # noqa: E402

MARKER = "[CLAUDE_ATTRIBUTION]"

FOOTERS = {
    "generated_with": "🤖 Generated with [Claude Code](https://claude.com/claude-code)",
    "co_authored_by": "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>",
    "session_link": "https://claude.ai/code/session_01EQ9Y5iWs84xD49mVVqTTp3",
}

ATTRIBUTION = re.compile(
    r"Generated with\s+\[?Claude Code|Co-Authored-By:\s*Claude|claude\.ai/code/session",
    re.IGNORECASE,
)

FILE_COMMANDS = [
    "gh pr create --title t --body-file {f}",
    "gh pr edit 12 -F {f}",
    "gh pr comment 12 --body-file={f}",
    "gh pr review 12 --comment -F {f}",
    "gh issue create --title t --body-file {f}",
    "gh issue edit 3 --body-file {f}",
    "gh issue comment 3 -F {f}",
    "gh release create v1.0.0 --notes-file {f}",
    "gh release edit v1.0.0 -F {f}",
    "ghx -C /home/jorge/ws/me/gaia pr create --title t --body-file {f}",
    "ghx issue comment 7 --body-file {f}",
    "gh api repos/o/r/issues/1/comments -F body=@{f}",
    "git commit -F {f}",
    "git commit --file={f}",
]

INLINE_COMMANDS = [
    # The body IS the footer: no preceding newline for the stripper to anchor on.
    'gh pr create --title t --body "' + FOOTERS["generated_with"] + '"',
    'ghx pr edit 4 --body "' + FOOTERS["co_authored_by"] + '"',
    # A session link on its own line is detected but never stripped.
    'gh pr comment 5 --body "Done.\n\n' + FOOTERS["session_link"] + '"',
    'gh issue comment 5 --body "Fixed.\n\n' + FOOTERS["generated_with"] + '"',
    'gh issue create --title t --body "Bug.\n\n' + FOOTERS["co_authored_by"] + '"',
    'gh release create v2 --notes "Notes.\n\n' + FOOTERS["co_authored_by"] + '"',
    'gh release edit v2 --notes "' + FOOTERS["session_link"] + '"',
    "gh pr create --title t --body-file - <<'EOF'\nSummary of the change.\n\n"
    + FOOTERS["generated_with"] + "\nEOF",
    'gh pr review 9 --approve --body "LGTM\n\n' + FOOTERS["session_link"] + '"',
    'git commit -m "fix: thing\n\n' + FOOTERS["session_link"] + '"',
]


@pytest.fixture
def validator():
    return BashValidator()


def _published_text_is_clean(result, command):
    """True when the command is refused, or what it would run carries no attribution."""
    if MARKER in (result.reason or ""):
        return True
    effective = (result.modified_input or {}).get("command", command)
    return ATTRIBUTION.search(effective) is None


class TestBodyFilesAreScanned:
    @pytest.mark.parametrize("template", FILE_COMMANDS)
    @pytest.mark.parametrize("footer", sorted(FOOTERS))
    def test_attribution_in_body_file_is_refused(self, validator, tmp_path, template, footer):
        body = tmp_path / "body.md"
        body.write_text(f"## Summary\n\nReal description.\n\n{FOOTERS[footer]}\n", encoding="utf-8")

        result = validator.validate(template.format(f=body))

        assert result.allowed is False
        assert MARKER in result.reason
        assert "line 5" in result.reason
        assert str(body) in result.reason

    def test_relative_body_file_resolves_after_cd(self, validator, tmp_path):
        (tmp_path / "body.md").write_text(FOOTERS["generated_with"] + "\n", encoding="utf-8")

        result = validator.validate(f"cd {tmp_path} && gh pr create --title t --body-file body.md")

        assert MARKER in (result.reason or "")

    @pytest.mark.parametrize("template", FILE_COMMANDS)
    def test_clean_body_file_is_not_refused(self, validator, tmp_path, template):
        body = tmp_path / "body.md"
        body.write_text(
            "## Summary\n\nStrip the footer that says Generated with Claude Code "
            "from published bodies.\n",
            encoding="utf-8",
        )

        result = validator.validate(template.format(f=body))

        assert MARKER not in (result.reason or "")

    def test_clean_commit_file_still_allowed(self, validator, tmp_path):
        body = tmp_path / "msg.txt"
        body.write_text("fix(hooks): tighten guard\n", encoding="utf-8")

        result = validator.validate(f"git commit -F {body}")

        assert result.allowed is True


class TestInlineTextNeverPublishesAttribution:
    @pytest.mark.parametrize("command", INLINE_COMMANDS)
    def test_inline_attribution_is_refused_or_stripped(self, validator, command):
        result = validator.validate(command)

        assert _published_text_is_clean(result, command)

    def test_clean_inline_body_is_untouched(self, validator):
        command = 'gh pr comment 5 --body "Rebased on main; tests green."'

        result = validator.validate(command)

        assert MARKER not in (result.reason or "")
        assert result.modified_input is None


class TestCommitFooterStripStillWorks:
    def test_co_authored_by_trailer_is_stripped_and_commit_allowed(self, validator):
        command = 'git commit -m "feat: add x\n\n' + FOOTERS["co_authored_by"] + '"'

        result = validator.validate(command)

        assert result.allowed is True
        assert "feat: add x" in result.modified_input["command"]
        assert ATTRIBUTION.search(result.modified_input["command"]) is None

    def test_generated_with_footer_is_stripped_and_commit_allowed(self, validator):
        command = 'git commit -m "fix: y\n\n' + FOOTERS["generated_with"] + '"'

        result = validator.validate(command)

        assert result.allowed is True
        assert ATTRIBUTION.search(result.modified_input["command"]) is None
