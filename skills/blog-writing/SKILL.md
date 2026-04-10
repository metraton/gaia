---
name: blog-writing
description: Use when writing, drafting, or publishing a blog article for metraton.github.io
metadata:
  user-invocable: true
  type: technique
---

# Blog Writing

Jorge's blog (metraton.github.io) is where he thinks out loud about agentic systems, context engineering, and the intersection of architecture and AI. The articles are bilingual (English and Spanish), grounded in real experience, and written in first person. They are not corporate content -- they are reflections from someone building these systems daily.

## The Writing Process

### 1. Find the story first

Every article starts with something that actually happened -- a deployment that went sideways, a late-night refactor, a conversation that shifted your thinking. The story is the anchor. Without it, you are writing a tutorial, not an article.

Ask Jorge: "What happened recently that surprised you or changed how you think about something?" The best topics come from moments where the outcome was different from the expectation.

### 2. Build the brief

Before writing a single paragraph, define: title, audience, core thesis (one sentence), and format. Jorge's natural format is **Strategic Insight** -- personal experience analyzed through a technical lens, ending with a transferable lesson.

The audience is engineers, solution architects, AI practitioners, and tech leaders. They do not need hand-holding but they appreciate honesty about what did not work.

### 3. Draft in Markdown, iterate with Jorge

Write the first draft in Markdown at `/home/jorge/ws/me/<slug>.md`. Work section by section -- do not dump an entire draft and ask "what do you think?" Each section should be reviewed before moving to the next.

**Article structure** (not rigid, but this is Jorge's natural flow):
- Opening story -- what happened, told personally
- The problem -- what was broken or surprising
- Investigation -- what you looked into and found
- The shift -- the insight or reframe
- In practice -- what changed, concretely
- Results -- evidence it worked
- Closing thought -- punchy, memorable, one line

### 4. Principles that matter

**Examples must be real.** Not "imagine a team that..." but "I was deploying to Cloud Run when..." If you cannot point to something that actually happened, the example does not belong.

**Each section adds something new.** Do not repeat the same example or insight across sections. If the investigation section already showed the problem, the "in practice" section should show the solution -- not restate the problem.

**Quotes add weight when grounded.** Citing Anthropic, Hinton, or other thought leaders works when the quote connects directly to the experience. A quote floating without context is decoration.

**Technical terms stay in English in both languages.** LLM, skills, agent, Cloud Run, Terraform -- these do not get translated.

**Closing lines are signatures.** "Built with context.", "Built with reasoning." -- short, confident, tied to the article's thesis.

### 5. Convert to bilingual HTML

Once the Markdown draft is approved, convert to the bilingual HTML format. The Spanish version is not a mechanical translation -- it is natural Latin American Spanish, with the same voice and directness. Read `reference.md` for the HTML template and front matter structure.

Final HTML goes in: `/home/jorge/ws/me/metraton.github.io/_posts/YYYY-MM-DD-slug.html`

### 6. Preview and publish

Run Jekyll locally for preview (port 4000). Then commit and push to publish via GitHub Pages.

## Jorge's Voice

- First person, always. "I was deploying..." not "The team deployed..."
- Honest about failures and iterations -- does not pretend things worked the first time
- Confident but not preachy. States what he found, not what everyone should do
- Direct. Short sentences when making a point. Longer when telling a story
- Latin American perspective -- references to the region's tech community are natural, not forced

## Anti-Patterns

- Writing generic content that could appear on any corporate blog
- Translating English to Spanish mechanically instead of rewriting naturally
- Dumping an entire draft without iterating section by section
- Using hypothetical examples when real ones exist
- Repeating the same insight across multiple sections
