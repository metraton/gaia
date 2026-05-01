"""
gaia -- Python library for Gaia substrate v6.

Provides the canonical storage substrate for all Gaia state:
paths, workspace identity, and directory layout.

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
