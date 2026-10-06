"""`gaia release publish`/`check` as one signable command from a Gaia worktree.

Three properties: the gh program is configurable and reaches only the
release's own gh calls; the rc push lands on the origin branch the release
was cut from whatever the local branch is called; and every release command
line the gaia-release skill documents passes the approval request-set
validator, so a retired form such as a token substitution cannot be taught.
Git runs for real against a local bare origin; gh is a fake program, so no
network is touched.
"""

import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cli import release  # noqa: E402
from gaia.approvals.command_set import CommandSetValidationError, validate_request_set  # noqa: E402

_FAKE_GH = textwrap.dedent(
    """\
    #!{python}
    import json, os, sys
    with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd()}}) + "\\n")
    mode = os.environ.get("FAKE_GH_MODE", "push")
    if mode == "unmapped":
        sys.stderr.write("ghx: owner 'someone' (from 'git@github.com:someone/x.git') maps to no account\\n")
        sys.exit(2)
    args = sys.argv[1:]
    if args[:2] == ["api", "user"]:
        print("metraton")
    elif args[:2] == ["api", "repos/metraton/gaia"]:
        print("true" if mode == "push" else "false")
    elif args[:2] == ["release", "create"]:
        print("https://github.com/metraton/gaia/releases/tag/" + args[2])
    else:
        sys.exit(1)
    """
)


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    program = tmp_path / "fake-gh"
    program.write_text(_FAKE_GH.format(python=sys.executable), encoding="utf-8")
    program.chmod(0o755)
    log = tmp_path / "fake-gh.log"
    monkeypatch.setenv("FAKE_GH_LOG", str(log))
    monkeypatch.delenv("GH_TOKEN", raising=False)

    def calls():
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    return str(program), calls


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def worktree_clone(tmp_path):
    """A clone on `fix/task` (no upstream) cut from origin's `feat/accumulating`."""
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    clone = tmp_path / "clone"
    _git("init", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    _git("init", "-b", "main", str(seed), cwd=tmp_path)
    for key, value in (("user.email", "t@example.com"), ("user.name", "t")):
        _git("config", key, value, cwd=seed)
    (seed / "f.txt").write_text("1\n", encoding="utf-8")
    _git("add", "f.txt", cwd=seed)
    _git("commit", "-m", "init", cwd=seed)
    _git("remote", "add", "origin", str(origin), cwd=seed)
    _git("push", "origin", "main", cwd=seed)
    (seed / "f.txt").write_text("accumulated\n", encoding="utf-8")
    _git("commit", "-am", "accumulated", cwd=seed)
    _git("push", "origin", "HEAD:refs/heads/feat/accumulating", cwd=seed)
    _git("clone", str(origin), str(clone), cwd=tmp_path)
    for key, value in (("user.email", "t@example.com"), ("user.name", "t")):
        _git("config", key, value, cwd=clone)
    _git("checkout", "--no-track", "-b", "fix/task", "origin/feat/accumulating", cwd=clone)
    return origin, seed, clone


# ---------------------------------------------------------------------------
# The gh program: configurable, used only by the release's own gh calls
# ---------------------------------------------------------------------------

def test_push_permission_runs_the_configured_program_in_the_repo(fake_gh, tmp_path):
    program, calls = fake_gh
    assert release._check_gh_push_permission(tmp_path, gh=program) is None
    [call] = calls()
    assert call["argv"][:2] == ["api", "repos/metraton/gaia"]
    assert call["cwd"] == str(tmp_path)


def test_unmapped_account_fails_naming_the_gh_option(fake_gh, tmp_path, monkeypatch):
    program, _ = fake_gh
    monkeypatch.setenv("FAKE_GH_MODE", "unmapped")
    err = release._check_gh_push_permission(tmp_path, gh=program)
    assert err is not None
    assert "maps to no account" in err
    assert "--gh" in err
    assert "$(" not in err


def test_account_without_push_fails_naming_the_gh_option(fake_gh, tmp_path, monkeypatch):
    program, _ = fake_gh
    monkeypatch.setenv("FAKE_GH_MODE", "nopush")
    err = release._check_gh_push_permission(tmp_path, gh=program)
    assert err is not None
    assert "push access" in err
    assert "--gh" in err
    assert "$(" not in err


def test_missing_gh_program_fails_naming_the_gh_option(tmp_path):
    err = release._check_gh_push_permission(tmp_path, gh=str(tmp_path / "no-such-ghx"))
    assert err is not None
    assert "--gh" in err


def _publish_spy(program, sha):
    """`_run` stand-in: the gh program runs for real, every other command is answered."""
    real_run = release._run
    seen = []

    def spy(cmd, **kwargs):
        seen.append((cmd, kwargs))
        if cmd[0] == program:
            return real_run(cmd, **kwargs)
        if cmd[:2] == ["git", "ls-remote"] and "--tags" in cmd:
            return 0, "", ""
        if cmd[:2] == ["git", "ls-remote"]:
            return 0, f"{sha}\trefs/heads/feat/accumulating\n", ""
        if cmd[:3] == ["git", "rev-parse", "HEAD"]:
            return 0, sha + "\n", ""
        if cmd[:2] == ["git", "rev-parse"]:
            return 128, "", "fatal: no upstream configured"
        if cmd[:2] == ["git", "status"]:
            return 0, "", ""
        if cmd[0] == sys.executable:
            return 1, "No reusable CI verdict. The suite runs.\n", ""
        return 0, "ok", ""

    return spy, seen


def test_gh_program_reaches_only_the_release_gh_calls(fake_gh):
    program, calls = fake_gh
    spy, seen = _publish_spy(program, "a" * 40)
    suite = {"name": "npm test", "status": "PASS", "detail": "ok", "duration_ms": 1}
    with patch("cli.release._run", side_effect=spy), \
         patch("cli.release._xdist_available", return_value=True), \
         patch("cli.release.gate_npm_test", return_value=suite):
        results = release.run_release_publish(_REPO_ROOT, "9.9.9-rc.1", gh=program)

    assert [r["status"] for r in results] == ["PASS"] * 6, results
    assert [c["argv"][:2] for c in calls()] == [
        ["api", "repos/metraton/gaia"],
        ["release", "create"],
    ]
    helper = [cmd for cmd, _ in seen if cmd[0] == sys.executable]
    assert helper and helper[0][helper[0].index("--gh") + 1] == program
    others = [cmd for cmd, _ in seen if cmd[0] not in (program, sys.executable)]
    assert others and all(program not in cmd for cmd in others)
    assert all("env" not in kwargs for _, kwargs in seen)


def test_failing_account_stops_before_any_step(fake_gh, monkeypatch):
    program, calls = fake_gh
    monkeypatch.setenv("FAKE_GH_MODE", "unmapped")
    spy, seen = _publish_spy(program, "a" * 40)
    with patch("cli.release._run", side_effect=spy), \
         patch("cli.release._xdist_available", return_value=True):
        results = release.run_release_publish(_REPO_ROOT, "9.9.9-rc.1", gh=program)

    assert [r["name"] for r in results] == ["preconditions"]
    assert results[0]["status"] == "FAIL"
    assert "--gh" in results[0]["detail"]
    assert [c["argv"][:2] for c in calls()] == [["api", "repos/metraton/gaia"]]
    assert not any(cmd[:2] == ["git", "push"] for cmd, _ in seen)


def test_ci_verdict_helper_uses_the_configured_program(fake_gh):
    program, calls = fake_gh
    sys.path.insert(0, str(_REPO_ROOT / ".github" / "scripts"))
    try:
        import ci_verdict
    finally:
        sys.path.pop(0)
    status = ci_verdict.main(["a" * 40, "--repo", "metraton/gaia", "--gh", program])
    assert status == 2
    assert calls()[0]["argv"][:2] == ["api", f"repos/metraton/gaia/commits/{'a' * 40}"]


# ---------------------------------------------------------------------------
# Push target: the origin branch, not the local branch name
# ---------------------------------------------------------------------------

def test_push_target_is_the_origin_branch_at_head(worktree_clone):
    _, _, clone = worktree_clone
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=clone) == "fix/task"
    branch, err = release.resolve_push_branch(clone)
    assert (branch, err) == ("feat/accumulating", None)


def test_rc_push_fast_forwards_the_origin_branch_and_pushes_the_tag(worktree_clone):
    origin, _, clone = worktree_clone
    (clone / "f.txt").write_text("2\n", encoding="utf-8")
    _git("commit", "-am", "chore(release): v1.0.0-rc.1", cwd=clone)
    _git("tag", "-a", "v1.0.0-rc.1", "-m", "Release v1.0.0-rc.1", cwd=clone)
    head = _git("rev-parse", "HEAD", cwd=clone)

    assert release._check_push_fast_forward(clone, "feat/accumulating") is None
    result = release.step_git_push(clone, "1.0.0-rc.1", "feat/accumulating")

    assert result["status"] == "PASS", result
    assert _git("rev-parse", "refs/heads/feat/accumulating", cwd=origin) == head
    assert _git("rev-parse", "refs/tags/v1.0.0-rc.1^{commit}", cwd=origin) == head
    assert _git("rev-parse", "refs/heads/main", cwd=origin) != head
    assert _git("for-each-ref", "refs/heads/fix", cwd=origin) == ""


def test_diverged_origin_branch_is_refused_before_tagging(worktree_clone):
    _, seed, clone = worktree_clone
    (seed / "g.txt").write_text("x\n", encoding="utf-8")
    _git("add", "g.txt", cwd=seed)
    _git("commit", "-m", "elsewhere", cwd=seed)
    _git("push", "origin", "HEAD:refs/heads/feat/accumulating", cwd=seed)
    (clone / "f.txt").write_text("2\n", encoding="utf-8")
    _git("commit", "-am", "local", cwd=clone)

    err = release._check_push_fast_forward(clone, "feat/accumulating")
    assert err is not None
    assert "fast-forward" in err
    assert "merge" in err


def test_unresolvable_push_target_names_the_branch_option(worktree_clone):
    _, _, clone = worktree_clone
    (clone / "f.txt").write_text("2\n", encoding="utf-8")
    _git("commit", "-am", "local", cwd=clone)
    branch, err = release.resolve_push_branch(clone)
    assert branch is None
    assert "--branch" in err


def test_stable_requires_the_push_target_to_be_main():
    assert release._check_stable_from_main("5.5.0", "main") is None
    assert release._check_stable_from_main("5.5.0-rc.4", "feat/accumulating") is None
    err = release._check_stable_from_main("5.5.0", "feat/accumulating")
    assert err is not None and "main" in err and "feat/accumulating" in err


# ---------------------------------------------------------------------------
# The documented command is one signable program
# ---------------------------------------------------------------------------

_SKILL_DIR = _REPO_ROOT / "skills" / "gaia-release"
_RELEASE_COMMAND_LINE = re.compile(
    r'^(?:\w+=(?:"[^"]*"|\S+) +)*(?:python3 \S+/bin/)?gaia release (?:publish|check)\b[^\n#`]*', re.M
)


def _release_commands_in(text):
    found = _RELEASE_COMMAND_LINE.findall(text)
    return [cmd.strip().replace("<version>", "5.5.0-rc.4").replace("<worktree>", str(_REPO_ROOT)) for cmd in found]


def _documented_release_commands():
    names = ("SKILL.md", "reference.md")
    return [cmd for name in names for cmd in _release_commands_in((_SKILL_DIR / name).read_text(encoding="utf-8"))]


def test_every_documented_release_command_passes_validate_request_set():
    commands = _documented_release_commands()
    publish = [cmd for cmd in commands if "gaia release publish" in cmd]
    assert any("--gh" in cmd.split() for cmd in publish), "no documented `gaia release publish ... --gh` line"
    for command in commands:
        # A dry-run mutates nothing, so it must clear every atomicity check and fail only for lacking a T3 command.
        if "--dry-run" in command.split():
            with pytest.raises(CommandSetValidationError, match="at least one command classified T3"):
                validate_request_set([command])
            continue
        [item] = validate_request_set([command])
        assert item["signed"] is True, command


def test_the_retired_substitution_form_is_extracted_and_refused():
    [retired] = _release_commands_in('GH_TOKEN="$(gh auth token --user metraton)" gaia release publish <version>\n')
    with pytest.raises(CommandSetValidationError, match="not a chain"):
        validate_request_set([retired])


def test_release_cli_does_not_teach_the_substitution_form():
    assert "$(gh auth token" not in (_BIN_DIR / "cli" / "release.py").read_text(encoding="utf-8")
