"""Throwaway git repositories whose branches add a known amount of comment."""

import subprocess
from pathlib import Path


def git(repo, *args):
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
         "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True, text=True,
    )
    return done.stdout.strip()


def commit_files(repo, files, message="change"):
    for name, content in files.items():
        path = Path(repo) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def code(count):
    return "".join(f"value_{i} = {i}\n" for i in range(count))


def interleave(code_lines, comments):
    step = max(1, code_lines // max(1, comments))
    out = []
    for i in range(code_lines):
        if comments and i % step == 0 and i // step < comments:
            out.append(f"# fact {i}\n")
        out.append(f"value_{i} = {i}\n")
    return "".join(out)


def make_change(tmp_path, base_files, change_files, branch="feat"):
    repo = Path(tmp_path) / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    commit_files(repo, base_files, "base")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "checkout", "-q", "-b", branch)
    commit_files(repo, change_files)
    return repo
