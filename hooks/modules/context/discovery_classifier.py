#!/usr/bin/env python3
"""
Discovery Classifier Module

Classifies agent output to detect structural infrastructure discoveries.

This module applies pattern-based heuristics to agent responses, identifying
when agents discover structural information that should potentially be added
to project-context.json (e.g., new namespaces, services, configuration drift).

Architecture:
1. Load classification rules from config/classification-rules.json
2. Apply positive patterns (regex) to detect discoveries
3. Filter out operational noise using negative patterns
4. Extract structured fields from matches
5. Build DiscoveryResult objects with confidence scores

Design Principles:
- Conservative: Err on the side of NOT suggesting (minimize false positives)
- Context-aware: Check negative patterns in ±2 line window around match
- Deduplication: Suppress duplicate matches within same output
- Graceful degradation: Return empty list on errors rather than crashing

Usage:
    from discovery_classifier import classify_output
    
    results = classify_output(
        agent_output="Discovered new namespace 'payments' not in context",
        agent_type="gitops-operator",
        user_task="Check cluster namespaces"
    )
    
    for result in results:
        print(f"Found: {result.summary} (confidence: {result.confidence})")
"""

import json
import re
import logging
import hashlib
from pathlib import Path
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

# ============================================================================
# Data Models
# ============================================================================

class DiscoveryCategory(str, Enum):
    """Classification types for structural discoveries."""
    NEW_RESOURCE = "new_resource"
    CONFIGURATION_ISSUE = "configuration_issue"
    DRIFT_DETECTED = "drift_detected"
    DEPENDENCY_DISCOVERED = "dependency_discovered"
    TOPOLOGY_CHANGE = "topology_change"


@dataclass
class DiscoveryResult:
    """
    A structural discovery detected in agent output.
    
    This represents a potential update to project-context.json.
    """
    # Classification
    rule_id: str                    # Which rule matched
    category: DiscoveryCategory     # Type of discovery
    confidence: float               # 0.0-1.0 confidence score
    
    # Content
    target_section: str             # project-context.json section
    proposed_change: Dict[str, Any] # Structured change data
    summary: str                    # Human-readable description
    
    # Source context
    matched_text: str               # The text that triggered the match
    agent_type: str                 # Which agent made the discovery
    user_task: str                  # Original user prompt


# ============================================================================
# Module-level cache for classification rules
# ============================================================================

_RULES_CACHE: Optional[Dict] = None
_RULES_CACHE_PATH: Optional[Path] = None


def _get_default_rules_path() -> Path:
    """Get default path to classification-rules.json."""
    # From hooks/modules/context/ up to repo root, then into config/
    return Path(__file__).parent.parent.parent.parent / "config" / "classification-rules.json"


def _load_rules(rules_path: Optional[Path] = None) -> Dict:
    """
    Load and cache classification rules.
    
    Args:
        rules_path: Path to classification-rules.json (None = use default)
    
    Returns:
        Dict with rules config
    
    Raises:
        None (returns empty dict on error, logs warning)
    """
    global _RULES_CACHE, _RULES_CACHE_PATH
    
    # Use default path if not provided
    if rules_path is None:
        rules_path = _get_default_rules_path()
    
    # Return cached rules if same path
    if _RULES_CACHE is not None and _RULES_CACHE_PATH == rules_path:
        return _RULES_CACHE
    
    # Load rules from file
    try:
        if not rules_path.exists():
            logger.warning(f"Classification rules file not found: {rules_path}")
            return {"rules": [], "global_negative_patterns": [], "confidence_threshold": 0.7}
        
        with open(rules_path, 'r') as f:
            rules_config = json.load(f)
        
        # Validate structure
        if "rules" not in rules_config:
            logger.warning(f"Invalid rules config: missing 'rules' key")
            return {"rules": [], "global_negative_patterns": [], "confidence_threshold": 0.7}
        
        # Cache and return
        _RULES_CACHE = rules_config
        _RULES_CACHE_PATH = rules_path
        
        logger.debug(f"Loaded {len(rules_config['rules'])} classification rules from {rules_path}")
        return rules_config
        
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to load classification rules: {e}")
        return {"rules": [], "global_negative_patterns": [], "confidence_threshold": 0.7}


# ============================================================================
# Helper Functions
# ============================================================================

def _check_negative_patterns(
    text: str,
    context_lines: List[str],
    negative_patterns: List[str],
    global_negatives: List[str]
) -> bool:
    """
    Check if text matches any negative pattern.
    
    Negative patterns suppress matches (e.g., "creating namespace" means
    the agent is performing an action, not discovering existing state).
    
    Args:
        text: The matched line
        context_lines: Lines around the match (±2 lines window)
        negative_patterns: Rule-specific negative patterns
        global_negatives: Global negative patterns
    
    Returns:
        True if ANY negative pattern matches (suppress this discovery)
    """
    all_negatives = negative_patterns + global_negatives
    
    # Check the matched line itself
    for pattern in all_negatives:
        try:
            if re.search(pattern, text, re.IGNORECASE):
                logger.debug(f"Negative pattern matched in line: {pattern}")
                return True
        except re.error:
            logger.warning(f"Invalid negative pattern: {pattern}")
            continue
    
    # Check surrounding context (±2 lines)
    for context_line in context_lines:
        for pattern in all_negatives:
            try:
                if re.search(pattern, context_line, re.IGNORECASE):
                    logger.debug(f"Negative pattern matched in context: {pattern}")
                    return True
            except re.error:
                continue
    
    return False


def _extract_fields(match: re.Match, extract_fields_config: Dict[str, str]) -> Dict[str, str]:
    r"""
    Extract named fields from regex match.

    Args:
        match: Regex match object
        extract_fields_config: Dict mapping field names to capture group refs ($1, $2, etc.)

    Returns:
        Dict of extracted field values

    Example:
        pattern: r"service '(\w+)' in namespace '(\w+)'"
        extract_fields: {"service_name": "$1", "namespace": "$2"}
        → {"service_name": "auth-api", "namespace": "payments"}
    """
    extracted = {}
    
    for field_name, group_ref in extract_fields_config.items():
        # Parse group reference ($1, $2, etc.)
        if not group_ref.startswith("$"):
            logger.warning(f"Invalid group reference: {group_ref}")
            continue
        
        try:
            group_num = int(group_ref[1:])
            if group_num <= match.lastindex:
                value = match.group(group_num)
                if value:
                    extracted[field_name] = value.strip()
        except (ValueError, IndexError) as e:
            logger.warning(f"Failed to extract field {field_name}: {e}")
            continue
    
    return extracted


def _build_summary(rule_id: str, category: DiscoveryCategory, extracted_fields: Dict[str, str], matched_text: str) -> str:
    """
    Build human-readable summary from discovery.
    
    Args:
        rule_id: Rule that matched
        category: Discovery category
        extracted_fields: Extracted structured data
        matched_text: Original matched text
    
    Returns:
        Human-readable summary string
    """
    # Try to build a summary from extracted fields
    if not extracted_fields:
        # Fallback: use matched text (truncated)
        return matched_text[:100] + ("..." if len(matched_text) > 100 else "")
    
    # Build summary based on category
    if category == DiscoveryCategory.NEW_RESOURCE:
        if "name" in extracted_fields:
            return f"New resource: {extracted_fields['name']}"
        elif "service_name" in extracted_fields:
            ns = extracted_fields.get("namespace", "unknown")
            return f"New service: {extracted_fields['service_name']} (namespace: {ns})"
        elif "bucket_name" in extracted_fields:
            return f"New bucket: {extracted_fields['bucket_name']}"
    
    elif category == DiscoveryCategory.CONFIGURATION_ISSUE:
        if "description" in extracted_fields:
            return f"Configuration issue: {extracted_fields['description']}"
        elif "expected" in extracted_fields and "actual" in extracted_fields:
            return f"Configuration mismatch: expected {extracted_fields['expected']}, found {extracted_fields['actual']}"
    
    elif category == DiscoveryCategory.DRIFT_DETECTED:
        if "actual" in extracted_fields and "expected" in extracted_fields:
            return f"Drift detected: actual={extracted_fields['actual']}, expected={extracted_fields['expected']}"
    
    elif category == DiscoveryCategory.DEPENDENCY_DISCOVERED:
        if "source" in extracted_fields and "target" in extracted_fields:
            return f"Dependency: {extracted_fields['source']} → {extracted_fields['target']}"
    
    elif category == DiscoveryCategory.TOPOLOGY_CHANGE:
        if "host" in extracted_fields:
            backend = extracted_fields.get("backend", "unknown")
            return f"Topology change: {extracted_fields['host']} → {backend}"
    
    # Fallback: generic summary
    field_str = ", ".join(f"{k}={v}" for k, v in extracted_fields.items())
    return f"{category.value}: {field_str}"


def _compute_content_hash(target_section: str, proposed_change: Dict) -> str:
    """
    Compute deterministic hash for deduplication.
    
    Args:
        target_section: Target section in project-context.json
        proposed_change: The proposed change dict
    
    Returns:
        12-char hex hash
    """
    # Normalize proposed_change by sorting keys
    normalized = json.dumps(proposed_change, sort_keys=True)
    content = f"{target_section}:{normalized}"
    
    # SHA-256, take first 12 chars
    hash_obj = hashlib.sha256(content.encode('utf-8'))
    return hash_obj.hexdigest()[:12]


def _dedup_within_output(results: List[DiscoveryResult]) -> List[DiscoveryResult]:
    """
    Deduplicate discoveries within the same output.
    
    If the same rule matches multiple times for essentially the same discovery,
    keep only the highest-confidence one.
    
    Args:
        results: List of DiscoveryResult objects
    
    Returns:
        Deduplicated list
    """
    if len(results) <= 1:
        return results
    
    # Group by (rule_id, target_section, content_hash)
    seen = {}
    
    for result in results:
        content_hash = _compute_content_hash(result.target_section, result.proposed_change)
        key = (result.rule_id, result.target_section, content_hash)
        
        if key not in seen:
            seen[key] = result
        else:
            # Keep the one with higher confidence
            if result.confidence > seen[key].confidence:
                seen[key] = result
    
    return list(seen.values())


# ============================================================================
# Main Classification Function
# ============================================================================

def classify_output(
    agent_output: str,
    agent_type: str,
    user_task: str,
    rules_path: Optional[Path] = None
) -> List[DiscoveryResult]:
    """
    Classify agent output to detect structural infrastructure discoveries.
    
    This function applies pattern-based heuristics to identify when agents
    discover structural information that should be added to project-context.json.
    
    Args:
        agent_output: The agent's response text
        agent_type: Type of agent (e.g., "gitops-operator")
        user_task: Original user prompt
        rules_path: Optional path to classification-rules.json
    
    Returns:
        List of DiscoveryResult objects (may be empty)
    
    Algorithm:
        1. Load classification rules from config
        2. Split output into lines for analysis
        3. For each rule:
           a. Apply positive patterns to find matches
           b. Check negative patterns (rule + global)
           c. Extract structured fields
           d. Compute confidence score
           e. If confidence >= threshold, create DiscoveryResult
        4. Deduplicate within output
        5. Return results
    
    Example:
        >>> output = "Discovered new namespace 'payments' not in context"
        >>> results = classify_output(output, "gitops-operator", "list namespaces")
        >>> print(results[0].summary)
        "New resource: payments"
    """
    # Handle empty/None input
    if not agent_output:
        return []
    
    # Load rules
    rules_config = _load_rules(rules_path)
    
    rules = rules_config.get("rules", [])
    global_negatives = rules_config.get("global_negative_patterns", [])
    confidence_threshold = rules_config.get("confidence_threshold", 0.7)
    
    if not rules:
        logger.debug("No classification rules loaded")
        return []
    
    # Split output into lines
    lines = agent_output.split("\n")
    
    results: List[DiscoveryResult] = []
    
    # Process each rule
    for rule in rules:
        rule_id = rule.get("id", "unknown")
        category_str = rule.get("category", "new_resource")
        target_section = rule.get("target_section", "project_details")
        patterns = rule.get("patterns", [])
        negative_patterns = rule.get("negative_patterns", [])
        confidence_weight = rule.get("confidence_weight", 0.7)
        extract_fields_config = rule.get("extract_fields", {})
        
        # Parse category enum
        try:
            category = DiscoveryCategory(category_str)
        except ValueError:
            logger.warning(f"Invalid category in rule {rule_id}: {category_str}")
            continue
        
        # Apply each positive pattern
        for pattern in patterns:
            try:
                regex = re.compile(pattern, re.IGNORECASE)
            except re.error as e:
                logger.warning(f"Invalid regex pattern in rule {rule_id}: {pattern} - {e}")
                continue
            
            # Search all lines
            for line_idx, line in enumerate(lines):
                match = regex.search(line)
                
                if not match:
                    continue
                
                # Get context lines (±2 lines window)
                context_start = max(0, line_idx - 2)
                context_end = min(len(lines), line_idx + 3)
                context_lines = lines[context_start:context_end]
                
                # Check negative patterns
                if _check_negative_patterns(line, context_lines, negative_patterns, global_negatives):
                    logger.debug(f"Match suppressed by negative pattern: {line[:50]}...")
                    continue
                
                # Extract fields
                extracted_fields = _extract_fields(match, extract_fields_config)
                
                # Compute confidence (could be adjusted based on context)
                confidence = confidence_weight
                
                # Skip if below threshold
                if confidence < confidence_threshold:
                    logger.debug(f"Confidence {confidence} below threshold {confidence_threshold}")
                    continue
                
                # Build proposed_change from extracted fields
                proposed_change = extracted_fields.copy()
                
                # Build summary
                summary = _build_summary(rule_id, category, extracted_fields, line)
                
                # Create DiscoveryResult
                result = DiscoveryResult(
                    rule_id=rule_id,
                    category=category,
                    confidence=confidence,
                    target_section=target_section,
                    proposed_change=proposed_change,
                    summary=summary,
                    matched_text=line.strip(),
                    agent_type=agent_type,
                    user_task=user_task
                )
                
                results.append(result)
                logger.debug(f"Discovery matched: {summary} (confidence: {confidence})")
    
    # Deduplicate within output
    results = _dedup_within_output(results)
    
    logger.info(f"Classification complete: {len(results)} discoveries found")
    return results


# ============================================================================
# CLI Interface (for testing)
# ============================================================================

def main():
    """CLI interface for testing the discovery classifier."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Test discovery classifier on agent output",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test with sample text
  echo "Discovered new namespace 'payments'" | python3 discovery_classifier.py
  
  # Test with file
  python3 discovery_classifier.py --input agent_output.txt
  
  # Use custom rules
  python3 discovery_classifier.py --rules custom-rules.json --input output.txt
        """
    )
    
    parser.add_argument(
        "--input", "-i", type=str, default=None,
        help="Path to file with agent output (default: read from stdin)"
    )
    parser.add_argument(
        "--rules", "-r", type=str, default=None,
        help="Path to classification rules JSON (default: config/classification-rules.json)"
    )
    parser.add_argument(
        "--agent-type", "-a", type=str, default="test-agent",
        help="Agent type (default: test-agent)"
    )
    parser.add_argument(
        "--user-task", "-u", type=str, default="test task",
        help="User task description (default: 'test task')"
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
    
    # Read input
    if args.input:
        try:
            with open(args.input, 'r') as f:
                agent_output = f.read()
        except IOError as e:
            print(f"Error reading input file: {e}")
            return 1
    else:
        # Read from stdin
        import sys
        agent_output = sys.stdin.read()
    
    if not agent_output.strip():
        print("No input provided")
        return 1
    
    # Parse rules path
    rules_path = Path(args.rules) if args.rules else None
    
    # Run classification
    print("\nDiscovery Classification")
    print("=" * 60)
    print(f"Agent type: {args.agent_type}")
    print(f"User task: {args.user_task}")
    print(f"Rules: {rules_path or 'default'}")
    print("-" * 60)
    
    results = classify_output(
        agent_output=agent_output,
        agent_type=args.agent_type,
        user_task=args.user_task,
        rules_path=rules_path
    )
    
    if not results:
        print("\n[NO DISCOVERIES]\n")
        print("No structural discoveries detected in output.")
    else:
        print(f"\n[FOUND {len(results)} DISCOVERIES]\n")
        
        for idx, result in enumerate(results, 1):
            print(f"{idx}. {result.summary}")
            print(f"   Category: {result.category.value}")
            print(f"   Confidence: {result.confidence:.0%}")
            print(f"   Target: {result.target_section}")
            print(f"   Rule: {result.rule_id}")
            print(f"   Matched: {result.matched_text[:80]}...")
            if result.proposed_change:
                print(f"   Fields: {json.dumps(result.proposed_change, indent=6)}")
            print()
    
    print("=" * 60)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
