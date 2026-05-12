# CLAUDE.md

## Project
Ursina Engine (Python) first-person shooter. Entry point: `main.py`.

## Design Skills
Before writing any code, read and apply these skills:

1. **Senior Fullstack Skill:** `/Users/ivanrybak/driftfix/SKILL.md`
2. **karpathy-coder Skill:** `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/claude-skills-main 2 copy/engineering/karpathy-coder/skills/karpathy-coder/SKILL.md`
3. **senior-architect Skill:** `/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/claude-skills-main 2 copy/engineering-team/senior-architect/SKILL.md`
4. **code-reviewer Skill:**`/Users/ivanrybak/Documents/python_projects/Sky_Jumper/ivans_3d_engine/claude-skills-main 2 copy/engineering-team/code-reviewer/SKILL.md`

## Structure
```
main.py                      # App init, global update loop, scene management
Scripts/
  collision_system.py        # Layers registry, AliveEntity mixin, CollisionManager singleton
  player_controller.py       # Player movement, multi-ray collision sweeps
  weapon.py                  # Weapon, PlayerBullet, EnemyBullet, BulletPool
  enemy.py                   # Enemy entity (EnemyBullet lives in weapon.py)
  health_bar.py              # HealthBar UI entity
  level_editor.py            # In-engine level editor
```

## Collision architecture (DO NOT add a second system)
- **Projectiles** → swept raycast inside `PlayerBullet.update` / `EnemyBullet.update` (both in `weapon.py`). This is the **single authority** for bullet damage. Do not add a second AABB loop in `main.py`.
- **Player movement** → five-height swept raycasts in `player_controller.py` (`_swept_blocked`), cast *before* moving. Offsets `[-0.4, +0.3, +0.9, +1.5, +1.9]` relative to entity origin cover feet to forehead. Uses raw `raycast().hit` — does **not** filter through `can_hit` because walls are unregistered entities.
- There is no active AABB broad-phase; `CollisionManager` is a spatial index for queries, not a hit resolver.

## Bitmask layer registry (`collision_system.py`)
Entities register themselves on construction; no file needs to import another to make collision decisions.

```python
from Scripts.collision_system import Layers, register, can_hit

register(self, Layers.ENEMY)   # in Enemy.__init__

# In PlayerBullet.update — replaces isinstance(hit.entity, Enemy):
if can_hit(self, hit.entity):
    hit.entity.health -= self.damage
```

`can_hit(a, b)` → `a._collision_layer & b._collision_mask`. Returns `False` for unregistered entities (walls). Never use `can_hit` inside `_swept_blocked`.

## Key rules
1. **Never** check `entity.name == 'enemy'`; use `can_hit(self, entity)` in bullet update, or `isinstance(entity, Enemy)` only where type identity is truly needed.
2. **Never** pass classes to `raycast(ignore=...)` — always pass instances. Build the ignore list from `scene.entities` filtered by `_collision_layer`.
3. **Never** call `destroy()` on a managed entity directly — call `self.die()`. See AliveEntity pattern below.
4. Use `except Exception:` not bare `except:`.
5. Bullet-vs-enemy/player AABB loops in `main.py` are **deleted** — don't re-add them.
6. **`swept_cast` (in `collision_system.py`) is for bullets only** — it filters hits through `can_hit`. Player `_swept_blocked` must NOT use it, or walls become passable.

## AliveEntity pattern (replaces `_destroyed` bool)
`Enemy`, `PlayerBullet`, `EnemyBullet` all inherit `AliveEntity` from `collision_system.py`.

```python
class MyEntity(AliveEntity):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        register(self, Layers.ENEMY)

    def update(self):
        if not self.alive:   # guard — update() fires this frame after die()
            return
        ...
        if should_die:
            self.die()

    def on_die(self):
        destroy(self.health_bar)   # sub-entities first; super().die() calls destroy(self)
```

`die()` is idempotent. For **pool bullets** (`PlayerBullet`, `EnemyBullet`), `die()` is overridden to skip `destroy()` and return the entity to its pool instead.

## Bullet pool
Pools live at module level in `weapon.py`:
- `_player_bullet_pool = BulletPool(PlayerBullet, size=30)`
- `_enemy_bullet_pool  = BulletPool(EnemyBullet,  size=60)`

Inactive bullets are parked at `Vec3(0, -10000, 0)`. Do **not** use `entity.enabled = False` on pooled bullets — Panda3D's `unstash()` asserts on re-enable and crashes.

`enemy.py` accesses the enemy pool via a lazy call:
```python
from Scripts.weapon import get_enemy_bullet_pool  # inside shoot(), not at module level
```

## Player collider dimensions
- `BoxCollider(self, center=(0, 0.75, 0), size=(0.8, 2.5, 0.8))` — feet at entity.y−0.5, top at entity.y+2.0.
- Entity settles at world-y ≈ 1.0 when standing (feet at y=0.5 = ground surface).
- `player_bottom = self.y - 0.5` in the ground snap check — unchanged.
- `collider_top = 2.0` in `generate_raycast_points` — drives ceiling raycast position.
- `_swept_blocked` offsets: `[-0.4, +0.3, +0.9, +1.5, +1.9]` — five heights, feet to forehead.
- `camera.clip_plane_near = 0.01` set at app init — prevents see-through-wall artifact when looking down at close geometry.

## Imports
Circular-import risk: `weapon.py ↔ enemy.py`. `EnemyBullet` lives in `weapon.py` to break the cycle. `enemy.py` uses a lazy import inside `shoot()`:
```python
def shoot(self):
    from Scripts.weapon import get_enemy_bullet_pool   # lazy — only at call time
```

## Globals in main.py
`player`, `game_paused`, `pause_menu` — treat as read-only outside `main.py`.

## Scene transitions
Return-to-menu is handled by `PauseMenu.return_to_main_menu()` → `main_menu()`. HealthBar sub-entities must NOT use `eternal=True`.

## Ursina 8.3.0 compatibility (macOS / OpenGL 2.1)

**Root cause of black screen after upgrade:** Ursina 8.3.0 set `Entity.default_shader = unlit_with_fog_shader`, and `Sky()` hardcodes `shader=unlit_shader`. Both shaders use GLSL `#version 130` / `#version 140`. macOS OpenGL 2.1 (the only context Panda3D's `CocoaGraphicsPipe` provides on Apple Silicon) supports GLSL 1.20 at most → every shader fails to compile → geometry renders black.

**Fix already applied in `main.py`:** `_patch_shaders_to_glsl120()` is called immediately after `Ursina()` and before any entity is created. It rewrites the `vertex` and `fragment` source strings on both shader objects and sets `compiled = False` so they recompile with the new source on first use.

Key GLSL 1.20 differences:
- `attribute` / `varying` instead of `in` / `out`
- `texture2D()` instead of `texture()`
- `gl_FragColor` instead of a named `out vec4`

**Do not** upgrade these shaders back to `#version 130+` without verifying that `gl-version 3 2` creates a working Core Profile context on the target machine. On macOS, `gl-version 3 2` in `loadPrcFileData` causes Core Profile (which rejects `#version 130` compatibility shaders) without Panda3D generating `#version 150 core` replacements — net result is the same compilation failure.

**Other Ursina 8.3.0 changes accounted for:**
- `window.color` default changed to black → set explicitly to `color.rgb(50, 50, 60)` after `App()`.
- `Sky(texture='sky_default')` → `Sky()` — the `sky_default` asset was removed.

## Crosshair
`self.crosshair` lives on the `Weapon` instance (`weapon.py`). Created with `visible=False`; shown in `start_game()` when gameplay begins. Hidden/restored in `main.py`'s `input()` escape handler. Access via `player.weapon.crosshair`.

## Known footguns
- `time` in Ursina scope is panda3d's clock, not stdlib `time`. Use `time.dt` for frame delta; use `import time as _time; _time.time()` for wall-clock timestamps.
- `Vec3 == tuple` is unreliable — use `distance(a, Vec3(*b)) < 0.01`.
- Player `BoxCollider` is created once manually; do not pass `collider=` to `super().__init__`.
- `destroy(entity)` is **deferred**: the entity stays in `scene.entities` and its `update()` keeps firing until the end-of-frame flush. Use `die()` for managed entities, never raw `destroy()`.
- `_swept_blocked` must use raw `raycast().hit`, not `can_hit` — walls are unregistered.
- Pool bullets must not have `enabled` toggled — use position parking (`y = -10000`) instead.
- `swept_cast` ignores unregistered entities (walls) by design — correct for bullets, wrong for player movement.
- Ursina 8.3.0 shader patching (`_patch_shaders_to_glsl120`) must run before any `Entity` is created. If you move or defer it, the first entity to compile its shader will cache the broken `_shader` object and all subsequent entities of that type will also fail.

## Testing a fix
```bash
python main.py          # launch game
# Scene renders (no black screen) — sky, ground, and level blocks all visible
# Shoot enemy → health bar decreases, enemy dies cleanly (no crash)
# Walk into wall at any height (feet, waist, head) → player stops (no tunnelling)
# Walk to wall and look down → wall stays visible (no clip-through)
# Enemy shoots player → player takes damage once per bullet
# Fire 30+ shots rapidly → no new Entity objects allocated after pool warms up
# Return to menu and re-enter → no duplicate UI elements
# Pause (Esc) → crosshair disappears; resume → crosshair reappears
```
