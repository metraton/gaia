"""Tests for hooks.modules.agents.transcript_analyzer."""

import json
from dataclasses import FrozenInstanceError

import pytest

from hooks.modules.agents.transcript_analyzer import (
    ComplianceScore,
    DuplicateCall,
    ToolCall,
    TranscriptAnalysis,
    analyze,
    compute_compliance_score,
)


# ============================================================================
# Helpers
# ============================================================================


def _write_jsonl(path, entries):
    """Write a list of dicts as JSONL to the given path."""
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


# ============================================================================
# Dataclass defaults
# ============================================================================


class TestTranscriptAnalysisDefaults:
    def test_token_defaults_zero(self):
        a = TranscriptAnalysis()
        assert a.input_tokens == 0
        assert a.cache_creation_tokens == 0
        assert a.cache_read_tokens == 0
        assert a.output_tokens == 0

    def test_list_defaults_empty(self):
        a = TranscriptAnalysis()
        assert a.stop_reasons == []
        assert a.tool_sequence == []
        assert a.bash_commands == []
        assert a.skills_injected == []
        assert a.duplicate_tool_calls == []
        assert a.pipe_commands == []

    def test_optional_defaults_none(self):
        a = TranscriptAnalysis()
        assert a.duration_ms is None
        assert a.first_timestamp is None
        assert a.last_timestamp is None
        assert a.first_tool_name is None

    def test_other_defaults(self):
        a = TranscriptAnalysis()
        assert a.model == ""
        assert a.api_call_count == 0
        assert a.tool_call_count == 0

    def test_list_fields_are_independent(self):
        """Each instance gets its own list (default_factory works)."""
        a = TranscriptAnalysis()
        b = TranscriptAnalysis()
        a.stop_reasons.append("x")
        assert b.stop_reasons == []


# ============================================================================
# Frozen dataclasses
# ============================================================================


class TestFrozenDataclasses:
    def test_tool_call_is_frozen(self):
        tc = ToolCall(index=1, tool_name="Read", arguments={"file_path": "/a"})
        with pytest.raises(FrozenInstanceError):
            tc.index = 2

    def test_duplicate_call_is_frozen(self):
        dc = DuplicateCall(tool_name="Read", arguments_hash="abc123", indices=[1, 2])
        with pytest.raises(FrozenInstanceError):
            dc.tool_name = "Write"


# ============================================================================
# analyze() -- empty and missing files
# ============================================================================


class TestAnalyzeEdgeCases:
    def test_empty_file(self):
        result = analyze("/dev/null")
        assert result.input_tokens == 0
        assert result.tool_sequence == []
        assert result.duration_ms is None

    def test_missing_file(self):
        result = analyze("/nonexistent/path/transcript.jsonl")
        assert result.input_tokens == 0
        assert result.tool_sequence == []
        assert result.duration_ms is None


# ============================================================================
# Token accumulation
# ============================================================================


class TestTokenAccumulation:
    def test_tokens_sum_across_turns(self, tmp_path):
        entries = [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"role": "assistant", "content": "hello"},
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_creation_input_tokens": 10,
                    "cache_read_input_tokens": 5,
                },
                "model": "claude-opus-4-6",
                "stop_reason": "end_turn",
            },
            {
                "timestamp": "2026-01-01T00:00:01Z",
                "message": {"role": "assistant", "content": "world"},
                "usage": {
                    "input_tokens": 200,
                    "output_tokens": 75,
                    "cache_creation_input_tokens": 20,
                    "cache_read_input_tokens": 15,
                },
                "model": "claude-opus-4-6",
                "stop_reason": "end_turn",
            },
        ]
        p = tmp_path / "transcript.jsonl"
        _write_jsonl(p, entries)

        result = analyze(str(p))
        assert result.input_tokens == 300
        assert result.output_tokens == 125
        assert result.cache_creation_tokens == 30
        assert result.cache_read_tokens == 20
        assert result.api_call_count == 2
        assert result.model == "claude-opus-4-6"
        assert result.stop_reasons == ["end_turn", "end_turn"]


# ============================================================================
# Duration
# ============================================================================


class TestDuration:
    def test_duration_ms_computed(self, tmp_path):
        entries = [
            {
                "timestamp": "2026-01-01T00:00:00.000Z",
                "message": {"role": "assistant", "content": "start"},
                "usage": {},
                "model": "m",
                "stop_reason": "end_turn",
            },
            {
                "timestamp": "2026-01-01T00:00:05.500Z",
                "message": {"role": "assistant", "content": "end"},
                "usage": {},
                "model": "m",
                "stop_reason": "end_turn",
            },
        ]
        p = tmp_path / "transcript.jsonl"
        _write_jsonl(p, entries)

        result = analyze(str(p))
        assert result.duration_ms == 5500
        assert result.first_timestamp == "2026-01-01T00:00:00.000Z"
        assert result.last_timestamp == "2026-01-01T00:00:05.500Z"

    def test_single_timestamp_no_duration(self, tmp_path):
        entries = [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"role": "assistant", "content": "only"},
                "usage": {},
                "model": "m",
                "stop_reason": "end_turn",
            },
        ]
        p = tmp_path / "transcript.jsonl"
        _write_jsonl(p, entries)

        result = analyze(str(p))
        assert result.duration_ms == 0


# ============================================================================
# Tool sequence
# ============================================================================


class TestToolSequence:
    def test_tool_calls_extracted(self, tmp_path):
        entries = [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Let me read that."},
                        {
                            "type": "tool_use",
                            "name": "Read",
                            "input": {"file_path": "/a.txt"},
                        },
                        {
                            "type": "tool_use",
                            "name": "Grep",
                            "input": {"pattern": "foo", "path": "/b"},
                        },
                    ],
                },
                "usage": {},
                "model": "m",
                "stop_reason": "tool_use",
            },
        ]
        p = tmp_path / "transcript.jsonl"
        _write_jsonl(p, entries)

        result = analyze(str(p))
        assert result.tool_call_count == 2
        assert len(result.tool_sequence) == 2
        assert result.tool_sequence[0].index == 1
        assert result.tool_sequence[0].tool_name == "Read"
        assert result.tool_sequence[0].arguments == {"file_path": "/a.txt"}
        assert result.tool_sequence[1].index == 2
        assert result.tool_sequence[1].tool_name == "Grep"


# ============================================================================
# Duplicate detection
# ============================================================================


class TestDuplicateDetection:
    def test_identical_calls_produce_duplicates(self, tmp_path):
        tool_use_block = {
            "type": "tool_use",
            "name": "Read",
            "input": {"file_path": "/same.txt"},
        }
        entries = [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {
                    "role": "assistant",
                    "content": [tool_use_block],
                },
                "usage": {},
                "model": "m",
                "stop_reason": "tool_use",
            },
            {
                "timestamp": "2026-01-01T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "content": [tool_use_block],
                },
                "usage": {},
                "model": "m",
                "stop_reason": "tool_use",
            },
        ]
        p = tmp_path / "transcript.jsonl"
        _write_jsonl(p, entries)

        result = analyze(str(p))
        assert len(result.duplicate_tool_calls) == 1
        dup = result.duplicate_tool_calls[0]
        assert dup.tool_name == "Read"
        assert dup.indices == [1, 2]
        assert len(dup.arguments_hash) == 16

    def test_different_calls_no_duplicates(self, tmp_path):
        entries = [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Read", "input": {"file_path": "/a.txt"}},
                    ],
                },
                "usage": {},
                "model": "m",
                "stop_reason": "tool_use",
            },
            {
                "timestamp": "2026-01-01T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Read", "input": {"file_path": "/b.txt"}},
                    ],
                },
                "usage": {},
                "model": "m",
                "stop_reason": "tool_use",
            },
        ]
        p = tmp_path / "transcript.jsonl"
        _write_jsonl(p, entries)

        result = analyze(str(p))
        assert result.duplicate_tool_calls == []


# ============================================================================
# Pipe detection
# ============================================================================


class TestPipeDetection:
    def test_bash_with_pipe_detected(self, tmp_path):
        entries = [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "input": {"command": "kubectl get pods | grep Error"},
                        },
                    ],
                },
                "usage": {},
                "model": "m",
                "stop_reason": "tool_use",
            },
        ]
        p = tmp_path / "transcript.jsonl"
        _write_jsonl(p, entries)

        result = analyze(str(p))
        assert len(result.pipe_commands) == 1
        assert result.pipe_commands[0] == "kubectl get pods | grep Error"

    def test_pipe_inside_quotes_not_detected(self, tmp_path):
        entries = [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "input": {"command": "echo 'hello | world'"},
                        },
                    ],
                },
                "usage": {},
                "model": "m",
                "stop_reason": "tool_use",
            },
        ]
        p = tmp_path / "transcript.jsonl"
        _write_jsonl(p, entries)

        result = analyze(str(p))
        assert result.pipe_commands == []


# ============================================================================
# Skills injected
# ============================================================================


class TestSkillsInjected:
    def test_command_name_tag_in_user_message_string(self, tmp_path):
        entries = [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {
                    "role": "user",
                    "content": "Hello <command-name>agent-protocol</command-name> world",
                },
            },
        ]
        p = tmp_path / "transcript.jsonl"
        _write_jsonl(p, entries)

        result = analyze(str(p))
        assert result.skills_injected == ["agent-protocol"]

    def test_command_name_tag_in_content_list(self, tmp_path):
        entries = [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Load <command-name>fast-queries</command-name> please",
                        },
                    ],
                },
            },
        ]
        p = tmp_path / "transcript.jsonl"
        _write_jsonl(p, entries)

        result = analyze(str(p))
        assert result.skills_injected == ["fast-queries"]

    def test_multiple_skills_no_duplicates(self, tmp_path):
        entries = [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {
                    "role": "user",
                    "content": "<command-name>skill-a</command-name> <command-name>skill-a</command-name>",
                },
            },
            {
                "timestamp": "2026-01-01T00:00:01Z",
                "message": {
                    "role": "user",
                    "content": "<command-name>skill-b</command-name>",
                },
            },
        ]
        p = tmp_path / "transcript.jsonl"
        _write_jsonl(p, entries)

        result = analyze(str(p))
        assert result.skills_injected == ["skill-a", "skill-b"]


# ============================================================================
# first_tool_name
# ============================================================================


class TestFirstToolName:
    def test_first_tool_name_set(self, tmp_path):
        entries = [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Glob", "input": {"pattern": "*.py"}},
                        {"type": "tool_use", "name": "Read", "input": {"file_path": "/x"}},
                    ],
                },
                "usage": {},
                "model": "m",
                "stop_reason": "tool_use",
            },
        ]
        p = tmp_path / "transcript.jsonl"
        _write_jsonl(p, entries)

        result = analyze(str(p))
        assert result.first_tool_name == "Glob"

    def test_no_tools_first_tool_name_none(self, tmp_path):
        entries = [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"role": "assistant", "content": "Just text"},
                "usage": {},
                "model": "m",
                "stop_reason": "end_turn",
            },
        ]
        p = tmp_path / "transcript.jsonl"
        _write_jsonl(p, entries)

        result = analyze(str(p))
        assert result.first_tool_name is None


# ============================================================================
# ComplianceScore
# ============================================================================


class TestComplianceScore:
    def _default_analysis(self):
        """Return a clean TranscriptAnalysis (no violations)."""
        return TranscriptAnalysis()

    def test_perfect_score(self):
        analysis = self._default_analysis()
        score = compute_compliance_score(
            analysis=analysis,
            contract_valid=True,
            has_scope_escalation=False,
            anchor_hit_rate=1.0,
        )
        assert score.total == 100
        assert score.grade == "A"
        assert score.deductions == []
        assert sum(score.factors.values()) == 100

    def test_zero_score(self):
        analysis = self._default_analysis()
        analysis.first_tool_name = "Bash"
        analysis.pipe_commands = ["a | b"] * 6
        analysis.duplicate_tool_calls = [
            DuplicateCall(tool_name="X", arguments_hash="h", indices=[1, 2])
            for _ in range(6)
        ]
        score = compute_compliance_score(
            analysis=analysis,
            contract_valid=False,
            has_scope_escalation=True,
            anchor_hit_rate=0.0,
        )
        assert score.total == 0
        assert score.grade == "F"

    def test_grade_boundary_90_is_a(self):
        """90 total => grade A."""
        analysis = self._default_analysis()
        score = compute_compliance_score(
            analysis=analysis,
            contract_valid=True,
            has_scope_escalation=False,
            anchor_hit_rate=1 / 3,
        )
        assert score.total == 90
        assert score.grade == "A"

    def test_grade_boundary_89_is_b(self):
        """89 total => grade B."""
        analysis = self._default_analysis()
        score = compute_compliance_score(
            analysis=analysis,
            contract_valid=True,
            has_scope_escalation=False,
            anchor_hit_rate=4 / 15,
        )
        assert score.total == 89
        assert score.grade == "B"

    def test_grade_boundary_75_is_b(self):
        """75 total => grade B."""
        analysis = self._default_analysis()
        analysis.duplicate_tool_calls = [
            DuplicateCall(tool_name="X", arguments_hash="h", indices=[1, 2])
            for _ in range(5)
        ]
        score = compute_compliance_score(
            analysis=analysis,
            contract_valid=True,
            has_scope_escalation=False,
            anchor_hit_rate=0.0,
        )
        assert score.total == 75
        assert score.grade == "B"

    def test_grade_boundary_74_is_c(self):
        """74 total => grade C."""
        analysis = self._default_analysis()
        score = compute_compliance_score(
            analysis=analysis,
            contract_valid=True,
            has_scope_escalation=True,
            anchor_hit_rate=4 / 15,
        )
        assert score.total == 74
        assert score.grade == "C"

    def test_grade_boundary_50_is_c(self):
        """50 total => grade C."""
        analysis = self._default_analysis()
        analysis.first_tool_name = "Bash"
        score = compute_compliance_score(
            analysis=analysis,
            contract_valid=True,
            has_scope_escalation=True,
            anchor_hit_rate=0.0,
        )
        assert score.total == 50
        assert score.grade == "C"

    def test_grade_boundary_49_is_f(self):
        """49 total => grade F."""
        analysis = self._default_analysis()
        analysis.first_tool_name = "Bash"
        analysis.pipe_commands = ["a | b"]
        score = compute_compliance_score(
            analysis=analysis,
            contract_valid=True,
            has_scope_escalation=True,
            anchor_hit_rate=2 / 15,
        )
        assert score.total == 49
        assert score.grade == "F"

    def test_compliance_score_is_frozen(self):
        score = compute_compliance_score(
            analysis=TranscriptAnalysis(),
            contract_valid=True,
            has_scope_escalation=False,
            anchor_hit_rate=1.0,
        )
        with pytest.raises(FrozenInstanceError):
            score.total = 0

    def test_disciplined_first_tools_get_full_points(self):
        """Read, Glob, Grep, and None all get investigation_discipline points."""
        for tool_name in ["Read", "Glob", "Grep", None]:
            analysis = self._default_analysis()
            analysis.first_tool_name = tool_name
            score = compute_compliance_score(
                analysis=analysis,
                contract_valid=True,
                has_scope_escalation=False,
                anchor_hit_rate=1.0,
            )
            assert score.factors["investigation_discipline"] == 20, (
                f"first_tool_name={tool_name} should get 20 investigation_discipline points"
            )
