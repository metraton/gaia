---
status: draft
surface_type: cli
acceptance_criteria:
  - id: AC-1
    description: "echo command exits 0"
    evidence:
      type: command
      shape:
        run: "echo ok"
        expect: "exit 0"
    artifact: evidence/AC-1.txt
  - id: AC-2
    description: "true command exits 0"
    evidence:
      type: command
      shape:
        run: "true"
        expect: "exit 0"
    artifact: evidence/AC-2.txt
---

# Sample Brief (fixture)

## Objective
Minimal fixture brief for testing gaia-evidence CLI end-to-end.
All ACs use trivially-passing commands so any correct runner exits 0.

## Out of Scope
Everything else.
