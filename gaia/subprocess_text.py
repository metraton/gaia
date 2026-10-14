"""Text-mode subprocess runs whose codec does not depend on the locale."""

import subprocess


def run_text(args, **kwargs) -> subprocess.CompletedProcess:
    """Run *args* as ``subprocess.run`` does, exchanging stdin/stdout/stderr as UTF-8 text.

    git and the other tools Gaia reads emit UTF-8; ``text=True`` alone decodes
    with the locale codec, which is cp1252 on Windows and ASCII under ``LC_ALL=C``.
    """
    return subprocess.run(args, encoding="utf-8", **kwargs)
