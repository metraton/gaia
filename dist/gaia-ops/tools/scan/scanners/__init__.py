"""
Scanner modules package.

Scanner modules are auto-discovered from this directory. Any .py file that
exports SCANNER_NAME, SCANNER_VERSION, OWNED_SECTIONS, and scan() is
registered automatically by ScannerRegistry.
"""


def __getattr__(name: str):
    """Lazy import to avoid circular dependency with tools.scan.registry."""
    if name == "ScannerRegistry":
        from tools.scan.registry import ScannerRegistry
        return ScannerRegistry
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["ScannerRegistry"]
