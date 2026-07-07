---
date: 2026-07-07
description: "Per-file module map for ivans_3d_engine — what lives where, key classes, and load-bearing implementation notes"
tags:
  - reference
---

# Module Map — Ivan's 3D Engine

Per-file breakdown of the codebase. Migrated from CLAUDE.md (v1.6 restructure, 2026-07-07) to keep
the project's session-start file lean — read this when navigating or modifying a specific module,
not every session.

```
main.py                    — App init, window setup, shader patch via Scripts/compat.py
                             patch_shaders_to_glsl120() (called twice),
                             _clear_gameplay_entities(), PlayerHUD, PauseMenu, EndScreen,
                             load_level(), main_menu(), global update()/input()
                             PlayerHUD: owns crosshair, hint_text, health_bar ref (not lifetime);
                             stored as game.hud; show()/hide() toggle all elements together.
                             EndScreen(title): fullscreen overlay (parent=camera.ui) shown on WIN/
                             GAME_OVER. Bg + 2 Text children parented to self → destroy() cascades.
                             Global update() triggers WIN when count_layer(Layers.ENEMY)==0 in PLAYING.
                             Global input() handles R during WIN/GAME_OVER → return_to_menu()+main_menu().
Scripts/
  game.py                  — Game state-machine class (MAIN_MENU/PLAYING/PAUSED/RETURNING_TO_MENU/
                             WIN/GAME_OVER), module-level singleton `game = Game()`;
                             no Ursina import at class-def time.
                             Tracks: player, enemies, pause_menu, hud, win_screen, game_over_screen.
                             return_to_menu() calls _clear_gameplay_entities() inside try/finally.
                             trigger_win() / trigger_game_over(): idempotent (only fire from PLAYING),
                             set state, freeze time_scale, surface mouse, hide HUD, build EndScreen.
                             Scene transitions: PauseMenu.return_to_main_menu() → game.return_to_menu()
                             → main_menu(). _clear_gameplay_entities() calls reset_bullet_pools() FIRST
                             — before any referenced entity is destroyed. HealthBar sub-entities must
                             NOT use eternal=True (would block teardown).
  collision_system.py      — Bitmask Layers (PLAYER/ENEMY/PLAYER_BULLET/ENEMY_BULLET/WALL/PICKUP),
                             COLLISION_MATRIX, register/unregister/can_hit,
                             AliveEntity (die()/on_die() lifecycle, _alive property),
                             CollisionManager spatial grid (query_layer/query_near).
                             __all__ defined; fully documented. swept_cast() deleted (never existed here).
  player_controller.py     — Player(FirstPersonController) subclass; SWEPT_OFFSETS module constant;
                             5-height swept raycast movement (_swept_blocked); BoxCollider;
                             HealthBar (screen-space); create_collider_visualization() (debug lines,
                             eternal=True but enabled=False — safe until show_colliders=True);
                             on_destroy() unregisters from collision_manager.
  weapon.py                — POOL_SIZE_PLAYER=30, POOL_SIZE_ENEMY=60 constants;
                             BulletPool (acquire/release/active_count/reset);
                             PlayerBullet(AliveEntity), EnemyBullet(AliveEntity);
                             Weapon (parented to camera); module-level pool singletons;
                             get_enemy_bullet_pool() accessor; reset_bullet_pools() teardown helper.
                             _pooled param deleted. Pool parks at Vec3(0,-10000,0).
                             **Viewmodel camera**: VIEWMODEL_MASK=BitMask32.bit(7);
                             _setup_viewmodel_camera() (module singleton, idempotent, called from
                             Weapon.__init__) clears bit 7 from main camera mask + adds a 2nd Panda
                             camera (reuses base.camLens, parented to base.cam) on its own display
                             region at sort 15. Gun is routed onto bit 7 only + depth-test/write OFF
                             → renders in a later pass, on top of world, no wall clipping. Region
                             clear-depth stays OFF (clearing blanked the world on macOS GL 2.1).
  enemy.py                 — ENEMY_HP_DEFAULT/SHOOT_COOLDOWN/DETECTION_RANGE/ATTACK_RANGE/
                             OCCLUSION_INTERVAL TUNE constants; VALID_ENEMY_TYPES tuple;
                             Enemy(AliveEntity): hp/enemy_type/rotation_y params,
                             throttled occlusion raycast, lazy pool import in shoot().
  health_bar.py            — HealthBar(Entity): world-space (is_3d=True) or screen-space;
                             BAR_COLOR_FULL/MID/LOW/BG_COLOR module constants;
                             HealthBar._registry class list (maintained by __init__/on_destroy);
                             on_destroy() explicitly destroys camera.ui text (not cascade-destroyed).
                             No eternal=True anywhere in this file.
  level_editor.py          — Standalone editor entry point ONLY (v1.6 split, 103 lines).
                             _launch(): prc multisample → compat shader patch BEFORE Ursina()
                             (HC10) → hot-reloader disable (Ursina binds F5 to scene.clear!) →
                             window setup → ground plane → LevelEditor() + EditorCamera + hint
                             legend. __main__ = `app = _launch(); app.run()` — the module-level
                             `app` name and trailing app.run() are load-bearing for
                             tests/smoke_test_harness.py. Runnable: `python Scripts/level_editor.py`
  editor_core.py           — LevelEditor(Entity) core class (v1.6 split): EDITOR_GRID_SNAPS=
                             (1.0,0.5,0.25,None); BUILTIN_MODELS (Cube/Stone/Metal/Wood/Enemy/
                             Trigger/Pickup synthetic Models-tab entries); toolbar; selection/
                             box-select; grid snap; trigger/pickup placeholder factories
                             (_make_trigger_entity/_make_pickup_entity); _build_level_data();
                             save/load; prefs/bookmarks; input()/update() dispatchers (v1.2.4
                             priority chain: gizmo → picker → panels → browser → tool action).
                             Five collaborators reach shared state via editor back-ref: self
                             .hierarchy/.gizmo/.browser/.inspector/.playmode (each own module).
                             **Tool modes**: self._tool = 'move'|'place'. Move: left-click selects only.
                             Place: left-click on collidable non-editor surface places a block.
                             [↖ Move]/[+ Place] toolbar buttons call _set_tool(mode).
                             **Delete key**: accepts 'delete' AND 'backspace' (macOS). Editor
                             placeholders use destroy() not die() — plain Entity, not AliveEntity.
                             **_apply_layout()**: repositions all border-anchored UI from window.aspect_ratio;
                             invoked via wrapped window.update_aspect_ratio. Also refreshes camera lens.
                             Toolbar widths: _TOOLBAR_BTN_W_BASE × min(1, aspect/_TOOLBAR_REF_ASPECT).
                             **Toolbar**: horizontal strip above inspector. _TOOLBAR_Y=0.475. No ▶ glyph.
                             Toolbar buttons: play_button text always black; Move/Place black-when-active.
                             Stats strip (_stats_text): 'entities: N  colliders: N', refreshed ~1/s.
                             **eternal=True**: ALL persistent editor UI uses eternal=True (survives
                             play-in-editor teardown). Level blocks/enemies do NOT — they must be
                             destroyable. Theme: _THEME_* constants (0–1 floats).
                             Core keeps one-line delegators with original names (_refresh_hierarchy,
                             _refresh_behaviour_ui, _update_inspector, …) — undo_redo.py's commands
                             call them on the editor; do NOT rename or bypass.
                             **Panel collapse (v1.7 C1)**: self.panel_visible = {'hierarchy',
                             'inspector', 'browser': bool}; _toggle_panel(name) flips it and
                             re-runs _apply_layout(). _effective_hier_w/_effective_insp_w/
                             _effective_browser_h properties return the full constant when
                             visible, a thin _LAYOUT_CHEVRON_W strip when collapsed — every
                             layout call site across hierarchy/inspector/browser reads these,
                             not the raw _LAYOUT_* constants, so collapse reclaims space
                             consistently. Ctrl+H/I/B hotkeys + [H]/[I]/[B] chevron buttons.
                             **Layout presets (v1.7 C2)**: self._layout_presets, same shape/
                             persistence pattern as _bookmarks; Ctrl+Alt+1-5 saves, Alt+1-5
                             recalls (Alt-modified — Ctrl+1-5 and bare 1-5 were already camera
                             bookmarks).
  editor_hierarchy.py      — HierarchyPanel (v1.6 split; editor back-ref): left-hand list UI.
                             Single row-position formula _hier_row_y(visual_index) — NEVER add a
                             second inline y formula or scroll indicator will drift.
                             _hier_visual_rows() is the layout model (header/row tuples). Transient
                             row buttons/swatches are NOT eternal=True — destroy() is a no-op on
                             eternal entities (ursina/destroy.py:27), they would leak on every rebuild.
                             Search box (_hier_search_field): live filter via on_value_changed.
                             Collapse marker: ASCII [+]/[-] — OpenSans has no triangle glyphs.
                             Scroll-bar rgba(0.78,0.78,0.78,0.47).
  editor_gizmo.py          — GizmoController (v1.6 split; editor back-ref): X/Y/Z handle geometry
                             (build/refresh), per-frame axis drag + MoveEntityCommand push,
                             cursor_ray() pick helper, try_begin_drag() = input() Step 1.
                             Pick sweep short-circuits on e.is_empty() before .name (HC13).
                             **Drag model (v1.7 B1)**: plane-projection, not velocity — on grab,
                             _begin_drag() casts the cursor ray onto a camera-facing plane
                             containing the drag axis and records the grabbed axis-offset;
                             handle_drag() re-projects each frame so the grabbed point stays
                             pinned under the cursor regardless of drag speed. Falls back to the
                             old velocity model below _AXIS_SCREEN_LEN_MIN screen-space axis
                             length (axis near-parallel to view). Hover highlight: tips brighten
                             to white via _update_hover_highlight() before a drag is committed to.
  editor_browser.py        — AssetBrowser (v1.6 split; editor back-ref): full-width bottom strip
                             (_BROWSER_Y=-0.40, _BROWSER_H=0.20). Tabs: Textures|Models|Sounds.
                             Texture/model picker overlays; texture hot-reload (subscribe/poll/
                             reload on a 2s timer); import pipeline + native file picker;
                             drag-ghost placement; status-notice toast.
                             Textures load via Texture(Path(path)) — raw path strings fail
                             silently. _is_over_browser() suppresses EditorCamera zoom.
  editor_inspector.py      — InspectorPanel (v1.6 split; editor back-ref): right-hand panel.
                             9 fields (pos_x/y/z, scl_x/y/z, rot_x/y/z — v1.7 B2 added the Rot
                             row), texture swatch, model field, door-name field, and the three
                             mutually-exclusive lower-band sections (behaviour-tree config /
                             trigger actions / pickup config).
                             HP/rotation/colour/enemy_type still round-trip through level.json —
                             display-only simplification. Labels use world_scale=Vec3(15,15,1) +
                             setBin('fixed',41) on the Entity (NOT scale= which inherits tiny
                             panel scale → microscopic).
  editor_playmode.py       — PlayModeController (v1.6 split; editor back-ref): F5 round-trip.
                             _enter_play_mode snapshots via _build_level_data, hides UI, disables
                             EditorCamera; _spawn_gameplay_from_snapshot mirrors main.start_game()
                             (Player/Enemy+trees/TriggerZones/AmmoPickups, then game.start()).
                             _restore_editor_level() rebuilds placeholders from the snapshot.
                             _exit_play_mode: sets game.state=MAIN_MENU before try; except
                             ImportError only. `_play_mode` flag stays ON THE EDITOR (core/gizmo/
                             browser read it); snapshot + saved-camera state live here.
  compat.py                — patch_shaders_to_glsl120() shared by main.py and the editor entry
                             (v1.6 step 1 — closes the copy-paste tech-debt row). HC10 ordering
                             unchanged: call before Ursina(); main.py keeps its second
                             post-window-setup call.
  asset_resolve.py         — resolve_texture()/resolve_model() (v1.6 step 5, relocated from
                             undo_redo.py): bridge the framework-free asset_registry to Ursina
                             loaders. undo_redo imports them under the old private names.
  session_logger.py        — SessionLogger: stdlib-only; logger.log(level, msg) / logger.flush().
                             Levels: INFO | WARN | ERROR. Format: [HH:MM:SS.mmm] [LEVEL] message.
                             get_editor_logger()/get_game_logger() cached accessors — all editor_*
                             modules share one log file per run (SessionLogger() alone is NOT a
                             singleton). logs/session_YYYYMMDD_HHMMSS.log on exit (atexit).
  undo_redo.py             — Command pattern: UndoRedoStack (depth 50) + 10 command types:
                             PlaceEntity, DeleteEntity, MoveEntity, ChangeTexture, ChangeModel,
                             ChangeColour, ChangeProperty, ChangeBehaviour, ChangeTriggerActions,
                             ChangePickupConfig. _restore_entity() helper sets origin_y=-0.5 for
                             enemy redo. Commands call editor._refresh_* delegators by name —
                             keep those names stable in editor_core.
  level_io.py              — Canonical level data loader. load_level_data(path_or_list) returns
                             normalised list of entity dicts with all fields filled (position,
                             rotation, scale, colour, texture; enemies also hp/enemy_type/rotation_y).
                             Single source of truth — replaces 4 duplicate parsers in main.py
                             and level_editor.py. Owns parsing only; Entity construction stays
                             at call sites (placeholder vs editor entity vs real Enemy/Player).
  asset_registry.py        — v1.3 asset pipeline Step 1. Pure I/O layer, ZERO framework
                             dependencies (no Ursina, no Panda3D, no main/level_editor imports).
                             AssetRegistry scans assets/textures|models|sounds → {name: path}
                             manifests (self.textures/models/sounds); persists assets/manifest.json
                             on every rebuild(). Startup loads from manifest.json cache when recorded
                             mtimes still match disk (skips full rescan), else rebuilds.
                             get_texture_path/get_model_path/get_sound_path(name) -> str|None.
                             register_callback(category, fn) + poll() drive hot-reload: poll()
                             diffs os.stat().st_mtime per tracked file, fires fn(name, path) on change
                             (no background thread — editor calls poll() on a 2s invoke timer in a
                             later step). Module-level singleton `asset_registry`. All file I/O wrapped
                             in `except Exception` — a single bad file is skipped, never crashes.
                             assets/manifest.json is gitignored; folders kept via .gitkeep.
level.json                 — Saved level data (blocks + enemies); schema v1.2 with colour/rotation/hp
editor_prefs.json          — Camera bookmarks (slots 1–5) + grid snap, persisted across editor sessions
```

## Related

- [[work/archive/2026/v1.6-level-editor-refactor]] — the split that produced editor_core/hierarchy/gizmo/browser/inspector/playmode
- [[brain/Patterns]] — collaborator-with-owner-back-ref pattern used by the editor split
- [[brain/North Star]] — v1.6 shipped summary
