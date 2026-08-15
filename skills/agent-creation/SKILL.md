---
name: agent-creation
description: Use when creating a new specialist agent for Gaia, or reviewing whether an existing agent follows the correct structure, tone, and component inventory
---

# Agent Creation

Every section below is tagged **universal** -- it holds for any agent, the orchestrator included -- or **specialist** -- it holds for an agent dispatched as a subagent, and the orchestrator is exempt from it by role rather than by oversight. The exemptions are structural: the orchestrator carries no `routing:` block because it is what routes, no `skills:` field because the host preloads skills only for a dispatched subagent, and no CANNOT DO -> DELEGATE table in the specialist sense because delegating is its function rather than its edge.

## What is an agent? -- universal

An agent is a contract over project-context plus a small identity. For a specialist that identity points at one domain surface; the orchestrator's points at the conversation and the routing between surfaces. If the component you are building has no distinct contract, no delegation surface, and could work as injected text, it is a skill, not an agent -- that decision belongs upstream and this skill assumes it has been made.

What the identity buys is posture, not capability: measured, a persona does not improve factual accuracy at all, and irrelevant persona attributes cost up to 30 points of it. What it genuinely moves is tone, escalation threshold, refusal bar and report shape, so write it for those and drop the seniority decoration -- "senior architect" buys nothing and is charged for.

## Step 0: Route every rule to a place that can hold it -- universal

Ask of each thing you want true of the agent: can it be enforced?

| Can it be enforced? | Where it goes |
|---|---|
| Yes | The `write` contract, the frontmatter (`tools`, `disallowedTools`, `permissionMode`), a PreToolUse hook, or simply not granting the tool |
| No, but it is posture | The prose, carrying the consequence that justifies it |
| No, and it is not posture | Nowhere -- cut it |

The third row is the one authors skip, and skipping it is not free: a line no mechanism can hold and no posture can bias lowers the odds that every other line holds, and adds one more pair that can conflict.

Prefer the enforceable destination whenever one exists, because prose is a probability shift while a mechanism is a boundary: a PreToolUse hook that returns `deny` blocks the tool without consulting `permissionMode` at all (`hooks/adapters/claude_code.py::_is_protected` never reads it), which is the same reason the sandboxing documentation puts the frontier in the operating system "regardless of what the model chose to run". A hook is gaia-system's to implement -- propose one rather than settle for a sentence. And when an agent crosses a line already written, what the crossing reopens is the destination, not the wording: "do not cheat" left cheating exactly where it was at 80%, and "solve it the way the designer intended" raised it to 95%.

## Step 1: Answer the bifurcating dimensions -- tagged per dimension

**D0 (decide first): What is the contract? -- universal, and degenerate for the orchestrator**
Every agent carries one; the orchestrator's is `read: [project_identity]` and `write: []`, because it holds no domain and persists no domain artifact. For a specialist, name the `read` slices this domain reasons over and the `write` slices it owns. `read` is the token lever -- the menu (`can_read` in the dispatch kernel) the agent may pull on demand, nothing preloaded, so every extra slice dilutes its focus and its evidence scope. `write` is the security lever -- the allowlist the runtime checks before accepting any `update_contracts` clause, so a contract absent from it cannot be persisted whatever the agent emits. `developer` writes `application_services`, `platform-architect` writes `infrastructure` and `infrastructure_topology`, a read-only diagnostic writes nothing or the single observation contract it curates. Get this wrong and every other decision is built on it.

**D1: Does the agent mutate system state? -- universal**
A "yes" means Write/Edit in `tools`, `permissionMode: acceptEdits`, the T3 approval flow in failure handling, and a Realization Package output type; a "no" means none of those and a read-only output. The hard `disallowedTools: [Write, Edit, NotebookEdit]` denylist is reserved for the read-only-into-prod case -- an agent that inspects live production and must be incapable of mutating it, e.g. `cloud-troubleshooter` -- because an accidental write to a live resource is a real incident. Builders are governed softly instead, by T3 consent and their contract, carrying at most `[NotebookEdit]`. Withhold a tool for what the role must not do, never as a rank: the orchestrator keeps `Read` deliberately, so it can settle a claim it is able to see for itself instead of spending a dispatch to have it confirmed.

**D2: Does the agent enter the orchestrator's automatic routing? -- specialist**
Almost always "yes" for a specialist. A "yes" means the description is written as triggering conditions and a `routing:` block (surface, adjacent_surfaces, commands, artifacts, required_checks) is proposed for it. Those signals are proposals -- gaia-system applies them to the agent's own frontmatter, `tools/scan/seed_surface_routing.py` seeds the `surface_routing` table at install time, and `tools/context/surface_router.py` reads that table at runtime.

One fact that is not a dimension: a subagent cannot spawn subagents -- `Agent`/`Task` are inert in a subagent's frontmatter even when listed. A specialist surfaces what it cannot do through its CANNOT DO -> DELEGATE table and the orchestrator routes; only an agent run as the main thread via `--agent` dispatches.

## Step 2: Apply the component inventory -- tagged per component

Obligatory in every agent, tagged where it is not:

1. **`project_context_contracts`** (frontmatter): the `read`/`write` lists from D0.
2. **Frontmatter**: `name`, `description` (triggering conditions only), `model`, `tools`; `permissionMode: acceptEdits` if D1=yes; `disallowedTools` only for the read-only-into-prod case; `maxTurns` for long-running agents.
3. **Identity** (1-2 paragraphs): for a builder, the shared essence plus a small subset. The builders share one essence by design -- defer to what already exists over a clean-slate design, verify the outcome rather than the exit code, emit a Realization Package XOR a Findings Report and never a hybrid, so the turn lands in one clean state instead of mutating files and returning a summary of them, flag what is out of lane instead of editing across boundaries, and operate with capability that is free under T3 consent rather than fenced by a fixed toolbox. Carry it the same way each time: inventing a fresh personality per builder drifts the fleet and spends the effort that belongs in the subset -- what this agent builds, and which neighbors own the adjacent surfaces. A non-builder names the constraint that fences it instead.
4. **Workflow** (numbered steps): the operational sequence for this domain, placed before Identity when the sequence is the agent's primary reference.
5. **Scope -- CAN DO / CANNOT DO -> DELEGATE** -- specialist: every CANNOT DO entry names a concrete delegate agent and the situation that triggers the handoff.
6. **Termination**: the condition under which this agent's work is finished, stated in its domain's own terms -- tests green and the change behaving as claimed, the component installed and exercised, the diagnosis anchored to a symbol. `agent-protocol` owns the closing states; what it cannot know is what "done" means here. Not knowing when to stop is the third-heaviest measured failure in multi-agent systems (12.4%), and adding a high-level goal check is the heaviest measured gain (+15.6%).
7. **Adjudication**: who has the last word when this agent disagrees -- with the evidence, with another agent's return, or with the instruction it received. Name the tie-break concretely: live state and code over memory, the codebase's pattern over the agent's own prior, the user for anything that needs consent. Stating it explicitly measures +9.4%; left implicit, the agent improvises one under pressure.
8. **Domain Errors**: concrete errors with concrete actions -- "report the error" is not an action.
9. **Response protocol**: `agent-protocol` and `security-tiers` are non-negotiable for every agent and carry the response contract and tier discipline. List them in `skills:` and never re-teach them in the identity.

Optional: a **domain reference inline** -- lookup tables or decision logic that apply only to this agent and do not warrant a skill.

## Step 3: Write for judgment, not compliance -- universal

**Weight test.** If the section were removed, would the agent behave differently? If not, it is decorative. A boundary stated as a category ("cloud infrastructure") carries less weight than one naming the situation that triggers it ("if the resource type is managed by IaC, creating it belongs to platform-architect even when you need it as a prerequisite").

**Polarity, with the why in the same sentence.** Each line names the behavior wanted rather than the one forbidden, and arrives with the consequence that justifies it. Requirements hold flat across a long session while prohibitions decay from 73% at turn 5 to 33% by turn 16, and 87.5% of those violations are priming -- naming the forbidden behavior is what activates it. The reason travels with the rule for the same kind of reason: training on the reasoning behind aligned behavior reached 3% misalignment where the behavior alone stopped at 15%. This reaches table columns too. A column describing where a naive agent crosses states the transgression vividly and in the agent's own voice, so keep the discrimination it carries and flip its polarity: the column states the condition that triggers the wanted action ("hand the search to the owning surface when the question is which files implement a behavior"), not the moment of crossing.

The polarity rule governs what is written INTO an agent, and not a document like this one. An identity is held by a model under context pressure across a long session, which is the condition every measurement above was taken in; an authoring document is read once, by a person or by gaia-system, for a bounded task. That is why the Anti-patterns section below names its failures directly, and why applying this rule to it would delete the failure catalogue without buying any of the adherence the rule exists to protect.

**A budget of seven always-on norms.** Count the rules the agent must hold on every turn whatever the situation -- identity commitments, workflow steps, standing principles. Seven is the ceiling, and the number does not go up: the agent gets cut. What sets it there is that per-instruction reliability barely moves (0.94 to 0.85) while the odds of satisfying all of them collapse from 94% to 21% between one instruction and ten; at seven that measured band still leaves roughly a third to a half of turns holding every norm, and each addition past it costs more than the one before it. Conditional rows are exempt because they do not multiply: an error table or a delegation table is looked up, at most a row or two fires per turn, and rows naming genuinely distinct situations do not compete. `developer` sits at the budget today; `gaia-orchestrator` runs at roughly double it, which is where the triage is owed.

**Deconflict in pairs.** The weight test reads each section alone, and conflict lives between them, so make a second pass over the pairs and ask which pair a single turn could satisfy only one half of -- "be exhaustive" against "make the minimal change", "verify before claiming" against a turn budget. Soft conflict between pairs grows with the square of the count and correlates -0.37 with adherence. Fewer rules and fewer collisions is the lever; ordering is not -- the claim that important rules go first does not survive measurement, which splits between primacy and recency and finds no consistent effect.

## Step 4: Write the description field as triggering conditions -- universal

The description is what the orchestrator reads to decide when to dispatch, so it must describe *when to use this agent*, not *what it is*. A role summary satisfies the read without triggering the dispatch.

```yaml
# Wrong -- describes the role
description: Senior infrastructure architect that manages the cloud lifecycle

# Right -- triggering conditions
description: Use when provisioning, modifying, or validating infrastructure-as-code (Terraform, Pulumi, CloudFormation, OpenTofu), or managing the infrastructure lifecycle
```

## Step 5: Evaluate the skills catalog -- specialist

The host preloads `skills:` for a dispatched subagent only, which is why this step has nothing to apply to the orchestrator. Do not hardcode a tool-to-skill mapping -- the catalog changes and a fixed mapping goes stale silently. Read the current catalog at `.claude/skills/` and propose the skills that address a recurring risk or discipline gap for this agent's tool set and domain (`command-execution` if it runs Bash, `investigation` if it diagnoses complex state).

## Step 6: Propose surface signals (if D2=yes) -- specialist

Propose the `routing:` block written for gaia-system to apply to the agent's own file -- `keywords` is retired, the matcher scores `commands`/`artifacts` only. Do not apply it yourself, and read the siblings' `routing:` blocks so the new surface and its signals do not overlap one.

## Step 7: Register it so it ships, then prove it governs -- universal

Writing `agents/<name>.md` is necessary and not sufficient, in three stages that each need their own verification. `gaia-verifier` is the standing case for the first two: committed to `agents/`, later added to the manifest, never carried by a release, and therefore absent from every installed copy.

1. **Manifest.** Append `"agents/<name>.md"` to the `agents` array in `build/gaia.manifest.json`. Verify with `grep -c "agents/<name>.md" build/gaia.manifest.json` returning `>= 1` -- an entry that does not grep back has not landed.
2. **Marketplace.** An installed `~/.claude/plugins/marketplaces/<name>` copy is a git checkout that does not track new commits until a release refreshes it. Getting there is a `gaia-release` action, never a hand-edit or a `git pull` of the `.claude/` copy. Verify with `find ~/.claude/plugins/marketplaces -name <name>.md` returning `>= 1`.
3. **Governance.** Arriving is not governing, and the gap is wide: against a long policy document, the best of thirty configurations satisfied 36.2% of 824 criteria, and what failed was not the work but the control -- the approval gate, the wait condition, the scope limit. So exercise the agent on a real task WITH its tools and read the artifacts it produced. Conversing with it proves nothing: the same prompt under the same policy went from perfect compliance to 85% violations on the single change of granting tools. Its own account proves less than nothing -- nearly every failing trajectory ends by confidently claiming it followed the manual, citing the sections it violated.

## Anti-patterns -- universal

- **Designing identity before the contract**: the contract sets token cost and write safety, so authoring a personality first and bolting a contract on after produces an agent that reads too much and may write where it should not.
- **Reaching for `disallowedTools` to govern a builder**: hard denylists are for the read-only-into-prod case; one on a builder either blocks legitimate work or signals a misunderstanding of where the security boundary lives.
- **Treating this as a form**: filling sections without the weight test produces a well-structured agent the model ignores in favor of its baseline behavior.
