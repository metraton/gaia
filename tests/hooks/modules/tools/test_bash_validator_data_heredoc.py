"""A quoted heredoc handed to a Gaia CLI content flag is data, not commands.

Reproduces the refusal the gaia-planner skill hit when saving a plan with
``gaia plan save --content-file=- <<'PLAN'``: the compound validator split the
plan's prose on newlines and denied it as "Compound T3 execution is disabled".
The security half pins that the exemption stays narrow: a heredoc feeding an
interpreter, an unquoted heredoc, or trailing commands after the terminator are
still analysed exactly as before.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

REAL_PLAN_BODY = (Path(__file__).parent / "fixtures" / "real_plan_body.md").read_text()

METACHAR_BODY = (
    "Step 1; then step 2 | tee out.txt > log && echo done || true\n"
    "Run $(rm -rf /tmp/gaia-heredoc-probe) and `git push origin main` later.\n"
    "Quotes: it's \"fine\" -- git reset --hard and terraform apply are prose.\n"
)


def _validate(command):
    from modules.tools.bash_validator import BashValidator

    return BashValidator().validate(
        command, is_subagent=True, session_id="t", agent_type="gaia-planner",
    )


def _heredoc(header, delimiter_spelling, delimiter, body):
    return f"{header} <<{delimiter_spelling}\n{body.rstrip(chr(10))}\n{delimiter}"


class TestQuotedHeredocToGaiaContentFlagIsData:
    def test_real_plan_body_passes(self):
        command = _heredoc(
            "gaia plan save --brief=aprobaciones-agnosticas-al-host --content-file=-",
            "'PLAN'", "PLAN", REAL_PLAN_BODY,
        )
        result = _validate(command)
        assert result.allowed is True, result.reason
        assert result.tier.value != "T3"

    @pytest.mark.parametrize("spelling", ["'PLAN'", '"PLAN"'])
    @pytest.mark.parametrize("flag", ["--content-file=-", "--content-file -"])
    def test_shell_metacharacters_in_body_pass(self, spelling, flag):
        command = _heredoc(
            f"gaia plan save --brief=b {flag}", spelling, "PLAN", METACHAR_BODY,
        )
        result = _validate(command)
        assert result.allowed is True, result.reason
        assert result.modified_input is None


class TestHeredocFeedingAnInterpreterIsStillCommands:
    @pytest.mark.parametrize("header", ["bash", "sh", "bash -s"])
    def test_rm_rf_fed_to_a_shell_is_not_allowed(self, header):
        command = _heredoc(header, "'X'", "X", "rm -rf /tmp/gaia-heredoc-probe\n")
        assert _validate(command).allowed is False

    def test_quoted_heredoc_piped_into_bash_is_not_allowed(self):
        command = "cat <<'X' | bash\nrm -rf /tmp/gaia-heredoc-probe\nX"
        assert _validate(command).allowed is False

    def test_permanent_deny_floor_still_reads_a_data_body(self):
        command = _heredoc(
            "gaia plan save --brief=b --content-file=-", "'PLAN'", "PLAN",
            "kubectl delete namespace prod\n",
        )
        assert _validate(command).allowed is False

    def test_command_after_the_terminator_is_still_analysed(self):
        command = (
            "gaia plan save --brief=b --content-file=- <<'PLAN'\nprose\nPLAN\n"
            "rm -rf /tmp/gaia-heredoc-probe"
        )
        assert _validate(command).allowed is False


class TestUnquotedHeredocGainsNoExemption:
    def test_real_plan_body_unquoted_is_still_split(self):
        command = _heredoc(
            "gaia plan save --brief=aprobaciones-agnosticas-al-host --content-file=-",
            "PLAN", "PLAN", REAL_PLAN_BODY,
        )
        assert _validate(command).allowed is False

    def test_substitution_in_unquoted_body_is_not_allowed(self):
        command = _heredoc(
            "gaia plan save --brief=b --content-file=-", "PLAN", "PLAN",
            "Approach: $(rm -rf /tmp/gaia-heredoc-probe)\n",
        )
        assert _validate(command).allowed is False


class TestDataHeredocHeader:
    def _header(self, command):
        from modules.security.data_heredoc import data_heredoc_header

        return data_heredoc_header(command)

    def test_returns_the_command_without_heredoc(self):
        command = "gaia plan save --brief=b --content-file=- <<'PLAN'\na; b\nPLAN\n"
        assert self._header(command) == "gaia plan save --brief=b --content-file=-"

    @pytest.mark.parametrize("command", [
        "gaia plan save --brief=b --content-file=- <<PLAN\na\nPLAN",
        "bash <<'X'\nrm -rf /tmp/x\nX",
        "python3 - <<'X'\nprint(1)\nX",
        "cat > /tmp/out.md <<'X'\na\nX",
        "gh pr create --body-file - <<'X'\na\nX",
        "gaia plan save --brief=b <<'PLAN'\na\nPLAN",
        "gaia plan save --brief=$B --content-file=- <<'PLAN'\na\nPLAN",
        "gaia plan save --brief=b --content-file=- <<'PLAN' | bash\na\nPLAN",
        "gaia plan save --brief=b --content-file=- <<'PLAN'\na\nnever terminated",
        "gaia plan save --brief=b --content-file=- <<'PLAN'\na\nPLAN\necho more",
    ])
    def test_everything_else_is_not_exempt(self, command):
        assert self._header(command) is None
