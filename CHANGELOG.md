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

- **Gizmo axis drag moved entities in the wrong direction when the camera was on certain sides of
  the selected object** — drag delta was computed in screen space without accounting for camera
  orientation, so the sign flipped when the camera orbited behind the object. Fixed by projecting
  each world axis into screen space each frame (`camera.getRelativePoint`) and dotting mouse
  velocity against that projection to determine signed magnitude.

### Added
- **Bottom asset tray** (`Scripts/level_editor.py`) — fixed panel at the bottom of the screen
  (dark background, full width, height ~0.12 units) with five hardcoded asset tiles:
  Cube (blue), Stone (grey), Metal (light grey), Wood (brown), Enemy (red).
  Tiles highlight on hover; mouse-wheel scrolls the tray horizontally.
- **Drag-and-drop placement** — click-and-hold a tile to pick up an asset; a semi-transparent
  ghost entity (alpha 0.5) follows the mouse raycast hit point, snapped to the current grid
  setting. Releasing the mouse over the viewport places the entity and pushes a
  `PlaceEntityCommand` (fully undoable with Ctrl+Z). Releasing over the tray or pressing Esc
  cancels the drag without placing. Enemy tiles spawn at `(1.5, 3, 1.5)` scale with
  `origin_y = −0.5` consistent with the existing enemy placeholder convention.

### Removed
- **Mode: Block / Enemy toggle button** — replaced by the asset tray. Asset type (block vs.
  enemy) is now selected by choosing the appropriate tile rather than a global mode switch.

### Changed
- **Left-click = place, Shift+click = select** — UX aligned with standard level-editor convention.
  Single left-click on any collidable surface (ground, wall, or existing block face) places a new
  entity. Shift+click adds/removes an entity from the selection. Right-drag box-select unchanged.
- Updated in-editor help overlay and `CLAUDE.md` to reflect new click bindings.
- **Karpathy guidelines skill installed** — `multica-ai/andrej-karpathy-skills` placed at
  `.claude/skills/karpathy-guidelines/SKILL.md` and imported via `@` directive at the top of
  `CLAUDE.md`. Guidelines (think before coding, simplicity first, surgical changes,
  goal-driven execution) are now active every Claude Code session automatically.
- **`mouse.direction` replaced with derived ray direction** (`Scripts/level_editor.py`
  `_update_ghost()`) — `mouse.direction` was removed in Ursina 8.3.0, causing an
  `AttributeError` every frame while dragging an asset from the tray. Fixed by deriving the
  ray direction from `camera.forward`, `camera.right`, `camera.up`, and `mouse.x`/`mouse.y`
  screen coordinates. Ghost placement and snap logic unchanged.

### Fixed (continued)
- **Mouse wheel over hierarchy / inspector panels zoomed EditorCamera** — scrolling while the
  cursor was over either side panel caused the camera to zoom instead of (or in addition to)
  the panel scrolling. Fixed by checking `_is_over_panel()` in `input()` before the event
  reaches `EditorCamera` and returning early to suppress the zoom.
- **Left-click on empty space while entities were selected placed a new block** — the placement
  branch fired on any collidable surface regardless of selection state, so deselecting by
  clicking empty space inadvertently created geometry. Fixed with a priority chain:
  clicking a tracked entity selects it; clicking empty space with a non-empty selection
  deselects only (no placement, no undo entry); placement only occurs when nothing is selected
  and the cursor is over a surface.
- **Block placement triggered during active gizmo drag or box-select** — held-key repeat events
  could fire `left mouse down` while a drag was in progress, creating phantom blocks. Fixed by
  guarding the entire `left mouse down` handler when `_gizmo_drag_axis is not None` or
  `_box_selecting` is `True`.

### Improved
- **Hierarchy panel scroll** — entity list now supports mouse-wheel scrolling when the cursor
  hovers the hierarchy panel. Shows 14 rows at a time; a proportional thumb on the right edge
  indicates scroll position. Hidden when all entities fit without scrolling.
- **Inspector even spacing** — property rows are distributed evenly across the full panel height
  instead of bunching at the top. A subtle separator line divides the transform group
  (Pos X/Y/Z, Rot Y, Scale X/Y/Z) from the entity group (HP).
- **GLSL 1.20 shader compatibility for macOS / OpenGL 2.1** — Ursina 8.3.0 ships
  `unlit_shader`, `unlit_with_fog_shader`, and `text_shader` with `#version 130/140` directives,
  causing all geometry to render black on Apple Silicon (Panda3D CocoaGraphicsPipe, GLSL 1.20
  max). A runtime monkey-patch could not win the race because `from ursina import *` at module
  top-level pre-compiles shader objects before any `if __name__ == '__main__'` code runs. Fixed by
  patching the three shader source files in the installed ursina package directly — `in`/`out`
  replaced with `attribute`/`varying`, `texture()` with `texture2D()`, named `out vec4` outputs
  replaced with `gl_FragColor`. Originals backed up as `.bak` files alongside each shader.

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
