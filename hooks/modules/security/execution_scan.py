"""The short execution scan (D198 point 3): an execution's entry and its literal local callees, depth 1, read for four signals."""

from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Mapping, Optional, Tuple

from .program_heredoc import heredoc_program
from .verb_lexicon import CHANGE_VERB_CLASSES, ambiguous_verb_relieved

# D198 point 6: only what the text shows literally is read; a signal hidden behind a variable,
# a helper of a helper or a computed name is not chased.
REMOTE_FAMILIES = frozenset({"k8s", "iac", "git", "cloud", "workspace", "package", "system"})
CHANGE_VERBS = frozenset().union(*CHANGE_VERB_CLASSES.values())
MAX_BYTES = 512 * 1024
MAX_CALLEES = 32
VERB_WINDOW = 4


@dataclass(frozen=True)
class ScanVerdict:
    """What the scan read and the signal it found; ``signal`` is None when the execution runs free."""

    kind: str
    entry: str
    signal: Optional[str] = None
    evidence: str = ""
    rule: str = ""


@dataclass(frozen=True)
class _Execution:
    kind: str
    entry: str
    texts: Tuple[Tuple[str, str], ...]
    base: Path


_OS_PATH = re.compile(
    r"(?<![\w.$}~)/-])/(?:etc|boot|sys|sbin|lib|lib64)(?=/|\b)"
    r"|(?<![\w.$}~)/-])/usr/(?!bin/env\b|bin/python|bin/node\b|bin/bash\b|bin/sh\b|local/bin/)"
    r"|(?<![\w.$}~)/-])/var/(?!tmp\b|folders\b)"
    r"|(?<![\w.$}~)/-])/bin/(?!(?:ba|z|da)?sh\b)"
)
_ACCOUNT_OR_HARNESS_PATH = re.compile(
    r"(?:~|\$HOME|\$\{HOME\}|/home/[\w.-]+|/root|/Users/[\w.-]+)/\.(?:ssh|bashrc|bash_profile|profile|zshrc|zprofile"
    r"|zshenv|gitconfig|aws|kube|gnupg|netrc|npmrc|pypirc|docker|config/gcloud|config/gh)\b"
    r"|\.claude(?=[/\"'\s]|$)|\.gaia/(?:gaia\.db|cache|plugins|hooks)\b|\.config/opencode\b|\.opencode/|\.codex/"
)
_DOWNLOAD_AND_RUN = re.compile(
    r"\b(?:curl|wget|iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\b[^|\n]*\|\s*(?:sudo\s+)?"
    r"(?:(?:ba|z|da|k)?sh|python[\d.]*|node|perl|ruby|bun|deno|iex|Invoke-Expression)\b"
    r"|<\(\s*(?:curl|wget)\b"
    r"|(?:eval|source|\.|-c)\s+\\?[\"']?\$\(\s*(?:curl|wget)\b"
    r"|\b(?:iex|Invoke-Expression)\s*\(?\s*\(?\s*(?:iwr|irm|Invoke-WebRequest|Invoke-RestMethod|New-Object\s+Net\.WebClient)"
)
_RUNTIME_CODE = re.compile(
    r"(?<![.\w$])(?:eval|exec)\s*\(\s*(?![\s'\"`)])"
    r"|\bnew\s+Function\b|(?<![.\w$])Function\s*\("
    r"|(?<![.\w$])(?:import|require)\s*\(\s*(?![\s'\"`)])"
    r"|(?<![.\w])(?:__import__|import_module)\s*\(\s*(?![\s'\"])"
    r"|\bvm\.(?:runIn\w+|Script|compileFunction)\b|\bplugin\.Open\s*\("
    r"|(?:^|[;&|({]\s*|\b(?:then|do|else)\s+|[\"']\s*)eval\s+\\?[\"'$]"
    r"|\$\(eval\b|(?:^|[;&|]\s*)(?:source|\.)\s+<\(|\bInvoke-Expression\b"
)
_SEND_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_SEND_PROGRAMS = frozenset({"curl", "wget", "http", "https", "xh", "scp", "sftp", "rsync"})
_CURL_SEND_LONG = ("--data", "--form", "--json", "--upload-file")
_NETWORK_SEND = re.compile(
    r"\b(?:requests|httpx|session|client|aiohttp|axios|got|ky|superagent)\.(?:post|put|patch|delete)\s*\("
    r"|\b(?:urlopen|Request)\s*\([^)]*\bdata\s*="
    r"|\bfetch\s*\([^)]*\b(?:body\s*:|method\s*:\s*['\"](?:POST|PUT|PATCH|DELETE))"
    r"|\bhttp\.(?:Post|PostForm)\s*\(|\bNewRequest\s*\(\s*['\"](?:POST|PUT|PATCH|DELETE)"
    r"|\bmethod\s*[:=]\s*['\"](?:POST|PUT|PATCH|DELETE)['\"]",
    re.I,
)
_DESTROY_CALL = re.compile(
    r"\b(?P<call>rmtree|RemoveAll|remove_dir_all|truncate|rmSync|rmdirSync|rm|rmdir)\s*\(\s*[rbf]?(['\"])(?P<path>[^'\"]+)\2"
)
_COMMENT_MARKS =("#", "//", "/*", "* ", "--")
_SEGMENT_SPLIT = re.compile(r"&&|\|\||[;|`(){}]|\$\(")
_LITERAL = re.compile(r"""(["'])((?:\\.|(?!\1).)*?)\1""")
_LEADING_WORDS = frozenset({"sudo", "env", "exec", "command", "time", "nohup", "then", "do", "else", "!", "if", "while"})
_FLOOR_PROGRAMS = frozenset({"rm", "dd", "shred", "wipefs", "fdisk", "sfdisk", "parted", "chmod", "chown",
                             "diskpart", "vssadmin", "format"})

_PATH_TOKEN = re.compile(
    r"(?<![\w$@{}/.-])((?:\.{1,2}/|/|~/)?[\w.@+-]+(?:/[\w.@+-]+)*"
    r"\.(?:sh|bash|zsh|py|js|mjs|cjs|ts|mts|cts|tsx|jsx|rb|pl|php|go|rs|mk))\b"
)
_JS_SPECIFIER = re.compile(r"""(?:\brequire\s*\(\s*|\bfrom\s+|\bimport\s*\(?\s*)['"](\.{1,2}/[^'"]+)['"]""")
_PY_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.M)
_JS_SUFFIXES = ("", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", "/index.js", "/index.ts")

_PYTHON = re.compile(r"python(?:\d+(?:\.\d+)?)?")
_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
_JS_RUNTIMES = frozenset({"node", "bun", "deno", "tsx", "ts-node"})
_INLINE_FLAGS = {
    "python": frozenset({"-c"}), "shell": frozenset({"-c"}), "ruby": frozenset({"-e"}), "perl": frozenset({"-e", "-E"}),
    "php": frozenset({"-r"}), "node": frozenset({"-e", "--eval", "-p", "--print"}),
}
_NO_RUN_FLAGS = {"python": frozenset({"-m"}), "shell": frozenset({"-n"}), "node": frozenset({"-c", "--check", "--test"})}
_VALUE_FLAGS = frozenset({"-W", "-X", "-r", "--require", "--import", "--loader", "--experimental-loader", "--conditions",
                          "-C", "--env-file", "--preload", "--cwd", "--config", "--import-map", "-I", "--input-type",
                          "--tsconfig", "--title"})
_SCRIPT_SUFFIXES = (".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".tsx", ".jsx",
                    ".rb", ".pl", ".php")
_OUTPUT_REDIRECT = re.compile(r"^\d?>&\d$|^\d?>/dev/null$")

_PACKAGE_DIR_FLAGS = frozenset({"--prefix", "-C", "--dir", "--cwd"})
_PACKAGE_UNRESOLVED_FLAGS = frozenset({"--workspace", "-w", "--workspaces", "-ws", "--filter", "-F", "--recursive", "-r"})
_NPM_RUN = frozenset({"run", "run-script", "rum", "urn"})
_LIFECYCLE_SCRIPTS = {"test": "test", "t": "test", "tst": "test", "start": "start", "stop": "stop", "restart": "restart"}
_MANAGER_BUILTINS = frozenset({
    "install", "i", "add", "remove", "rm", "uninstall", "update", "up", "upgrade", "exec", "x", "dlx", "create", "init",
    "publish", "deploy", "link", "unlink", "why", "list", "ls", "outdated", "audit", "store", "config", "import",
    "rebuild", "prune", "pack", "patch", "env", "setup", "workspace", "workspaces", "info", "cache", "global",
    "version", "bin", "node", "pm", "upgrade-interactive", "dedupe", "licenses", "set", "plugin",
})
_BUN_COMMANDS = _MANAGER_BUILTINS | {"build", "test", "repl", "completions", "discord", "help"}
_MAKE_NO_RUN = frozenset({"-n", "--dry-run", "--just-print", "--recon", "-q", "--question", "-p", "--print-data-base"})
_MAKE_RULE = re.compile(r"^([^\s#:=][^:=]*?)\s*::?(?!=)\s*([^;=]*?)\s*(?:;(.*))?$")
_MAKE_DIRECTIVE = re.compile(r"^\s*(?:export|override|define|endef|include|-include|sinclude|ifeq|ifneq|ifdef|ifndef|else|endif|vpath|unexport)\b")
_JUST_NO_RUN = frozenset({"-l", "--list", "--summary", "-n", "--dry-run", "-s", "--show", "--evaluate", "--dump",
                          "--choose", "--fmt", "--init", "--edit", "--variables", "--groups"})
_JUST_RECIPE = re.compile(r"^@?([A-Za-z_][\w-]*)\b[^:]*:(?!=)\s*(.*)$")
_GO_VALUE_FLAGS = frozenset({"-o", "-tags", "-ldflags", "-gcflags", "-asmflags", "-mod", "-p", "-modfile", "-overlay",
                             "-pgo", "-pkgdir", "-buildmode", "-compiler", "-gccgoflags", "-installsuffix"})
_CARGO_VALUE_FLAGS = frozenset({"--features", "-F", "--target", "--profile", "--target-dir", "-j", "--jobs", "--config",
                                "-Z", "--color", "--message-format"})


def scan_execution(command: str, cwd: Optional[str], families: Mapping[str, str]) -> Optional[ScanVerdict]:
    """Judge *command* when it is an execution the scan models; None leaves it to the lanes after the scan."""
    base = Path(cwd or os.getcwd())
    found = _execution(command, base)
    if found is None or isinstance(found, ScanVerdict):
        return found
    roots = _kept_roots(base)
    callees: List[Tuple[str, str]] = []
    for label, text in found.texts:
        hit = _signal(text, families, roots)
        if hit is not None:
            return _signed(found.kind, found.entry, hit, label)
        callees.extend(_callees(text, Path(label).parent if label.startswith("/") else found.base, found.base, label))
    for label, text in callees[:MAX_CALLEES]:
        hit = _signal(text, families, roots)
        if hit is not None:
            return _signed(found.kind, found.entry, hit, label)
    return ScanVerdict(found.kind, found.entry)


def _signed(kind: str, entry: str, hit: Tuple[str, str, str], where: str) -> ScanVerdict:
    signal, evidence, rule = hit
    return ScanVerdict(kind, entry, signal, f"{where}: {evidence}", rule)


def _kept_roots(base: Path) -> Tuple[str, ...]:
    """The project root holding *base* (its git top, else *base*) and Gaia scratch: deletes inside them are local work."""
    project = next((p for p in (base, *base.parents) if (p / ".git").exists()), base)
    data = os.environ.get("GAIA_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".gaia")
    return tuple(os.path.realpath(str(p)) for p in (project, os.path.join(data, "scratch")))


def _outside(path: str, roots: Tuple[str, ...]) -> bool:
    expanded = os.path.expanduser(path.replace("${HOME}", "~").replace("$HOME", "~"))
    if not os.path.isabs(expanded):
        return False
    real = os.path.realpath(expanded)
    return not any(real == root or real.startswith(root + os.sep) for root in roots)


def _destroys_outside(words: List[str], roots: Tuple[str, ...]) -> bool:
    name, args = os.path.basename(words[0]), words[1:]
    if name == "rm" and any(a in ("-r", "-R", "--recursive") or (a[:1] == "-" and a[1:2] != "-" and set(a[1:]) & {"r", "R"})
                            for a in args):
        return any(_outside(a, roots) for a in args if not a.startswith("-"))
    if name == "truncate":
        sizes = {i + 1 for i, a in enumerate(args) if a in ("-s", "--size")}
        return any(_outside(a, roots) for i, a in enumerate(args) if not a.startswith("-") and i not in sizes)
    return False


def _shell_sends(words: List[str]) -> bool:
    name, args = os.path.basename(words[0]), words[1:]
    operands = [a for a in args if not a.startswith("-")]
    if name == "curl":
        method = next((args[i + 1] for i, a in enumerate(args[:-1]) if a in ("-X", "--request")), "")
        method = method or next((a[2:] for a in args if a.startswith("-X") and len(a) > 2), "")
        if method.upper() in _SEND_METHODS:
            return True
        data = any(a in ("-d", "-F", "-T") or a.startswith(_CURL_SEND_LONG)
                   or (a[:1] == "-" and a[1:2] != "-" and set(a[1:]) & {"d", "F", "T"}) for a in args)
        return data and not any(a in ("-G", "--get") for a in args)
    if name == "wget":
        return any(a.startswith(("--post-data", "--post-file", "--body-data", "--body-file")) or
                   (a.startswith("--method") and a.partition("=")[2].upper() in _SEND_METHODS) for a in args)
    if name in ("http", "https", "xh"):
        return bool(operands) and operands[0].upper() in _SEND_METHODS
    if name in ("scp", "sftp", "rsync"):
        return bool(operands) and bool(re.match(r"^[\w.@-]+:", operands[-1]))
    if name in ("gsutil", "gcloud", "aws") and operands:
        return bool(set(operands) & {"cp", "rsync", "sync", "mv"}) and operands[-1].startswith(("gs://", "s3://"))
    return False


def _signal(text: str, families: Mapping[str, str], roots: Tuple[str, ...]) -> Optional[Tuple[str, str, str]]:
    """The first signal on a non-comment line of *text*, as (signal, line excerpt, rule)."""
    for raw in text.replace("\\\n", " ").splitlines():
        line = raw.strip()
        if not line or line.startswith(_COMMENT_MARKS):
            continue
        excerpt = line[:160]
        if _DOWNLOAD_AND_RUN.search(line):
            return "download-and-run", excerpt, "sign.foreign-code"
        if _RUNTIME_CODE.search(line):
            return "runtime-code", excerpt, "sign.foreign-code"
        if _ACCOUNT_OR_HARNESS_PATH.search(line):
            return "system-path", excerpt, "sign.lands-on-protected"
        if _OS_PATH.search(line):
            return "system-path", excerpt, "sign.system-path"
        if _NETWORK_SEND.search(line):
            return "network-send", excerpt, "sign.speaks"
        if any(_outside(m.group("path"), roots) for m in _DESTROY_CALL.finditer(line)
               if m.group("call") in ("rmtree", "RemoveAll", "remove_dir_all", "truncate") or "recursive" in line):
            return "destroys-outside", excerpt, "sign.destroys"
        for words in _candidates(line[:4000], families):
            if not words:
                continue
            if _remote_change(words, families):
                return "remote-change-verb", excerpt, "sign.lands"
            if os.path.basename(words[0]) in _FLOOR_PROGRAMS and _blocked(" ".join(words)):
                return "floor", excerpt, "sign.destroys"
            if _destroys_outside(words, roots):
                return "destroys-outside", excerpt, "sign.destroys"
            if _shell_sends(words):
                return "network-send", excerpt, "sign.speaks"
    return None


def _blocked(command: str) -> bool:
    from .blocked_commands import is_blocked_command  # blocked_commands imports mutative_verbs, which imports this module

    return is_blocked_command(command).is_blocked


def _candidates(line: str, families: Mapping[str, str]) -> Iterator[List[str]]:
    """Word lists that may stand in command position: shell segments, spaced string literals, a line's literal argv."""
    literals = [m.group(2) for m in _LITERAL.finditer(line)]
    texts = _SEGMENT_SPLIT.split(line) + [lit for lit in literals if " " in lit.strip()]
    for text in texts:
        cheap = _strip_leading(text.split())
        if cheap and _worth_parsing(cheap[0], families):
            try:
                yield _strip_leading(shlex.split(text))
            except ValueError:
                yield cheap
    if len(literals) > 1 and _worth_parsing(literals[0], families):
        yield literals


def _worth_parsing(program: str, families: Mapping[str, str]) -> bool:
    name = os.path.basename(program)
    return (families.get(name) in REMOTE_FAMILIES or name in _FLOOR_PROGRAMS or name in _SEND_PROGRAMS
            or name == "truncate" or name.startswith("mkfs"))


def _strip_leading(words: List[str]) -> List[str]:
    index = 0
    while index < len(words) and (words[index] in _LEADING_WORDS or re.match(r"^\w+=", words[index])):
        index += 1
    return words[index:]


def _remote_change(words: List[str], families: Mapping[str, str]) -> bool:
    if not words:
        return False
    family = families.get(os.path.basename(words[0]))
    if family not in REMOTE_FAMILIES:
        return False
    positionals = [w.lower() for w in words[1:] if not w.startswith("-")][:VERB_WINDOW]
    return any(
        fragment in CHANGE_VERBS and not ambiguous_verb_relieved(fragment, family)
        for word in positionals for fragment in (word, *word.split("-"))
    )


def _callees(text: str, entry_dir: Path, base: Path, entry: str) -> List[Tuple[str, str]]:
    """The literal local files *text* names, read once each; their own callees are not followed."""
    names = [m.group(1) for m in _PATH_TOKEN.finditer(text)]
    found: List[Path] = []
    for name in names:
        found.extend(p for p in (_resolve(name, entry_dir), _resolve(name, base)) if p is not None)
    for spec in _JS_SPECIFIER.findall(text):
        found.extend(p for p in (_resolve(spec + suffix, entry_dir) for suffix in _JS_SUFFIXES) if p is not None)
    for match in _PY_IMPORT.finditer(text):
        module = (match.group(1) or match.group(2)).lstrip(".").replace(".", "/")
        found.extend(p for p in (_resolve(module + ".py", entry_dir), _resolve(module + "/__init__.py", entry_dir)) if p)
    seen = {entry}
    result: List[Tuple[str, str]] = []
    for path in found:
        label = str(path)
        if label in seen:
            continue
        seen.add(label)
        body = _read(path)
        if body is not None:
            result.append((label, body))
        if len(result) >= MAX_CALLEES:
            break
    return result


def _resolve(name: str, directory: Path) -> Optional[Path]:
    path = Path(os.path.expanduser(name))
    path = path if path.is_absolute() else directory / path
    try:
        return path.resolve() if path.is_file() else None
    except OSError:
        return None


def _read(path: Path) -> Optional[str]:
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_BYTES)
    except OSError:
        return None
    if b"\0" in data[:4096]:
        return None
    return data.decode("utf-8", errors="replace")


def _execution(command: str, base: Path):
    """The execution *command* starts, a direct verdict for a remote fetch, or None when it is not one the scan models."""
    program = heredoc_program(command)
    if program is not None:
        language = _language(os.path.basename(program.interpreter))
        return _Execution("inline", program.interpreter, (("<heredoc>", program.body),), base) if language else None
    if "<<" in command:
        return None
    try:
        tokens = [t for t in shlex.split(command) if not _OUTPUT_REDIRECT.match(t)]
    except ValueError:
        return None
    if not tokens or re.match(r"^\w+=", tokens[0]) or any(t in ("|", "||", "&&", ";", "&") or t[:1] in ("<", ">") for t in tokens):
        return None
    name, args = os.path.basename(tokens[0]), tokens[1:]
    if name in ("npm", "pnpm", "yarn"):
        return _package_script(name, args, base)
    if name == "make":
        return _make(args, base)
    if name == "just":
        return _just(args, base)
    if name == "go" and args[:1] == ["run"]:
        return _go_run(args[1:], base)
    if name == "cargo" and args[:1] == ["run"]:
        return _cargo_run(args[1:], base)
    language = _language(name)
    if language:
        return _interpreter(name, language, args, base)
    if tokens[0].startswith(("./", "../")) or tokens[0].endswith(_SCRIPT_SUFFIXES):
        return _script(tokens[0], base, "script-file")
    return None


def _language(name: str) -> Optional[str]:
    if _PYTHON.fullmatch(name):
        return "python"
    if name in _SHELLS:
        return "shell"
    if name in _JS_RUNTIMES:
        return name if name in ("bun", "deno") else "node"
    return name if name in ("ruby", "perl", "php") else None


def _script(name: str, base: Path, kind: str):
    path = _resolve(name, base)
    if path is None or path.name == "gaia" or (path.parent.name == "cli" and path.parent.parent.name == "bin"):
        return None
    body = _read(path)
    return None if body is None else _Execution(kind, str(path), ((str(path), body),), path.parent)


def _interpreter(name: str, language: str, args: List[str], base: Path):
    inline = _INLINE_FLAGS.get("node" if language == "bun" else language, frozenset())
    index = 0
    while index < len(args):
        arg = args[index]
        flag, _, attached = arg.partition("=")
        if flag in inline:
            code = attached if attached else (args[index + 1] if index + 1 < len(args) else None)
            return None if code is None else _Execution("inline", f"{name} {flag}", (("<inline>", code),), base)
        if arg in _NO_RUN_FLAGS.get(language, ()):
            return None
        if arg == "--":
            index += 1
            break
        if not arg.startswith("-") or arg == "-":
            break
        index += 2 if arg in _VALUE_FLAGS else 1
    rest = args[index:]
    if not rest or rest[0] == "-":
        return None
    if language == "deno":
        if rest[0] == "eval" and len(rest) > 1:
            return _Execution("inline", "deno eval", (("<inline>", rest[1]),), base)
        rest = rest[1:] if rest[0] == "run" else rest
        rest = [a for a in rest if not a.startswith("-")]
    if language == "bun":
        if rest[0] == "run":
            return _bun_run(rest[1:], base)
        if rest[0] in _BUN_COMMANDS:
            return None
        if not _resolve(rest[0], base):
            return _package_script("bun", rest, base)
    return _script(rest[0], base, "script-file") if rest else None


def _bun_run(args: List[str], base: Path):
    directory, positional = _directory_and_positionals(args, base, limit=1)
    if directory is None or not positional:
        return None
    if _resolve(positional[0], directory):
        return _script(positional[0], directory, "script-file")
    return _package_body("bun", directory, positional[0])


def _directory_and_positionals(args: List[str], base: Path, limit: int) -> Tuple[Optional[Path], List[str]]:
    directory, positional, index = base, [], 0
    while index < len(args) and len(positional) < limit:
        arg = args[index]
        flag, has_value, attached = arg.partition("=")
        if flag in _PACKAGE_UNRESOLVED_FLAGS:
            return None, []
        if flag in _PACKAGE_DIR_FLAGS:
            value = attached if has_value else (args[index + 1] if index + 1 < len(args) else "")
            directory = Path(os.path.expanduser(value)) if os.path.isabs(os.path.expanduser(value)) else directory / value
            index += 1 if has_value else 2
            continue
        if not arg.startswith("-"):
            positional.append(arg)
        index += 1
    return directory, positional


def _package_script(manager: str, args: List[str], base: Path):
    directory, positional = _directory_and_positionals(args, base, limit=2)
    if directory is None or not positional:
        return None
    head = positional[0]
    if head in _NPM_RUN or (manager != "npm" and head == "run"):
        return _package_body(manager, directory, positional[1]) if len(positional) > 1 else None
    if head in _LIFECYCLE_SCRIPTS:
        return _package_body(manager, directory, _LIFECYCLE_SCRIPTS[head])
    if manager == "npm" or head in _MANAGER_BUILTINS:
        return None
    return _package_body(manager, directory, head)


def _package_body(manager: str, directory: Path, script: str):
    try:
        scripts = json.loads((directory / "package.json").read_text(encoding="utf-8")).get("scripts") or {}
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(scripts, dict) or not isinstance(scripts.get(script), str):
        return None
    names = (f"pre{script}", script, f"post{script}")
    texts = tuple((f"package.json:scripts.{n}", scripts[n]) for n in names if isinstance(scripts.get(n), str))
    return _Execution("package-script", f"{manager} {script}", texts, directory)


def _make(args: List[str], base: Path):
    directory, makefile, targets, index = base, None, [], 0
    while index < len(args):
        arg = args[index]
        if arg in _MAKE_NO_RUN:
            return None
        if arg in ("-j", "-l", "--jobs") and index + 1 < len(args) and args[index + 1].isdigit():
            index += 2
            continue
        if arg in ("-C", "--directory", "-f", "--file", "--makefile") and index + 1 < len(args):
            if arg in ("-C", "--directory"):
                directory = directory / args[index + 1]
            else:
                makefile = args[index + 1]
            index += 2
            continue
        if not arg.startswith("-") and "=" not in arg:
            targets.append(arg)
        index += 1
    names = [makefile] if makefile else ["GNUmakefile", "makefile", "Makefile"]
    path = next((p for p in (_resolve(n, directory) for n in names) if p is not None), None)
    body = _read(path) if path else None
    if body is None:
        return None
    return _Execution("makefile", f"make {' '.join(targets)}".strip(), ((str(path), _make_recipes(body, targets)),), directory)


def _make_recipes(text: str, targets: List[str]) -> str:
    """The recipes of *targets* (default: the first) and of their direct prerequisites; the whole file when a target is implicit."""
    rules: dict = {}
    order: List[str] = []
    current: List[str] = []
    for line in text.splitlines():
        if line.startswith("\t"):
            for name in current:
                rules[name][1].append(line[1:])
            continue
        match = None if _MAKE_DIRECTIVE.match(line) else _MAKE_RULE.match(line)
        if match is None:
            current = [] if line.strip() and not line.lstrip().startswith("#") else current
            continue
        current = match.group(1).split()
        for name in current:
            rules.setdefault(name, ([], []))[0].extend(match.group(2).split())
            if match.group(3):
                rules[name][1].append(match.group(3))
            if not name.startswith(".") and name not in order:
                order.append(name)
    wanted = targets or order[:1]
    if not wanted or any(target not in rules for target in wanted):
        return text
    lines: List[str] = []
    for target in wanted:
        lines.extend(rules[target][1])
        for prerequisite in rules[target][0]:
            lines.extend(rules.get(prerequisite, ([], []))[1])
    return "\n".join(lines)


def _just(args: List[str], base: Path):
    directory, justfile, recipe, index = base, None, None, 0
    while index < len(args):
        arg = args[index]
        if arg in _JUST_NO_RUN:
            return None
        if arg in ("-f", "--justfile", "-d", "--working-directory") and index + 1 < len(args):
            if arg in ("-f", "--justfile"):
                justfile = args[index + 1]
            else:
                directory = directory / args[index + 1]
            index += 2
            continue
        if not arg.startswith("-") and "=" not in arg:
            recipe = arg
            break
        index += 1
    names = [justfile] if justfile else ["justfile", "Justfile", ".justfile", "JUSTFILE"]
    path = next((p for p in (_resolve(n, directory) for n in names) if p is not None), None)
    body = _read(path) if path else None
    if body is None:
        return None
    return _Execution("justfile", f"just {recipe or ''}".strip(), ((str(path), _just_recipe(body, recipe)),), directory)


def _just_recipe(text: str, recipe: Optional[str]) -> str:
    """The body of *recipe* (default: the first) and of its direct dependencies; the whole file when it is not found."""
    bodies: dict = {}
    order: List[str] = []
    current: Optional[str] = None
    for line in text.splitlines():
        if line[:1] in (" ", "\t"):
            if current:
                bodies[current][1].append(line.strip())
            continue
        match = None if line.lstrip().startswith("#") else _JUST_RECIPE.match(line)
        current = match.group(1) if match else None
        if current:
            bodies[current] = ([w for w in match.group(2).split() if re.fullmatch(r"[\w-]+", w)], [])
            order.append(current)
    name = recipe or (order[0] if order else None)
    if name not in bodies:
        return text
    lines = list(bodies[name][1])
    for dependency in bodies[name][0]:
        lines.extend(bodies.get(dependency, ([], []))[1])
    return "\n".join(lines)


def _go_run(args: List[str], base: Path):
    directory, index = base, 0
    while index < len(args) and args[index].startswith("-"):
        flag = args[index].split("=", 1)[0]
        if flag in ("-exec", "-toolexec"):
            return None
        if flag == "-C" and "=" not in args[index] and index + 1 < len(args):
            directory = directory / args[index + 1]
        index += 2 if (flag in _GO_VALUE_FLAGS or flag == "-C") and "=" not in args[index] else 1
    if index >= len(args):
        return None
    target = args[index]
    if "@" in target or (not target.startswith((".", "/")) and "." in target.split("/")[0]):
        return ScanVerdict("go-run", f"go run {target}", "download-and-run", f"go run {target}: a module fetched to run",
                           "sign.foreign-code")
    if target.endswith(".go"):
        files = [p for p in (_resolve(a, directory) for a in args[index:] if a.endswith(".go")) if p is not None]
    else:
        package = directory / target
        files = sorted(p for p in package.glob("*.go") if not p.name.endswith("_test.go"))[:64] if package.is_dir() else []
    texts = tuple((str(p), body) for p in files for body in (_read(p),) if body is not None)
    return _Execution("go-run", f"go run {target}", texts, directory) if texts else None


def _cargo_run(args: List[str], base: Path):
    directory, binary, index = base, None, 0
    while index < len(args) and args[index] != "--":
        flag, has_value, attached = args[index].partition("=")
        if flag in ("-p", "--package", "--example", "--examples"):
            return None
        if flag in ("--manifest-path", "--bin"):
            value = attached if has_value else (args[index + 1] if index + 1 < len(args) else "")
            if flag == "--bin":
                binary = value
            else:
                directory = (directory / value).parent
            index += 1 if has_value else 2
            continue
        index += 2 if flag in _CARGO_VALUE_FLAGS and not has_value else 1
    candidates = ["build.rs"] + ([f"src/bin/{binary}.rs", f"src/bin/{binary}/main.rs"] if binary else ["src/main.rs"])
    files = [p for p in (_resolve(c, directory) for c in candidates) if p is not None]
    texts = tuple((str(p), body) for p in files for body in (_read(p),) if body is not None)
    return _Execution("cargo-run", "cargo run", texts, directory) if texts else None
