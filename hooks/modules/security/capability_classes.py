r"""
Capability classes for shell commands.

A *capability class* is a group of CLI binaries that share the same risk
profile because they all expose the same kind of side effect.  For example,
every database CLI (``sqlite3``, ``psql``, ``mysql``, ``mongosh``) can apply
arbitrary mutations when given a SQL file or an inline mutative statement,
regardless of the specific verb syntax of each tool.

Without this layer the bash validator has to carry a separate rule for every
binary -- and the verb scanner cannot help, because tools like ``sqlite3``
accept *the entire mutation language* as a single argument.  The verb scan
saw ``sqlite3 /home/jorge/.gaia/gaia.db < /tmp/migration_all.sql`` as a
non-mutative command and let 856 INSERTs through.

The model
=========

Each entry in :data:`CAPABILITY_CLASSES` is a dict with:

* ``verbs`` -- frozenset of base CLI tokens that belong to the class.
* ``default_intent`` -- the safety category to apply when no override
  matches.  Currently always ``MUTATIVE`` so that approval is the default
  and read-only is the exception.
* ``readonly_overrides`` -- a tuple of override rules.  Each rule is a dict
  with one of:

  - ``flag`` -- a single flag token (e.g. ``-readonly``) that, when present
    in the command tokens, downgrades the intent to read-only.
  - ``inline_command_pattern`` -- a compiled regex that, when matched
    against the inline payload of a flag-pair like ``-c "SQL"`` /
    ``-e "SQL"`` / ``--eval "JS"``, downgrades the intent to read-only.
    The pattern is matched conservatively: only literal SELECT / EXPLAIN /
    safe PRAGMA prefixes count.

Resolution rules (Nivel 1)
==========================

When the command's base CLI is in a capability class, classification works
as follows:

1. If a redirect-input token (``<``) or a pipe-input is present, the
   payload is considered external and uninspected -- keep MUTATIVE.
2. If a positional argument starts with a sqlite-style dot-command that
   loads or executes a script / writes to disk (``.read``, ``.import``,
   ``.restore``, ``.clone``, ``.load``, ``.system``, ``.shell``, ``.save``),
   keep MUTATIVE.
3. If every dot-command present is a strictly read-only sqlite3 schema /
   metadata command (``.schema``, ``.tables``, ``.databases``,
   ``.indexes`` / ``.indices``, ``.dbinfo``, ``.show``, ``.fullschema``),
   classify as READ_ONLY.  This check runs *after* rule 2, so the
   write-capable dot-commands above are caught first and never downgraded;
   ``.dump`` / ``.output`` / ``.once`` / ``.backup`` are deliberately left
   out of the read-only set (conservative) and fall through to MUTATIVE.
4. If the CLI is ``psql`` and every inline payload is read-only with at
   least one being a read-only backslash meta-command (``\l``, ``\dt``,
   ``\du``, ``\d*``, ``\z``, ...), classify as READ_ONLY.  Meta-commands
   that execute or write (``\i``, ``\o``, ``\copy``, ``\!``) are absent from
   the allow-list and fall through.
5. If a flag override matches (e.g. ``-readonly``), classify as READ_ONLY.
6. If the command exposes an inline payload via a recognised flag pair
   (``-c``, ``-e``, ``--eval``) and the payload matches the read-only
   regex, classify as READ_ONLY.
7. Otherwise return ``default_intent`` (MUTATIVE).

A future Nivel 2 (`sql_payload_analyzer.py`) will parse external SQL files
and inline payloads into an AST and downgrade more cases -- e.g., a file
that contains only SELECT statements.  This dispatch deliberately stays
out of that work; it returns MUTATIVE whenever the payload is external.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, Mapping, Optional, Tuple

from .command_semantics import CommandSemantics


# ============================================================================
# Public constants
# ============================================================================

CATEGORY_MUTATIVE = "MUTATIVE"
CATEGORY_READ_ONLY = "READ_ONLY"

#: Pattern for SQL statements that are demonstrably read-only.  Matched
#: against the leading tokens of an inline payload.  Conservative on
#: purpose -- when in doubt, MUTATIVE wins.
_SQL_READONLY_PREFIX = re.compile(
    r"^\s*(SELECT|EXPLAIN|WITH\s+\w+\s+AS|PRAGMA\s+"
    r"(table_info|table_xinfo|index_list|index_info|database_list|schema_version|"
    r"foreign_key_list|quick_check|integrity_check|user_version|"
    r"compile_options|encoding|page_count|page_size))\b",
    re.IGNORECASE,
)

#: Characters an inline psql meta-command argument may NOT contain.
#:
#: Two groups, for two different reasons:
#:
#:   * ``\;|&`$<>`` -- shell metacharacters and psql's own statement
#:     separator.  ``psql -c '\dt; DROP TABLE t'`` must not read as read-only.
#:   * ``\x00-\x1f\x7f\x85\u2028\u2029`` -- every C0 control (which is where
#:     ``\n``, ``\r``, ``\v`` and ``\f`` live), DEL, the C1 NEL, and the
#:     unicode line/paragraph separators.  A LINE BREAK is a statement
#:     separator exactly as ``;`` is: psql's lexer terminates a meta-command
#:     at ``newline [\n\r]`` (``psqlscan.l``), and since PostgreSQL 10 one
#:     ``-c`` string may mix meta-commands with SQL and run BOTH.
#:
#: The second group is written as a RANGE rather than an enumeration of the
#: break characters someone thought of, so a splitter nobody listed cannot
#: appear in the argument at all.  Non-ASCII printable text is still allowed:
#: a table named ``a\u00f1o`` is a legitimate ``\dt`` pattern.
#:
#: Spelled with escapes on purpose -- U+2028 / U+2029 are invisible in a
#: source file and must never be pasted literally into one.
_PSQL_META_ARG_FORBIDDEN = r"\\;|&`$<>\x00-\x1f\x7f\x85\u2028\u2029"

#: Pattern for a psql BACKSLASH META-COMMAND that only introspects.  psql's
#: meta-commands are not SQL, so ``_SQL_READONLY_PREFIX`` never matches them
#: and every ``psql -c '\dt'`` fell through to the MUTATIVE default.
#:
#: The allowed set is:
#:   * the whole ``\d`` family (``\d``, ``\dt``, ``\du``, ``\dn``, ``\di``,
#:     ``\df``, ``\dRp``, ...) -- every ``\d*`` command in psql is a DESCRIBE,
#:     including the ``+`` verbose suffix;
#:   * ``\l`` / ``\list`` (list databases), ``\z`` (access privileges),
#:     ``\sf`` / ``\sv`` (show a function / view definition),
#:     ``\conninfo``, and the help forms ``\?`` / ``\h`` / ``\help``.
#:
#: Everything that executes or writes is absent by construction (allow-list,
#: not deny-list): ``\i`` / ``\ir`` (run a script), ``\o`` (redirect output to
#: a file or a command), ``\copy`` (bulk load/unload), ``\!`` (shell), ``\g``
#: with a target, ``\gexec`` / ``\gset`` / ``\watch``.
#:
#: An OPTIONAL argument is allowed (``\dt public.*``) but is constrained by
#: ``_PSQL_META_ARG_FORBIDDEN``.
#:
#: WHITESPACE RUNS ARE HORIZONTAL-ONLY (``[ \t]``, never ``\s``), and that is
#: the load-bearing half of the line-break defense -- not the character class.
#: Python's ``\s`` matches ``\n``, ``\r``, ``\v``, ``\f``, ``\x85``, U+2028 and
#: U+2029, so a ``\s+`` separator swallows the break ITSELF, after which the
#: argument class only has to absorb ordinary text.  Measured: with ``\s+``
#: retained, excluding the break characters from the class closes NONE of the
#: break payloads -- ``\s+`` is greedy and eats ``" \n"`` in one bite.
#:
#: The terminating anchor is ``\Z``, not ``$``: without ``re.MULTILINE`` a
#: ``$`` still matches BEFORE a trailing newline.  The caller strips the
#: payload first, so this is defense in depth -- the pattern must hold alone.
#:
#: Still allowed, and correct: text after the command on the SAME line
#: (``\dt DROP TABLE users``).  psql reads it as the meta-command's pattern
#: argument, never as a statement -- the same behaviour as ``\dt public.*``.
_PSQL_READONLY_META_COMMAND = re.compile(
    r"^[ \t]*\\(?:d[a-zA-Z]*\+?|l(?:ist)?\+?|z\+?|sf\+?|sv\+?|conninfo|\?|h|help)"
    rf"(?:[ \t]+[^{_PSQL_META_ARG_FORBIDDEN}]*)?[ \t]*\Z"
)

#: Pattern for mongosh / nodejs-style payloads that only read.  ``find``,
#: ``findOne``, ``aggregate`` (read-only by default), ``count*``, ``stats``,
#: ``getCollection`` chained with a read.  Insert / update / delete / drop /
#: replace / save anywhere in the payload keeps it MUTATIVE.
_JS_READONLY_PATTERN = re.compile(
    r"^\s*db(?:\.\w+)*\.(?:find(?:One)?|count(?:Documents)?|"
    r"estimatedDocumentCount|aggregate|distinct|stats|getCollection)\s*\(",
    re.IGNORECASE,
)
_JS_MUTATIVE_KEYWORDS = re.compile(
    r"\.(?:insert(?:One|Many)?|update(?:One|Many)?|delete(?:One|Many)?|"
    r"replaceOne|drop(?:Database|Index|Indexes)?|remove|save|"
    r"createIndex|createCollection|renameCollection|bulkWrite)\s*\(",
    re.IGNORECASE,
)

#: SQLite "dot-commands" (positional arguments starting with ``.``) that
#: load or execute external scripts.  Keep these MUTATIVE even without a
#: shell redirect, because the payload is still external.
_SQLITE_MUTATIVE_DOT_COMMANDS: FrozenSet[str] = frozenset({
    ".read", ".import", ".restore", ".clone", ".load", ".system", ".shell", ".save",
})

#: SQLite dot-commands that are strictly read-only schema/metadata introspection.
#: These produce no side effects on the database file and write nothing to disk.
#:
#: NOT included (remain MUTATIVE):
#:   .import, .restore, .backup, .clone, .save  -- write to db/file
#:   .read                                       -- executes an arbitrary script
#:   .output / .once                             -- redirects output to a file
#:   .load                                       -- loads a native extension (exec)
#:   .system / .shell                            -- arbitrary OS command execution
#:   .dump                                       -- NOT included: commonly piped to
#:                                                  files and by default prints the
#:                                                  full db; conservative exclusion.
_SQLITE_READONLY_DOT_COMMANDS: FrozenSet[str] = frozenset({
    ".schema",      # prints CREATE statements for tables/indexes
    ".tables",      # lists tables in the database
    ".databases",   # lists attached databases
    ".indexes",     # lists indexes for a table or all tables
    ".indices",     # alias for .indexes
    ".dbinfo",      # prints low-level metadata about the db file
    ".show",        # prints current settings (not data)
    ".fullschema",  # prints CREATE statements including schema_table
})

#: Tokens shlex emits for unquoted shell redirects.  Their presence in the
#: positional argument stream means the inline command was fed from an
#: external source -- the payload is uninspected at Nivel 1.
_REDIRECT_INPUT_TOKENS: FrozenSet[str] = frozenset({"<", "<<", "<<<"})


# ============================================================================
# Capability class registry
# ============================================================================

#: Every entry maps a class name to its rule set.  Adding a new class is the
#: only intended way to extend this layer -- the resolver below is class-
#: agnostic.
#:
#: TODO(Nivel 2): a follow-up ``sql_payload_analyzer.py`` will parse
#: external ``.sql`` files and inline payloads and downgrade more cases.
#: This module does not perform that analysis -- when the payload is
#: external (redirect / pipe / dot-command load), it stays MUTATIVE here.
CAPABILITY_CLASSES: Dict[str, Dict[str, object]] = {
    "database_cli": {
        "verbs": frozenset({
            "sqlite3", "sqlite",
            "psql",
            "mysql", "mariadb",
            "mongo", "mongosh",
            "redis-cli",
            "cqlsh",
            "duckdb",
        }),
        "default_intent": CATEGORY_MUTATIVE,
        "readonly_overrides": (
            # Flag-based overrides.
            {"flag": "-readonly"},
            {"flag": "--readonly"},
            # Inline-payload overrides.  The matchers run against the
            # payload of a recognised flag pair (-c / -e / --eval).
            {"inline_command_pattern": _SQL_READONLY_PREFIX},
            {"inline_command_pattern": _JS_READONLY_PATTERN,
             "deny_pattern": _JS_MUTATIVE_KEYWORDS},
        ),
        # Flags that pair with an inline payload.  When one of these is
        # present, the next token is the payload to inspect.
        "_inline_payload_flags": frozenset({
            "-c", "--command",      # psql, mysql --execute, redis-cli (alt)
            "-e", "--execute",      # mysql, mariadb
            "--eval",               # mongosh, mongo
        }),
    },
}


# ============================================================================
# Result type
# ============================================================================

@dataclass(frozen=True)
class CapabilityResult:
    """Outcome of capability-class classification.

    ``matched`` is True only when the base CLI belongs to a capability
    class -- callers should fall through to the regular verb scanner when
    it is False.
    """

    matched: bool = False
    capability_class: str = ""
    intent: str = ""        # CATEGORY_MUTATIVE / CATEGORY_READ_ONLY
    reason: str = ""
    matched_flag: str = ""
    inline_payload: str = ""


_NO_MATCH = CapabilityResult(matched=False)


# ============================================================================
# Lookup helpers
# ============================================================================

def _verb_to_class() -> Mapping[str, str]:
    """Reverse index from verb -> capability class name.

    Built once at import time.  If a verb appears in two classes (which the
    design forbids), the last one wins; the assertion in this function
    catches that mistake at import time so it cannot leak to runtime.
    """
    index: Dict[str, str] = {}
    for class_name, spec in CAPABILITY_CLASSES.items():
        verbs: Iterable[str] = spec["verbs"]  # type: ignore[assignment]
        for verb in verbs:
            assert verb not in index, (
                f"Capability verb collision: '{verb}' is in both "
                f"'{index[verb]}' and '{class_name}'"
            )
            index[verb] = class_name
    return index


VERB_TO_CLASS: Mapping[str, str] = _verb_to_class()


def is_capability_verb(base_cmd: str) -> bool:
    """Return True when ``base_cmd`` belongs to any capability class."""
    return base_cmd in VERB_TO_CLASS


# ============================================================================
# Inline payload extraction
# ============================================================================

def _extract_inline_payloads(
    tokens: Tuple[str, ...],
    payload_flags: FrozenSet[str],
) -> Tuple[str, ...]:
    """Return the list of payloads that follow a recognised flag pair.

    For ``mysql -e "SELECT 1"`` the tokens after shlex are
    ``("mysql", "-e", "SELECT 1")`` -- the payload is the token immediately
    after ``-e``.  Equals-style flags (``--eval=foo``) are also supported.
    """
    payloads = []
    for i, tok in enumerate(tokens):
        if "=" in tok and tok.split("=", 1)[0] in payload_flags:
            payloads.append(tok.split("=", 1)[1])
            continue
        if tok in payload_flags and i + 1 < len(tokens):
            payloads.append(tokens[i + 1])
    return tuple(payloads)


def _has_redirect_input(tokens: Tuple[str, ...]) -> bool:
    """Return True when an unquoted ``<`` redirect appears in the tokens.

    shlex preserves ``<`` and ``<<`` as plain tokens because we tokenize
    without ``posix=True``'s redirect collapsing.  When such a token is
    present, the payload is being read from an external source and Nivel 1
    cannot inspect it -- the command must stay MUTATIVE.
    """
    return any(t in _REDIRECT_INPUT_TOKENS for t in tokens)


def _has_sqlite_load_dot_command(tokens: Tuple[str, ...]) -> bool:
    """Return True when a positional argument is a sqlite3 dot-command that
    loads or executes an external script."""
    for tok in tokens:
        # Strip wrapping quotes -- shlex usually removes them, but defensive.
        stripped = tok.strip().strip('"').strip("'")
        first_word = stripped.split(None, 1)[0] if stripped else ""
        if first_word.lower() in _SQLITE_MUTATIVE_DOT_COMMANDS:
            return True
    return False


def _psql_inline_payloads_readonly(payloads: Tuple[str, ...]) -> bool:
    """Return True when ``psql`` inline payloads are read-only AND at least
    one of them is a backslash meta-command.

    Requiring EVERY payload to be read-only is what keeps
    ``psql -c '\\dt' -c 'DROP TABLE t'`` mutative: psql accepts repeated
    ``-c``, so a single read-only meta-command must never launder the rest of
    the invocation.  Returns False when no meta-command is present so a plain
    SQL payload keeps going through the ordinary inline-payload rule.
    """
    saw_meta_command = False
    for payload in payloads:
        stripped = payload.strip()
        if stripped.startswith("\\"):
            if not _PSQL_READONLY_META_COMMAND.match(stripped):
                return False
            saw_meta_command = True
        elif not _SQL_READONLY_PREFIX.match(stripped):
            return False
    return saw_meta_command


def _has_sqlite_readonly_dot_command(tokens: Tuple[str, ...]) -> bool:
    """Return True when ALL dot-commands present in the tokens are
    strictly read-only schema/metadata commands.

    Returns False (falls through) when no dot-command is present so the
    regular inline-payload and default rules continue to apply.
    Returns False when a dot-command outside the read-only allowlist is
    found -- the caller should treat those as MUTATIVE.
    """
    dot_cmds_found = []
    for tok in tokens:
        stripped = tok.strip().strip('"').strip("'")
        first_word = stripped.split(None, 1)[0] if stripped else ""
        if first_word.startswith("."):
            dot_cmds_found.append(first_word.lower())

    if not dot_cmds_found:
        return False

    # Every dot-command present must be in the read-only set.
    return all(cmd in _SQLITE_READONLY_DOT_COMMANDS for cmd in dot_cmds_found)


# ============================================================================
# Main entry point
# ============================================================================

def classify_capability(semantics: CommandSemantics) -> CapabilityResult:
    r"""Classify a command via its capability class, when applicable.

    Returns :data:`_NO_MATCH` (``matched=False``) when the base CLI is not
    in any capability class -- the caller should fall through to the
    regular verb scanner in that case.

    Resolution order (mirrors module docstring):

    1. External payload (redirect ``<``) -> MUTATIVE.
    1b. sqlite write-capable dot-command (``.read`` / ``.import`` /
        ``.restore`` / ``.clone`` / ``.load`` / ``.system`` / ``.shell`` /
        ``.save``) -> MUTATIVE.
    1c. sqlite read-only schema/metadata dot-command (``.schema`` /
        ``.tables`` / ``.databases`` / ``.indexes`` / ``.indices`` /
        ``.dbinfo`` / ``.show`` / ``.fullschema``) -> READ_ONLY.  Runs after
        1b so write-capable dot-commands are never downgraded; ``.dump`` /
        ``.output`` / ``.once`` / ``.backup`` are excluded (conservative)
        and fall through to the default.
    1d. psql read-only backslash meta-command (``\l`` / ``\dt`` / ``\du`` /
        ``\d*`` / ``\z`` / ...) in EVERY inline payload -> READ_ONLY.
        Execute/write meta-commands (``\i`` / ``\o`` / ``\copy`` / ``\!``)
        are not in the allow-list and fall through to the default.
    2. Flag override -> READ_ONLY.
    3. Inline-payload override -> READ_ONLY.
    4. Default -> ``default_intent`` (always MUTATIVE today).
    """
    base_cmd = semantics.base_cmd
    class_name = VERB_TO_CLASS.get(base_cmd)
    if class_name is None:
        return _NO_MATCH

    spec = CAPABILITY_CLASSES[class_name]
    default_intent: str = spec["default_intent"]  # type: ignore[assignment]
    overrides = spec.get("readonly_overrides", ())  # type: ignore[assignment]
    payload_flags: FrozenSet[str] = spec.get(
        "_inline_payload_flags", frozenset()
    )  # type: ignore[assignment]

    tokens = semantics.tokens

    # --- Rule 1: external payload keeps MUTATIVE -----------------------------
    if _has_redirect_input(tokens):
        return CapabilityResult(
            matched=True,
            capability_class=class_name,
            intent=CATEGORY_MUTATIVE,
            reason=(
                f"{class_name}: redirect input detected -- external payload "
                "not inspected at Nivel 1"
            ),
        )

    if base_cmd in {"sqlite3", "sqlite"} and _has_sqlite_load_dot_command(tokens):
        return CapabilityResult(
            matched=True,
            capability_class=class_name,
            intent=CATEGORY_MUTATIVE,
            reason=(
                f"{class_name}: sqlite dot-command loads an external script "
                "(.read / .import / .restore)"
            ),
        )

    # --- Rule 1c: sqlite read-only dot-commands -> READ_ONLY ----------------
    # Must run after the mutative-dot-command check so that write-capable
    # dot-commands (.read, .import, ...) are never downgraded here.
    if base_cmd in {"sqlite3", "sqlite"} and _has_sqlite_readonly_dot_command(tokens):
        return CapabilityResult(
            matched=True,
            capability_class=class_name,
            intent=CATEGORY_READ_ONLY,
            reason=(
                f"{class_name}: sqlite dot-command is a read-only schema/metadata "
                "introspection command (.schema / .tables / .databases / ...)"
            ),
        )

    # --- Rule 1d: psql read-only meta-commands -> READ_ONLY -----------------
    # Only the inline payloads are inspected (never bare positionals): for
    # psql a positional is the dbname/username, not a command, so widening the
    # candidate set would let an unrelated token decide the tier.
    if base_cmd == "psql":
        psql_payloads = _extract_inline_payloads(tokens, payload_flags)
        if _psql_inline_payloads_readonly(psql_payloads):
            return CapabilityResult(
                matched=True,
                capability_class=class_name,
                intent=CATEGORY_READ_ONLY,
                reason=(
                    f"{class_name}: psql meta-command is read-only "
                    "introspection (\\l / \\dt / \\du / \\d* / \\z / ...)"
                ),
                inline_payload=psql_payloads[0] if psql_payloads else "",
            )

    # --- Rule 2: flag-based overrides ---------------------------------------
    flag_overrides = [
        rule["flag"] for rule in overrides
        if isinstance(rule, dict) and "flag" in rule
    ]
    for flag in flag_overrides:
        if flag in tokens:
            return CapabilityResult(
                matched=True,
                capability_class=class_name,
                intent=CATEGORY_READ_ONLY,
                reason=f"{class_name}: read-only flag '{flag}' present",
                matched_flag=flag,
            )

    # --- Rule 3: inline-payload overrides -----------------------------------
    inline_overrides = [
        rule for rule in overrides
        if isinstance(rule, dict) and "inline_command_pattern" in rule
    ]
    if inline_overrides:
        # Inline payloads can come from the flag pair (mongosh --eval ...,
        # psql -c ...) OR from a bare positional (sqlite3 db "SELECT ...").
        candidate_payloads = list(_extract_inline_payloads(tokens, payload_flags))
        # Add bare positional candidates for sqlite-style usage.
        # sqlite3 takes "<db> <command>" as positional args; the second
        # positional looks like SQL.  We add ALL non-flag-looking positionals
        # so that every candidate payload gets a chance against the regex.
        for tok in semantics.non_flag_tokens:
            if tok and not tok.startswith("-"):
                candidate_payloads.append(tok)

        for payload in candidate_payloads:
            for rule in inline_overrides:
                pattern = rule["inline_command_pattern"]
                deny = rule.get("deny_pattern")
                if pattern.search(payload) and not (
                    deny is not None and deny.search(payload)
                ):
                    return CapabilityResult(
                        matched=True,
                        capability_class=class_name,
                        intent=CATEGORY_READ_ONLY,
                        reason=(
                            f"{class_name}: inline payload matches read-only "
                            "pattern"
                        ),
                        inline_payload=payload,
                    )

        # If we extracted a payload but none matched read-only, the inline
        # statement is presumed mutative -- fall through to default below.

    # --- Rule 4: default -----------------------------------------------------
    return CapabilityResult(
        matched=True,
        capability_class=class_name,
        intent=default_intent,
        reason=(
            f"{class_name}: default intent {default_intent.lower()} -- "
            "no read-only override matched"
        ),
    )
