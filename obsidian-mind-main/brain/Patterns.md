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
**Source:** [[work/active/v2.0-release]], [[work/active/v1.3-asset-import-pipeline]]

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
