# tools/validator_replay

This folder replays the Bash validator of two revisions over the same commands and reports every verdict that moved. One side is an older revision, by default the merge-base of `HEAD` with `replay.py::DEFAULT_BASE`. The other is the working tree. The commands come from four corpora (`replay.py::CORPORA`): the effect truth table (`tests/hooks/modules/security/data/effect_truth_table.json`), the curated cases in `cases.json`, the shell commands the store recorded as `tool.called` events, and the commands sealed in approval requests. Each one is classified as `FREE`, `SIGNED`, `BLOCKED` or `UNKNOWN` (`replay.py::STATES`).

It exists separately from the security tests because those pin individual verdicts, while this tool measures what a validator change does to the commands people actually run. A change that frees a hundred commands, or makes fifty more of them ask, can pass every unit test. Here it shows up as a number held against a cap that was set before the change. The developer changing anything under `hooks/modules/security/` or `hooks/modules/tools/bash_validator.py` runs it before landing, and declares the verdicts the change is meant to move.

## How it is wired in

It is invoked by a person, through one pytest module, and the default suite never runs it:

```
GAIA_REPLAY_OLD=<rev> GAIA_REPLAY_SAMPLE=2000 GAIA_REPLAY_OUT=~/.gaia/scratch/<name> \
GAIA_REPLAY_DECLARED=tools/validator_replay/declared/<task>.json \
  python3 -m pytest tests/replay/test_validator_replay_diff.py -m replay
```

1. `tests/replay/conftest.py` registers the `replay` marker, and `test_the_working_tree_validator_stays_within_the_friction_caps` skips unless `-m replay` selects it.
2. `replay.py::run` builds the units (`replay.py::build_units`). Through `corpus.py::open_read_only`, it reads history and approvals from a store that cannot be written.
3. `runner.py::checkout` extracts the old revision under `<out>/checkouts/<sha>`, reused across runs. `runner.py::classify` then splits the units over worker processes for each side. Each worker (`worker.py`) gets its own `GAIA_DATA_DIR` and `GAIA_DB` and classifies through that revision's own truth-table harness. A worker that stalls is killed and restarted past the unit, which is reported as an `ERROR` with the rule `timeout`.
4. `replay.py::compare` builds a 4x4 matrix per corpus and lists the changes. `replay.py::check_caps` turns the caps into violations, and the test fails on any violation.

The variables are all optional:

- `GAIA_REPLAY_OLD`: the old revision. When unset, the merge-base with `origin/feat/gaia-5-7`.
- `GAIA_REPLAY_SAMPLE`: replay a deterministic sample of at least that many history and approval commands (`replay.py::draw`, one of each program word first). When unset, the full corpus runs, which takes hours.
- `GAIA_REPLAY_OUT`: where the report goes. When unset, `~/.gaia/scratch/validator-replay-<timestamp>`.
- `GAIA_REPLAY_DECLARED`: the declared file of the task. When unset, nothing is declared.
- `GAIA_REPLAY_DB`: the store to read. When unset, `~/.gaia/gaia.db`.
- `GAIA_REPLAY_SINCE`: only history and approvals from that timestamp on.

**What breaks if you get it wrong here.** Editing `caps.json` after seeing a run's numbers defeats the tool: its `justification` records the baseline the caps were set from, and a cap is only a guard if it is fixed before the change. A second mistake is a declared file that only exists in the folder. `replay.py::run` reads one only when `GAIA_REPLAY_DECLARED` names it, so a declared change that is not passed counts as undeclared. A third is pointing `GAIA_REPLAY_OUT` inside the repository: the report carries raw commands, and `run` refuses that path with a `ValueError`.

## What's here

```
replay.py        the run: units, sample, comparison, caps, report.json and report.md
corpus.py        the four corpora; opens the store read-only
runner.py        extracts the old revision and drives the worker processes, with the stall timeouts
worker.py        one process: classifies its units with the truth-table harness of its revision
caps.json        the friction caps and the baseline that justifies them
cases.json       curated commands (friction seen on installed builds, git merge and push shapes)
declared/        one <task>.json per validator change: the pinned changes it intends, and the reviews
__init__.py      package marker
```

## Conventions

**Caps** (`caps.json`, read by `replay.py::check_caps`; a value above its cap is a violation):

- `history_newly_asking_max_count` and `history_newly_asking_max_fraction`: history commands that were `FREE` and now ask, counted and as a share of the history corpus.
- `pinned_changes_max_outside_declared`: changes to a truth row or a curated case (`replay.py::PINNED`) whose key the declared file does not list under `truth_rows`.
- `loosened_unreviewed_max`: changes that move to `FREE`, or out of `BLOCKED`, with no entry under `reviews` for their key or their exact command.
- `new_errors_max`: units that classified on the old side and fail on the new one. A `timeout` is not counted.

**A declared file** is `declared/<task>.json` with four keys:

- `task`: the task number.
- `truth_rows`: the keys of the truth rows and curated cases the change is meant to move.
- `reviews`: a key or an exact command, mapped to the sentence of review that accepts its loosening.
- `justification`: the run it was measured on and the changes by class.

Add one per validator change and pass it with `GAIA_REPLAY_DECLARED`.

**Reading the report.** `report.md` and `report.json` are written under the out folder.

- The first lines give the revisions, the corpus sizes, the sample, the seconds taken and the violations. The run passed only if the violations line reads `none`.
- Each corpus then has a matrix with old verdicts as rows and new ones as columns. Off-diagonal cells are the moves.
- `Changes` lists each move as `[corpus] OLD(rule) -> NEW(rule)` with its key and command.
- Errors on either side are listed apart and never count as changes; the run only caps the new errors.
- Timing tables by command length and the ten slowest units show where classification is slow.

## See also

- [`tests/replay/test_validator_replay_diff.py`](../../tests/replay/test_validator_replay_diff.py): the only entry point, and where each variable is read.
- [`skills/security-tiers/SKILL.md`](../../skills/security-tiers/SKILL.md): the validator model the replay guards, and the rule that every validator change lands behind a replay.
- [`tests/hooks/modules/security/test_effect_and_target_truth_table.py`](../../tests/hooks/modules/security/test_effect_and_target_truth_table.py): the harness each worker loads to classify.
