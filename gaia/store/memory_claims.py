"""The kinds of claim a curated memory row can make, read from its description.

The first word of a memory description names the kind of claim the row makes,
so a listing or the session's birth block reads as a set of claims and a reader
tells a user's fact from a user's preference without opening the body. The
vocabulary is open: a description may start with another word, which simply
has no recognised kind. ``gaia memory add --help`` renders it and the CLI's
write warnings read it.
"""

from __future__ import annotations

import unicodedata

MEMORY_CLAIM_KINDS = ("Hecho", "Preferencia", "Decisión", "Aprendizaje", "Pendiente", "Bug")


def _fold(word: str) -> str:
    decomposed = unicodedata.normalize("NFKD", word)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


_KIND_BY_FOLDED = {_fold(kind): kind for kind in MEMORY_CLAIM_KINDS}


def memory_claim_kind(description: str | None) -> str | None:
    """The :data:`MEMORY_CLAIM_KINDS` entry a description starts with, or None.

    Only the first word counts, read without case or accents and without a
    trailing colon, so ``"Decision: ..."`` and ``"Preferencia temporal: ..."``
    are recognised.
    """
    words = (description or "").split(maxsplit=1)
    if not words:
        return None
    return _KIND_BY_FOLDED.get(_fold(words[0].rstrip(":")))
