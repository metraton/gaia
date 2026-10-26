"""Generated attack batch: mv, cp and tar -xf are free only when every written path stays in scratch or a managed worktree."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import mutative_verbs  # noqa: E402
from modules.security import tiers as tiers_module  # noqa: E402
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

FREE, ASK = "FREE", "ASK"


def _git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for args in (("init", "-q", "-b", "main"), ("commit", "-q", "--allow-empty", "-m", "init"), ("branch", "feature-x")):
        subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


def _archive(path: Path, members: list) -> None:
    with tarfile.open(path, "w") as archive:
        for name, link in members:
            info = tarfile.TarInfo(name)
            if link is None:
                info.size = 1
                archive.addfile(info, io.BytesIO(b"x"))
            else:
                info.type, info.linkname = tarfile.SYMTYPE, link
                archive.addfile(info)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("local-work")
    patch = pytest.MonkeyPatch()
    patch.setenv("GAIA_DATA_DIR", str(root / "data"))
    patch.setenv("GAIA_DB", str(root / "data" / "gaia.db"))
    patch.delenv("GAIA_CLI_ALIASES", raising=False)
    from gaia.paths import ensure_layout, scratch_dir, worktrees_dir

    ensure_layout()
    data = Path(os.path.realpath(root / "data"))
    scratch_root = Path(os.path.realpath(scratch_dir()))
    scratch = scratch_root / "alocalwork01.token"
    scratch.mkdir()
    managed = Path(os.path.realpath(worktrees_dir()))
    outside = root / "outside"
    outside.mkdir()
    (outside / "f.txt").write_text("x")
    (data / "contract_drafts").mkdir(exist_ok=True)
    worktree, repo = managed / "wt-a", root / "repo"
    _git_repo(worktree)
    _git_repo(repo)
    (worktree / "sub").mkdir()
    (worktree / "x.txt").write_text("x")
    (worktree / "link_out").symlink_to(outside)
    (scratch / "a.txt").write_text("a")
    (scratch / "real").mkdir()
    (scratch / "link_in").symlink_to(scratch / "real")
    (scratch / "link_out").symlink_to(outside)
    (scratch / "link_drafts").symlink_to(data / "contract_drafts")
    _archive(scratch / "clean.tar", [("pkg/file.txt", None)])
    _archive(scratch / "gitdir.tar", [(".git/hooks/post-checkout", None)])
    _archive(scratch / "dotgit.tar", [(".git", None)])
    _archive(scratch / "link.tar", [("l", str(outside)), ("l/x", None)])
    _archive(scratch / "strip.tar", [("top/.git/config", None)])
    (scratch / "withgit" / ".git" / "hooks").mkdir(parents=True)
    (scratch / "withgit" / ".git" / "hooks" / "post-checkout").write_text("x")
    (scratch / "nested" / "pkg" / ".git").mkdir(parents=True)
    other = scratch_root / "aotheragent02.token"
    (other / "real").mkdir(parents=True)
    (other / "x.txt").write_text("x")
    _archive(other / "clean.tar", [("pkg/file.txt", None)])
    yield {"S": str(scratch), "SR": str(scratch_root), "M": str(managed), "W": str(worktree), "R": str(repo),
           "OUT": str(outside), "DATA": str(data), "O": str(other)}
    patch.undo()


@pytest.fixture(autouse=True)
def _world_environment(world, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", world["DATA"])
    monkeypatch.setenv("GAIA_DB", os.path.join(world["DATA"], "gaia.db"))
    monkeypatch.delenv("GAIA_CLI_ALIASES", raising=False)
    monkeypatch.chdir(world["OUT"])


def observe(command: str, cwd: str, agent: str = "alocalwork01") -> str:
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()
    payload = {"cwd": cwd, "tool_use_id": "t-local-work", "session_id": "s-local-work",
               "agent_id": agent, "agent_type": "developer"}
    result = BashValidator().validate(command, is_subagent=True, session_id="s-local-work",
                                      agent_type="developer", hook_payload=payload)
    LAST_REASON[:] = [result.reason or ""]
    return FREE if result.allowed else ASK


LAST_REASON = [""]


DESTINATIONS = [
    ("scratch", "{S}/new.txt", FREE),
    ("scratch-traversal-in", "{S}/real/../new2.txt", FREE),
    ("scratch-traversal-out", "{S}/../escape.txt", ASK),
    ("scratch-link-in", "{S}/link_in/new.txt", FREE),
    ("scratch-link-out", "{S}/link_out/new.txt", ASK),
    ("scratch-link-drafts", "{S}/link_drafts/x.json", ASK),
    ("scratch-glob", "{S}/*.txt", ASK),
    ("scratch-var", "$GAIA_DATA_DIR/scratch/new.txt", ASK),
    ("scratch-shared-root", "{SR}/new.txt", ASK),
    ("scratch-other-entry", "{SR}/other.token/new.txt", ASK),
    ("home-brace-var", "${HOME}/new.txt", ASK),
    ("home-tilde", "~/new.txt", ASK),
    ("outside", "{OUT}/new.txt", ASK),
    ("worktree", "{W}/new.txt", FREE),
    ("worktree-sub", "{W}/sub/new.txt", FREE),
    ("worktree-git", "{W}/.git/new", ASK),
    ("worktree-git-hooks", "{W}/.git/hooks/pre-commit", ASK),
    ("worktree-git-traversal", "{W}/sub/../.git/config", ASK),
    ("worktree-link-out", "{W}/link_out/new.txt", ASK),
    ("worktree-escape", "{W}/../../escape.txt", ASK),
    ("managed-root-outside-worktree", "{M}/stray.txt", ASK),
    ("drafts", "{DATA}/contract_drafts/x.json", ASK),
    ("backups", "{DATA}/backups/x.db", ASK),
    ("live-db-wal", "{DATA}/gaia.db-wal", ASK),
]
WRITERS = ["cp {S}/a.txt {dest}", "cp -f {S}/a.txt {dest}", "mv {S}/a.txt {dest}", "mv -n {S}/a.txt {dest}",
           "cp -r {S}/real {dest}", "cp -a {S}/real {dest}"]

MOVED_SOURCES = [
    ("scratch", "{S}/a.txt", FREE),
    ("worktree", "{W}/x.txt", FREE),
    ("scratch-link-itself", "{S}/link_out", FREE),
    ("outside", "{OUT}/f.txt", ASK),
    ("worktree-git", "{W}/.git/config", ASK),
    ("worktree-top", "{W}", ASK),
    ("scratch-root", "{SR}", ASK),
    ("scratch-other-entry", "{SR}/other.token/f.txt", ASK),
    ("tilde", "~/f.txt", ASK),
]

TAR = [
    ("clean-into-scratch", "tar -xf {S}/clean.tar -C {S}/out", FREE),
    ("clean-into-worktree", "tar -xf {S}/clean.tar -C {W}", FREE),
    ("clean-into-worktree-sub", "tar -xvf {S}/clean.tar -C {W}/sub", FREE),
    ("clean-split-flags", "tar -x -f {S}/clean.tar -C {W}", FREE),
    ("clean-traditional", "tar xf {S}/clean.tar -C {W}", FREE),
    ("clean-long", "tar --extract --file={S}/clean.tar --directory={W}", FREE),
    ("clean-one-top-level", "tar -xf {S}/clean.tar -C {W} --one-top-level=pkg2", FREE),
    ("clean-to-stdout", "tar -xOf {S}/clean.tar pkg/file.txt", FREE),
    ("clean-into-git-dir", "tar -xf {S}/clean.tar -C {W}/.git", ASK),
    ("clean-into-outside", "tar -xf {S}/clean.tar -C {OUT}", ASK),
    ("clean-into-root", "tar -xf {S}/clean.tar -C /", ASK),
    ("clean-through-link", "tar -xf {S}/clean.tar -C {S}/link_out", ASK),
    ("clean-into-managed-root", "tar -xf {S}/clean.tar -C {M}", ASK),
    ("absolute-names", "tar -xPf {S}/clean.tar -C {W}", ASK),
    ("to-command", "tar -xf {S}/clean.tar --to-command=sh -C {W}", ASK),
    ("stdin-archive", "tar -xf - -C {W}", ASK),
    ("no-directory", "tar -xf {S}/clean.tar", ASK),
    ("missing-into-worktree", "tar -xf {S}/missing.tar -C {W}", ASK),
    ("missing-into-scratch", "tar -xf {S}/missing.tar -C {S}/out", FREE),
    ("missing-into-other-entry", "tar -xf {S}/missing.tar -C {SR}/other.token", ASK),
    ("clean-into-shared-root", "tar -xf {S}/clean.tar -C {SR}", ASK),
    ("clean-into-other-entry", "tar -xf {S}/clean.tar -C {SR}/other.token", ASK),
    ("gitdir-member", "tar -xf {S}/gitdir.tar -C {W}", ASK),
    ("dotgit-file-member", "tar -xf {S}/dotgit.tar -C {W}", ASK),
    ("symlink-member", "tar -xf {S}/link.tar -C {W}", ASK),
    ("strip-into-git", "tar -xf {S}/strip.tar -C {W} --strip-components=1", ASK),
    ("gitdir-member-into-scratch", "tar -xf {S}/gitdir.tar -C {S}/out", ASK),
    ("variable-directory", "tar -xf {S}/clean.tar -C $DEST", ASK),
]

CHAINS = [
    ("link-planted-then-cp", "ln -s {OUT} {S}/fresh && cp {S}/a.txt {S}/fresh/x", "{S}", ASK),
    ("link-moved-then-cp", "mv {S}/link_out {S}/d && cp {S}/a.txt {S}/d/x", "{S}", ASK),
    ("link-extracted-then-cp", "tar -xf {S}/link.tar -C {S}/out ; cp {S}/a.txt {S}/out/l/x", "{S}", ASK),
    ("checkout-then-cp", "git -C {W} checkout feature-x && cp {S}/a.txt {W}/sub/x", "{W}", ASK),
    ("background-link", "ln -s {OUT} {S}/bg & cp {S}/a.txt {S}/bg/x", "{S}", ASK),
    ("mkdir-then-cp", "mkdir -p {S}/d2 && cp {S}/a.txt {S}/d2/x", "{S}", FREE),
    ("cd-then-relative-cp", "cd {S} && cp a.txt b.txt", "{OUT}", FREE),
    ("cd-out-then-relative-cp", "cd {OUT} && cp f.txt g.txt", "{S}", ASK),
    ("relative-cp-in-scratch", "cp a.txt c.txt", "{S}", FREE),
    ("relative-cp-outside", "cp f.txt g.txt", "{OUT}", ASK),
    ("xargs-appends-destination", "printf '%s\\n' {OUT}/ | xargs mv {S}/a.txt {S}/b.txt", "{S}", ASK),
]

GIT = [
    ("merge-tree", "git merge-tree --write-tree HEAD feature-x", FREE),
    ("merge-tree-options", "git merge-tree --write-tree --name-only --messages -X ours HEAD feature-x", FREE),
    ("merge-tree-dash-c", "git -C {R} merge-tree --write-tree --name-only HEAD feature-x", FREE),
    ("grep", "git grep -n TODO", FREE),
    ("grep-no-pager", "git --no-pager grep -n TODO", FREE),
    ("grep-paginate-flag", "git -P grep -n TODO", FREE),
    ("grep-pathspec", "git grep -n TODO -- '*.py'", FREE),
    ("ls-tree", "git ls-tree -r HEAD", FREE),
    ("cat-file", "git cat-file -p HEAD", FREE),
    ("rev-list", "git rev-list --count HEAD", FREE),
    ("for-each-ref", "git for-each-ref refs/heads", FREE),
    ("show-ref", "git show-ref", FREE),
    ("describe", "git describe --always", FREE),
    ("name-rev", "git name-rev HEAD", FREE),
    ("merge-base", "git merge-base HEAD feature-x", FREE),
    ("diff-tree", "git diff-tree -r HEAD", FREE),
    ("ls-files", "git ls-files", FREE),
    ("count-objects", "git count-objects -v", FREE),
    ("rev-parse", "git rev-parse HEAD", FREE),
    ("cherry", "git cherry main feature-x", FREE),
    ("grep-open-in-pager", "git grep -O TODO", ASK),
    ("grep-open-in-named-pager", "git grep --open-files-in-pager=vim TODO", ASK),
    ("merge", "git merge feature-x", ASK),
    ("update-ref", "git update-ref refs/heads/x HEAD", ASK),
]


def _fill(template: str, world: dict, **extra: str) -> str:
    for key, value in {**world, **extra}.items():
        template = template.replace("{" + key + "}", value)
    return template


CASES = (
    [pytest.param(w, d, e, id=f"write-{w.split()[0]}{w.split()[1] if w.split()[1].startswith('-') else ''}-{n}")
     for w in WRITERS for n, d, e in DESTINATIONS]
    + [pytest.param("mv {src} {S}/moved.txt", s, e, id=f"move-source-{n}") for n, s, e in MOVED_SOURCES]
)


@pytest.mark.parametrize("writer,target,expected", CASES)
def test_writer_by_destination(writer, target, expected, world):
    key = "src" if "{src}" in writer else "dest"
    command = _fill(writer, world, **{key: _fill(target, world)})
    assert observe(command, world["S"]) == expected, (command, LAST_REASON[0])


@pytest.mark.parametrize("command,expected", [pytest.param(c, e, id=n) for n, c, e in TAR])
def test_tar_extraction(command, expected, world):
    command = _fill(command, world)
    assert observe(command, world["S"]) == expected, (command, LAST_REASON[0])


@pytest.mark.parametrize("command,cwd,expected", [pytest.param(c, w, e, id=n) for n, c, w, e in CHAINS])
def test_a_chain_cannot_redirect_a_freed_write(command, cwd, expected, world):
    command = _fill(command, world)
    assert observe(command, _fill(cwd, world)) == expected, (command, LAST_REASON[0])


RM = [
    ("rm-in-scratch", "rm -f {S}/gone.txt", "{OUT}", "{OUT}", FREE),
    ("rm-relative-in-scratch", "rm -f gone.txt", "{S}", "{OUT}", FREE),
    ("rm-cd-scratch-relative", "cd {S} && rm -f gone.txt", "{OUT}", "{OUT}", FREE),
    ("rm-mkdir-then-rm", "mkdir -p {S}/d3 && rm -rf {S}/d3", "{S}", "{OUT}", FREE),
    ("rm-relative-hook-cwd-in-scratch", "rm -f f.txt", "{OUT}", "{S}", ASK),
    ("rm-cd-out-relative", "cd {OUT} && rm -f f.txt", "{S}", "{S}", ASK),
    ("rm-link-planted", "ln -s {OUT} {S}/fresh_rm && rm -rf {S}/fresh_rm/f.txt", "{S}", "{OUT}", ASK),
    ("rm-link-moved", "mv {S}/link_out {S}/d_rm && rm -rf {S}/d_rm/f.txt", "{S}", "{OUT}", ASK),
    ("rm-link-background", "ln -s {OUT} {S}/bg_rm & rm -rf {S}/bg_rm/f.txt", "{S}", "{OUT}", ASK),
]


@pytest.mark.parametrize("command,cwd,hook_cwd,expected", [pytest.param(c, w, h, e, id=n) for n, c, w, h, e in RM])
def test_a_scratch_rm_is_judged_at_the_shell_cwd_and_after_the_line(command, cwd, hook_cwd, expected, world, monkeypatch):
    monkeypatch.chdir(_fill(hook_cwd, world))
    command = _fill(command, world)
    assert observe(command, _fill(cwd, world)) == expected, (command, LAST_REASON[0])


@pytest.mark.parametrize("command,expected", [pytest.param(c, e, id=n) for n, c, e in GIT])
@pytest.mark.parametrize("repo", ["R", "W"])
def test_local_git_reads(command, expected, repo, world):
    command = _fill(command, world)
    assert observe(command, world[repo]) == expected, (command, LAST_REASON[0])


@pytest.mark.parametrize("name,expected", [("gaia.db", ASK), ("gaia.db-wal", ASK), ("gaia.db-shm", ASK),
                                           ("backups/x.db", ASK), ("contract_drafts/x.json", ASK), ("notes.txt", FREE)])
def test_the_live_db_backups_and_drafts_are_never_a_free_root(name, expected, world, monkeypatch):
    monkeypatch.setattr(mutative_verbs, "_gaia_scratch_root", lambda: world["DATA"])
    assert observe(f"cp /dev/null {world['DATA']}/{name}", world["S"]) == expected


GIT_TREES = [
    ("mv-tree-with-git-into-worktree", "mv {S}/withgit {W}/sub/", ASK),
    ("cp-r-tree-with-git-into-worktree", "cp -r {S}/withgit {W}/sub/newrepo", ASK),
    ("cp-a-tree-with-git-within-scratch", "cp -a {S}/withgit {S}/copy_git", ASK),
    ("mv-tree-with-nested-git-within-scratch", "mv {S}/nested {S}/moved_nested", ASK),
    ("cp-r-plain-tree-into-worktree", "cp -r {S}/real {W}/sub/plain", FREE),
    ("mv-plain-tree-within-scratch", "mv {S}/real {S}/moved_real", FREE),
]

IDENTITY = [
    ("cp-into-other-entry", "cp {OUT}/f.txt {O}/a.txt", "alocalwork01", ASK),
    ("mv-within-other-entry", "mv {O}/x.txt {O}/y.txt", "alocalwork01", ASK),
    ("tar-within-other-entry", "tar -xf {O}/clean.tar -C {O}/out", "alocalwork01", ASK),
    ("rm-other-entry-whole", "rm -rf {O}", "alocalwork01", ASK),
    ("rmdir-in-other-entry", "rmdir {O}/real", "alocalwork01", ASK),
    ("rm-own-entry-whole", "rm -rf {S}", "alocalwork01", FREE),
    ("rmdir-in-own-entry", "rmdir {S}/real", "alocalwork01", FREE),
    ("other-agent-in-my-entry", "cp {S}/a.txt {S}/new.txt", "aotheragent02", ASK),
    ("id-prefix-is-not-the-id", "cp {S}/a.txt {S}/new.txt", "alocalwork0", ASK),
    ("main-session-cp-scratch", "cp {S}/a.txt {S}/new.txt", "", ASK),
    ("main-session-rm-scratch", "rm -f {S}/gone.txt", "", ASK),
    ("main-session-tar-scratch", "tar -xf {S}/clean.tar -C {S}/out", "", ASK),
    ("main-session-cp-worktree", "cp {S}/a.txt {W}/new.txt", "", FREE),
    ("new-entry-not-owned", "cp {S}/a.txt {SR}/aotheragent03.json", "alocalwork01", ASK),
    ("new-entry-tar-not-owned", "tar -xf {S}/clean.tar -C {SR}/aotheragent03.token", "alocalwork01", ASK),
    ("new-entry-owned", "cp {S}/a.txt {SR}/alocalwork01.notes", "alocalwork01", FREE),
    ("rmdir-p-absolute", "rmdir -p {S}/real", "alocalwork01", ASK),
    ("rmdir-parents-absolute", "rmdir --parents {S}/real", "alocalwork01", ASK),
    ("rmdir-p-relative-in-entry", "rmdir -p real", "alocalwork01", FREE),
    ("sudo-cp", "sudo cp {S}/a.txt {S}/new.txt", "alocalwork01", ASK),
    ("sudo-mv", "sudo mv {S}/a.txt {S}/new.txt", "alocalwork01", ASK),
    ("sudo-rm", "sudo rm -f {S}/gone.txt", "alocalwork01", ASK),
    ("sudo-rmdir", "sudo rmdir {S}/real", "alocalwork01", ASK),
    ("sudo-tar", "sudo tar -xf {S}/clean.tar -C {S}/out", "alocalwork01", ASK),
    ("sudo-cp-in-chain", "cd {S} && sudo cp a.txt new.txt", "alocalwork01", ASK),
]


@pytest.mark.parametrize("command,expected", [pytest.param(c, e, id=n) for n, c, e in GIT_TREES])
def test_a_tree_carrying_git_internals_asks_like_a_tar_member(command, expected, world):
    command = _fill(command, world)
    assert observe(command, world["S"]) == expected, (command, LAST_REASON[0])


@pytest.mark.parametrize("command,agent,expected", [pytest.param(c, a, e, id=n) for n, c, a, e in IDENTITY])
def test_scratch_relief_belongs_to_the_entry_owner(command, agent, expected, world):
    command = _fill(command, world)
    assert observe(command, world["S"], agent) == expected, (command, agent, LAST_REASON[0])


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A fresh scratch entry and outside directory, so a replay in a real shell can write freely."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GAIA_DB", str(tmp_path / "data" / "gaia.db"))
    from gaia.paths import ensure_layout, scratch_dir

    ensure_layout()
    entry = Path(os.path.realpath(scratch_dir())) / "alocalwork01.replay"
    outside = Path(os.path.realpath(tmp_path)) / "outside"
    for directory in (entry, outside):
        directory.mkdir()
        (directory / "a.txt").write_text("a")
    _archive(entry / "clean.tar", [("pkg/file.txt", None)])
    return {"S": str(entry), "OUT": str(outside)}


CWD_SHIFTS = [
    ("cd-then-semicolon", "cd {S}/nope ; cp a.txt b1.txt", "{OUT}", ASK),
    ("subshell-cd", "(cd {S}) && cp a.txt b2.txt", "{OUT}", ASK),
    ("cd-in-pipeline", "cd {S} | cp a.txt b3.txt", "{OUT}", ASK),
    ("cd-or", "cd {S}/nope || cp a.txt b4.txt", "{OUT}", ASK),
    ("env-chdir-cp", "env -C {OUT} cp a.txt b5.txt", "{S}", ASK),
    ("env-chdir-long-cp", "env --chdir={OUT} cp a.txt b6.txt", "{S}", ASK),
    ("env-chdir-rm", "env -C {OUT} rm -f a.txt", "{S}", ASK),
    ("env-chdir-tar", "env --chdir={OUT} tar -xf {S}/clean.tar -C .", "{S}", ASK),
    ("cd-variable", "cd $D && cp a.txt b7.txt", "{S}", ASK),
    ("cd-substitution", "cd $(printf %s {OUT}) && cp a.txt b8.txt", "{S}", ASK),
    ("pushd", "pushd {OUT} && cp a.txt b9.txt", "{S}", ASK),
    ("second-cd-later", "cd {S} && true && cd {OUT} && cp a.txt b10.txt", "{S}", ASK),
    ("literal-cd-and", "cd {S} && cp a.txt b11.txt", "{OUT}", FREE),
    ("no-cd", "cp a.txt b12.txt", "{S}", FREE),
]


@pytest.mark.parametrize("line,cwd,expected", [pytest.param(c, w, e, id=n) for n, c, w, e in CWD_SHIFTS])
def test_relief_needs_the_directory_every_write_resolves_against(line, cwd, expected, box):
    line, cwd = _fill(line, box), _fill(cwd, box)
    verdict = observe(line, cwd)
    before = sorted(os.listdir(box["OUT"]))
    subprocess.run(["bash", "-c", line], cwd=cwd, capture_output=True, env={**os.environ, "D": box["OUT"]})
    touched_outside = sorted(os.listdir(box["OUT"])) != before
    assert (verdict, touched_outside) == (expected, expected == ASK), (line, LAST_REASON[0])
