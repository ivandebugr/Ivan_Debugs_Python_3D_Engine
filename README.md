# Ivan_Debugs_Python_3D_Engine

A first-person shooter **engine** built on top of [Ursina](https://www.ursinaengine.org/) — providing the systems, tooling, and runtime architecture that Ursina itself doesn't: swept collision, spatial-grid broad-phase, layered hit detection, a world-space UI framework, and an in-engine level editor. Build your FPS game on top of it; don't rewrite this stuff yourself.

---

## What this is

Ursina gives you a Python-friendly wrapper over Panda3D — entities, basic raycasting, a scene graph. What it *doesn't* give you is a production-ready FPS foundation. This engine fills that gap:

| Layer | What it solves |
|---|---|
| **Swept collision** | Bullets and players that don't tunnel through walls at low FPS |
| **Spatial broad-phase** | O(1) entity lookup per frame; scales to hundreds of entities |
| **Collision layers** | Typed separation — bullets don't collide with bullets |
| **Health & damage** | Entity health, damage routing, death callbacks — wired up, not bolted on |
| **World-space UI** | Health bars and floating labels that track 3D entities |
| **Level editor** | In-engine block + entity placer with JSON save/load |
| **Scene lifecycle** | Menu → gameplay → pause → menu transitions without entity leaks |

---

## Getting Started

**Requirements:** Python 3.10+

```bash
git clone <repo>
cd <repo>
pip install ursina
python main.py          # launches the bundled demo game
```

The demo game is the canonical usage example. Read `main.py` alongside the docs below.

---

## Architecture

```
main.py                       Demo game — shows engine usage end-to-end
Scripts/
  collision_system.py         Core: CollisionManager, spatial grid, CollisionLayer enum
  player_controller.py        First-person controller with swept-raycast movement
  weapon.py                   Weapon system: Weapon, PlayerBullet, EnemyBullet
  enemy.py                    Enemy base class with health and AI hooks
  health_bar.py               World-space health bar — attaches to any Entity
  level_editor.py             In-engine editor with JSON persistence
```

---

## Core systems

### Collision (`collision_system.py`)

The engine runs **three non-overlapping collision authorities**. Never add a fourth.

**1. Swept projectile raycasts** — inside each bullet's `update()`. Cast from the bullet's previous position over the full frame displacement. Handles fast bullets at low FPS without tunnelling. The single point where bullet damage is applied.

**2. Swept player movement** — multi-height raycasts cast *before* moving, along the intended move vector. Supports wall-slide via axis separation.

**3. AABB spatial grid** — `CollisionManager` handles slow-moving overlap checks (pickups, triggers, zone entry). Spatial grid with O(1) per-entity cell lookup; cells update lazily only when an entity crosses a cell boundary.

```python
from Scripts.collision_system import CollisionManager, CollisionLayer

manager = CollisionManager()
manager.add_entity(my_entity, CollisionLayer.ENEMY)
manager.add_entity(pickup, CollisionLayer.PICKUP)
```

`CollisionLayer` values: `PLAYER`, `ENEMY`, `PLAYER_BULLET`, `ENEMY_BULLET`, `WALL`, `PICKUP`

---

### Player controller (`player_controller.py`)

Drop-in first-person controller. Extend or replace movement, look, and jump behaviour by subclassing.

```python
from Scripts.player_controller import PlayerController

player = PlayerController(
    position=(0, 1, 0),
    speed=8,
    jump_height=2,
    skin_width=0.05,
)
```

Key overridable methods: `handle_horizontal_movement`, `handle_jump`, `_swept_blocked`.

---

### Weapon system (`weapon.py`)

Attach a `Weapon` to any entity. Bullets are self-managing — they raycast, apply damage, and clean up without an external loop.

```python
from Scripts.weapon import Weapon

player.weapon = Weapon(
    parent=player,
    fire_rate=0.15,
    bullet_speed=50,
    bullet_damage=25,
    bullet_lifetime=2.0,
)
```

To implement a new projectile type, subclass `PlayerBullet` or `EnemyBullet` and override `on_hit(entity)`.

---

### Enemy base class (`enemy.py`)

`Enemy` is a subclassable entity with built-in health, damage routing, and a death callback hook.

```python
from Scripts.enemy import Enemy

class TankEnemy(Enemy):
    def __init__(self, **kwargs):
        super().__init__(health=200, speed=2, **kwargs)

    def on_death(self):
        # drop loot, trigger animation, etc.
        super().on_death()
```

---

### World-space UI (`health_bar.py`)

`HealthBar` attaches to any 3D entity and tracks its world-space position. Supports custom colours, scale, and an optional label.

```python
from Scripts.health_bar import HealthBar

bar = HealthBar(parent=enemy, offset=(0, 2.2, 0), max_health=enemy.health)
bar.set_health(enemy.health)
```

---

### Level editor (`level_editor.py`)

Press `E` in the demo to open the editor. Saves and loads levels as JSON.

```python
from Scripts.level_editor import LevelEditor

editor = LevelEditor()
editor.load('levels/level_01.json')
```

Level JSON schema:

```json
{
  "blocks":  [{ "position": [x, y, z], "texture": "brick" }],
  "enemies": [{ "position": [x, y, z], "type": "basic" }]
}
```

---

## Authoring conventions

**No name-string type checks.** Use `isinstance` or class-level markers:

```python
class MyProjectile(Entity):
    is_projectile = True   # filter: if getattr(e, 'is_projectile', False)
```

**Register with the collision manager in `__init__`:**
```python
collision_manager.add_entity(self, CollisionLayer.PLAYER_BULLET)
```

**Use `_cleanup_and_destroy()` not bare `destroy()`** on managed entities — it removes the entity from the spatial grid first.

**Break circular imports with lazy method-level imports:**
```python
def update(self):
    from Scripts.enemy import Enemy   # safe — Python caches after first load
```

---

## Engine status

| System | Status |
|---|---|
| Swept player collision | Stable |
| Spatial broad-phase | Stable |
| Weapon / bullet system | Stable |
| Enemy base class | Stable |
| World-space health bar | Stable |
| Level editor | Usable — schema expanding |
| Scene lifecycle | Stable |
| `Game` state machine | Planned |

---

## Fixed in v2

**Bullet damage not applying** ✓ — replaced `entity.name == 'enemy'` with `isinstance(hit.entity, Enemy)`; damage now applied inside the swept-raycast path; duplicate AABB loop removed from `main.py`.

**Player sticks near projectiles** ✓ — `_ignored_entities()` builds a live instance list from `scene.entities` at call time instead of passing bare classes to `raycast(ignore=...)`.

**Player tunnels at low FPS** ✓ — `_swept_blocked()` casts three-height rays from the current position before any move; `handle_horizontal_movement()` falls back to axis-separated sliding on block.

**Double-destroy on bullet expiry** ✓ — `MAX_LIFETIME = 2.0` class constant checked once against `spawn_time`; all destroy paths go through `_destroy()` helper.

**Startup / transition crash (`AssertionError: !is_empty()`)** ✓ — `main_menu()` and `load_level()` now iterate `scene.entities[:]` (a copy) with a `try/except` guard, preventing dead Panda3D NodePaths from crashing `.name` access after mid-loop destroys.

Full diagnosis: [`docs/collision_diagnosis.md`](./docs/collision_diagnosis.md)

---

## Roadmap

### Near-term
- [x] Ship the four active collision fixes (+ startup crash fix)
- [ ] `Game` state-machine class replacing module-level globals in `main.py`
- [ ] `_clear_gameplay_entities()` as the canonical scene teardown path
- [ ] Fix `HealthBar` eternal-entity leak on scene transition
- [ ] Expand level editor JSON schema: enemy type, HP, rotation, block colour

### Engine features
- [ ] Collision bitmask system — replace `isinstance` layer checks, break import cycles
- [ ] Object pooling for bullets — eliminate per-shot `Entity` allocation at high fire rates
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

## See also

- [`docs/collision_diagnosis.md`](./docs/collision_diagnosis.md) — root-cause write-up for all four active bugs

## License

MIT
