# agent-smith 2.0

Agent Smith is a rule-governed agent orchestration micro-framework for AI-assisted
development. It ships an MCP server, a curated set of opencode agents, and a
knowledge-base pipeline that turns host-authored rule files into a semantically
queryable rules database.

## Overview

Agent Smith gives an opencode project three things:

- **An MCP server** (`agent_smith`) exposing protocol, rules-access, and AST
  code-analysis tools.
- **A team of opencode agents** — a primary Architect that plans and delegates,
  plus specialist subagents for execution, review, QA, bash, and tool workflows.
- **A knowledge-base pipeline** that embeds host-authored rule scenarios into a
  vector database, and a reasoning-enrichment plugin that surfaces the relevant
  rules to agents automatically, as they think.

Agent Smith never distributes rule content. The host owns its rules; Agent Smith
provides the machinery to govern agents with them.

## Agent roles

| Role           | Purpose                                                             |
|----------------|---------------------------------------------------------------------|
| The Architect  | Primary coordinator; plans work and delegates to specialist agents. |
| The Executor   | Applies file edits and implementation steps; delegates bash to the Basher. |
| The Oracle     | Reviews changes for rule and convention compliance.                 |
| The Inquisitor | Audits intent match, test quality, and coverage risk.               |
| The Basher     | Runs bash commands on behalf of other agents.                       |
| The Librarian  | Handles Jira, GitHub, and CircleCI tool workflows.                  |
| The Analyst    | Standalone primary agent that maintains the Agent Smith Knowledge Base. |

## Quick start

Prerequisites: [`uv`](https://astral.sh/uv), `git`, and Git authentication for
this repository. An authenticated `gh` session can configure Git credentials with
`gh auth setup-git`.

**1. Install.** No checkout of this repo is needed. From inside your host
repository, run the bootstrap:

```bash
gh api -H "Accept: application/vnd.github.raw" \
  "repos/GektorPrime/agent-smith/contents/install.sh?ref=mainframe" | bash
```

This installs the `agent-smith-*` commands as a persistent `uv` tool, downloads the
latest release into `~/.agent-smith-tool/<version>`, and seeds the scaffolding into
`<repo-root>/.agent-smith/`.  
If the `uv` tool bin directory is not already on your `PATH`, the bootstrap prints it 
— run `uv tool update-shell` once and open a new shell.

**2. Author your lore.** Agent Smith ships **no** rule content —
`.agent-smith/lore/rules_md/` is empty on install. Add your own:

- Rule markdown → `.agent-smith/lore/rules_md/` (subfolders are read
  recursively). To pull in existing documentation instead of copying it, run
  `agent-smith-symlink-rules <path>` — it symlinks a folder (with subfolders)
  or a single `.md` file into `rules_md/<name>/`. Each source is namespaced by
  its basename (override with `--name`), so multiple sources accumulate in
  batches without clobbering each other.
- Scenario JSON (for the knowledge base) → `.agent-smith/lore/json/scenarios/`,
  authored per `.agent-smith/lore/authoring/AUTHORING.md` and validated against
  `.agent-smith/lore/json/schema.json` using `agent-smith-kb-audit`

**3. (Optional) Add a persona.** Copy `.agent-smith/persona.md.example` to
`.agent-smith/persona.md` and edit it to shape the agents' communication style.

**4. Activate.** From inside your repository:

```bash
agent-smith-sync
```

`--repo-root` is auto-discovered from the nearest `.git`;  
pass it explicitly only for non-git projects or when running from elsewhere. 
Add `--protocol rules|knowledge` to override the protocol.  
Sync is idempotent and safe to re-run; deactivate any time with `agent-smith-sync --desync`.

## How it works

### Protocols

The active protocol is resolved as: `--protocol` flag → `.agent-smith/.mode` file
→ default `knowledge`, and is persisted to `.agent-smith/.mode` on every sync.

| Protocol | Behaviour                                                                                                                                                                                                                       |
|---|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `rules` | `agent_smith_init` rotates gate phrases and mandates reading of the entry point, all rule files, and the persona, followed by an acknowledgment and `agent_smith_handoff`.                                                      |
| `knowledge` | No mandatory reads: agents query rules on demand via `agent_smith_query_rules`, and the `reasoning-enrichment.ts` plugin surfaces relevant rules passively. Falls back to the full `rules` protocol when `rules.db` is missing. |

### Reasoning enrichment

The reasoning-enrichment plugin (`reasoning-enrichment.ts`) is the backbone of the
`knowledge` protocol. Symlinked into `.opencode/plugins/` at sync, it monitors the
agent's reasoning stream in real time, detects project relevance via keyword
matching, fires a debounced semantic knowledge-base query, and injects the matched
rules into the system prompt — and into the compaction summary so they survive
context resets. It never blocks: rules appear passively on the agent's next turn.

Two host-tunable knobs live at the top of the file:

- `AGENT_SMITH_PROJECT_PATH_MARKER` — environment variable naming the path prefix
  that marks relevant files (e.g. `src/`, `app/`, `tests/`). Defaults to the worktree.
- `KEYWORDS` — the list of terms that trigger enrichment. Replace the generic
  defaults with terms specific to your project and conventions.

A companion TUI plugin, `agent-smith-rules-panel.tsx`, surfaces the currently
active rules (count, severity, tags) in the opencode terminal UI; sync registers
it in `.opencode/tui.json` under the `knowledge` protocol.

### Gate mechanism

Every gate-carrying file ends with an `AGENT GATE:` HTML comment holding a random
`XXXX-XXXX-XXXX` token. The token is rotated on every `agent_smith_init` MCP tool call,
forcing agents to actually read files to completion before acknowledging. 

Token rotations are git-visible; `agent-smith-rollback-gates` reverts files whose only
change is the token.  
**It is recommended to implement pre-commit logic on the hosting end to automate this part.**

Gate markers live in `entry-point.md`, `persona.md`, and every
`lore/rules_md/**/*.md` file (sync recurses into subfolders and appends a marker
to any rule file that lacks one; symlinked rule files are marked and rotated
through the link into their source).

## Commands

After the Quick start install these are available as bare commands. The CLI
discovers `.agent-smith`, the project root, and `.mode` from the current directory.

| Command | Purpose                                                                                                  |
|---|----------------------------------------------------------------------------------------------------------|
| `agent-smith-install-release` | Install, upgrade, or remove a versioned Agent Smith release. <br/>[Uninstall](#Uninstall) (`--uninstall`)|
| `agent-smith-symlink-rules` | Symlink rules documentation (a folder with subfolders, or a single `.md` file) into `.agent-smith/lore/rules_md/`, namespaced per source (`--name`). |
| `agent-smith-sync` | Activate (default) or deactivate (`--desync`) Agent Smith in a host project.                             |
| `agent-smith-kb-sync` | Build/sync the knowledge base from scenario JSON (`--force` for full rebuild).                           |
| `agent-smith-kb-audit` | Audit scenario JSON against the schema and content checks.                                               |
| `agent-smith-kb-visualize` | 3D UMAP visualization of the knowledge base (requires the `viz` extra).  <br/>2D Spotlight visualization with DB inspector. (`--no-spotlight` loads only 3D UMAP view) |
| `agent-smith-kb-export-tensorboard` | Export embeddings for the TensorBoard Embedding Projector.                                               |
| `agent-smith-kb-query` | Query the knowledge base from the command line.                                                          |
| `agent-smith-rollback-gates` | Revert gate-token-only changes in gate files.                                                            |
| `agent-smith-mcp` | Run the MCP server (stdio) for debug purposes. MCP server starts automatically for regular flow.         |

Common examples (run from inside your repository):

```bash
agent-smith-sync                        # activate with the resolved protocol
agent-smith-sync --protocol knowledge   # activate a specific protocol
agent-smith-sync --desync               # deactivate
agent-smith-symlink-rules ./docs/rules  # link a rules-doc tree (subfolders supported)
agent-smith-symlink-rules ./GUIDE.md --name onboarding   # link one file under a batch name
agent-smith-kb-query "query text" --k 5
```

`--repo-root <path>` is available on `agent-smith-install-release` and
`agent-smith-sync` but is not required: both auto-discover the project root by
walking up to the nearest `.git`.  
**Pass it only for non-git projects or when running from outside the repository.**

### Make shortcuts

So you don't have to memorize the `agent-smith-*` command names, install seeds a
`Makefile` into `<repo-root>/.agent-smith/`. It wraps the bare commands as short
targets. From the repo root:

```bash
make -C .agent-smith help                    # list all targets
make -C .agent-smith sync                     # agent-smith-sync
make -C .agent-smith sync ARGS='--protocol rules'
make -C .agent-smith kb-query ARGS='"query text" --k 5'
make -C .agent-smith desync
make -C .agent-smith uninstall-release ARGS='--version <tag>'  # pin a release
make -C .agent-smith nuke                     # uninstall + drop .agent-smith
```

Every target accepts extra flags via `ARGS='...'` and maps 1:1 to the command in
the [Commands](#commands) table (target `kb-query` → `agent-smith-kb-query`,
`rollback-gates` → `agent-smith-rollback-gates`, etc.). Targets call the bare
commands, so they require the standard install to be on `PATH`.

To drop the `-C .agent-smith`, add the shell alias printed by
`make -C .agent-smith alias`:

```bash
alias asm='make -C .agent-smith'
# then: asm sync   /   asm kb-query ARGS='"query text" --k 5'
```

The `Makefile` is host-owned once seeded — edit it freely; reinstalls never
overwrite your changes.

### MCP tools

The `agent_smith` MCP server exposes these tools (agents call them prefixed with
`agent_smith_`):

- **Protocol:** `init`, `handoff`, `check_db`
- **Rules access:** `query_rules`, `read_entry_point`, `read_persona`, `read_rules`
- **AST code analysis:** `ast_analyze_structure`, `ast_class_outline`,
  `ast_list_imports`, `ast_find_definitions`, `ast_search`

### Running commands by path

If the bare commands are not on `PATH`, or to pin an exact version, run through the
extracted release instead. This also avoids stale global Python launchers shadowing
the current package:

```bash
# any console script, from the extracted release
uvx --from ~/.agent-smith-tool/<version> agent-smith-kb-query "query text" --k 5

# sync also ships a thin wrapper for this
bash ~/.agent-smith-tool/<version>/bin/sync.sh [--protocol rules|knowledge]

# or straight from GitHub, no install dir needed (requires repository access)
uvx --from 'git+https://github.com/GektorPrime/agent-smith.git@<version>' agent-smith-sync
```

Running sync via `bin/sync.sh` records the local release path as the MCP server's
package source, so the server and plugin queries need no GitHub access. Bare
`agent-smith-sync` and plain `uvx --from ...` record `git+…@<version>` instead,
which is re-fetched from GitHub at runtime.

SQLite-dependent commands automatically re-execute through the host's local
runtime package when the launcher's Python cannot load SQLite extensions.

## Installation reference

The Quick start bootstrap (`install.sh`) does two things: it runs `uv tool install`
on the `mainframe` package — exposing every `[project.scripts]` entry point as a
bare command — then execs `agent-smith-install-release`, which:

- resolves the latest release tag from GitHub (pin one with `--version <tag>`);
- downloads the release into `~/.agent-smith-tool/<version>`, using a local
  `git archive` for developer checkouts and an authenticated Git clone otherwise;
- mirrors the structural scaffolding (`wireframe/`) into
  `<repo-root>/.agent-smith/`, updating files that still match a previously
  installed release while never overwriting host-customized files.

If the `uv` tool bin directory is not already on `PATH`, the bootstrap prints it;
run `uv tool update-shell` once and open a new shell to use the bare commands.

Once a release is installed, `agent-smith-install-release` runs
`uv tool install --force ~/.agent-smith-tool/<version>`, re-pointing the
persistent `agent-smith-*` PATH commands at the version just installed. This
keeps the bare `agent-smith-sync` matching the latest release instead of lagging
behind at whatever `install.sh` last bootstrapped. If `uv` is missing the step
is skipped with a warning and the release install still succeeds; the install
summary then falls back to recommending the pinned `uvx --from ...` invocation.
As a safety net, `agent-smith-sync` also warns when it detects a newer release
under `~/.agent-smith-tool/` than the command currently running.

> **`install.sh` is still required for the first bootstrap.** The
> `agent-smith-install-release` **console script** can only refresh the PATH
> commands once it exists — so on a clean machine a bare
> `agent-smith-install-release` is `command not found`; bootstrap with
> `install.sh` (or `uvx --from ...`) first. After a clean uninstall
> (`uv tool uninstall agent-smith`) the bare commands disappear until the next
> bootstrap.

### Install without the bootstrap

`agent-smith-install-release` can also be run directly via `uvx`, skipping
`install.sh`:

```bash
uvx --from 'git+https://github.com/GektorPrime/agent-smith.git@mainframe' \
  agent-smith-install-release
```

It downloads the release, seeds the scaffolding, and (when `uv` is available)
refreshes the persistent `agent-smith-*` PATH commands to the installed version.
If you prefer not to touch `PATH`, follow-up commands can still be run by path
(see [Running commands by path](#running-commands-by-path)).

### Passing options through the pipe

These forms apply to the `install.sh` bootstrap above.

Environment variables are the simplest form
(`AGENT_SMITH_TARGET_REPO_ROOT`, `AGENT_SMITH_VERSION`):

```bash
gh api -H "Accept: application/vnd.github.raw" \
  "repos/GektorPrime/agent-smith/contents/install.sh?ref=mainframe" \
  | AGENT_SMITH_TARGET_REPO_ROOT=/path/to/your/project bash
```

Flags work too, but must go after `bash -s --` (an easy-to-miss form — plain
`bash --repo-root ...` or `bash -s --repo-root ...` fails):

```bash
gh api -H "Accept: application/vnd.github.raw" \
  "repos/GektorPrime/agent-smith/contents/install.sh?ref=mainframe" \
  | bash -s -- --repo-root /path/to/your/project --version <tag>
```

### Uninstall

`--uninstall` removes the installed version (pin one with `--version <tag>`,
otherwise the latest is resolved); add `--purge-host-data` to also delete
`<repo-root>/.agent-smith`. A normal uninstall operates globally and does not
require a repository path:

```bash
agent-smith-install-release --uninstall
# or by path:
uvx --from ~/.agent-smith-tool/<version> agent-smith-install-release --uninstall
```

### Non-git projects

Supported: pass `--repo-root <path>` explicitly to both `install.sh` and
`agent-smith-sync` (auto-discovery anchors on `.git`). The only git-dependent
piece is `agent-smith-rollback-gates`, which becomes a no-op.

### Developer checkout

Developers working on Agent Smith itself can run `bash install.sh` from their
checkout. The release source is archived from the local tag; `uv` may still
download uncached Python dependencies.

## Sync reference

Activation (`agent-smith-sync`):

- appends an `AGENT GATE:` marker to any rule file under `lore/rules_md/`
  (recursively, including subfolders and symlinked batches) that lacks one
  (warns when the directory has no rule files yet);
- symlinks `.agent-smith/opencode/` files into the host `.opencode/`
  (host-owned files and foreign symlinks are never clobbered — they are skipped
  with a warning);
- selects `.opencode/AGENTS.instructions.md` for the resolved protocol and
  registers it in `.opencode/opencode.json`;
- writes the `agent_smith` MCP block into `.opencode/opencode.json` (other keys
  untouched);
- writes `.agent-smith/knowledge_base/runtime.json` so opencode plugins use the
  same package source and protocol as the MCP server;
- registers the Agent Smith rules panel in `.opencode/tui.json` under the
  `knowledge` protocol, preserving host TUI settings and plugins;
- builds the knowledge base in-process when the protocol is `knowledge` and
  scenario JSON exists.

Deactivation (`agent-smith-sync --desync`) removes the Agent Smith symlinks from
`.opencode/`, strips the `agent_smith` MCP block and instruction registration from
`.opencode/opencode.json`, removes the rules-panel registration from
`.opencode/tui.json`, and leaves `.agent-smith/` fully intact (gate markers
included). Re-run sync to reactivate.

The package source recorded in the MCP block and `runtime.json` depends on how
sync was run:

- via a bare `agent-smith-sync` (Quick start) or plain `uvx --from ...` —
  `git+…@<version>`, which the MCP server and plugin queries re-fetch from GitHub
  at runtime;
- via the `bin/sync.sh` wrapper — the local extracted release under
  `~/.agent-smith-tool/<version>`, needing no GitHub access after installation.

Either way the MCP server runs with `AGENT_SMITH_HOME`, `AGENT_SMITH_PROJECT_ROOT`,
and `AGENT_SMITH_PROTOCOL` set.

## Development

```bash
uv venv && uv pip install -e '.[dev]'
.venv/bin/python -m pytest tests/
```

The `viz` extra installs the heavy visualization dependencies
(`uv pip install -e '.[viz]'`). When a bare `agent-smith-kb-visualize` launcher
lacks them, it automatically re-executes from the host's local runtime package as
a uv project with the `viz` extra enabled.
