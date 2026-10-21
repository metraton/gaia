"""Net comment lines a push adds through commits no remote holds yet, and the limit they must stay under."""

from __future__ import annotations

import ast
import importlib.util
import io
import re
import subprocess
import sys
import tokenize
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Optional, Set, Tuple

_LIMIT_FLOOR = 2
_LIMIT_SHARE = 0.02
_MAX_RUN = 2
_GIT_TIMEOUT_SECONDS = 30
_MAX_PUSHED_REFS = 100
_MAX_CONTAINED_REFS = 50
_PYTHON_EXT = {".py"}
_JS_EXT = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}
_HASH_EXT = {".sh", ".bash", ".zsh", ".yaml", ".yml"}
_HCL_EXT = {".tf", ".hcl"}
_SOURCE_EXT = _PYTHON_EXT | _JS_EXT | _HASH_EXT | _HCL_EXT
_GENERATED_PARTS = {"node_modules", "vendor", "dist", "generated", "__generated__"}
_GENERATED_NAMES = {"pnpm-lock.yaml", "yarn.lock", ".terraform.lock.hcl"}
_GENERATED_HEADER = re.compile(r"@generated|DO NOT EDIT|Code generated", re.IGNORECASE)
_COMMENT_MARKER = re.compile(r"^(?://|/\*|#)")
_DECORATION_RUN = r"([^\w\s.`'\"]|_)\1{2,}"
_BANNER_OPENING = re.compile(_DECORATION_RUN)
_BANNER_CLOSING = re.compile(_DECORATION_RUN + r"$")
_BANNER_TITLE = re.compile(r"\w[\w\s,:&/-]*")
_MAX_BANNER_TITLE = 40
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

    @property
    def reasons(self) -> List[str]:
        found = []
        if self.over_limit:
            found.append(f"it adds {self.net} net comment and docstring lines over "
                         f"{self.added_code} added code lines (limit {self.limit})")
        if self.banners:
            found.append(f"it adds {len(self.banners)} section banner(s)")
        if self.runs:
            found.append(f"it adds {len(self.runs)} comment block(s) over two lines")
        return found

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
                f"{self.excluded} already-pushed commits left out)")
        if not self.blocked:
            return head + " -- ok"

        rows = [f"  {p}:{m.line}: section banner: {m.text[:80]}" for p, m in self.banners]
        rows += [f"  {p}:{run[0].line}-{run[-1].line}: comment block of {len(run)} lines"
                 for p, run in self.runs]
        if self.over_limit:
            rows += [f"  {p}:{m.line}: {m.text[:100]}" for p, m in self.offenders]
        if len(rows) > max_lines:
            rows = rows[:max_lines] + [f"  ... {len(rows) - max_lines} more (gaia lint comments --json)"]
        return "\n".join([head + " -- OVER", *rows])


def _is_banner(text: str) -> bool:
    body = _COMMENT_MARKER.sub("", text.removesuffix("*/").rstrip(), count=1).strip()
    opening, closing = _BANNER_OPENING.match(body), _BANNER_CLOSING.search(body)
    if not (opening or closing):
        return False
    start, end = opening.end() if opening else 0, closing.start() if closing else len(body)
    if start >= end:
        return True
    inner = body[start:end]
    framed = (not opening or inner[0].isspace()) and (not closing or inner[-1].isspace())
    title = inner.strip()
    if not framed or len(title) > _MAX_BANNER_TITLE:
        return False
    return bool(opening and closing) or not title or bool(_BANNER_TITLE.fullmatch(title))


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


def _git(repo: str, *args: str, check: bool = True) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", repo, "-c", "core.quotepath=false", *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CommentLintError(f"git {args[0]} did not run: {exc}") from exc
    if result.returncode != 0 and check:
        raise CommentLintError(f"git {' '.join(args[:2])} failed: {result.stderr.strip()[:200]}")
    return result.stdout if result.returncode == 0 else ""


def _commit(repo: str, rev: str) -> Optional[str]:
    return _git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", check=False).strip() or None


def _own_destination(repo: str, head: str) -> Optional[str]:
    full = _git(repo, "rev-parse", "--symbolic-full-name", head, check=False).strip()
    if not full.startswith("refs/heads/"):
        return None
    branch = full.removeprefix("refs/heads/")
    remote = _git(repo, "config", f"branch.{branch}.remote", check=False).strip() or "origin"
    return f"{remote}/{branch}"


def _default_branch_ref(repo: str, remote: str) -> Optional[str]:
    for name in dict.fromkeys([remote, "origin"]):
        pointed = _git(repo, "symbolic-ref", "--short", f"refs/remotes/{name}/HEAD", check=False).strip()
        for short in [pointed, f"{name}/main", f"{name}/master"]:
            if short and _commit(repo, f"refs/remotes/{short}"):
                return f"refs/remotes/{short}"
    return None


def _nearest_remote_base(repo: str, head_sha: str) -> Optional[str]:
    best: Optional[Tuple[int, str]] = None
    for ref in _remote_refs(repo):
        base = _git(repo, "merge-base", ref, head_sha, check=False).strip()
        if not base or base == head_sha:
            continue
        ahead = _commits_between(repo, base, head_sha)
        if best is None or ahead < best[0]:
            best = (ahead, base)
    return best[1] if best else None


def _start(repo: str, head: str, head_sha: str, base: Optional[str],
           destination: Optional[str]) -> Optional[str]:
    if base:
        return _git(repo, "merge-base", base, head_sha).strip() or None
    destination = destination or _own_destination(repo, head)
    if destination and _commit(repo, f"refs/remotes/{destination}"):
        return f"refs/remotes/{destination}"
    default = _default_branch_ref(repo, destination.split("/", 1)[0] if destination else "origin")
    floor = (_git(repo, "merge-base", default, head_sha, check=False).strip() if default
             else _nearest_remote_base(repo, head_sha))
    return (_contained_remote_tip(repo, floor, head_sha) or floor) if floor else None


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_BLAME_HEADER = re.compile(r"^([0-9a-f]{40,64}) \d+ (\d+)")

Hunk = Tuple[int, int, int, int]


def _diff_hunks(repo: str, base: str, head: str) -> Dict[str, List[Hunk]]:
    """Per path, each hunk as (old_start, old_count, new_start, new_count)."""
    diff = _git(repo, "diff", "--unified=0", "--no-renames", "--no-color", base, head, "--")
    hunks: Dict[str, List[Hunk]] = {}
    old_path = new_path = ""
    for line in diff.splitlines():
        if line.startswith("--- "):
            old_path = line[4:].removeprefix("a/")
        elif line.startswith("+++ "):
            new_path = line[4:].removeprefix("b/")
            hunks.setdefault(old_path if new_path == "/dev/null" else new_path, [])
        elif line.startswith("@@"):
            match = _HUNK.match(line)
            if match:
                old_start, old_count, new_start, new_count = (
                    int(group) if group is not None else 1 for group in match.groups())
                hunks[old_path if new_path == "/dev/null" else new_path].append(
                    (old_start, old_count, new_start, new_count))
    return hunks


def _added_lines(hunks: List[Hunk]) -> Set[int]:
    return {n for _, _, start, count in hunks for n in range(start, start + count)}


def _old_lines(hunks: List[Hunk], line: int) -> range:
    """The lines of the old file that ``line`` of the new file stands for; empty for an insertion."""
    shift = 0
    for old_start, old_count, new_start, new_count in hunks:
        if new_count and new_start <= line < new_start + new_count:
            return range(old_start, old_start + old_count)
        before_hunk = line <= new_start if new_count == 0 else line < new_start
        if before_hunk:
            break
        shift += new_count - old_count
    return range(line - shift, line - shift + 1)


def _blob(repo: str, rev: str, path: str) -> str:
    return _git(repo, "show", f"{rev}:{path}", check=False)


def _is_generated(text: str) -> bool:
    return any(_GENERATED_HEADER.search(line) for line in text.splitlines()[:5])


def _comment_runs(scan: Scan) -> List[List[Marked]]:
    marks = sorted((m for m in scan.counted.values() if m.full_line and m.kind == "comment"),
                   key=lambda m: m.line)
    runs: List[List[Marked]] = []
    for mark in marks:
        if runs and runs[-1][-1].line + 1 == mark.line:
            runs[-1].append(mark)
        else:
            runs.append([mark])
    return runs


def _inherited_length(run: List[Marked], hunks: List[Hunk], old_runs: List[List[Marked]]) -> int:
    stood_for = {n for mark in run for n in _old_lines(hunks, mark.line)}
    return max((len(old) for old in old_runs if any(m.line in stood_for for m in old)), default=0)


def _long_runs(path: str, scan: Scan, old_scan: Scan, hunks: List[Hunk],
               added: Set[int]) -> List[Tuple[str, List[Marked]]]:
    old_runs = _comment_runs(old_scan)
    return [(path, run) for run in _comment_runs(scan)
            if len(run) > max(_MAX_RUN, _inherited_length(run, hunks, old_runs))
            and any(m.line in added for m in run)]


def _remote_refs(repo: str) -> List[str]:
    refs = _git(repo, "for-each-ref", "--sort=-committerdate", "--format=%(refname)",
                "refs/remotes", check=False).split()
    return [ref for ref in refs if not ref.endswith("/HEAD")][:_MAX_PUSHED_REFS]


def _commits_between(repo: str, older: str, newer: str) -> int:
    return int(_git(repo, "rev-list", "--count", f"{older}..{newer}", check=False).strip() or 0)


def _inserted_and_deleted(repo: str, older: str, newer: str) -> Tuple[int, int]:
    rows = [line.split("\t")[:2] for line in
            _git(repo, "diff", "--numstat", "--no-renames", older, newer, "--", check=False).splitlines()]
    counts = [(int(a), int(b)) for a, b in rows if a.isdigit() and b.isdigit()]
    return sum(a for a, _ in counts), sum(b for _, b in counts)


def _contained_remote_tip(repo: str, start: str, head: str) -> Optional[str]:
    """The remote tip whose whole content ``head`` already holds (squashed in), leaving the least to add."""
    best: Tuple[int, Optional[str]] = (_inserted_and_deleted(repo, start, head)[0], None)
    for ref in _remote_refs(repo)[:_MAX_CONTAINED_REFS]:
        inserted, deleted = _inserted_and_deleted(repo, ref, head)
        if deleted == 0 and inserted < best[0]:
            best = (inserted, ref)
    return best[1]


def _removed_lines(hunks: List[Hunk]) -> Set[int]:
    return {n for start, count, _, _ in hunks for n in range(start, start + count)}


def _lines_added_by(repo: str, start: str, head_sha: str, path: str, commits: Set[str]) -> Set[int]:
    blame = _git(repo, "blame", "--porcelain", head_sha, f"^{start}", "--not", "--remotes", "--", path)
    return {int(found.group(2)) for found in map(_BLAME_HEADER.match, blame.splitlines())
            if found and found.group(1) in commits}


def _texts_removed_by_new(repo: str, start: str, head_sha: str) -> Dict[str, Counter[str]]:
    patch = _git(repo, "log", "--no-merges", "--format=", "-p", "--unified=0", "--no-renames",
                 "--no-color", head_sha, "--not", start, "--remotes", "--")
    removed: Dict[str, Counter[str]] = {}
    path, in_hunk = "", False
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
        elif not in_hunk and line.startswith("--- "):
            path = line[4:].removeprefix("a/")
        elif not in_hunk and line.startswith("+++ ") and line != "+++ /dev/null":
            path = line[4:].removeprefix("b/")
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk and line.startswith("-"):
            removed.setdefault(path, Counter())[line[1:].strip()] += 1
    return removed


def _credited(removed: Set[int], old_text: str, texts: Counter[str]) -> Set[int]:
    lines, credited = old_text.splitlines(), set()
    for number in sorted(removed):
        text = lines[number - 1].strip()
        if texts[text] > 0:
            texts[text] -= 1
            credited.add(number)
    return credited


def measure(repo: str, head: str = "HEAD", base: Optional[str] = None,
            destination: Optional[str] = None) -> Report:
    """Measure the lines commits no remote holds add or remove between the push's start and ``head``.

    The start is the remote ref of ``destination`` (``<remote>/<branch>``, default the branch's own),
    else the default branch's merge-base moved to a remote tip ``head`` wholly contains; ``base`` overrides.
    """
    head_sha = _git(repo, "rev-parse", "--verify", f"{head}^{{commit}}").strip()
    start = _start(repo, head, head_sha, base, destination)
    if start is None:
        return Report(None, head, 0, 0, _LIMIT_FLOOR, [], [], [])
    return _measure_range(repo, start, head_sha, head)


def _measure_range(repo: str, start: str, head_sha: str, label: str) -> Report:
    new = set(_git(repo, "rev-list", head_sha, "--not", start, "--remotes").split())
    report = Report(start, label, 0, 0, _LIMIT_FLOOR, [], [], [],
                    _commits_between(repo, start, head_sha) - len(new))
    start_sha = _commit(repo, start)
    all_new = not report.excluded and _git(repo, "merge-base", start, head_sha).strip() == start_sha
    removed_texts = {} if all_new else _texts_removed_by_new(repo, start, head_sha)
    for path, hunks in sorted(_diff_hunks(repo, start, head_sha).items()):
        if not is_measured_path(path):
            continue
        new_text, old_text = _blob(repo, head_sha, path), _blob(repo, start, path)
        if _is_generated(new_text) or _is_generated(old_text):
            continue
        new_scan = scan_text(path, new_text) or Scan({}, set())
        old_scan = scan_text(path, old_text) or Scan({}, set())
        added, removed = _added_lines(hunks), _removed_lines(hunks)
        if not all_new:
            added &= _lines_added_by(repo, start, head_sha, path, new) if added and new else set()
            removed = _credited(removed, old_text, removed_texts.get(path, Counter()))
        report.net += (sum(line in added for line in new_scan.counted)
                       - sum(line in removed for line in old_scan.counted))
        report.added_code += len(added - new_scan.noncode)
        mine = [m for line, m in sorted(new_scan.counted.items()) if line in added]
        report.offenders += [(path, m) for m in mine]
        report.banners += [(path, m) for m in mine if m.banner]
        report.runs += _long_runs(path, new_scan, old_scan, hunks, added)
    report.limit = max(_LIMIT_FLOOR, int(_LIMIT_SHARE * report.added_code))
    return report
