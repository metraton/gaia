"""
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
   loads a script (``.read``, ``.import``, ``.restore``), keep MUTATIVE.
3. If a flag override matches (e.g. ``-readonly``), classify as READ_ONLY.
4. If the command exposes an inline payload via a recognised flag pair
   (``-c``, ``-e``, ``--eval``) and the payload matches the read-only
   regex, classify as READ_ONLY.
5. Otherwise return ``default_intent`` (MUTATIVE).

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


# ============================================================================
# Main entry point
# ============================================================================

def classify_capability(semantics: CommandSemantics) -> CapabilityResult:
    """Classify a command via its capability class, when applicable.

    Returns :data:`_NO_MATCH` (``matched=False``) when the base CLI is not
    in any capability class -- the caller should fall through to the
    regular verb scanner in that case.

    Resolution order (mirrors module docstring):

    1. External payload (redirect ``<`` or sqlite ``.read``-style command)
       -> MUTATIVE.
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
