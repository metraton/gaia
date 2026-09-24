---
name: technical-explanation
description: Use when the deliverable explains a system, a process, an architecture, or a failure -- a bare "explain X", "how does this work", "what happened here", "qué es esto", "explicame cómo funciona", a status the reader must understand rather than execute, or the explanatory part of a README, a ticket, a deck, or a report. Loaded by the orchestrator when its answer explains, and by any specialist whose output explains.
---

# Technical Explanation

An explanation builds a mental model in the reader's head: what exists, how it
connects, what happens, and why -- in that order, at the altitude the reader can
hold, with one picture where a picture teaches faster than a sentence. This skill
is the shared vocabulary for doing that: the four decisions taken before writing,
the fixed order the model is built in, the two registers it is told in, and the
drawing rules for the one picture a fenced code block can carry. Whoever produces
an explanation loads it -- the orchestrator answering "explain X", a specialist
writing the narrative of a README, the objective of a ticket, or the idea behind
a deck. The container skills (`readme-writing`, `ticket-writing`,
`diagram-builder`) own their format and continue from here; this skill owns what
goes inside when the content explains.

The skill sits mid-flow. Upstream is a finding, a request, or a status already
established by investigation; it never re-derives those. Downstream is a
container that consumes the explanation as written -- so an explanation built in
the wrong order does not stay a local defect: the README that embeds it teaches
the wrong model, and the ticket that quotes it is misread by the stakeholder it
was written for.

## Core principle

The reader receives the model in the order they can hold it. A real identifier
named before the reader has a place to put it is lost; a picture with fifteen
concepts shown to a reader who can hold seven is noise; a reference table handed
to someone who asked "what is this" answers a question they did not ask. So the
explanation is built top-down -- a small picture of common nouns first, real
components second, detail last -- and every element in it is chosen by a
criterion the writer can state out loud: which kind of relation this idea is,
how much the reader needs right now, what this sentence or this picture is FOR.

## Step 1: Decide four things before writing

Nothing is written until these four are settled, because each one changes what
gets written. They are decided in this order and take a minute.

**1. Intent -- what the reader can do afterwards.** Locate a failure, choose
between two designs, operate a pipeline, trust a status, adopt a repository.
An explanation with no intent explains everything at equal weight, which is the
same as explaining nothing: the reader cannot tell which sentence to act on.

**2. Mode -- which of four kinds of text this is.** The four modes are distinct
things a reader arrives for, and mixing them is how a "what is X" question gets
a field reference for an answer.

| Mode | The reader arrives to... | It reads as |
|------|--------------------------|-------------|
| **Concept** | understand what something is and why it exists | a model: nouns, relations, consequences |
| **Procedure** | do something, once, now | numbered steps with the expected result of each |
| **Reference** | look one fact up | a table, scanned, never read start to end |
| **Decision** | choose between options | the options, the criterion, the recommendation and its cost |

A single deliverable can hold more than one mode (a README's narrative is a
concept; its usage is a procedure), but one mode per section, never blended in
one paragraph.

**3. Representation -- chosen by criterion, never by lookup.** Two questions
decide which shape the idea has, and the answer is a shape before it is a form:

- **Does the idea MOVE or STAND?** A thing that moves -- a request, a change,
  a failure propagating -- is a path with a direction, and its picture reads
  along that direction. A thing that stands -- a structure, a hierarchy, a set
  of responsibilities -- is an arrangement, and its picture reads by position.
- **Does the reading CONVERGE on one thing or DIVERGE into many?** A convergent
  idea has one centre (one decision, one root cause, one component everything
  depends on); a divergent one has peers (several services, several options,
  several phases with nothing above them).

The catalogue of forms is a set of EXAMPLES of what those two answers produce,
so the writer recognizes the shape they already chose -- not a menu to pick from:

| The idea... | Forms that shape produces |
|-------------|---------------------------|
| moves, diverges | flow (a path through stages), swimlane (a path across owners), timeline (a path across time), sequence (a path across parties, in message order) |
| moves, converges | decision (paths that collapse into one choice), state (a thing moving between a fixed set of conditions) |
| stands, diverges | architecture map (parts and the boundaries between them), before/after (two arrangements of the same parts) |
| stands, converges | tree (a hierarchy under one root), dependency (what rests on what, down to one base) |

When the two questions give an answer that the catalogue has no name for, the
answer still holds: draw the shape the questions produced and call it what it
is. When the two questions give no clear answer, the idea is not yet one idea --
split it before drawing it.

**4. Density -- how much the reader needs now.** The situation sets it, not the
writer's thoroughness. Under urgency the order is fixed and the form is short:
**STATE -> PROBLEM -> ACTION** -- what is the case now, what is wrong, what to
do next -- and nothing before the state, because a reader in an incident reads
the first line and acts. Normal density carries the model in the fixed order of
Step 2. Deep density adds the why behind each connection and the alternatives
that were rejected. The same truth appears at all three; density removes
sentences, never precision. The rule comes from the disciplines that write for
people under time pressure -- an aviation checklist or a control-room message is
short because a long one is misread, not because the writer knew less.

## Step 2: Build the model in the fixed order

The explanation answers four questions, always in this order, because each
answer is the place the next one is stored in:

```
what exists  ->  how it connects  ->  what happens  ->  why
   (nouns)        (relations)          (the path)     (the reasons)
```

Naming what happens before what exists forces the reader to invent the parts;
naming why before what happens gives reasons for events they have not seen.
The order is not stylistic -- it is the order a model can be assembled in.

The model is told at three levels, and the reader always meets them top-down:

- **Level 1 -- the picture.** At most seven boxes, common nouns only: the CI
  machine, the cluster, the old system, the edge, the store. No real identifier
  appears here -- no namespace name, no service-account email, no file path,
  no flag -- because an identifier is a label on a box the reader does not yet
  have. If the picture needs more than seven boxes, it is two pictures or a
  level too low.
- **Level 2 -- the real components.** The same boxes, now named as they are:
  the workflow file, the controller, the namespace, the bucket. Every real name
  lands on a box that level 1 already placed, so the reader files it instead of
  meeting it.
- **Detail.** Identifiers, flags, configuration values, commands, the exact
  error text. Reached by a reader who wants it, never encountered by one who
  does not.

A reader may stop after any level and hold a true model. That is the test of
the order: the explanation is true at every altitude it is cut at.

## Step 3: The two registers

The user works in two registers, and the levels above are how both are served
from one explanation rather than two.

**Level 1 IS the plain register.** It reads as a narrative, not an inventory:
what we came to do, what we found, where we are, what follows. Its nouns are
common ("the CI machine", not the service-account email; "the old system", not
the namespace). Its findings are told by their CONSEQUENCE, not by their
artifact -- "it would have destroyed something that is working", not the pasted
output of the plan. It separates explicitly what is ours from what is inherited.
And it closes with one sentence that holds the whole situation.

**The technical register sits beneath it** -- level 2 and the detail. It is the
same truth at another altitude, never a vaguer one: the plain register does not
simplify by omitting, it simplifies by choosing the altitude. A plain sentence
that cannot be expanded into its technical counterpart without changing its
meaning was not plain, it was wrong. Precision is never traded for vagueness at
either level; what changes between them is which nouns are used and how much is
said, not whether it is true.

A one-off measurement report does not need the plain register. A situation
described, a status summarized, a task explained, does.

## Step 4: Drawing rules for the inline lane

The inline lane is ASCII inside a fenced code block: the only picture that
reads identically on the web, in a terminal, and in a diff, and the only one
that needs no tool to verify. It is the default lane; the rendered lanes in
Step 5 are reached on request.

- **Depict the mechanism, not its name.** A box labelled "Kubernetes" teaches
  nothing; a box labelled "reconciles desired state" beside one labelled "holds
  desired state" teaches the mechanism. The picture shows what the parts DO to
  each other.
- **Label the arrows.** An unlabelled arrow is a relation the reader has to
  guess. `--pushes image-->` and `--reads manifests-->` are two different
  facts; `-->` twice is none.
- **One arrow = one relationship. One block = one responsibility.** A block
  that does two things is two blocks; an arrow that means "calls and also
  configures" is two arrows or one sentence.
- **One term per concept, one idea per sentence.** Do not call the same thing
  "the controller", "the operator", and "Flux" across three paragraphs; do not
  put a cause and its effect in one sentence when two sentences read faster.
  This is the controlled-language discipline of technical writing that must not
  be misread: a term with one meaning is a term the reader never re-resolves.
- **Control flow and data/traffic flow are two drawings, never one.** Who tells
  whom what to do, and what travels through whom, are different relations; a
  single picture carrying both is the crowded picture nobody can read.
- **Only notation that reads without a legend.** Boxes, `│ ─ ▼ ►`, and the
  tree marks `└── ├──`. No invented glyphs, no colour codes in text, no symbol
  whose meaning the reader has to be told. If a notation needs a legend it is
  not notation, it is a second thing to learn.
- **If a sentence says it faster, write the sentence.** Two parts and one
  relation is a sentence, not a diagram.
- **No decorative diagram.** A picture that repeats the paragraph beside it
  costs the reader a second read of the same fact and asserts nothing. A
  picture earns its place by showing a relation the prose would take longer to
  state.

The picture obeys the levels: the level-1 picture holds at most seven boxes of
common nouns; the level-2 picture is the same shape with real names.

## Step 5: Continuing into a render lane -- on request only

An explanation written by this skill is complete as text. Two lanes render it
when someone asks for a rendered artifact; neither is entered automatically,
and the request is what opens them -- an explanation that would "look better as
a deck" stays text until the user asks for the deck.

- **`diagram-builder`** -- a deck of nested sections and components, authored in
  YAML. The explanation lowers into it by a fixed correspondence: a **phase**
  becomes a **section** in reading `order`; a **step** becomes a **component**
  ordered inside its phase; the **path** becomes one **chip** whose members
  cross the phase-sections; a **disclosure level** becomes a **page**. Arrows
  do not survive the crossing: the engine draws no edges, so every arrow of the
  inline picture becomes chip membership plus `order`, and a relation that only
  an arrowhead could express is stated in a component's text instead. The
  flow-doctrine passage of that skill's semantic doctrine and its "flow --
  phases as sections" skeleton carry the field-level form; the field names and
  what the engine rejects are in `reference.md` here.
- **`artifact-diagramming`** -- inline SVG inside an HTML page, for a single
  rendered picture rather than a deck. It is a host-provided skill, not a Gaia
  one; the drawing rules of Step 4 hold there unchanged (mechanism over name,
  labelled edges, one relation per edge), and it adds the mechanics that keep an
  SVG legible in both themes.

The lane is chosen by the reader's medium, never by the writer's preference,
and the text explanation is kept -- the rendered artifact is a second telling
of it, not a replacement.

## Anti-patterns

- **Fifteen concepts before a map.** The reader is given every part at once and
  asked to assemble them; the level-1 picture of seven common nouns existed to
  spare them exactly that, and skipping it is how a correct explanation is not
  understood.
- **Identifiers in level 1.** A namespace name, a service-account email, or a
  file path in the first picture is a label on a box the reader does not have
  yet; it costs attention and stores nothing. Real names arrive at level 2, on
  boxes already placed.
- **A reference dump answering a "what is X" question.** The reader asked for a
  concept and received a table of fields: true, complete, and useless, because
  the mode was wrong. Mode is decided before writing (Step 1) so this cannot
  happen by momentum.
- **Invented notation.** A glyph, a bracket convention, or a colour code the
  reader must be taught turns the picture into a second subject. If it needs a
  legend, it is not the inline lane's notation.
- **Decoration.** A diagram that restates its paragraph, a box added for
  symmetry, an arrow drawn because the layout had room. Each costs a read and
  asserts nothing; the reader learns to skip pictures, including the one that
  mattered.
- **One picture carrying two flows.** Control and traffic in one drawing
  doubles the arrows and halves the legibility; the reader cannot tell "tells"
  from "sends".
- **Vagueness sold as plainness.** "Some things changed in the deployment" is
  not the plain register, it is the technical register with the facts removed.
  The plain sentence names the consequence ("the change would have deleted the
  database that is serving production") and the technical one names the
  artifact beneath it.

## Where the rest lives

- `examples.md` -- three worked explanations in the DevOps register (a
  Kubernetes workload with control and traffic separated; a CI/CD pipeline at
  level 1 and level 2; a troubleshooting flow at urgent density) and each
  anti-pattern shown before and after.
- `reference.md` -- the field-level correspondence into the two render lanes,
  what each engine accepts and rejects, and the form catalogue with the
  criterion each form answers.
