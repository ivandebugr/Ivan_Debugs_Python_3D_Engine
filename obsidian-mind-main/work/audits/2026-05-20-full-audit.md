---
tags: [audit, ursina, collision, performance]
date: 2026-05-20
project: ivans-3d-engine
links: [[brain/Gotchas]], [[brain/Patterns]], [[brain/North Star]]
---

# Ivan's 3D Engine — Audit Report
Date: 2026-05-20
Auditor: Claude Code

## Executive Summary

14 issues found across 8 audit tracks: 0 critical, 4 high, 5 medium, 5 low. The most impactful issue is that `CollisionManager` is architecturally disconnected — entities are registered via the standalone `register()` function, not `collision_manager.add()`, so `_tracked` is always empty and the spatial grid, `query_layer()`, and `query_near()` never return any results. The second most impactful is a genuine entity accumulation bug: `Weapon` is parented to `camera` (not `player`), so it is never destroyed when the player is, causing weapons and crosshairs to accumulate across Play → Menu → Play cycles. `level.json` has 19 excess duplicate block entries at four stacked positions, causing z-fighting and wasted entity slots.

---

## Track 1 — Collision Authority

### Findings

**PlayerBullet → Enemy/Wall:** `weapon.py` `PlayerBullet.update()` lines 86–101. Single raycast per frame, `can_hit(self, hit.entity)` bitmask check, `hit.entity.health -= self.damage` as the sole damage application point. The former AABB loop in `main.py` is confirmed deleted. **Single authority. ✓**

**EnemyBullet → Player/Wall:** `weapon.py` `EnemyBullet.update()` lines 153–170. Single raycast per frame, `can_hit(self, hit.entity)` check, `self.player.health -= 25` as the sole damage point. Hardcoded `25` (not `self.damage` — no `damage` attribute on `EnemyBullet`); functionally correct but inconsistent with `PlayerBullet`. **Single authority. ✓**

**Player → Wall (horizontal):** `player_controller.py` `_swept_blocked()` lines 226–239. Five-height raw `raycast().hit` sweep, no `can_hit()` used (correct per CLAUDE.md rule 6). **Single authority. ✓**

**Player → Ground/Ceiling (vertical):** `player_controller.py` `update()` lines 161–193. Two raycasts — ground + ceiling. Physics only, no damage. **Single authority. ✓**

**Enemy LOS check:** `enemy.py` `_is_occluded()` lines 54–62. Raycast for visibility only — not a damage path. Uses `hit.entity.name != 'player'` name check (flagged in Track 8). **No damage authority conflict. ✓**

**Dual-authority conflicts:** None found. The `CollisionManager` is updated every frame but never applies damage — it is purely a spatial index.

### Verdict
PASS (with one rule-compliance note in Track 8 re: `_is_occluded` name check)

---

## Track 2 — Entity Lifecycle

### Findings

**Player (FirstPersonController subclass — not AliveEntity):**
- Destroyed via `destroy(player)` at `main.py:86` and `main.py:164`.
- `destroy()` does NOT call `unregister()` → `Layers.PLAYER` entry leaks in `_registry` dict on every scene transition.
- `Weapon` is `parent=camera` — NOT a child of player. `destroy(player)` does not touch it.
- `Weapon.crosshair` is `parent=camera.ui` — also not destroyed with player.
- `HealthBar` is `parent=camera.ui` — not destroyed with player.
- `HealthBar.text` is `parent=camera.ui` — not destroyed with player or health bar.
- Debug lines/rays are `parent=self, eternal=True`. `eternal=True` prevents `scene.clear()` from removing them; whether `destroy(parent)` cascades to `eternal` children is not guaranteed.
- `main_menu()` loop (lines 47–52) iterates `scene.entities[:]` and destroys everything except `main_camera`. Entities parented to `camera` and `camera.ui` are in `scene.entities`, so weapon/crosshair/health_bar are eventually destroyed — **but only after `main_menu()` is called.** Between `destroy(player)` and `main_menu()`, these entities are orphaned.
- **KEY BUG:** In `start_game()` line 85–87, `if player: destroy(player)` is called before creating a new player. This destroys the old player but NOT the old `Weapon`. The new `Player.__init__` creates a second `Weapon` also parented to `camera`. After one Play→Menu→Play cycle, two weapons exist simultaneously on camera. Each subsequent cycle adds another. **Entity accumulation.**

**Enemy (AliveEntity subclass):**
- `register()` called in `__init__` ✓
- `on_die()` at line 79 destroys `self.health_bar` before `super().on_die()` ✓
- `update()` guard `if not self.alive` ✓
- `die()` idempotent via `AliveEntity._alive` guard ✓
- `unregister()` called via `AliveEntity.die()` ✓
- **RISK:** `main_menu()` loop calls `destroy(e)` on all entities including any living Enemy. This bypasses `die()`, `unregister()`, and `on_die()`. If an enemy is alive when the loop runs, its health bar sub-entities may not be cleaned up and its registry entry leaks.

**PlayerBullet / EnemyBullet (AliveEntity + pool):**
- `die()` overridden to return to pool instead of `destroy()` ✓
- `update()` alive guard present ✓
- Double-destroy prevented by `if not self._alive: return` ✓
- **MINOR:** `die()` calls `unregister(self)` then `pool.release(self)` which also calls `unregister()` — double unregister. Harmless (dict.pop with None default) but redundant.
- **DEAD CODE:** `PlayerBullet.__init__` lines 52–67 (the `_pooled=True, position=None` branch) creates the entity with `enabled=False`. This violates CLAUDE.md rule 9 (no `enabled` toggle on pool bullets). This code path is never reached because `BulletPool.acquire()` always passes `position` — but the path exists and is confusing.

**HealthBar (plain Entity, not AliveEntity):**
- Has a `destroy()` override at lines 87–91 that calls `destroy(self.text)` then `super().destroy()`.
- **BUG:** Ursina's `destroy()` function does NOT call `entity.destroy()`. It calls `entity.parent.children.remove(entity)` and schedules destruction via Panda3D. The `HealthBar.destroy()` override is dead code — it is never invoked by Ursina's cleanup path.
- For 2D health bars (`is_3d=False`): `self.text = Text(parent=camera.ui, ...)` — text is parented to `camera.ui`, NOT to the health bar. When the health bar is destroyed, `self.text` is NOT automatically destroyed (it is not a child of the health bar). On paths where `destroy(player)` is called without going through `main_menu()`, the `health_bar.text` leaks.

**Weapon (plain Entity, not AliveEntity):**
- No lifecycle method. See Player findings above.

### Verdict
ISSUES FOUND

---

## Track 3 — Performance & Frame Budget

### Findings

**Raycast count per frame (worst case: 1 enemy, 30 player bullets, 60 enemy bullets active):**
- Ground raycast: 1
- Ceiling raycast: 1
- `_swept_blocked()` when player moves into wall (diagonal + x-axis + z-axis): 3 × 5 = 15
- `PlayerBullet.update()`: 30
- `EnemyBullet.update()`: 60
- `Enemy._is_occluded()`: 1
- **Total: ~108 raycasts/frame worst case.** Manageable for Panda3D's BVH at ~15 blocks, but grows linearly with enemy/bullet count.

**Per-frame allocations (hot paths):**
1. `_swept_blocked()` lines 228–232: constructs `ignore = [self] + [e for e in scene.entities if getattr(e, '_collision_layer', 0) in (...)]` — scans ALL `scene.entities` (100+ entities in current level) on every call, up to 3× per movement frame. New list allocated every call. **O(N) scan × 3 per movement frame = significant per-frame cost.**
2. `swept_cast()` in `collision_system.py` lines 70–73: same ignore-list pattern. (Note: `swept_cast` is defined but not called anywhere in the current codebase — dead code.)
3. `CollisionManager.update()` line 106: `list(self._tracked)` — converts set to list every frame, even though `_tracked` is always empty (see Track 4). Zero cost in practice but logically wrong.
4. Per-bullet `ignore = [self, self.player] if self.player else [self]` (weapon.py:91, weapon.py:163): small fixed-size allocations, not a concern.

**O(N²) patterns:**
- None found in damage/collision paths. ✓
- `main.py update()` lines 309–312: `for e in scene.entities: if isinstance(e, HealthBar)` — O(N) scan every frame for world-scale adjustment. With 1 3D health bar this is benign, but it grows with enemy count.

**Frame-budget risk:**
- `_swept_blocked()`'s ignore-list comprehension is the primary concern at 60fps. At current scene size (~100 entities) this is ~300 iterations per movement frame (3 calls × 100 entities) for a list that typically contains only 0–5 bullets. Should be replaced with `collision_manager.query_layer()` once the CollisionManager integration is fixed.

### Verdict
ISSUES FOUND

---

## Track 4 — Scene Lifecycle & Memory Leaks

### Findings

**Entity graph from `main_menu()`:**
Sky (name: 'main_sky'), ground (name: 'ground'), camera_pivot (name: 'camera_pivot'), play_button (Button), quit_button (Button), level blocks (name: 'level_block'), level enemy placeholders (name: 'level_enemy').

**Entity graph from `start_game()`:**
Player → BoxCollider, debug_lines (12×, eternal, parent=player), debug_rays (~21×, eternal, parent=player), HealthBar (parent=camera.ui, eternal) → bg (eternal), bar (eternal), text (parent=camera.ui, eternal). Weapon (parent=camera) → crosshair (parent=camera.ui). Enemy → HealthBar (parent=enemy) → bg (eternal), bar (eternal), text (eternal). Controls Text (unnamed).

**Teardown in `PauseMenu.return_to_main_menu()`:**
1. `destroy(player)` — destroys player node but NOT weapon, crosshair, health_bar, health_bar.text
2. Loop destroys by name: 'level_block', 'ground', 'main_sky' — does NOT include enemies or controls text
3. `main_menu()` called — its loop destroys all `scene.entities` except 'main_camera'

**Leaks and gaps:**
1. **Weapon accumulation (HIGH):** `start_game()` calls `destroy(player)` on the old player but not `destroy(player.weapon)`. New player creates a new weapon. Each cycle adds a weapon + crosshair entity permanently to camera's children until `main_menu()`'s full clear runs. In `start_game()` the gap is just within the same call, but the old weapon is orphaned for one frame before the destroy call indirectly cleans it via `main_menu()`. However `start_game()` does NOT call `main_menu()` — it only calls `destroy(play_button)`, `destroy(quit_button)`, `destroy(camera_pivot)`. There is no `main_menu()` call during `start_game()`. So weapon accumulation IS a real multi-session bug.
2. **`CollisionManager._tracked` always empty (HIGH):** All entity registrations (Player line 25, Enemy line 15, BulletPool.acquire line 33) call the standalone `register()` function, which only updates `_registry` and sets `_collision_layer`/`_collision_mask` on the entity. `collision_manager.add()` — the only method that adds entities to `_tracked` — is never called anywhere. Therefore `collision_manager._tracked` is always empty, `collision_manager.update()` does nothing, and `query_layer()`/`query_near()` always return `[]`. The spatial grid feature is entirely non-functional.
3. **HealthBar.destroy() dead code:** See Track 2. The 2D health bar's `text` (parented to `camera.ui`) is not cleaned up when `destroy(health_bar)` is called because Ursina's `destroy()` function does not invoke the custom `destroy()` override.
4. **AliveEntity bypass in main_menu() loop:** `main_menu()` calls `destroy(e)` on every entity in `scene.entities`, including any live `Enemy` instances. This bypasses `Enemy.on_die()` (which destroys the health bar sub-entity) and `unregister()`. If an enemy is alive when `main_menu()` runs (e.g. during a crash recovery), its health bar's sub-entities may survive.

### Verdict
ISSUES FOUND

---

## Track 5 — Bitmask Registry Correctness

### Findings

**Registration completeness:**
- Player: `register(self, Layers.PLAYER)` — `player_controller.py:25` ✓
- Enemy: `register(self, Layers.ENEMY)` — `enemy.py:15` ✓
- PlayerBullet: `register(b, b._layer)` in `BulletPool.acquire()` — `weapon.py:33` ✓
- EnemyBullet: same ✓
- Walls/blocks: not registered — correct; `can_hit()` returns False, bullets call `die()` on any wall hit ✓

**Unregister on every die path:**
- Enemy via `AliveEntity.die()` → `unregister(self)` ✓
- PlayerBullet.die() → `unregister(self)` → `pool.release()` → `unregister(bullet)` again — **double unregister** (harmless, dict.pop with None default, but redundant)
- EnemyBullet.die() — same double-unregister pattern
- Player: `destroy(player)` called directly — `unregister()` never called → **registry leak** each scene transition

**COLLISION_MATRIX symmetry:**
- `PLAYER_BULLET` mask = `ENEMY | WALL`; `ENEMY` mask = `WALL | PLAYER_BULLET` → symmetric ✓
- `ENEMY_BULLET` mask = `PLAYER | WALL`; `PLAYER` mask = `WALL | ENEMY_BULLET | PICKUP` → symmetric ✓
- `WALL` not in matrix (no entry) — intentional; walls do not initiate hits ✓

**Unused layers:**
- `Layers.PICKUP` defined at `collision_system.py:11`; in `COLLISION_MATRIX` as a target for `PLAYER`; no entity ever registered with it — dead forward declaration, not a bug

### Verdict
ISSUES FOUND (minor — double unregister, Player registry leak)

---

## Track 6 — level.json Data Quality

### Findings

**Duplicate position entries:**
- `[0.0, 0.0, -2.0]`: 8 entries (7 excess) with textures `white_cube.png`×6, `grass.png`×2
- `[2.0, 1.0, -5.0]`: 3 entries (2 excess), all `white_cube.png`
- `[2.0, 2.0, -5.0]`: 5 entries (4 excess), all `white_cube.png`
- `[3.0, 3.0, -6.0]`: 7 entries (6 excess), all `white_cube.png`
- **Total excess entries: 19.** Each creates a real `Entity` + `BoxCollider` at load time — z-fighting artifacts and wasted memory.

**Missing required fields:**
- All block entries have `type`, `position`, `texture` ✓
- Enemy entry (position `[5.0, 2.0, 1.0]`) has `type`, `position` — no `texture` field, which is correct (enemies don't use it) ✓

**Texture names:**
- `white_cube.png` and `grass.png` are both Ursina built-in textures. Ursina's asset loader strips file extensions before lookup, so these names should resolve correctly ✓

**Schema gaps (editor vs loader):**
- `level_editor.py save_level()` writes: `type`, `position`, `texture` for blocks; `type`, `position` for enemies ✓
- `main.py load_level()` reads: same fields ✓
- No schema mismatch in field names/types ✓
- **Visual mismatch:** `level_editor.py` enemy placeholder uses `scale=(1,2,1)`, `origin_y=-0.5`. `main.py load_level()` enemy placeholder uses `scale=(1.5, 3, 1.5)`, `y=+3`. Actual `Enemy` in gameplay uses `scale=(1.5, 3, 1.5)`. The level editor does not visually represent enemy size accurately — what you see in the editor is half the width and half the height of the real enemy.

### Verdict
ISSUES FOUND

---

## Track 7 — Ursina 8.3.0 / macOS Compatibility

### Findings

**GLSL 1.20 patch coverage:**
- `main.py` patches `unlit_shader` and `unlit_with_fog_shader` — the two shaders Ursina 8.3.0 assigns by default (`Entity.default_shader` and `Sky.shader`) ✓
- `level_editor.py` applies identical patches ✓
- UI entities (Buttons, Text) in Ursina typically use the same `unlit_shader` or no custom shader → covered ✓
- Cannot determine without runtime data whether any Ursina 8.3.0 internal system (e.g. particle systems, line renderers) uses a third shader that is not patched.

**Patch order — `level_editor.py` vs `main.py`:**
- `main.py`: `loadPrcFileData` (inside `if __name__`) → `Ursina()` → `_patch_shaders_to_glsl120()` → `window.color` → `render.setAntialias` ✓
- `level_editor.py`: `loadPrcFileData('', 'model-cache-dir')` at **module level** (line 7, before `Ursina()`) + `loadPrcFileData(...)` inside `if __name__` → `Ursina()` → `_patch_shaders_to_glsl120()` → `window.color` → `render.setAntialias`
- **Observation:** The module-level `loadPrcFileData('', 'model-cache-dir')` disables model caching. This is harmless but means if `level_editor` is imported as a module (not run standalone), the PRC config is applied as a side effect at import time.
- Patch order is otherwise identical ✓

**Ursina 8.x API usage:**
- `raycast()` called with `(origin, direction, distance=..., ignore=..., debug=...)` — standard signature ✓
- `Entity` constructor kwargs — standard ✓
- `invoke(setattr, self, 'can_shoot', True, delay=...)` — standard ✓
- **BUG:** `main.py:304`: `window.on_resize = on_window_resize()` — this CALLS `on_window_resize()` immediately and assigns its return value `None` to `window.on_resize`. The function runs once (repositioning the window on startup) but is never registered as a resize callback. Subsequent window resizes do not re-center the window. Should be `window.on_resize = on_window_resize` (no parentheses).

Regression checks on previously fixed bugs:
- Black screen (GLSL patch): PASS — patch still present and applied before any Entity ✓
- `sky_default` texture: PASS — `Sky()` called with no texture argument ✓

### Verdict
ISSUES FOUND

---

## Track 8 — Code Quality & CLAUDE.md Rule Compliance

### Findings

**a. `entity.name == 'enemy'` string checks:**
- `main.py:19`: `e.name in ['level_block', 'level_enemy']` — level editor placeholder cleanup; these are deliberately named non-gameplay entities. Acceptable.
- `main.py:49`: `e.name not in ['main_camera']` — scene clear guard. Acceptable.
- `main.py:88`: `e.name == 'level_enemy'` — finding placeholder entities to spawn from. Acceptable (placeholder, not live enemy).
- `main.py:168`: `e.name in ['level_block', 'ground', 'main_sky']` — teardown by name. Acceptable.
- **`enemy.py:62`**: `hit.entity.name != 'player'` — **RULE VIOLATION.** `_is_occluded()` uses name check to determine if the occlusion ray hit the player. Should use bitmask: `getattr(hit.entity, '_collision_layer', 0) == Layers.PLAYER` or `not isinstance(hit.entity, Player)`. If a second entity is ever named 'player', this silently breaks.

**b. `raycast(ignore=[ClassName])` class-based ignores:**
- All ignore lists inspected: `player_controller.py:165,182,235`, `weapon.py:91,163`, `enemy.py:58`, `collision_system.py:70–73` — all use **instances**, not classes ✓

**c. Bare `except:`:**
- `main.py:21` and `main.py:51`: `except Exception:` ✓
- No bare `except:` found ✓

**d. `destroy()` called directly on AliveEntity subclasses:**
- `enemy.py:81`: `destroy(self.health_bar)` — `HealthBar` is NOT an `AliveEntity`, so this is acceptable ✓
- `main.py:164`: `destroy(player)` — `Player` is NOT an `AliveEntity` (it extends `FirstPersonController`) ✓
- `main_menu()` loop may call `destroy(e)` on live `Enemy` instances — this bypasses `die()` (flagged in Track 4). Technically violates the spirit of rule 3 though Player is the direct target.
- No direct `destroy()` on a clearly identified `AliveEntity` subclass ✓

**e. `import time` namespace collision:**
- `weapon.py:5`: `import time as _time` ✓ — aliased correctly per CLAUDE.md rule 7
- No other project file imports `time` at module level ✓

**f. Circular import risks:**
- `main.py` → `player_controller`, `enemy`, `health_bar`, `collision_system`: no cycles ✓
- `player_controller.py` → `weapon`, `health_bar`, `collision_system`: no cycles ✓
- `weapon.py` → `collision_system` only: no cycles ✓
- `enemy.py` → `health_bar`, `collision_system` at module level; `weapon` via lazy import in `shoot()` ✓
- `health_bar.py` → ursina only ✓
- `collision_system.py` → ursina only ✓
- No circular imports at module load time ✓

**Additional: `swept_cast()` unused:**
- `collision_system.py` lines 66–80: `swept_cast()` is defined and exported but called nowhere in the codebase. Dead code.

### Verdict
ISSUES FOUND

---

## Prioritised Fix List

| # | Severity | Track | File | Line(s) | Issue | Recommended Fix |
|---|----------|-------|------|---------|-------|-----------------|
| 1 | HIGH | 4/3 | `main.py`, `weapon.py` | 85–87, 194 | Weapon (parent=camera) not destroyed when player destroyed → entity accumulates on each Play→Menu→Play cycle | Add `destroy(player.weapon)` before `destroy(player)` in `start_game()`, or make Weapon a child of player |
| 2 | HIGH | 4/5 | `collision_system.py`, all files | 93, 25, 15, 33 | `CollisionManager._tracked` always empty — `collision_manager.add()` never called; spatial grid, `query_layer()`, `query_near()` all non-functional | Replace standalone `register()` calls in Player/Enemy/BulletPool with `collision_manager.add()`, and `unregister()` with `collision_manager.remove()` |
| 3 | HIGH | 3 | `player_controller.py` | 228–232 | `_swept_blocked()` scans all `scene.entities` to build ignore list on every call (up to 3× per frame) | Use `collision_manager.query_layer()` once Track 2 is fixed, or cache the bullet entity list |
| 4 | HIGH | 8 | `enemy.py` | 62 | `hit.entity.name != 'player'` violates CLAUDE.md rule 1 — name-based type check | Replace with `getattr(hit.entity, '_collision_layer', 0) != Layers.PLAYER` |
| 5 | MEDIUM | 7 | `main.py` | 304 | `window.on_resize = on_window_resize()` calls the function and assigns None — resize callback never registered | Change to `window.on_resize = on_window_resize` (remove parentheses) |
| 6 | MEDIUM | 2/5 | `player_controller.py`, `main.py` | 25, 164 | `destroy(player)` bypasses `unregister()` → `Layers.PLAYER` leaks in `_registry` each scene transition | Make Player extend AliveEntity and call `player.die()`, or add explicit `unregister(player)` before destroy |
| 7 | MEDIUM | 2/4 | `health_bar.py` | 87–91 | `HealthBar.destroy()` override is dead code — Ursina's `destroy()` does not call `entity.destroy()` — 2D health bar text leaks | Remove the override; instead call `destroy(self.text)` explicitly in callers, or parent text to the health bar instead of camera.ui |
| 8 | MEDIUM | 4 | `main.py` | 47–52 | `main_menu()` loop calls `destroy(e)` directly on live AliveEntity instances — bypasses `die()`, `unregister()`, `on_die()` | Iterate enemies and call `.die()` explicitly before the blanket destroy loop, or filter AliveEntity instances |
| 9 | MEDIUM | 6 | `level.json` | — | 19 excess duplicate block entries at 4 positions — creates z-fighting and wastes entity/collider slots | Dedup on next editor save; add dedup pass to `save_level()` in `level_editor.py` |
| 10 | LOW | 5 | `weapon.py` | 107–108, 177–178 | Double `unregister()` in `PlayerBullet.die()` and `EnemyBullet.die()` — harmless but redundant | Remove `unregister(self)` from `die()` in both bullet classes; let `pool.release()` be the sole unregister |
| 11 | LOW | 2 | `weapon.py` | 52–67 | Dead code path in `PlayerBullet.__init__` uses `enabled=False` — violates rule 9 and is never reached | Delete the `if _pooled and position is None` branch |
| 12 | LOW | 6 | `level_editor.py` | 109–117, 162–172 | Enemy placeholder scale `(1,2,1)` in editor does not match actual enemy scale `(1.5,3,1.5)` in game — placement is visually inaccurate | Update editor enemy entity scale to `(1.5, 3, 1.5)` |
| 13 | LOW | 5 | `collision_system.py` | 11 | `Layers.PICKUP` defined and in COLLISION_MATRIX but no entity ever registers it | Leave as forward declaration (fine); add comment marking it unimplemented |
| 14 | LOW | 3/4 | `collision_system.py` | 66–80 | `swept_cast()` is defined but never called — dead code | Remove or add a call site; currently it adds ignore-list scanning cost if ever used naively |

---

## Tech Debt Additions

These items should be added to the Known Tech Debt table in `CLAUDE.md`:

| Issue | Location | Priority | Notes |
|---|---|---|---|
| Weapon entity not destroyed with player — accumulates across sessions | `main.py:85–87`, `weapon.py:194` | High | Weapon is parented to camera not player; needs explicit destroy or parent change |
| `CollisionManager._tracked` always empty — spatial grid never populated | `collision_system.py:93`, `player_controller.py:25`, `enemy.py:15`, `weapon.py:33` | High | All registrations use standalone `register()` not `collision_manager.add()`; query_layer/query_near non-functional |
| `_swept_blocked()` allocates scene.entities ignore list on every call | `player_controller.py:228–232` | Medium | O(N) scan up to 3× per movement frame; fix after CollisionManager integration |
| `window.on_resize` callback incorrectly registered | `main.py:304` | Low | `on_window_resize()` called once at startup, not set as callback; one-line fix |
| `HealthBar.destroy()` override never called by Ursina | `health_bar.py:87–91` | Medium | Ursina's `destroy()` doesn't invoke entity method; 2D health bar text leaks |
| Enemy name-check in `_is_occluded` | `enemy.py:62` | Medium | `hit.entity.name != 'player'` — use bitmask instead |

---

## Patterns Worth Capturing

**1. CollisionManager.add() vs standalone register() — always use add() for tracked entities**
When `CollisionManager` exists as a spatial index, entities that need spatial queries must be registered through `collision_manager.add()`, not the standalone `register()`. Only `register()` sets the bitmask attributes; only `add()` populates the tracked set and spatial grid. Using `register()` alone gives bitmask filtering but no spatial queries.

**2. Parenting determines destroy cascade — weapon must be child of player**
Ursina's `destroy()` propagates to children of the destroyed entity. If a sub-entity (like Weapon) is parented to a global node (camera) rather than its logical owner (player), it will not be destroyed when the owner is. Always parent owned sub-entities to their owner, or maintain explicit destroy references.

**3. Ursina entity.destroy() override is dead code — override `on_die()` instead**
Ursina's `destroy()` function does not call Python's `entity.destroy()` method. Custom cleanup logic placed in a `destroy()` override will never run. Use `AliveEntity.on_die()` for cleanup in managed entities, or call sub-entity destroys explicitly at the call site.

---

## Footguns Worth Capturing

**1. Ursina destroy() does not call entity.destroy() — override is dead code**
Context: Tried to override `HealthBar.destroy()` to auto-clean up sub-entities parented outside the hierarchy.
Symptom: Sub-entities (text parented to camera.ui) survive after the health bar is destroyed.
Root cause: Ursina's `destroy()` function operates at the Panda3D NodePath level — it removes the entity from its parent's children list and schedules NodePath removal. It does not call any Python method on the entity object.
Fix: Use `AliveEntity.on_die()` for cleanup, or explicitly call `destroy(sub_entity)` at every destroy call site. Never rely on `entity.destroy()` being invoked by Ursina.

**2. CollisionManager.add() vs register() — two separate registries**
Context: `CollisionManager` and standalone `register()` both exist; entities used standalone `register()` throughout.
Symptom: `collision_manager.query_layer()` and `query_near()` always return empty lists; spatial grid never populated.
Root cause: Standalone `register()` sets bitmask attributes and updates `_registry` dict. `collision_manager.add()` additionally adds to `_tracked` set and spatial grid. They are not the same operation.
Fix: Call `collision_manager.add(entity, layer)` instead of `register(entity, layer)` when the entity needs spatial query support.

**3. window.on_resize = fn() vs fn — callback vs return value**
Context: Tried to register a window resize callback in Ursina.
Symptom: Callback runs once on startup, never again on resize.
Root cause: `window.on_resize = fn()` calls `fn` immediately and assigns its return value (None). Should be `window.on_resize = fn` (no parentheses) to assign the function itself.
Fix: `window.on_resize = on_window_resize` — pass the function reference.

---

## Resolution

**Date resolved:** 2026-05-20

### Issues fixed (14/14)

| # | Severity | Status | Notes |
|---|----------|--------|-------|
| 1 | HIGH | FIXED | Weapon + crosshair now destroyed before player in both `start_game()` and `return_to_main_menu()`. Reparenting Weapon to player deferred — camera-space positioning would break gun's screen-fixed position. |
| 2 | HIGH | FIXED | All entity registrations switched to `collision_manager.add()`. `AliveEntity.die()` now calls `collision_manager.remove()`. `BulletPool.release()` now calls `collision_manager.remove()`. Player teardown adds explicit `collision_manager.remove(player)`. |
| 3 | HIGH | FIXED | `_swept_blocked()` now uses `collision_manager.query_layer(Layers.PLAYER_BULLET) + query_layer(Layers.ENEMY_BULLET)` — O(1) lookup vs O(N) scan. Unblocked by #2. |
| 4 | HIGH | FIXED | `_is_occluded()` uses `getattr(hit.entity, '_collision_layer', 0) != Layers.PLAYER` — bitmask check, not name check. |
| 5 | MEDIUM | FIXED | `window.on_resize = on_window_resize` (no parens). |
| 6 | MEDIUM | FIXED | Explicit `collision_manager.remove(player)` before every `destroy(player)`. Player does not extend AliveEntity — `FirstPersonController` conflict risk noted; explicit remove is safer. |
| 7 | MEDIUM | FIXED | `HealthBar.destroy()` override removed. 2D health bar text kept as `parent=camera.ui` (preserves screen-space positioning); callers now explicitly `destroy(player.health_bar.text)` then `destroy(player.health_bar)` at both teardown sites. |
| 8 | MEDIUM | FIXED | `main_menu()` runs `e.die()` on all live `AliveEntity` instances before the blanket destroy loop. |
| 9 | MEDIUM | FIXED | `level.json` deduped 86→65 entries (21 removed). `save_level()` in `level_editor.py` now deduplicates by `(type, position)` key before writing. |
| 10 | LOW | FIXED | Removed `unregister(self)` from `PlayerBullet.die()` and `EnemyBullet.die()` — `pool.release()` is the sole unregister path via `collision_manager.remove()`. |
| 11 | LOW | FIXED | Dead `if _pooled and position is None` branches deleted from both `PlayerBullet.__init__` and `EnemyBullet.__init__`. `_pooled` param retained in signature (harmless, still accepted). |
| 12 | LOW | FIXED | Enemy placeholder scale updated to `(1.5, 3, 1.5)` in all three locations in `level_editor.py`: `toggle_mode()`, `input()`, `load_existing_level()`. |
| 13 | LOW | FIXED | Added comment `# 32 -- forward declaration; no entity registers this yet` on `Layers.PICKUP`. |
| 14 | LOW | FIXED | Added `# NOTE: unused — candidate for removal if no call site added by next audit` above `swept_cast()`. |

### New tech debt introduced by fixes

None. No new entity types, collision authorities, or module-level globals added.

### `weapon.py` import cleanup (side effect of #10)

`unregister` and `register` removed from `weapon.py` imports — no longer called directly; `collision_manager` handles both operations.
