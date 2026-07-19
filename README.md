# Ivan's 3D Engine

A first-person shooter built on [Ursina](https://www.ursinaengine.org/) (Python / Panda3D). It
started as an engine experiment and grew a full in-engine level editor, data-driven enemies,
weapons, and a hand-written lighting/post-processing stack that runs on macOS's OpenGL 2.1 ceiling.

**Current version:** 1.7 · **Platform:** macOS (Apple Silicon, OpenGL 2.1 / GLSL 1.20)

---

## Getting started

**Requirements:** Python 3.10+, Ursina 8.3.0 (Panda3D 1.10.16)

```bash
pip install ursina
python main.py
```

**Controls:** `WASD` move · `Space` jump · `LMB` shoot · `1/2/3` switch weapon · `R` reset · `Esc` pause · `F` fullscreen · `C` toggle collider debug

**Level editor:** `python Scripts/level_editor.py` — place blocks and enemies, edit properties, and play-test in place with `F5`.

---

## What's in it

- **First-person combat** — three weapons (Pistol / Shotgun / Rifle) with per-weapon damage, fire
  rate, ammo, and reload; object-pooled bullets; swept-raycast hit detection.
- **Data-driven enemies** — each enemy runs its own behaviour tree (patrol / chase / attack / flee),
  composed from named presets in `level.json` and editable in the level editor.
- **Trigger/zone system** — invisible volumes for kill planes, checkpoints, doors, and win conditions.
- **In-engine level editor** — grid snap, multi-select, transform gizmos, hierarchy and inspector
  panels, undo/redo, camera bookmarks, an asset browser with drag-to-place and hot-reload, and
  play-in-editor.
- **Lighting & post-processing** — a hand-written GLSL 1.20 lit shader with a real depth-map sun
  shadow pass (9-tap PCF), blob ground shadows, and a bright-pass bloom post-process — all built to
  run on the macOS 2.1 context without a core-profile move.

---

## Architecture

```
main.py                      App init, scene management, pause menu, game loop
Scripts/
  collision_system.py        Bitmask layer registry, AliveEntity mixin, CollisionManager
  game.py                    Game state machine (singleton) + canonical scene teardown
  player_controller.py       First-person controller with swept-raycast wall collision
  weapon.py                  Weapon base + Pistol/Shotgun/Rifle, PlayerBullet, EnemyBullet, BulletPool
  weapon_inventory.py        3-slot weapon switching, ammo pickups
  enemy.py                   Enemy entity (behaviour-tree driven)
  behaviour_tree.py          Compositors + decorators (Sequence/Selector/Parallel, Invert/Repeat/Cooldown)
  behaviour_nodes.py         Leaf nodes (Idle/Attack/Chase/Patrol/Flee)
  behaviour_tree_factory.py  Named presets (default / patrol_then_attack / flee_when_low / aggressive / cautious)
  trigger_system.py          TriggerZone volumes — kill_plane / checkpoint / open_door / win_condition
  lit_shader.py              Hand-written GLSL 1.20 lit shader + real sun-shadow depth pass
  bloom.py                   Bright-pass + quarter-res blur + composite post-process
  light_lifecycle.py         Scene light setup + teardown (LightAttrib / shadow FBO cleanup)
  ground_shadow.py           Blob ground-shadow decals for player and enemies
  health_bar.py              World-space health bar (attaches to any entity)
  compat.py                  GLSL 1.20 shader patch (shared by game + editor entry points)
  audio_workaround.py        Forces NullAudioManager to dodge an OpenAL import-time crash on macOS
  level_io.py / asset_registry.py / asset_resolve.py   Level + asset loading
  editor_core.py + editor_*.py   Level editor (core class + hierarchy/gizmo/browser/inspector/playmode collaborators)
  undo_redo.py               Command-pattern undo/redo stack
level.json                   Saved level data
```

### Collision design

Three authorities. Never add a fourth.

| Authority | Where | What it covers |
|---|---|---|
| Swept projectile raycast | `PlayerBullet.update` / `EnemyBullet.update` in `weapon.py` | Bullet → wall / character — single point where damage is applied |
| Swept player movement | `Player._swept_blocked` in `player_controller.py` | Player → wall — 5 rays at different heights cast *before* moving |
| Ground / ceiling raycast | `Player.update` in `player_controller.py` | Gravity, landing, head bumps |

Entities declare what they are and what they hit via a **bitmask layer registry**
(`collision_system.py`): `can_hit(a, b)` checks `a._collision_layer & b._collision_mask`, so no
`isinstance` checks are needed outside that module. Walls are unregistered plain entities and are
handled by raw raycasts, never routed through `can_hit`. Pickup and trigger volumes are detection
layers that never block movement.

### Entity lifecycle — `AliveEntity`

Anything that can die mid-frame inherits `AliveEntity`. Call `die()`, never `destroy()` directly —
`die()` is idempotent, removes the entity from the collision manager, runs `on_die()` for sub-entity
cleanup, then destroys the node. Pooled bullets override `die()` to return to their pool instead.

### Bullet pool

`PlayerBullet` (30) and `EnemyBullet` (60) are rented from module-level pools in `weapon.py`; once
warm, no new entities are allocated per shot. Inactive bullets are parked off-screen rather than
disabled, working around a Panda3D `unstash()` assertion.

### Rendering on OpenGL 2.1

On Apple Silicon, Panda3D only grants an OpenGL 2.1 / GLSL 1.20 context. Ursina 8.3.0 ships shaders
targeting newer GLSL, so `compat.py` rewrites them to 1.20 syntax before the first entity is created.
The lit shader, sun-shadow pass, and bloom are all hand-written in 1.20-compatible GLSL for the same
reason — the project deliberately stays on the 2.1 profile rather than switching to core profile.
See `obsidian-mind-main/brain/Key Decisions.md` for that trade-off.

---

## Engine status

| System | Status |
|---|---|
| Swept player collision | Stable |
| Bullet pool (PlayerBullet / EnemyBullet) | Stable |
| Bitmask layer registry | Stable |
| `AliveEntity` lifecycle | Stable |
| `CollisionManager` spatial grid | Stable |
| `Game` state machine + canonical teardown | Stable |
| Enemy behaviour trees | Stable |
| Trigger / zone system | Stable |
| Weapon inventory (3-slot + pickups) | Stable |
| World-space health bar | Stable |
| Lit shader + real sun shadows | Stable |
| Blob ground shadows | Stable |
| Bloom post-process | Stable |
| Level editor | Stable — schema still expanding |

---

## Roadmap

### Near-term
- [ ] itch.io release prep — PyInstaller macOS `.app` build, ambient music track, store page
- [ ] Hands-on game-feel pass on the curated level (jump feel, difficulty, marker readability)

### Long-term
- [ ] Packaged runtime via PyInstaller / Nuitka
- [ ] Full gamepad / controller input layer
- [ ] Procedural level generator outputting the existing JSON schema
- [ ] Networked multiplayer substrate — authoritative server, client-side prediction

---

## Version history

See [`CHANGELOG.md`](CHANGELOG.md) for the full, per-version history.

---

## License

MIT
