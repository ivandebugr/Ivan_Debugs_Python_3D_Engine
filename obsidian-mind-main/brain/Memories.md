---
description: "Index of memory topics — key decisions, patterns, gotchas, people context"
tags:
  - brain
  - index
---

# Memories

Persistent context and knowledge retained across sessions. Each topic lives in its own note — follow the links.

- [[Key Decisions]] — architectural and workflow decisions worth recalling
- [[Patterns]] — recurring patterns and conventions discovered across work
- [[Gotchas]] — things that have bitten before and will bite again
- [[People & Context]] — org structure, teams, review history, dynamics
- [[North Star]] — living goals document, read at session start
- [[Skills]] — custom slash commands and workflows

## Recent Context

- **v1.4 enemy behaviour trees shipped 2026-06-30** — all 9 steps verified by a wrap-up integration audit (110/110 unit tests); enemies now run a per-enemy behaviour tree (`behaviour_tree.py` compositors+decorators, `behaviour_nodes.py` 5 leaves, `behaviour_tree_factory.py` 4 presets, `level.json` `behaviour` field, editor preset+waypoint UI). See [[North Star#v1.4 Shipped — 2026-06-30]] and the archived [[work/archive/2026/v1.4-enemy-behaviour-trees]]
- Six new v1.4 entries in [[Key Decisions]] (one-tree-per-enemy ownership, RUNNING re-eval from child 0, Cooldown/Repeat semantics, FleeNode flee_range = separation distance, decorators-unexercised gap), two in [[Patterns]] (editor-stores/runtime-builds, derived movement thresholds), one in [[Gotchas]] (never build the tree on an editor placeholder)
- Active focus is now **v1.5 — trigger/zone system + weapon inventory API**; v1.3 and v1.4 docs archived to `work/archive/2026/`. See [[work/active/v1.5-gameplay-systems]]
- v1.2.6 shipped 2026-06-24 — startup crash, resize/camera, texture thumbnail, and tray-merge fixes all verified; see [[North Star]] and three [[Gotchas]] entries (`Text(text='')`, `window.on_resize` dead code, camera lens aspect ratio)
- **v1.6 level editor refactor shipped 2026-07-07** — `level_editor.py` (4,169 lines) → `editor_core.py` + five collaborator modules (back-ref pattern, see [[Patterns]]) + `compat`/`asset_resolve`; zero behaviour change, 9 harness-verified steps; backlog items 4/10/15 fixed in passing. Active focus is now the v1.3 remainder (itch.io ship prep) + the [[work/active/v1.6-fix-backlog]] remainder. See [[North Star#v1.6 Shipped — 2026-07-07]] and [[work/archive/2026/v1.6-level-editor-refactor]]
