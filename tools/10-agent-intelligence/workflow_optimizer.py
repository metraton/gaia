#!/usr/bin/env python3
"""
WorkflowOptimizer - Applies the 7 LLM Engineering Principles to optimize workflows
Helps Gaia create and improve workflows based on proven design patterns.
"""

import re
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum


class DesignPrinciple(Enum):
    """The 7 LLM Engineering Principles"""
    BINARY_DECISIONS = "Binary Decision Trees"
    GUARDS_OVER_ADVICE = "Guards Over Advice"
    TOOL_CONTRACTS = "Tool Contracts"
    FAILURE_PATHS = "Failure Paths"
    TLDR_FIRST = "TL;DR First"
    REFERENCES = "References Over Duplication"
    METRICS = "Metrics Over Subjective Goals"


@dataclass
class PrincipleViolation:
    """Represents a violation of a design principle"""
    principle: DesignPrinciple
    severity: str  # 'high', 'medium', 'low'
    location: str
    description: str
    suggestion: str


@dataclass
class OptimizationResult:
    """Result of workflow optimization analysis"""
    score: float
    violations: List[PrincipleViolation] = field(default_factory=list)
    improvements: List[str] = field(default_factory=list)
    optimized_content: str = ""


class WorkflowOptimizer:
    """
    Optimizes workflows by applying the 7 LLM Engineering Principles
    """

    def __init__(self):
        """Initialize the optimizer with principle definitions"""
        self.principles = {
            DesignPrinciple.BINARY_DECISIONS: self._check_binary_decisions,
            DesignPrinciple.GUARDS_OVER_ADVICE: self._check_guards,
            DesignPrinciple.TOOL_CONTRACTS: self._check_tool_contracts,
            DesignPrinciple.FAILURE_PATHS: self._check_failure_paths,
            DesignPrinciple.TLDR_FIRST: self._check_tldr_first,
            DesignPrinciple.REFERENCES: self._check_references,
            DesignPrinciple.METRICS: self._check_metrics
        }

    def analyze_workflow(self, content: str) -> OptimizationResult:
        """
        Analyze a workflow against all 7 principles

        Args:
            content: Workflow markdown content

        Returns:
            OptimizationResult with violations and suggestions
        """
        result = OptimizationResult(score=100.0)

        # Check each principle
        for principle, check_func in self.principles.items():
            violations = check_func(content)
            result.violations.extend(violations)

        # Calculate score
        for violation in result.violations:
            if violation.severity == 'high':
                result.score -= 10
            elif violation.severity == 'medium':
                result.score -= 5
            else:
                result.score -= 2

        result.score = max(0, result.score)

        # Generate improvements
        result.improvements = self._generate_improvements(result.violations)

        return result

    def _check_binary_decisions(self, content: str) -> List[PrincipleViolation]:
        """Check for binary decision tree violations"""
        violations = []

        # Look for complex conditional statements
        complex_conditions = re.findall(
            r'(?:if|when|unless).*(?:and|or|but).*(?:and|or|but)',
            content,
            re.IGNORECASE
        )

        for condition in complex_conditions:
            violations.append(PrincipleViolation(
                principle=DesignPrinciple.BINARY_DECISIONS,
                severity='medium',
                location=condition[:50] + "...",
                description="Complex conditional with multiple logical operators",
                suggestion="Break down into binary decision tree with yes/no branches"
            ))

        # Check for nested conditionals
        lines = content.split('\n')
        indent_stack = []
        for i, line in enumerate(lines):
            if re.search(r'^\s*(if|when|unless)', line, re.IGNORECASE):
                indent = len(line) - len(line.lstrip())
                if indent_stack and indent > indent_stack[-1]:
                    violations.append(PrincipleViolation(
                        principle=DesignPrinciple.BINARY_DECISIONS,
                        severity='low',
                        location=f"Line {i+1}",
                        description="Nested conditional detected",
                        suggestion="Flatten to sequential binary checks"
                    ))
                indent_stack.append(indent)

        # Look for switch/case patterns that should be binary trees
        if re.search(r'(case|switch|match)\s+\w+', content, re.IGNORECASE):
            violations.append(PrincipleViolation(
                principle=DesignPrinciple.BINARY_DECISIONS,
                severity='medium',
                location="Switch/case statement",
                description="Multi-way branch detected",
                suggestion="Convert to binary decision tree for clarity"
            ))

        return violations

    def _check_guards(self, content: str) -> List[PrincipleViolation]:
        """Check for guards vs advice violations"""
        violations = []

        # Look for advisory language instead of guards
        advisory_patterns = [
            (r'\bshould\b', "should"),
            (r'\bmay\b', "may"),
            (r'\bmight\b', "might"),
            (r'\bconsider\b', "consider"),
            (r'\bprefer(ably)?\b', "prefer"),
            (r'\btry to\b', "try to"),
            (r'\battempt\b', "attempt"),
            (r'\bif possible\b', "if possible")
        ]

        for pattern, word in advisory_patterns:
            matches = re.finditer(pattern, content, re.IGNORECASE)
            for match in matches:
                line_num = content[:match.start()].count('\n') + 1
                violations.append(PrincipleViolation(
                    principle=DesignPrinciple.GUARDS_OVER_ADVICE,
                    severity='medium',
                    location=f"Line {line_num}: '{word}'",
                    description=f"Advisory language '{word}' instead of guard",
                    suggestion=f"Replace with binary guard: 'MUST' or 'MUST NOT'"
                ))

        # Look for missing validation guards
        if 'validate' not in content.lower() and 'check' not in content.lower():
            violations.append(PrincipleViolation(
                principle=DesignPrinciple.GUARDS_OVER_ADVICE,
                severity='high',
                location="Global",
                description="No validation guards found",
                suggestion="Add explicit validation guards before operations"
            ))

        return violations

    def _check_tool_contracts(self, content: str) -> List[PrincipleViolation]:
        """Check for tool contract violations"""
        violations = []

        # Look for tool invocations without contracts
        tool_patterns = [
            r'Task\s*\(',
            r'Tool\s*\(',
            r'invoke\s*\(',
            r'execute\s*\(',
            r'run\s*\('
        ]

        for pattern in tool_patterns:
            matches = list(re.finditer(pattern, content, re.IGNORECASE))
            if matches:
                # Check if there's a contract definition nearby
                has_contract = bool(re.search(
                    r'(?:contract|interface|schema|parameters|returns)',
                    content,
                    re.IGNORECASE
                ))

                if not has_contract:
                    violations.append(PrincipleViolation(
                        principle=DesignPrinciple.TOOL_CONTRACTS,
                        severity='high',
                        location=f"Tool invocations found",
                        description="Tool usage without defined contracts",
                        suggestion="Define input/output contracts for all tools"
                    ))

        # Check for undefined parameters
        param_refs = re.findall(r'\$\{?(\w+)\}?', content)
        param_defs = re.findall(r'(?:param|parameter|var|variable)\s+(\w+)', content, re.IGNORECASE)

        undefined_params = set(param_refs) - set(param_defs)
        if undefined_params:
            violations.append(PrincipleViolation(
                principle=DesignPrinciple.TOOL_CONTRACTS,
                severity='medium',
                location=f"Parameters: {', '.join(list(undefined_params)[:3])}",
                description="Undefined parameters referenced",
                suggestion="Define all parameters with types and constraints"
            ))

        return violations

    def _check_failure_paths(self, content: str) -> List[PrincipleViolation]:
        """Check for proper failure path handling"""
        violations = []

        # Count error handling blocks
        error_blocks = len(re.findall(
            r'(?:error|exception|failure|catch|rescue|recover)',
            content,
            re.IGNORECASE
        ))

        # Count operations that could fail
        operations = len(re.findall(
            r'(?:call|invoke|execute|run|apply|deploy|create|delete|update)',
            content,
            re.IGNORECASE
        ))

        if operations > 0 and error_blocks < operations / 3:
            violations.append(PrincipleViolation(
                principle=DesignPrinciple.FAILURE_PATHS,
                severity='high',
                location="Global",
                description=f"Insufficient error handling: {error_blocks} handlers for {operations} operations",
                suggestion="Add failure paths for at least 1/3 of operations"
            ))

        # Check for try without catch
        try_blocks = re.findall(r'\btry\b', content, re.IGNORECASE)
        catch_blocks = re.findall(r'\b(?:catch|except|rescue)\b', content, re.IGNORECASE)

        if len(try_blocks) > len(catch_blocks):
            violations.append(PrincipleViolation(
                principle=DesignPrinciple.FAILURE_PATHS,
                severity='medium',
                location="Try blocks",
                description="Try blocks without corresponding catch/except",
                suggestion="Add explicit error handling for all try blocks"
            ))

        # Check for rollback/recovery strategies
        if 'rollback' not in content.lower() and 'recover' not in content.lower():
            violations.append(PrincipleViolation(
                principle=DesignPrinciple.FAILURE_PATHS,
                severity='medium',
                location="Global",
                description="No rollback or recovery strategies defined",
                suggestion="Define rollback procedures for critical operations"
            ))

        return violations

    def _check_tldr_first(self, content: str) -> List[PrincipleViolation]:
        """Check for TL;DR first principle"""
        violations = []

        lines = content.split('\n')

        # Check if there's a summary at the beginning
        first_100_lines = '\n'.join(lines[:100])
        has_summary = any([
            'tl;dr' in first_100_lines.lower(),
            'summary' in first_100_lines.lower(),
            'overview' in first_100_lines.lower(),
            'quick' in first_100_lines.lower()
        ])

        if not has_summary:
            violations.append(PrincipleViolation(
                principle=DesignPrinciple.TLDR_FIRST,
                severity='medium',
                location="Document start",
                description="No TL;DR or summary at the beginning",
                suggestion="Add a TL;DR section within the first 20 lines"
            ))

        # Check for overly long sections without summaries
        current_section = []
        for i, line in enumerate(lines):
            if line.startswith('#'):
                if len(current_section) > 50:
                    # Long section without sub-summary
                    violations.append(PrincipleViolation(
                        principle=DesignPrinciple.TLDR_FIRST,
                        severity='low',
                        location=f"Line {i-len(current_section)}-{i}",
                        description=f"Section with {len(current_section)} lines without summary",
                        suggestion="Add brief summary for sections > 50 lines"
                    ))
                current_section = []
            else:
                current_section.append(line)

        return violations

    def _check_references(self, content: str) -> List[PrincipleViolation]:
        """Check for references vs duplication"""
        violations = []

        # Find duplicate content blocks
        paragraphs = re.split(r'\n\s*\n', content)
        seen_content = {}

        for i, para in enumerate(paragraphs):
            if len(para) > 100:  # Only check substantial paragraphs
                # Create content fingerprint
                fingerprint = re.sub(r'\s+', ' ', para.lower().strip())[:100]

                if fingerprint in seen_content:
                    violations.append(PrincipleViolation(
                        principle=DesignPrinciple.REFERENCES,
                        severity='medium',
                        location=f"Paragraph {i+1}",
                        description="Duplicate content detected",
                        suggestion=f"Reference paragraph {seen_content[fingerprint]+1} instead of duplicating"
                    ))
                else:
                    seen_content[fingerprint] = i

        # Check for missing references to external docs
        external_refs = len(re.findall(r'\[.*?\]\(.*?\)', content))
        doc_length = len(content)

        if doc_length > 5000 and external_refs < 5:
            violations.append(PrincipleViolation(
                principle=DesignPrinciple.REFERENCES,
                severity='low',
                location="Global",
                description="Large document with few external references",
                suggestion="Add references to external documentation instead of duplicating"
            ))

        return violations

    def _check_metrics(self, content: str) -> List[PrincipleViolation]:
        """Check for metrics vs subjective goals"""
        violations = []

        # Look for subjective terms without metrics
        subjective_terms = [
            (r'\bfast(er)?\b', "fast"),
            (r'\bslow\b', "slow"),
            (r'\bbetter\b', "better"),
            (r'\bimprove\b', "improve"),
            (r'\boptimize\b', "optimize"),
            (r'\befficient\b', "efficient"),
            (r'\bperformance\b', "performance"),
            (r'\bquality\b', "quality")
        ]

        for pattern, term in subjective_terms:
            matches = list(re.finditer(pattern, content, re.IGNORECASE))
            for match in matches:
                # Check if there's a metric nearby (within 50 chars)
                context_start = max(0, match.start() - 50)
                context_end = min(len(content), match.end() + 50)
                context = content[context_start:context_end]

                # Look for metrics (numbers, percentages, units)
                has_metric = bool(re.search(r'\d+(?:\.\d+)?(?:\s*(?:%|ms|s|MB|GB|tokens))?', context))

                if not has_metric:
                    line_num = content[:match.start()].count('\n') + 1
                    violations.append(PrincipleViolation(
                        principle=DesignPrinciple.METRICS,
                        severity='low',
                        location=f"Line {line_num}: '{term}'",
                        description=f"Subjective term '{term}' without metric",
                        suggestion=f"Add specific metric: e.g., '{term}' → '< 100ms' or '> 95%'"
                    ))

        # Check for success criteria without metrics
        if 'success' in content.lower() or 'criteria' in content.lower():
            success_sections = re.findall(
                r'(?:success|criteria)[^.]*\.',
                content,
                re.IGNORECASE
            )

            for section in success_sections:
                if not re.search(r'\d+', section):
                    violations.append(PrincipleViolation(
                        principle=DesignPrinciple.METRICS,
                        severity='medium',
                        location="Success criteria",
                        description="Success criteria without measurable metrics",
                        suggestion="Define success with specific numbers/thresholds"
                    ))

        return violations

    def apply_transformations(self, content: str) -> str:
        """
        Apply automatic transformations to improve the workflow

        Args:
            content: Original workflow content

        Returns:
            Optimized workflow content
        """
        optimized = content

        # Transform 1: Convert advisory to guards
        advisory_to_guard = {
            r'\bshould\b': 'MUST',
            r'\bshould not\b': 'MUST NOT',
            r'\bmay\b': 'CAN',
            r'\bmight\b': 'COULD',
            r'\bprefer(ably)?\b': 'MUST',
            r'\btry to\b': 'MUST attempt to',
            r'\bif possible\b': '(when conditions allow)'
        }

        for pattern, replacement in advisory_to_guard.items():
            optimized = re.sub(pattern, replacement, optimized, flags=re.IGNORECASE)

        # Transform 2: Add TL;DR if missing
        if 'tl;dr' not in optimized.lower()[:500]:
            lines = optimized.split('\n')
            # Find first header
            for i, line in enumerate(lines):
                if line.startswith('#'):
                    tldr = self._generate_tldr(optimized)
                    lines.insert(i+1, f"\n**TL;DR**: {tldr}\n")
                    break
            optimized = '\n'.join(lines)

        # Transform 3: Add failure paths template
        if 'error' not in optimized.lower() and 'failure' not in optimized.lower():
            failure_section = """
## Error Handling

### Failure Scenarios

| Operation | Failure Mode | Detection | Recovery |
|-----------|--------------|-----------|----------|
| [Operation] | [What can fail] | [How to detect] | [Recovery action] |

### Rollback Procedures

1. **Automatic Rollback**: [Conditions for auto-rollback]
2. **Manual Rollback**: [Steps for manual intervention]
3. **Partial Rollback**: [Handling partial failures]
"""
            optimized += "\n" + failure_section

        # Transform 4: Add metrics template
        if 'metric' not in optimized.lower():
            metrics_section = """
## Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Execution Time | < 30 seconds | Timer from start to completion |
| Success Rate | > 95% | Successful runs / Total runs |
| Resource Usage | < 1000 tokens | Token counter per execution |
"""
            optimized += "\n" + metrics_section

        return optimized

    def _generate_tldr(self, content: str) -> str:
        """Generate a TL;DR summary for the workflow"""
        # Extract key information
        lines = content.split('\n')[:50]  # First 50 lines

        # Look for purpose/objective statements
        purpose_patterns = [
            r'purpose[:\s]+(.+)',
            r'objective[:\s]+(.+)',
            r'goal[:\s]+(.+)',
            r'this (?:workflow|process|procedure) (.+)',
        ]

        for pattern in purpose_patterns:
            match = re.search(pattern, '\n'.join(lines), re.IGNORECASE)
            if match:
                return match.group(1).strip()[:200]

        # Fallback: extract first meaningful sentence
        for line in lines:
            if len(line) > 20 and not line.startswith('#'):
                return line.strip()[:200]

        return "Workflow automation process with defined steps and validation"

    def generate_optimization_report(self, content: str) -> str:
        """
        Generate a detailed optimization report in RFC format

        Args:
            content: Workflow content to analyze

        Returns:
            RFC-formatted optimization report
        """
        result = self.analyze_workflow(content)

        report = f"""# Workflow Optimization Report

## Executive Summary

**Score**: {result.score}/100
**Violations Found**: {len(result.violations)}
**Critical Issues**: {len([v for v in result.violations if v.severity == 'high'])}

## Principle Analysis

"""

        # Group violations by principle
        by_principle = {}
        for violation in result.violations:
            if violation.principle not in by_principle:
                by_principle[violation.principle] = []
            by_principle[violation.principle].append(violation)

        # Report each principle
        for principle in DesignPrinciple:
            violations = by_principle.get(principle, [])
            status = "✅ PASS" if not violations else f"❌ FAIL ({len(violations)} issues)"

            report += f"### {principle.value}\n\n"
            report += f"**Status**: {status}\n\n"

            if violations:
                report += "**Issues**:\n\n"
                for v in violations:
                    report += f"- **[{v.severity.upper()}]** {v.description}\n"
                    report += f"  - Location: {v.location}\n"
                    report += f"  - Suggestion: {v.suggestion}\n\n"
            else:
                report += "No violations detected.\n\n"

        # Add recommendations
        report += """## Recommendations

### Priority 1: Critical Fixes
"""
        critical = [v for v in result.violations if v.severity == 'high']
        for v in critical[:5]:
            report += f"1. {v.suggestion} (Location: {v.location})\n"

        report += """
### Priority 2: Important Improvements
"""
        medium = [v for v in result.violations if v.severity == 'medium']
        for v in medium[:5]:
            report += f"1. {v.suggestion} (Location: {v.location})\n"

        report += """
### Priority 3: Nice to Have
"""
        low = [v for v in result.violations if v.severity == 'low']
        for v in low[:5]:
            report += f"1. {v.suggestion} (Location: {v.location})\n"

        report += """
## Implementation Guide

To implement these optimizations:

1. **Start with Critical Fixes**: Address all high-severity violations first
2. **Apply Transformations**: Use `apply_transformations()` for automatic fixes
3. **Manual Review**: Review and adjust the automated changes
4. **Test Changes**: Validate the optimized workflow with real scenarios
5. **Iterate**: Re-run analysis to confirm improvements

## References

- [7 Principles of LLM Engineering](https://docs.example.com/llm-principles)
- [Workflow Best Practices](https://docs.example.com/workflow-patterns)
- [Binary Decision Trees](https://docs.example.com/decision-trees)

---
*Generated by WorkflowOptimizer v1.0.0*
"""

        return report

    def suggest_workflow_pattern(self, task_type: str) -> str:
        """
        Suggest an optimal workflow pattern based on task type

        Args:
            task_type: Type of task (deployment, validation, troubleshooting, etc.)

        Returns:
            Suggested workflow pattern
        """
        patterns = {
            "deployment": """# Deployment Workflow

## TL;DR
Binary decision tree for safe deployment with automatic rollback on failure.

## Pre-Deployment Guards
- [ ] All tests MUST pass
- [ ] Approval MUST be obtained for T3 operations
- [ ] Backup MUST exist

## Decision Tree

```
1. Is environment production?
   YES -> Require approval (goto 2)
   NO  -> Continue (goto 2)

2. Are all tests passing?
   YES -> Continue (goto 3)
   NO  -> ABORT with error

3. Is backup current?
   YES -> Deploy (goto 4)
   NO  -> Create backup first (goto 3)

4. Did deployment succeed?
   YES -> Validate (goto 5)
   NO  -> Rollback (goto 6)

5. Is validation passing?
   YES -> Complete
   NO  -> Rollback (goto 6)

6. Is rollback successful?
   YES -> Alert team
   NO  -> ESCALATE to on-call
```

## Success Metrics
- Deployment time: < 5 minutes
- Rollback time: < 2 minutes
- Success rate: > 99%
""",
            "validation": """# Validation Workflow

## TL;DR
Systematic validation with binary pass/fail at each step.

## Validation Guards
- [ ] Each check MUST have a binary outcome
- [ ] Failures MUST stop the process
- [ ] All checks MUST be logged

## Validation Steps

```
1. Schema valid?
   YES -> Continue
   NO  -> Return error details

2. Constraints met?
   YES -> Continue
   NO  -> Return constraint violations

3. Dependencies available?
   YES -> Continue
   NO  -> List missing dependencies

4. Integration tests pass?
   YES -> Mark as valid
   NO  -> Return test failures
```

## Metrics
- Validation time: < 10 seconds
- False positive rate: < 1%
- Coverage: > 95%
""",
            "troubleshooting": """# Troubleshooting Workflow

## TL;DR
Binary search through problem space with metric-based decisions.

## Diagnostic Guards
- [ ] MUST collect metrics before diagnosis
- [ ] MUST NOT modify system during diagnosis
- [ ] MUST log all findings

## Diagnostic Tree

```
1. Is service responding?
   YES -> Check performance (goto 2)
   NO  -> Check availability (goto 3)

2. Response time < 1000ms?
   YES -> Check errors (goto 4)
   NO  -> Diagnose slowness (goto 5)

3. Is process running?
   YES -> Check connectivity (goto 6)
   NO  -> Check crash logs (goto 7)

4. Error rate < 1%?
   YES -> System healthy
   NO  -> Analyze error patterns (goto 8)
```

## Resolution Metrics
- Time to diagnosis: < 5 minutes
- Root cause accuracy: > 90%
- False positives: < 5%
"""
        }

        return patterns.get(task_type, self._generate_generic_pattern(task_type))

    def _generate_generic_pattern(self, task_type: str) -> str:
        """Generate a generic optimized pattern for unknown task types"""
        return f"""# {task_type.title()} Workflow

## TL;DR
Optimized workflow for {task_type} following the 7 LLM principles.

## Guards
- [ ] Prerequisite X MUST be met
- [ ] Validation Y MUST pass
- [ ] Approval MUST be obtained for T3

## Binary Decision Flow

```
1. Prerequisites met?
   YES -> Continue
   NO  -> Abort with requirements

2. Validation passed?
   YES -> Execute
   NO  -> Return validation errors

3. Execution successful?
   YES -> Verify
   NO  -> Rollback

4. Verification passed?
   YES -> Complete
   NO  -> Investigate
```

## Error Handling

| Step | Failure Mode | Recovery |
|------|--------------|----------|
| Prerequisites | Missing requirement | List requirements |
| Validation | Invalid input | Return specific errors |
| Execution | Operation failed | Automatic rollback |
| Verification | Check failed | Manual intervention |

## Success Metrics
- Execution time: < X seconds
- Success rate: > 95%
- Error recovery: < Y seconds

## References
- [Related Documentation]
- [Tool Contracts]
- [Error Codes]
"""


def main():
    """Main function for testing the optimizer"""
    import sys

    optimizer = WorkflowOptimizer()

    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "analyze":
            if len(sys.argv) > 2:
                with open(sys.argv[2]) as f:
                    content = f.read()
                result = optimizer.analyze_workflow(content)
                print(f"Score: {result.score}/100")
                print(f"Violations: {len(result.violations)}")
                for v in result.violations:
                    print(f"- [{v.severity}] {v.principle.value}: {v.description}")
            else:
                print("Usage: python workflow_optimizer.py analyze <workflow_file>")

        elif command == "optimize":
            if len(sys.argv) > 2:
                with open(sys.argv[2]) as f:
                    content = f.read()
                optimized = optimizer.apply_transformations(content)
                print(optimized)
            else:
                print("Usage: python workflow_optimizer.py optimize <workflow_file>")

        elif command == "report":
            if len(sys.argv) > 2:
                with open(sys.argv[2]) as f:
                    content = f.read()
                report = optimizer.generate_optimization_report(content)
                print(report)
            else:
                print("Usage: python workflow_optimizer.py report <workflow_file>")

        elif command == "pattern":
            task_type = input("Enter task type (deployment/validation/troubleshooting): ")
            pattern = optimizer.suggest_workflow_pattern(task_type)
            print(pattern)

        else:
            print(f"Unknown command: {command}")

    else:
        print("""
WorkflowOptimizer - Apply 7 LLM Engineering Principles

Usage:
  python workflow_optimizer.py analyze <file>  - Analyze workflow
  python workflow_optimizer.py optimize <file> - Apply optimizations
  python workflow_optimizer.py report <file>   - Generate RFC report
  python workflow_optimizer.py pattern         - Suggest workflow pattern

Principles Applied:
  1. Binary Decision Trees
  2. Guards Over Advice
  3. Tool Contracts
  4. Failure Paths
  5. TL;DR First
  6. References Over Duplication
  7. Metrics Over Subjective Goals
        """)


if __name__ == "__main__":
    main()