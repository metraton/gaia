#!/usr/bin/env python3
"""
Context Exhaustion Detector

Monitors conversation metrics to detect when context is being exhausted.
Uses heuristics since Claude Code doesn't expose remaining context.

Why this matters:
- Claude models have context limits (~200K tokens)
- Long conversations can exhaust context silently
- This detector provides early warning to spawn new agents

Usage:
    # Programmatic
    from exhaustion_detector import ExhaustionDetector
    detector = ExhaustionDetector()
    result = detector.check_exhaustion_risk(session_stats)
    
    # CLI testing
    python3 exhaustion_detector.py --tool-calls 60 --output-kb 500
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)


class ExhaustionDetector:
    """
    Detects context exhaustion using observable metrics.
    
    Since Claude Code doesn't expose "remaining context", we use heuristics
    based on empirically observed thresholds.
    """

    # Thresholds based on empirical observation of context exhaustion patterns
    # These are conservative estimates - better to warn early than lose context
    THRESHOLDS = {
        "tool_calls": {
            "warning": 50,      # 50+ tool calls = conversation getting long
            "critical": 90,     # 90+ = high risk of context issues
            "weight": 1.0       # Primary indicator
        },
        "output_kb": {
            "warning": 500,     # 500KB total output
            "critical": 800,    # 800KB = approaching limits
            "weight": 0.8       # Secondary indicator
        },
        "resume_count": {
            "warning": 5,       # 5+ resumes to same agent
            "critical": 8,      # 8+ = context accumulation risk
            "weight": 0.7       # Context accumulates with resumes
        },
        "session_minutes": {
            "warning": 60,      # 1 hour session
            "critical": 90,     # 1.5 hours = likely long conversation
            "weight": 0.5       # Time is weak indicator alone
        }
    }

    # Recommendations based on risk level
    RECOMMENDATIONS = {
        "OK": None,
        "WARNING": "SUMMARIZE_CONTEXT",
        "CRITICAL": "SPAWN_NEW_AGENT"
    }

    def check_exhaustion_risk(self, session_stats: Dict) -> Dict:
        """
        Check if context exhaustion is at risk.
        
        Args:
            session_stats: Dict with observable metrics:
                - tool_calls: int (number of tool calls in session)
                - output_kb: int (total output size in KB)
                - resume_count: int (number of resumes to same agent)
                - session_minutes: int (duration of session)
        
        Returns:
            Dict with:
                - status: "OK" | "WARNING" | "CRITICAL"
                - recommendation: What action to take (or None if OK)
                - reason: Human-readable explanation
                - risks: List of individual risk assessments
        """
        risks: List[Dict] = []
        weighted_score = 0.0
        max_weight = sum(t["weight"] for t in self.THRESHOLDS.values())

        for metric, threshold in self.THRESHOLDS.items():
            current = session_stats.get(metric, 0)
            
            # Skip if no data
            if current == 0:
                continue
            
            # Calculate ratios
            warning_ratio = current / threshold["warning"]
            critical_ratio = current / threshold["critical"]
            
            # Determine level for this metric
            level = "OK"
            if critical_ratio >= 1.0:
                level = "CRITICAL"
                weighted_score += threshold["weight"] * 1.0
            elif warning_ratio >= 1.0:
                level = "WARNING"
                weighted_score += threshold["weight"] * 0.5
            
            if level != "OK":
                risks.append({
                    "metric": metric,
                    "level": level,
                    "value": current,
                    "warning_threshold": threshold["warning"],
                    "critical_threshold": threshold["critical"],
                    "ratio": round(critical_ratio, 2)
                })

        # No risks detected
        if not risks:
            return {
                "status": "OK",
                "recommendation": None,
                "reason": "All metrics within normal range",
                "risks": [],
                "score": 0.0
            }

        # Find highest risk
        max_risk = max(risks, key=lambda x: x["ratio"])
        
        # Determine overall status based on weighted score
        score_ratio = weighted_score / max_weight
        
        if max_risk["level"] == "CRITICAL" or score_ratio >= 0.6:
            status = "CRITICAL"
        elif score_ratio >= 0.3:
            status = "WARNING"
        else:
            status = "WARNING"

        # Build human-readable reason
        reason_parts = []
        for risk in risks:
            reason_parts.append(
                f"{risk['metric']}: {risk['value']}/{risk['critical_threshold']} "
                f"({risk['level']})"
            )
        
        return {
            "status": status,
            "recommendation": self.RECOMMENDATIONS[status],
            "reason": "; ".join(reason_parts),
            "risks": risks,
            "score": round(score_ratio, 2)
        }

    def get_mitigation_steps(self, result: Dict) -> List[str]:
        """
        Get specific mitigation steps based on exhaustion result.
        
        Args:
            result: Output from check_exhaustion_risk()
        
        Returns:
            List of actionable steps
        """
        if result["status"] == "OK":
            return []
        
        steps = []
        
        if result["recommendation"] == "SUMMARIZE_CONTEXT":
            steps = [
                "1. Summarize current progress before continuing",
                "2. Document key findings and decisions made",
                "3. Consider creating checkpoint with AGENT_STATUS",
                "4. If task is complex, consider spawning specialized sub-agent"
            ]
        elif result["recommendation"] == "SPAWN_NEW_AGENT":
            steps = [
                "1. STOP current operation - context at risk",
                "2. Create summary of all progress so far",
                "3. List pending tasks explicitly",
                "4. Spawn NEW agent with fresh context",
                "5. Pass summary to new agent as initial context"
            ]
        
        return steps


def check_context_health(session_id: str) -> Optional[str]:
    """
    Check context health for a session.
    
    This is the main entry point for hook integration.
    
    Args:
        session_id: Session identifier (for audit log lookup)
    
    Returns:
        Warning message if at risk, None if OK
    """
    try:
        # Load session stats from audit logs
        stats = _load_session_stats(session_id)
        
        detector = ExhaustionDetector()
        result = detector.check_exhaustion_risk(stats)
        
        if result["status"] == "CRITICAL":
            mitigation = detector.get_mitigation_steps(result)
            return (
                f"[CRITICAL] Context exhaustion detected\n"
                f"Reason: {result['reason']}\n"
                f"Recommendation: {result['recommendation']}\n\n"
                f"Mitigation steps:\n" + "\n".join(mitigation)
            )
        elif result["status"] == "WARNING":
            return (
                f"[WARNING] Context approaching limits\n"
                f"Reason: {result['reason']}\n"
                f"Consider: {result['recommendation']}"
            )
        
        return None
        
    except Exception as e:
        logger.error(f"Failed to check context health: {e}")
        return None


def _load_session_stats(session_id: str) -> Dict:
    """
    Load session statistics from audit logs.
    
    Parses audit log files to extract session metrics.
    
    Args:
        session_id: Session identifier
    
    Returns:
        Dict with:
            - tool_calls: int
            - output_kb: int
            - resume_count: int
            - session_minutes: int
    """
    stats = {
        "tool_calls": 0,
        "output_kb": 0,
        "resume_count": 0,
        "session_minutes": 0
    }
    
    try:
        # Try to find audit logs
        audit_paths = [
            Path.cwd() / ".claude" / "logs",
            Path.home() / ".claude" / "logs"
        ]
        
        for audit_path in audit_paths:
            if not audit_path.exists():
                continue
                
            # Look for session-specific audit files
            # Pattern: audit-{session_id}-*.json or audit-*.json
            for audit_file in audit_path.glob("audit*.json"):
                try:
                    with open(audit_file, 'r') as f:
                        audit_data = json.load(f)
                    
                    # Extract metrics from audit data
                    if isinstance(audit_data, list):
                        stats["tool_calls"] += len(audit_data)
                    elif isinstance(audit_data, dict):
                        stats["tool_calls"] += audit_data.get("tool_calls", 0)
                        stats["output_kb"] += audit_data.get("output_kb", 0)
                        stats["resume_count"] += audit_data.get("resume_count", 0)
                        
                        # Calculate session duration
                        if "start_time" in audit_data:
                            start = datetime.fromisoformat(audit_data["start_time"])
                            duration = (datetime.now() - start).total_seconds() / 60
                            stats["session_minutes"] = int(duration)
                            
                except (json.JSONDecodeError, KeyError):
                    continue
        
        return stats
        
    except Exception as e:
        logger.warning(f"Could not load session stats: {e}")
        return stats


# ============================================================================
# CLI INTERFACE
# ============================================================================

def main():
    """CLI interface for testing the exhaustion detector."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Test context exhaustion detector",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test with specific metrics
  python3 exhaustion_detector.py --tool-calls 60 --output-kb 500
  
  # Test critical scenario
  python3 exhaustion_detector.py --tool-calls 95 --resume-count 10
  
  # Check real session
  python3 exhaustion_detector.py --session-id abc123
        """
    )
    
    parser.add_argument(
        "--tool-calls", type=int, default=0,
        help="Number of tool calls (default: 0)"
    )
    parser.add_argument(
        "--output-kb", type=int, default=0,
        help="Total output size in KB (default: 0)"
    )
    parser.add_argument(
        "--resume-count", type=int, default=0,
        help="Number of agent resumes (default: 0)"
    )
    parser.add_argument(
        "--session-minutes", type=int, default=0,
        help="Session duration in minutes (default: 0)"
    )
    parser.add_argument(
        "--session-id", type=str, default=None,
        help="Check real session by ID (reads from audit logs)"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose output"
    )
    
    args = parser.parse_args()
    
    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(levelname)s: %(message)s'
    )
    
    # Check real session or use provided metrics
    if args.session_id:
        print(f"\nChecking session: {args.session_id}")
        print("=" * 60)
        
        warning = check_context_health(args.session_id)
        if warning:
            print(warning)
        else:
            print("Context health: OK")
    else:
        # Use provided metrics
        stats = {
            "tool_calls": args.tool_calls,
            "output_kb": args.output_kb,
            "resume_count": args.resume_count,
            "session_minutes": args.session_minutes
        }
        
        print("\nContext Exhaustion Analysis")
        print("=" * 60)
        print(f"Input metrics:")
        for key, value in stats.items():
            print(f"  {key}: {value}")
        print("-" * 60)
        
        detector = ExhaustionDetector()
        result = detector.check_exhaustion_risk(stats)
        
        # Status with color indicator
        status_icons = {"OK": "[OK]", "WARNING": "[!]", "CRITICAL": "[X]"}
        print(f"\nStatus: {status_icons.get(result['status'], '?')} {result['status']}")
        print(f"Score: {result['score']:.0%} of risk threshold")
        
        if result.get("recommendation"):
            print(f"Recommendation: {result['recommendation']}")
        
        print(f"Reason: {result['reason']}")
        
        if result.get("risks"):
            print("\nRisk breakdown:")
            for risk in result["risks"]:
                print(f"  - {risk['metric']}: {risk['value']}/{risk['critical_threshold']} ({risk['level']})")
        
        # Show mitigation steps if needed
        steps = detector.get_mitigation_steps(result)
        if steps:
            print("\nMitigation steps:")
            for step in steps:
                print(f"  {step}")
        
        print("=" * 60)


if __name__ == "__main__":
    main()
