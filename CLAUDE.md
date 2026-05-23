# CLAUDE.md — Ivan's 3D Engine Operating Manual

@.claude/skills/karpathy-guidelines/SKILL.md

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
Read `/.claude/agents/solo-founder.md` at session start.
This is a solo indie game project. Think in terms of a single developer who designs, codes,
and ships everything. Prioritize:
- Playability and game-feel over perfect architecture
- Shipping increments over rewrites
- Highest-impact fixes first (RICE score thinking)
- Honest tech-debt tracking so decisions are made consciously, not by accident

Switch to `/.claude/agents/startup-cto.md` for architecture, performance, and collision audit tasks.

---

## Active Skills Stack

### Always Load
| Skill               | Path                                                  | Why for this project                                       |
|---------------------|-------------------------------------------------------|------------------------------------------------------------|
| `karpathy-coder`    | `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/engineering/karpathy-coder/SKILL.md`                 |
| `senior-architect`  | `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/engineering-team/senior-architect/SKILL.md`          |
| `code-reviewer`     | `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/engineering-team/code-reviewer/SKILL.md`             | 
| `tech-debt-tracker` | `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/engineering/tech-debt-tracker/SKILL.md`              | 

### Load When Relevant
| Task                                       | Skill                    | Path                                              |
|--------------------------------------------|--------------------------|---------------------------------------------------|
| New feature design / roadmap               | `product-manager-toolkit`| `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/product-team/product-manager-toolkit/SKILL.md`  |
| Level design, balance experiments          | `experiment-designer`    | `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/product-team/experiment-designer/SKILL.md`      |
| Writing changelogs / release notes         | `changelog-generator`    | `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/engineering/changelog-generator/SKILL.md`       |
| Cutting a release / version bump           | `release-manager`        | `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/engineering/release-manager/SKILL.md`           |
| Scoping a fix to minimum change            | `focused-fix`            | `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/engineering/focused-fix/SKILL.md`               |
| Performance profiling / FPS drops          | `performance-profiler`   | `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/engineering/performance-profiler/SKILL.md`      |
| Scope / cut / ship decisions               | `founder-coach`          | `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/c-level-advisor/founder-coach/SKILL.md`         |
| Brain dump → roadmap / tasks               | `capture`                | `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/productivity/capture/SKILL.md`                  |
| Prompt engineering for tooling             | `senior-prompt-engineer` | `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/engineering-team/senior-prompt-engineer/SKILL.md`|

### Obsidian Mind Plugin
Loaded from `breferrari/obsidian-mind`. Vault root: check `brain/North Star.md` for current location.
The vault is the persistent memory layer — every session builds on the last.
See **Obsidian Mind Integration** below.

---

## Architecture Overview

### Module Map
```
main.py                    — App init, window setup, _patch_shaders_to_glsl120() (called twice),
                             _clear_gameplay_entities(), PlayerHUD, PauseMenu,
                             load_level(), main_menu(), global update()/input()
                             PlayerHUD: owns crosshair, hint_text, health_bar ref (not lifetime);
                             stored as game.hud; show()/hide() toggle all elements together.
Scripts/
  game.py                  — Game state-machine class (MAIN_MENU/PLAYING/PAUSED/RETURNING_TO_MENU/
                             WIN/GAME_OVER), module-level singleton `game = Game()`;
                             no Ursina import at class-def time.
                             Tracks: player, enemies, pause_menu, hud, win_screen, game_over_screen.
                             return_to_menu() calls _clear_gameplay_entities() inside try/finally.
  collision_system.py      — Bitmask Layers (PLAYER/ENEMY/PLAYER_BULLET/ENEMY_BULLET/WALL/PICKUP),
                             COLLISION_MATRIX, register/unregister/can_hit,
                             AliveEntity (die()/on_die() lifecycle, _alive property),
                             CollisionManager spatial grid (query_layer/query_near).
                             __all__ defined; fully documented. swept_cast() deleted (never existed here).
  player_controller.py     — Player(FirstPersonController) subclass; SWEPT_OFFSETS module constant;
                             5-height swept raycast movement (_swept_blocked); BoxCollider;
                             HealthBar (screen-space); create_collider_visualization() (debug lines,
                             eternal=True but enabled=False — safe until show_colliders=True);
                             on_destroy() unregisters from collision_manager.
  weapon.py                — POOL_SIZE_PLAYER=30, POOL_SIZE_ENEMY=60 constants;
                             BulletPool (acquire/release/active_count/reset);
                             PlayerBullet(AliveEntity), EnemyBullet(AliveEntity);
                             Weapon (parented to camera); module-level pool singletons;
                             get_enemy_bullet_pool() accessor; reset_bullet_pools() teardown helper.
                             _pooled param deleted. Pool parks at Vec3(0,-10000,0).
  enemy.py                 — ENEMY_HP_DEFAULT/SHOOT_COOLDOWN/DETECTION_RANGE/ATTACK_RANGE/
                             OCCLUSION_INTERVAL TUNE constants; VALID_ENEMY_TYPES tuple;
                             Enemy(AliveEntity): hp/enemy_type/rotation_y params,
                             throttled occlusion raycast, lazy pool import in shoot().
  health_bar.py            — HealthBar(Entity): world-space (is_3d=True) or screen-space;
                             BAR_COLOR_FULL/MID/LOW/BG_COLOR module constants;
                             HealthBar._registry class list (maintained by __init__/on_destroy);
                             on_destroy() explicitly destroys camera.ui text (not cascade-destroyed).
                             No eternal=True anywhere in this file.
  level_editor.py          — LevelEditor(Entity): EDITOR_GRID_SNAPS=(1.0,0.5,0.25,None) constant;
                             snap/undo/redo/multi-select/inspector/hierarchy/gizmos/bookmarks/
                             play-in-editor/asset tray/drag-and-drop placement.
                             _patch_shaders_to_glsl120() duplicated here for standalone runs
                             (compat.py extraction still TODO).
                             _exit_play_mode: sets game.state=MAIN_MENU before try block;
                             except ImportError only (not bare except).
                             ALL persistent editor UI entities (panels, tray tiles, gizmo axes/tips,
                             toolbar buttons, model_preview, spawn marker, ground, hint Text) use
                             eternal=True so scene teardown during play-in-editor cannot destroy them.
                             Level blocks/enemies in self.blocks/self.enemies do NOT use eternal=True —
                             they must be destroyable. _restore_editor_level() rebuilds them from
                             _play_level_snapshot when play mode exits.
                             Standalone runnable: `python Scripts/level_editor.py`
                             SessionLogger (module-level singleton) writes structured log to
                             logs/session_YYYYMMDD_HHMMSS.log on exit (atexit). Log dir auto-created.
  session_logger.py        — SessionLogger: stdlib-only; logger.log(level, msg) / logger.flush().
                             Levels: INFO | WARN | ERROR. Format: [HH:MM:SS.mmm] [LEVEL] message.
                             Instantiated once at module level in level_editor.py as `logger`.
  undo_redo.py             — Command pattern: UndoRedoStack (depth 50) + 6 command types:
                             PlaceEntityCommand, DeleteEntityCommand, MoveEntityCommand,
                             ChangeTextureCommand, ChangeColourCommand, ChangePropertyCommand.
                             _restore_entity() helper sets origin_y=-0.5 for enemy redo.
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

# In Enemy.__init__ — via collision_manager.add() which calls register() internally:
collision_manager.add(self, Layers.ENEMY)

# In BulletPool.acquire() — adds bullet to collision_manager after reset:
collision_manager.add(b, b._layer)   # b._layer = Layers.PLAYER_BULLET or ENEMY_BULLET

# In PlayerBullet.update — single damage authority:
if can_hit(self, hit.entity):
    hit.entity.health -= self.damage
```
`can_hit(a, b)` → `a._collision_layer & b._collision_mask`. Returns `False` for
unregistered entities (walls). **Never** use `can_hit` inside `_swept_blocked`.

`Layers.PICKUP` (bitmask 32) is a forward declaration — no entity registers it yet.

### AliveEntity Lifecycle (replaces `_destroyed` bool)
```python
class MyEntity(AliveEntity):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        collision_manager.add(self, Layers.ENEMY)   # add() calls register() internally

    def update(self):
        if not self.alive:   # guard — update() fires this frame after die()
            return
        if should_die:
            self.die()

    def on_die(self):
        destroy(self.health_bar)   # sub-entities first; AliveEntity.die() calls destroy(self) after on_die
```
`die()` is idempotent. `AliveEntity.die()` calls `collision_manager.remove(self)` then `on_die()`
then `destroy(self)`. Pool bullets override `die()` to return to pool instead of calling `destroy()`.

### Bullet Pool
Module-level singletons in `weapon.py`:
- `_player_bullet_pool = BulletPool(PlayerBullet, size=POOL_SIZE_PLAYER)`  — 30 bullets
- `_enemy_bullet_pool  = BulletPool(EnemyBullet,  size=POOL_SIZE_ENEMY)`   — 60 bullets

Inactive bullets are parked at `BulletPool._PARK = Vec3(0, -10000, 0)`. **Never** toggle
`entity.enabled` on pooled bullets — Panda3D's `unstash()` asserts on re-enable and crashes.

`BulletPool.reset()` must be called during scene teardown (before `main_menu()` sweeps entities).
`reset_bullet_pools()` is the public helper — called by `_clear_gameplay_entities()`.

`BulletPool.active_count()` returns `max(built - free, 0)` — useful for perf debugging.

### Player Collider Dimensions
- `BoxCollider(self, center=(0, 0.75, 0), size=(0.8, 2.5, 0.8))`
- Feet at `entity.y − 0.5`, top at `entity.y + 2.0`
- `SWEPT_OFFSETS = (Vec3(0,-0.4,0), Vec3(0,0.3,0), Vec3(0,0.9,0), Vec3(0,1.5,0), Vec3(0,1.9,0))` — module constant
- `camera.clip_plane_near = 0.01` set at app init

---

## Ursina 8.3.0 Compatibility (macOS / OpenGL 2.1)

**Root cause of black screen after upgrade:** Ursina 8.3.0 set `Entity.default_shader =
unlit_with_fog_shader`, and `Sky()` hardcodes `shader=unlit_shader`. Both use GLSL `#version 130/140`.
macOS OpenGL 2.1 (Panda3D CocoaGraphicsPipe on Apple Silicon) supports GLSL 1.20 at most →
every shader fails to compile → geometry renders black.

**Fix in `main.py`:** `_patch_shaders_to_glsl120()` is called **twice** — once before `Ursina()`,
and once after all window setup (fullscreen/size assignments) because window resize events
can re-initialize shader objects on internal Ursina entities, undoing the first patch.

The patch targets three shaders: `unlit_shader`, `unlit_with_fog_shader`, `text_shader`.
It patches both direct-import instances AND `ursina.shader.imported_shaders` dict entries
(which may be different Python objects due to Ursina's module registration quirk).

Key GLSL 1.20 differences: `attribute`/`varying` instead of `in`/`out`, `texture2D()` instead
of `texture()`, `gl_FragColor` instead of a named `out vec4`.

**Do not** upgrade shaders back to `#version 130+` without verifying a working Core Profile
context exists on the target machine.

**Other 8.3.0 changes accounted for:**
- `window.color` default → black; set explicitly to `color.rgb(50, 50, 60)` after `App()`
- `Sky(texture='sky_default')` → `Sky()` — the `sky_default` asset was removed
- `render.setAntialias()` / `render2d.setAntialias()` deferred to first frame via
  `taskMgr.doMethodLater(0, ...)` — accessing these NodePaths during startup resize crashes Panda3D

The same shader patch is duplicated in `level_editor.py` for standalone runs.
(Extraction to `Scripts/compat.py` is still a TODO — see Tech Debt table.)

---

## Known Tech Debt (Open Items Only)

| Issue | Location | Priority | Notes |
|---|---|---|---|
| No win condition / game-over state | `main.py`, `player_controller.py:124` | **HIGH** | Player HP→0 teleports with `# TODO: call game.trigger_game_over()`. Wire `game.state = WIN` when `len(game.enemies)==0`; show GAME OVER panel on HP≤0. `Game.GAME_OVER` and `game.game_over_screen` now exist — wire up the UI |
| `_patch_shaders_to_glsl120()` copy-pasted in `level_editor.py` | `level_editor.py:1168-1306` | Low | Extract to shared `Scripts/compat.py`; `compat.py` does not exist yet |
| `start_game()` duplicates player teardown from `_clear_gameplay_entities` | `main.py:212-220` | Medium | Inline teardown block repeats logic already in `_clear_gameplay_entities`. Should call `game.return_to_menu()` before respawning, not repeat teardown inline |
| `player_controller.py`: debug collider lines use `eternal=True` | `player_controller.py:76` | Low | `create_collider_visualization()` creates 12 eternal Entity lines (enabled=False). They survive menu transitions. Not harmful while `show_colliders` is always False, but leaks if that flag ever defaults True |
| `player_controller.py`: shoot not gated on `grounded` — can fire in air | `player_controller.py:129-130` | Low | `left mouse down` fires `weapon.shoot()` unconditionally. Original grounded guard was removed in audit. Intentional or bug? Confirm and document |
| `level_editor.py`: `_save_prefs()` has no error handling | `Scripts/level_editor.py` | Low | Write failure silently drops prefs; add `try/except` with `logger.log('ERROR', ...)` |

### Confirmed Fixed (for reference — do not reopen unless code evidence)
| ~~Issue~~ | ~~Location~~ | ~~Notes~~ |
|---|---|---|
| ~~F5 play-in-editor crash — snapshot empty + `_set_editor_ui_visible` AssertionError on dead NodePath~~ | ~~`Scripts/level_editor.py`~~ | **FIXED v1.2.3** — `eternal=True` on all persistent editor UI (panels, tray, gizmo, buttons, preview, spawn marker); snapshot taken first; `_restore_editor_level()` rebuilds editor blocks/enemies from snapshot on play-mode exit; `_set_editor_ui_visible` guarded against destroyed entities |
| ~~Gizmo handles visible through blocks but clicks select the block instead~~ | ~~`Scripts/level_editor.py`~~ | **FIXED v1.2.3** — explicit gizmo raycast in `input()` before selection logic; consumes `left mouse down` and sets `_gizmo_drag_axis` directly |
| ~~Inspector labels not rendering (z-order behind panel background)~~ | ~~`Scripts/level_editor.py`~~ | **FIXED v1.2.3** — `z=-1` added to all label `Text` and `InputField` children of inspector panel so they render in front of the quad |
| ~~Asset tray drag-and-drop places ghost on gizmo/editor entities~~ | ~~`Scripts/level_editor.py`~~ | **FIXED v1.2.3** — `_update_ghost()` now rejects `hovered.name.startswith('editor_')` as a valid placement surface |
| ~~HealthBar uses `eternal=True` on sub-entities~~ | ~~`health_bar.py`~~ | **FIXED 2026-05-22** — all `eternal=True` removed; `on_destroy()` handles camera.ui text explicitly; `_registry` added |
| ~~Crosshair visibility not restored on non-Esc pause paths~~ | ~~`main.py`~~ | **FIXED v1.3** — PlayerHUD.show()/hide() called by PauseMenu; all paths covered |
| ~~`swept_cast()` is dead code~~ | ~~`collision_system.py`~~ | **FIXED v1.3** — deleted |
| ~~`_pooled` param on `PlayerBullet`/`EnemyBullet.__init__` is dead~~ | ~~`weapon.py`~~ | **FIXED v1.3** — deleted from both signatures and BulletPool.acquire |
| ~~`_exit_play_mode` swallows all exceptions~~ | ~~`Scripts/level_editor.py:813`~~ | **FIXED v1.3** — narrowed to `except ImportError` |
| ~~`_exit_play_mode` sets `game.state` after try/except — not set if teardown raises~~ | ~~`Scripts/level_editor.py`~~ | **FIXED 2026-05-22** — `game.state = Game.MAIN_MENU` moved before try block |
| ~~`PlaceEntityCommand.execute()` and `DeleteEntityCommand.undo()` omit `origin_y=-0.5` for enemy redo~~ | ~~`Scripts/undo_redo.py`~~ | **FIXED 2026-05-22** — extracted `_restore_entity()` helper; sets `origin_y` for enemies |
| ~~`level_editor.py`: inline snap tuple; no `EDITOR_GRID_SNAPS` constant~~ | ~~`Scripts/level_editor.py`~~ | **FIXED 2026-05-22** — `EDITOR_GRID_SNAPS = (1.0, 0.5, 0.25, None)` added at module level |
| ~~`_build_level_data()` has dead r/g/b_val variables computed but unused~~ | ~~`Scripts/level_editor.py`~~ | **FIXED 2026-05-22** — removed; `actual_color` already captured the same value |
| ~~Global `update()` does per-frame `isinstance(e, HealthBar)` scan over `scene.entities`~~ | ~~`main.py`~~ | **FIXED 2026-05-22** — replaced with `HealthBar._registry` iteration |
| ~~`invoke()` time_scale=1 race on rapid re-pause~~ | ~~`main.py`~~ | **FIXED v1.3** — deleted delayed invoke, immediate assignment is sufficient |
| ~~`game.enemies` not reset on teardown exception~~ | ~~`Scripts/game.py`~~ | **FIXED 2026-05-22** — `finally` block in `return_to_menu()` now resets all ref attrs |
| ~~`game.win_screen`/`game.game_over_screen` not tracked for teardown~~ | ~~`Scripts/game.py`~~ | **FIXED 2026-05-22** — both initialized to `None` in `__init__`, cleared in `return_to_menu()` finally |
| ~~`collision_system.py` missing `__all__`, docstrings, audit header~~ | ~~`Scripts/collision_system.py`~~ | **FIXED 2026-05-22** — `__all__` added, all public functions documented, audit header added |
| ~~`player_controller.py`: inline SWEPT_OFFSETS magic numbers~~ | ~~`player_controller.py`~~ | **FIXED 2026-05-22** — extracted to module-level `SWEPT_OFFSETS` constant |
| ~~`player_controller.py`: duplicate `health_bar.value` assignment per frame~~ | ~~`player_controller.py`~~ | **FIXED 2026-05-22** — removed redundant second assignment |
| ~~`player_controller.py`: no `collision_manager.remove` on teardown~~ | ~~`player_controller.py`~~ | **FIXED 2026-05-22** — `on_destroy()` calls `collision_manager.remove(self)` |
| ~~`player_controller.py`: dead `generate_raycast_points` / `draw_raycast_visuals` machinery~~ | ~~`player_controller.py`~~ | **FIXED 2026-05-22** — removed; debug lines for collider box kept |
| ~~`weapon.py`: hardcoded pool sizes 30/60~~ | ~~`weapon.py`~~ | **FIXED 2026-05-22** — extracted to `POOL_SIZE_PLAYER = 30`, `POOL_SIZE_ENEMY = 60` |
| ~~`weapon.py`: no `BulletPool.active_count()` method~~ | ~~`weapon.py`~~ | **FIXED 2026-05-22** — added; returns `max(built - free, 0)` |
| ~~`_build_level_data()` silently drops block scale — data loss on save~~ | ~~`Scripts/level_editor.py`~~ | **FIXED v1.3** — `'scale': [round(e.scale_x,4), ...]` added; both loaders read back with `get('scale', [1,1,1])` default |

Log new footguns → `brain/Gotchas.md` in the vault.

---

## Key Rules

### Hard Constraints (violating these crashes or breaks invariants)
1. **Never** check `entity.name == 'enemy'` for damage dispatch — use `can_hit(self, entity)` in
   bullet updates, or `isinstance(entity, Enemy)` only where type identity is truly needed.
   (Scene cleanup code checking `e.name in ['level_block', 'level_enemy']` is fine — those are
   structural names set at spawn, not enemy-type dispatch.)
2. **Never** pass classes to `raycast(ignore=...)` — always pass instances filtered from `scene.entities`
   or from `collision_manager.query_layer()`.
3. **Never** call `destroy()` on an AliveEntity directly — call `self.die()` (AliveEntity pattern).
   `die()` is idempotent and handles collision unregistration before destroy.
6. **`swept_cast()` does not exist** — it was deleted. Player `_swept_blocked` uses raw `raycast().hit`.
   Do not re-add swept_cast or any new global sweep function.
7. `time` in Ursina scope is Panda3D's clock. Use `import time as _time; _time.time()` for wall-clock.
9. Pool bullets must not have `enabled` toggled — use position parking (`y = -10000`) instead.
   Panda3D's `unstash()` asserts on re-enable and crashes.
10. **Ursina 8.3.0 shader patch must run before any `Entity` is created** (and again after window setup).
    `_patch_shaders_to_glsl120()` must be the first call in `__main__`, before `Ursina()`.

### Conventions
4. Use `except Exception:` not bare `except:`.
5. Bullet-vs-enemy/player AABB loops in `main.py` are **deleted** — don't re-add them.
8. `destroy(entity)` is **deferred** — entity stays in `scene.entities` until end-of-frame flush.
   Use `die()` for managed AliveEntities; `destroy()` is fine for plain UI entities.

---

## Common Workflows

### Adding a New Enemy Type
1. Extend `Enemy` (which extends `AliveEntity`) in `enemy.py`
2. Add the new type string to `VALID_ENEMY_TYPES` in `enemy.py`
3. Call `collision_manager.add(self, Layers.ENEMY)` in `__init__` (do not call `register()` directly)
4. Override `on_die()` to destroy sub-entities (health bar, particles, etc.) before `super().on_die()`
   — `AliveEntity.die()` calls `on_die()` then `destroy(self)`, so sub-entities must be cleaned in `on_die()`
5. Update `level.json` schema and `load_level()` in `main.py`
6. Add spawn handling in `start_game()` inside `main_menu()`

### Adding a New Weapon / Bullet Type
1. Add a new `Layers` bitmask entry in `collision_system.py`
2. Update `COLLISION_MATRIX` with what the new layer hits
3. Create bullet class extending `AliveEntity` in `weapon.py`; set `_layer` class attribute
4. Override `die()` to return to pool instead of calling `destroy()`
5. Add `POOL_SIZE_X` constant and a module-level `BulletPool` singleton in `weapon.py`
6. Expose a `get_X_bullet_pool()` accessor for other modules (breaks circular imports)
7. Add pool to `reset_bullet_pools()` so scene teardown clears it

### Adding a New Level Feature
1. Add entry type to `level.json` schema
2. Handle the new type in `load_level()` (main.py) and `load_existing_level()` (level_editor.py)
3. Add cleanup to `_clear_gameplay_entities()` and the entity-clearing loop in `main_menu()`

### Using the Level Editor (v1.2+)
```
python Scripts/level_editor.py
# Click-hold tile in bottom tray  — drag asset; ghost follows mouse
# Release over viewport           — place entity (block or enemy per tile type)
# Release over tray / Esc         — cancel drag, no placement
# Scroll wheel over tray          — scroll tile list horizontally
# Shift + left click              — add/remove from selection (multi-select)
# Right mouse drag                — box-select rectangle
# Shift + click on entity         — select it (inspector + hierarchy update)
# Delete                          — remove all selected entities
# Ctrl+Z / Ctrl+Y (or Ctrl+Shift+Z) — undo / redo (depth 50)
# Ctrl+S                          — save level.json (clears undo stack + saves prefs)
# G or Snap button                — cycle grid snap: 1.0 → 0.5 → 0.25 → Off
# Drag X/Y/Z gizmo axis           — move selection along that axis (snapped)
# Ctrl+1 through Ctrl+5           — save camera bookmark to slot
# 1 through 5                     — recall camera bookmark
# F5 / Esc                        — toggle play-in-editor mode
```
`editor_prefs.json` persists bookmarks and snap setting across sessions.
All editor-only entities are named `editor_*` — excluded from level save/load.
Green `editor_player_spawn` cube marker visible at (0, 1.4, 0) — shows player spawn; no collider, not selectable, not saved.
F5 saves editor camera position/rotation before entering play mode and restores it on exit.

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
# Kill all enemies → WIN state triggers (once implemented)
# Player HP → 0 → game over screen (once implemented); currently teleports to origin
```

### Performance Investigation Order
```
1. window.fps_counter — baseline FPS drop identification
2. Panda3D PStats (import pstats) — CPU timeline, draw call count
3. Inspect scene.entities length — leaked entities inflate raycast cost every frame
4. Profile _swept_blocked — 5 raycasts × N movement frames; most expensive per-frame cost
5. Profile CollisionManager.update() — O(entities) per frame; ensure pool bullets stay parked
6. Watch HealthBar._registry — must iterate registry list, not scene.entities
7. BulletPool.active_count() — check for pool exhaustion (returns None on acquire)
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
- `game.hud` — current `PlayerHUD` instance or `None` (set in `start_game()`, cleared in `return_to_menu()`)
- `game.win_screen` — win screen entity or `None`
- `game.game_over_screen` — game over screen entity or `None`
- `game.state` — one of `Game.MAIN_MENU`, `Game.PLAYING`, `Game.PAUSED`, `Game.RETURNING_TO_MENU`, `Game.WIN`, `Game.GAME_OVER`

Use `game.state == Game.PLAYING` guards in `input()` and `update()` instead of `player` existence checks.
`game.return_to_menu()` is the **only** code path that calls `_clear_gameplay_entities()`.
`return_to_menu()` uses a `finally` block to reset all refs — teardown exceptions don't leave stale state.

---

## Scene Transitions
Return-to-menu: `PauseMenu.return_to_main_menu()` → `game.return_to_menu()` → `main_menu()`.
`HealthBar` sub-entities must **not** use `eternal=True` (blocks teardown).
`_clear_gameplay_entities()` calls `reset_bullet_pools()` first — before any referenced entity is destroyed.

---

## Roadmap

### v1.3 — Ship a playable demo (current focus)
- [ ] Win screen — `game.state = WIN` when `len(game.enemies) == 0`
- [ ] Game Over screen — triggered on player HP ≤ 0 instead of silent teleport
- [ ] PyInstaller macOS `.app` build, documented in README
- [ ] One curated 5-enemy level saved as `levels/v1.json`
- [ ] 1 shot SFX + 1 ambient track (CC-0 from freesound/Pixabay)
- [ ] itch.io page with 2 screenshots + 30-second clip
- [ ] Extract shader patch to `Scripts/compat.py`

### Near-term housekeeping
- [x] Delete `swept_cast()` dead code from `collision_system.py`
- [x] Delete `_pooled` dead param from `PlayerBullet` and `EnemyBullet`
- [x] Extract `SWEPT_OFFSETS` constant in `player_controller.py`
- [x] Fix duplicate `health_bar.value` update in `Player.update()`
- [x] Add `on_destroy()` to `Player` — unregisters from `collision_manager`
- [x] Add `POOL_SIZE_PLAYER`/`POOL_SIZE_ENEMY` constants to `weapon.py`
- [x] Add `BulletPool.active_count()` method
- [x] Add `BulletPool.reset()` + `reset_bullet_pools()` — called during scene teardown
- [x] Cache `HealthBar._registry` — replaces per-frame `scene.entities` scan
- [x] Remove `eternal=True` from `HealthBar`; `on_destroy()` handles camera.ui text
- [x] PlayerHUD — consolidate crosshair, hint text, health bar ref; fix hint-text leak and crosshair restore bug
- [x] Add TUNE constants to `enemy.py` (HP, cooldown, ranges, occlusion interval)
- [x] Add `VALID_ENEMY_TYPES` guard in `Enemy.__init__`
- [x] `_restore_entity()` helper in `undo_redo.py` — fixes enemy redo origin_y
- [x] `EDITOR_GRID_SNAPS` constant in `level_editor.py`
- [ ] Wire `game.trigger_game_over()` — replace teleport in `Player.update()`

### Engine features (post-demo)
- [x] Collision bitmask system — `Layers` registry + `can_hit()`
- [x] Object pooling for bullets — `BulletPool` eliminates per-shot allocation
- [x] AliveEntity lifecycle — idempotent `die()` replaces `_destroyed` bool
- [x] CollisionManager spatial grid — `query_layer()` / `query_near()`
- [x] Game state machine — `Scripts/game.py`, replaces module-level globals
- [x] Level editor: snap, undo/redo, multi-select, inspector, hierarchy, gizmos, bookmarks, play-in-editor, asset tray, drag-and-drop placement
- [ ] Pluggable enemy behaviour trees — patrol / attack / flee state composition
- [ ] Trigger/zone system — volume entry/exit callbacks
- [ ] Weapon inventory API — multi-weapon, ammo pickup, switch animations

---

## Obsidian Mind Integration

The vault (`breferrari/obsidian-mind`) is the persistent memory layer across sessions.
**Vault location:** confirm path in `brain/North Star.md` — do not assume a working directory.

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
