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
| A reminder, or something offered routinely | `reminders`, recorded with `gaia notifications add`; nothing runs unattended, what is due waits for the next session |
| See or act on pending approvals | `pending-approvals` |
| Triage the mailbox, or connect a Google account | `gmail-triage`, `gws-setup` |
| Remember, find or curate what Gaia knows | `memory` |
| See what a session, an agent or a plan cost in tokens, or how a brief changed | `gaia usage show --plan N` or `--session ID`, and `gaia brief history <slug>`, which the orchestrator reads itself; the transcripts are loaded first with `gaia usage ingest`, run by `gaia-operator` |

Coming from 5.4? The behaviour changes you will meet -- declared workspaces, reminders in place of `gaia schedule`, append-only memory, signed package runners, the new approval questions and more -- are listed in [CHANGELOG.md, Upgrading from 5.4](./CHANGELOG.md#upgrading-from-54).

## Flow

One turn, from your prompt to the answer:

```
1. You write a prompt in Claude Code or OpenCode.
2. The orchestrator matches it against the surface_routing table (seeded from
   each agent's routing: frontmatter) and dispatches one specialist.
3. The dispatch is validated and its contract row born; the specialist is
   handed that contract, its CLI lane, and what Gaia already knows about it.
   On Claude Code that is hooks/pre_tool_use.py and hooks/subagent_start.py;
   on OpenCode, opencode/bridge.py prepends the same kernel to the Task
   prompt, with the specialist's skills listed by name.
4. The specialist works. Every command passes the tier classifier:
   T0-T2 run; T3 stops with an approval_id you answer in the host's dialog;
   a blocked command is refused with nothing to approve.
5. The specialist fills its row as it goes (gaia contract set/add/fill) and
   closes it (gaia contract finalize); the SubagentStop gate validates the
   row and records the episode in ~/.gaia/gaia.db -- hooks/subagent_stop.py
   on Claude Code, the child session's session.idle on OpenCode.
6. The orchestrator reads the row, not the message, and answers you.
```

Gaia interacts with three things outside itself: the host, which loads the hooks -- from [`hooks/hooks.json`](./hooks/hooks.json) on the Claude Code plugin, from `.claude/settings.local.json` on the npm package, through [`opencode/plugin.ts`](./opencode/plugin.ts) on OpenCode, which forwards the same lifecycle (session start on the first user message, each message, tool calls, a child's start and stop, compaction, session end) to [`opencode/bridge.py`](./opencode/bridge.py) -- only Claude Code's TaskCompleted hook, a logging passthrough, has no OpenCode counterpart; the `~/.gaia/` directory, where the database, evidence and logs live (`gaia paths` prints the resolved locations); and your repositories, which a specialist touches through its own git worktree (`gaia worktree`) and only mutates past the gate.

## Requirements

- One host: Claude Code >= 2.1.0 (the floor declared in [`.claude-plugin/plugin.json`](./.claude-plugin/plugin.json)) or OpenCode. No OpenCode floor is declared or checked; the plugin was measured on OpenCode 1.18.32, which runs subagents in the background only with `OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true` in its environment ([INSTALL.md](./INSTALL.md), Surface 3).
- Python >= 3.12 on `PATH` (the `engines` in [`package.json`](./package.json)); the CLI and the hooks are Python. On the plugin the hooks start through [`hooks/launch.sh`](./hooks/launch.sh), which needs `sh` and takes the first of `python3`, `python` or `py -3` that is really Python 3, so the python.org Windows install, which has no `python3`, works too.
- Node.js >= 18 and npm or pnpm, only for the package channels below.
- git, for the per-turn worktrees.
- Nothing is installed behind your back: there is no `postinstall`, and the database is created lazily on the first `gaia` command (`_ensure_db_bootstrapped` in [`bin/gaia`](./bin/gaia)).

## How it is used

Gaia reaches your host through one of three channels. The folder you install in is where the host loads Gaia; it is not a workspace until you declare one (see [Workspaces and projects](#workspaces-and-projects)).

| Channel | Host | What you install | Where `gaia` runs from |
|---|---|---|---|
| Plugin | Claude Code | `gaia@gaia-marketplace`, from this repository | the plugin's own `bin/gaia`, run by the orchestrator |
| Package | Claude Code | `@jaguilar87/gaia` from npm, then `gaia install --channel npm` | `node_modules/.bin/gaia`, or `~/.local/bin` with `--path` |
| OpenCode | OpenCode | the same package, then `gaia install --channel opencode` | as for the package |

`gaia install` and `gaia dev` take the channel with `--channel`; there is no default and no `all`. The package channel and the plugin exclude each other in one Claude Code install folder, since each registers Gaia's hooks: `gaia install --channel npm` refuses while the folder's settings enable a `gaia@...` plugin and names `claude plugin uninstall <plugin> --scope <scope>`, and `gaia dev --channel plugin` refuses while the package's hooks are registered in `.claude/settings.local.json` and names `gaia uninstall --workspace <folder>`, with `--channel npm` when OpenCode is recorded beside it (`channel_conflict` in [`bin/cli/install.py`](./bin/cli/install.py)). OpenCode joins either and is removed on its own with `gaia uninstall --channel opencode`; npm and the plugin share `.claude/`, so uninstalling either takes back the Claude Code entries. `gaia update` re-wires the channels `gaia install` recorded in `.claude/gaia-manifest.json`. `gaia doctor` names the channel it finds.

**Plugin.** In Claude Code:

```
/plugin marketplace add metraton/gaia
/plugin install gaia@gaia-marketplace      # terminal: claude plugin install gaia@gaia-marketplace
```

That is the whole install, and it does not put `gaia` on your terminal's `PATH`. The first session merges Gaia's permission set into `.claude/settings.local.json`, and asks for `/reload-plugins` (or a restart); when the folder lies outside every declared workspace, the session says so and names the command that declares one. Auto-update is off for third-party marketplaces; take a new release with `claude plugin marketplace update gaia-marketplace`, then `claude plugin update gaia@gaia-marketplace` and a restart.

**Package and OpenCode.** From the folder you install in:

```bash
npm install @jaguilar87/gaia      # or: pnpm add @jaguilar87/gaia
npx gaia install --channel npm    # or: pnpm exec gaia install --channel npm
                                  #   --channel opencode for OpenCode; --path
npx gaia doctor                   # one line per check, PASS or FAIL
```

`gaia install` migrates or creates `~/.gaia/gaia.db`, links six directories (`agents`, `tools`, `hooks`, `config`, `skills`, `opencode`) plus `CHANGELOG.md` into `.claude/`, merges the permission set and the hook registrations into `.claude/settings.local.json` without removing what you had there, and records every file and key it wrote in `.claude/gaia-manifest.json`. It declares no workspace and scans nothing: it reports the declared workspace that holds the folder, or the command to declare one. `--channel opencode` writes `opencode.json` pointing at the packaged `opencode/plugin.ts` instead of touching `.claude/`, so it can sit beside either Claude Code channel; `--path` also writes the `gaia` launcher to `~/.local/bin`. To take a new release: `npm install @jaguilar87/gaia@latest`, then `npx gaia update`, which re-wires the channels recorded in `.claude/gaia-manifest.json`. The step-by-step walk-through is in [INSTALL.md](./INSTALL.md).

**Then declare a workspace.** Installing is one step; declaring a workspace is another, and Gaia does not do it for you:

```bash
npx gaia workspace declare <name> <path>    # package and OpenCode
npx gaia scan --workspace <name> <path>
```

On the plugin, `gaia` is on the Bash tool's `PATH` inside a Claude Code session in the install folder, so the same two commands run there without `npx`.

### Workspaces and projects

Gaia is installed once. That one install, and its one database at `~/.gaia/gaia.db`, serves every workspace you declare.

A workspace exists only when you declare it: a name and the folder it covers. Installing Gaia, opening a session or scanning never creates one. Declare it, then let Gaia find its projects:

```
gaia workspace declare <name> <path>     # e.g. gaia workspace declare personal ~/code/personal
gaia scan --workspace <name> <path>      # finds the projects inside it
```

A name or a folder already declared is never given to another. Outside every declared workspace Gaia does not guess one; the session start, `gaia install`, `gaia workspace current` and `gaia worktree create` say:

```
/home/you/somewhere is not inside a declared workspace.
Declare one with: gaia workspace declare <name> <path>
```

Inside a workspace, four rules decide what Gaia sees:

```
~/code/                    workspace "code"
├── tools/                 project of "code", no group
├── clients/acme/api/      project of "code", group "clients/acme"
├── notes/                 not a project: no git
└── personal/              workspace "personal", declared inside "code"
    └── blog/              project of "personal", not of "code"
```

- **A project is a git repository**: any folder holding `.git`. A folder without git is not a project.
- **A group is the folder path between the workspace and the repository**, at any depth: `clients/acme` above. A repository sitting directly in the workspace has no group. The scan enters folders whose names start with a dot too, except tool folders such as `.git`, `.claude` and `node_modules`.
- **A repository belongs to the nearest declared workspace that contains it.** Workspaces can sit inside each other; the inner one owns its repositories, and a scan of the outer one leaves them alone.
- **A project is known by its `origin` remote before its folder.** Moving the folder keeps the project: the next scan finds it in its new place. A repository with no remote is known by its folder, so moving it makes a new project.

A second clone of the same remote is not a second project. The scan reports it as a copy of the project already recorded, and both folders share that one project and its memory.

To move a project into another declared workspace:

```
gaia project move <project> --into <workspace> --dry-run   # lists what moves and what stays
gaia project move <project> --into <workspace>
```

The project goes in one step, with its briefs (those created for it with `gaia brief new --project`; an existing brief can be tagged with its project, `gaia brief set-project <brief> <project> [--dry-run]`) and its profile: what Gaia knows about it, its declared workflow included. Its memory belongs to the project and is read from any workspace, so it moves with it without being copied. Briefs written for the workspace as a whole stay where they are. A workspace that is not declared is refused as the target, and a name found in two workspaces asks for `--from <workspace>`.

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
OpenCode   npx gaia uninstall --channel opencode --workspace <folder>, then the npm step above
```

`gaia uninstall` with no channel takes back every channel recorded in the install folder. Where two run side by side -- the plugin and OpenCode -- `gaia uninstall --channel npm|plugin|opencode` takes back only that one: OpenCode owns `opencode.json` and `.opencode/`, the Claude Code channel the rest of the manifest, the package copy stays while a remaining channel runs from it, and the other channel stays recorded. A channel the install folder does not record fails, naming the recorded ones. Run `gaia uninstall` from the install folder before removing the package or the plugin, while `gaia` still exists. It reverts `.claude/gaia-manifest.json` -- every file, link and settings key back to its prior state, `opencode.json` and the `--path` launcher included -- and writes a gzip snapshot of the database to `~/.gaia/snapshots/` unless `--no-backup`. An OpenCode-only folder has no `.claude/` to detect, hence `--workspace`. It also removes what the package manager and `gaia dev` left -- the Gaia package under `node_modules` and its `.bin` shim, Gaia's line in `package.json` and `package-lock.json` (your other dependencies stay), the tarballs `gaia dev` cached -- and lists as left in place, with the reason, what it cannot fix itself (a `pnpm-lock.yaml` or `yarn.lock`, `.claude/logs`); `--dry-run` lists exactly what the real run does. The plugin's sessions record what they write into the install folder in the same manifest -- the permissions and attribution merged into `.claude/settings.local.json`, the `.claude/hooks` link -- so `gaia uninstall` reverts the plugin's writes too, including the user entries the merge replaced, and keeps what you added since; a plugin install folder from before that record is recognized by Gaia's permissions and attribution.

On the plugin, `gaia` is not on your terminal's `PATH` and is gone once the plugin is removed, so run the plugin's own copy first. `claude plugin list --json` prints an `installPath` for each `gaia@gaia-marketplace` install; take the one installed for this folder (a local-scope install names it in `projectPath`) and run `<installPath>/bin/gaia uninstall` in a terminal in the install folder -- it needs only Python on `PATH`. Inside a Claude Code session in the install folder the same `bin/gaia` is on the Bash tool's `PATH`, so Gaia can run `gaia uninstall` there for you. Then remove the plugin.

**First turn.** Start the host in the install folder and ask:

```
claude          # or: opencode
> what is Gaia, and what can you do for me?
```

The orchestrator answers with the picture above and the table of what it can offer. `gaia scan --workspace <name>` re-indexes a declared workspace's repositories when they change, and `gaia status` shows what is wired.

## Structure

```
gaia/
├── agents/          # orchestrator + eight specialists; routing: seeds the table
├── skills/          # 40 techniques loaded by description match
├── hooks/           # host lifecycle entry points + security/context modules
├── gaia/            # host-neutral core: approvals, SQLite store, worktrees
├── opencode/        # the OpenCode plugin; registered by --channel opencode
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

MIT, see [LICENSE](./LICENSE). Created and maintained by Jorge Aguilar. Bugs and feature requests: [GitHub Issues](https://github.com/metraton/gaia/issues). Questions or contact: a GitHub direct message to [@metraton](https://github.com/metraton) or LinkedIn, linked from that GitHub profile.
