"""A generated batch of decorations x verdict words x evidence polarity x turn binding against the finalize content checks."""

from __future__ import annotations

import itertools

from gaia.contract.content_checks import check_content

PYTEST = "python3 -m pytest tests/ -q"
FAILING_RUN = [{"command": PYTEST, "excerpt": "1 failed, 4 passed", "exit_code": 1}]
BRIEF = 5
BRIEF_TURN = {"brief_ids": frozenset({BRIEF}), "task_ids": frozenset()}
TASK_TURN = {"brief_ids": frozenset({BRIEF}), "task_ids": frozenset({"42"})}
COORDINATES = {
    "brief-wide": {"brief_id": BRIEF, "task_id": None},
    "own task": {"brief_id": BRIEF, "task_id": "42"},
    "own task under another brief": {"brief_id": 6, "task_id": "42"},
    "own task, brief spelled as text": {"brief_id": str(BRIEF), "task_id": "42"},
    "sibling task": {"brief_id": BRIEF, "task_id": "99999"},
    "other brief": {"brief_id": 6, "task_id": None},
}
DECORATIONS = (
    "{}", "**{}**", "__{}__", "_{}_", "*{}*", "~~{}~~", "`{}`", "'{}'", '"{}"', "“{}”", "«{}»", "({})", "[{}]",
    "<{}>", "{{{}}}", "{}!", "{}.", "{}:", "- {}", "> {}", "{}/", "/{}", "{} /", "<b>{}</b>", "<i><b>{}</b></i>",
    "{} (5/5)", "{} 12/12", "{} (12 of 12)",
)
MARKS = {"pass": ("✅ {}", "{} ✓", "{} ✔️", "{} 👍"), "fail": ("❌ {}", "{} ✗", "{} 👎")}
PASS_WORDS = (
    "pass", "passed", "passing", "ok", "okay", "success", "succeeded", "done", "true", "green",
    "all passed", "all tests pass", "pass/ok", "12880 passed", "PASS (0 failures)", "12880 passed, 0 failed",
    "failures: 0, all passed",
)
FAIL_WORDS = ("fail", "failed", "failing", "failure", "false", "1 failed", "3 failures", "1 failed, 4 passed", "not passed")
CASES = (str.lower, str.upper, str.title, lambda text: "".join(chr(ord(char) + 0xFEE0) if "!" <= char <= "~" else char for char in text),
         lambda text: " ".join(text.upper()), lambda text: f"{text[:-2]}**{text[-2:]}")


def _variants(words, decorations):
    for word, case, decoration in itertools.product(words, CASES, decorations):
        yield decoration.format(case(word))


def _check(verification, records=(), evidence=None, binding=BRIEF_TURN, bound=True):
    envelope = {"evidence_report": {"verification": verification}}
    return check_content(envelope, list(records), bound=bound, evidence=(evidence or {}).get, binding=binding)[0]


def test_every_decorated_verdict_named_as_the_command_is_refused_whatever_evidence_it_cites():
    leaked = []
    evidence = {
        index: {**coordinate, "polarity": polarity}
        for index, (coordinate, polarity) in enumerate(itertools.product(COORDINATES.values(), ("positive", "negative")))
    }
    for polarity, words in (("pass", PASS_WORDS), ("fail", FAIL_WORDS)):
        for command in _variants(words, DECORATIONS + MARKS[polarity]):
            for binding, cited in itertools.product((BRIEF_TURN, TASK_TURN), evidence):
                errors = _check({"command": command, "result": "pass", "evidence_ids": [cited]}, evidence=evidence, binding=binding)
                if not any("is a verdict, not a check" in error for error in errors):
                    leaked.append((command, cited, sorted(binding["task_ids"])))
    assert leaked == []


def test_without_a_tool_record_a_command_in_markup_is_read_without_it():
    commands = [decoration.format(word) for word, decoration in itertools.product(
        ("pass", "ok", "PASS", "verified", "100%", "🟢", "LGTM"), ("<b>{}</b>", "<i><b>{}</b></i>", "<span class='x'>{}</span>"),
    )]
    accepted = [command for command in commands if not _check({"command": command, "result": "pass"}, bound=False)]
    assert accepted == []


def test_a_command_that_runs_a_program_is_not_read_as_a_verdict():
    commands = (PYTEST, "make test", "npm test", "go test ./...", "test -f out.txt", "git status", "gaia evidence show 5", "ls -la")
    refused = [command for command in commands if _check({"command": command, "result": "pass", "evidence_ids": [1]},
                                                         evidence={1: {**COORDINATES["brief-wide"], "polarity": "positive"}})]
    assert refused == []


def test_every_decorated_pass_result_over_a_failing_run_gets_the_exit_check():
    pure = [decoration for decoration in DECORATIONS + MARKS["pass"] if not any(char.isalnum() for char in decoration.format(""))]
    leaked = [
        result for result in _variants(("pass",), pure)
        if not any("last exited 1" in error for error in _check({"command": PYTEST, "result": result}, FAILING_RUN))
    ]
    assert leaked == []


def test_no_result_but_pass_is_interpreted_over_a_failing_run():
    results = itertools.chain(_variants(PASS_WORDS[1:], DECORATIONS + MARKS["pass"]), _variants(FAIL_WORDS, DECORATIONS + MARKS["fail"]))
    refused = [result for result in results if _check({"command": PYTEST, "result": result}, FAILING_RUN)]
    assert refused == []


def test_only_positive_evidence_of_the_turns_own_brief_and_task_proves_a_command_it_did_not_run():
    wrong = []
    for (name, coordinate), polarity, (turn, binding) in itertools.product(
        COORDINATES.items(), ("positive", "negative"), (("brief", BRIEF_TURN), ("task", TASK_TURN)),
    ):
        evidence = {1: {**coordinate, "polarity": polarity}}
        accepted = not _check({"command": "make verify-everything", "result": "pass", "evidence_ids": [1]}, evidence=evidence, binding=binding)
        owns = (turn, name) in {("brief", "brief-wide"), ("task", "own task")}
        if accepted != (polarity == "positive" and owns):
            wrong.append((name, polarity, turn, accepted))
    assert wrong == []


def test_an_evidence_id_that_is_not_a_plain_integer_is_refused_without_a_crash():
    evidence = {1: {**COORDINATES["brief-wide"], "polarity": "positive"}}
    spellings = ("²", "١", "𝟏", "１", " 1 ", "1.0", 1.0, True, -1, "0x1", "", None, {"id": 1})
    wrong = []
    for value in spellings:
        cited = check_content(
            {"evidence_report": {"verification": {"command": "make verify-everything", "result": "pass", "evidence_ids": [value]},
                                 "verbatim_outputs": [{"command": PYTEST, "excerpt": "4 passed", "evidence_id": value}]}},
            FAILING_RUN, bound=True, evidence=evidence.get, binding=BRIEF_TURN,
        )[0]
        if sum("is not an evidence id" in error for error in cited) != 2:
            wrong.append((value, cited))
    assert wrong == []
