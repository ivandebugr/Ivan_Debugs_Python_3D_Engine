# CLAUDE.md — Ivan's 3D Engine Operating Manual

**Ivan's 3D Engine** is a first-person shooter built on Ursina (Python / Panda3D).
This file is the authoritative operating manual loaded at the start of every Claude Code session.

---

## Project Identity

| Field            | Value                                                                 |
|------------------|-----------------------------------------------------------------------|
| Engine           | Ursina 8.3.0 (Panda3D 1.10.16)                                        |
| Language         | Python 3.10+                                                          |
| Genre            | First-person shooter                                                  |
| Entry point      | `main.py`                                                             |
| Level format     | JSON (`level.json`)                                                   |
| Platform target  | macOS (OpenGL 2.1 / GLSL 1.20 constraint — see Compatibility section) |

---

## Active Persona

### `solo-founder`
This is a solo indie game project. Think in terms of a single developer who designs, codes,
and ships everything. Prioritize:
- Playability and game-feel over perfect architecture
- Shipping increments over rewrites
- Highest-impact fixes first (RICE score thinking)
- Honest tech-debt tracking so decisions are made consciously, not by accident

---

## Active Skills Stack

### Always Load
| Skill                  | Path                                                   | Why for this project                                       |
|------------------------|--------------------------------------------------------|------------------------------------------------------------|
| `senior-architect`     | `engineering-team/senior-architect/SKILL.md`           | Collision authority, singleton design, scene lifecycle     |
| `karpathy-coder`       | `engineering/karpathy-coder/SKILL.md`                  | Game-loop performance, tight Python loops, raycast budget  |
| `code-reviewer`        | `engineering-team/code-reviewer/SKILL.md`              | Catch deferred-destroy bugs, double-authority collisions   |
| `tech-debt-tracker`    | `engineering/tech-debt-tracker/SKILL.md`               | Track module-level globals, missing collision_system stubs |

### Load When Relevant
| Task                               | Skill                               |
|------------------------------------|-------------------------------------|
| New feature design / roadmap       | `product-manager-toolkit`           |
| Level design, balance experiments  | `experiment-designer`               |
| Writing changelogs / release notes | `release-manager`                   |
| Prompt engineering for tooling     | `senior-prompt-engineer`            |

### Obsidian Mind Plugin
Loaded from `breferrari/obsidian-mind`. The vault is the persistent memory layer —
every session builds on the last. See **Obsidian Mind Integration** below.

---

## Architecture Overview

### Module Map
```
main.py                    — App init, window setup, scene management, _clear_gameplay_entities(),
                             PauseMenu, load_level(), main_menu(), global update/input
Scripts/
  game.py                  — Game state-machine class (MAIN_MENU/PLAYING/PAUSED/RETURNING_TO_MENU/WIN),
                             module-level singleton game = Game(); no Ursina import at class-def time
  collision_system.py      — Bitmask Layers, COLLISION_MATRIX, register/unregister/can_hit,
                             AliveEntity mixin, swept_cast(), CollisionManager spatial grid
  player_controller.py     — FirstPersonController subclass, 5-height swept raycast movement,
                             BoxCollider, HealthBar attachment
  weapon.py                — Weapon, PlayerBullet, EnemyBullet, BulletPool (module-level singletons)
  enemy.py                 — Enemy (AliveEntity subclass), hp/enemy_type/rotation_y params,
                             lazy pool import pattern
  health_bar.py            — World-space and screen-space health bar
  level_editor.py          — Unity-feel level editor: snap, undo/redo, multi-select, inspector,
                             hierarchy, gizmos, bookmarks, play-in-editor (standalone runnable)
  undo_redo.py             — Command-pattern undo/redo stack; UndoRedoStack + 6 command types
level.json                 — Saved level data (blocks + enemies); schema v1.2 with colour/rotation/hp
editor_prefs.json          — Camera bookmarks (slots 1–5) + grid snap, persisted across editor sessions
```

### Collision Authority (Three Systems — Never Add a Fourth)

| Authority | Where | What it covers |
|---|---|---|
| Swept projectile raycast | `PlayerBullet.update`, `EnemyBullet.update` in `weapon.py` | Bullet → wall/character; single damage application point |
| Swept player movement | `Player._swept_blocked` in `player_controller.py` | Player → wall; 5 rays cast *before* moving |
| Ground / ceiling raycast | `Player.update` in `player_controller.py` | Gravity, landing, head-bump |

### Bitmask Layer Registry (`collision_system.py`)
```python
from Scripts.collision_system import Layers, register, can_hit

register(self, Layers.ENEMY)           # in Enemy.__init__
register(self, Layers.PLAYER_BULLET)   # in PlayerBullet.__init__ (via pool)

# In PlayerBullet.update — single authority:
if can_hit(self, hit.entity):
    hit.entity.health -= self.damage
```
`can_hit(a, b)` → `a._collision_layer & b._collision_mask`. Returns `False` for
unregistered entities (walls). **Never** use `can_hit` inside `_swept_blocked`.

### AliveEntity Lifecycle (replaces `_destroyed` bool)
```python
class MyEntity(AliveEntity):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        register(self, Layers.ENEMY)

    def update(self):
        if not self.alive:   # guard — update() fires this frame after die()
            return
        if should_die:
            self.die()

    def on_die(self):
        destroy(self.health_bar)   # sub-entities first; super().die() calls destroy(self)
```
`die()` is idempotent. Pool bullets override `die()` to return to pool instead of calling `destroy()`.

### Bullet Pool
Module-level singletons in `weapon.py`:
- `_player_bullet_pool = BulletPool(PlayerBullet, size=30)`
- `_enemy_bullet_pool  = BulletPool(EnemyBullet,  size=60)`

Inactive bullets are parked at `Vec3(0, -10000, 0)`. **Never** toggle `entity.enabled` on pooled
bullets — Panda3D's `unstash()` asserts on re-enable and crashes.

### Player Collider Dimensions
- `BoxCollider(self, center=(0, 0.75, 0), size=(0.8, 2.5, 0.8))`
- Feet at `entity.y − 0.5`, top at `entity.y + 2.0`
- `_swept_blocked` offsets: `[−0.4, +0.3, +0.9, +1.5, +1.9]` — five heights, feet to forehead
- `camera.clip_plane_near = 0.01` set at app init

---

## Ursina 8.3.0 Compatibility (macOS / OpenGL 2.1)

**Root cause of black screen after upgrade:** Ursina 8.3.0 set `Entity.default_shader =
unlit_with_fog_shader`, and `Sky()` hardcodes `shader=unlit_shader`. Both use GLSL `#version 130/140`.
macOS OpenGL 2.1 (Panda3D CocoaGraphicsPipe on Apple Silicon) supports GLSL 1.20 at most →
every shader fails to compile → geometry renders black.

**Fix already applied in `main.py`:** `_patch_shaders_to_glsl120()` rewrites both shader objects
before any entity is created and sets `compiled = False` so they recompile on first use.

Key GLSL 1.20 differences: `attribute`/`varying` instead of `in`/`out`, `texture2D()` instead
of `texture()`, `gl_FragColor` instead of a named `out vec4`.

**Do not** upgrade shaders back to `#version 130+` without verifying a working Core Profile
context exists on the target machine.

**Other 8.3.0 changes accounted for:**
- `window.color` default → black; set explicitly to `color.rgb(50, 50, 60)` after `App()`
- `Sky(texture='sky_default')` → `Sky()` — the `sky_default` asset was removed

The same shader patch is duplicated in `level_editor.py` for standalone runs.

---

## Known Tech Debt (Track Consciously)

| Issue | Location | Priority | Notes |
|---|---|---|---|
| **[FIXED v1.2]** Module-level globals (`player`, `game_paused`, `pause_menu`) | `main.py` | ~~Medium~~ | `Game` state-machine class in `Scripts/game.py`; module-level singleton `game = Game()` |
| **[FIXED v1.2]** Scene teardown is ad-hoc | `PauseMenu.return_to_main_menu()` | ~~Medium~~ | `_clear_gameplay_entities()` in `main.py`; called exclusively by `game.return_to_menu()` |
| No win condition / game-over state | `main.py`, `player_controller.py` | Low | Player health→0 resets position, not game |
| HealthBar uses `eternal=True` on sub-entities | `health_bar.py` | Medium | Prevents clean scene teardown on menu return |
| `level_editor.py` `_patch_shaders_to_glsl120()` is a copy-paste of `main.py` | `level_editor.py` | Low | Extract to shared module |
| Crosshair visibility state not restored on non-Esc pause paths | `main.py` | Low | Only Esc-key path is guarded |
| `swept_cast()` is unused dead code | `collision_system.py` | Low | Defined and exported but called nowhere; remove if no call site by next audit |
| `_pooled` param on `PlayerBullet`/`EnemyBullet.__init__` is now unused | `weapon.py` | Low | Dead param after #11 fix; harmless but confusing |
| **[FIXED v2026-05-20]** Weapon entity not destroyed with player | `main.py` | ~~High~~ | Explicit `destroy(weapon)` + `destroy(crosshair)` at both destroy sites |
| **[FIXED v2026-05-20]** `CollisionManager._tracked` always empty | `collision_system.py` | ~~High~~ | All registrations now use `collision_manager.add()`; spatial grid functional |
| **[FIXED v2026-05-20]** `_swept_blocked()` O(N) scene.entities scan | `player_controller.py` | ~~Medium~~ | Now uses `collision_manager.query_layer()` |
| **[FIXED v2026-05-20]** `window.on_resize` callback incorrectly assigned | `main.py` | ~~Low~~ | Removed parens |
| **[FIXED v2026-05-20]** `HealthBar.destroy()` override dead code | `health_bar.py` | ~~Medium~~ | Override removed; callers explicitly destroy text before health bar |
| **[FIXED v2026-05-20]** `_is_occluded()` uses name check instead of bitmask | `enemy.py` | ~~Medium~~ | Now uses `Layers.PLAYER` bitmask check |
| **[FIXED v2026-05-20]** `level.json` has duplicate block entries | `level.json` | ~~Low~~ | Deduped 86→65 entries; `save_level()` now deduplicates on write |
| **[FIXED v2026-05-20]** Double `unregister()` in bullet `die()` | `weapon.py` | ~~Low~~ | `unregister(self)` removed from both bullet `die()` overrides |
| **[FIXED v2026-05-20]** Dead `enabled=False` branch in bullet `__init__` | `weapon.py` | ~~Low~~ | Pre-allocation branches deleted from both `PlayerBullet` and `EnemyBullet` |
| **[FIXED v2026-05-20]** Enemy placeholder scale mismatch in level editor | `level_editor.py` | ~~Low~~ | All three placeholder instances updated to `(1.5, 3, 1.5)` |
| **[FIXED v2026-05-20]** Player unregister leak on scene transition | `player_controller.py`, `main.py` | ~~Medium~~ | Explicit `collision_manager.remove(player)` before every `destroy(player)` |
| **[FIXED v2026-05-20]** `main_menu()` loop bypasses `AliveEntity.die()` | `main.py` | ~~Medium~~ | Explicit `die()` pass added before blanket destroy loop |

Log new footguns discovered during development → `brain/Gotchas.md` in the vault.

---

## Key Rules

1. **Never** check `entity.name == 'enemy'`; use `can_hit(self, entity)` in bullet updates,
   or `isinstance(entity, Enemy)` only where type identity is truly needed.
2. **Never** pass classes to `raycast(ignore=...)` — always pass instances filtered from `scene.entities`.
3. **Never** call `destroy()` on a managed entity directly — call `self.die()` (AliveEntity pattern).
4. Use `except Exception:` not bare `except:`.
5. Bullet-vs-enemy/player AABB loops in `main.py` are **deleted** — don't re-add them.
6. **`swept_cast` (collision_system.py) is for bullets only** — player `_swept_blocked` must use
   raw `raycast().hit` or walls become passable.
7. `time` in Ursina scope is Panda3D's clock. Use `import time as _time; _time.time()` for wall-clock.
8. `destroy(entity)` is **deferred** — entity stays in `scene.entities` until end-of-frame flush.
   Use `die()` for managed entities.
9. Pool bullets must not have `enabled` toggled — use position parking (`y = -10000`) instead.
10. **Ursina 8.3.0 shader patch must run before any `Entity` is created.**

---

## Common Workflows

### Adding a New Enemy Type
1. Extend `Enemy` (which extends `AliveEntity`) in `enemy.py`
2. `register(self, Layers.ENEMY)` in `__init__`
3. Override `on_die()` to destroy sub-entities before `super().on_die()`
4. Update `level.json` schema and `load_level()` in `main.py`
5. Add spawn handling in `start_game()` inside `main_menu()`

### Adding a New Weapon / Bullet Type
1. Add a new `Layers` bitmask entry in `collision_system.py`
2. Update `COLLISION_MATRIX` with what the new layer hits
3. Create bullet class extending `AliveEntity` in `weapon.py`
4. Override `die()` to return to pool instead of calling `destroy()`
5. Add a module-level `BulletPool` singleton in `weapon.py`
6. Expose a `get_X_bullet_pool()` accessor for other modules

### Adding a New Level Feature
1. Add entry type to `level.json` schema
2. Handle the new type in `load_level()` (main.py) and `load_existing_level()` (level_editor.py)
3. Add cleanup to `main_menu()` entity-clearing loop and `return_to_main_menu()`

### Using the Level Editor (v1.2+)
```
python Scripts/level_editor.py
# Left click on surface     — place block or enemy (mode button)
# Shift + left click        — add to selection (multi-select)
# Right mouse drag          — box-select rectangle
# Click on entity           — select it (inspector + hierarchy update)
# Delete                    — remove all selected entities
# Ctrl+Z / Ctrl+Y           — undo / redo (depth 50)
# Ctrl+S                    — save level.json (clears undo stack + saves prefs)
# G or Snap button          — cycle grid snap: 1.0 → 0.5 → 0.25 → Off
# Drag X/Y/Z gizmo axis     — move selection along that axis (snapped)
# Ctrl+1 through Ctrl+5     — save camera bookmark to slot
# 1 through 5               — recall camera bookmark
# F5 / Esc                  — toggle play-in-editor mode
```
`editor_prefs.json` persists bookmarks and snap setting across sessions.
All editor-only entities are named `editor_*` — excluded from level save/load.

### Testing a Fix
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
```

### Performance Investigation Order
```
1. window.fps_counter — baseline FPS drop identification
2. Panda3D PStats (import pstats) — CPU timeline, draw call count
3. Inspect scene.entities length — leaked entities inflate raycast cost every frame
4. Profile _swept_blocked — 5 raycasts × N movement frames; most expensive per-frame cost
5. Profile CollisionManager.update() — O(entities) per frame; ensure pool bullets stay parked
```

---

## Imports and Circular Import Rules

The `weapon.py ↔ enemy.py` circular import is broken by:
- `EnemyBullet` lives in `weapon.py` (not `enemy.py`)
- `enemy.py` accesses the pool via a **lazy import inside `shoot()`**:
  ```python
  def shoot(self):
      from Scripts.weapon import get_enemy_bullet_pool   # lazy — only at call time
  ```
Never move `EnemyBullet` back to `enemy.py`.

---

## Game State Machine (`Scripts/game.py`)
`game = Game()` is the module-level singleton. Import with `from Scripts.game import game, Game`.
- `game.player` — current `Player` instance or `None`
- `game.enemies` — `list[Enemy]` of active enemies
- `game.pause_menu` — current `PauseMenu` instance or `None`
- `game.state` — one of `Game.MAIN_MENU`, `Game.PLAYING`, `Game.PAUSED`, `Game.RETURNING_TO_MENU`, `Game.WIN`

Use `game.state == Game.PLAYING` guards in `input()` and `update()` instead of the old `player` existence checks.
`game.return_to_menu()` is the **only** code path that calls `_clear_gameplay_entities()`.

---

## Scene Transitions
Return-to-menu: `PauseMenu.return_to_main_menu()` → `main_menu()`.
`HealthBar` sub-entities must **not** use `eternal=True` (blocks teardown).

---

## Roadmap

### Near-term
- [x] `Game` state-machine class replacing module-level globals in `main.py` — **v1.2**
- [x] `_clear_gameplay_entities()` as the canonical scene teardown path — **v1.2**
- [x] Expand level editor JSON schema: enemy type, HP, rotation, block colour — **v1.2**
- [x] Dedup `level.json` entries — **v1.1**
- [ ] Extract shader patch into shared `compat.py` module

### Engine features
- [x] Collision bitmask system — `Layers` registry + `can_hit()`
- [x] Object pooling for bullets — `BulletPool` eliminates per-shot allocation
- [x] AliveEntity lifecycle — idempotent `die()` replaces `_destroyed` bool
- [x] CollisionManager spatial grid — `query_layer()` / `query_near()`
- [x] Game state machine — `Scripts/game.py`, replaces module-level globals — **v1.2**
- [x] Level editor: snap, undo/redo, multi-select, inspector, hierarchy, gizmos, bookmarks, play-in-editor — **v1.2**
- [ ] Asset browser + hot-reload in level editor — **v1.3**
- [ ] Pluggable enemy behaviour trees — patrol / attack / flee state composition — **v1.4**
- [ ] Trigger/zone system — volume entry/exit callbacks — **v1.5**
- [ ] Weapon inventory API — multi-weapon, ammo pickup, switch animations — **v1.5**

---

## Obsidian Mind Integration

The vault (`breferrari/obsidian-mind`) is the persistent memory layer across sessions.

### Where Project Content Lives
| Content type | Vault location |
|---|---|
| Active feature development | `work/active/` |
| Shipped features / milestones | `work/archive/YYYY/` |
| Architecture decisions (collision design, pool pattern, shader patch) | `work/` — Decision Record template |
| Game design decisions (balance, enemy behaviour) | `brain/Key Decisions.md` |
| Discovered Ursina / Panda3D footguns | `brain/Gotchas.md` |
| Reusable patterns (AliveEntity, lazy import, pool parking) | `brain/Patterns.md` |
| Roadmap / what to build next | `brain/North Star.md` |
| Playtest / QA observations | `thinking/YYYY-MM-DD-playtest.md` |
| Audit reports | `work/audits/YYYY-MM-DD-audit.md` |
| Build / release notes | `work/archive/YYYY/` |

### Session Start
1. Read `brain/North Star.md` — current dev focus
2. Check `work/Index.md` — active feature work
3. Scan `brain/Gotchas.md` — known Ursina/Panda3D footguns before touching collisions or pools
4. Load the skill stack for today's task

### Session End
Run `/wrap-up` or at minimum:
1. Log any new Ursina / Panda3D footgun → `brain/Gotchas.md`
2. Log any reusable pattern discovered → `brain/Patterns.md`
3. Archive completed features → `work/archive/YYYY/`, update `work/Index.md`
4. Log wins → `perf/Brag Doc.md`
5. Update `brain/North Star.md` if priority shifted
6. Every new vault note must link to at least one existing note (orphans are bugs)