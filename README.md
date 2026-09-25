# GAIA

**Generative AI Interface for Agents.** Specialist agents for Claude Code and OpenCode, with a memory that outlives the session and a consent gate on everything that changes state.

[![npm version](https://badge.fury.io/js/@jaguilar87%2Fgaia.svg)](https://www.npmjs.com/package/@jaguilar87/gaia)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Node.js Version](https://img.shields.io/node/v/@jaguilar87/gaia.svg)](https://nodejs.org)

## What it is and why it exists

Gaia is a plugin for two terminal AI hosts, Claude Code and OpenCode. You talk to one agent, and it holds the conversation; it never edits a file or runs a general command itself. For each piece of work it sends out a specialist that lives for one turn inside its own field -- application code, infrastructure as code, a cluster's desired state, a live system, planning, or Gaia itself -- and that specialist comes back with a contract: what it looked at, what it changed, how it checked the result, and how the turn ended. What a turn learns is kept in a database on your machine, so the next session starts from what the last one found instead of from zero. And every command any agent tries to run is classified before it runs: reads pass, changes wait for your yes, and a short list of irreversible commands never runs at all.

The four belong together. A conversation that forgets makes you re-explain the project every day; a specialist that reports in prose makes you trust a story instead of a record; an agent that can change your cluster without asking makes you supervise every keystroke. Gaia keeps the conversation in one place, the work in specialists, the record in contracts, and the risk behind a gate.

```
                 you
                  │ asks, in your words
                  ▼
   ┌──────────────────────────────┐       ┌──────────────────────────────┐
   │ the one who holds the        │◄──────│ memory that outlives the     │
   │ conversation                 │recalls│ session                      │
   └──────────────┬───────────────┘       └──────────────▲───────────────┘
                  │ sends out one turn                   │ what stays
                  ▼                                      │
   ┌──────────────────────────────┐       ┌──────────────┴───────────────┐
   │ a specialist, born for this  │──────►│ a contract: what it found,   │
   │ turn inside its own field    │closes │ what it changed, how it ended│
   └──────────────┬───────────────┘  on   └──────────────────────────────┘
                  │ every command
                  ▼
   ┌──────────────────────────────┐       ┌──────────────────────────────┐
   │ a gate: reads pass, changes  │──────►│ your repository, cluster or  │
   │ wait for your yes            │ once  │ account                      │
   └──────────────────────────────┘you say└──────────────────────────────┘
                                    yes
```

The same boxes with their real names: the one who holds the conversation is the orchestrator, [`agents/gaia-orchestrator.md`](./agents/gaia-orchestrator.md), the identity [`settings.json`](./settings.json) activates. The specialists are the other eight agent files in [`agents/`](./agents/). The contract is a row in `~/.gaia/gaia.db` that the specialist writes through `gaia contract` while it works and closes with `gaia contract finalize`. Memory is the same database, read and curated through `gaia memory`. The gate is the tier classifier in [`hooks/modules/security/tiers.py`](./hooks/modules/security/tiers.py): T0 reads, T1 validation and T2 dry-runs run freely; a T3 mutation stops with an `approval_id` you answer in the host's dialog (`gaia approvals`); a blocked command has no approval path at all.

## What it can do for you

Ask the orchestrator "what is Gaia?" or "what can you do for me?" and it explains the picture above and offers this table. Each row names the skill or agent that answers it; every one exists under [`skills/`](./skills/) or [`agents/`](./agents/).

| You want to... | What answers |
|---|---|
| Change or investigate code, infrastructure, a cluster's desired state, or a live system | A specialist: `developer`, `platform-architect`, `gitops-operator`, `cloud-troubleshooter` |
| Understand something -- a system, a process, what happened, why it failed | `technical-explanation` |
| A README for a repository or a folder | `readme-writing` |
| A ticket or an issue | `ticket-writing` |
| A blog post | `blog-writing` |
| A diagram deck -- an architecture map, a timeline, a flow, a comparison | `diagram-builder` |
| Capture a feature before planning it | `brief-spec` |
| Plan it -- decompose it into verifiable tasks | `gaia-planner` (a skill and the agent of the same name) |
| A review of a module, a branch or a PR | `code-review` |
| Audit a Gaia component, live-check an area of Gaia, release it, verify the install | `gaia-audit`, `gaia-check`, `gaia-release`, `gaia-verify`, through the `gaia-system` agent |
| Look at repositories for something Gaia could take | `gaia-research` |
| Reflect on the session, or compact it | `session-reflection`, `gaia-compact` |
| Something that runs routinely rather than once | `scheduled-task`, mounted with `gaia schedule`, reporting through `gaia notifications` |
| See or act on pending approvals | `pending-approvals` |
| Triage the mailbox, or connect a Google account | `gmail-triage`, `gws-setup` |
| Remember, find or curate what Gaia knows | `memory` |

## Flow

One turn, from your prompt to the answer:

```
1. You write a prompt in Claude Code or OpenCode.
2. The orchestrator matches it against the surface_routing table (seeded from
   each agent's routing: frontmatter) and dispatches one specialist.
3. hooks/pre_tool_use.py validates the dispatch and births the contract row;
   hooks/subagent_start.py hands the specialist that contract, its CLI lane,
   and what Gaia already knows about it.
4. The specialist works. Every command passes the tier classifier:
   T0-T2 run; T3 stops with an approval_id you answer in the host's dialog;
   a blocked command is refused with nothing to approve.
5. The specialist fills its row as it goes (gaia contract set/add/fill) and
   closes it (gaia contract finalize); hooks/subagent_stop.py validates the
   row and records the episode in ~/.gaia/gaia.db.
6. The orchestrator reads the row, not the message, and answers you.
```

Gaia interacts with three things outside itself: the host, which loads the hooks in [`hooks/hooks.json`](./hooks/hooks.json) (Claude Code) or the plugin in [`opencode/plugin.ts`](./opencode/plugin.ts) (OpenCode); the `~/.gaia/` directory, where the database, evidence and logs live (`gaia paths` prints the resolved locations); and your repositories, which a specialist touches through its own git worktree (`gaia worktree`) and only mutates past the gate.

## Requirements

- One host: Claude Code >= 2.1.0 (the floor declared in [`.claude-plugin/plugin.json`](./.claude-plugin/plugin.json)) or OpenCode.
- Node.js >= 18 and Python >= 3.12 (the `engines` in [`package.json`](./package.json)). The CLI is Python; npm is the delivery channel.
- git, for the per-turn worktrees.
- Nothing is installed behind your back: there is no `postinstall`, and the database is created lazily on the first `gaia` command (`_ensure_db_bootstrapped` in [`bin/gaia`](./bin/gaia)).

## How it is used

**Install.** In Claude Code, as a plugin -- the host clones the repository at the tag of the current release, as pinned by `source.ref` in [`.claude-plugin/marketplace.json`](./.claude-plugin/marketplace.json), and reads its hooks from `hooks/hooks.json`:

```
/plugin marketplace add metraton/gaia
/plugin install gaia@gaia-marketplace      # terminal: claude plugin install gaia@gaia-marketplace
```

That route loads the agents, skills and hooks only. The `gaia` CLI on your terminal and the workspace wiring still come from npm plus `gaia install` below, and the hooks need `python3` >= 3.12 on `PATH`. Auto-update is off for third-party marketplaces; to take a new release run `/plugin marketplace update gaia-marketplace`, then `claude plugin update gaia@gaia-marketplace`.

Through npm, for either host (and for the CLI on the plugin route):

```bash
npm install @jaguilar87/gaia      # or: pnpm add @jaguilar87/gaia
gaia install                      # Claude Code; or --host opencode / --host all
gaia doctor                       # one line per check, PASS or FAIL
```

`gaia install` bootstraps `~/.gaia/gaia.db`, links six directories (`agents`, `tools`, `hooks`, `config`, `skills`, `opencode`) plus `CHANGELOG.md` into `.claude/`, and merges the twelve hook events and the permission set into `.claude/settings.local.json` without removing what you already had there. With `--host opencode` it writes `opencode.json` in the workspace pointing at the packaged plugin instead of touching `.claude/`. Run `gaia doctor` from the workspace, or pass `--workspace <path>`; the full walk-through and the troubleshooting table are in [INSTALL.md](./INSTALL.md).

**First turn.** Start the host in the workspace and ask:

```
claude          # or: opencode
> what is Gaia, and what can you do for me?
```

The orchestrator answers with the picture above and the table of what it can offer. Then `gaia scan` indexes the repositories under the workspace so every later dispatch carries the project's shape, and `gaia status` shows what is wired.

## Structure

```
gaia/
├── agents/          # orchestrator + eight specialists; routing: seeds the table
├── skills/          # 39 techniques loaded by description match
├── hooks/           # host lifecycle entry points + security/context modules
├── gaia/            # host-neutral core: approvals, SQLite store, worktrees
├── opencode/        # the OpenCode plugin; registered by --host opencode
├── config/          # context contracts, git standards and rules the hooks read
├── build/           # gaia.manifest.json -> plugin.json + hooks.json at pack
├── bin/             # the gaia CLI and its subcommands (bin/cli/)
├── tools/           # scanners, context providers and validators for CLI/hooks
├── scripts/         # build, migration and release scripts
├── tests/           # pytest suite plus prompt-regression and eval layers
├── INSTALL.md       # full install, manual equivalent, troubleshooting
├── ARCHITECTURE.md  # the component map at depth
├── CONTRIBUTING.md  # how to propose a change
└── SECURITY.md      # how to report a vulnerability
```

Each folder with a README explains what it is wired to and what breaks if you change it: [`agents/`](./agents/README.md), [`skills/`](./skills/README.md), [`hooks/`](./hooks/README.md), [`gaia/`](./gaia/README.md), [`config/`](./config/README.md), [`build/`](./build/README.md), [`bin/`](./bin/README.md), [`tests/`](./tests/README.md). Version history is in [CHANGELOG.md](./CHANGELOG.md).

## License and ownership

MIT, see [LICENSE](./LICENSE). Written and maintained by Jorge Aguilar; issues and questions go to [GitHub Issues](https://github.com/metraton/gaia/issues).
