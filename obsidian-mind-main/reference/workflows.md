---
date: 2026-07-07
description: "Step-by-step workflows for ivans_3d_engine — adding enemy/weapon/level-feature types, level editor keybindings, testing checklist, perf investigation order"
tags:
  - reference
---

# Common Workflows — Ivan's 3D Engine

Migrated from CLAUDE.md (v1.6 restructure, 2026-07-07) — read this when doing one of these specific
tasks, not every session.

## Adding a New Enemy Type
1. Extend `Enemy` (which extends `AliveEntity`) in `enemy.py`
2. Add the new type string to `VALID_ENEMY_TYPES` in `enemy.py`
3. Call `collision_manager.add(self, Layers.ENEMY)` in `__init__` (do not call `register()` directly)
4. Override `on_die()` to destroy sub-entities (health bar, particles, etc.) before `super().on_die()`
   — `AliveEntity.die()` calls `on_die()` then `destroy(self)`, so sub-entities must be cleaned in `on_die()`
5. Update `level.json` schema and `load_level()` in `main.py`
6. Add spawn handling in `start_game()` inside `main_menu()`

## Adding a New Weapon / Bullet Type
1. Add a new `Layers` bitmask entry in `collision_system.py`
2. Update `COLLISION_MATRIX` with what the new layer hits
3. Create bullet class extending `AliveEntity` in `weapon.py`; set `_layer` class attribute
4. Override `die()` to return to pool instead of calling `destroy()`
5. Add `POOL_SIZE_X` constant and a module-level `BulletPool` singleton in `weapon.py`
6. Expose a `get_X_bullet_pool()` accessor for other modules (breaks circular imports)
7. Add pool to `reset_bullet_pools()` so scene teardown clears it

## Adding a New Level Feature
1. Add entry type to `level.json` schema
2. Handle the new type in `load_level()` (main.py) and `load_existing_level()` (editor_core.py)
3. Add cleanup to `_clear_gameplay_entities()` and the entity-clearing loop in `main_menu()`

## Using the Level Editor (v1.3+)
```
python Scripts/level_editor.py
# [↖ Move] button                 — Move tool (default): left-click selects/deselects; never places
# [+ Place] button                — Place tool: left-click on any surface places a block
# Click a Models-tab card         — drag built-in type (Cube/Stone/Metal/Wood/Enemy); ghost follows mouse
# Release over viewport           — place entity (block or enemy per card type)
# Release over browser / Esc      — cancel drag, no placement
# Scroll wheel over browser       — scroll card list horizontally (per-tab)
# Shift + left click              — add/remove from selection (multi-select, both modes)
# Right mouse drag                — box-select rectangle
# Shift + click on entity         — select it (inspector + hierarchy update)
# Hierarchy search box (top-left) — type to live-filter rows by label (substring); clear to restore
# Click [Blocks (N)]/[Enemies (N)]— collapse/expand that hierarchy section ([+]=collapsed, [-]=expanded)
# Delete                          — remove all selected entities
# Ctrl+Z / Ctrl+Y (or Ctrl+Shift+Z) — undo / redo (depth 50)
# Ctrl+S                          — save level.json (clears undo stack + saves prefs)
# G or Snap button                — cycle grid snap: 1.0 → 0.5 → 0.25 → Off
# Drag X/Y/Z gizmo axis           — move selection along that axis (snapped); works in both modes
# Ctrl+1 through Ctrl+5           — save camera bookmark to slot
# 1 through 5                     — recall camera bookmark (blocked while typing in inspector or hierarchy search)
# F5 / Esc                        — toggle play-in-editor mode
# Ctrl+H / Ctrl+I / Ctrl+B        — collapse/expand hierarchy / inspector / browser panel (v1.7)
# Click panel chevron              — same toggle, mouse alternative to the hotkeys
# Ctrl+Alt+1 through Ctrl+Alt+5   — save layout preset (panel visibility) to slot (v1.7)
# Alt+1 through Alt+5             — recall layout preset
```
`editor_prefs.json` persists bookmarks and snap setting across sessions.
All editor-only entities are named `editor_*` — excluded from level save/load.
Green `editor_player_spawn` cube marker visible at (0, 1.4, 0) — shows player spawn; no collider, not selectable, not saved.
F5 saves editor camera position/rotation before entering play mode and restores it on exit.

## Testing a Fix
```bash
python main.py
# Scene renders (no black screen) — sky, ground, level blocks all visible
# Shoot enemy → health bar decreases, enemy dies cleanly (no crash)
# Walk into wall at any height (feet, waist, head) → player stops (no tunnelling)
# Walk to wall and look down → wall stays visible (no clip-through)
# Enemy shoots player → player takes damage once per bullet
# Fire 30+ shots rapidly → no new Entity objects allocated after pool warms up
# Return to menu and re-enter → no duplicate UI elements
# Pause (Esc) → crosshair disappears; resume → crosshair reappears
# Kill all enemies → WIN screen appears; R returns to main menu cleanly
# Player HP → 0 → GAME OVER screen appears; R returns to main menu cleanly
```

## Performance Investigation Order
```
1. window.fps_counter — baseline FPS drop identification
2. Panda3D PStats (import pstats) — CPU timeline, draw call count
3. Inspect scene.entities length — leaked entities inflate raycast cost every frame
4. Profile _swept_blocked — 5 raycasts × N movement frames; most expensive per-frame cost
5. Profile CollisionManager.update() — O(entities) per frame; ensure pool bullets stay parked
6. Watch HealthBar._registry — must iterate registry list, not scene.entities
7. BulletPool.active_count() — check for pool exhaustion (returns None on acquire)
```

## Related

- [[reference/module-map]] — per-file breakdown of the modules these workflows touch
- [[brain/Patterns]] — reusable architecture patterns referenced by these steps
- [[brain/Gotchas]] — pitfalls specific to editor keybindings and testing traps
