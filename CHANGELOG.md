# Changelog

All notable changes to Ivan's 3D Engine are documented here.
Versions follow [Semantic Versioning](https://semver.org/).

---

## [1.2.2] - 2026-05-20

### Fixed
- **Level editor placement broken** — left-clicking any surface now places a block/enemy as expected.
  Previously, clicking an existing block always triggered selection instead of using the block as a
  placement surface, making it impossible to build adjacent to existing geometry.
  Root causes: (1) selection logic short-circuited before the placement branch when
  `hovered_entity` was in `self.blocks`/`self.enemies`; (2) toolbar `Button` widgets (which have
  colliders) could intercept clicks and trigger phantom placements.

### Changed
- **Left-click = place, Shift+click = select** — UX aligned with standard level-editor convention.
  Single left-click on any collidable surface (ground, wall, or existing block face) places a new
  entity. Shift+click adds/removes an entity from the selection. Right-drag box-select unchanged.
- Updated in-editor help overlay and `CLAUDE.md` to reflect new click bindings.

---

## [1.2.1] - 2026-05-20

### Fixed
- Enemy Y position spawned 1.5 units above editor placement — removed hardcoded `y + 1.5`
  offset in `load_level()`; Y coordinate from `level.json` is now trusted directly.
- `PlaceEntityCommand.redo()` was a permanent no-op — constructor now captures a snapshot;
  `execute()` recreates the entity from that snapshot on redo.
- `game.state` stuck at `PLAYING` after play-in-editor exit — `game.state = Game.MAIN_MENU`
  now set after teardown in both happy path and fallback.
- 201 `eternal=True` debug ray-cast entities created per Player spawn — `draw_raycasts`
  default changed `True` → `False`; no extra entities created during normal play.
- `return_to_menu()` state stuck at `RETURNING_TO_MENU` on exception — `_clear_gameplay_entities()`
  wrapped in `try/finally` to guarantee state transition even on error.
- Block `rotation` not applied in play-in-editor spawn — `rotation=tuple(entry.get('rotation',
  [0,0,0]))` added to block `Entity` constructor in `_spawn_gameplay_from_snapshot()`.

---

## [1.2.0] - 2026-05-20

### Added
- **Game state machine** (`Scripts/game.py`) — replaces scattered module-level globals
  (`player`, `game_paused`, `pause_menu`) with a `Game` class singleton. States:
  `MAIN_MENU`, `PLAYING`, `PAUSED`, `RETURNING_TO_MENU`, `WIN`.
- **`_clear_gameplay_entities()`** in `main.py` — canonical, single-path scene teardown
  called exclusively via `game.return_to_menu()`.
- **Level editor v1.2** (`Scripts/level_editor.py`) — full Unity-feel editor with:
  snap (1.0 / 0.5 / 0.25 / Off), undo/redo (depth 50), multi-select, inspector panel,
  hierarchy panel, XYZ transform gizmos, camera bookmarks (slots 1–5), play-in-editor (F5).
- **`Scripts/undo_redo.py`** — command-pattern undo/redo stack with 6 command types:
  `PlaceEntityCommand`, `DeleteEntityCommand`, `MoveEntityCommand`, `ChangeTextureCommand`,
  `ChangeColourCommand`, `ChangePropertyCommand`.
- `level.json` schema v1.2 — blocks now carry `colour` and `rotation`; enemies carry
  `hp`, `enemy_type`, and `rotation_y`.
- `editor_prefs.json` — persists camera bookmarks and grid-snap setting across sessions.

### Fixed
- Weapon entity not destroyed with player on scene transition.
- `CollisionManager._tracked` always empty — all registrations now go through
  `collision_manager.add()`; spatial grid fully functional.
- `_swept_blocked()` O(N) `scene.entities` scan replaced with `collision_manager.query_layer()`.
- `window.on_resize` callback incorrectly assigned (extra parens removed).
- `HealthBar.destroy()` override was dead code — removed; callers explicitly destroy text first.
- `_is_occluded()` in enemy used `entity.name == 'player'` string check — replaced with
  `Layers.PLAYER` bitmask check.
- `level.json` had 86 duplicate block entries — deduped to 65; `save_level()` now
  deduplicates on every write.
- Double `unregister()` in bullet `die()` removed from both `PlayerBullet` and `EnemyBullet`.
- Dead `enabled=False` pre-allocation branches deleted from both bullet `__init__` methods.
- Enemy placeholder scale mismatch in level editor — all three instances updated to `(1.5, 3, 1.5)`.
- Player `unregister` leak on scene transition — explicit `collision_manager.remove(player)`
  added before every `destroy(player)`.
- `main_menu()` destroy loop bypassed `AliveEntity.die()` — explicit `die()` pass added first.

---

## [1.1.0] — pre-versioning (audit release)

### Added
- Collision bitmask system — `Layers` registry, `COLLISION_MATRIX`, `can_hit()`.
- `AliveEntity` mixin — idempotent `die()` lifecycle replacing ad-hoc `_destroyed` bool.
- `BulletPool` — module-level singletons `_player_bullet_pool` (30) and `_enemy_bullet_pool` (60);
  eliminates per-shot allocation. Pool bullets parked at `y = −10000` (no `enabled` toggle).
- `CollisionManager` spatial grid — `register()`, `unregister()`, `query_layer()`, `query_near()`.

### Fixed
- 5 critical bugs: bullet damage not applying, `raycast(ignore=)` passed classes not instances,
  wall tunnelling (swept player movement), double-destroy on entity death, startup crash.

---

## [1.0.0] — initial release

- First-person shooter on Ursina 8.3.0 / Panda3D 1.10.16.
- GLSL 1.20 shader patch for macOS OpenGL 2.1 / Apple Silicon compatibility
  (`_patch_shaders_to_glsl120()` in `main.py` and `level_editor.py`).
- MSAA 4× anti-aliasing.
- `Player` (FirstPersonController subclass) with 5-height swept raycast movement.
- `Enemy` with line-of-sight check, patrol, shoot behaviour.
- `Weapon` / `PlayerBullet` / `EnemyBullet` with swept raycast hit detection.
- World-space and screen-space `HealthBar`.
- JSON level format (`level.json`).
