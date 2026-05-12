# Ivan's 3D Engine

A first-person shooter built on [Ursina](https://www.ursinaengine.org/) with a bitmask collision registry, object-pooled bullets, a shared swept-raycast system, and an in-engine level editor.

---

## Getting started

**Requirements:** Python 3.10+, Ursina

```bash
pip install ursina
python main.py
```

Controls: `WASD` move · `Space` jump · `LMB` shoot · `R` reset · `Esc` pause · `F` fullscreen · `C` toggle collider debug

---

## Architecture

```
main.py                      App init, scene management, pause menu
Scripts/
  collision_system.py        Bitmask layer registry, AliveEntity mixin, CollisionManager
  player_controller.py       First-person controller with swept-raycast wall collision
  weapon.py                  Weapon, PlayerBullet, EnemyBullet, BulletPool
  enemy.py                   Enemy entity
  health_bar.py              World-space health bar (attaches to any entity)
  level_editor.py            In-engine block/entity placer with JSON save/load
level.json                   Saved level data
```

---

## Collision design

Three authorities. Never add a fourth.

| Authority | Where | What it covers |
|---|---|---|
| Swept projectile raycast | `PlayerBullet.update`, `EnemyBullet.update` in `weapon.py` | Bullet hits wall or character — single point where damage is applied |
| Swept player movement | `Player._swept_blocked` in `player_controller.py` | Wall collision — 5 rays at different heights before moving, falls back to axis slide |
| Ground / ceiling raycast | `Player.update` in `player_controller.py` | Gravity, landing, head bumps |

### Bitmask layer registry (`collision_system.py`)

Entities declare what they are and what they hit. No `isinstance` needed outside `collision_system.py`.

```python
from Scripts.collision_system import Layers, register, can_hit

register(self, Layers.ENEMY)          # in Enemy.__init__
register(self, Layers.PLAYER_BULLET)  # in PlayerBullet.__init__ (via pool)

# In PlayerBullet.update — replaces isinstance(hit.entity, Enemy):
if can_hit(self, hit.entity):
    hit.entity.health -= self.damage
```

`can_hit(a, b)` checks `a._collision_layer & b._collision_mask` against the `COLLISION_MATRIX` without importing any entity types.

**Important:** wall entities are plain Ursina `Entity` objects and are **not** registered. `_swept_blocked` therefore does raw `raycast().hit` — it does **not** filter through `can_hit`. `swept_cast` (the version in `collision_system.py`) is for bullets only.

### Player collider

`BoxCollider(self, center=(0, 0.75, 0), size=(0.8, 2.5, 0.8))`

- Bottom: `entity.y − 0.5` (feet). Ground snap uses `player_bottom = self.y − 0.5`.
- Top: `entity.y + 2.0` (above camera, prevents clipping through head-height objects).
- Wall sweep offsets (local Y): `−0.4, +0.3, +0.9, +1.5, +1.9` — five heights from feet to forehead.

---

## Entity lifecycle — `AliveEntity`

All entities that can die mid-frame inherit `AliveEntity` from `collision_system.py`. Use `die()`, never `destroy()` directly.

```python
class MyEntity(AliveEntity):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        register(self, Layers.ENEMY)

    def update(self):
        if not self.alive:   # guard — update() fires after die() this frame
            return
        ...
        if should_die:
            self.die()

    def on_die(self):
        destroy(self.health_bar)   # sub-entities first; super().die() calls destroy(self)
```

`die()` is idempotent — safe to call from multiple paths in the same frame.

---

## Bullet pool

`PlayerBullet` and `EnemyBullet` live in `weapon.py` and are rented from module-level pools. After the pool is warm, no new `Entity` objects are allocated per shot.

```python
# Weapon.shoot():
bullet = _player_bullet_pool.acquire(position=..., direction=..., speed=50, damage=25, player=self.player)
if bullet is None:
    return   # pool exhausted (>30 simultaneous player bullets)

# Enemy.shoot():
from Scripts.weapon import get_enemy_bullet_pool
get_enemy_bullet_pool().acquire(position=..., target=..., player=self.player, enemy=self, speed=10)
```

Inactive bullets are teleported to `(0, −10000, 0)` instead of being stashed/enabled-toggled — Panda3D's `unstash()` asserts on re-enable of a previously disabled `NodePath`.

---

## Changelog

### v5 — Ursina 8.3.0 compatibility, anti-aliasing, crosshair polish

#### Black screen fix (root cause)

Ursina 8.3.0 changed `Entity.default_shader` from `None` to `unlit_with_fog_shader`, and `Sky()` hardcodes `shader=unlit_shader`. Both shaders use GLSL `#version 130` / `#version 140`, but macOS OpenGL 2.1 (the only context available on Apple Silicon via Panda3D's CocoaGraphicsPipe) supports GLSL 1.20 at most. Every shader compilation failed silently → all geometry rendered as opaque black.

**Fix:** `_patch_shaders_to_glsl120()` in `main.py` rewrites both shader objects to GLSL 1.20 syntax (`attribute`/`varying`, `texture2D()`, `gl_FragColor`) before the first entity is created.

Additional fixes applied alongside:
- `window.color = color.rgb(50, 50, 60)` — Ursina 8.3.0 changed the default window background to black.
- `Sky()` instead of `Sky(texture='sky_default')` — the `sky_default` texture asset was removed in 8.3.0.

#### Anti-aliasing (MSAA 4×)

- `loadPrcFileData('', 'framebuffer-multisample 1\nmultisamples 4')` called before `App()` so the OS-level MSAA framebuffer is requested before the window opens.
- `render.setAntialias(AntialiasAttrib.MAuto)` + `render2d.setAntialias(AntialiasAttrib.MAuto)` enable AA on the 3D scene and all UI.

#### Crosshair

- Scale halved: `(0.02, 0.02)` → `(0.01, 0.01)`.
- Hidden by default (`visible=False` on creation); shown only when gameplay starts.
- Hidden when pause menu opens; restored immediately on resume.

---

### v6 — Level editor Ursina 8.3.0 compatibility

Applied the same fixes from `main.py` to `Scripts/level_editor.py` so it renders correctly when run standalone (`python Scripts/level_editor.py`).

- **GLSL 1.20 shader patch** — `_patch_shaders_to_glsl120()` defined and called immediately after `Ursina()`, before any entity is created. Same fix as `main.py`: rewrites `unlit_shader` and `unlit_with_fog_shader` to use `attribute`/`varying`, `texture2D()`, and `gl_FragColor` instead of GLSL 1.30+ syntax. Root cause and mechanism identical to the v5 black screen fix.
- **MSAA 4×** — `loadPrcFileData('', 'framebuffer-multisample 1\nmultisamples 4')` added before `Ursina()` + `render.setAntialias` / `render2d.setAntialias` after.
- **Window background** — `window.color = color.rgb(50, 50, 60)` to match the game's grey instead of Ursina 8.3.0's black default.
- **`AntialiasAttrib` import** — added to the existing `from panda3d.core import ...` line at the top of the file.

---

### v4 — dependency updates

All Python dependencies bumped to their latest stable versions.

| Package | Before | After |
|---|---|---|
| `ursina` | 8.1.1 | 8.3.0 |
| `panda3d` | 1.10.15 | 1.10.16 |
| `panda3d-simplepbr` | 0.12.0 | 0.13.1 |
| `numpy` | 2.2.2 | 2.4.4 |
| `pillow` | 11.1.0 | 12.2.0 |
| `pygame` | 2.6.1 | 2.6.1 (already latest) |
| `panda3d-gltf` | 1.3.0 | 1.3.0 (already latest) |

---

### v3 — collision system redesign

**Bitmask layer registry** — `collision_system.py` created with `Layers`, `COLLISION_MATRIX`, `register()`, `unregister()`, `can_hit()`. Replaces all `isinstance(x, Enemy)` hit checks in `weapon.py`.

**`AliveEntity` mixin** — single `die()` entry point replaces the `_destroyed` bool pattern. `Enemy`, `PlayerBullet`, `EnemyBullet` all inherit it. `update()` guards with `if not self.alive: return`.

**Bullet object pool** — `BulletPool` in `weapon.py` recycles `PlayerBullet` (30 cap) and `EnemyBullet` (60 cap). Pool uses position-park (`y=−10000`) instead of `enabled` toggling to avoid a Panda3D `unstash()` assertion crash.

**`EnemyBullet` moved to `weapon.py`** — eliminates the `weapon.py ↔ enemy.py` circular import. `enemy.py` accesses the pool via a lazy call to `get_enemy_bullet_pool()`.

**`CollisionManager`** — spatial grid with `query_layer()` and `query_near()`. `collision_manager.update()` runs every frame in `main.py` (no frame-skip throttle).

**Player collider raised** — height 1 → 2.5, center shifted to `(0, 0.75, 0)`. Feet unchanged at `entity.y − 0.5`; top raised to `entity.y + 2.0`. Fixes camera clipping through head-height geometry.

**Wall sweep extended** — `_swept_blocked` offsets expanded from 3 heights (`−0.4, 0, +0.4`) to 5 (`−0.4, +0.3, +0.9, +1.5, +1.9`), covering the full collider height.

**Camera near clip** — `camera.clip_plane_near = 0.01` (was ~0.1). Fixes see-through-wall artifact when looking down at close geometry.

**Wall-clip regression introduced and fixed** — `swept_cast` was incorrectly used in `_swept_blocked`, which filtered wall hits through `can_hit`. Walls are unregistered so `can_hit` always returned `False` → player walked through walls. Fixed: `_swept_blocked` uses raw `raycast().hit` with layer-based ignore list.

### v2 — collision audit + fixes

**Enemy death crash** — `_destroyed` guard added to `Enemy.update()`.

**EnemyBullet double damage** — AABB loop in `main.py` removed; raycast is single authority.

**EnemyBullet double destroy** — `_destroy()` helper with guard on `EnemyBullet`.

**EnemyBullet expiry** — distance check replaced with `MAX_LIFETIME` + `spawn_time`.

**Wall collision at foot level** — `_swept_blocked` offsets made symmetric around entity center.

**Player height** — `BoxCollider` reduced from height 2 to height 1.

**Bullet hit condition** — `entity.name == 'enemy'` → `isinstance(hit.entity, Enemy)`.

**Raycast ignore list** — bare class references → live instance list from `scene.entities`.

---

## Engine status

| System | Status |
|---|---|
| Swept player collision | Stable |
| Bullet pool (PlayerBullet / EnemyBullet) | Stable |
| Bitmask layer registry | Stable |
| `AliveEntity` lifecycle | Stable |
| `CollisionManager` spatial grid | Stable |
| Enemy base class | Stable |
| World-space health bar | Stable |
| Level editor | Usable — schema expanding |
| Scene lifecycle | Stable |
| `Game` state machine | Planned |

---

## Roadmap

### Near-term
- [ ] `Game` state-machine class replacing module-level globals in `main.py`
- [ ] `_clear_gameplay_entities()` as the canonical scene teardown path
- [ ] Expand level editor JSON schema: enemy type, HP, rotation, block colour

### Engine features
- [x] Collision bitmask system — `Layers` registry + `can_hit()` replaces `isinstance` checks
- [x] Object pooling for bullets — `BulletPool` eliminates per-shot allocation
- [ ] Pluggable enemy behaviour trees — patrol / attack / flee state composition
- [ ] Trigger/zone system — volume entry/exit callbacks for doors, checkpoints, kill planes
- [ ] Weapon inventory API — multi-weapon, ammo pickup, switch animations
- [ ] Asset hot-reload in the level editor

### Long-term
- [ ] Packaged runtime via PyInstaller / Nuitka
- [ ] Networked multiplayer substrate — authoritative server, client-side prediction
- [ ] Procedural level generator outputting the existing JSON schema
- [ ] Full gamepad / controller input layer

---

## License

MIT

