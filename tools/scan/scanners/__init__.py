"""
Scanner modules package.

Scanner modules are auto-discovered from this directory: ScannerRegistry
registers every concrete BaseScanner subclass found in a module, except in
base.py and modules whose name starts with "_".
"""


def __getattr__(name: str):
    """Lazy import to avoid circular dependency with tools.scan.registry."""
    if name == "ScannerRegistry":
        from tools.scan.registry import ScannerRegistry
        return ScannerRegistry
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["ScannerRegistry"]
