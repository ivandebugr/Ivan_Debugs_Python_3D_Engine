---
description: "Recurring patterns and conventions discovered across work — architecture, naming, tooling, and implementation patterns"
tags:
  - brain
---

# Patterns

Recurring patterns discovered across work.

## CollisionManager.add() for tracked entities, not standalone register() — 2026-05-20
**Problem:** Entities registered with standalone `register()` get bitmask attributes but are invisible to the spatial grid — `query_layer()` and `query_near()` return nothing.
**Solution:** Always use `collision_manager.add(entity, layer)` when you want an entity to participate in spatial queries. Use `collision_manager.remove(entity)` on teardown. The standalone `register()` / `unregister()` functions are for bitmask-only use cases (e.g. pool bullets that are acquired/released frequently).
**Used in:** `collision_system.py`, `player_controller.py`, `enemy.py`, `weapon.py`
**Source:** [[work/audits/2026-05-20-full-audit]]

## Parent owns sub-entities — parent to logical owner, not to global nodes — 2026-05-20
**Problem:** When a sub-entity (e.g. Weapon, crosshair) is parented to a global node (camera) instead of its logical owner (player), it is not destroyed when the owner is destroyed. Entities accumulate across scene transitions.
**Solution:** Parent all owned sub-entities to their logical owner. If a sub-entity must be parented to a global (e.g. UI overlay parented to camera.ui), store an explicit reference and destroy it in `on_die()` or at the destroy call site.
**Used in:** `weapon.py` (Weapon + crosshair should be destroyed with player), `health_bar.py` (text parented to camera.ui instead of health bar)
**Source:** [[work/audits/2026-05-20-full-audit]]

## AliveEntity.on_die() is the cleanup hook — not destroy() override — 2026-05-20
**Problem:** Ursina's `destroy()` does not invoke Python's `entity.destroy()` method, so overrides are dead code.
**Solution:** Put all entity cleanup logic in `on_die()` (for `AliveEntity` subclasses) — called by `die()` before `destroy(self)`. For non-managed entities, call `destroy(sub_entity)` explicitly at every destroy call site.
**Used in:** `enemy.py` (correctly uses `on_die()`), `health_bar.py` (override removed, callers now explicit)
**Source:** [[work/audits/2026-05-20-full-audit]]

## Explicit teardown at every destroy call site for camera.ui sub-entities — 2026-05-20
**Problem:** `HealthBar.text` is parented to `camera.ui`, not to the health bar entity. `destroy(health_bar)` does not cascade to it. The dead `destroy()` override was the attempted workaround but Ursina never calls it.
**Solution:** Store a reference (`health_bar.text`) and explicitly `destroy(health_bar.text)` then `destroy(health_bar)` at every call site that tears down a 2D health bar. If a sub-entity must be parented to a global node, the owner's destroy site must know about it.
**Used in:** `main.py` — both `start_game()` and `return_to_main_menu()` now explicitly destroy `player.health_bar.text`
**Source:** [[work/audits/2026-05-20-full-audit]]

## Pure Python layer for game content generators — 2026-05-20
**Problem:** Procedural generators and data pipelines that import Ursina become hard to test standalone and create implicit startup dependencies (App must be running, window must be open).
**Solution:** Any content-generation module (`LevelGenerator`, `AssetRegistry`, future `NavMeshBuilder`) must be pure Python — no Ursina import, no Panda3D import, no global state. They take plain Python data in and return plain Python data out. The layer that uses Ursina (level editor, `load_level()`) calls them and wires up the resulting data to entities.
**Used in:** `Scripts/level_generator.py` (v2.0 design), `Scripts/asset_registry.py` (v1.3 design)
**Source:** [[work/active/v2.0-release]], [[work/archive/2026/v1.3-asset-import-pipeline]]

## Unified input manager hides device type from game logic — 2026-05-20
**Problem:** `Player.update()` reads `held_keys['w']` etc. directly. Adding gamepad support requires touching every input read site, and the keyboard path must be preserved for players without a gamepad.
**Solution:** `InputManager` wraps keyboard/mouse and gamepad behind a unified interface (`get_move_vector()`, `get_look_delta()`, `is_pressed(action)`). Player controller calls `InputManager` only. When no gamepad is connected, `InputManager` falls through to `held_keys`. Adding a new input device means updating only `InputManager`, not the game logic.
**Used in:** `Scripts/input_manager.py` (v2.0 design)
**Source:** [[work/active/v2.0-release]]

## Command pattern for undo/redo — 2026-05-20
**Problem:** Level editor needs multi-step undo/redo without tight coupling between operations and the UI or history mechanism.
**Solution:** Each operation implements `execute()` and `undo()` as a `Command` subclass. A bounded `deque(maxlen=50)` stores executed commands. `push(cmd)` clears the redo stack. `undo()` pops from the undo stack, calls `cmd.undo()`, pushes to redo. `redo()` is symmetric. Stack clears on level load and save — these are ground-truth reset points.
**Used in:** `Scripts/undo_redo.py`, `Scripts/level_editor.py`
**Source:** [[work/active/v1.2-level-editor-overhaul]]

## Game state machine as plain Python class — 2026-05-20
**Problem:** Module-level globals (`player`, `game_paused`, `pause_menu`) in `main.py` require `global` declarations in every function that writes them, and they are invisible to external callers like `level_editor.py`.
**Solution:** A plain Python class (NOT a Ursina `Entity` subclass) with string state constants, a module-level singleton `game = Game()`, and transition methods (`start()`, `pause()`, `resume()`, `return_to_menu()`). The class holds references to Ursina objects (`game.player`, `game.enemies`) but has no Panda3D lifecycle itself — callers drive it from Ursina's `input()` and `update()`. Import is `from Scripts.game import game, Game`.
**Why not Entity subclass:** No `update()` hook needed; subclassing adds startup dependency on Ursina App being live at import time.
**Used in:** `Scripts/game.py`, `main.py`, `Scripts/level_editor.py` (play-in-editor)
**Source:** [[work/active/v1.2-level-editor-overhaul]]

## Run AliveEntity.die() before blanket destroy loops — 2026-05-20
**Problem:** `main_menu()` called `destroy(e)` on all scene entities including live `AliveEntity` instances, bypassing `die()`, `on_die()`, and `unregister()`.
**Solution:** Before any blanket `destroy()` loop, iterate `scene.entities[:]` and call `e.die()` on any entity that `isinstance(e, AliveEntity) and e.alive`. This fires cleanup and unregistration before Ursina's deferred destroy queue processes them.
**Used in:** `main.py:main_menu()`
**Source:** [[work/audits/2026-05-20-full-audit]]

## Editor is a config store; runtime is a factory consumer — 2026-06-30
**Problem:** A system where both the level editor and the running game read the same `level.json` field, but the editor only needs to *display and edit* a config while the runtime needs to *construct a live object* from it. Building the live object in the editor attaches framework-coupled state (game-state guards, collision registration, player references) to an entity that has none of that context — see [[brain/Gotchas]] "BehaviourTreeFactory.build() must never run on an editor placeholder".
**Solution:** Split the two read paths by responsibility, both fed from the single canonical parser (`level_io.load_level_data`). The **editor load path** stores the raw config dict verbatim on the placeholder (`placeholder.behaviour_config = entry['behaviour']`) and constructs nothing. The **runtime / play-spawn path** is the only consumer that calls the factory (`BehaviourTreeFactory.build(config['tree'], config)`) — and only after it has built the real gameplay object. Serialization is symmetric: write the stored config back IFF it is non-empty, omit the key otherwise (backwards-compatible with pre-feature files). This generalises beyond behaviour trees: any v1.5+ system where the editor displays config and the runtime builds a live object (e.g. the planned trigger/zone system) should follow this shape — editor stores, runtime builds.
**Used in:** `Scripts/level_io.py` (single parser), `main.py` (`load_level` stores → `start_game` builds), `Scripts/level_editor.py` (`load_existing_level`/`_restore_editor_level` store → `_spawn_gameplay_from_snapshot` builds), `Scripts/behaviour_tree_factory.py`
**Source:** [[work/archive/2026/v1.4-enemy-behaviour-trees]]

## Movement-threshold constants derived from max per-frame step, never magic numbers — 2026-06-30
**Problem:** A "has the mover arrived?" distance check (`to_target.length() <= threshold`) with a hardcoded literal threshold. If the threshold is smaller than the distance the mover can cover in one slow frame (`speed * dt_max`), a single low-FPS frame can step the mover clean past the target without ever landing inside the radius — producing an infinite oscillation around the waypoint that never fires the arrival branch.
**Solution:** Define the threshold as a **named module constant** whose value is *derived*, not guessed: `threshold >= speed * dt_max`, where `dt_max` is the worst-case frame delta the game tolerates (e.g. a 20 FPS floor → `dt_max = 0.05`). Document the derivation inline so a retune of speed forces a conscious re-derivation. `PatrolNode` uses `PATROL_WAYPOINT_THRESHOLD = 0.3` against `PATROL_SPEED_DEFAULT = 5` and `dt_max = 0.05` (`5 * 0.05 = 0.25`, cleared with margin).
**Used in:** `Scripts/behaviour_nodes.py` (`PATROL_WAYPOINT_THRESHOLD`, derived from `PATROL_SPEED_DEFAULT` × the 20 FPS-floor `dt_max`)
**Source:** [[work/archive/2026/v1.4-enemy-behaviour-trees]]

## Call setBin/setDepthTest/setDepthWrite on the Entity, never on .node() — 2026-06-24
**Problem:** Ursina's `Entity` subclasses Panda3D's `NodePath` directly, but it's easy to assume render-bin/depth-test calls belong on the underlying Panda node instead.
**Solution:** Call `setBin`, `setDepthTest`, and `setDepthWrite` on the `Entity` instance itself — `entity.node()` returns a `PandaNode`, which has no `setBin`. Pattern: `tip.setBin('fixed', 100); tip.setDepthTest(False); tip.setDepthWrite(False)`. Bin 100 renders 3D gizmo handles above all other 3D geometry.
**Used in:** `Scripts/editor_gizmo.py` (gizmo axis tips; lived in `level_editor.py` before the v1.6 split)
**Source:** CLAUDE.md Hard Constraint 11

## One shared UI-theme module for palette + spacing, imported everywhere it applies — 2026-06-30
**Problem:** `PlayerHUD`/`PauseMenu`/`EndScreen` (`main.py`) and `HealthBar` (`health_bar.py`) each hardcoded their own colours (`color.black`, `color.black66`, `color.rgba(0,0,0,0.7)` — three different overlay-blacks for the same conceptual "dim background" — plus an unrelated green/orange/red health-bar ramp) and spacing (`0.15` vs `0.2` vertical button gaps between the pause menu and main menu, no shared rationale). Symptom was purely visual inconsistency, not a crash — "looks bad" audited down to "four ad hoc blacks and two button gaps," not a deeper structural problem.
**Solution:** A single `Scripts/ui_theme.py` module with zero logic — just named 0–1-float `color.rgb()` constants (backgrounds, text weights, health-bar ramp, win/lose accents) and a small spacing scale (`BUTTON_SCALE`, `BUTTON_GAP`, `HUD_MARGIN`). Every screen imports from it; `health_bar.py`'s old `BAR_COLOR_*` names are kept as local aliases pointing at the shared constants so call sites didn't need to change. Win/lose end screens share one layout, differentiated only by passing an `accent` colour through — no per-screen redesign needed to make them visually distinct.
**Used in:** `Scripts/ui_theme.py`, `main.py` (`PlayerHUD`, `PauseMenu`, `EndScreen`), `Scripts/health_bar.py`
**Source:** v1.5 HUD/menu redesign — see [[work/archive/2026/v1.5-gameplay-systems]]

## Corner-anchor HUD elements off window.bottom_left/top_right, not literal x — 2026-06-30
**Problem:** Verifying whether `main.py`'s HUD needed a manual resize hook (the level editor already has one — `_apply_layout()` wrapping `window.update_aspect_ratio`, per CLAUDE.md Hard Constraint 15/16). Reading `window.py` showed Ursina wires `base.accept('aspectRatioChanged', self.update_aspect_ratio)` itself at `Window.__init__` — unrelated to the dead `window.on_resize` — and that handler already rescales every `camera.ui` child's `x` by the aspect-ratio delta every resize, with no opt-in required.
**Solution:** Any HUD element built with `position=window.bottom_left + Vec2(...)` (or `top_right`, etc.) automatically resizes correctly for free, because `window.bottom_left.x` is `-aspect_ratio/2` and the child's resulting `.x` participates in Ursina's built-in rescale. Verified live: a headless smoke-test resized the window 1280×720 → 2560×1080 (aspect 1.78 → 2.37) → back, mid-`PLAYING` state, with `PlayerHUD`'s corner-anchored `hint_text`/`ammo_text` intact and no crash. The level editor's `_apply_layout()` machinery is solving a *harder* problem (toolbar widths, multi-panel layouts) — don't assume every screen needs that scale of resize plumbing. For simple corner-pinned text/icons, anchoring off `window.*` corner properties at construction time is sufficient.
**Used in:** `main.py` (`PlayerHUD.hint_text`, `PlayerHUD.ammo_text`)
**Source:** v1.5 HUD/menu redesign — see [[work/archive/2026/v1.5-gameplay-systems]]; see also [[brain/Gotchas]] "window.on_resize is dead code in Ursina 8.3.0"

## Visible pedestal block under every invisible pickup — 2026-07-06
**Problem:** `AmmoPickup` is `visible=False` with no model (v1.5 shipped it as a pure collider), so a level-placed pickup is undiscoverable — the player has no idea anything is there.
**Solution:** Content-only fix, no engine change: place a small collidable pedestal block (`scale (0.8, 0.5, 0.8)`, distinct texture per pickup family) directly under each pickup, with the pickup floating at pedestal-top + 0.3 so the player's collider overlaps it when stepping up. Texture families double as colour-coding (orange test-texture = shotgun, blue tiles = rifle in `levels/v1.json`). Bonus behaviour for free: an ammo pickup for an un-owned weapon isn't consumed (`_collect` returns False), so the pedestal display "restocks" honestly until the matching weapon is owned.
**Used in:** `levels/v1.json` (4 pickups, 4 pedestals)
**Source:** CHANGELOG [1.5.1]; see [[brain/Key Decisions]] pre-v1.6 closure pass

## Collaborator class with owner back-ref for splitting a god-file UI class — 2026-07-07
**Problem:** Splitting a 4k+ line Ursina `Entity` subclass (`LevelEditor`) whose concerns share mutable state (selection set, entity lists, undo history, grid snap, mode flags) and whose `input()`/`update()` dispatch order is itself a documented invariant (v1.2.4 priority chain). Naive "one file per feature" breaks either the shared-state coupling or external callers that reach methods by name (`undo_redo.py` commands call `editor._refresh_*`).
**Solution:** Extract each self-contained UI/behaviour band as a plain class (NOT an Entity) taking the owner in its constructor (`self.editor`), moving method bodies verbatim — `self.X` becomes `ed.X` only for state that stays owner-side. Three rules decide what stays on the owner: (1) state read by more than one module stays (e.g. `_play_mode` — core, gizmo, and browser all read it); (2) state touched only by the moved methods moves with them (e.g. the play snapshot, saved camera); (3) methods external modules call by name keep one-line delegators with the original names on the owner. The owner's `input()`/`update()` keep the dispatch order and route to collaborators at the same priority steps. Verify each step in-app with real key/mouse dispatch, not just imports — the one integration miss (a class constant read off the wrong object post-move) only surfaced in a live run.
**Used in:** `Scripts/editor_core.py` + `editor_hierarchy/gizmo/browser/inspector/playmode.py` (v1.6 split)
**Source:** [[work/archive/2026/v1.6-level-editor-refactor]]

## Commit mechanical extraction before call-site wiring in multi-step refactors — 2026-07-07
**Problem:** A credit/session cutoff mid-extraction leaves an ambiguous working tree: the new module may exist while the core still holds (or half-holds) the moved code, and the next session can't tell narration from ground truth.
**Solution:** Within each extraction step, land two commits: (a) mechanical — new file + core excision + constructor wiring, compiling but with call sites still pointing at the old names; (b) wiring + verification — call-site rewrites plus the in-app checks. An interruption then loses at most the wiring pass, and the resume point is a clean commit boundary instead of a diff archaeology session. Adopted mid-v1.6 after exactly that cutoff; used for steps 6–7.
**Used in:** v1.6 steps 6–7 (`1b441df`/`b4d7322`, `cf0dde4`/`363f5fc`)
**Source:** [[work/archive/2026/v1.6-level-editor-refactor]] (Process Note)
