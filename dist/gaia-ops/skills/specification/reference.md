# Specification -- Reference

## Spec Template Format

Use `speckit/templates/spec-template.md` as the canonical format.

### Mandatory Sections

1. **Problem Statement** -- 2-3 sentences. Why this matters and who feels it.
2. **User Stories** -- "As [actor], I want [goal], so that [benefit]." 1-5 stories; each names a real actor, not "the system."
3. **Acceptance Criteria** -- Given/When/Then. At least one per story. These are the contract -- if a criterion cannot be tested without knowing the implementation, rewrite it.
4. **Scope Boundaries** -- IN / OUT columns. Explicit exclusions prevent scope creep during planning.
5. **Constraints** -- From governance.md. Only constraints relevant to this feature.
6. **Key Entities** -- If data is involved. Plain language, no field types or schemas.

### Optional Sections

Edge Cases, Security Considerations, Performance Expectations.

Mark uncertainty with `[NEEDS CLARIFICATION: specific question]`.

### Pre-Presentation Checklist

Before presenting, verify:
- Every story names a real actor
- Every criterion is implementation-agnostic
- Technologies appear only as context
- Scope excludes at least one adjacent concern
- Constraints come from governance
