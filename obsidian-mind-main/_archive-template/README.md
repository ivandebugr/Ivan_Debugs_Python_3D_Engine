---
description: "Archive of obsidian-mind template machinery stripped for the solo-dev vault — team, review-cycle, and meeting features this project never used."
tags:
  - archive
---

# Archive: stripped template machinery

This folder holds the parts of the stock **obsidian-mind** template that a
solo indie game-dev project doesn't use. Nothing here was deleted — it was
relocated here (via `git mv` for tracked files, plain `mv` + `git add` for the
formerly-`.claude/`-ignored files) so it stays browsable and fully recoverable
from git history. Restore any piece by moving it back to its original path.

Original paths are preserved inside this folder, with one exception: the
`.claude/` command and agent files live under `dot-claude/` here instead of
`.claude/`, because the repo's root `.gitignore` ignores `.claude` at any
depth — keeping them under a real `.claude/` segment would leave them
untracked and *not* recoverable from history. `dot-claude/` escapes that rule.

## What's here and why it was stripped

| Path (here) | Original path | Why removed |
|---|---|---|
| `org/` | `org/` | No team — solo project. People/teams knowledge doesn't apply. |
| `work/1-1/` | `work/1-1/` | No 1:1s. |
| `work/meetings/` | `work/meetings/` | No meeting-notes inbox. |
| `work/incidents/` | `work/incidents/` | `brain/Gotchas.md` already serves the "what broke and why" function — no parallel system. |
| `perf/competencies/` | `perf/competencies/` | Corporate competency framework — not relevant to a public devlog. |
| `perf/evidence/` | `perf/evidence/` | PR deep-scans for performance reviews — not relevant. |
| `bases/Incidents.base` | `bases/` | View over stripped `work/incidents/`. |
| `bases/People Directory.base` | `bases/` | View over stripped `org/people/`. |
| `bases/1-1 History.base` | `bases/` | View over stripped `work/1-1/`. |
| `bases/Review Evidence.base` | `bases/` | View over stripped `perf/evidence/`. |
| `bases/Competency Map.base` | `bases/` | View over stripped `perf/competencies/`. |
| `dot-claude/commands/` | `.claude/commands/` | Review-cycle + meeting-capture commands: `om-self-review`, `om-review-peer`, `om-review-brief`, `om-peer-scan`, `om-slack-scan`, `om-capture-1on1`, `om-prep-1on1`, `om-incident-capture`. |
| `dot-claude/agents/` | `.claude/agents/` | Subagents that only those commands invoked: `people-profiler`, `slack-archaeologist`, `review-prep`, `review-fact-checker`. |

`perf/Brag Doc.md` and `perf/brag/` were **not** archived — they were repurposed
into the public `devlog/` (see `devlog/Devlog.md`).
