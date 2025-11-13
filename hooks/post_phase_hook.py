#!/usr/bin/env python3
"""
Post-Phase Hook - Validar resultados DESPUÉS de cada fase.
"""

import sys
import logging
from pathlib import Path
from typing import Dict, Any

sys.path.insert(0, str(Path(__file__).parent.parent / "tools" / "0-guards"))

from workflow_enforcer import get_enforcer, GuardViolation

logger = logging.getLogger(__name__)


def post_phase_4_approval(
    tier: str,
    user_response: str,
    validation_result: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Validar que T3 operations recibieron approval.
    """
    enforcer = get_enforcer()

    try:
        if tier == "T3":
            enforcer.enforce(
                "guard_phase_4_approval_validation",
                validation_result=validation_result
            )

        return {"valid": True, "reason": "Approval validation passed"}

    except GuardViolation as e:
        return {"valid": False, "reason": str(e)}


def post_phase_6_ssot_update(
    tier: str,
    ssot_updated: bool
) -> Dict[str, Any]:
    """
    Validar que T3 operations actualizaron SSOT.
    """
    enforcer = get_enforcer()

    try:
        enforcer.enforce(
            "guard_phase_6_ssot_update_after_t3",
            tier=tier,
            ssot_updated=ssot_updated
        )

        return {"valid": True, "reason": "SSOT update validation passed"}

    except GuardViolation as e:
        return {"valid": False, "reason": str(e)}


# CLI for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("🧪 Testing Post-Phase Hooks...\n")

    # Test post-phase 4 validation (T3 without approval)
    result = post_phase_4_approval(
        tier="T3",
        user_response="reject",
        validation_result={"approved": False, "action": "abort"}
    )
    print(f"Post-Phase 4 (T3 rejected): {result}")

    # Test post-phase 4 validation (T3 with approval)
    result = post_phase_4_approval(
        tier="T3",
        user_response="approve",
        validation_result={"approved": True, "action": "proceed"}
    )
    print(f"Post-Phase 4 (T3 approved): {result}")

    # Test post-phase 6 validation (T3 without SSOT update)
    result = post_phase_6_ssot_update(
        tier="T3",
        ssot_updated=False
    )
    print(f"Post-Phase 6 (T3 no SSOT update): {result}")

    # Test post-phase 6 validation (T3 with SSOT update)
    result = post_phase_6_ssot_update(
        tier="T3",
        ssot_updated=True
    )
    print(f"Post-Phase 6 (T3 SSOT updated): {result}")