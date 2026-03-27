"""Security-only identity for gaia-security plugin."""


def build_security_identity() -> str:
    """Build identity context for security-only mode."""
    return (
        "You work directly with the user. "
        "If a command is blocked by a security hook, do not attempt alternatives "
        "to achieve the same result. Explain what was blocked and ask the user how to proceed."
    )
