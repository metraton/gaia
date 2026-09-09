"""Keep the security-tier skill's owning table aligned with runtime tiers."""

import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]
_SKILL = _ROOT / "skills" / "security-tiers" / "SKILL.md"
_HOOKS = _ROOT / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

from modules.security.tiers import SecurityTier, classify_command_tier


_TABLE_HEADING = "## The four tiers"
_EXPECTED_TIERS = {
    "T0": SecurityTier.T0_READ_ONLY,
    "T1": SecurityTier.T1_VALIDATION,
    "T2": SecurityTier.T2_DRY_RUN,
    "T3": SecurityTier.T3_BLOCKED,
}


def _tier_rows() -> list[list[str]]:
    """Return the four data rows from the skill's owning tier table."""
    content = _SKILL.read_text(encoding="utf-8")
    section = content.split(_TABLE_HEADING, maxsplit=1)
    assert len(section) == 2, f"missing exact tier-table heading: {_TABLE_HEADING}"

    lines = section[1].lstrip().splitlines()
    assert lines[:2] == [
        "| Tier | What it is | Approval? | Example verbs |",
        "|------|------------|:---:|---------------|",
    ], "security tier table header or four-column shape changed"

    rows: list[list[str]] = []
    for line in lines[2:]:
        if not line.startswith("|"):
            break
        cells = [cell.strip().replace("**", "") for cell in line.strip("|").split("|")]
        assert len(cells) == 4, f"tier row must have exactly four columns: {line}"
        rows.append(cells)

    assert [row[0] for row in rows] == list(_EXPECTED_TIERS), (
        "security tier table must contain exactly one ordered row for T0, T1, T2, T3"
    )
    return rows


def test_documented_tier_table_matches_runtime_approval_and_examples():
    """Each table row's approval marker and example verbs match runtime."""
    for tier_name, _, approval, examples in _tier_rows():
        runtime_tier = _EXPECTED_TIERS[tier_name]
        assert (approval == "Yes") is runtime_tier.requires_approval, (
            f"{tier_name} approval marker {approval!r} disagrees with "
            f"SecurityTier.requires_approval"
        )
        for verb in [item.strip() for item in examples.split(",")]:
            command = f"tool {verb}"
            actual_tier = classify_command_tier(command)
            assert actual_tier is runtime_tier, (
                f"documented {tier_name} example verb {verb!r} classifies as "
                f"{actual_tier.value} when placed in command position"
            )
