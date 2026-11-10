"""Every ``gaia approvals <verb>`` a skill names is a verb the CLI registers."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SKILLS = REPO_ROOT / "skills"
#: The verb word after the group; ``a|b`` and a table's ``a\|b`` name several.
_NAMED = re.compile(r"gaia approvals ([a-z][a-z|\\-]*)")


def _registered_verbs() -> set[str]:
    from bin.cli import approvals

    parser = argparse.ArgumentParser()
    approvals.register(parser.add_subparsers(dest="group"))
    group = parser._subparsers._group_actions[0].choices["approvals"]
    return set(group._subparsers._group_actions[0].choices)


def _named_verbs() -> dict[str, set[str]]:
    """Each verb the skills name, with the files that name it."""
    named: dict[str, set[str]] = {}
    for path in sorted(SKILLS.rglob("*.md")):
        for words in _NAMED.findall(path.read_text(encoding="utf-8")):
            for verb in filter(None, re.split(r"\\?\|", words)):
                named.setdefault(verb.rstrip("-\\"), set()).add(str(path.relative_to(REPO_ROOT)))
    return named


def test_every_approvals_verb_a_skill_names_is_registered():
    named = _named_verbs()
    assert {"request-set", "question", "show", "revoke"} <= set(named), sorted(named)

    unknown = {verb: sorted(files) for verb, files in named.items() if verb not in _registered_verbs()}

    assert not unknown, f"skills name approvals verbs the CLI does not register: {unknown}"
