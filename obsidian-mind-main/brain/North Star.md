---
date:
description: "Living document of goals, focus areas, and aspirations — read at session start, updated when direction shifts"
tags:
  - brain
  - north-star
aliases:
  - Goals
  - Focus
---

# North Star

A living document of goals, aspirations, and current focus areas. Both you and Claude write to this. Claude reads it at the start of meaningful work sessions and references it when making suggestions.

v1.2 audit complete — 19 findings (0 CRITICAL, 4 HIGH, 9 MEDIUM, 6 LOW); fix session pending. See [[work/audits/2026-v1.2-audit]].

v1.2.3 full audit complete — 12 findings (0 CRITICAL, 2 HIGH, 6 MEDIUM, 4 LOW); fix session pending. RICE top-3: hierarchy/inspector/tray scroll zooms EditorCamera; drag-and-drop ghost ray math wrong; enemy `origin_y` mismatch with editor placeholder. See [[work/audits/2026-v1.2.3-full-audit]].

## v1.2 Shipped — 2026-05-20
All tracks complete. Game state machine live. Canonical teardown functional. Level editor is Unity-feel with 8 features.

**Shipped in v1.2:**
- `Scripts/game.py` — `Game` state-machine class + singleton; zero module-level globals remain in `main.py`
- `_clear_gameplay_entities()` — single canonical 6-step teardown path
- JSON schema: `colour`, `rotation` on blocks; `hp`, `enemy_type`, `rotation_y` on enemies — all backwards-compatible
- `Scripts/undo_redo.py` — command-pattern undo/redo stack (depth 50)
- Level editor: grid snap (1/0.5/0.25/Off), multi-select + box-select, inspector panel, hierarchy panel, transform gizmos (X/Y/Z), camera bookmarks (1–5, persisted), play-in-editor (F5)

**Next priority:**
- v1.3 — Asset import pipeline: browser panel, drag-drop import, texture/model pickers, asset hot-reload in level editor

See: [[work/active/v1.2-level-editor-overhaul]] (Resolution section), [[work/archive/version-map]]

## v1.2.6 Shipped — 2026-06-24
Startup crash fixed (`Text(text='')` → `IndexError` in `Text.align()`), window resize + camera lens aspect ratio fix, texture thumbnail rendering fix, standalone placement tray merged into the Models tab of the asset browser. Closed out — no longer "next," fully verified per `CHANGELOG.md` [1.2.6].

## v1.3 Shipped — 2026-06-26
All 7 Implementation-Order steps complete and verified by a cross-step integration audit (re-derived from code, not session logs). The level editor now has a full asset pipeline: drop a file in and use it immediately without editing config.

**Shipped in v1.3:**
- `Scripts/asset_registry.py` — framework-free `AssetRegistry`: scans `assets/textures|models|sounds` → `{name: path}` manifests, persists `assets/manifest.json` (gitignored), mtime-cache fast path, `poll()`-driven hot-reload callbacks (Steps 1 & 3)
- Asset browser panel — Textures/Models/Sounds tabs, scrollable thumbnail cards, built-in models merged into the Models tab, drag-to-place with ghost/snap/undo (Step 2)
- Texture hot-reload — `_on_texture_changed` re-uploads to subscribed entities live; model/sound log-only per spec (Step 3)
- Texture picker (Step 4) + Model picker (Step 5) — floating inspector overlays, blocks-only model swap, each pushes its own `ChangeTextureCommand`/`ChangeModelCommand` (independent undo). `_resolve_texture`/`_resolve_model` fix the bare-path load bug.
- Asset import (Step 6) — shipped as an **Import Asset toolbar button** + native file picker (Panda3D has no OS file-drop on any release); copy-on-import, extension routing, name-collision skip-with-notice, manifest rebuild, play-mode guard
- `level.json` `model` field (Step 7) — optional, default `'cube'`, omitted-at-default for backwards-compat; resolved everywhere via `_resolve_model`

One known risk carried forward (flagged, ships per decision): bare-string texture on the load path — works via Ursina's folder glob but not routed through `_resolve_texture` like models are. See [[work/archive/2026/v1.3-asset-import-pipeline#Final Integration Audit — 2026-06-26]] and [[brain/Gotchas]].

See: [[work/archive/2026/v1.3-asset-import-pipeline]], [[work/archive/version-map]]

## v1.4 Shipped — 2026-06-30
All 9 Implementation-Order steps complete and verified by a wrap-up integration audit (re-derived from code, not session logs): 9/9 steps PASS, 7/7 cross-step traces clean, 8/8 hard-rule classes compliant, 110/110 unit tests green. Enemies are now driven by a per-enemy behaviour tree instead of a hardcoded detect→shoot block — composable, configured per-enemy in `level.json`, editable in the level editor, with the "default" preset reproducing the prior single-behaviour enemy exactly.

**Shipped in v1.4:**
- `Scripts/behaviour_tree.py` — pure compositor/decorator layer: `Status` enum + `BehaviourNode` ABC + 3 compositors (`Sequence`/`Selector`/`Parallel`) + 3 decorators (`Invert`/`Repeat`/`Cooldown`); zero Ursina/Panda3D imports (Steps 1 & 6)
- `Scripts/behaviour_nodes.py` — the 5 leaf nodes (`IdleNode`, `AttackNode`, `ChaseNode`, `PatrolNode`, `FleeNode`); movement delegated to `Enemy.chase_step`/`patrol_step` which route through the shared `swept_move_blocked` helper — no duplicated raycast loop (Steps 2–5)
- `Scripts/behaviour_tree_factory.py` — `BehaviourTreeFactory` with the 4 named presets (`default`, `patrol_then_attack`, `flee_when_low`, `aggressive`); unknown preset warns and falls back to default; default preset pinned to `enemy.py` tuned constants (Step 7)
- `level.json` `"behaviour"` field — optional per-enemy `{tree, waypoints}` config, surfaced once in `Scripts/level_io.py`, omitted-at-default for full backwards-compat; editor load stores the raw config, runtime/play-spawn builds the tree (Step 8)
- Level editor inspector behaviour UI — preset selector (4 buttons) + waypoint list editor (add/edit/delete, min-1 enforced via disable) shown only for `patrol_then_attack` enemies; `ChangeBehaviourCommand` snapshots the full per-entity config dict for clean multi-select undo/redo (Step 9)

One known limitation carried forward (logged, ships per decision): the three decorators are unit-tested but unexercised by any shipping preset — no decorator runs in the live frame loop until a future preset uses one. See [[work/archive/2026/v1.4-enemy-behaviour-trees]] and [[brain/Key Decisions]].

See: [[work/archive/2026/v1.4-enemy-behaviour-trees]], [[work/archive/version-map]]

## v1.5 Shipped — 2026-07-01
Two independent gameplay systems, both data-driven via `level.json`. Wrap-up audit re-derived every item from the committed code (not the step-status table, not session reports): **13/13 steps PASS** (System A 6 + System B 7), all cross-system traces clean, 5/6 hard-rule classes compliant on first read with the one violation fixed as its own step.

**Shipped in v1.5:**
- **System A — trigger/zone system** (`Scripts/trigger_system.py`): `TriggerZone(AliveEntity)` invisible AABB volumes, `Layers.TRIGGER` bitmask (skipped by both the horizontal swept-move ray and the vertical ground/ceiling rays so triggers never block movement), actions `kill_plane`/`checkpoint`/`open_door`/`win_condition`, editor placement + inspector action editor.
- **System B — weapon inventory** (`Scripts/weapon_inventory.py`, `Scripts/weapon.py`): `WeaponInventory` 3-slot switching with 0.2s slide anim; `Weapon` base class → `Pistol`/`Shotgun`/`Rifle` with per-weapon damage/fire-rate/ammo + reload + dry-click; `Layers.PICKUP` + `AmmoPickup`; ammo HUD counter; editor pickup placement.
- **HUD/menu redesign**: shared `Scripts/ui_theme.py` palette+spacing reused across `PlayerHUD`/`PauseMenu`/`EndScreen`/`health_bar.py`; resize-aware positions; Inter-Bold font.

The spec's own `TriggerZone`/`AmmoPickup` pseudocode was wrong (`self.intersects(player).hit`, standalone `register()`); the code correctly used the fixed pattern in both classes. Pool 30→50 resize correctly **not** applied (evidence-gated: 25/30 peak under real fire).

Gaps carried forward (logged): **§5 combined manual regression not yet run**; `open_door` + pickups are code-complete but **unexercised by `level.json`** (no door/pickup content placed); `checkpoint`'s `game.respawn_point` is store-only (no consumer); player still pre-given all 3 weapons in `Player.__init__` (temporary TODO). See [[v1.5-gameplay-systems#Wrap-up audit — 2026-07-01]] and [[brain/Gotchas]].

See: [[work/archive/2026/v1.5-gameplay-systems]], [[work/archive/version-map]]

## v1.6 Audit Complete — 2026-07-06
Whole-project architecture audit run (Fable 5, xHigh effort), widened beyond the original single-file refactor scope at Ivan's request. Read-only — no code changed this session.

**Findings:**
- Track A: `level_editor.py` confirmed as the only file at god-file scale (3957 lines, ~20 concerns already sectioned by banner comments). No other file has grown to a comparable size. Both v1.5 tails confirmed still open. New findings logged to Gotchas (editor F5 leak, `start_game()` duplicate teardown, and several minor items — see [[work/active/v1.6-fix-backlog|fix backlog]]).
- Track B: three candidate module-boundary breakdowns produced (panels-as-collaborators / state-chrome-interaction layers / strangler extraction) — **no candidate selected**, pending Ivan's manual review per the standing v1.6 gate.
- Track C: graphics recommendations checked against the actual GLSL 1.20 / macOS GL 2.1 ceiling (distance fog already built and unused; blob shadows and a texture pass are cheap wins; real lighting/post-processing blocked without a verified Core Profile context — a 1-day timeboxed spike was proposed to check feasibility). Content recommendation: one curated level closes 5 open items at once (see [[brain/Key Decisions]]).

**Next:** Pre-v1.6 closure pass (curated level + checkpoint consumer + tail closure) runs first. v1.6 module-boundary decision (Candidate A/B/C) remains open until Ivan reviews Track B.

See: v1.6 audit report, 2026-07-06.

## Current Focus

_What am I working toward right now?_

- **Pre-v1.6 closure pass** — curated level (`levels/v1.json`), checkpoint consumer, weapon pre-grant removal, §5 regression. Prerequisite before the editor split begins.
- **v1.6 module-boundary decision** — pending Ivan's manual review of the three candidates from the 2026-07-06 audit. See [[work/active/v1.6-level-editor-refactor]].

## Goals

### Short-term (This Quarter)

-

### Medium-term (This Half)

-

### Long-term (This Year+)

-

## Aspirations

_What kind of engineer/person am I becoming?_

-

## Anti-goals

_What am I explicitly NOT optimizing for?_

-

## 2026-05-20 — Full roadmap to v2.0 crystallised

Immediate: v1.1 audit fixes [[work/audits/2026-05-20-full-audit]].

Sequence to public release:
- v1.2 — Level editor overhaul + Game state machine + schema expansion [[work/active/v1.2-level-editor-overhaul]]
- v1.3 — Asset import pipeline + hot-reload [[work/archive/2026/v1.3-asset-import-pipeline]]
- v1.4 — Enemy behaviour trees (patrol/attack/flee) [[work/archive/2026/v1.4-enemy-behaviour-trees]]
- v1.5 — Trigger/zone system + Weapon inventory API [[work/archive/2026/v1.5-gameplay-systems]]
- v1.6 — Level editor refactor: split `level_editor.py` into smaller modules [[work/active/v1.6-level-editor-refactor]]
- v2.0 — Modding + packaged runtime + gamepad + procedural gen — PUBLIC RELEASE [[work/active/v2.0-release]]
- v2.x — Networked multiplayer (own milestone, post-release)

Rules: each version is a separate Claude Code session; no version starts until the previous passes the manual test checklist in CLAUDE.md; the CLAUDE.md tech debt table is updated at session end; [[work/archive/version-map]] is the authoritative public↔internal tag mapping.

## Shifts Log

Record when focus changes, with date and reason.

| Date | Shift | Reason |
|------|-------|--------|
| 2026-05-20 | v1.2 shipped — focus moves to v1.3 asset import pipeline | All 4 tracks complete; editor is now Unity-feel |
| 2026-05-20 | Full v1.2–v2.0 roadmap planned | Post-audit; engine in clean state, unblocked for feature work |
| 2026-06-24 | v1.2.6 cleanup closed out — focus moves to active v1.3 work (Steps 4–7) | Startup crash, resize/camera, texture thumbnail, and tray-merge fixes all verified; [[work/archive/2026/v1.3-asset-import-pipeline]] Steps 1–3 already done, picking up at the texture picker |
| 2026-06-26 | Captured v1.6 — level editor refactor — as a forward-looking planned milestone | Not started or designed yet; deliberately sequenced after v1.3–v1.5 feature work and gated on a manual design review. See [[work/active/v1.6-level-editor-refactor]] |
| 2026-06-26 | v1.3 asset import pipeline shipped — focus moves to v1.4 enemy behaviour trees | Cross-step integration audit passed all 7 steps; one latent texture-load risk flagged (not blocking) and carried forward. See [[work/archive/2026/v1.3-asset-import-pipeline#Final Integration Audit — 2026-06-26]] |
| 2026-06-30 | v1.4 enemy behaviour trees shipped — focus moves to v1.5 trigger/zone + weapon inventory | Wrap-up integration audit passed all 9 steps (7/7 cross-step traces, 8/8 hard-rule classes, 110/110 unit tests); decorators shipped unit-tested but unexercised by any preset (logged). v1.3 + v1.4 docs archived to `work/archive/2026/`. See [[work/archive/2026/v1.4-enemy-behaviour-trees]] |
| 2026-07-01 | v1.5 trigger/zone + weapon inventory shipped — focus moves to v1.6 level editor refactor | Wrap-up audit re-derived from committed code: 13/13 steps PASS, one hard-rule violation (PICKUP in COLLISION_MATRIX) fixed as its own step (`6b97cd4`). Two tails logged and carried forward (neither blocks v1.6): §5 combined manual regression not run; `open_door`/pickups code-complete but unexercised by `level.json`. System B + HUD landed as one squashed commit (interwoven working-tree snapshot from parallel worktrees, never branched). Stray `Co-Authored-By: Claude` trailer stripped from a v1.2.2 ancestor across `version_1.2–1.5` + force-pushed. v1.5 doc archived. See [[work/archive/2026/v1.5-gameplay-systems]] |
| 2026-07-06 | v1.6 widened to whole-project audit; pre-v1.6 closure pass identified as prerequisite | Ivan's explicit request; audit surfaced a higher-leverage move before the editor split |
|      | Created North Star | Initial setup |
