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

Gaia interacts with three things outside itself: the host, which loads the hooks -- from [`hooks/hooks.json`](./hooks/hooks.json) on the Claude Code plugin, from `.claude/settings.local.json` on the npm package, through [`opencode/plugin.ts`](./opencode/plugin.ts) on OpenCode; the `~/.gaia/` directory, where the database, evidence and logs live (`gaia paths` prints the resolved locations); and your repositories, which a specialist touches through its own git worktree (`gaia worktree`) and only mutates past the gate.

## Requirements

- One host: Claude Code >= 2.1.0 (the floor declared in [`.claude-plugin/plugin.json`](./.claude-plugin/plugin.json)) or OpenCode.
- Python >= 3.12 on `PATH` (the `engines` in [`package.json`](./package.json)); the CLI and the hooks are Python. On the plugin the hooks start through [`hooks/launch.sh`](./hooks/launch.sh), which needs `sh` and takes the first of `python3`, `python` or `py -3` that is really Python 3, so the python.org Windows install, which has no `python3`, works too.
- Node.js >= 18 and npm or pnpm, only for the package channels below.
- git, for the per-turn worktrees.
- Nothing is installed behind your back: there is no `postinstall`, and the database is created lazily on the first `gaia` command (`_ensure_db_bootstrapped` in [`bin/gaia`](./bin/gaia)).

## How it is used

Gaia reaches a workspace through one of three channels. The workspace is the folder you install in: its repositories are what the first scan indexes.

| Channel | Host | What you install | Where `gaia` runs from |
|---|---|---|---|
| Plugin | Claude Code | `gaia@gaia-marketplace`, from this repository | the plugin's own `bin/gaia`, run by the orchestrator |
| Package | Claude Code | `@jaguilar87/gaia` from npm, then `gaia install` | `node_modules/.bin/gaia`, or `~/.local/bin` with `--path` |
| OpenCode | OpenCode | the same package, then `gaia install --host opencode` | as for the package |

A Claude Code workspace with both the plugin and the package runs each hook once, the plugin's: `gaia install` writes no hooks when the workspace settings enable the plugin, and every plugin session takes the package's registrations out of `.claude/settings.local.json` (`resolve_hook_channel` and `sync_workspace_hooks` in [`hooks/modules/core/plugin_setup.py`](./hooks/modules/core/plugin_setup.py)). `gaia doctor` names the channel it finds.

**Plugin.** In Claude Code:

```
/plugin marketplace add metraton/gaia
/plugin install gaia@gaia-marketplace      # terminal: claude plugin install gaia@gaia-marketplace
```

That is the whole install, and it does not put `gaia` on your terminal's `PATH`. The first session merges Gaia's permission set into `.claude/settings.local.json`, asks for `/reload-plugins` (or a restart), and starts the first scan of the folder in the background. Auto-update is off for third-party marketplaces; take a new release with `claude plugin marketplace update gaia-marketplace`, then `claude plugin update gaia@gaia-marketplace` and a restart.

**Package and OpenCode.** From the folder that becomes the workspace:

```bash
npm install @jaguilar87/gaia      # or: pnpm add @jaguilar87/gaia
npx gaia install                  # or: pnpm exec gaia install
                                  #   --host opencode | --host all, --path
npx gaia doctor                   # one line per check, PASS or FAIL
```

`gaia install` migrates or creates `~/.gaia/gaia.db`, links six directories (`agents`, `tools`, `hooks`, `config`, `skills`, `opencode`) plus `CHANGELOG.md` into `.claude/`, merges the permission set and the hook registrations into `.claude/settings.local.json` without removing what you had there, and records every file and key it wrote in `.claude/gaia-manifest.json`. The first install registers the workspace under its folder name and scans the repositories beneath it. `--host opencode` writes `opencode.json` pointing at the packaged `opencode/plugin.ts` instead of touching `.claude/`; `--path` also writes the `gaia` launcher to `~/.local/bin`. To take a new release: `npm install @jaguilar87/gaia@latest`, then `npx gaia update` (an alias of `gaia install`) with the same `--host`. The step-by-step walk-through is in [INSTALL.md](./INSTALL.md).

**Database migrations.** A new release may move `~/.gaia/gaia.db` to a newer schema. `gaia install` and `gaia update` do it on their own, and so does the plugin at SessionStart when the database is behind. On its own means without asking: a backup goes to `backups/` beside the database, and the whole chain runs in one transaction. What decides whether it can go on alone is what the chain reaches:

```
chain only adds structure       -> applied on its own
chain reaches rows that exist   -> stops, and the message (or the plugin's
                                   startup notice) names the command to run:
     gaia migrate plan                          # the chain and what it reaches
     gaia migrate apply --consent-chain vA..vB  # consent once, for that chain
```

**Uninstall.** Each channel takes back only what it wrote. `~/.gaia/gaia.db` is never touched: delete `~/.gaia/` yourself if you want the memory gone too.

```
Plugin     <installPath>/bin/gaia uninstall    # installPath: claude plugin list --json
           claude plugin uninstall gaia@gaia-marketplace
           claude plugin marketplace remove gaia-marketplace   # optional
Package    npx gaia uninstall            # --dry-run first shows what reverts
           npm uninstall @jaguilar87/gaia     # or: pnpm remove @jaguilar87/gaia
OpenCode   npx gaia uninstall --workspace <folder>, then the npm step above
```

Run `gaia uninstall` from the workspace folder before removing the package or the plugin, while `gaia` still exists. It reverts `.claude/gaia-manifest.json` -- every file, link and settings key back to its prior state, `opencode.json` and the `--path` launcher included -- and writes a gzip snapshot of the database to `~/.gaia/snapshots/` unless `--no-backup`. An OpenCode-only folder has no `.claude/` to detect, hence `--workspace`. The plugin's sessions record what they write into the workspace in the same manifest -- the permissions and attribution merged into `.claude/settings.local.json`, the `.claude/hooks` link -- so `gaia uninstall` reverts the plugin's writes too, including the user entries the merge replaced, and keeps what you added since; a plugin workspace from before that record is recognized by Gaia's permissions and attribution.

On the plugin, `gaia` is not on your terminal's `PATH` and is gone once the plugin is removed, so run the plugin's own copy first. `claude plugin list --json` prints an `installPath` for each `gaia@gaia-marketplace` install; take the one installed for this workspace (a local-scope install names it in `projectPath`) and run `<installPath>/bin/gaia uninstall` in a terminal in the workspace folder -- it needs only Python on `PATH`. Inside a Claude Code session in the workspace the same `bin/gaia` is on the Bash tool's `PATH`, so Gaia can run `gaia uninstall` there for you. Then remove the plugin.

**First turn.** Start the host in the workspace and ask:

```
claude          # or: opencode
> what is Gaia, and what can you do for me?
```

The orchestrator answers with the picture above and the table of what it can offer. `gaia scan` re-indexes the workspace's repositories when they change, and `gaia status` shows what is wired.

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
