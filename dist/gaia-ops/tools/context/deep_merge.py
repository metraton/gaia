"""
Deep merge utility for project-context.json updates.

Merges two dicts recursively following the gaia-ops merge decision tree:
  1. Key missing in current  -> ADD
  2. Both values are dicts   -> RECURSE (deep merge)
  3. Both values are lists   -> UNION (primitives: sorted set union;
                                       dicts with "name": merge by name;
                                       other dicts: concatenate + deduplicate)
  4. Both values are scalars -> OVERWRITE (new replaces old)
  5. Type mismatch           -> OVERWRITE with warning

No-Delete Policy: keys in current but NOT in update are always preserved.
"""

import copy
import json
import logging

logger = logging.getLogger(__name__)


def deep_merge(current: dict, update: dict) -> tuple[dict, dict]:
    """Merge *update* into *current* returning ``(merged, diff)``.

    Parameters
    ----------
    current:
        The existing data (will NOT be mutated).
    update:
        New data to merge on top of *current*.

    Returns
    -------
    tuple[dict, dict]
        ``merged`` – the result of the merge.
        ``diff``   – audit trail recording changes (``{key: {old, new}}``).
    """
    merged = copy.deepcopy(current)
    diff: dict = {}

    for key, new_value in update.items():
        if key not in merged:
            # Rule 1: ADD missing key
            merged[key] = copy.deepcopy(new_value)
            continue

        old_value = merged[key]

        # Rule 2: Both dicts -> recurse
        if isinstance(old_value, dict) and isinstance(new_value, dict):
            sub_merged, sub_diff = deep_merge(old_value, new_value)
            merged[key] = sub_merged
            if sub_diff:
                diff[key] = sub_diff
            continue

        # Rule 3: Both lists -> union strategy
        if isinstance(old_value, list) and isinstance(new_value, list):
            merged_list = _merge_lists(old_value, new_value)
            if merged_list != old_value:
                diff[key] = {"old": old_value, "new": merged_list}
            merged[key] = merged_list
            continue

        # Rule 5: Type mismatch -> overwrite with warning
        if type(old_value) is not type(new_value):
            logger.warning(
                "Type mismatch for key '%s': %s -> %s. New value wins.",
                key,
                type(old_value).__name__,
                type(new_value).__name__,
            )
            diff[key] = {"old": old_value, "new": new_value}
            merged[key] = copy.deepcopy(new_value)
            continue

        # Rule 4: Both scalars -> overwrite
        if old_value != new_value:
            diff[key] = {"old": old_value, "new": new_value}
        merged[key] = copy.deepcopy(new_value)

    return merged, diff


# ---------------------------------------------------------------------------
# List merge helpers
# ---------------------------------------------------------------------------

def _merge_lists(current: list, update: list) -> list:
    """Merge two lists following the union strategy.

    a) All items are primitives (str, int, float, bool) -> sorted set union.
    b) Items are dicts with a ``"name"`` key -> merge by name, preserve missing.
    c) Otherwise -> concatenate, deduplicate by JSON equality.
    """
    if _all_primitives(current) and _all_primitives(update):
        return sorted(set(current) | set(update))

    if _all_dicts_with_name(current) and _all_dicts_with_name(update):
        return _merge_named_dicts(current, update)

    # Fallback: concatenate + deduplicate by JSON equality
    return _concat_deduplicate(current, update)


def _all_primitives(items: list) -> bool:
    """Return True if every item is a primitive (str, int, float, bool)."""
    return all(isinstance(i, (str, int, float, bool)) for i in items)


def _all_dicts_with_name(items: list) -> bool:
    """Return True if every item is a dict containing a ``"name"`` key."""
    return bool(items) and all(
        isinstance(i, dict) and "name" in i for i in items
    )


def _merge_named_dicts(current: list[dict], update: list[dict]) -> list[dict]:
    """Merge lists of dicts by their ``"name"`` field.

    - Matching names: deep-merge the dict fields.
    - Names only in current: preserved (no-delete).
    - Names only in update: appended.
    """
    result_by_name: dict[str, dict] = {}
    order: list[str] = []

    # Seed with current entries (preserves order + no-delete)
    for item in current:
        name = item["name"]
        result_by_name[name] = copy.deepcopy(item)
        order.append(name)

    # Merge / add from update
    for item in update:
        name = item["name"]
        if name in result_by_name:
            merged_item, _ = deep_merge(result_by_name[name], item)
            result_by_name[name] = merged_item
        else:
            result_by_name[name] = copy.deepcopy(item)
            order.append(name)

    return [result_by_name[n] for n in order]


def _concat_deduplicate(current: list, update: list) -> list:
    """Concatenate two lists, deduplicating by JSON equality."""
    seen: list[str] = []
    result: list = []

    for item in current + update:
        serialized = json.dumps(item, sort_keys=True)
        if serialized not in seen:
            seen.append(serialized)
            result.append(copy.deepcopy(item))

    return result
