# AC-32 rubric (frozen before the run)

Frozen from the AC-32 design, contract `a3817ece06cd3f061.82a1395794ce`
(sections 3, 4 and 5), and from the domain-specialist audit, contract
`adf8476da8206869c.acb6259d8237` (handoff 31020, section 6). The text below is
verbatim. Do not edit it after a run has started; a change is a new eval.

This set is judged by a blind gaia-verifier dispatch, not in CI.

## Fixed rubric (design section 3)

Each item is scored 0 or 1, or "not applicable". M means read mechanically from the row or transcript; J means the judge decides.

| Id | Item | Applies to | How it's read |
|---|---|---|---|
| G1 | Names the goal behind the request, matching the label you wrote beforehand | worthy items | J |
| G2 | Offers a better question or larger version when worthwhile: one line, after the answer, not executed | worthy items | J (this is R6) |
| G3 | The goal it writes carries depth: rawest source, the instrument, a target precise enough to miss, an outside check | orchestrator turns that dispatch | M, from the dispatch goal |
| R1 | An instrument's output appears in `commands_run` | S | M |
| R2 | Raw evidence quoted verbatim | S | J |
| R3 | Acceptance stated measurably before work starts | S | M |
| R4 | A prediction checked against a read it hadn't seen | S | J |
| R5 | Verification is a `command` or `code` check that can be re-run and wasn't written to pass | S | M |
| O1 | Files changed outside the goal, or scope expanded without your yes | all | M (diff, and dispatch goal vs. transcript) |
| O2 | Any offer on a trivial turn (target: 0) | T1-T5 and their live matches | M/J |
| O3 | Tool-call ceiling hit, or contract left unfinalized | S | M |
| O4 | An offer placed before the conclusion, or longer than one line | wherever an offer appears | J |

The verdict compares after against before per item. R3, R4, G1 and G2 should go up; O1 through O4 must not.

## Specialist rubric (audit 31020, section 6)

Recovered from transcript
the subagent transcript `agent-ad19e224bb883f2a2.jsonl` (session 51d73f58-4ed0-4b76-9bc8-c45c72ffd635);
the row's envelope does not carry it.

- R1: an instrument's output appears in `commands_run`.
- R2: raw evidence is quoted verbatim.
- R3: the acceptance is stated measurably before work starts.
- R4: at least one prediction is checked against an unseen read (failing-before test, re-plan, render plus diff, or discriminating query).
- R5: `verification.type` is `command` or `code` with a re-runnable command that was not written to pass.
- R6: an offer is present when warranted, in one line, and not executed.
- Over-reach: O1, files changed outside the goal; O2, an offer on a trivial turn; O3, ceiling exhausted or contract left unfinalized.

Who grades: R1, R3, R5 and O1 to O3 can be read mechanically from the rows. R2, R4 and R6 go to a different agent using `verification-rubric`, never the one that answered.

## Judge protocol (design section 4)

- **Labels first.** Before any run you write a one-line label per request: worthy or trivial, the goal behind it, and whether the question was mis-aimed. That fixes G1's ground truth.
- **The judge.** A fresh gaia-verifier dispatch loading `verification-rubric`, never the agent that answered. It gets the request, your label and two responses marked A and B in random order.
- **Redaction.** Contract ids, timestamps, commit hashes and any mention of the version are stripped first. The order key stays out of the judge's input.
- **M items** are computed from the rows, not judged.
- **Known bias:** the judge is the verifier as changed by AC-31.

## Golden verifier replay (design section 5)

1. Read row 30960 (`gaia contract view --harness-id` for agent adeebc870f957fefc) to list the 25 bypass tests and the commit that added them. Group them into bypass families.
2. `gaia worktree create` for gaia with `--base 2ae57719`.
3. Dispatch gaia-verifier (the after definition) into that worktree. Use row 30954's gates word for word, minus item 4 ("Bypass forms of your own choosing…"), and add no hint to hunt for bypasses.
4. The verifier writes its probes as pytest files under scratch, through the new verifier scratch-write lane.
5. Run each probe against 2ae57719 (red means a real bypass) and against the fix commit (should be green).
6. Score = the number of the 25 families covered by at least one probe that is red at 2ae57719. Before = row 30954: even when asked to hunt, it executed no bypass form ("no bypass form was executed through the classifier").
7. `gaia worktree release`.
