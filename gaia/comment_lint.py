"""Net comment lines a change adds against its merge-base, and the limit it must stay under."""

from __future__ import annotations

import ast
import importlib.util
import io
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Optional, Set, Tuple

_LIMIT_FLOOR = 2
_LIMIT_SHARE = 0.02
_MAX_RUN = 2
_MAX_BASE_CANDIDATES = 300
_GIT_TIMEOUT_SECONDS = 30
_MAX_PUSHED_REFS = 100
_MAX_SQUASH_REFS = 50
_MAX_SQUASH_COMMITS = 20
_PYTHON_EXT = {".py"}
_JS_EXT = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}
_HASH_EXT = {".sh", ".bash", ".zsh", ".yaml", ".yml"}
_HCL_EXT = {".tf", ".hcl"}
_SOURCE_EXT = _PYTHON_EXT | _JS_EXT | _HASH_EXT | _HCL_EXT
_GENERATED_PARTS = {"node_modules", "vendor", "dist", "generated", "__generated__"}
_GENERATED_NAMES = {"pnpm-lock.yaml", "yarn.lock", ".terraform.lock.hcl"}
_GENERATED_HEADER = re.compile(r"@generated|DO NOT EDIT|Code generated", re.IGNORECASE)
_BANNER = re.compile(r"^(?:#+|//+|/\*+)\s*([-=*_~+#])\1{3,}")
_DIRECTIVE = re.compile(
    r"^(?:#+|//+|/\*+)\s*(?:!|noqa\b|type:|pragma\b|pylint:|fmt:|isort:|mypy:|pyright:|ruff:"
    r"|flake8:|coding[:=]|-\*-|eslint|@ts-|prettier-ignore|shellcheck|tflint|tfsec|checkov"
    r"|yamllint|SPDX-|Copyright|Licensed|nosec|istanbul|c8\s|vim:|region|endregion|<reference"
    r"|///)",
    re.IGNORECASE,
)
_EXPORT_LINE = re.compile(r"^\s*export\b")


class CommentLintError(RuntimeError):
    """The change could not be measured (not a repository, an unreadable ref)."""


@dataclass(frozen=True)
class Marked:
    line: int
    kind: str
    text: str
    full_line: bool = True
    banner: bool = False
    rev: str = ""


@dataclass
class Scan:
    counted: Dict[int, Marked]
    noncode: Set[int]


@dataclass
class Report:
    base: Optional[str]
    head: str
    added_code: int
    net: int
    limit: int
    offenders: List[Tuple[str, Marked]]
    banners: List[Tuple[str, Marked]]
    runs: List[Tuple[str, List[Marked]]]
    excluded: int = 0

    @property
    def over_limit(self) -> bool:
        return self.net > self.limit

    @property
    def blocked(self) -> bool:
        return self.over_limit or bool(self.banners) or bool(self.runs)

    def to_dict(self) -> dict:
        def row(path: str, mark: Marked, reason: str) -> dict:
            return {"file": path, "line": mark.line, "kind": mark.kind,
                    "reason": reason, "text": mark.text}

        listed = [row(p, m, "comment") for p, m in self.offenders]
        listed += [row(p, m, "banner") for p, m in self.banners]
        listed += [row(p, m, "block over two lines") for p, run in self.runs for m in run]
        return {
            "base": self.base, "head": self.head, "added_code_lines": self.added_code,
            "net_comment_lines": self.net, "limit": self.limit, "excluded_commits": self.excluded,
            "over_limit": self.over_limit, "blocked": self.blocked,
            "offenders": listed if self.blocked else [],
        }

    def render(self, max_lines: int = 40) -> str:
        head = (f"comments: net {self.net} over {self.added_code} added code lines, "
                f"limit {self.limit} (base {self.base or 'none'}, "
                f"{self.excluded} commits already pushed)")
        if not self.blocked:
            return head + " -- ok"

        def where(path: str, mark: Marked) -> str:
            return f"{path}:{mark.line}" + (f"@{mark.rev}" if mark.rev else "")

        rows = [f"  {where(p, m)}: section banner: {m.text[:80]}" for p, m in self.banners]
        rows += [f"  {where(p, run[0])}-{run[-1].line}: comment block of {len(run)} lines"
                 for p, run in self.runs]
        if self.over_limit:
            rows += [f"  {where(p, m)}: {m.text[:100]}" for p, m in self.offenders]
        if len(rows) > max_lines:
            rows = rows[:max_lines] + [f"  ... {len(rows) - max_lines} more (gaia lint comments --json)"]
        return "\n".join([head + " -- OVER", *rows])


def _is_banner(text: str) -> bool:
    return bool(_BANNER.match(text))


def _lexer():
    name = "gaia_source_lexer"
    if name not in sys.modules:
        path = Path(__file__).resolve().parent.parent / "hooks/modules/security/source_lexer.py"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _mark(counted: Dict[int, Marked], noncode: Set[int], line: int, kind: str,
          text: str, full_line: bool) -> None:
    if full_line:
        noncode.add(line)
    if _DIRECTIVE.match(text):
        return
    counted[line] = Marked(line, kind, text, full_line, _is_banner(text) and full_line)


def _scan_hash(lines: List[str], block_comments: bool = False) -> Scan:
    scan = Scan({}, set())
    in_block = False
    for number, raw in enumerate(lines, start=1):
        text = raw.strip()
        if not text:
            scan.noncode.add(number)
        elif in_block:
            _mark(scan.counted, scan.noncode, number, "comment", text, True)
            in_block = "*/" not in text
        elif text.startswith("#") or (block_comments and text.startswith("//")):
            _mark(scan.counted, scan.noncode, number, "comment", text, True)
        elif block_comments and text.startswith("/*"):
            _mark(scan.counted, scan.noncode, number, "comment", text, True)
            in_block = "*/" not in text
    return scan


def _is_public_name(name: str) -> bool:
    return not name.startswith("_") or (name.startswith("__") and name.endswith("__"))


def _units(node: ast.AST, public: bool) -> Iterable[Tuple[ast.AST, bool]]:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            unit_public = public and _is_public_name(child.name)
            yield child, unit_public
            yield from _units(child, unit_public and isinstance(child, ast.ClassDef))
        else:
            yield from _units(child, public)


def _scan_python(text: str, lines: List[str]) -> Optional[Scan]:
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
        tree = ast.parse(text)
    except (tokenize.TokenError, SyntaxError, ValueError):
        return None
    scan = Scan({}, {n for n, raw in enumerate(lines, start=1) if not raw.strip()})
    for token in tokens:
        if token.type == tokenize.COMMENT:
            row, col = token.start
            _mark(scan.counted, scan.noncode, row, "comment", token.string.strip(),
                  not lines[row - 1][:col].strip())
    for unit, public in [(tree, True), *_units(tree, True)]:
        body = getattr(unit, "body", None)
        first = body[0] if body else None
        if not (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            continue
        span = range(first.lineno, first.end_lineno + 1)
        scan.noncode.update(span)
        if public and len(span) == 1:
            continue
        for number in span:
            content = lines[number - 1].strip()
            if content.strip("\"'rRbBuUfF"):
                scan.counted[number] = Marked(number, "docstring", content)
    return scan


def _scan_js(text: str, lines: List[str]) -> Scan:
    lexer = _lexer()
    view = lexer.strip_source(text, lexer.JS_SPEC).exec_view.splitlines()
    scan = Scan({}, set())
    in_jsdoc = False
    for number, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        code = view[number - 1] if number <= len(view) else raw
        if not stripped:
            scan.noncode.add(number)
            continue
        if code.strip() == stripped:
            continue
        cut = next((i for i, (a, b) in enumerate(zip(raw, code)) if a != b), len(raw))
        comment = raw[cut:].strip()
        full_line = not code.strip()
        in_jsdoc = in_jsdoc or stripped.startswith("/**")
        kind = "docstring" if in_jsdoc else "comment"
        following = next((later for later in lines[number:] if later.strip()), "")
        one_line_free = (in_jsdoc and stripped.startswith("/**") and stripped.endswith("*/")
                         and _EXPORT_LINE.match(following))
        if one_line_free:
            scan.noncode.add(number)
        else:
            _mark(scan.counted, scan.noncode, number, kind, comment, full_line)
        in_jsdoc = in_jsdoc and "*/" not in stripped
    return scan


def scan_text(path: str, text: str) -> Optional[Scan]:
    """Classify every line of one source file, or None when its type is not measured."""
    suffix = PurePosixPath(path).suffix.lower()
    lines = text.splitlines()
    if suffix in _PYTHON_EXT:
        return _scan_python(text, lines) or _scan_hash(lines)
    if suffix in _JS_EXT:
        return _scan_js(text, lines)
    if suffix in _HCL_EXT:
        return _scan_hash(lines, block_comments=True)
    if suffix in _HASH_EXT:
        return _scan_hash(lines)
    return None


def is_measured_path(path: str) -> bool:
    """True for a source file type the counter measures and that is not vendored or lock output."""
    posix = PurePosixPath(path)
    return (
        posix.suffix.lower() in _SOURCE_EXT
        and posix.name not in _GENERATED_NAMES
        and not (set(posix.parts[:-1]) & _GENERATED_PARTS)
        and not posix.name.endswith(".min.js")
    )


def _git(repo: str, *args: str, check: bool = True, stdin: Optional[str] = None) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", repo, "-c", "core.quotepath=false", *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=_GIT_TIMEOUT_SECONDS, input=stdin,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CommentLintError(f"git {args[0]} did not run: {exc}") from exc
    if result.returncode != 0 and check:
        raise CommentLintError(f"git {' '.join(args[:2])} failed: {result.stderr.strip()[:200]}")
    return result.stdout if result.returncode == 0 else ""


def _own_remote_refs(repo: str, head: str) -> Set[str]:
    branch = _git(repo, "rev-parse", "--abbrev-ref", head, check=False).strip()
    if not branch or branch == "HEAD":
        return set()
    refs = _git(repo, "for-each-ref", "--format=%(refname)", "refs/remotes", check=False)
    return {ref for ref in refs.split() if ref.split("/", 3)[-1] == branch}


def resolve_base(repo: str, head: str, explicit: Optional[str] = None) -> Optional[str]:
    """Merge-base of ``head`` with ``explicit`` or the nearest remote branch not containing it, or None."""
    head_sha = _git(repo, "rev-parse", "--verify", f"{head}^{{commit}}").strip()
    if explicit:
        return _git(repo, "merge-base", explicit, head_sha).strip() or None
    skip = _own_remote_refs(repo, head)
    refs = _git(repo, "for-each-ref", "--format=%(refname)", "refs/remotes", check=False).split()
    best: Optional[Tuple[int, str]] = None
    for ref in refs[:_MAX_BASE_CANDIDATES]:
        if ref in skip or ref.endswith("/HEAD"):
            continue
        base = _git(repo, "merge-base", ref, head_sha, check=False).strip()
        if not base or base == head_sha:
            continue
        ahead = int(_git(repo, "rev-list", "--count", f"{base}..{head_sha}").strip() or 0)
        if best is None or ahead < best[0]:
            best = (ahead, base)
    return best[1] if best else None


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _added_ranges(repo: str, base: str, head: str) -> Dict[str, Set[int]]:
    diff = _git(repo, "diff", "--unified=0", "--no-renames", "--no-color", base, head, "--")
    added: Dict[str, Set[int]] = {}
    old_path = new_path = ""
    for line in diff.splitlines():
        if line.startswith("--- "):
            old_path = line[4:].removeprefix("a/")
        elif line.startswith("+++ "):
            new_path = line[4:].removeprefix("b/")
            path = old_path if new_path == "/dev/null" else new_path
            added.setdefault(path, set())
        elif line.startswith("@@"):
            match = _HUNK.match(line)
            if match:
                start, count = int(match.group(1)), int(match.group(2) or 1)
                added[old_path if new_path == "/dev/null" else new_path].update(
                    range(start, start + count))
    return added


def _blob(repo: str, rev: str, path: str) -> str:
    return _git(repo, "show", f"{rev}:{path}", check=False)


def _is_generated(text: str) -> bool:
    return any(_GENERATED_HEADER.search(line) for line in text.splitlines()[:5])


def _long_runs(path: str, scan: Scan, added: Set[int]) -> List[Tuple[str, List[Marked]]]:
    marks = sorted((m for m in scan.counted.values() if m.full_line and m.kind == "comment"),
                   key=lambda m: m.line)
    runs: List[List[Marked]] = []
    for mark in marks:
        if runs and runs[-1][-1].line + 1 == mark.line:
            runs[-1].append(mark)
        else:
            runs.append([mark])
    return [(path, run) for run in runs
            if len(run) > _MAX_RUN and sum(m.line in added for m in run) > _MAX_RUN]


def _patch_id(repo: str, *revs: str) -> str:
    diff = _git(repo, "diff", "--no-renames", *revs, check=False)
    words = _git(repo, "patch-id", "--stable", stdin=diff, check=False).split() if diff else []
    return words[0] if words else ""


def _already_pushed(repo: str, merge_base: str, head: str, commits: List[str]) -> Set[str]:
    """Commits a remote ref already holds: by ancestry, by patch-id, or as a squash of one branch."""
    span = f"{merge_base}..{head}"
    unpushed = _git(repo, "rev-list", "--no-merges", span, "--not", "--remotes", check=False).split()
    done = set(commits) - set(unpushed)
    refs = _git(repo, "for-each-ref", "--sort=-committerdate", "--format=%(refname)",
                "refs/remotes", check=False).split()
    refs = [ref for ref in refs if not ref.endswith("/HEAD")][:_MAX_PUSHED_REFS]
    for ref in refs:
        done.update(line[2:].strip() for line in _git(repo, "cherry", ref, head, check=False)
                    .splitlines() if line.startswith("- "))
    for commit in [c for c in commits if c not in done][:_MAX_SQUASH_COMMITS]:
        patch = _patch_id(repo, f"{commit}^", commit)
        for ref in refs[:_MAX_SQUASH_REFS]:
            fork = _git(repo, "merge-base", f"{commit}^", ref, check=False).strip()
            if patch and fork and _patch_id(repo, fork, ref) == patch:
                done.add(commit)
                break
    return done


def measure(repo: str, head: str = "HEAD", base: Optional[str] = None) -> Report:
    """Measure the change ``head`` carries, leaving out commits a remote ref already holds and merges.

    An explicit ``base`` measures its exact range with nothing left out.
    """
    merge_base = resolve_base(repo, head, base)
    if merge_base is None:
        return Report(None, head, 0, 0, _LIMIT_FLOOR, [], [], [])
    commits = _git(repo, "rev-list", "--no-merges", "--reverse", f"{merge_base}..{head}").split()
    skipped = set() if base else _already_pushed(repo, merge_base, head, commits)
    merges = not base and int(
        _git(repo, "rev-list", "--merges", "--count", f"{merge_base}..{head}").strip() or 0)
    spans = [(merge_base, head, "")]
    if skipped or merges:
        spans = [(f"{c}^", c, c[:7]) for c in commits if c not in skipped]
    report = Report(merge_base, head, 0, 0, _LIMIT_FLOOR, [], [], [], len(skipped & set(commits)))
    for span_base, span_head, rev in spans:
        _measure_span(repo, span_base, span_head, rev, report)
    report.limit = max(_LIMIT_FLOOR, int(_LIMIT_SHARE * report.added_code))
    return report


def _measure_span(repo: str, merge_base: str, head: str, rev: str, report: Report) -> None:
    def tag(mark: Marked) -> Marked:
        return replace(mark, rev=rev) if rev else mark

    for path, added in sorted(_added_ranges(repo, merge_base, head).items()):
        if not is_measured_path(path):
            continue
        new_text, old_text = _blob(repo, head, path), _blob(repo, merge_base, path)
        if _is_generated(new_text) or _is_generated(old_text):
            continue
        new_scan = scan_text(path, new_text) or Scan({}, set())
        old_scan = scan_text(path, old_text) or Scan({}, set())
        report.net += len(new_scan.counted) - len(old_scan.counted)
        report.added_code += len(added - new_scan.noncode)
        mine = [tag(m) for line, m in sorted(new_scan.counted.items()) if line in added]
        report.offenders += [(path, m) for m in mine]
        report.banners += [(path, m) for m in mine if m.banner]
        report.runs += [(p, [tag(m) for m in run]) for p, run in _long_runs(path, new_scan, added)]
