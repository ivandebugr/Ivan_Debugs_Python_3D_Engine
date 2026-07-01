# CLAUDE.md — Ivan's 3D Engine Operating Manual

@.claude/skills/karpathy-guidelines/SKILL.md

**Ivan's 3D Engine** is a first-person shooter built on Ursina (Python / Panda3D).
This file is the authoritative operating manual loaded at the start of every Claude Code session.

---

## Project Identity

| Field            | Value                                                                 |
|------------------|-----------------------------------------------------------------------|
| Version          | 1.4 (see `CHANGELOG.md`)                                              |
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
Loaded from `obsidian-mind-main/`. Vault root: check `brain/North Star.md` for current location.
The vault is the persistent memory layer — every session builds on the last.
See **Obsidian Mind Integration** below.

---

## Architecture Overview

### Module Map
```
main.py                    — App init, window setup, _patch_shaders_to_glsl120() (called twice),
                             _clear_gameplay_entities(), PlayerHUD, PauseMenu, EndScreen,
                             load_level(), main_menu(), global update()/input()
                             PlayerHUD: owns crosshair, hint_text, health_bar ref (not lifetime);
                             stored as game.hud; show()/hide() toggle all elements together.
                             EndScreen(title): fullscreen overlay (parent=camera.ui) shown on WIN/
                             GAME_OVER. Bg + 2 Text children parented to self → destroy() cascades.
                             Global update() triggers WIN when count_layer(Layers.ENEMY)==0 in PLAYING.
                             Global input() handles R during WIN/GAME_OVER → return_to_menu()+main_menu().
Scripts/
  game.py                  — Game state-machine class (MAIN_MENU/PLAYING/PAUSED/RETURNING_TO_MENU/
                             WIN/GAME_OVER), module-level singleton `game = Game()`;
                             no Ursina import at class-def time.
                             Tracks: player, enemies, pause_menu, hud, win_screen, game_over_screen.
                             return_to_menu() calls _clear_gameplay_entities() inside try/finally.
                             trigger_win() / trigger_game_over(): idempotent (only fire from PLAYING),
                             set state, freeze time_scale, surface mouse, hide HUD, build EndScreen.
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
                             **Viewmodel camera**: VIEWMODEL_MASK=BitMask32.bit(7);
                             _setup_viewmodel_camera() (module singleton, idempotent, called from
                             Weapon.__init__) clears bit 7 from main camera mask + adds a 2nd Panda
                             camera (reuses base.camLens, parented to base.cam) on its own display
                             region at sort 15. Gun is routed onto bit 7 only + depth-test/write OFF
                             → renders in a later pass, on top of world, no wall clipping. Region
                             clear-depth stays OFF (clearing blanked the world on macOS GL 2.1).
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
                             play-in-editor/drag-and-drop placement via asset browser Models tab.
                             **Tool modes**: self._tool = 'move'|'place'. Move: left-click selects only.
                             Place: left-click on collidable non-editor surface places a block.
                             [↖ Move]/[+ Place] toolbar buttons call _set_tool(mode).
                             **Inspector**: 6 fields (pos_x/y/z, scl_x/y/z only). HP/rotation/colour/
                             texture/enemy_type still round-trip through level.json — display-only
                             simplification. Labels use world_scale=Vec3(15,15,1) + setBin('fixed',41)
                             on the Entity (NOT scale= which inherits tiny panel scale → microscopic).
                             **Delete key**: accepts 'delete' AND 'backspace' (macOS). Editor
                             placeholders use destroy() not die() — they are plain Entity, not AliveEntity.
                             **_apply_layout()**: repositions all border-anchored UI from window.aspect_ratio;
                             invoked via wrapped window.update_aspect_ratio. Also refreshes camera lens.
                             Toolbar widths: _TOOLBAR_BTN_W_BASE × min(1, aspect/_TOOLBAR_REF_ASPECT).
                             **Hierarchy panel**: single row-position formula _hier_row_y(visual_index) —
                             NEVER add a second inline y formula or scroll indicator will drift.
                             _hier_visual_rows() is the layout model (header/row tuples). Transient
                             row buttons/swatches are NOT eternal=True — destroy() is a no-op on
                             eternal entities (ursina/destroy.py:27), they would leak on every rebuild.
                             Search box (_hier_search_field): live filter via on_value_changed.
                             Collapse marker: ASCII [+]/[-] — OpenSans has no triangle glyphs.
                             **Toolbar**: horizontal strip above inspector. _TOOLBAR_Y=0.475. No ▶ glyph.
                             Toolbar buttons: play_button text always black; Move/Place black-when-active.
                             Stats strip (_stats_text): 'entities: N  colliders: N', refreshed ~1/s.
                             **Asset browser**: _build_asset_browser(), full-width bottom strip
                             (_BROWSER_Y=-0.40, _BROWSER_H=0.20). Tabs: Textures|Models|Sounds.
                             Built-in types (Cube/Stone/Metal/Wood/Enemy) are synthetic Models-tab
                             entries via BUILTIN_MODELS. Textures load via Texture(Path(path)) —
                             raw path strings fail silently. _is_over_browser() suppresses EditorCamera zoom.
                             **eternal=True**: ALL persistent editor UI uses eternal=True (survives
                             play-in-editor teardown). Level blocks/enemies do NOT — they must be
                             destroyable. _restore_editor_level() rebuilds from _play_level_snapshot.
                             _exit_play_mode: sets game.state=MAIN_MENU before try; except ImportError only.
                             Theme: _THEME_* constants (0–1 floats). Scroll-bar rgba(0.78,0.78,0.78,0.47).
                             Standalone runnable: `python Scripts/level_editor.py`
                             SessionLogger singleton → logs/session_YYYYMMDD_HHMMSS.log on exit (atexit).
  session_logger.py        — SessionLogger: stdlib-only; logger.log(level, msg) / logger.flush().
                             Levels: INFO | WARN | ERROR. Format: [HH:MM:SS.mmm] [LEVEL] message.
                             Instantiated once at module level in level_editor.py as `logger`.
  undo_redo.py             — Command pattern: UndoRedoStack (depth 50) + 6 command types:
                             PlaceEntityCommand, DeleteEntityCommand, MoveEntityCommand,
                             ChangeTextureCommand, ChangeColourCommand, ChangePropertyCommand.
                             _restore_entity() helper sets origin_y=-0.5 for enemy redo.
  level_io.py              — Canonical level data loader. load_level_data(path_or_list) returns
                             normalised list of entity dicts with all fields filled (position,
                             rotation, scale, colour, texture; enemies also hp/enemy_type/rotation_y).
                             Single source of truth — replaces 4 duplicate parsers in main.py
                             and level_editor.py. Owns parsing only; Entity construction stays
                             at call sites (placeholder vs editor entity vs real Enemy/Player).
  asset_registry.py        — v1.3 asset pipeline Step 1. Pure I/O layer, ZERO framework
                             dependencies (no Ursina, no Panda3D, no main/level_editor imports).
                             AssetRegistry scans assets/textures|models|sounds → {name: path}
                             manifests (self.textures/models/sounds); persists assets/manifest.json
                             on every rebuild(). Startup loads from manifest.json cache when recorded
                             mtimes still match disk (skips full rescan), else rebuilds.
                             get_texture_path/get_model_path/get_sound_path(name) -> str|None.
                             register_callback(category, fn) + poll() drive hot-reload: poll()
                             diffs os.stat().st_mtime per tracked file, fires fn(name, path) on change
                             (no background thread — editor calls poll() on a 2s invoke timer in a
                             later step). Module-level singleton `asset_registry`. All file I/O wrapped
                             in `except Exception` — a single bad file is skipped, never crashes.
                             assets/manifest.json is gitignored; folders kept via .gitkeep.
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

`_patch_shaders_to_glsl120()` is called **twice**: once before `Ursina()`, and once after all
window setup. Targets three shaders: `unlit_shader`, `unlit_with_fog_shader`, `text_shader` —
both direct-import instances AND `ursina.shader.imported_shaders` dict entries.
**Do not** upgrade shaders back to `#version 130+` without a verified Core Profile context.
The same patch is duplicated in `level_editor.py` for standalone runs (compat.py TODO).

**Other 8.3.0 changes:**
- `window.color` default → black; set to `color.rgb(0.196, 0.196, 0.235)` (≈50,50,60 in 0–1) after `App()`
- `Sky(texture='sky_default')` → `Sky()` — asset removed
- `render.setAntialias()` / `render2d.setAntialias()` deferred to first frame via `taskMgr.doMethodLater(0, ...)`

**Ursina 8.3.0 API footguns** (see `brain/Gotchas.md` for full context):
- **`color.rgb()` expects 0–1 floats.** `color.rgb(80,120,200)` clamps to white. Use `color.rgb32()` for 0–255.
- **`InputField.submit_on` defaults to `[]`.** Set `field.submit_on = ['enter']`. Callback must be no-arg.
- **Pick ray: `camera.lens.extrude`, not `camera.forward`.** See `LevelEditor._cursor_ray()`.

---

## Known Tech Debt (Open Items Only)

| Issue | Location | Priority | Notes |
|---|---|---|---|
| `_patch_shaders_to_glsl120()` copy-pasted in `level_editor.py` | `level_editor.py:1168-1306` | Low | Extract to shared `Scripts/compat.py`; `compat.py` does not exist yet |
| `start_game()` duplicates player teardown from `_clear_gameplay_entities` | `main.py:212-220` | Medium | Inline teardown block repeats logic already in `_clear_gameplay_entities`. Should call `game.return_to_menu()` before respawning, not repeat teardown inline |
| `player_controller.py`: debug collider lines use `eternal=True` | `player_controller.py:76` | Low | `create_collider_visualization()` creates 12 eternal Entity lines (enabled=False). They survive menu transitions. Not harmful while `show_colliders` is always False, but leaks if that flag ever defaults True |
| `player_controller.py`: shoot not gated on `grounded` — can fire in air | `player_controller.py:129-130` | Low | `left mouse down` fires `weapon.shoot()` unconditionally. Original grounded guard was removed in audit. Intentional or bug? Confirm and document |
| `level_editor.py`: `_save_prefs()` has no error handling | `Scripts/level_editor.py` | Low | Write failure silently drops prefs; add `try/except` with `logger.log('ERROR', ...)` |

Full fix history (root causes, diagnosis, verification) lives in `CHANGELOG.md` — every entry in
the table above has a corresponding dated changelog entry. Do not reopen a fixed item without new
code evidence; do not duplicate fix history back into this file.

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
10. **Shader patch must run before any `Entity` is created** (and again after window setup).
    `_patch_shaders_to_glsl120()` must be the first call in `__main__`, before `Ursina()`.
11. **Gizmo render-bin: call `setBin` / `setDepthTest` / `setDepthWrite` on the Entity directly**
    (Ursina's `Entity` subclasses `NodePath`). Never call on `entity.node()` — that returns
    `PandaNode` which has no `setBin`. Pattern: `tip.setBin('fixed', 100); tip.setDepthTest(False);
    tip.setDepthWrite(False)`. Bin 100 ensures 3D handles render above all other 3D geometry.
12. **`self._tool` in LevelEditor** — always `'move'` or `'place'`. Gate placement branches
    with `if self._tool == 'place':`. Move mode must never place entities on left-click.
13. **Never read `e.name` (or any NodePath property) on an entity destroyed earlier in the
    same synchronous call.** `destroy()` empties the NodePath immediately but defers list
    removal to next frame — `scene.entities[:]` snapshots still contain emptied NodePaths.
    `getName()` on an empty NodePath fires a C++ assertion that `except Exception` cannot catch.
    Always filter with `_is_live(e)` (`not e.is_empty()`) **before** touching `.name` in every
    sweep loop in `_clear_gameplay_entities`, `main_menu`, and `load_level`.
14. **Gameplay entity `input()` handlers must early-return when `game.state != Game.PLAYING`.**
    Ursina runs `__main__.input(key)` first (which may destroy gameplay entities via
    `return_to_menu()`), then continues the *same* dispatch into the per-entity loop calling
    `entity.input(key)` on already-destroyed-but-not-yet-flushed entities. Acting on those
    (e.g. `self.position = …`) raises `entity has been destroyed by: …`. `Player.input()`
    gates on `game.state == Game.PLAYING` for exactly this reason.

15. **Camera lens aspect ratio must be explicitly refreshed on window resize.** `_apply_layout()`
    calls `camera.perspective_lens.set_aspect_ratio(aspect)` every time it runs.
    Every dynamic UI element's size/position must be computed relative to available space inside
    `_apply_layout()`, never hardcoded. Toolbar widths: `_TOOLBAR_BTN_W_BASE × min(1, aspect / _TOOLBAR_REF_ASPECT)`.
16. **`window.on_resize` is never called by Ursina** — setting it is dead code. Wrap
    `window.update_aspect_ratio` instead; the wrapped version calls the original then `_apply_layout()`.
17. **Never set a `Text` entity's `.text` to `''` or to a bare `'<'`/`'>'` with tag parsing on.**
    Both cases leave `Text.align()` with an empty linewidths list → `IndexError` (C++ cannot catch).
    Use `enabled=False/True` to hide/show; pass `use_tags=False` for literal `<`/`>` glyphs.

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

### Using the Level Editor (v1.3+)
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
# Kill all enemies → WIN screen appears; R returns to main menu cleanly
# Player HP → 0 → GAME OVER screen appears; R returns to main menu cleanly
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

v1.4 shipped (enemy behaviour trees). v1.5 (trigger/zone system + weapon inventory API) in progress. Full history: `brain/North Star.md`.

### Open items — v1.3 remainder (before itch.io)
- [ ] PyInstaller macOS `.app` build, documented in README
- [ ] One curated 5-enemy level saved as `levels/v1.json`
- [ ] 1 shot SFX + 1 ambient track (CC-0 from freesound/Pixabay)
- [ ] itch.io page with 2 screenshots + 30-second clip
- [ ] Extract shader patch to `Scripts/compat.py`

### Open items — post-demo engine
- [x] Pluggable enemy behaviour trees — patrol / attack / flee state composition (v1.4, shipped 2026-06-30)
- [ ] Trigger/zone system — volume entry/exit callbacks (v1.5, in progress)
- [ ] Weapon inventory API — multi-weapon, ammo pickup, switch animations (v1.5, in progress)

---

## Obsidian Mind Integration

The vault (`obsidian-mind-main/`) is the persistent memory layer across sessions.
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
