# Obsidian Mind

Personal Obsidian vault -- an external brain for work notes, decisions, performance tracking, and Claude context.

## Skills & Capabilities

This vault has [obsidian-skills](https://github.com/kepano/obsidian-skills) installed in `.claude/skills/`. Follow these skill conventions:

- **obsidian-markdown**: Obsidian-flavored markdown -- wikilinks, embeds, callouts, properties. See `references/` for callout types, embed syntax, and property specs. Always prefer `[[wikilinks]]` over markdown links.
- **obsidian-cli**: CLI commands for vault operations when Obsidian is running. See CLI section below.
- **json-canvas**: Create `.canvas` files with nodes, edges, and visual layouts. See `references/EXAMPLES.md`.
- **obsidian-bases**: Create `.base` files with views, filters, and formulas. Bases core plugin is enabled. See `references/FUNCTIONS_REFERENCE.md`.
- **defuddle**: Extract clean markdown from web pages via `defuddle parse <url> --md`.
- **qmd**: Semantic search across the vault via [QMD](https://github.com/tobi/qmd). Use PROACTIVELY before reading files. **Preference order — pick the highest surface available and stop:**
  1. **`mcp__qmd__query`, `mcp__qmd__get`, `mcp__qmd__multi_get`, `mcp__qmd__status`** — registered MCP tools. If you see them in your tool menu, they are live and pre-scoped to this vault's index. Use them first; no `--index` argument needed.
  2. **`qmd --index <name> query|search|vsearch|get|multi-get`** — CLI fallback for one-off shell checks or when the MCP server is unavailable. Always pass `--index <name>` where `<name>` is the `qmd_index` field from `vault-manifest.json` so the SQLite store stays isolated from other vaults on the machine.
  3. **Grep / Glob / Read** — last resort, only when QMD is not installed at all.

  The MCP server (`.mcp.json` → `.claude/scripts/qmd-mcp.mjs`), the CLI, and the SessionStart hook all read the same manifest field, so every surface scopes to the same store. On a fresh clone, run `node --experimental-strip-types scripts/qmd-bootstrap.ts` once to build the index.

### Custom Slash Commands

Defined in `.claude/commands/`. See [[Skills]] for full documentation.

| Command | Purpose |
|---------|---------|
| `/om-standup` | Morning kickoff -- load context, review yesterday, surface tasks, priorities |
| `/om-dump` | Freeform capture -- dump anything, gets routed to the right notes |
| `/om-wrap-up` | Full session review -- verify notes, indexes, links, suggest improvements |
| `/om-humanize` | Voice-calibrated editing -- make notes sound like you, not AI |
| `/om-weekly` | Weekly synthesis -- cross-session patterns, North Star alignment, uncaptured wins |
| `/om-vault-audit` | Audit indexes, links, orphans, stale context |
| `/om-vault-upgrade` | Import content from an existing vault into this obsidian-mind instance |
| `/om-meeting` | Prep for any meeting by topic -- subject-forward briefing with open items and considerations |
| `/om-intake` | Process notes inbox -- classify and route to the right vault notes |
| `/om-project-archive` | Move completed project from active/ to archive/, update indexes |

## Vault Structure

| Folder | Purpose | Key Files |
|--------|---------|-----------|
| `Home.md` | **Vault entry point** -- embedded Base views, quick links | Open this first |
| `vault-manifest.json` | **Template metadata** -- version, infrastructure vs user content boundaries, frontmatter schemas, version fingerprints | Used by `/om-vault-upgrade` for migration |
| `CHANGELOG.md` | **Version history** -- tracks template releases | Reference for upgrade paths |
| `bases/` | **All Bases centralized** -- dynamic views for navigation | `Work Dashboard`, `Templates` |
| `work/` | Work notes index | `Index.md` (detailed MOC) |
| `work/active/` | **Current projects only** | Move here when starting, move to archive when done |
| `work/archive/YYYY/` | Completed work organized by year | Grows over time |
| `work/audits/` | Project audits | Named `YYYY-<scope>-audit.md` |
| `devlog/` | Public devlog / changelog feed | `Devlog.md` (index) |
| `devlog/entries/` | Longer per-period devlog notes | One note per period |
| `brain/` | Claude's operational knowledge | `Memories.md`, `Key Decisions.md`, `Patterns.md`, `Gotchas.md`, `Skills.md`, `North Star.md` |
| `reference/` | Codebase knowledge, architecture maps | Flow docs, architecture docs |
| `thinking/` | Scratchpad for drafts and reasoning | Named `YYYY-MM-DD-topic.md` |
| `templates/` | Obsidian templates | `Work Note.md`, `Decision Record.md`, etc. |
| `_archive-template/` | Stripped stock-template machinery (team/review/meeting features), kept for recoverability | See its `README.md` |
| `.claude/commands/` | Slash commands | See command table above |
| `.claude/agents/` | Subagents | See subagents table below |
| `.claude/scripts/` | Hook scripts | `session-start.ts`, `classify-message.ts`, `validate-write.ts`, `pre-compact.ts`, `stop-checklist.ts`, `charcount.ts` |
| `.claude/skills/` | Obsidian + QMD skills | Loaded automatically via Skill tool |

## Obsidian CLI

When Obsidian is running, prefer CLI over raw filesystem — it provides vault-aware search, backlink discovery, and property management. **On macOS, open Obsidian before invoking the CLI**: the first `obsidian` call launches the Electron app (visible window flash) if no instance is running; subsequent calls forward args silently. In non-interactive contexts where you can't guarantee Obsidian is open (background hooks, automation), prefer filesystem reads instead.

```bash
obsidian read file="Note Name"                    # Read a note
obsidian create name="Name" content="..." silent   # Create without opening
obsidian append file="Name" content="..."          # Append to note
obsidian search query="text" limit=10              # Vault-aware search
obsidian backlinks file="Name"                     # Discover connections
obsidian tags sort=count counts                    # List all tags
obsidian tasks daily todo                          # Open tasks
obsidian daily:read                                # Today's daily note
obsidian property:set name="status" value="done" file="Name"
obsidian orphans                                   # Unlinked notes
```

`file=` resolves like a wikilink (by name). `path=` for exact path from root. Use `silent` to prevent files from opening. Run `obsidian help` for full reference.

## Session Workflow

### Starting a Substantial Session

The `SessionStart` hook automatically injects rich context: vault file listing, North Star goals, active work, recent git changes, open tasks (aggregated from `work/active/` and the vault root, excluding infrastructure files), and triggers a QMD re-index. Most context is already loaded -- you don't need to manually read files.

**Shortcut**: Run `/om-standup` for a structured morning kickoff that reads everything and presents a summary with suggested priorities.

If doing it manually:

1. Read `Home.md` -- vault entry point with embedded dashboards
2. Read `brain/North Star.md` -- ground suggestions in current goals
3. Check `work/Index.md` -- see active projects and recent notes
4. Scan `brain/Memories.md` -- index of memory topics, then read relevant topic notes
5. `obsidian tasks daily todo` -- see pending items

### Ending a Substantial Session

**When the user says "wrap up", "let's wrap", "wrapping up", or similar -- invoke `/om-wrap-up` automatically.** This runs a full review of the session.

If `/om-wrap-up` is not invoked, at minimum do these before wrapping up:

1. **Archive completed projects**: `git mv` from `work/active/` to `work/archive/YYYY/`, update `status: completed` (or use `/om-project-archive`)
2. Update `work/Index.md` if new notes or decisions were created
3. Update the relevant brain topic note (`brain/Key Decisions.md`, `brain/Patterns.md`, `brain/Gotchas.md`) with key learnings
4. Update `devlog/Devlog.md` if something shipped worth a devlog entry
5. Offer to update `brain/North Star.md` if goals shifted or new focus emerged
6. Verify all new notes link to at least one existing note (orphans are bugs)
7. Run `/om-vault-audit` if the session created many notes

Skip steps that don't apply. The goal is transferring durable knowledge from conversation to vault state.

### Thinking Workflow

Use `thinking/` for drafts, reasoning, and analysis before writing final notes. **Thinking notes are scratchpads, not storage.** They exist to help you reason -- once the reasoning produces durable knowledge, promote it to proper notes and delete the scratchpad.

1. Create a thinking note: `thinking/YYYY-MM-DD-descriptive-name.md`
2. Use the Thinking Note template
3. Reason through the problem, analyze options, draft content
4. Promote findings to atomic notes in the correct folder (not one monolith -- one note per distinct concept)
5. Delete the thinking note -- it served its purpose
6. If the thinking process itself is worth preserving (unusual), keep it but link to the promoted notes

### Creating Notes

1. **Always use YAML frontmatter** with at minimum `date`, `description` (~150 chars), `tags`, and type-specific fields. Work notes also need `quarter` (e.g., `Q1-2026`).
2. **Use templates** from `templates/`. Fill `{{placeholders}}` with real values.
3. **Place files correctly**:
   - **Active** work notes, decisions -- `work/active/`
   - **Completed** work notes -- `work/archive/YYYY/` (by year)
   - Project audits -- `work/audits/`
   - Devlog entries -- `devlog/` (`Devlog.md` index, longer notes in `devlog/entries/`)
   - Claude operational context -- `brain/`
   - Codebase knowledge -- `reference/`
   - Drafts -- `thinking/`
   - Vault root: `Home.md`, `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `vault-manifest.json`, `CHANGELOG.md`, `CONTRIBUTING.md`, `README.md`, `LICENSE`, `.gitignore`. No user notes at root.
4. **Name files descriptively.** Use the note title as filename.

### Note Types

| Type | Location | Naming | Key Sections |
|------|----------|--------|--------------|
| Work note | `work/active/` (then `archive/YYYY/` when done) | Descriptive title | Context, What/Why, Links, Related |
| Audit | `work/audits/` | `YYYY-<scope>-audit.md` | Findings, Severity, Resolution, Related |
| Devlog entry | `devlog/` (index) / `devlog/entries/` (long form) | Date + shipped feature | What shipped, What it looked like, What's next |
| Brain note | `brain/` | Topic name | Topic-specific content |

### Linking -- This Is Critical

**Graph-first, not folder-first.** Folders help browse in the sidebar. Links help discover through connections. Both matter, but links are the primary organizational tool.

**A note without links is a bug.** When creating a note, the FIRST thing to do after writing content is add wikilinks. Every new note must link to at least one existing note.

**Atomicity rule**: Before writing or appending to any note, ask: "Does this cover multiple distinct concepts that could be separate nodes?" If a note has or would have 3+ independent sections that don't need each other to make sense, split into atomic notes that link to each other.

Note types have graph roles:
- **Evidence nodes** (work notes, audits, devlog entries): add outbound links to concepts they demonstrate
- **Concept nodes** (patterns, decisions): stay definitional -- evidence arrives via backlinks
- **Index nodes** (Index, Devlog, Memories): actively curate links -- they're navigational

Link syntax:
- `[[Note Title]]` -- standard wikilink
- `[[Note Title|display text]]` -- aliased link
- `[[Note Title#Heading]]` -- deep link to section
- `![[Note Title]]` -- embed content inline
- `[[Note Title#^block-id]]` -- link to specific block

#### When to Link

- **Work note <-> Decision**: bidirectional links
- **Work note -> Audit**: link the audit that surfaced the work
- **Devlog -> Work note**: every entry links to the work/archive note behind it
- **Memories -> Source**: every memory links to where it was learned
- **Index -> Everything**: `work/Index.md` links to all work notes
- **North Star -> Projects**: active focus areas link to project work notes

### Maintaining Indexes

Update these when creating or archiving notes:

- **`work/Index.md`** -- add to Active Projects or Recent Notes, move completed to Archive
- **`brain/Memories.md`** -- index of memory topics. Add new memories to the relevant topic note, not here.
- **`brain/Skills.md`** -- register vault-specific workflows and slash commands
- **`devlog/Devlog.md`** -- log what shipped with links to the work note, add longer notes in `devlog/entries/` as needed

### Decision Records

1. Create in `work/` using the Decision Record template
2. Link from the work note(s) that led to the decision
3. Add to the Decisions Log table in `work/Index.md`
4. If significant, note in `brain/Key Decisions.md`

### Wins & Achievements

When significant work ships, add an entry to `devlog/Devlog.md` with a link to the work note(s). Frame it for an outside reader: what shipped, what it looks like (link screenshots/clips where they exist), what's next. This is the source feed for public devlog / YouTube content, not an internal review log.

## North Star

`brain/North Star.md` is a living document of goals and focus areas.

- **Read it** at the start of substantial sessions
- **Reference it** when suggesting priorities or trade-offs
- **Update it** when the user signals a shift in goals
- Both the user and Claude write to it

## Tags Convention

Use tags in frontmatter (not inline):

- **Type**: `work-note`, `decision`, `audit`, `devlog`, `thinking`, `north-star`, `brain`
- **Index**: `index`, `moc`
- **Status** (frontmatter field): `active`, `completed`, `archived`, `proposed`, `accepted`, `deprecated`
- **Project**: as needed, e.g. `project/level-editor`

## Properties for Querying

Beyond tags, use these frontmatter properties to enable search and Bases views:

- `status: active` -- find active projects
- `quarter: Q1-2026` -- find all work for a quarter (used by Work Dashboard Base)

## Memory System

**All project memories live in the vault.** The `~/.claude/` MEMORY.md is an auto-loaded index that points to vault locations. The `~/.claude/` MEMORY.md is the only file that should exist there -- it is an auto-loaded index. Never create additional memory files in that directory.

| System | Location | Purpose |
|--------|----------|---------|
| **MEMORY.md** | `~/.claude/projects/.../memory/MEMORY.md` | Auto-loaded index only. Pointers to vault notes. |
| **Vault memories** | `brain/` topic notes | Git-tracked, Obsidian-browsable, linked. All durable knowledge lives here. |

When asked to "remember" something:
1. Find or create the appropriate `brain/` topic note (Gotchas, Patterns, Key Decisions, etc.)
2. Add the knowledge there with a wikilink to context
3. Update `brain/Memories.md` index if a new topic note was created
4. Do NOT create additional files in `~/.claude/projects/.../memory/` beyond MEMORY.md -- they are not version-controlled

### When to Consult Brain Topics

The SessionStart hook injects a **Brain Topics (read on demand)** index listing each `brain/` topic note with its description and an `(empty)` marker for stub notes. Treat that index as a menu:

- When the user's message touches a topic from the index (debugging → Gotchas, "how do we usually…" → Patterns, "why did we decide" → Key Decisions, "which command / slash" → Skills), query QMD **first** before answering — call `mcp__qmd__query` with a `query` argument describing the topic (or fall back to `qmd --index <name> query "<topic>"` if MCP is unavailable). The search covers the whole vault, so filter or prioritize results whose `file` path is under `brain/`. Do not assume the topic name alone scopes the search.
- If QMD is unavailable, read the specific `brain/` note directly with the Read tool. Don't load all of `brain/` — only the one(s) matching the topic.
- Skip notes marked `(empty)` in the index — they're stubs with no substantive content.
- After answering, if the conversation produced durable knowledge, update the relevant brain note (see the "remember" workflow above).

## Agent Guidelines

### Graph-First Thinking

- **Folders group by purpose, links group by meaning.** A note lives in ONE folder (its home) but links to MANY notes (its context).
- When creating a note, add wikilinks FIRST. A note without links is a bug.
- Prefer bidirectional links: if A links to B, B should link back to A (unless B is a concept node that receives backlinks passively).
- Before creating a new subfolder, ask: "Can I solve this with a tag, a property, or a link instead?" Folders are for browsing convenience, not for categorization.
- After every substantial session, verify new notes have at least one inbound link.

### Where to Put Things

- **Writing about how the codebase works?** -- `brain/` (Patterns, Gotchas, Key Decisions)
- **Writing about what Claude should remember?** -- `brain/Memories.md` topic notes
- **Tracking active project work?** -- `work/active/`
- **Auditing the project?** -- `work/audits/`
- **Logging what shipped?** -- `devlog/` (see [[Devlog]])
- **Dumping unstructured info?** -- use `/om-dump` to auto-classify and route everything

## Subagents

Specialized agents in `.claude/agents/` for heavy operations. They run in isolated context windows.

| Agent | Purpose | Invoked by |
|-------|---------|------------|
| `brag-spotter` | Finds uncaptured wins for the devlog | `/om-wrap-up`, `/om-weekly` |
| `context-loader` | Loads all vault context about a project or concept | Direct |
| `cross-linker` | Finds missing wikilinks, orphans, broken backlinks | `/om-vault-audit` |
| `vault-librarian` | Deep vault maintenance -- orphans, broken links, stale notes | `/om-vault-audit` |
| `vault-migrator` | Classifies, transforms, and migrates content from a source vault | `/om-vault-upgrade` |

## Hooks

Five lifecycle hooks in `.claude/settings.json`:

| Hook | When | What |
|------|------|------|
| SessionStart | On startup/resume | QMD re-index, inject North Star, active work, recent changes, tasks, file listing |
| UserPromptSubmit | Every message | Classifies content (decision, incident, win, 1:1, architecture, person, project update) and injects routing hints |
| PostToolUse | After writing `.md` | Validates frontmatter, checks for wikilinks |
| PreCompact | Before context compaction | Backs up session transcript to `thinking/session-logs/` |
| Stop | End of every session | Lightweight checklist reminder: archive, update indexes, check orphans. For thorough review, use `/om-wrap-up` instead. |

## Rules

- Never modify `.obsidian/` config files unless explicitly asked.
- Preserve existing frontmatter when editing notes.
- Git sync is handled by the user's preferred method (obsidian-git, manual commits, etc.) -- don't configure git hooks or auto-commit.
- When asked to "remember" something, write to the relevant `brain/` topic note with a link to context. Never create memory files in `~/.claude/` -- they are not git-tracked.
- Prefer Obsidian CLI over filesystem when Obsidian is **already** running. On macOS, the first `obsidian` call launches the Electron app (visible window flash) if no instance is running — open Obsidian once at session start, then subsequent calls forward args silently. In non-interactive contexts where you can't guarantee Obsidian is open (background hooks, automation), prefer filesystem reads.
- **Always invoke Obsidian skills via the Skill tool** before doing vault work. Load `obsidian-markdown` when creating/editing `.md` files. Load `obsidian-cli` when running vault commands. Load `obsidian-bases` or `json-canvas` when working with those file types.
- Always check for and suggest connections between notes.
- Every note must have a `description` field (~150 chars). Claude fills this automatically.
- **Zero data loss**: when reorganizing, always use `git mv`. Never delete without explicit user confirmation.
