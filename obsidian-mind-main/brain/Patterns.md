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
**Used in:** `level_editor.py` (gizmo axis tips)
**Source:** CLAUDE.md Hard Constraint 11
