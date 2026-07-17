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

## Pre-v1.6 Closure Pass Complete — 2026-07-06
Both v1.5 tails are closed and the editor split is unblocked. Shipped as CHANGELOG [1.5.1]:
- **`cautious` preset** — first live decorator use (`Cooldown` 3.0s over the tuned default envelope); in-gameplay smoke check per the standing v1.4 decorator decision (3 attempts/7.5s vs default's 8). `Invert`/`Repeat` remain unexercised.
- **Checkpoint consumer** — `kill_plane` respawns at the last checkpoint for 25 HP once one is crossed; terminal before that (see [[brain/Key Decisions]]).
- **Curated level `levels/v1.json`** (shipped as `level.json`) — first content to exercise doors, pickups, and all of patrol/flee/aggressive/cautious together; closes the v1.3-remainder curated-level item too.
- **Weapon pre-grant removed** — Pistol-only start; Shotgun/Rifle gated behind level pickups.
- **§5 combined regression run** against the new level via the smoke harness (playthrough + pickup-in-trigger same-frame, kill-plane mid-switch, pause mid-reload, resize mid-firefight, WIN/GAME-OVER + R). Harness itself fixed en route (argv[0]/asset_folder — see [[brain/Gotchas]]).

Remaining human-only item (logged in CLAUDE.md tech debt, low): one hands-on game-feel pass of `levels/v1.json` (jump feel, difficulty, marker readability, OS-drag resize) before itch.io.

## v1.6 Shipped — 2026-07-07
The level editor refactor is done: `Scripts/level_editor.py` (4,169 lines) split into seven focused modules with **zero behaviour change**, verified step-by-step through the real-dispatch smoke harness (113/113 unit tests + the 4 standing scenarios after every step, plus 163 step-specific in-app checks). CHANGELOG [1.6.0].

**Shipped in v1.6:**
- `Scripts/editor_core.py` (1,346) — the `LevelEditor` class: toolbar, selection, snap, save/load, prefs, layout, and the input()/update() dispatchers (v1.2.4 priority chain intact)
- Five collaborator modules, each a class with an editor back-reference: `editor_hierarchy.py` (282), `editor_gizmo.py` (194), `editor_browser.py` (1,032), `editor_inspector.py` (1,201), `editor_playmode.py` (241)
- `Scripts/compat.py` (167) — shared shader patch (closed the long-standing tech-debt row); `Scripts/asset_resolve.py` (77) — resolvers relocated out of `undo_redo.py`
- `Scripts/level_editor.py` is now the 103-line standalone entry point (`_launch()` + two-line `__main__`; smoke-harness contract preserved)
- Fixed while moving: fix-backlog items 4 / 10 (gizmo pick HC13 `is_empty()` guard) / 15

Module boundary chosen: panels-as-collaborators (Track B Candidate A shape) — shared state stays on the core, collaborators reach it via back-ref, `undo_redo.py`'s delegator-name contract respected. See [[work/archive/2026/v1.6-level-editor-refactor]].

## v1.6 Fix Backlog Closed — 2026-07-07
All 16 items from the 2026-07-06 audit closed. CHANGELOG [1.6.1]. Highlight: fixing the editor F5
entity leak (item 2) surfaced a deeper, previously-invisible bug — Ursina's `destroy()` never
cascades to `parent=` children (only `loose_children`), so `HealthBar`/debug-line sub-entities
leaked on every teardown. The normal game's R-path was accidentally immune because `main_menu()`
runs a scene-wide nuclear sweep right after teardown, masking the gap; the editor's F5 exit has no
such sweep, making it directly observable (31→62 leaked entities across two round-trips before the
fix). Fixed at the source in `HealthBar.on_destroy()` rather than patched per call site. New
general Gotcha logged for the pattern — see [[brain/Gotchas]]. A small (2-entity) unrelated
editor-UI widget leak was found during verification and spun off as a separate low-priority task
rather than expanding this backlog's scope further.

See: [[work/archive/2026/v1.6-fix-backlog]].

## v1.7 Shipped — 2026-07-07

Editor UX bundle: gizmo precision + panel basics. CHANGELOG [1.7.0]. Fixed the actual "gizmo drag
feels imprecise" complaint — the old velocity-based drag moved entities based on mouse *speed*, not
cursor *position*, so a fast flick and a slow drag to the same screen point landed differently.
Replaced with plane-projection (cast the cursor ray onto a camera-facing plane containing the drag
axis, project each frame's hit onto the axis line); verified headless that identical start/end
cursor positions now produce identical world displacement regardless of speed. Also added a hover
highlight, a Rot X/Y/Z inspector field (rotation previously had zero editor UI — confirmed via full
git history that this was a gap since v1.2, not a regression), collapsible hierarchy/inspector/
browser panels (`Ctrl+H/I/B` + chevron buttons, `_apply_layout` reclaims freed width), and saved
layout presets (`Ctrl+Alt+1-5` / `Alt+1-5`, mirrors the camera-bookmark pattern exactly).

**Deferred, explicitly not silently dropped:** rotation *ring* gizmo (B2-ring), resizable panels
(C3 — also blocks full browser-height reclaim on collapse), and dock-style panel layout (C4/C5 —
C5 is a multi-week framework project, likely not worth it for a solo editor). Full cost/risk
breakdown in [[v1.7-editor-ux-scoping]].

See: [[work/archive/2026/v1.7-editor-ux-bundle]].

## Current Focus

_What am I working toward right now?_

- **v1.3 remainder / itch.io ship prep** — PyInstaller macOS `.app` build, 1 shot SFX (blaster.ogg/blaster_repeater.ogg wired on weapon fire, closed 2026-07-07 — but currently **INAUDIBLE**: the 2026-07-12 OpenAL crash fix forces `NullAudioManager` on this Mac, which postdates and silently undercuts the wiring work; needs re-scoping, not re-closing) + 1 ambient track (CC-0, still open — no source pulled yet), itch.io page with screenshots + clip. See CLAUDE.md Roadmap.
- **Hands-on playtest of `levels/v1.json`** — mechanics are harness-verified; game-feel is not.
- **Small editor-UI widget leak** (inspector/browser swatch, 2 entities per F5 cycle) — spun off from the fix-backlog close-out, not yet scheduled.
- **v1.7 deferred tier, if there's appetite** — rotation ring gizmo, resizable panels, dock-style layout. Not scheduled; see [[v1.7-editor-ux-scoping]].

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
- v1.6 — Level editor refactor: split `level_editor.py` into smaller modules [[work/archive/2026/v1.6-level-editor-refactor]]
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
| 2026-07-06 | Pre-v1.6 closure pass shipped ([1.5.1]) — both v1.5 tails closed, editor split unblocked | Curated level + checkpoint consumer + cautious preset + §5 regression all landed and harness-verified in one session; only the v1.6 module-boundary choice (Ivan's review) remains before the split |
| 2026-07-07 | v1.6 level editor refactor shipped — focus moves to v1.3 remainder (itch.io ship prep) + fix-backlog | 9 steps across two sessions (credit cutoff mid-step-4; resumed from re-derived repo state); zero behaviour change, every step harness-verified; backlog items 4/10/15 fixed in passing. Process change adopted mid-run: mechanical extraction committed before wiring so interruptions lose at most the wiring pass. See [[work/archive/2026/v1.6-level-editor-refactor]] |
|      | Created North Star | Initial setup |
