# CLAUDE.md — Ivan's 3D Engine Operating Manual

@.claude/skills/karpathy-guidelines/SKILL.md

**Ivan's 3D Engine** is a first-person shooter built on Ursina (Python / Panda3D).
This file is the authoritative operating manual loaded at the start of every Claude Code session.
It is deliberately lean — deep detail lives in the vault (`obsidian-mind-main/`); this file is an
index of hard invariants plus trigger-based pointers into that detail.

---

## Project Identity

| Field            | Value                                                                 |
|------------------|-----------------------------------------------------------------------|
| Version          | 1.6 (see `CHANGELOG.md`)                                              |
| Engine           | Ursina 8.3.0 (Panda3D 1.10.16)                                        |
| Language         | Python 3.10+                                                          |
| Genre            | First-person shooter                                                  |
| Entry point      | `main.py`                                                             |
| Level format     | JSON (`level.json`)                                                   |
| Platform target  | macOS (OpenGL 2.1 / GLSL 1.20 — shader patch is a Hard Constraint)    |

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
| Skill               | Path                                                                                             |
|----------------------|---------------------------------------------------------------------------------------------------|
| `karpathy-coder`    | `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/engineering/karpathy-coder/SKILL.md`        |
| `senior-architect`  | `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/engineering-team/senior-architect/SKILL.md` |
| `code-reviewer`     | `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/engineering-team/code-reviewer/SKILL.md`    |
| `tech-debt-tracker` | `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/engineering/tech-debt-tracker/SKILL.md`     |

### Load When Relevant
| Task                                | Skill                     | Path                                                                                                    |
|--------------------------------------|---------------------------|----------------------------------------------------------------------------------------------------------|
| New feature design / roadmap        | `product-manager-toolkit` | `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/product-team/product-manager-toolkit/SKILL.md` |
| Level design, balance experiments   | `experiment-designer`     | `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/product-team/experiment-designer/SKILL.md`     |
| Writing changelogs / release notes  | `changelog-generator`     | `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/engineering/changelog-generator/SKILL.md`      |
| Cutting a release / version bump    | `release-manager`         | `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/engineering/release-manager/SKILL.md`          |
| Scoping a fix to minimum change     | `focused-fix`             | `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/engineering/focused-fix/SKILL.md`              |
| Performance profiling / FPS drops   | `performance-profiler`    | `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/engineering/performance-profiler/SKILL.md`     |
| Scope / cut / ship decisions        | `founder-coach`           | `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/c-level-advisor/founder-coach/SKILL.md`        |
| Brain dump → roadmap / tasks        | `capture`                 | `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/productivity/capture/SKILL.md`                 |
| Prompt engineering for tooling      | `senior-prompt-engineer`  | `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/engineering-team/senior-prompt-engineer/SKILL.md` |

---

## If you're about to...

- **Touch collision, pools, or `AliveEntity` lifecycle** → read [Core Architecture](#core-architecture) below, then `brain/Patterns.md` + `brain/Gotchas.md` in the vault
- **Touch any `editor_*.py` module** → read `reference/module-map.md` in the vault (editor section)
- **Touch any other module** (`game.py`, `weapon.py`, `enemy.py`, etc.) → read `reference/module-map.md`
- **Add a new enemy / weapon / level-feature type, or use the level editor** → read `reference/workflows.md`
- **Hit an Ursina/Panda3D API surprise** (colors, InputField, pick rays, resize, `Text`, NodePath teardown) → check `brain/Gotchas.md` before debugging from scratch
- **Check what's still open** → read `work/active/v1.6-fix-backlog.md` (do not re-list items here)
- **Check the roadmap / what shipped** → read `brain/North Star.md` (do not duplicate the roadmap here)
- **Wonder why something was built a certain way** → check `brain/Key Decisions.md`

Vault root: `obsidian-mind-main/`. See **Obsidian Mind Integration** below for the full workflow;
`obsidian-mind-main/CLAUDE.md` is the authoritative reference for vault conventions.

---

## Core Architecture

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

### Player Collider Dimensions
- `BoxCollider(self, center=(0, 0.75, 0), size=(0.8, 2.5, 0.8))`
- Feet at `entity.y − 0.5`, top at `entity.y + 2.0`
- `SWEPT_OFFSETS = (Vec3(0,-0.4,0), Vec3(0,0.3,0), Vec3(0,0.9,0), Vec3(0,1.5,0), Vec3(0,1.9,0))` — module constant
- `camera.clip_plane_near = 0.01` set at app init

### Game State Machine (`Scripts/game.py`)
`game = Game()` is the module-level singleton. Import with `from Scripts.game import game, Game`.
States: `Game.MAIN_MENU`, `PLAYING`, `PAUSED`, `RETURNING_TO_MENU`, `WIN`, `GAME_OVER`.
Use `game.state == Game.PLAYING` guards in `input()`/`update()` instead of `player` existence checks.
`game.return_to_menu()` is the **only** code path that calls `_clear_gameplay_entities()`; it uses
a `finally` block so teardown exceptions don't leave stale state.

### Circular Import Rule
`weapon.py ↔ enemy.py` circularity is broken by keeping `EnemyBullet` in `weapon.py` and having
`enemy.py` reach the pool via a lazy import inside `shoot()`:
```python
def shoot(self):
    from Scripts.weapon import get_enemy_bullet_pool   # lazy — only at call time
```
Never move `EnemyBullet` back to `enemy.py`.

---

## Hard Constraints (violating these crashes or breaks invariants)

Rationale and repro detail for most of these live in `brain/Gotchas.md` — this list is bare rules only.

1. **Never** check `entity.name == 'enemy'` for damage dispatch — use `can_hit(self, entity)` or `isinstance(entity, Enemy)`. (Structural name checks like `e.name in ['level_block', 'level_enemy']` for scene cleanup are fine.)
2. **Never** pass classes to `raycast(ignore=...)` — pass instances filtered from `scene.entities` or `collision_manager.query_layer()`.
3. **Never** call `destroy()` on an `AliveEntity` directly — call `self.die()`.
4. `swept_cast()` does not exist — it was deleted. Do not re-add it or any new global sweep function.
5. `time` in Ursina scope is Panda3D's clock — use `import time as _time; _time.time()` for wall-clock.
6. Pool bullets must never have `enabled` toggled — use position parking (`y = -10000`) instead.
7. Shader patch (`compat.patch_shaders_to_glsl120()`) must run before any `Entity` is created, and again after window setup. Must be the first call in `__main__`, before `Ursina()`.
8. Gizmo/UI render-bin calls (`setBin`/`setDepthTest`/`setDepthWrite`) go on the Entity directly, never on `entity.node()`.
9. `self._tool` in `LevelEditor` is always `'move'` or `'place'` — gate placement branches accordingly.
10. **Never** read `e.name` (or any NodePath property) on an entity destroyed earlier in the same synchronous call — filter with `_is_live(e)` (`not e.is_empty()`) first.
11. Gameplay entity `input()` handlers must early-return when `game.state != Game.PLAYING`.
12. Camera lens aspect ratio must be explicitly refreshed on window resize — `_apply_layout()` calls `set_aspect_ratio` every time it runs; all dynamic UI sizing must be relative, never hardcoded.
13. `window.on_resize` is never called by Ursina — don't set it. Wrap `window.update_aspect_ratio` instead.
14. **Never** set a `Text` entity's `.text` to `''` or a bare `'<'`/`'>'` with tag parsing on — both crash `Text.align()`. Use `enabled=False/True` to hide/show; pass `use_tags=False` for literal angle-bracket glyphs.

### Conventions
- Use `except Exception:`, not bare `except:`.
- Bullet-vs-enemy/player AABB loops in `main.py` are deleted — don't re-add them.
- `destroy(entity)` is deferred — entity stays in `scene.entities` until end-of-frame flush. Use `die()` for managed `AliveEntity`s; `destroy()` is fine for plain UI entities.

---

## Known Tech Debt

Tracked exclusively in `work/active/v1.6-fix-backlog.md` (vault) — do not duplicate items here.
Log new footguns to `brain/Gotchas.md` in the vault.

---

## Roadmap

Tracked exclusively in `brain/North Star.md` (vault) — do not duplicate the roadmap here.

---

## Obsidian Mind Integration

The vault (`obsidian-mind-main/`) is the persistent memory layer across sessions — every session
builds on the last. Its own `CLAUDE.md` is the authoritative reference for vault structure, note
types, linking conventions, and slash commands (`/om-standup`, `/om-wrap-up`, etc.); don't
duplicate that content here.

### Session Start
1. Read `brain/North Star.md` — current dev focus
2. Check `work/Index.md` — active feature work
3. Scan `brain/Gotchas.md` — known Ursina/Panda3D footguns before touching collisions or pools
4. Load the skill stack for today's task

### Session End
Run `/wrap-up` or at minimum: log new footguns/patterns, archive completed features to
`work/archive/YYYY/`, update `work/Index.md`, log wins to `perf/Brag Doc.md`, update
`brain/North Star.md` if priority shifted. Every new vault note must link to at least one
existing note (orphans are bugs).
