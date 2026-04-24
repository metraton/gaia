"""gaia-ops tools namespace package.

Marking ``tools`` as a regular package ensures pytest's rootdir discovery
resolves to the repo root for both ``tests/`` and ``tools/scan/tests/``
during full-suite collection. Without this file, pytest walks up from
``tools/scan/tests/__init__.py`` to ``tools/`` (no ``__init__.py``) and
uses that as the package root, which makes ``from tools.scan...`` imports
fail at collection time.
"""
