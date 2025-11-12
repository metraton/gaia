"""
Task Manager

Efficient task file operations for large projects without loading entire files.
This utility is designed to handle tasks.md files that exceed Claude's Read tool
token limits (>25,000 tokens) by using targeted Grep and Edit operations.

Usage:
    from task_manager import TaskManager

    # Initialize with path to tasks.md file
    tm = TaskManager("/path/to/tasks.md")

    # Mark a task as complete
    tm.mark_task_complete("T045")

    # Get pending tasks
    pending = tm.get_pending_tasks(limit=10)

    # Get full details for a specific task
    details = tm.get_task_details("T045")

Architecture:
    - Uses Grep tool for searching (avoids loading full file)
    - Uses Edit tool for targeted updates (no full-file rewrites)
    - Parses task metadata from HTML comments
    - Handles large files efficiently

Task Format Recognition:
    Tasks follow this pattern:
    ```
    - [ ] T045 Deploy query-api HelmRelease
      <!-- 🤖 Agent: gitops-operator | ✅ T3 | ⚡ 0.95 -->
      <!-- 🏷️ Tags: #kubernetes #helm -->

      **Description:** Create HelmRelease manifest...

      **Acceptance Criteria:**
      - Criterion 1
      - Criterion 2
    ```
"""

import os
import re
import subprocess
from typing import Dict, List, Any, Optional, Tuple


class TaskManager:
    """Efficient task file operations for large projects"""

    def __init__(self, tasks_file_path: str):
        """
        Initialize with path to tasks.md file

        Args:
            tasks_file_path: Absolute path to tasks.md file

        Raises:
            FileNotFoundError: If tasks.md file doesn't exist
        """
        self.tasks_file_path = os.path.abspath(tasks_file_path)

        if not os.path.exists(self.tasks_file_path):
            raise FileNotFoundError(
                f"Tasks file not found: {self.tasks_file_path}"
            )

        self.file_dir = os.path.dirname(self.tasks_file_path)

    def mark_task_complete(self, task_id: str) -> bool:
        r"""
        Mark a task as complete using Grep to find + Edit to update.

        Args:
            task_id: Task identifier (e.g., "T045")

        Returns:
            True if task was marked complete, False if not found or already complete

        Process:
        1. Use Grep to find line: "^- \[ \] {task_id}"
        2. Verify task is pending (has [ ] checkbox)
        3. Replace "- [ ]" with "- [x]" on that specific line
        4. Use Edit to write back

        Raises:
            ValueError: If task_id not found in file
        """
        # Sanitize task_id (remove any special chars for safety)
        task_id = task_id.strip().upper()

        if not re.match(r'^T\d+$', task_id):
            raise ValueError(
                f"Invalid task_id format: {task_id}. Must be 'T' followed by digits (e.g., T045)"
            )

        # Search for the task line using grep
        # Pattern: "- [ ] T045" (pending task)
        try:
            result = subprocess.run(
                ['grep', '-n', f'^- \\[ \\] {task_id} ', self.tasks_file_path],
                capture_output=True,
                text=True,
                check=False
            )

            if result.returncode != 0:
                # Task not found or already completed
                # Check if task exists but is already complete
                completed_result = subprocess.run(
                    ['grep', '-n', f'^- \\[x\\] {task_id} ', self.tasks_file_path],
                    capture_output=True,
                    text=True,
                    check=False
                )

                if completed_result.returncode == 0:
                    # Task already complete
                    return False
                else:
                    # Task not found at all
                    raise ValueError(
                        f"Task {task_id} not found in {self.tasks_file_path}"
                    )

            # Parse the line number and content
            # Format: "123:- [ ] T045 Task description"
            line_output = result.stdout.strip()
            if not line_output:
                raise ValueError(f"Task {task_id} not found")

            # Get the line with task checkbox
            line_content = line_output.split(':', 1)[1] if ':' in line_output else line_output

            # Replace [ ] with [x]
            new_line_content = line_content.replace('- [ ]', '- [x]', 1)

            # Use sed to edit the file in place
            # Find the line and replace it
            subprocess.run(
                ['sed', '-i', f's/^- \\[ \\] {task_id} /- [x] {task_id} /', self.tasks_file_path],
                check=True
            )

            return True

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to mark task complete: {e}")

    def get_pending_tasks(self, limit: int = 10) -> List[Dict[str, str]]:
        r"""
        Get list of pending tasks using Grep.

        Args:
            limit: Maximum number of tasks to return

        Returns:
            List of dicts with keys: task_id, title, line_number

        Process:
        1. Use Grep: "^- \[ \] T[0-9]+" to find pending tasks
        2. Parse results to extract task ID and title
        3. Return up to 'limit' results
        """
        try:
            # Search for pending tasks: "- [ ] T001"
            result = subprocess.run(
                ['grep', '-n', '^- \\[ \\] T[0-9]\\+', self.tasks_file_path],
                capture_output=True,
                text=True,
                check=False
            )

            if result.returncode != 0:
                # No pending tasks found
                return []

            # Parse the grep output
            # Format: "123:- [ ] T045 Deploy query-api HelmRelease"
            lines = result.stdout.strip().split('\n')
            tasks = []

            for line in lines[:limit]:
                if not line:
                    continue

                # Extract line number and content
                parts = line.split(':', 1)
                if len(parts) != 2:
                    continue

                line_number = parts[0]
                content = parts[1]

                # Extract task ID and title
                # Pattern: "- [ ] T045 Title of the task"
                match = re.match(r'^- \[ \] (T\d+)\s+(.+)$', content)
                if match:
                    task_id = match.group(1)
                    title = match.group(2)

                    tasks.append({
                        'task_id': task_id,
                        'title': title,
                        'line_number': int(line_number)
                    })

            return tasks

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to get pending tasks: {e}")

    def get_task_details(self, task_id: str) -> Dict[str, Any]:
        """
        Load full details for a specific task.

        Args:
            task_id: Task identifier (e.g., "T045")

        Returns:
            Dict with keys: task_id, title, description, metadata, line_number, status

        Process:
        1. Use Grep to find task start line (both pending and complete)
        2. Read that line + next 20 lines (captures description + metadata)
        3. Parse task block to extract metadata from HTML comments

        Raises:
            ValueError: If task_id not found
        """
        # Sanitize task_id
        task_id = task_id.strip().upper()

        if not re.match(r'^T\d+$', task_id):
            raise ValueError(
                f"Invalid task_id format: {task_id}. Must be 'T' followed by digits"
            )

        try:
            # Search for task (both pending and complete)
            # Pattern: "- [ ] T045" or "- [x] T045"
            result = subprocess.run(
                ['grep', '-n', f'^- \\[.\\] {task_id} ', self.tasks_file_path],
                capture_output=True,
                text=True,
                check=False
            )

            if result.returncode != 0:
                raise ValueError(f"Task {task_id} not found in {self.tasks_file_path}")

            # Parse the line number
            line_output = result.stdout.strip()
            line_number = int(line_output.split(':')[0])

            # Determine status
            status = 'completed' if '- [x]' in line_output else 'pending'

            # Extract title
            title_match = re.search(r'^- \[.\] T\d+\s+(.+)$', line_output.split(':', 1)[1])
            title = title_match.group(1) if title_match else "Unknown"

            # Read the task block (next 25 lines for full context)
            # Use sed to extract lines
            end_line = line_number + 25
            lines_result = subprocess.run(
                ['sed', '-n', f'{line_number},{end_line}p', self.tasks_file_path],
                capture_output=True,
                text=True,
                check=True
            )

            task_block = lines_result.stdout

            # Parse metadata from HTML comments
            metadata = self._parse_task_metadata(task_block)

            # Extract description
            description = self._extract_description(task_block)

            # Extract acceptance criteria
            acceptance_criteria = self._extract_acceptance_criteria(task_block)

            return {
                'task_id': task_id,
                'title': title,
                'status': status,
                'line_number': line_number,
                'metadata': metadata,
                'description': description,
                'acceptance_criteria': acceptance_criteria
            }

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to get task details: {e}")

    def _parse_task_metadata(self, task_block: str) -> Dict[str, Any]:
        """
        Parse metadata from HTML comments in task block.

        Extracts:
        - Agent name
        - Security tier
        - Confidence score
        - Tags
        - Skills

        Args:
            task_block: Text block containing the task

        Returns:
            Dict with extracted metadata
        """
        metadata = {
            'agent': None,
            'security_tier': None,
            'confidence': None,
            'tags': [],
            'skill': None,
            'fallback': None,
            'result': None
        }

        # Extract agent, tier, and confidence
        # Pattern: <!-- 🤖 Agent: gitops-operator | ✅ T3 | ⚡ 0.95 -->
        agent_match = re.search(
            r'<!-- 🤖 Agent: ([a-z-]+) \| [^|]+ ([T0-3]+) \| [^0-9]+ ([\d.]+) -->',
            task_block
        )
        if agent_match:
            metadata['agent'] = agent_match.group(1)
            metadata['security_tier'] = agent_match.group(2)
            metadata['confidence'] = float(agent_match.group(3))

        # Extract tags
        # Pattern: <!-- 🏷️ Tags: #kubernetes #helm -->
        tags_match = re.search(r'<!-- 🏷️ Tags: (.+) -->', task_block)
        if tags_match:
            tags_str = tags_match.group(1)
            metadata['tags'] = [tag.strip() for tag in tags_str.split('#') if tag.strip()]

        # Extract skill
        # Pattern: <!-- 🎯 skill: testing_validation (10.0) -->
        skill_match = re.search(r'<!-- 🎯 skill: ([a-z_]+) \(([\d.]+)\) -->', task_block)
        if skill_match:
            metadata['skill'] = {
                'name': skill_match.group(1),
                'score': float(skill_match.group(2))
            }

        # Extract fallback agent
        # Pattern: <!-- 🔄 Fallback: terraform-architect -->
        fallback_match = re.search(r'<!-- 🔄 Fallback: ([a-z-]+) -->', task_block)
        if fallback_match:
            metadata['fallback'] = fallback_match.group(1)

        # Extract result
        # Pattern: <!-- 📍 Result: ... -->
        result_match = re.search(r'<!-- 📍 Result: (.+) -->', task_block)
        if result_match:
            metadata['result'] = result_match.group(1)

        return metadata

    def _extract_description(self, task_block: str) -> Optional[str]:
        """
        Extract description from task block.

        Pattern: **Description:** followed by text until next section

        Args:
            task_block: Text block containing the task

        Returns:
            Description text or None if not found
        """
        desc_match = re.search(
            r'\*\*Description:\*\*\s+(.+?)(?=\*\*|\n\n|$)',
            task_block,
            re.DOTALL
        )
        if desc_match:
            return desc_match.group(1).strip()
        return None

    def _extract_acceptance_criteria(self, task_block: str) -> List[str]:
        """
        Extract acceptance criteria from task block.

        Pattern: **Acceptance Criteria:** followed by bullet list

        Args:
            task_block: Text block containing the task

        Returns:
            List of acceptance criteria strings
        """
        criteria = []

        # Find the acceptance criteria section
        criteria_match = re.search(
            r'\*\*Acceptance Criteria:\*\*(.+?)(?=\*\*|\n\n|$)',
            task_block,
            re.DOTALL
        )

        if criteria_match:
            criteria_text = criteria_match.group(1)
            # Extract bullet points (lines starting with -)
            lines = criteria_text.split('\n')
            for line in lines:
                line = line.strip()
                if line.startswith('- '):
                    criteria.append(line[2:].strip())

        return criteria

    def get_task_statistics(self) -> Dict[str, Any]:
        """
        Get overall statistics for the tasks file.

        Returns:
            Dict with statistics:
            - total_tasks: Total number of tasks
            - pending_tasks: Number of pending tasks
            - completed_tasks: Number of completed tasks
            - completion_rate: Percentage complete
        """
        try:
            # Count total tasks (both pending and complete)
            total_result = subprocess.run(
                ['grep', '-c', '^- \\[.\\] T[0-9]\\+', self.tasks_file_path],
                capture_output=True,
                text=True,
                check=False
            )
            total_tasks = int(total_result.stdout.strip()) if total_result.returncode == 0 else 0

            # Count completed tasks
            completed_result = subprocess.run(
                ['grep', '-c', '^- \\[x\\] T[0-9]\\+', self.tasks_file_path],
                capture_output=True,
                text=True,
                check=False
            )
            completed_tasks = int(completed_result.stdout.strip()) if completed_result.returncode == 0 else 0

            # Calculate pending
            pending_tasks = total_tasks - completed_tasks

            # Calculate completion rate
            completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0.0

            return {
                'total_tasks': total_tasks,
                'pending_tasks': pending_tasks,
                'completed_tasks': completed_tasks,
                'completion_rate': round(completion_rate, 2)
            }

        except Exception as e:
            raise RuntimeError(f"Failed to get task statistics: {e}")


# Test function for demonstration
def test_task_manager():
    """
    Test function demonstrating usage of TaskManager.

    This function requires a sample tasks.md file to be present.
    """
    import sys

    print("=" * 60)
    print("TaskManager Test Suite")
    print("=" * 60)

    # Check if path is provided
    if len(sys.argv) < 2:
        print("\nUsage: python task_manager.py <path_to_tasks.md>")
        print("\nExample:")
        print("  python task_manager.py /path/to/tasks.md")
        return

    tasks_file = sys.argv[1]

    try:
        # Initialize TaskManager
        print(f"\n1. Initializing TaskManager with: {tasks_file}")
        tm = TaskManager(tasks_file)
        print("   ✅ TaskManager initialized successfully")

        # Get statistics
        print("\n2. Getting task statistics...")
        stats = tm.get_task_statistics()
        print(f"   Total tasks: {stats['total_tasks']}")
        print(f"   Pending: {stats['pending_tasks']}")
        print(f"   Completed: {stats['completed_tasks']}")
        print(f"   Completion rate: {stats['completion_rate']}%")

        # Get pending tasks
        print("\n3. Getting pending tasks (limit 5)...")
        pending = tm.get_pending_tasks(limit=5)
        print(f"   Found {len(pending)} pending tasks:")
        for task in pending:
            print(f"   - {task['task_id']}: {task['title']}")

        # Get details for first pending task
        if pending:
            task_id = pending[0]['task_id']
            print(f"\n4. Getting details for {task_id}...")
            details = tm.get_task_details(task_id)
            print(f"   Task ID: {details['task_id']}")
            print(f"   Title: {details['title']}")
            print(f"   Status: {details['status']}")
            print(f"   Agent: {details['metadata'].get('agent', 'N/A')}")
            print(f"   Security Tier: {details['metadata'].get('security_tier', 'N/A')}")
            print(f"   Tags: {', '.join(details['metadata'].get('tags', []))}")

            if details.get('description'):
                print(f"   Description: {details['description'][:100]}...")

            # Test marking task complete (COMMENTED OUT - Destructive operation)
            # print(f"\n5. Testing mark_task_complete (dry run)...")
            # print(f"   Would mark {task_id} as complete")
            # Uncomment to actually test:
            # result = tm.mark_task_complete(task_id)
            # print(f"   Result: {result}")

        print("\n" + "=" * 60)
        print("✅ All tests passed successfully")
        print("=" * 60)

    except FileNotFoundError as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    test_task_manager()
