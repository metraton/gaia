# Skill Creation -- Reference

Detailed guidance on writing style and design philosophy. Read on-demand when crafting or reviewing skill content.

## Tone by Type

**Reference** is where tone matters least and accuracy matters most: every field name, tag, and line reference must be checked against the source file, because a Reference skill is read as ground truth -- a wrong name propagates into envelopes that fail validation.

**Protocol** needs precision in its state machines and formats, but transitions should explain why they exist. An agent that understands why APPROVAL_REQUEST precedes IN_PROGRESS for T3 operations will handle edge cases the protocol didn't enumerate.

## Connection to Design Philosophy

The gaia-patterns Workflow Design Philosophy captures this directly: *"Be positive -- describe what to do, not what to avoid"* and *"Allow discovery -- agent reaches conclusions empirically."* These principles apply directly to skill writing. A skill full of prohibitions ("never do X", "do NOT do Y") trains avoidance, not understanding. A skill that describes the better path and explains why it's better trains judgment that generalizes.
