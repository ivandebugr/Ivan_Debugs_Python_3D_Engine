# Ivan_Debugs_Python_3D_Engine

A first-person shooter / custom 3D engine built with [Ursina Engine](https://www.ursinaengine.org/) — a Python game framework built on Panda3D. Features a custom swept-collision system, spatial-grid broad-phase, in-engine level editor, and enemy AI with health tracking.

---

## Status

| System | State |
|---|---|
| Player movement & wall collision | Stable (swept raycast) |
| Bullet → enemy damage | Fix in progress (Bug #1) |
| Enemy AI & pathfinding | Stable |
| Health bar UI | Stable |
| Level editor | Usable |
| Scene transitions | Known entity leak |
| Performance monitor | Stable |

---

## Getting Started

**Requirements:** Python 3.10+, pip

```bash
git clone <repo>
cd <repo>
pip install ursina
python main.py
```

**Controls**

| Key | Action |
|---|---|
| `W A S D` | Move |
| Mouse | Look |
| `Left Click` | Shoot |
| `Esc` | Pause / Menu |
| `F3` | Toggle debug collision rays |
| `E` | Open level editor |

---

## Architecture

```
main.py                     Entry point, global update loop, scene management
Scripts/
  player_controller.py      Movement, multi-height swept-raycast wall/floor/ceiling checks
  weapon.py                 Weapon, PlayerBullet (swept raycast, single damage authority)
  enemy.py                  Enemy entity, EnemyBullet
  collision_system.py       CollisionManager, spatial grid (cell_size=10), CollisionLayer enum
  health_bar.py             3D world-space health bar with child Text entities
  level_editor.py           In-engine block + enemy placer with save/load
```

### Collision design

Three non-overlapping systems handle different concerns:

- **Projectiles** — swept `raycast` inside each bullet's `update()`. Single authority for hit detection and damage application. Continuous, so fast bullets don't tunnel at low FPS.
- **Player movement** — multi-height swept raycasts (`_swept_blocked`) cast from the *current* position before moving, preventing wall tunnelling.
- **Pickups / triggers** — AABB overlap via `CollisionManager` (spatial-grid broad phase + precise check). Appropriate for slow-moving or stationary triggers.

---

## What's been built

- **Custom swept collision** for player movement with wall-slide on axis separation
- **Spatial grid broad-phase** (`CollisionManager`) with per-entity cell tracking — O(1) removal
- **Layered collision** (`CollisionLayer` enum): `PLAYER`, `ENEMY`, `PLAYER_BULLET`, `ENEMY_BULLET`, `WALL`, `PICKUP`
- **Health system** with 3D world-space bars parented to enemies
- **Weapon system** with configurable fire rate, bullet speed, and lifetime
- **Level editor** — click to place/remove blocks and enemies, save/load JSON levels
- **Scene management** — main menu → gameplay → pause → main menu transitions
- **Performance monitor** using `time.dt` for frame-time tracking

---

## Known issues / active fixes

### Critical (in progress)

**Bug #1 — Bullets hit enemies but deal no damage**
`PlayerBullet.update` was checking `entity.name == 'enemy'`; `Enemy` never sets that attribute. Fix: `isinstance(hit.entity, Enemy)` + apply damage in the raycast path, removing the duplicate AABB pass in `main.py`.

**Bug #2 — Player sticks / rubber-bands near projectiles**
`raycast(ignore=[self, EnemyBullet, PlayerBullet])` passes *classes*, not instances — every live bullet is a valid wall-hit target. Fix: dynamic instance list built from `scene.entities` at call time.

**Bug #3 — Player clips through thin walls at low FPS**
Move-then-check puts the player inside the wall before rays are cast. Fix: swept check *before* moving, using full move distance.

**Bug #4 — Double-destroy on bullet lifetime expiry**
`self.lifetime` was used as both a countdown and a max-age constant, triggering `destroy()` twice. Fix: single `MAX_LIFETIME` class constant checked against `spawn_time`.

### Non-critical (queued)

- Bare `except:` blocks silencing real errors across several files — narrowing to `except Exception:` with logging
- `HealthBar` sub-entities marked `eternal=True` leak across scene transitions
- `Weapon` crosshair not destroyed on weapon teardown
- `Player.__init__` allocates two `BoxCollider`s; second silently replaces first
- `level_editor.py` save format missing rotation, enemy parameters, and custom colors

---

## Roadmap

### Near-term

- [ ] Finish active bug fixes (see above)
- [ ] Replace module-level globals in `main.py` with a `Game` class (`start`, `pause`, `resume`, `return_to_menu`)
- [ ] Single `_clear_gameplay_entities()` method replacing manual scene-scrub loops
- [ ] Narrow all bare `except:` to `except Exception:` with `logging.warning`
- [ ] Fix `HealthBar` eternal-entity leak; maintain explicit cleanup registry
- [ ] Extend level editor save schema: rotation, enemy HP/weapon type, block color

### Medium-term

- [ ] Enemy variety — ranged, melee, patrol-path configs saved in level JSON
- [ ] Weapon inventory — multiple weapons, ammo pickups, weapon switching
- [ ] Room/wave system — spawn waves from level data, inter-room doors
- [ ] Collision mask system — replace per-type `isinstance` checks with bitmask layers, eliminating import cycles
- [ ] Object pooling for bullets — avoid per-frame `Entity` alloc/dealloc at high fire rates

### Long-term / exploratory

- [ ] Networked multiplayer (Ursina + `python-enet` or WebSockets)
- [ ] Procedural level generation feeding the existing level editor format
- [ ] Full controller / gamepad support
- [ ] Packaged builds via PyInstaller / Nuitka

---

## License

MIT