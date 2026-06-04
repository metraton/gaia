---
name: jira-ticket-writing
description: Use when creating or drafting Jira/Atlassian tickets or issues, or when standardizing ticket output for a project
metadata:
  user-invocable: true
  type: technique
---

# Jira Ticket Writing

The formula for tickets humans can read in ~15 seconds. The what and why go at
the top; the how and evidence go in a comment. One Story per brief or theme;
consolidate rather than inflate.

## Core principle

A Story is readable by a non-technical stakeholder in 15 seconds. This means:
plain English, active voice, value-oriented title, high-level description. All
implementation detail and verification evidence lands in the first comment --
never in the description -- so the description reads clean.

## Story template

**Title:** verb + outcome, plain English, no jargon, 80 chars max.

**Description fields:**

| Field | Content |
|-------|---------|
| *Objective* | 1-2 sentences: what it achieves and why (high level). |
| *What it covers* | 3-5 bullets of scope -- what, not how. |
| *Acceptance criteria* | Checklist "Done when..." -- verifiable, not vague. |
| *Links* | Brief slug / repo if applicable. |

**Evidence** -- first comment only, never in description:
commit hashes, verbatim commands, anything reproducible.

## Subtask template

**Title:** short action phrase, plain English.
**Description:** one line -- what it is / done when X.

## Process

1. **Map to a brief or theme.** One Story = one brief or coherent chunk of
   work. If the work spans multiple unrelated concerns, split into separate
   Stories. If it is one theme with sequential steps, use Subtasks.

2. **Write the title last.** Draft the Objective and What-it-covers first;
   the title compresses naturally from those. If the title needs jargon to
   be precise, the description is missing context.

3. **Write the Acceptance Criteria as done-when checks.** Each AC is a
   checkbox that a person (not a CI system) can verify. Vague ACs get pushed
   back: "migrated" means what? "History intact" means no commit lost.

4. **Move all evidence to the first comment.** After the ticket is created,
   post a comment with: commit hashes, commands run verbatim, output snippets.
   This keeps the description clean for stakeholders and the evidence
   auditable for engineers.

5. **Adapt to the project's board conventions.** Do not create Epics if the
   team does not use them. Respect the active Sprint. Use existing Labels and
   Components, not invented ones. Check 2-3 existing tickets before creating
   the first one.

6. **Consolidate, do not inflate.** Prefer fewer well-scoped tickets over many
   granular ones. If two Subtasks share all context, make one Subtask. If a
   Story has one Subtask, fold the Subtask into the Story.

## Style rules

- Plain English, active voice. Write "Move repositories to Bitbucket" not
  "Repository migration to Bitbucket platform will be performed."
- Title is value-oriented: what the user or team gains, not the mechanism.
- No implementation detail in the description. "Configure DNS records" belongs
  in a Subtask description or the evidence comment, not the Story Objective.
- Subtasks are ultra-light: one-line description, action-phrase title.
- Fields: Assignee, Status (To Do / Done), Sprint -- set these before saving.

See `examples.md` for real tickets from the AOS migration project.

## Anti-patterns

- **Evidence in the description** -- pollutes the readable summary; goes in
  the first comment instead.
- **Title is the mechanism, not the outcome** -- "Run git push for all repos"
  is a task; "Migrate AOS repositories to Bitbucket" is a Story.
- **One Subtask per command** -- over-granular; group related steps into one
  Subtask with a multi-step description.
- **Inventing Epics or Labels** -- inconsistency with the team's board adds
  noise. Adapt to what is already there.
- **Vague Acceptance Criteria** -- "Works correctly" is not verifiable.
  "History intact" is verifiable only when it means "no commit missing from
  git log --oneline."
