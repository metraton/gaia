"""The provable read subset of sqlite3 input: dot-commands and SQL, judged by allowlist."""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

DOT_COMMANDS = frozenset({
    "tables", "schema", "fullschema", "headers", "mode", "indexes", "indices", "dump",
    "databases", "show", "print", "width", "nullvalue", "separator", "timer", "changes",
    "stats", "help", "quit", "exit", "echo", "eqp", "explain", "bail",
})

_TOKEN = re.compile(
    r"(?P<space>\s+)|(?P<comment>--[^\n]*|/\*.*?\*/)|(?P<string>[xX]?'(?:[^']|'')*')"
    r"|(?P<quoted>\"(?:[^\"]|\"\")*\"|\[[^\]]*\]|`(?:[^`]|``)*`)"
    r"|(?P<number>0[xX][0-9a-fA-F]+|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)"
    r"|(?P<word>[A-Za-z_][A-Za-z0-9_$]*)"
    r"|(?P<unclosed>/\*)|(?P<op>\|\||->>|->|<=|>=|==|!=|<>|<<|>>|[(),;.+\-*/%<>=&|~])",
    re.S,
)
_STATEMENTS = frozenset({
    "SELECT", "VALUES", "WITH", "INSERT", "REPLACE", "UPDATE", "DELETE", "CREATE", "DROP",
    "PRAGMA", "BEGIN", "COMMIT", "END", "ROLLBACK",
})
_CREATED = frozenset({"TABLE", "INDEX", "VIEW"})
_PRAGMA_ARGUMENT_READS = frozenset({
    "table_info", "table_xinfo", "index_info", "index_xinfo", "index_list", "foreign_key_list",
    "foreign_key_check", "integrity_check", "quick_check", "table_list",
})
_PRAGMA_READS = _PRAGMA_ARGUMENT_READS | frozenset({
    "user_version", "schema_version", "application_id", "page_count", "page_size",
    "freelist_count", "encoding", "journal_mode", "database_list", "compile_options",
    "function_list", "module_list", "pragma_list", "collation_list", "foreign_keys", "data_version",
})
_FUNCTIONS = frozenset({
    "abs", "avg", "char", "coalesce", "concat", "concat_ws", "count", "date", "datetime",
    "format", "glob", "group_concat", "hex", "ifnull", "iif", "instr", "julianday", "length",
    "like", "likelihood", "likely", "lower", "ltrim", "max", "min", "nullif", "octet_length",
    "printf", "quote", "random", "randomblob", "replace", "round", "rtrim", "sign", "strftime",
    "string_agg", "substr", "substring", "sum", "time", "timediff", "total", "trim", "typeof",
    "unhex", "unicode", "unixepoch", "unlikely", "upper", "zeroblob", "ceil", "ceiling", "floor",
    "exp", "ln", "log", "log10", "log2", "mod", "pi", "pow", "power", "sqrt", "row_number",
    "rank", "dense_rank", "percent_rank", "cume_dist", "ntile", "lag", "lead", "first_value",
    "last_value", "nth_value", "json", "json_array", "json_array_length", "json_extract",
    "json_group_array", "json_group_object", "json_object", "json_quote", "json_type",
    "json_valid", "sqlite_version", "last_insert_rowid", "changes", "total_changes",
})
_KEYWORDS = frozenset("""
ABORT ACTION ADD AFTER ALL ALTER ALWAYS AND AS ASC BEFORE BETWEEN BY CASCADE CASE CAST CHECK
COLLATE COLUMN CONFLICT CONSTRAINT CREATE CROSS CURRENT DEFAULT DEFERRABLE DEFERRED DELETE DESC
DISTINCT DO DROP ELSE END ESCAPE EXCEPT EXCLUDE EXISTS FAIL FILTER FIRST FOLLOWING FOR FOREIGN
FROM FULL GENERATED GLOB GROUP GROUPS HAVING IF IGNORE IMMEDIATE IN INDEX INITIALLY INNER INSERT
INTERSECT INTO IS ISNULL JOIN KEY LAST LEFT LIKE LIMIT MATCH MATERIALIZED NATURAL NO NOT NOTHING
NOTNULL NULL NULLS OF OFFSET ON OR ORDER OTHERS OUTER OVER PARTITION PRECEDING PRIMARY RAISE RANGE
RECURSIVE REFERENCES REGEXP REPLACE RESTRICT RETURNING RIGHT ROW ROWS SELECT SET STRICT TABLE THEN
TIES TO UNBOUNDED UNION UNIQUE UPDATE USING VALUES VIEW WHEN WHERE WINDOW WITH WITHOUT
""".split())
_VIRTUAL_SOURCES = frozenset({
    "fsdir", "zipfile", "sqlar", "sqlite_dbpage", "sqlite_dbdata", "sqlite_dbptr", "dbstat",
    "sqlite_stmt", "json_each", "json_tree", "generate_series", "completion", "carray",
})
_SOURCE_KEYWORDS = frozenset({"FROM", "JOIN", "INTO", "TABLE", "UPDATE", "VIEW", "INDEX"})
_SOURCE_END = frozenset({
    "WHERE", "GROUP", "HAVING", "ORDER", "LIMIT", "WINDOW", "UNION", "EXCEPT", "INTERSECT",
    "ON", "USING", "SELECT", "VALUES", "SET", "RETURNING",
})
_NAMED_COLUMNS_AFTER = frozenset({"TABLE", "EXISTS", "ON", "REFERENCES", "VIEW", "INTO"})


def _tokens(sql: str) -> Tuple[List[Tuple[str, str]], Optional[str]]:
    tokens, index = [], 0
    while index < len(sql):
        match = _TOKEN.match(sql, index)
        if not match:
            return tokens, f"the SQL text `{sql[index:index + 20].strip()}`"
        index = match.end()
        if match.lastgroup not in ("space", "comment"):
            tokens.append((match.lastgroup, match.group(0)))
    return tokens, None


def _name(token: Tuple[str, str]) -> str:
    kind, text = token
    return text[1:-1] if kind == "quoted" else text


def _statement_part(statement: List[Tuple[str, str]]) -> Optional[str]:
    words = [text.upper() for kind, text in statement if kind == "word"]
    head = statement[0][1].upper() if statement[0][0] == "word" else statement[0][1]
    if head not in _STATEMENTS:
        return f"the statement `{head}`"
    if head in ("CREATE", "DROP"):
        kind = words[2] if len(words) > 2 and words[1] == "UNIQUE" else (words[1] if len(words) > 1 else "")
        if kind not in _CREATED:
            return f"the statement `{head} {kind}`"
    if head == "PRAGMA":
        names = [_name(t).lower() for t in statement[1:] if t[0] in ("word", "quoted")]
        pragma = names[-1] if len(statement) > 2 and statement[2][1] == "." else (names[0] if names else "")
        has_argument = any(text == "(" for _, text in statement)
        if any(text == "=" for _, text in statement) or pragma not in _PRAGMA_READS or (
            has_argument and pragma not in _PRAGMA_ARGUMENT_READS
        ):
            return f"the pragma `{pragma}`"
        return None
    in_cte = head == "WITH"
    sources: List[bool] = [False]
    expect_source = False
    for index, token in enumerate(statement):
        kind, text = token
        upper = text.upper()
        following = statement[index + 1][1] if index + 1 < len(statement) else ""
        previous = statement[index - 1][1].upper() if index else ""
        if text == "(":
            sources.append(False)
        elif text == ")" and len(sources) > 1:
            sources.pop()
        if kind == "word" and upper in ("SELECT", "INSERT", "UPDATE", "DELETE", "VALUES") and len(sources) == 1:
            in_cte = False
        if kind == "string" and (expect_source or following in ("(", ".") or previous == "."):
            return "a string literal used as a name"
        expect_source = False
        if kind == "word" and upper in _SOURCE_KEYWORDS:
            sources[-1], expect_source = upper in ("FROM", "JOIN"), True
        elif kind == "word" and upper in _SOURCE_END:
            sources[-1] = False
        elif text == "," and sources[-1]:
            expect_source = True
        if kind in ("word", "quoted"):
            lowered = _name(token).lower()
            if lowered in _VIRTUAL_SOURCES or lowered.startswith("pragma_"):
                return f"the table source `{_name(token)}`"
            if following != "(" or lowered in _FUNCTIONS or (kind == "word" and upper in _KEYWORDS):
                continue
            if head in ("CREATE", "INSERT", "REPLACE") and previous in _NAMED_COLUMNS_AFTER:
                continue
            if in_cte and len(sources) == 1 and previous in ("WITH", "RECURSIVE", ","):
                continue
            return f"the function `{_name(token)}`"
        elif kind not in ("string", "number", "op"):
            return f"the SQL token `{text}`"
    return None


def sql_outside_allowlist(sql: str) -> Optional[str]:
    """The first part of *sql* outside the allowlisted statements, functions and sources, or None."""
    tokens, unplaced = _tokens(sql)
    if unplaced:
        return unplaced
    statement: List[Tuple[str, str]] = []
    for token in tokens + [("op", ";")]:
        if token[1] != ";":
            statement.append(token)
            continue
        part = statement and _statement_part(statement)
        if part:
            return part
        statement = []
    return None


def script_outside_subset(script: str) -> Optional[str]:
    """The first dot-command or SQL in a sqlite3 script outside the read subset, or None."""
    sql: List[str] = []
    for line in script.split("\n"):
        stripped = line.strip()
        if stripped.startswith("."):
            name = re.match(r"\.(\S*)", stripped).group(1)
            if name not in DOT_COMMANDS:
                return f"the dot-command `.{name}`"
        else:
            sql.append(line)
    return sql_outside_allowlist("\n".join(sql))
