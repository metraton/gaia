#!/usr/bin/env python3
"""
Subagent stop hook for Claude Code Agent System

Handles session state persistence when specialized agents complete execution.

Responsibilities:
1. Update .claude/session/active/ with current session state
2. Collect and index artifacts generated during agent execution
3. Persist metadata for session continuity
4. Prepare state for potential bundle creation (via /save-session)

Architecture:
- Part of the hybrid session management system
- Updates live session context (.claude/session/active/)
- Does NOT create bundles directly (bundles are on-demand via /save-session)
- Enables artifact collection for later bundling

Integration:
- Executed automatically after agent tool completes
- Works with session_startup_check.py for initialization
- Feeds data to /save-session for explicit bundling
- Maintains artifact inventory for historical reference
"""

import os
import sys
import json
import shutil
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
import hashlib
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_workflow_memory_dir() -> Path:
    """
    Get workflow memory directory path.

    Supports override via WORKFLOW_MEMORY_BASE_PATH env var for testing.
    In production, uses .claude/memory/workflow-episodic relative to CWD.
    """
    base_path = os.environ.get("WORKFLOW_MEMORY_BASE_PATH")
    if base_path:
        return Path(base_path) / "memory" / "workflow-episodic"
    return Path(".claude/memory/workflow-episodic")


class ArtifactCopyError(Exception):
    """Custom exception for artifact copy failures"""
    pass


class BundleCreationError(Exception):
    """Custom exception for bundle creation failures"""
    pass

class SessionManager:
    """Manages session state and bundle creation"""

    def __init__(self, bundle_dir: str = None):
        if bundle_dir is None:
            # Find the .claude directory by looking upward from current location
            claude_dir = self._find_claude_dir()
            bundle_dir = claude_dir / "session" / "bundles"

        self.bundle_dir = Path(bundle_dir)
        self.bundle_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = self._get_or_create_session_id()
        self.current_bundle_path = self.bundle_dir / f"{datetime.now().strftime('%Y-%m-%d')}-{self.session_id}"
        logger.info(f"SessionManager initialized with bundle_dir={self.bundle_dir}, session_id={self.session_id}")

    def _find_claude_dir(self) -> Path:
        """Find the .claude directory by searching upward from current location"""
        current = Path.cwd()

        # If we're already in a .claude directory, go up one level
        if current.name == ".claude":
            logger.debug("Already in .claude directory")
            return current

        # Look for .claude in current directory
        claude_dir = current / ".claude"
        if claude_dir.exists():
            logger.debug(f"Found .claude at {claude_dir}")
            return claude_dir

        # Search upward through parent directories
        for parent in current.parents:
            claude_dir = parent / ".claude"
            if claude_dir.exists():
                logger.debug(f"Found .claude at {claude_dir}")
                return claude_dir

        # Default fallback - create .claude in current directory
        logger.warning("No .claude directory found, creating in current directory")
        claude_dir = current / ".claude"
        claude_dir.mkdir(exist_ok=True)
        return claude_dir

    def _get_or_create_session_id(self) -> str:
        """Get existing session ID or create new one"""
        session_id = os.environ.get("CLAUDE_SESSION_ID")
        if not session_id:
            # Generate session ID based on timestamp and some randomness
            timestamp = datetime.now().strftime("%H%M%S")
            hash_input = f"{timestamp}-{os.getpid()}"
            session_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:8]
            session_id = f"session-{timestamp}-{session_hash}"
            os.environ["CLAUDE_SESSION_ID"] = session_id
            logger.info(f"Generated new session_id: {session_id}")
        else:
            logger.info(f"Using existing session_id: {session_id}")
        return session_id

class BundleManager:
    """Manages artifact bundling and session persistence with enhanced error handling"""

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager
        self.bundle_path = session_manager.current_bundle_path
        logger.info(f"BundleManager initialized with bundle_path={self.bundle_path}")

    def create_bundle(self, task_info: Dict[str, Any], artifacts: List[str],
                     agent_transcript: str) -> Path:
        """
        Create a bundle with task artifacts and metadata

        Raises:
            BundleCreationError: If bundle creation fails critically
        """
        try:
            # Create bundle directory
            self.bundle_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created bundle directory: {self.bundle_path}")

            # Bundle metadata
            bundle_metadata = {
                "session_id": self.session_manager.session_id,
                "timestamp": datetime.now().isoformat(),
                "task_info": task_info,
                "artifacts": artifacts,
                "bundle_version": "1.1",
                "errors": []  # Track any non-critical errors
            }

            # Save metadata
            self._save_metadata(bundle_metadata)

            # Save transcript
            self._save_transcript(agent_transcript)

            # Copy artifacts (with error tracking)
            copy_errors = self._copy_artifacts(artifacts)
            if copy_errors:
                bundle_metadata["errors"].extend(copy_errors)
                # Re-save metadata with error info
                self._save_metadata(bundle_metadata)

            # Generate summary
            self._generate_summary(task_info, artifacts, copy_errors)

            # Update latest symlink
            self._update_latest_link()

            logger.info(f"Bundle created successfully: {self.bundle_path}")
            return self.bundle_path

        except Exception as e:
            logger.error(f"Critical error creating bundle: {e}", exc_info=True)
            raise BundleCreationError(f"Failed to create bundle: {e}") from e

    def _save_metadata(self, metadata: Dict[str, Any]):
        """Save metadata with error handling"""
        try:
            metadata_file = self.bundle_path / "metadata.json"
            with open(metadata_file, "w") as f:
                json.dump(metadata, f, indent=2)
            logger.debug(f"Saved metadata to {metadata_file}")
        except Exception as e:
            logger.error(f"Failed to save metadata: {e}")
            raise

    def _save_transcript(self, transcript: str):
        """Save transcript with error handling"""
        try:
            transcript_file = self.bundle_path / "transcript.md"
            with open(transcript_file, "w") as f:
                f.write(transcript)
            logger.debug(f"Saved transcript to {transcript_file}")
        except Exception as e:
            logger.error(f"Failed to save transcript: {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(ArtifactCopyError)
    )
    def _copy_single_artifact(self, artifact_path: Path, dest_path: Path):
        """
        Copy a single artifact with retry logic

        Raises:
            ArtifactCopyError: If copy fails after retries
        """
        try:
            shutil.copy2(artifact_path, dest_path)

            # Verify copy
            if not dest_path.exists():
                raise ArtifactCopyError(f"Destination file not created: {dest_path}")

            if dest_path.stat().st_size == 0 and artifact_path.stat().st_size > 0:
                raise ArtifactCopyError(f"Destination file empty: {dest_path}")

            logger.debug(f"Successfully copied artifact: {artifact_path.name}")

        except Exception as e:
            logger.warning(f"Failed to copy {artifact_path}: {e}")
            raise ArtifactCopyError(f"Copy failed: {e}") from e

    def _copy_artifacts(self, artifacts: List[str]) -> List[str]:
        """
        Copy artifact files to bundle directory with graceful degradation

        Returns:
            List of error messages for artifacts that failed to copy
        """
        artifacts_dir = self.bundle_path / "artifacts"
        artifacts_dir.mkdir(exist_ok=True)

        errors = []
        successful_copies = 0

        for artifact_path_str in artifacts:
            artifact_file = Path(artifact_path_str)

            if not artifact_file.exists():
                error_msg = f"Artifact not found: {artifact_path_str}"
                logger.warning(error_msg)
                errors.append(error_msg)
                continue

            try:
                dest_path = artifacts_dir / artifact_file.name
                self._copy_single_artifact(artifact_file, dest_path)
                successful_copies += 1

            except Exception as e:
                error_msg = f"Failed to copy {artifact_path_str}: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)
                # Continue with other artifacts

        logger.info(f"Copied {successful_copies}/{len(artifacts)} artifacts successfully")

        if errors:
            logger.warning(f"Encountered {len(errors)} errors during artifact copy")

        return errors

    def _generate_summary(self, task_info: Dict[str, Any], artifacts: List[str],
                         copy_errors: List[str]):
        """Generate bundle summary with error information"""
        try:
            summary_content = [
                f"# Bundle Summary - {self.session_manager.session_id}",
                f"",
                f"**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"**Task**: {task_info.get('task_id', 'Unknown')} - {task_info.get('description', 'Unknown')}",
                f"**Agent**: {task_info.get('agent', 'Unknown')}",
                f"**Security Tier**: {task_info.get('tier', 'Unknown')}",
                f"",
                f"## Artifacts Generated",
                f"",
            ]

            for artifact in artifacts:
                artifact_name = Path(artifact).name
                status = "❌" if any(artifact in err for err in copy_errors) else "✅"
                summary_content.append(f"{status} `{artifact_name}`: {artifact}")

            if copy_errors:
                summary_content.extend([
                    f"",
                    f"## ⚠️ Errors Encountered",
                    f"",
                ])
                for error in copy_errors:
                    summary_content.append(f"- {error}")

            summary_content.extend([
                f"",
                f"## Task Context",
                f"",
                f"- **Working Directory**: {task_info.get('working_dir', 'Unknown')}",
                f"- **Tags**: {', '.join(task_info.get('tags', []))}",
                f"- **Project Context**: {task_info.get('project_context', 'None')}",
                f"",
                f"## Next Steps",
                f"",
                f"Review artifacts in `artifacts/` directory and check transcript for detailed execution log.",
                f"",
            ])

            summary_file = self.bundle_path / "summary.md"
            with open(summary_file, "w") as f:
                f.write("\n".join(summary_content))

            logger.debug(f"Generated summary at {summary_file}")

        except Exception as e:
            logger.error(f"Failed to generate summary: {e}")
            # Non-critical, don't raise

    def _update_latest_link(self):
        """Update latest bundle symlink"""
        latest_link = self.bundle_path.parent / "latest"

        try:
            # Remove existing symlink
            if latest_link.is_symlink():
                latest_link.unlink()

            # Create new symlink
            latest_link.symlink_to(self.bundle_path.name)
            logger.debug(f"Updated latest symlink to {self.bundle_path.name}")

        except Exception as e:
            logger.warning(f"Could not create latest symlink: {e}")
            # Non-critical, don't raise

class ArtifactCollector:
    """Collects artifacts generated during agent execution"""

    def __init__(self, working_dir: str = "."):
        self.working_dir = Path(working_dir)
        self.common_artifact_patterns = [
            "*-validation-report.md",
            "*-results-summary.md",
            "*.tfplan",
            "*-analysis.md",
            "*-recommendations.md",
            "security-scan-results.md",
            "terraform-validation-report.md",
            "gitops-validation-report.md",
            "gcp-health-queries.md",
            "gcp-health-results-summary.md",
        ]
        logger.info(f"ArtifactCollector initialized with working_dir={self.working_dir}")

    def collect_artifacts(self, task_id: str = None) -> List[str]:
        """
        Collect all relevant artifacts from working directory with error handling

        Returns:
            List of artifact paths
        """
        artifacts = []

        try:
            # Collect by pattern
            for pattern in self.common_artifact_patterns:
                try:
                    matching_files = list(self.working_dir.glob(pattern))
                    artifacts.extend([str(f) for f in matching_files])
                except Exception as e:
                    logger.warning(f"Pattern {pattern} failed: {e}")
                    continue

            # Collect task-specific artifacts
            if task_id:
                try:
                    task_pattern = f"*{task_id.lower()}*"
                    task_files = list(self.working_dir.glob(task_pattern))
                    artifacts.extend([str(f) for f in task_files if f.suffix in ['.md', '.txt', '.json', '.yaml']])
                except Exception as e:
                    logger.warning(f"Task-specific collection failed: {e}")

            # Collect recent agent-generated files (created in last hour)
            try:
                import time
                current_time = time.time()
                agent_keywords = ['report', 'analysis', 'validation', 'summary', 'results', 'recommendations']
                for file_path in self.working_dir.rglob("*"):
                    if file_path.is_file():
                        file_age = current_time - file_path.stat().st_mtime
                        if file_age < 3600 and file_path.suffix in ['.md', '.txt', '.json', '.yaml']:
                            if any(keyword in file_path.name.lower() for keyword in agent_keywords):
                                artifacts.append(str(file_path))
            except Exception as e:
                logger.warning(f"Recent file collection failed: {e}")

            # Remove duplicates
            artifacts = list(set(artifacts))

            # Filter out system files
            artifacts = [a for a in artifacts if not any(exclude in a for exclude in ['.git', '__pycache__', '.claude'])]

            logger.info(f"Collected {len(artifacts)} artifacts")
            return artifacts

        except Exception as e:
            logger.error(f"Error collecting artifacts: {e}")
            return []  # Return empty list on error, don't crash

def subagent_stop_hook(task_info: Dict[str, Any], agent_output: str) -> Dict[str, Any]:
    """
    Main subagent stop hook - OPTION A: SILENT MODE

    Philosophy:
    - Only updates .claude/session/active/ with session state
    - Does NOT create bundles automatically
    - Bundles are created on-demand via /save-session
    - Keeps everything silent and non-intrusive

    Args:
        task_info: Task information including ID, description, agent, etc.
        agent_output: Complete output from agent execution

    Returns:
        Success confirmation (no bundle creation)
    """

    try:
        # Initialize session manager (NOT bundle manager)
        session_manager = SessionManager()

        # Update .claude/session/active/ with current session state
        active_dir = Path(session_manager._find_claude_dir()) / "session" / "active"
        active_dir.mkdir(parents=True, exist_ok=True)

        # Save session context for this agent execution
        session_context = {
            "timestamp": datetime.now().isoformat(),
            "session_id": session_manager.session_id,
            "task_id": task_info.get('task_id', 'unknown'),
            "agent": task_info.get('agent', 'unknown'),
            "description": task_info.get('description', ''),
            "tier": task_info.get('tier', 'unknown'),
            "tags": task_info.get('tags', []),
            "working_dir": str(task_info.get('working_dir', '.')),
            "project_context": task_info.get('project_context', ''),
            # Store agent output for later retrieval
            "agent_output_available": len(agent_output) > 0
        }

        # Save context as JSON
        context_file = active_dir / "context.json"
        with open(context_file, "w") as f:
            json.dump(session_context, f, indent=2)

        logger.debug(f"✅ Updated session context: {context_file}")

        # Silently collect artifacts (for later use by /save-session)
        artifact_collector = ArtifactCollector()
        artifacts = artifact_collector.collect_artifacts(task_info.get('task_id'))
        logger.debug(f"📊 Artifacts available for bundling: {len(artifacts)}")

        # NEW: Capture workflow metrics
        workflow_metrics = capture_workflow_metrics(task_info, agent_output, session_context)

        # NEW: Check for anomalies
        anomalies = detect_anomalies(workflow_metrics)

        if anomalies:
            logger.warning(f"🚨 {len(anomalies)} anomalies detected in workflow")
            # Signal for Gaia analysis
            signal_gaia_analysis(anomalies, workflow_metrics)

        # Return success (silent - no bundle created yet)
        return {
            "success": True,
            "session_id": session_manager.session_id,
            "status": "active_updated",
            "artifacts_available": len(artifacts),
            "metrics_captured": True,  # NEW
            "anomalies_detected": len(anomalies) if anomalies else 0,  # NEW
            "note": "Bundle creation deferred to /save-session"
        }

    except Exception as e:
        logger.debug(f"⚠️ Error updating session context: {e}")
        # Don't fail completely - just log and continue
        return {
            "success": False,
            "error": str(e),
            "status": "partial_update",
            "note": "Session context update failed, but agent execution may have succeeded"
        }

def capture_workflow_metrics(task_info: Dict[str, Any], agent_output: str, session_context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Capture workflow execution metrics for analysis.

    Args:
        task_info: Task metadata
        agent_output: Output from agent execution
        session_context: Current session context

    Returns:
        Dict with duration, exit_code, agent, tier, etc.
    """
    import re
    import time

    # Try to extract duration from agent output
    duration_match = re.search(r"Duration:\s*(\d+)\s*ms", agent_output)
    if not duration_match:
        # Alternative pattern
        duration_match = re.search(r"took\s+(\d+(?:\.\d+)?)\s*(?:seconds?|s)", agent_output)
        if duration_match:
            duration_ms = int(float(duration_match.group(1)) * 1000)
        else:
            duration_ms = None
    else:
        duration_ms = int(duration_match.group(1))

    # Try to extract exit code
    exit_code = 0  # Default to success
    if "error" in agent_output.lower() or "failed" in agent_output.lower():
        exit_code_match = re.search(r"exit\s+code:?\s*(\d+)", agent_output.lower())
        if exit_code_match:
            exit_code = int(exit_code_match.group(1))
        else:
            exit_code = 1  # Generic error

    metrics = {
        "timestamp": session_context["timestamp"],
        "session_id": session_context["session_id"],
        "task_id": task_info.get("task_id", "unknown"),
        "agent": task_info.get("agent", "unknown"),
        "tier": task_info.get("tier", "unknown"),
        "duration_ms": duration_ms,
        "exit_code": exit_code,
        "output_length": len(agent_output),
        "tags": task_info.get("tags", [])
    }

    # Save to workflow memory
    workflow_memory_dir = get_workflow_memory_dir()
    workflow_memory_dir.mkdir(parents=True, exist_ok=True)

    metrics_file = workflow_memory_dir / "metrics.jsonl"
    with open(metrics_file, "a") as f:
        f.write(json.dumps(metrics) + "\n")

    logger.debug(f"📊 Captured workflow metrics: {metrics['agent']} (duration: {duration_ms}ms, exit: {exit_code})")

    return metrics


def detect_anomalies(metrics: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Detect anomalies in workflow execution.

    Checks:
    - Slow execution (> 120s)
    - Failed executions (exit_code != 0)
    - Consecutive failures (3+ in a row)

    Returns:
        List of anomaly descriptions
    """
    anomalies = []

    # Check duration (if available)
    if metrics.get("duration_ms") and metrics["duration_ms"] > 120000:
        anomalies.append({
            "type": "slow_execution",
            "severity": "warning",
            "message": f"Agent {metrics['agent']} took {metrics['duration_ms']/1000:.1f}s (threshold: 120s)"
        })

    # Check exit code
    if metrics.get("exit_code", 0) != 0:
        anomalies.append({
            "type": "execution_failure",
            "severity": "error",
            "message": f"Agent {metrics['agent']} failed with exit code {metrics['exit_code']}"
        })

    # Check consecutive failures (read last N metrics)
    try:
        workflow_memory_dir = get_workflow_memory_dir()
        metrics_file = workflow_memory_dir / "metrics.jsonl"

        if metrics_file.exists():
            with open(metrics_file) as f:
                lines = f.readlines()
                # Get last 5 metrics (excluding current)
                if len(lines) >= 5:
                    last_5 = [json.loads(line) for line in lines[-6:-1]]  # Skip current (last one)
                else:
                    last_5 = [json.loads(line) for line in lines[:-1]] if len(lines) > 1 else []

                # Count recent failures for same agent
                agent = metrics["agent"]
                recent_failures = [
                    m for m in last_5
                    if m.get("agent") == agent and m.get("exit_code", 0) != 0
                ]

                # If current also failed and we have 2+ previous failures
                if metrics.get("exit_code", 0) != 0 and len(recent_failures) >= 2:
                    anomalies.append({
                        "type": "consecutive_failures",
                        "severity": "critical",
                        "message": f"Agent {agent} has failed {len(recent_failures) + 1} times consecutively"
                    })
    except Exception as e:
        logger.debug(f"Could not check consecutive failures: {e}")

    return anomalies


def signal_gaia_analysis(anomalies: List[Dict], metrics: Dict[str, Any]):
    """
    Signal that Gaia analysis is needed.

    Creates a flag file that orchestrator can detect.
    """
    try:
        signals_dir = get_workflow_memory_dir() / "signals"
        signals_dir.mkdir(parents=True, exist_ok=True)

        signal_file = signals_dir / "needs_analysis.flag"

        signal_data = {
            "timestamp": datetime.now().isoformat(),
            "anomalies": anomalies,
            "metrics_summary": {
                "agent": metrics["agent"],
                "task_id": metrics["task_id"],
                "duration_ms": metrics.get("duration_ms"),
                "exit_code": metrics.get("exit_code")
            },
            "suggested_action": "Invoke /gaia for system analysis"
        }

        with open(signal_file, "w") as f:
            json.dump(signal_data, f, indent=2)

        logger.info(f"🚨 Gaia analysis signal created: {signal_file}")

        # Also log to a permanent anomaly log
        anomaly_log = signals_dir.parent / "anomalies.jsonl"
        with open(anomaly_log, "a") as f:
            f.write(json.dumps({
                "timestamp": datetime.now().isoformat(),
                "anomalies": anomalies,
                "metrics": metrics
            }) + "\n")

    except Exception as e:
        logger.warning(f"Could not create analysis signal: {e}")


def main():
    """CLI interface for testing bundle creation"""

    if len(sys.argv) < 2:
        print("Usage: python subagent_stop.py <task_id>")
        print("       python subagent_stop.py --test")
        sys.exit(1)

    if sys.argv[1] == "--test":
        # Test bundle creation
        test_task_info = {
            "task_id": "T006",
            "description": "Terraform plan for infrastructure",
            "agent": "terraform-specialist",
            "tier": "T1",
            "tags": ["#terraform", "#infrastructure"],
            "working_dir": os.getcwd(),
            "project_context": "Multi-project repository with TCM application"
        }

        test_output = """
# Terraform Specialist Execution Log

## Task: T006 - Terraform plan for infrastructure

### Actions Performed:
1. ✅ Format check: terraform fmt -check
2. ✅ Initialization: terraform init -backend=false
3. ✅ Validation: terraform validate
4. ✅ Planning: terraform plan -out=tfplan

### Results:
- Configuration is properly formatted
- All modules validated successfully
- Plan generated with 12 resources to create
- No security issues detected

### Artifacts Generated:
- terraform-validation-report.md
- infrastructure.tfplan
- security-scan-results.md

### Recommendations:
- Review tfplan before any apply operations
- Consider implementing resource tagging
- Enable state locking for production
"""

        result = subagent_stop_hook(test_task_info, test_output)

        if result["success"]:
            print("✅ Test bundle creation successful!")
            print(f"📁 Bundle path: {result['bundle_path']}")
            print(f"📊 Artifacts: {result['artifacts_count']}")

            # Show bundle contents
            bundle_path = Path(result["bundle_path"])
            if bundle_path.exists():
                print("\n📋 Bundle contents:")
                for item in sorted(bundle_path.rglob("*")):
                    if item.is_file():
                        relative_path = item.relative_to(bundle_path)
                        print(f"  {relative_path}")
        else:
            print(f"❌ Test failed: {result['error']}")

    else:
        task_id = sys.argv[1]
        print(f"Creating bundle for task: {task_id}")
        # In real usage, this would be called by the agent system
        print("Note: This would normally be called automatically by the agent system")

if __name__ == "__main__":
    main()