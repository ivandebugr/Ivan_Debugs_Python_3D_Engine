# Changelog

All notable changes to Ivan's 3D Engine are documented here.
Versions follow [Semantic Versioning](https://semver.org/).

---

## [1.7.0] - 2026-07-07 — gizmo precision + panel basics

Implements B1 + B2(field) + C1 + C2 from the v1.7 editor UX scoping report, in Ivan's stated
priority order (gizmo/selection feel first, then panel/layout). 113/113 unit tests green
throughout; all 4 `tests/smoke_test_harness.py` scenarios pass after every step; each step
additionally verified via headless in-process simulation (real `LevelEditor` instance, no
mouse/window needed) since this environment has no way to drive the Panda3D GUI directly.

### Added
- **Rotation numeric field (B2)** — Inspector grid extended from Pos/Scale (2 rows) to
  Pos/Scale/Rot (3 rows); `rot_x/y/z` reuse `_apply_inspector_value`/`ChangePropertyCommand`
  unchanged. Rotation previously round-tripped through `level.json` with no editor UI to set
  it — confirmed via full git history search (including the pre-v1.6-split monolith) that no
  rotation field or slider ever existed; this was a real gap, not a regression.
- **Collapsible panels (C1)** — Hierarchy/inspector/browser each toggle via `Ctrl+H`/`Ctrl+I`/
  `Ctrl+B` or a `[H]`/`[I]`/`[B]` chevron button. `_apply_layout` reclaims the freed horizontal
  space (collapsed panels shrink to a thin chevron-width strip); `_is_over_panel`/
  `_is_over_browser` return `False` for a disabled panel so collapsed chrome never intercepts
  viewport clicks. Browser vertical space is hidden but not reclaimed on collapse (full
  height-reclaim cascades through the card-grid/scroll-indicator row math extensively — closer
  to C3 scope; deferred).
- **Layout presets (C2)** — `Ctrl+Alt+1-5` saves current panel visibility to a slot, `Alt+1-5`
  recalls it; mirrors the existing camera-bookmark (`_bookmarks`/`_save_prefs`) pattern exactly,
  persisted in `editor_prefs.json` under a new `layout_presets` key. Alt-modified specifically
  because `Ctrl+1-5` and bare `1-5` were already fully claimed by camera bookmarks.

### Fixed
- **Gizmo drag was speed-dependent, not cursor-pinned (B1)** — `handle_drag()` computed
  `mouse_vel.dot(axis_screen) * 200.0` each frame, so a fast flick moved an entity further than
  a slow drag across the same screen distance — the primary source of the editor's "feels
  imprecise" complaint. Replaced with plane-projection: on grab, cast the cursor ray onto a
  camera-facing plane containing the drag axis, then each frame project the new cursor hit onto
  the axis line and move by the delta from the grabbed offset. Verified headless that a single
  fast jump and a slow multi-step drag to the same cursor position now land at identical world
  positions. Falls back to the old velocity model when the drag axis is nearly parallel to the
  view direction (`_AXIS_SCREEN_LEN_MIN` threshold) — plane-projection is numerically unstable
  there, a standard gizmo edge case.
- **No hover feedback on gizmo handles (B1)** — tips now brighten to white on
  `mouse.hovered_entity` before a drag is committed to, using the pre-existing hook (line
  meshes already report as `hovered_entity` — see Gotchas).

### Added — asset integration (2026-07-07 asset audit)
- **Gun viewmodels** — Pistol/Shotgun/Rifle now render a real OBJ+MTL model (Ultimate Gun Pack)
  instead of the placeholder cube, with per-subclass `view_model`/`view_scale`/`view_position`/
  `view_rotation` tuning on `Weapon`. Fixed a latent bug the refactor exposed: `original_pos` was
  building from the constructor's now-optional `position` param instead of the resolved
  `self.position`.
- **Crosshair** — `PlayerHUD.crosshair` points at a real PNG (Kenney crosshair pack) instead of
  the plain-circle placeholder. Also fixed a pre-existing issue where `FirstPersonController`'s
  built-in pink diamond cursor sat on top of the crosshair (Ursina calls `on_enable()` after
  `Player.__init__` finishes, re-enabling anything disabled mid-`__init__`).
- **Shot SFX** — `blaster.ogg`/`blaster_repeater.ogg` play on weapon fire. **Only half of the
  open v1.3 remainder item is closed** — no ambient/music track is wired; that still needs a
  separate CC0 source.
- **Blob shadows** — Player and enemies now cast a simple ground-tracking shadow quad
  (`Scripts/ground_shadow.py`).
- Font (Lilita One) and the full Kenney UI icon/sound packs imported for future menu work, not
  yet wired into any screen.

### Deferred (parked, not silently dropped)
- **B2-ring** — a rotation *ring* gizmo (vs. the numeric field shipped here). ~1–1.5 days,
  medium risk (new geometry, new drag mode, thin-target picking).
- **C3** — resizable panels (drag a divider). ~1–1.5 days per edge; needed before browser
  vertical-space reclaim on collapse can be done properly.
- **C4/C5** — movable-dock panels and true Unity-style drag-to-dock. C4 is 2–3 days; C5 is a
  multi-week framework-scale project and, per the scoping report, likely not worth it for a
  solo game editor. See `obsidian-mind-main/work/active/v1.7-editor-ux-scoping.md` for the full
  cost/risk breakdown.

---

## [1.6.1] - 2026-07-07 — post-refactor fix backlog

Closes 9 of the 16 items from the 2026-07-06 whole-project audit (items 2/3/6/8/9/11/12/13/14/16;
item 7 closed as a doc-structure side effect of [1.6.0]; items 1/4/5/10/15 already shipped).
113/113 unit tests green throughout; all 4 `tests/smoke_test_harness.py` scenarios
(`load_and_close`, `win_then_r`, `gameover_then_r`, `editor_f5_roundtrip`) pass after every step.

### Fixed
- **Editor F5 entity leak** (item 2) — exiting play-in-editor (F5/Esc) now explicitly tears down
  live `TriggerZone`/`AmmoPickup` entities and parked bullet-pool entities, which
  `_clear_gameplay_entities()`'s name-only sweep never covered. A deeper follow-on leak surfaced
  during verification: Ursina's `destroy()` never cascades to `parent=` children (only
  `loose_children`), so `HealthBar`'s `bg`/`bar` quads and `Player`'s debug collider lines leaked
  as orphaned NodePaths on every teardown — masked on the normal game path only because
  `main_menu()` runs a scene-wide nuclear sweep afterward. `HealthBar.on_destroy()` now
  explicitly destroys its own children; `_clear_gameplay_entities()` explicitly destroys
  `Player.debug_lines`. An F5 round-trip with a live enemy, fired shots, and a non-default
  texture now leaves only 2 residual entities (unrelated editor-UI widget refresh, tracked
  separately).
- **`start_game()` duplicate teardown** (item 3) — routed through `game.return_to_menu()` instead
  of an inline copy of the player-cleanup logic.
- **Editor `window.color` clamp** (item 6) — divided by 255 to match the 0–1 float convention
  `color.rgb()` expects, same fix already applied to the main game window.
- **Dead `window.on_resize` assignment** (item 8) — removed; Ursina never calls it.
- **`save_level()` unguarded write** (item 11) — wrapped in the same try/except-and-log guard
  `_save_prefs()` already used, so a write failure (disk full, permissions) no longer unwinds
  Ctrl+S silently.
- **Eternal debug collider lines** (item 12) — `Player`'s 12 wireframe-box debug lines dropped
  `eternal=True`, which was blocking `destroy()` outright; the remaining "children don't cascade"
  gap was closed as part of the F5-leak follow-up above.
- **Bare-string texture loads** (item 14) — the four remaining sites that assigned
  `entry['texture']` straight to `Entity(texture=...)` now route through
  `asset_resolve.resolve_texture`, matching the existing `resolve_model` usage at each site.

### Changed
- **Dev tooling relocated** (item 16) — `auto_fix.sh`, `auto_fix_loop.py`, and
  `v1_3_step_runner.py` moved from `Scripts/` to `tools/`; internal path references updated.
- `main.py`'s `_resolve_model` import switched from the `undo_redo.py` backward-compat shim to
  the canonical `Scripts.asset_resolve` module (touched the same import line as the item 14 fix).

### Documented
- Air-shoot (shooting while not `grounded`) confirmed intentional — standard FPS jump-shooting,
  not a gating bug (item 13).
- `weapon.py`'s viewmodel header comment corrected to match the shipped depth-test/write-off
  behaviour; it previously claimed the second camera "clears the depth buffer first," which was
  the first approach tried and abandoned (it blanked the world on macOS GL 2.1).
- CLAUDE.md tech-debt drift (item 7) closed as a side effect of [1.6.0]'s doc restructure, which
  removed the parallel Known Tech Debt table in favor of a single backlog file.

---

## [1.6.0] - 2026-07-07 — level editor refactor

Splits the 4,169-line `Scripts/level_editor.py` monolith into seven focused modules with zero
behaviour change — the milestone planned since 2026-06-26 for human navigability and agent
productivity. Every step verified in-app through `tests/smoke_test_harness.py` driving the real
Ursina dispatch (163 step-specific checks across steps 2–6 plus the 4 standing scenarios after
every step); 113/113 unit tests green throughout.

### Changed
- **`Scripts/level_editor.py`** (4,169 → 103 lines) is now only the standalone entry point:
  shader patch before `Ursina()` (HC10 order preserved), F5 hot-reloader disable, window setup,
  ground plane, hint legend — all inside `_launch()`, with a two-line `__main__`
  (`app = _launch(); app.run()`). The harness contract (module-level `app`, final blocking
  `app.run()`) is unchanged.
- **`Scripts/editor_core.py`** (new, 1,346 lines) — the `LevelEditor` class: toolbar + tool
  modes, selection/box-select, grid snap, trigger/pickup placeholder factories, level-data
  serialisation + save/load, prefs/bookmarks, resize layout, stats strip, and the
  `input()`/`update()` dispatchers (v1.2.4 priority chain preserved) routing to five
  collaborator classes, each reaching shared state through an editor back-reference:
  - **`Scripts/editor_hierarchy.py`** (282) — `HierarchyPanel`: left-hand list, search filter,
    collapsible sections, swatches, scroll bar, highlight.
  - **`Scripts/editor_gizmo.py`** (194) — `GizmoController`: X/Y/Z handles, drag translation +
    `MoveEntityCommand` push, cursor-ray pick.
  - **`Scripts/editor_browser.py`** (1,032) — `AssetBrowser`: tabbed bottom band, texture/model
    picker overlays, texture hot-reload, import pipeline + native file picker, drag-ghost
    placement, status-notice toast.
  - **`Scripts/editor_inspector.py`** (1,201) — `InspectorPanel`: field grid, texture swatch,
    model/door fields, behaviour-tree / trigger-action / pickup-config sections.
  - **`Scripts/editor_playmode.py`** (241) — `PlayModeController`: F5 snapshot → spawn →
    teardown → restore round-trip, editor-camera save/restore. `_exit_play_mode` invariants
    (state set before teardown try, `except ImportError` only) preserved verbatim.
- **`Scripts/compat.py`** (new, 167) — the GLSL 1.20 shader patch, deduplicated out of both
  `main.py` and the editor `__main__` (closes the long-standing tech-debt row).
- **`Scripts/asset_resolve.py`** (new, 77) — `resolve_texture`/`resolve_model` relocated out of
  `undo_redo.py` (they bridge the framework-free `asset_registry` to Ursina loaders and were
  never command-specific); `undo_redo` imports them under the old private names, command bodies
  unchanged.
- **`Scripts/session_logger.py`** — `get_editor_logger()` accessor (same cached pattern as
  `get_game_logger`) so all editor modules share one log file per run.

### Fixed
- **Gizmo pick unguarded `e.name` sweep** (moved into `editor_gizmo.py`) — the pick's
  `scene.entities` sweep read `.name` on entities `destroy()`ed earlier the same frame
  (emptied NodePaths still in the list → C++ assertion `except` cannot catch, HC13 family).
  The sweep now short-circuits on `e.is_empty()` first; repro survives in-app.
- **Inspector/browser integration miss** caught by the in-app run during step 5 — the picker
  build read `_INSP_LABEL_WS` off the editor after the constant moved to `InspectorPanel`.

### Verified (step 9 wrap-up audit)
- Import graph of the editor modules is acyclic — collaborators never import `editor_core`;
  back-references are runtime-only.
- Hard constraints spot-checked present post-split: HC10 (patch before `Ursina()` in both entry
  points), HC12 (Move tool never places), HC13 (`is_empty` guards), HC16
  (`update_aspect_ratio` wrap), HC17 (no empty-Text writes introduced).
- Two full F5/toggle play-in-editor round-trips with real state-restore assertions (24 checks:
  entity counts, `game.state`, player spawn/clear, UI hide/show, camera disable/re-enable +
  position restore, snapshot lifecycle).
- Test docstrings that pointed at monolith line numbers updated to the new module layout.

---

## [1.5.1] - 2026-07-06 — pre-v1.6 closure pass

Closes both v1.5 tails (§5 combined regression; door/pickup content) before the v1.6 editor
split begins, per the 2026-07-06 audit's sequencing decision. All steps in-app verified through
`tests/smoke_test_harness.py` driving the real Ursina dispatch; 113/113 unit tests green.

### Added
- **`cautious` behaviour preset** (`Scripts/behaviour_tree_factory.py`) — the first shipping
  preset to use a decorator in the live frame loop. Shape:
  `Selector([Sequence([ChaseNode(tuned), Cooldown(AttackNode(tuned), 3.0)]), Idle])` — same
  engagement envelope as `default` (tuned constants 100/30/1.0) with fire cadence rate-limited
  to one shot per 3s by `Cooldown`. Live smoke check: 3 fire attempts in 7.5s vs a default
  enemy's 8; zero attempts once the player left detection range (child-0 re-evaluation holds).
- **Checkpoint consumer** (`Scripts/trigger_system.py`) — `game.respawn_point` (store-only since
  v1.5) is now read by `kill_plane`: once a `checkpoint` trigger has fired, falling into a kill
  plane teleports the player back to the recorded point at a cost of `KILL_PLANE_RESPAWN_COST`
  (25 HP) instead of ending the game. Pre-checkpoint falls stay terminal. Health cost chosen over
  brief invulnerability (no timer state in the damage paths; repeated falls still end the run —
  the fourth fall from full health dies at the checkpoint through the player controller's single
  death authority). Smoke-verified: terminal pre-checkpoint, 100→75 respawn, 4-fall drain to
  GAME OVER.
- **Curated level `levels/v1.json`**, shipped as the live `level.json` — the first level to
  exercise doors, pickups, and non-default presets together: armory room gated by an
  `open_door` pressure plate (`door_name: armory_door`) holding a `flee_when_low` enemy and the
  Shotgun weapon/ammo pickups; NE patrol courtyard (placed >60u from spawn so the patrol branch
  is actually observable) with two `patrol_then_attack` enemies on real multi-waypoint routes
  guarding the Rifle pickups; checkpoint at a walled corridor mouth; lava pit (visual block +
  `kill_plane` trigger) crossed on rising platforms; walled arena with an `aggressive` enemy and
  the `win_condition` trigger. Intro enemy uses the new `cautious` preset (`default` already had
  its live run in the v1.5 level). World-edge kill plane retained at y=-25.

### Changed
- **Weapon pre-grant removed** (`Scripts/player_controller.py`) — the player now starts with only
  the slot-0 Pistol; Shotgun/Rifle are granted by the curated level's weapon pickups. Empty-slot
  switching verified as a no-op; pickup-grant path verified live.

### Fixed
- **Smoke harness asset resolution** (`tests/smoke_test_harness.py`) — Ursina derives
  `application.asset_folder` from `sys.argv[0]`, so `python -m tests.smoke_test_harness` resolved
  assets against `tests/` and every scenario crashed at launch in Ursina's font setter — broken
  since v1.5 added the Inter-Bold font. The harness now points `argv[0]` at the launched module
  before the first ursina import. All four scenarios pass again.

### Verified (closes the v1.5 §5 combined regression)
- Full curated-level playthrough via the harness: 5 enemies spawn with the intended trees;
  patrol enemies walked ~20u of route each in 4s and stayed in the courtyard; low-HP flee enemy
  opened distance; door slid fully open; all 4 pickups granted/topped-up and consumed; checkpoint
  recorded; lava respawn at exactly 75 HP; aggressive enemy contained in the arena; win trigger
  fired over the new HUD; R returned to menu cleanly.
- §5 combos: weapon pickup inside a trigger volume firing on the same entry frame; kill-plane
  fall mid weapon-switch animation; pause (Esc) mid-reload with clean completion after resume;
  programmatic window resize (1280→900→1280) mid-firefight; WIN and GAME OVER over the new HUD
  followed by R-teardown (`win_then_r` / `gameover_then_r` scenarios).
- Editor: `editor_f5_roundtrip` passes against the new level (load → play-in-editor → exit).
- Remaining human-only checks (not blockers, recommended before itch.io): game-feel pass on the
  platform jumps and enemy difficulty, OS-drag window resize (only the programmatic path is
  covered), and visual readability of the lava/plate/pedestal markers.

## [1.5] - 2026-06-30 (in progress)

### Fixed
- FPS gun viewmodel clipped through walls and level blocks when the player stood flush against
  them. Root cause: `Weapon` is `parent=camera` at local `position=(0.5,-0.5,1)`, so its far end
  sits ~1.5u ahead of the camera and physically intersects world geometry in world space — a
  depth-order problem the geometry causes, not a draw-order one. Depth-state-only fixes
  (`always_on_top`, `setBin('fixed', 100)` + `setDepthTest(False)`) could NOT fix it because the
  gun shared the world's render pass: other world geometry in the same pass still painted over it.
  Fix is the standard Unity/Unreal dual-camera viewmodel pattern (`Scripts/weapon.py`):
  `VIEWMODEL_MASK = BitMask32.bit(7)`; `_setup_viewmodel_camera()` (module-level idempotent
  singleton, called from `Weapon.__init__`) clears bit 7 from the main camera's mask so it renders
  everything except the gun, then adds a second Panda3D camera (camera-mask = bit 7 only, reusing
  `base.camLens`, parented to `base.cam` so fov/aspect/transform track the main view) on its own
  display region at sort 15 (after world=0 and render2d=10, before UI=20). The gun is hidden from
  all masks then shown only to `VIEWMODEL_MASK`, with `setDepthTest(False)`/`setDepthWrite(False)`
  — because the viewmodel pass runs after the whole world pass, an always-pass gun lands on top
  regardless of wall proximity. The display region's clear-depth is intentionally left OFF: on
  macOS GL 2.1 enabling it blanked the already-rendered world (verified via headless Panda3D
  render tests). Verified in the live game: gun visible, no clipping. See `brain/Gotchas.md`.

### Added
- **System A — trigger/zone system** (Steps 1–6, committed earlier in this branch). `Layers.TRIGGER`
  bitmask; `TriggerZone(AliveEntity)` with `kill_plane` / `checkpoint` / `open_door` / `win_condition`
  actions; both the horizontal swept-move ray and the vertical ground/ceiling ray skip `Layers.TRIGGER`
  so a trigger volume never blocks movement or fall detection. Level-editor trigger placement
  (placeholder, inspector action editor, hierarchy section, save/load).
- **System B — weapon inventory** (Steps 7–13). `WeaponInventory` (`Scripts/weapon_inventory.py`)
  with 3 slots, `switch_to()`/`next_weapon()`/`prev_weapon()`, and a 0.2s slide animation; replaces
  the single `self.weapon = Weapon(self)` in `Player.__init__`. `Weapon` is now a base class
  (viewmodel + `ammo`/`reload()` scaffold); `Pistol` (dmg 25, cd 0.15s, infinite ammo), `Shotgun`
  (5 pellets ×15 dmg, ±5° spread, cd 0.8s, 8 ammo), `Rifle` (dmg 40, cd 0.08s, 24 ammo), each with
  ammo decrement, dry-click-on-empty, and reload. `Layers.PICKUP` + `AmmoPickup(AliveEntity)`
  (weapon-grant and ammo-topup variants); level-editor pickup placement mirroring the trigger pattern.
  Ammo HUD counter (`PlayerHUD.ammo_text`, polled from the global `update()`, `INF`/`RELOADING`/`n/max`).
  `POOL_SIZE_PLAYER` left at 30 — the spec's suggested 30→50 bump was not applied; measured pool
  usage peaked at 25/30 under sustained real-play fire (see `v1.5-gameplay-systems.md` step notes).
- FPS gun 3D model assets (`3d models/gun.obj`, `gun.mtl`).

### Changed
- **HUD / menu redesign.** Shared UI constants extracted to `Scripts/ui_theme.py` (palette + spacing)
  and reused across `PlayerHUD`, `PauseMenu`, `EndScreen`, and `health_bar.py`. HUD/menu positions
  recomputed from `window.aspect_ratio` for resize-awareness. `Inter-Bold` font added
  (`assets/fonts/Inter-Bold.ttf`).

> **Note:** System B and the HUD redesign arrived as an integrated working-tree snapshot (parallel
> worktrees, never committed to their own branches) and were committed together. Per-step commit
> granularity for Steps 7–13 was not recoverable. This entry documents what landed; a wrap-up audit
> verifies it against the code before v1.5 ships.

## [1.2.6] - 2026-06-22 (wip, see [1.2.7])

### Fixed
- Editor startup crash: `IndexError: list index out of range` in `Text.align()` at
  `ursina/text.py:408` (`Scripts/level_editor.py` `_build_asset_browser` scroll arrows,
  `_update_inspector`, `_show_status_notice`). Root cause: `Text.start_tag`/`end_tag` default to
  `'<'`/`'>'` with `use_tags=True`, so a literal `text='<'`/`'>'` parses as an empty tag pair with
  zero content lines (same failure mode as `text=''`) → `Text.align()` indexes `linewidths[-1]`
  into an empty list. Fixed scroll arrows with `use_tags=False` in the constructor; fixed two
  latent empty-string sites in the same sweep (`_update_inspector` deselected fields now use
  `' '`; `_show_status_notice` passes the real text directly instead of `text=''` + mutation).
  See Hard Constraint 17 in CLAUDE.md.
- Window resize broke 3D viewport + toolbar overlap. Two root causes: (A) `window.on_resize` is
  never invoked by Ursina (dead code) — wrapped `window.update_aspect_ratio` instead, calling
  `_apply_layout()` after Ursina's own work, plus an explicit
  `camera.perspective_lens.set_aspect_ratio(aspect)` refresh. (B) toolbar button widths were
  fixed constants sized for 16:9 — renamed to `_TOOLBAR_BTN_W_BASE`, scaled by
  `min(1, aspect / _TOOLBAR_REF_ASPECT)` in `_apply_layout()`.
- Texture thumbnail cards failed to render (raw path strings fail silently in Ursina's texture
  setter) — load via `Texture(Path(path))`.
- Crosshair visibility not restored on non-Esc pause paths — `PlayerHUD.show()`/`hide()` now
  called by `PauseMenu` on every path.

### Removed
- Standalone placement tray (replaced by built-in model cards prepended to the Models tab via
  `BUILTIN_MODELS`/`_browser_card_assets` — same drag-to-place flow).
- `swept_cast()` dead code from `collision_system.py` (never had any callers).
- `_pooled` dead param from `PlayerBullet`/`EnemyBullet.__init__` and `BulletPool.acquire`.

### Changed
- `_exit_play_mode` exception handling narrowed from bare `except Exception` to `except ImportError`.

---

## [Unreleased housekeeping] - 2026-05-22

Batch of small fixes/cleanups from a single audit-driven session, landed between 1.2.2 and 1.2.3.

### Fixed
- Ghost preview entity flickered every frame during drag — `_update_ghost()` now only hides on a
  definitive miss; stale/invalid hits leave the ghost at its last good position.
- `_exit_play_mode` set `game.state` after the try/except — not set if teardown raised. Moved
  `game.state = Game.MAIN_MENU` before the try block.
- `PlaceEntityCommand.execute()` / `DeleteEntityCommand.undo()` omitted `origin_y=-0.5` for enemy
  redo — extracted `_restore_entity()` helper in `undo_redo.py` that sets it.
- `game.enemies` (and other refs) not reset on teardown exception — `return_to_menu()` `finally`
  block now resets all ref attrs.
- `game.win_screen`/`game.game_over_screen` not tracked for teardown — initialized to `None` in
  `Game.__init__`, cleared in the `return_to_menu()` finally block.

### Changed
- `HealthBar` — removed `eternal=True` from all sub-entities; `on_destroy()` now explicitly
  destroys `camera.ui` text; added `_registry` class list for O(registry) iteration instead of a
  per-frame `isinstance(e, HealthBar)` scan over `scene.entities`.
- `player_controller.py`: extracted inline magic-number swept-test offsets to module-level
  `SWEPT_OFFSETS` constant; removed duplicate per-frame `health_bar.value` assignment; added
  `on_destroy()` calling `collision_manager.remove(self)`; removed dead
  `generate_raycast_points`/`draw_raycast_visuals` machinery.
- `weapon.py`: extracted hardcoded pool sizes to `POOL_SIZE_PLAYER = 30`, `POOL_SIZE_ENEMY = 60`;
  added `BulletPool.active_count()`.
- `level_editor.py`: extracted inline snap tuple to module-level `EDITOR_GRID_SNAPS = (1.0, 0.5,
  0.25, None)`; removed dead unused r/g/b_val variables in `_build_level_data()`.
- `main.py`: removed `time_scale=1` delayed-invoke race on rapid re-pause (immediate assignment
  is sufficient).
- `collision_system.py`: added `__all__`, docstrings, audit header.

---

## [1.2.5] - 2026-06-20

WIN/GAME_OVER → R return-to-menu crash fix. Root-caused against the installed Ursina 8.3.0 /
Panda3D source and a faithful repro (the real `__main__` wiring driven by `app.input('r',
is_raw=True)` + `app.step()`), not from memory. Prior sessions framed this as a Python exception
and added `except Exception` guards that could never work — the failure is a **C++ NodePath
assertion**, which Python `except` cannot catch.

### Fixed
- **Pressing R on the WIN or GAME_OVER screen flooded the console with
  `Assertion failed: !is_empty() at line 2102 of nodePath.I` and never returned to the menu**
  (`main.py` `_clear_gameplay_entities` / `main_menu` / `load_level`). `nodePath.I:2102` is
  Panda3D's `getName()`. Ursina's `destroy()` empties an entity's NodePath **synchronously**
  (`removeNode()`) but defers removal from `scene.entities` to the *next* frame's
  `Ursina._update()` flush. The teardown runs entirely inside one synchronous R-dispatch (no
  frame boundary), so its `scene.entities[:]` sweeps still contained the entities it had just
  destroyed — and reading `e.name` on those empty NodePaths asserted. A diagnostic proved
  **7 of 150** entities in `scene.entities` had empty NodePaths at the sweep point. Fixed with a
  NodePath-level guard `_is_live(e)` (`not e.is_empty()`) applied **before** any `.name` read in
  all three sweep loops — replacing the ineffective `try/except Exception` wrappers.
- **`Player.input('r')` ran on the just-destroyed player, raising
  `Exception: entity has been destroyed by: _clear_gameplay_entities`**
  (`Scripts/player_controller.py` `Player.input`). Surfaced once the assertion flood was gone.
  Ursina dispatches `__main__.input('r')` first (which tears down the player), then continues the
  **same** input call into the per-entity loop and calls `Player.input('r')` →
  `self.position = (0, 2, 0)` on the destroyed NodePath. Fixed by early-returning from
  `Player.input()` when `game.state != Game.PLAYING`; after R the state is already MAIN_MENU, so
  the player's R-reset and the global R-to-menu handlers are now mutually exclusive.

### Changed
- **Teardown is now logged step-by-step** (`main.py`, `Scripts/session_logger.py`). Each step of
  `_clear_gameplay_entities()` / `main_menu()` writes `logger.log('INFO', 'teardown: …')` so a
  future crash shows exactly how far teardown got. `SessionLogger` gained an `open_message`
  param and a shared `get_game_logger()` singleton — `main.py` is imported as both `__main__`
  and `main` (via `game.py`'s `from main import`), which would otherwise create two log files
  per run; the singleton keeps the whole session in one log.

### Verified
- WIN → R, GAME_OVER → R, and pause → Main Menu all complete with **0 Panda3D assertions and
  0 Python exceptions**, land on `state=MAIN_MENU`, and a brand-new game restarts with all
  enemies present. Session log shows the full ordered INFO teardown sequence for each path.

---

## [1.2.4] - 2026-06-10

Level-editor targeted fix pass. Phase 2 fixes 1A (gizmo depth render), 2 (2× handles),
and 3 (Move/Place tool) had already landed in the prior "massive refactoring" commit and were
verified intact. This release fixes the four genuinely-broken items found in deep review, each
diagnosed against the installed Ursina 8.3.0 source rather than from memory.

### Fixed
- **Inspector field Enter applied nothing (FIX 4 — third attempt, now root-caused)**
  (`Scripts/level_editor.py` `_build_inspector` / `_apply_inspector_value`). Two real causes,
  verified against `ursina/prefabs/input_field.py`: (1) `InputField.submit_on` **defaults to `[]`**
  and was never set, so `on_submit` could never fire on Enter; (2) Ursina calls `self.on_submit()`
  with **no arguments**, but the prior callback was `lambda val, k=key:` — wrong arity, so it would
  have raised `TypeError` the instant it ever fired. Fixed by setting `field.submit_on = ['enter']`
  and using a no-arg `lambda k=key, f=field: self._apply_inspector_value(k, f.text)` that reads the
  field text at call time. HP now casts to `int`. Verified end-to-end via the real
  `InputField.input('enter')` path: the value applies and a `ChangePropertyCommand` lands on the
  undo stack; clicking a field no longer clears selection (existing `_is_over_panel` guard).
- **Gizmo handle could not be grabbed (FIX 1B — persistent bug, real cause found)**
  (`Scripts/level_editor.py` `input()` + new `_cursor_ray()`). The gizmo pick raycast used
  `camera.world_position, camera.forward` — a **screen-centre** ray — but the editor cursor is
  free, so it never tested where the user actually clicked. Replaced with a world-space ray cast
  through the **mouse cursor** (`camera.lens.extrude`, mirroring Ursina's own `mouse.update`
  picker). Ignoring every non-`editor_gizmo_tip` entity lets the handle win even when a block
  overlaps it on screen — verified: the tip ray hits the handle while a full ray hits the block
  in front.
- **Editor asset colours rendered white and corrupted `level.json` on save**
  (`Scripts/level_editor.py` `ASSETS`). `color.rgb()` is an alias for `rgba()` and builds
  `Color(r,g,b,a)` with **no /255** (verified in `ursina/color.py`). The `ASSETS` table used
  0–255 tuples, so tray tiles, drag ghosts, and placed blocks clamped to white, and saving a
  tray-placed block wrote colour components > 1.0 into `level.json`. Converted the table to 0–1
  floats (same unit as `level.json`). One-off heal divided the 3 remaining inflated `level.json`
  entries (the Cube/Metal/Wood asset colours) back into range; file now has 0 components > 1.0.
- **Move/Place buttons and spawn marker stayed visible during play-in-editor** (`Scripts/level_editor.py`
  `_set_editor_ui_visible`) — added `_move_button`, `_place_button`, and `_spawn_marker` to the
  hide list so play mode hides all editor chrome; verified re-enabled on exit.

### Notes
- Deferred items (cosmetic 0–255 UI-chrome colours, unused `position_valid()`, `_load_prefs`
  coupling, the `↖` U+2196 glyph missing from the default font) are logged in
  `docs/audit_v1.2.3.md` under **Deferred (post-1.2.3)**.

---

## [1.2.3] - 2026-05-22

### Added
- **WIN / GAME_OVER states wired** (`Scripts/game.py`, `main.py`, `Scripts/player_controller.py`).
  `Game.trigger_win()` and `Game.trigger_game_over()` are idempotent — they only fire from
  `PLAYING`, set the state, freeze `application.time_scale`, surface the mouse cursor, hide the
  HUD, and build an `EndScreen` overlay. WIN is detected in the global `update()` via the new
  `CollisionManager.count_layer(Layers.ENEMY) == 0` check (does not allocate a list and reads
  from `_tracked`, which `AliveEntity.die()` prunes — `game.enemies` still holds dead refs so
  `len(game.enemies)` would lie). GAME_OVER fires from `Player.update` when `health <= 0`,
  replacing the silent teleport-to-origin. Pressing `R` on either end screen calls
  `game.return_to_menu()` then `main_menu()`. Esc during WIN/GAME_OVER is a no-op (the existing
  guard only branches on PLAYING/PAUSED). Both screens are tracked as `game.win_screen` /
  `game.game_over_screen` and torn down by `_clear_gameplay_entities()`.
- **`EndScreen(title)`** (`main.py`) — single `Entity(parent=camera.ui)` with a semi-transparent
  black quad background and two `Text` children (title + "Press R to return to menu" hint). All
  children are parented to `self` so `destroy(self)` cascades — no manual sub-entity teardown.
- **`CollisionManager.count_layer(layer)`** (`Scripts/collision_system.py`) — allocation-free
  alternative to `len(query_layer(...))`.

### Fixed
- **`level.json` colour-channel inflation (65025.0 = 255²)** — save path wrote 0–1 floats; all
  four loaders multiplied by 255 on read, squaring the value across each save/load cycle until
  all blocks rendered white (Ursina clamped). Removed `[int(c * 255) for c in …]` from every
  loader (`main.py:load_level`, `Scripts/level_editor.py:load_existing_level`, `_restore_editor_level`,
  `_spawn_gameplay_from_snapshot`); they now spread the 0–1 list directly into `color.rgb()`,
  which accepts floats. A one-off `level.json` migration deflated 79 entries (any component
  > 1.0 divided by 255 until in range). Verified: post-migration sample colour is
  `(0.313725, 0.470588, 0.784314)` and no component exceeds 1.0.
- **Block `scale` dropped in play-in-editor** (`Scripts/level_editor.py:_spawn_gameplay_from_snapshot`).
  The `Entity(…)` ctor was missing `scale=`, so every block reverted to 1×1×1 in F5 play mode
  even though the snapshot carried the value. Added `scale=tuple(entry.get('scale', [1, 1, 1]))`
  to match the rotation pattern.

### Changed
- **Single source of truth for level loading**: extracted `Scripts/level_io.py::load_level_data(path_or_list)`.
  All four loader sites (`load_level`, `load_existing_level`, `_restore_editor_level`,
  `_spawn_gameplay_from_snapshot`) now call it. Defaults (scale `[1,1,1]`, rotation `[0,0,0]`,
  colour `[1,1,1]`, hp `100`, enemy_type `'default'`) live in one place. Entity construction
  stays at call sites — each builds a different shape (placeholder vs. editor entity vs. real
  `Enemy`/`Player`).

### Removed
- **`Scripts/color.py`** — dead module (confirmed unused via `grep -r "from Scripts.color"`)
  with HSV values passed to `color.rgb()` (e.g. `red = color.rgb(0, 1, 1)` actually renders
  cyan). Removing it eliminates a class of future foot-gun without losing any consumer. Use
  `ursina.color` directly.

---

## [1.2.2] - 2026-05-20

### Fixed
- **Level editor placement broken** — left-clicking any surface now places a block/enemy as expected.
  Previously, clicking an existing block always triggered selection instead of using the block as a
  placement surface, making it impossible to build adjacent to existing geometry.
  Root causes: (1) selection logic short-circuited before the placement branch when
  `hovered_entity` was in `self.blocks`/`self.enemies`; (2) toolbar `Button` widgets (which have
  colliders) could intercept clicks and trigger phantom placements.

- **Gizmo axis drag moved entities in the wrong direction when the camera was on certain sides of
  the selected object** — drag delta was computed in screen space without accounting for camera
  orientation, so the sign flipped when the camera orbited behind the object. Fixed by projecting
  each world axis into screen space each frame (`camera.getRelativePoint`) and dotting mouse
  velocity against that projection to determine signed magnitude.

### Added
- **Bottom asset tray** (`Scripts/level_editor.py`) — fixed panel at the bottom of the screen
  (dark background, full width, height ~0.12 units) with five hardcoded asset tiles:
  Cube (blue), Stone (grey), Metal (light grey), Wood (brown), Enemy (red).
  Tiles highlight on hover; mouse-wheel scrolls the tray horizontally.
- **Drag-and-drop placement** — click-and-hold a tile to pick up an asset; a semi-transparent
  ghost entity (alpha 0.5) follows the mouse raycast hit point, snapped to the current grid
  setting. Releasing the mouse over the viewport places the entity and pushes a
  `PlaceEntityCommand` (fully undoable with Ctrl+Z). Releasing over the tray or pressing Esc
  cancels the drag without placing. Enemy tiles spawn at `(1.5, 3, 1.5)` scale with
  `origin_y = −0.5` consistent with the existing enemy placeholder convention.

### Removed
- **Mode: Block / Enemy toggle button** — replaced by the asset tray. Asset type (block vs.
  enemy) is now selected by choosing the appropriate tile rather than a global mode switch.

### Changed
- **Left-click = place, Shift+click = select** — UX aligned with standard level-editor convention.
  Single left-click on any collidable surface (ground, wall, or existing block face) places a new
  entity. Shift+click adds/removes an entity from the selection. Right-drag box-select unchanged.
- Updated in-editor help overlay and `CLAUDE.md` to reflect new click bindings.
- **Karpathy guidelines skill installed** — `multica-ai/andrej-karpathy-skills` placed at
  `.claude/skills/karpathy-guidelines/SKILL.md` and imported via `@` directive at the top of
  `CLAUDE.md`. Guidelines (think before coding, simplicity first, surgical changes,
  goal-driven execution) are now active every Claude Code session automatically.
- **`mouse.direction` replaced with derived ray direction** (`Scripts/level_editor.py`
  `_update_ghost()`) — `mouse.direction` was removed in Ursina 8.3.0, causing an
  `AttributeError` every frame while dragging an asset from the tray. Fixed by deriving the
  ray direction from `camera.forward`, `camera.right`, `camera.up`, and `mouse.x`/`mouse.y`
  screen coordinates. Ghost placement and snap logic unchanged.

### Fixed (continued)
- **Mouse wheel over hierarchy / inspector panels zoomed EditorCamera** — scrolling while the
  cursor was over either side panel caused the camera to zoom instead of (or in addition to)
  the panel scrolling. Fixed by checking `_is_over_panel()` in `input()` before the event
  reaches `EditorCamera` and returning early to suppress the zoom.
- **Left-click on empty space while entities were selected placed a new block** — the placement
  branch fired on any collidable surface regardless of selection state, so deselecting by
  clicking empty space inadvertently created geometry. Fixed with a priority chain:
  clicking a tracked entity selects it; clicking empty space with a non-empty selection
  deselects only (no placement, no undo entry); placement only occurs when nothing is selected
  and the cursor is over a surface.
- **Block placement triggered during active gizmo drag or box-select** — held-key repeat events
  could fire `left mouse down` while a drag was in progress, creating phantom blocks. Fixed by
  guarding the entire `left mouse down` handler when `_gizmo_drag_axis is not None` or
  `_box_selecting` is `True`.

### Improved
- **Hierarchy panel scroll** — entity list now supports mouse-wheel scrolling when the cursor
  hovers the hierarchy panel. Shows 14 rows at a time; a proportional thumb on the right edge
  indicates scroll position. Hidden when all entities fit without scrolling.
- **Inspector even spacing** — property rows are distributed evenly across the full panel height
  instead of bunching at the top. A subtle separator line divides the transform group
  (Pos X/Y/Z, Rot Y, Scale X/Y/Z) from the entity group (HP).
- **GLSL 1.20 shader compatibility for macOS / OpenGL 2.1** — Ursina 8.3.0 ships
  `unlit_shader`, `unlit_with_fog_shader`, and `text_shader` with `#version 130/140` directives,
  causing all geometry to render black on Apple Silicon (Panda3D CocoaGraphicsPipe, GLSL 1.20
  max). A runtime monkey-patch could not win the race because `from ursina import *` at module
  top-level pre-compiles shader objects before any `if __name__ == '__main__'` code runs. Fixed by
  patching the three shader source files in the installed ursina package directly — `in`/`out`
  replaced with `attribute`/`varying`, `texture()` with `texture2D()`, named `out vec4` outputs
  replaced with `gl_FragColor`. Originals backed up as `.bak` files alongside each shader.

---

## [1.2.1] - 2026-05-20

### Fixed
- Enemy Y position spawned 1.5 units above editor placement — removed hardcoded `y + 1.5`
  offset in `load_level()`; Y coordinate from `level.json` is now trusted directly.
- `PlaceEntityCommand.redo()` was a permanent no-op — constructor now captures a snapshot;
  `execute()` recreates the entity from that snapshot on redo.
- `game.state` stuck at `PLAYING` after play-in-editor exit — `game.state = Game.MAIN_MENU`
  now set after teardown in both happy path and fallback.
- 201 `eternal=True` debug ray-cast entities created per Player spawn — `draw_raycasts`
  default changed `True` → `False`; no extra entities created during normal play.
- `return_to_menu()` state stuck at `RETURNING_TO_MENU` on exception — `_clear_gameplay_entities()`
  wrapped in `try/finally` to guarantee state transition even on error.
- Block `rotation` not applied in play-in-editor spawn — `rotation=tuple(entry.get('rotation',
  [0,0,0]))` added to block `Entity` constructor in `_spawn_gameplay_from_snapshot()`.

---

## [1.2.0] - 2026-05-20

### Added
- **Game state machine** (`Scripts/game.py`) — replaces scattered module-level globals
  (`player`, `game_paused`, `pause_menu`) with a `Game` class singleton. States:
  `MAIN_MENU`, `PLAYING`, `PAUSED`, `RETURNING_TO_MENU`, `WIN`.
- **`_clear_gameplay_entities()`** in `main.py` — canonical, single-path scene teardown
  called exclusively via `game.return_to_menu()`.
- **Level editor v1.2** (`Scripts/level_editor.py`) — full Unity-feel editor with:
  snap (1.0 / 0.5 / 0.25 / Off), undo/redo (depth 50), multi-select, inspector panel,
  hierarchy panel, XYZ transform gizmos, camera bookmarks (slots 1–5), play-in-editor (F5).
- **`Scripts/undo_redo.py`** — command-pattern undo/redo stack with 6 command types:
  `PlaceEntityCommand`, `DeleteEntityCommand`, `MoveEntityCommand`, `ChangeTextureCommand`,
  `ChangeColourCommand`, `ChangePropertyCommand`.
- `level.json` schema v1.2 — blocks now carry `colour` and `rotation`; enemies carry
  `hp`, `enemy_type`, and `rotation_y`.
- `editor_prefs.json` — persists camera bookmarks and grid-snap setting across sessions.

### Fixed
- Weapon entity not destroyed with player on scene transition.
- `CollisionManager._tracked` always empty — all registrations now go through
  `collision_manager.add()`; spatial grid fully functional.
- `_swept_blocked()` O(N) `scene.entities` scan replaced with `collision_manager.query_layer()`.
- `window.on_resize` callback incorrectly assigned (extra parens removed).
- `HealthBar.destroy()` override was dead code — removed; callers explicitly destroy text first.
- `_is_occluded()` in enemy used `entity.name == 'player'` string check — replaced with
  `Layers.PLAYER` bitmask check.
- `level.json` had 86 duplicate block entries — deduped to 65; `save_level()` now
  deduplicates on every write.
- Double `unregister()` in bullet `die()` removed from both `PlayerBullet` and `EnemyBullet`.
- Dead `enabled=False` pre-allocation branches deleted from both bullet `__init__` methods.
- Enemy placeholder scale mismatch in level editor — all three instances updated to `(1.5, 3, 1.5)`.
- Player `unregister` leak on scene transition — explicit `collision_manager.remove(player)`
  added before every `destroy(player)`.
- `main_menu()` destroy loop bypassed `AliveEntity.die()` — explicit `die()` pass added first.

---

## [1.1.0] — pre-versioning (audit release)

### Added
- Collision bitmask system — `Layers` registry, `COLLISION_MATRIX`, `can_hit()`.
- `AliveEntity` mixin — idempotent `die()` lifecycle replacing ad-hoc `_destroyed` bool.
- `BulletPool` — module-level singletons `_player_bullet_pool` (30) and `_enemy_bullet_pool` (60);
  eliminates per-shot allocation. Pool bullets parked at `y = −10000` (no `enabled` toggle).
- `CollisionManager` spatial grid — `register()`, `unregister()`, `query_layer()`, `query_near()`.

### Fixed
- 5 critical bugs: bullet damage not applying, `raycast(ignore=)` passed classes not instances,
  wall tunnelling (swept player movement), double-destroy on entity death, startup crash.

---

## [1.0.0] — initial release

- First-person shooter on Ursina 8.3.0 / Panda3D 1.10.16.
- GLSL 1.20 shader patch for macOS OpenGL 2.1 / Apple Silicon compatibility
  (`_patch_shaders_to_glsl120()` in `main.py` and `level_editor.py`).
- MSAA 4× anti-aliasing.
- `Player` (FirstPersonController subclass) with 5-height swept raycast movement.
- `Enemy` with line-of-sight check, patrol, shoot behaviour.
- `Weapon` / `PlayerBullet` / `EnemyBullet` with swept raycast hit detection.
- World-space and screen-space `HealthBar`.
- JSON level format (`level.json`).
