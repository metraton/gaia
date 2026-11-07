"""
gaia -- the Python library behind Gaia's CLI and hooks.

Holds the storage substrate (paths, workspace identity, the gaia.db store) and
the approval, contract and retention machinery built on it.

Usage::

    import gaia
    print(gaia.__version__)

    from gaia.paths import data_dir, db_path
    print(db_path())

    from gaia.project import current
    print(current())
"""

__version__ = "5.0.0-rc.3"

__all__ = ["__version__"]
