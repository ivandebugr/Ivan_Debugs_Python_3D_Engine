---
description: "Things that have bitten before and will bite again — pitfalls, edge cases, and testing traps"
tags:
  - brain
---

# Gotchas

Things that have bitten before and will bite again.

## Ursina destroy() does not call entity.destroy() — override is dead code — 2026-05-20
**Context:** Tried to override `HealthBar.destroy()` to auto-clean up sub-entities parented outside the hierarchy (text parented to camera.ui instead of the health bar).
**Symptom:** Sub-entities survive after the parent entity is destroyed; text elements leak on scene transition.
**Root cause:** Ursina's `destroy()` function operates at the Panda3D NodePath level — it removes the entity from its parent's children list and schedules NodePath removal. It does NOT call any Python method on the entity object. A `def destroy(self)` override on an Entity subclass is unreachable via `destroy(entity)`.
**Fix:** Use `AliveEntity.on_die()` for cleanup in managed entities. Never rely on `entity.destroy()` being invoked. Explicitly call `destroy(sub_entity)` at every call site that needs cleanup.
**Source:** [[work/audits/2026-05-20-full-audit]]

## `camera.world_to_screen()` can raise or return None on off-screen entities — 2026-05-20
**Context:** Box-select in the level editor calls `camera.world_to_screen(e.world_position)` for every entity to test if it falls inside the drag rectangle.
**Symptom:** Crash or spurious selection when entities are behind the camera or far off-screen.
**Root cause:** `world_to_screen()` returns coordinates in clip space; entities behind the near plane produce coordinates outside [−1, 1] or raise exceptions in some Ursina versions. The return value may also be `None` if Ursina can't project the point.
**Fix:** Always wrap in `try/except` and guard `if screen_pos and ...` before testing coordinates.
**Source:** [[work/active/v1.2-level-editor-overhaul]]

## InputField focus swallows keyboard input — 2026-05-20
**Context:** Inspector panel uses Ursina `InputField` for position/rotation/HP values. After clicking into a field, keys like Delete, Ctrl+Z, and 1–5 fire in the `InputField` rather than the editor.
**Symptom:** Pressing `Delete` while an `InputField` is focused deletes text characters, not selected entities. Ctrl+Z triggers undo inside the field, not the editor undo stack.
**Fix:** Gate all keyboard shortcuts in `LevelEditor.input()` behind a check that no `InputField` has focus. In practice, `on_submit` is the only safe place to capture field values — don't try to intercept keypresses while a field is active.
**Source:** [[work/active/v1.2-level-editor-overhaul]]

## `Mesh(mode='line')` gizmo entities receive mouse hover events — 2026-05-20
**Context:** Gizmo axes are `Mesh(mode='line', thickness=3)` entities. Wanted line segments to be visual-only.
**Symptom:** `mouse.hovered_entity` reports the line mesh entity when cursor passes near the axis, even without a `collider=`. The cone tips (small cube entities with `collider='box'`) are the intended click targets.
**Fix:** Use the `name` attribute to distinguish gizmo hits. Both the line entities and the cube tips are named `editor_gizmo_*` and mapped in `axis_map`. The line entities don't have colliders but still show up as hovered — this is actually useful for detecting which axis the user is near.
**Source:** [[work/active/v1.2-level-editor-overhaul]]

## CollisionManager.add() vs register() — two separate registries — 2026-05-20
**Context:** `CollisionManager` and standalone `register()` both exist; all entities used standalone `register()`.
**Symptom:** `collision_manager.query_layer()` and `query_near()` always return empty lists; spatial grid never populated despite entities being visible in the scene.
**Root cause:** Standalone `register()` only sets bitmask attributes and updates `_registry` dict. `collision_manager.add()` additionally adds to `_tracked` set and spatial grid. They are two separate operations — using one does not imply the other.
**Fix:** Call `collision_manager.add(entity, layer)` for entities that need spatial query support. Use `collision_manager.remove(entity)` on teardown instead of standalone `unregister()`.
**Source:** [[work/audits/2026-05-20-full-audit]]

## window.on_resize = fn() vs fn — callback vs return value — 2026-05-20
**Context:** Registering a window resize callback in Ursina.
**Symptom:** The resize logic runs once on startup but never fires on actual resize.
**Root cause:** `window.on_resize = on_window_resize()` calls the function immediately and assigns its return value (`None`) to `window.on_resize`. No callback is registered.
**Fix:** `window.on_resize = on_window_resize` — assign the function reference without calling it.
**Source:** [[work/audits/2026-05-20-full-audit]]

## `PlaceEntityCommand.execute()` no-op breaks redo after undo — 2026-05-20
**Context:** Command-pattern undo/redo where entity is created before the command is pushed to the stack.
**Symptom:** Ctrl+Z removes a newly placed block correctly. Ctrl+Y (redo) does nothing — block stays gone, no error.
**Root cause:** `PlaceEntityCommand.execute()` is `pass` because the entity already exists at push time. On `undo()` the entity is destroyed and removed from `editor.blocks`. On `redo()`, `execute()` is called again — still `pass`. Entity is never recreated. The stack holds a stale NodePath reference; further Ctrl+Z may crash or silently skip.
**Fix:** Store a creation snapshot in `PlaceEntityCommand.__init__`. `execute()` must recreate the entity from the snapshot (on redo) and add it back to `editor.blocks`/`editor.enemies`. See `DeleteEntityCommand.undo()` for the recreation pattern.
**Source:** [[work/audits/2026-v1.2-audit]]

## `game.state` not reset by `_clear_gameplay_entities()` — only by `game.return_to_menu()` — 2026-05-20
**Context:** Level editor `_exit_play_mode()` calls `_clear_gameplay_entities()` directly instead of going through `game.return_to_menu()`.
**Symptom:** After exiting editor play mode, `game.state == Game.PLAYING`. Any code guard on this flag behaves as if gameplay is still active.
**Root cause:** `_clear_gameplay_entities()` is a pure teardown function — it destroys entities but never changes `game.state`. Only `game.return_to_menu()` transitions state to `MAIN_MENU`. Direct calls skip the state transition.
**Fix:** Always route teardown through `game.return_to_menu()` (it calls `_clear_gameplay_entities()` internally), or explicitly set `game.state = Game.MAIN_MENU` after direct calls.
**Source:** [[work/audits/2026-v1.2-audit]]

## Dead `"diagonal" in side_name` branch creates 201 eternal debug entities per Player — 2026-05-20
**Context:** `generate_raycast_points()` in `player_controller.py` attempts to use fewer ray origins for diagonal corners.
**Symptom:** 201 `eternal=True` entity visuals created per Player spawn (all disabled; no visible impact but inflate `scene.entities` and GC burden at teardown).
**Root cause:** Diagonal side names are `"front_right"`, `"back_left"`, etc. — none contain the literal string `"diagonal"`. The `if "diagonal" in side_name` branch is always `False`. All 8 sides fall through to the 5×5 column grid, creating 200 + 1 ceiling = 201 entities instead of the intended ~121.
**Fix:** Rename diagonal side names to include `"diagonal_"` prefix, or restructure the loop. Also set `draw_raycasts=False` by default since debug visuals should be opt-in.
**Source:** [[work/audits/2026-v1.2-audit]]

## Block `rotation` written to JSON but silently ignored on load — 2026-05-20
**Context:** `_build_level_data()` serializes block `rotation` correctly. `load_level()` and `_spawn_gameplay_from_snapshot()` create entities without passing `rotation=`.
**Symptom:** Rotated blocks in the editor appear un-rotated in both the main game and play-in-editor mode. JSON is written correctly but silently ignored on read.
**Root cause:** `rotation=tuple(entity_data.get('rotation', [0,0,0]))` was omitted from the entity constructor in both `load_level()` (main.py) and `_spawn_gameplay_from_snapshot()` (level_editor.py).
**Fix:** Add `rotation=tuple(entry.get('rotation', [0,0,0]))` to entity constructors in both read paths.
**Source:** [[work/audits/2026-v1.2-audit]]

## EditorCamera intercepts all scroll events — no built-in panel detection — 2026-05-20
**Context:** Scrolling mouse wheel over a `camera.ui` hierarchy panel in the level editor.
**Symptom:** Camera zooms instead of panel scrolling; no way to opt out per-panel via Ursina API.
**Root cause:** `EditorCamera`'s `input()` handler processes `scroll up`/`scroll down` unconditionally; `camera.ui` panels have no scroll event consumption mechanism.
**Fix:** In `LevelEditor.input()`, check `_is_over_panel()` for all UI panels before the event reaches `EditorCamera`; `return` early to suppress camera zoom when the cursor is over a panel.
**Source:** [[work/active/v1.2-level-editor-overhaul]]

## Left-click in Ursina fires for ALL clicks regardless of selection state — 2026-05-20
**Context:** Level editor with selection + gizmo system.
**Symptom:** Clicking empty space while entities are selected places a new block instead of deselecting.
**Root cause:** Ursina's `input()` receives every click; there is no built-in "selection mode" vs "placement mode" — the handler must explicitly check selection/drag state before placement.
**Fix:** Priority chain in `input()` — gizmo drag guard → entity selection → deselect-if-selected → place. Deselect must not push to the undo stack.
**Source:** [[work/active/v1.2-level-editor-overhaul]]

## Ursina input() events broadcast to every Entity — `return` is not "consume" — 2026-05-20
**Context:** Tried to suppress mouse-wheel zoom in `EditorCamera` by `return`ing early from `LevelEditor.input('scroll up')` when the cursor was over a UI panel.
**Symptom:** Hierarchy list scrolls AND camera dollies on every wheel tick over a panel — both handlers fire.
**Root cause:** Ursina dispatches `input(key)` to every Entity that defines an `input` method. There is no per-event "handled" flag at the Python layer. `return` only ends *this* entity's handler — sibling entities (like `EditorCamera`) still receive the event independently.
**Fix:** Disable or gate the consuming entity. For scroll-over-panel suppression: set `EditorCamera.zoom_speed = 0` while cursor is over a panel (toggle in `update()`). The earlier Gotcha "EditorCamera intercepts all scroll events" had the right diagnosis but the wrong fix — `return` doesn't suppress; gating the camera does.
**Source:** [[work/audits/2026-v1.2.3-full-audit]]

## `mouse.x` / `mouse.y` are screen-normalized, NOT angular deflections — 2026-05-20
**Context:** Reconstructed a cursor pick-ray in `_update_ghost()` after `mouse.direction` was removed in Ursina 8.3.0 — treated `mouse.x`/`y` as degree angles times π/180.
**Symptom:** Drag-and-drop ghost in level editor lands at a world point near the cursor but consistently offset; "drag-and-drop is broken" from the user's perspective.
**Root cause:** `mouse.x` ranges roughly `[-aspect/2, +aspect/2]` and `mouse.y` roughly `[-0.5, +0.5]` in Ursina's `camera.ui` normalized coords. Multiplying by `fov * π/180` produces wildly miscalibrated tilt angles (~25° at the screen edge).
**Fix:** Don't reconstruct the ray manually. Use `mouse.hovered_entity` + `mouse.world_point` + `mouse.normal` — Ursina's built-in picker. These are populated every frame and work mid-drag. `update_model_preview()` in `level_editor.py` already uses this pattern correctly — the drag ghost should mirror it.
**Source:** [[work/audits/2026-v1.2.3-full-audit]]

## Drag preview / ghost entities must have `collider=None` — 2026-05-20
**Context:** Translucent ghost entity that follows the cursor for drag-and-drop placement.
**Symptom:** After first frame, picker returns the ghost itself; placement position computed relative to ghost's own face → ghost drifts away from cursor.
**Root cause:** Ursina's mouse picker returns the closest hoverable entity. A ghost with `collider='box'` becomes hoverable and recursively shadows the real surface.
**Fix:** `collider=None` on every preview/ghost entity, always.
**Source:** [[work/audits/2026-v1.2.3-full-audit]]

## `Enemy` without `origin_y` renders 1.5u below editor placeholder — 2026-05-20
**Context:** Editor `load_existing_level()` sets `origin_y=-0.5` on placeholders so visual base aligns with stored JSON Y. `Enemy.__init__` does NOT set `origin_y`.
**Symptom:** Enemies look correct in the editor; in-game they spawn with feet buried in the floor, centered on JSON Y. The v1.2.1 hotfix that removed `+1.5` upward drift in `load_level()` exposed this lower-half mismatch.
**Root cause:** Editor placeholder uses `origin_y=-0.5` (base-anchored); runtime `Enemy` uses default `0` (center-anchored). With `scale_y=3`, the live enemy renders 1.5 units lower than the placeholder.
**Fix:** Add `origin_y=-0.5` to `Enemy.__init__()`'s `super().__init__()` call — one line. Aligns editor and runtime conventions.
**Source:** [[work/audits/2026-v1.2.3-full-audit]]

## `Text(text='')` triggers IndexError in Ursina's align() — 2026-06-24
**Context:** `_build_asset_browser()`'s scroll-arrow `Text` entities (and two latent sites in `_update_inspector`/`_show_status_notice`) constructed or mutated a `Text` entity's `.text` to an empty string to hide it.
**Symptom:** Editor startup crash — `IndexError: list index out of range` in `Text.align()` at `ursina/text.py:408`.
**Root cause:** Ursina's `Text.start_tag`/`end_tag` default to `'<'`/`'>'` with `use_tags=True`. A literal `'<'` or `'>'` parses as an empty tag pair with zero content lines, and an empty string also produces zero lines — either case leaves `Text.align()` indexing `linewidths[-1]` into an empty list.
**Fix:** Never construct or set a `Text` entity's `.text` to `''`. Use `enabled=False`/`True` to hide/show a `Text` instead. For literal `<`/`>` glyphs, pass `use_tags=False` to the constructor (read from kwargs before the initial `self.text = text` assignment, so it takes effect immediately).
**Source:** CLAUDE.md Hard Constraint 17; CHANGELOG [1.2.6]

## `window.on_resize` is dead code in Ursina 8.3.0 — 2026-06-24
**Context:** Level editor window resize broke the 3D viewport + toolbar overlap; root-cause investigation during the v1.2.6 fix session.
**Symptom:** Resize-driven layout logic never runs on an actual window resize.
**Root cause:** Ursina never invokes `window.on_resize` (detail unconfirmed beyond CLAUDE.md's statement that it is dead code in this version).
**Fix:** Wrap `window.update_aspect_ratio` instead — Panda3D's `aspectRatioChanged` event invokes it. The wrapped version calls the original (which rescales `camera.ui` children's x-positions) then calls `_apply_layout()` to reset all managed elements to correct absolute positions.
**Source:** CLAUDE.md Hard Constraint 16; CHANGELOG [1.2.6]

## Camera lens aspect ratio is not auto-refreshed on resize — 2026-06-24
**Context:** Same v1.2.6 resize fix session — UI panels were repositioned correctly via `_apply_layout()`, but the 3D viewport itself remained distorted.
**Symptom:** 3D viewport stays stretched/squashed after a window resize even once UI panel positions are corrected.
**Root cause:** Panda3D's automatic lens update via `base.camLens` is unreliable on some macOS resize paths (per CLAUDE.md; underlying Panda3D mechanism unconfirmed beyond this).
**Fix:** `_apply_layout()` explicitly calls `camera.perspective_lens.set_aspect_ratio(aspect)` (and the orthographic equivalent) every time it runs — UI repositioning alone is insufficient.
**Source:** CLAUDE.md Hard Constraint 15; CHANGELOG [1.2.6]

## `color.rgb()` expects 0–1 floats, not 0–255 — Ursina 8.3.0
**Context:** Setting entity colours in Ursina 8.3.0 after migrating from a version that used 0–255 conventions.
**Symptom:** `color.rgb(80,120,200)` renders as white. Any colour call with values > 1 clamps to white on render.
**Root cause:** `color.rgb` is an alias for `rgba()` which returns `Color(r, g, b, a)` with **no division by 255** (`ursina/color.py`). Passing 0–255 ints produces `Color(80,120,200,1)` which clamps to white.
**Fix:** Use 0–1 floats everywhere. Use `color.rgb32()` only when you genuinely have 0–255 ints. `level.json` colours and `LevelEditor.BUILTIN_MODELS` are both 0–1.
**Source:** CLAUDE.md Ursina 8.3.0 Compatibility section

## `InputField.submit_on` defaults to `[]` — on_submit never fires — Ursina 8.3.0
**Context:** Wiring up an `InputField` to fire a callback when the user presses Enter.
**Symptom:** `on_submit` callback never fires even when Enter is pressed and `on_submit` is assigned.
**Root cause:** `InputField.submit_on` defaults to `[]`, so `on_submit` never fires until you set `field.submit_on = ['enter']`. Ursina then calls `self.on_submit()` with **no arguments** — the callback must be no-arg and read `field.text` itself. A `lambda val, ...` raises `TypeError`.
**Fix:** Set `field.submit_on = ['enter']` after construction. Make the callback no-arg: `lambda k=key, f=field: handler(k, f.text)`.
**Source:** CLAUDE.md Ursina 8.3.0 Compatibility section (v1.2.4 FIX 4)

## Pick ray uses `camera.lens.extrude`, not `camera.forward` — Ursina editor cursor
**Context:** Building a mouse pick ray for a free-cursor editor (not a locked FPS).
**Symptom:** Raycasts hit wrong geometry — ray goes through screen centre, not cursor position.
**Root cause:** `camera.forward` always rays through the screen centre. With a free editor cursor that is almost never at screen centre, the ray misses the intended target.
**Fix:** Build the cursor ray the way Ursina's own picker does: `camera.lens.extrude(Point2(mouse.x*2/window.aspect_ratio, mouse.y*2), near, far)`, then transform both points with `render.get_relative_point(camera, …)` and normalise. Combined with `ignore=[non-gizmo-tips]`, a handle wins even when a block overlaps it on screen. See `LevelEditor._cursor_ray()`.
**Source:** CLAUDE.md Ursina 8.3.0 Compatibility section (v1.2.4 FIX 1B)

## `BehaviourTreeFactory.build()` must never run on an editor placeholder — 2026-06-30
**Context:** v1.4 Step 8/9. The level editor shows behaviour config for enemy placeholders (plain `Entity`, not `Enemy`/`AliveEntity`). The tempting shortcut is to build the live tree at editor-load time the same way the runtime does.
**Symptom:** None today — because the code does NOT do this. The trap is latent: a future maintainer "improving" `load_existing_level()` / `_restore_editor_level()` to construct the tree on load would attach a live tree to an entity with no `alive` guard, no `collision_manager` registration, and no `game.state == PLAYING` context. The tree's leaf nodes would then read `enemy.player`, `enemy.health`, `enemy.chase_step` — none of which exist on an editor placeholder — and `tick()` would crash (or worse, silently no-op) the first time anything ticked it.
**Root cause:** Two read paths share `level_io.load_level_data()` but have opposite responsibilities. The **editor load path** (`load_existing_level`, `_restore_editor_level`) is a *config store* — it stashes the raw `behaviour` dict on `placeholder.behaviour_config` and stops. The **runtime path** (`main.py:start_game()`) and the **F5 play-spawn path** (`level_editor._spawn_gameplay_from_snapshot()`) are *factory consumers* — they build real `Enemy` objects and only then call `BehaviourTreeFactory.build()`.
**Fix:** Editor load stores `entry['behaviour']` verbatim, never the tree. `BehaviourTreeFactory.build()` is called in exactly three production sites — `enemy.py` (default), `main.py:start_game()`, and `level_editor._spawn_gameplay_from_snapshot()` (the F5 play path, which is runtime-equivalent). The editor load path is NOT one of them, by design. The positive convention this enforces is logged in [[brain/Patterns]] ("Editor is a config store, runtime is a factory consumer").
**Source:** [[work/archive/2026/v1.4-enemy-behaviour-trees]]; CLAUDE.md (v1.4 Step 8 constraint)

## Bare-string texture on the load path works, but bare-string model does not — 2026-06-26
**Context:** v1.3 asset-pipeline final integration audit. Models load via `_resolve_model(entry['model'])` at every site; textures are still assigned bare (`Entity(texture=entry['texture'])`) at four load sites (`main.py:168`, and `Scripts/level_editor.py` `_restore_editor_level`/`_spawn_gameplay_from_snapshot`/`load_existing_level`).
**Symptom:** None today — it works. The concern is that it *looks* like the Step-4 bug that broke models, so a future maintainer might "fix" it wrong or extend the bare pattern to a case that does break.
**Root cause:** Ursina's `load_texture` recursively globs the `assets/` folders and resolves a bare filename (e.g. `floor_stone.png`) to the real file; `load_model` does NOT — a bare path string double-nests against `application.asset_folder` and fails (that was the Step-4 model bug, fixed with `_resolve_model`). So the same code shape is safe for textures and broken for models. Verified headless: bare `floor_stone.png` loads the real 64×64 image; `''` → `.texture = None`, no crash; `Texture.name` carries the filename-with-extension, which is what `_build_level_data` saves.
**Latent risk:** Texture resolution relies on filenames being globally unique across `assets/` subfolders. If two subfolders ever hold the same filename, the bare-string glob could pick the wrong one. Safe fix when convenient: route those four sites through `_resolve_texture` the way models already go through `_resolve_model`. Not blocking — shipped per decision.
**Source:** [[work/archive/2026/v1.3-asset-import-pipeline#Final Integration Audit — 2026-06-26]]; CLAUDE.md (Step 4 `_resolve_model`/`_resolve_texture` notes)
