"""
Canonical approval indicators for T3 operation gate.

Single source of truth imported by task_validator.py and pre_tool_use.py.
Any new approval phrase must be added here ONLY.
"""

# Canonical approval indicators — matched case-insensitively against the resume prompt.
# Includes both the new scoped token ("User approved: <scope>") and legacy synonyms
# so old prompts keep working.
APPROVAL_INDICATORS = [
    "user approved:",        # New canonical token (scoped): "User approved: terraform apply prod"
    "user approval received",
    "approved by user",
    "user approved",
    "approved. execute",
    "approved, execute",
    "approval confirmed",
    "proceed with execution",
    "go ahead",
    "confirmed. proceed",
]
