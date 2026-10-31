"""The two lexed views keep length and newlines, and blank exactly what they claim.

``strip_source`` skips plain runs of code, string text and comment text in bulk
rather than character by character; these cases pin the output at every state
boundary so that speed-up cannot change what the classifier sees.
"""
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from modules.security.source_lexer import (  # noqa: E402
    JS_SPEC, PERL_SPEC, RUBY_SPEC, strip_source,
)


@pytest.mark.parametrize(
    "source, verb_view, exec_view",
    [
        ("a = 1;\n", "a = 1;\n", "a = 1;\n"),
        ("x // note\ny", "x        \ny", "x        \ny"),
        ("x /* a\nb */ y", "x     \n     y", "x     \n     y"),
        ("x /* open", "x        ", "x        "),
        ("s = 'ab\\'c';", "s = '     ';", "s = 'ab\\'c';"),
        ("s = `a\nb`;", "s = ` \n `;", "s = `a\nb`;"),
        ("u = 'http://x'; // c", "u = '        ';     ", "u = 'http://x';     "),
        ("a / b", "a / b", "a / b"),
        ("s = 'open", "s = '    ", "s = 'open"),
        ("end\\", "end\\", "end\\"),
        ("'a\\", "'  ", "'a\\"),
    ],
)
def test_js_views(source, verb_view, exec_view):
    stripped = strip_source(source, JS_SPEC)
    assert stripped.verb_view == verb_view
    assert stripped.exec_view == exec_view


def test_views_have_the_source_length_and_newlines():
    source = "a // c\n/* x\ny */ 'p\nq' `r`\n"
    stripped = strip_source(source, JS_SPEC)
    for view in (stripped.verb_view, stripped.exec_view):
        assert len(view) == len(source)
        assert [i for i, ch in enumerate(view) if ch == "\n"] == [
            i for i, ch in enumerate(source) if ch == "\n"
        ]


def test_column_zero_comment_families_still_blank():
    assert strip_source("=begin\nhidden\n=end\ncode", RUBY_SPEC).verb_view == (
        "      \n      \n    \ncode"
    )
    assert strip_source("=pod\nhidden\n=cut\ncode", PERL_SPEC).verb_view == (
        "    \n      \n    \ncode"
    )
