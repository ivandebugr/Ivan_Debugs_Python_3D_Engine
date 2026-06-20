# Audit Report — Ivan's 3D Engine (v1.2.3 cold read)

**Date:** 2026-05-22
**Scope:** main.py + Scripts/* + level.json + Scripts/color.py
**Pass type:** Diagnosis only — no code changes
**Reviewer persona stack:** karpathy-coder, senior-architect, code-reviewer, tech-debt-tracker, product-manager-toolkit

---

## TL;DR

The collision/AliveEntity/BulletPool refactor is in excellent shape — the three-authority rule is respected, hard rules from CLAUDE.md hold up, the singletons are clean. **Two issues are critical and previously undocumented:**

1. **`level.json` colour values are corrupted** (stored as `65025.0` = 255²). A unit-mismatch between `_build_level_data` (writes 0–1 floats) and the four loaders (multiply by 255) is amplifying values across save/load cycles.
2. **`Scripts/color.py` is dead, *and* mis-implemented** — its bottom block passes HSV arguments to `color.rgb()`. Not imported anywhere, so it has caused no visible bug, but anyone who `from Scripts.color import red` will get cyan.

Everything else is medium-low priority cleanup. The level editor's UX is functional but the right-side inspector + left-side hierarchy panels devour 42 % of horizontal viewport at 1280×720 — the single biggest polish win.

---

## Active Bugs

1. **`level.json` colour-channel inflation** — [level.json:11](../level.json#L11)
   Saved values are `65025.0` (= 255 × 255). The save path writes `actual_color.r` (assumed 0–1) but the load path passes `int(c * 255)` to `color.rgb()`. The roundtrip multiplies by 255 each cycle until `entity.color` returns values already in 0–255 space. **Result:** all current blocks load as white (Ursina clamps), and the file is unreadable by any future code that trusts the documented 0–1 schema. Fix order: pick one canonical unit (recommend 0–1 floats), patch the four loaders to stop multiplying, then heal the file (replace 65025 → 1.0).
2. **Block `scale` dropped in play-in-editor** — [Scripts/level_editor.py:1121-1130](../Scripts/level_editor.py#L1121-L1130)
   `_spawn_gameplay_from_snapshot()` creates blocks without passing `scale=`. Every block reverts to 1×1×1 in play mode, even though the snapshot contains the scale and `_build_level_data` writes it.
4. **`EnemyBullet._reset` divides by zero on coincident target** — [Scripts/weapon.py:144-150](../Scripts/weapon.py#L144-L150)
   `(target - position).normalized()` plus a follow-up `self.look_at(self.position + self.direction)` will crash if `target == position`. Caller in `enemy.py` adds Vec3(0,1.5,0) to position and Vec3(0,1,0) to target.position, so currently safe, but a future enemy that fires from its own head will tip this over.
5. **EnemyBullet uses hardcoded `distance=1` raycast** — [Scripts/weapon.py:159-165](../Scripts/weapon.py#L159-L165)
   PlayerBullet sweeps `direction * speed * dt + 0.2`; EnemyBullet always raycasts 1 unit ahead regardless of speed. At speed=10, dt≈0.016, the bullet only travels 0.16 units/frame but tests 1 unit forward, so a bullet can "hit" a wall it would never reach. Not a crash, but caps effective enemy bullet range artificially.
6. **`Player.update` self-teleport on HP≤0 leaves stale game state** — [Scripts/player_controller.py:122-126](../Scripts/player_controller.py#L122-L126)
   `TODO: call game.trigger_game_over()` — documented in CLAUDE.md. Until that lands, the player can be ground down to 0 indefinitely with no game-over trigger and no win condition for the enemies, breaking the core loop.
7. **`Player.input` fires weapon without grounded guard** — [Scripts/player_controller.py:128-130](../Scripts/player_controller.py#L128-L130)
   Mentioned in CLAUDE.md tech debt; flagging again because it is *also* unconditional on `game.state`. A `left mouse down` during PAUSED still routes here because `input()` runs whenever the Player exists. The `application.time_scale=0` freezes motion but bullet acquire() still runs.
8. **`start_game()` re-runs the teardown that `_clear_gameplay_entities()` already covers** — [main.py:212-220](../main.py#L212-L220)
   Documented. Risk is real: any future change to teardown order must be made in two places, and the inline version is missing `bullet pool reset`. Right now `game.player` is None on first run so this branch never executes; but if menu→play→menu→play happens, this runs the second-and-onward time.
9. **Three near-identical level loaders drift apart** — [main.py:72-106](../main.py#L72-L106), [Scripts/level_editor.py:1100-1110](../Scripts/level_editor.py#L1100-L1110), [Scripts/level_editor.py:1120-1130](../Scripts/level_editor.py#L1120-L1130), [Scripts/level_editor.py:1444-1472](../Scripts/level_editor.py#L1444-L1472)
   `load_level()`, `load_existing_level()`, `_restore_editor_level()`, `_spawn_gameplay_from_snapshot()` all reconstruct entities from JSON-shaped dicts. Three pass `scale`; one does not (bug #2). Three pass `rotation`; the gameplay-spawn path passes only `rotation` for blocks but not snapshot scale.
10. **`_apply_layout` runs at the end of `__init__` before `_load_prefs`** — [Scripts/level_editor.py:163-178](../Scripts/level_editor.py#L163-L178), [Scripts/level_editor.py:1482-1484](../Scripts/level_editor.py#L1482-L1484)
    `load_existing_level()` calls `self._load_prefs()` at its end, which may overwrite the snap button label. But layout is already applied. The snap button text is then changed by `_load_prefs`. No visual bug, but the call order is fragile — `_load_prefs` is logically "session-startup" and should not be called from inside the level loader.
11. **`_save_prefs()` is unguarded** — [Scripts/level_editor.py:943-949](../Scripts/level_editor.py#L943-L949)
    A write failure (disk full, permission, read-only mount) raises and propagates out of `save_level()`, which is called from `Ctrl+S` and from `save_level` after `_history.clear()`. The Ctrl+S path therefore commits an undo-stack reset *before* it knows whether the prefs save succeeded.
12. **`scene.focused_entity` is read but never set anywhere I can find in this repo** — [Scripts/level_editor.py:1223](../Scripts/level_editor.py#L1223)
    `isinstance(getattr(scene, 'focused_entity', None), InputField)` is the only guard preventing bookmark-recall keys (1–5) from firing while typing in the inspector. Ursina's `Entity.input()` does not set `scene.focused_entity`; that attribute is typically managed by `InputField` widgets in newer Ursina builds. Verify by typing "1" into an inspector field: today the camera may also jump to bookmark 1.
13. **`_update_ghost` reads `mouse.normal` without checking that `hovered` has a collider** — [Scripts/level_editor.py:735](../Scripts/level_editor.py#L735)
    For non-collider editor entities, `mouse.normal` may be a stale (1,0,0). The early-out for `editor_*` and `camera.ui` mostly catches this, but a stray collider-less scene entity could slip through and place at a wrong offset.
14. **Crosshair sized at `scale=(0.01, 0.01)`** — [main.py:24](../main.py#L24)
    That's ~7 px tall at 1280×720. Functional but indistinct against busy backgrounds; ranked under design recommendations rather than as a bug.
15. **`HealthBar` text never reads `value` updates if `is_3d=True`** — wait, it does (line 97-98). False positive — disregard.
16. **Bullet pool `acquire()` returns `None` silently** — [Scripts/weapon.py:38-44](../Scripts/weapon.py#L38-L44)
    No user-visible signal. With POOL_SIZE_PLAYER=30, sustained spray will quietly drop shots. Worth either logging or surfacing via the FPS counter, but not a defect — caller in `Weapon.shoot()` handles `None` cleanly.

---

## Hard Rule Violations

**None.** The repo holds the line on every rule in CLAUDE.md's "Key Rules" section:

| Rule | Status |
|---|---|
| No `entity.name == 'enemy'` for damage dispatch | ✅ `can_hit()` used everywhere; structural-name checks (`'level_block'`, `'level_enemy'`) are only in scene-cleanup loops |
| No classes in `raycast(ignore=...)` | ✅ All `ignore` lists are instance lists from `collision_manager.query_layer` or literal `[self, ...]` |
| No `destroy()` on AliveEntity | ✅ `Enemy.on_die` destroys sub-entity; bullets override `die()` to release back to pool |
| No `enabled` toggle on pool bullets | ✅ Park-at-Vec3(0,-10000,0) pattern is intact |
| No 4th collision authority | ✅ Three authorities only |
| `swept_cast()` not re-added | ✅ Absent |
| Pool position parking | ✅ |
| Shader patch order | ✅ Twice in main.py (pre-Ursina + post-window), once in editor |

---

## Tech Debt

1. **Shader patch duplication** — ~150 lines copy-pasted between [main.py:316-455](../main.py#L316-L455) and [Scripts/level_editor.py:1494-1632](../Scripts/level_editor.py#L1494-L1632). Already in CLAUDE.md tech-debt table; flagging to confirm the count and exact line ranges for the eventual extraction.
2. **Four near-identical entity loaders** — bug #9 above is also tech debt: a `level_io.py` with `load_entities(json_data, *, attach_collider=True, with_origin_y_for_enemies=True)` would consolidate the schema in one place.
3. **`Scripts/color.py` is dead** — Not imported anywhere (verified by `grep -r "from Scripts.color"`). Worse, its values are wrong (see color.py table below). Recommend either deletion or full rewrite + a real consumer.
4. **`session_logger.py` keeps log lines in memory and writes on `atexit`** — [Scripts/session_logger.py:26-36](../Scripts/session_logger.py#L26-L36). Editor crashes that bypass `atexit` (signal kills, Panda3D abort assertion) lose the entire session log — exactly when you most need it. Stream-write each line, or at least `flush()` periodically.
5. **Duplicate keys in `color.py`** — `white_cyan = color.rgb(102,255,255)` and `light_blue = color.rgb(102,255,255)` are bitwise identical. Adds to the "this file is broken" smell.
6. **`load_existing_level()` calls `_load_prefs()`** — [Scripts/level_editor.py:1484](../Scripts/level_editor.py#L1484). Conceptually wrong — prefs are not part of level state.
7. **`Player.create_collider_visualization` creates 12 eternal Entities** — [Scripts/player_controller.py:59-79](../Scripts/player_controller.py#L59-L79). Documented in CLAUDE.md; mentioning again because `eternal=True` on a debug-only entity means it survives every menu transition and Player respawn. Each `Player(...)` call creates 12 more orphan lines. Toggle to non-eternal or build lazily on first `show_colliders=True`.
8. **`Enemy.shoot()` uses `invoke(setattr, ...)` to reset cooldown** — [Scripts/enemy.py:107](../Scripts/enemy.py#L107). Works, but the invoke fires even after the enemy dies — `setattr(dead_enemy_instance, 'can_shoot', True)` is harmless but allocates a Panda3D task slot per shot. A `time`-based check inside `update()` is cheaper and self-cleans with the entity.
9. **`PlayerBullet._reset` and `EnemyBullet._reset` rebuild `direction.normalized()`** — [Scripts/weapon.py:92](../Scripts/weapon.py#L92), [Scripts/weapon.py:145](../Scripts/weapon.py#L145). No big deal, just a Vec3 alloc per shot.
10. **`PlayerBullet.update` recomputes `Vec3(self.position)` and `(self.direction * self.speed * time.dt).length()`** — [Scripts/weapon.py:102-103](../Scripts/weapon.py#L102-L103). Could cache `speed*dt` once. Sub-microsecond, included only for completeness.
11. **`HealthBar` text uses `camera.ui` parent for screen-space but `parent=self` for 3D** — [Scripts/health_bar.py:51-74](../Scripts/health_bar.py#L51-L74). Asymmetric ownership; documented in `on_destroy`. Future maintainers may not realise the screen-space text needs explicit cleanup.
12. **`level_editor.input` has two `'left mouse down'` branches** — [Scripts/level_editor.py:1254-1259](../Scripts/level_editor.py#L1254-L1259) and [Scripts/level_editor.py:1275-1300](../Scripts/level_editor.py#L1275-L1300). Both run on the same event; the tile-drag branch has an early `return`. Reads as accidental — a single dispatcher would be clearer.
13. **`UndoRedoStack._undo` and `._redo` are private but read directly in `input()`** — [Scripts/level_editor.py:1195-1205](../Scripts/level_editor.py#L1195-L1205). Cosmetic; add a `peek()` method.

---

## Architecture Risks

1. **`Scripts.game.return_to_menu` imports `main`** — [Scripts/game.py:42](../Scripts/game.py#L42). Documented but worth noting: this is a reverse dependency from a script module into the entry-point module. The lazy import inside the method avoids a startup cycle, but it forbids `Scripts/` from being imported without `main.py` present (e.g., for unit tests, packaging into a library, or running scripts in isolation). The same pattern appears in `_exit_play_mode` in the editor.
2. **`enemy.shoot` lazy-imports `weapon`** — Documented in CLAUDE.md. Healthy mitigation of the circular import. ✅
3. **Module-level state outside the sanctioned singletons:**
   - `_player_bullet_pool`, `_enemy_bullet_pool` (sanctioned — BulletPool singletons)
   - `HealthBar._registry` class list (sanctioned)
   - `_registry` dict in `collision_system.py` (sanctioned — internal to `register()`/`unregister()`)
   - `logger` in `level_editor.py` (sanctioned — SessionLogger singleton)
   - **No unsanctioned globals found.** ✅
4. **`level_editor.py` is 1,682 lines** — past the "god module" threshold. Six concerns live here: layout, asset tray, hierarchy, inspector, gizmo, undo/redo wiring, play-in-editor, level IO. Splitting along these seams would let each piece be tested independently. Lower priority than #1/#2 above because the editor is internal tooling.
5. **`collision_manager.update()` runs every frame from `main.update()`** — [main.py:510-511](../main.py#L510-L511). It iterates every tracked entity, including bullets whether parked or in flight. With POOL_SIZE_PLAYER+POOL_SIZE_ENEMY=90 plus enemies plus player, that's still O(n) which is fine for now — but bullet pools are `add()`'d on acquire and `remove()`'d on release, so they shouldn't be tracked while parked. Verify: yes, `release()` calls `collision_manager.remove(bullet)` — ✅ this is correct.
6. **`window.on_resize` chaining in `level_editor.py`** — [Scripts/level_editor.py:167-178](../Scripts/level_editor.py#L167-L178). The chain captures `_prev_on_resize` once at `__init__` time. If anything else later replaces `window.on_resize`, our handler is stranded. Low risk because no other code does this, but the pattern is brittle.

---

## color.py: Colors to Fix

`Scripts/color.py` is **not imported anywhere** (verified). Either delete the file or fix it. If kept, every entry below is wrong — the function is `color.rgb(r, g, b)` (0–255 ints or 0–1 floats), but the values look like `color.hsv(hue°, saturation, value)` arguments. Ursina exposes `color.hsv()` — use it.

| Name | Current call | What it actually renders as | Correct call |
|---|---|---|---|
| `smoke` | `color.rgb(0, 0, 0.96)` | Very faint blue | `color.hsv(0, 0, 0.96)` |
| `light_gray` | `color.rgb(0, 0, 0.75)` | Faint blue | `color.hsv(0, 0, 0.75)` |
| `gray` | `color.rgb(0, 0, 0.5)` | Dim blue | `color.hsv(0, 0, 0.5)` |
| `dark_gray` | `color.rgb(0, 0, 0.25)` | Very dim blue | `color.hsv(0, 0, 0.25)` |
| `black` | `color.rgb(0, 0, 0)` | Black ✅ (coincidentally correct) | `color.hsv(0, 0, 0)` or just keep |
| `red` | `color.rgb(0, 1, 1)` | Cyan | `color.hsv(0, 1, 1)` |
| `orange` | `color.rgb(30, 1, 1)` | Near-red (clamped) | `color.hsv(30, 1, 1)` |
| `yellow` | `color.rgb(60, 1, 1)` | Near-red (clamped) | `color.hsv(60, 1, 1)` |
| `lime` | `color.rgb(90, 1, 1)` | Near-red (clamped) | `color.hsv(90, 1, 1)` |
| `green` | `color.rgb(120, 1, 1)` | Near-red (clamped) | `color.hsv(120, 1, 1)` |
| `turquoise` | `color.rgb(150, 1, 1)` | Near-red (clamped) | `color.hsv(150, 1, 1)` |
| `cyan` | `color.rgb(180, 1, 1)` | Near-red (clamped) | `color.hsv(180, 1, 1)` |
| `azure` | `color.rgb(210, 1, 1)` | Near-red (clamped) | `color.hsv(210, 1, 1)` |
| `blue` | `color.rgb(240, 1, 1)` | Near-red (clamped) | `color.hsv(240, 1, 1)` |
| `violet` | `color.rgb(270, 1, 1)` | Near-red (clamped) | `color.hsv(270, 1, 1)` |
| `magenta` | `color.rgb(300, 1, 1)` | Pink-red | `color.hsv(300, 1, 1)` |
| `pink` | `color.rgb(330, 1, 1)` | Pink-red | `color.hsv(330, 1, 1)` |

Also: `light_blue = color.rgb(102, 255, 255)` is a literal duplicate of `white_cyan`. Pick one. The RGB-form entries at the top of the file (rows 2–25, 43–60) appear correct.

**Recommendation:** delete `Scripts/color.py` outright. It is dead, contains a buggy reimplementation of Ursina's own colour primitives, and removing it eliminates an entire class of "future foot-gun" without losing any used functionality.

---

## Design Recommendations — Level Editor

Ranked by impact-per-effort. UX critique below assumes a 1280×720 window.

1. **Shrink the side panels and centre them vertically.**
   At 90 % panel height (`_LAYOUT_PANEL_H = 0.9`), the hierarchy and inspector cover y=[-0.45, 0.45] — basically the entire viewport vertically — while leaving the top-left hint and top-right toolbar floating in a thin strip. Together with 20 % + 22 % widths, the editable scene area is **~58 % of horizontal × 90 % of vertical with two heavy slabs flanking it**. Drop panel height to 0.75 and centre the inspector around the toolbar so the toolbar sits inside the panel header. This single change makes the 3D view feel like the focus instead of a sliver.
2. **Add a status bar at the bottom-right (or center-top) that always shows snap value + selected count + drag state.**
   The current UX scatters this information: snap is a button label in the top-right, selection count is implicit in the hierarchy highlights, drag state is invisible. A single line `Snap: 1.0   Selected: 3   Mode: drag` at a fixed location reduces cognitive load and gives the editor a sense of "I am here, doing this".
3. **Better inspector field affordances.**
   `InputField(scale=(.15, .04))` is functional but the field looks like a sunken rectangle with no border or focus glow. Two fixes: (a) add a subtle background highlight on the focused field (Ursina has `InputField.highlight_color`), and (b) right-align numeric content so decimal points line up across X/Y/Z rows. Both are 10-line fixes that disproportionately raise perceived polish.

Lower priority (worth queuing):
- The asset tray is bottom-centre with only 5 tiles — feels empty at 1280 px wide. Consider compacting it to bottom-left half or aligning to a target tile count.
- Multi-line hint text overlaps where the SPAWN marker text would project in screen space when looking down. Move hint to a collapsible drawer.
- No undo/redo toast — the only feedback is in the session log file.

---

## Design Recommendations — Game

1. **No damage feedback.**
   When the player takes a hit, the only signal is a numeric drop in the corner health bar — at 0.4 × screen units wide and lower-left position, the eye won't catch it during combat. Add (a) a brief red screen-edge vignette on damage, and (b) a one-frame camera shake. Both are 30-line additions and dramatically improve game feel.
2. **Crosshair is 1 % of UI height (~7 px) and a flat red dot.**
   In Ursina's `circle` texture at that scale, it nearly disappears against red enemies. Two options: (a) increase scale to 0.018 and switch to a thin ring instead of a filled disc, or (b) keep size but add a 1 px white outline so it survives any background. Game-feel critical for an FPS.
3. **PauseMenu has Continue / Main Menu / Quit but no Restart Level.**
   When the player dies (eventually, once GAME_OVER is wired), the natural action is "try again", which currently routes through Main Menu → Play. Add a "Restart" button that calls `game.return_to_menu()` then `start_game()` in one step. Pre-requisite: the game-over screen must exist first, so this is paired with the v1.3 roadmap item.

Lower priority (worth queuing):
- The hint text is permanently visible — fades the player's attention away from gameplay. Show on first session, then toggle on `H` key.
- No HUD ammo readout, even as `bullet pool active_count()` — small thing but ties the existing pool infra into something the player sees.

---

## Recommended Fix Order (next 3 prompts)

The priority queue below balances **risk** (data loss > crash > rendering > polish) against **scope** (small surgical edits before larger refactors). Each prompt is sized to ≤ 2 files unless noted.

### Prompt 1 — Stop the colour data corruption (highest risk, smallest scope)
Files: `Scripts/level_editor.py`, `main.py`, `level.json`.
1. Pick canonical unit: **0–1 floats**, as `actual_color.r` already returns.
2. Change all four loaders to pass `color.rgb(c[0], c[1], c[2])` (no ×255) — Ursina accepts 0–1 floats.
3. Heal `level.json` by replacing every `65025.0` with `1.0`.
4. Add a one-line unit test or assertion that `colour` values in the file are ≤ 1.0.
5. While in the area, fix bug #2 (missing `scale` in `_spawn_gameplay_from_snapshot`).

Verify: save → quit → restart → load → colours unchanged. Save twice in a row — no drift.

### Prompt 2 — Delete `Scripts/color.py` and consolidate the four loaders
Files: `Scripts/color.py` (delete), new `Scripts/level_io.py`, `main.py`, `Scripts/level_editor.py`.
1. Delete `Scripts/color.py` — confirmed dead, fixes the HSV-as-RGB bug class entirely.
2. Extract a single `load_entities(json_data, *, with_collider=True)` into `Scripts/level_io.py`.
3. Replace the four duplicate loaders with calls to this function. Snapshot loaders pass `with_collider=True` for blocks, the gameplay spawn skips the placeholder pattern.
4. Add a `save_entities(entities)` partner with the canonical unit settled in Prompt 1.

Verify: editor → save → main.py loads identical scene. Editor → play → exit → identical editor scene. Diff `level.json` before/after: byte-identical for a no-op save.

### Prompt 3 — Wire WIN / GAME_OVER (close the v1.3 demo-loop gap)
Files: `Scripts/game.py`, `main.py`, `Scripts/player_controller.py`.
1. `game.trigger_game_over()` — sets state, builds a `GameOverScreen` (Continue → Main Menu, Quit), shows mouse cursor, `application.time_scale = 0`.
2. `game.trigger_win()` — same shape, different copy.
3. In global `update()` (after `collision_manager.update()`): `if game.state == Game.PLAYING and not game.enemies: game.trigger_win()`.
4. Replace the `Player.update` teleport-on-death block with `game.trigger_game_over()`.
5. Crosshair-sizing nudge + damage-flash from the game design section, since both touch Player and HUD anyway.

Verify: kill all enemies → WIN screen. Player HP→0 → GAME_OVER screen. Either screen → Main Menu → restart loop completes without dead NodePaths (regression target).

---

## Deferred (post-1.2.3)

Items found during the v1.2.4 level-editor fix pass that were **out of scope** for that pass
(each is cosmetic, pre-existing dead code, or would touch unrelated lines). Logged here per the
"defer >30-line / multi-concern changes" rule.

1. **Editor UI-chrome colours use 0–255 values** — [Scripts/level_editor.py](../Scripts/level_editor.py)
   toolbar buttons (`play_button` `color.rgb(120,60,100)`, Move/Place highlight colours), panel
   backgrounds, tray panel/tile/hover colours, hierarchy scroll bar, inspector separator, and the
   spawn marker all pass 0–255 values to `color.rgb`/`color.rgba`. Because `color.rgb` = `rgba` =
   `Color(r,g,b,a)` with **no /255** (verified in `ursina/color.py`), every one clamps to white/full
   when rendered. Cosmetic only — none of these are serialised — and the active/inactive tool-button
   highlight is still distinguishable (bright-white active vs. `dark_gray` inactive). The **ASSETS**
   colours were the in-scope subset (fixed in v1.2.4) because those get written to `level.json`.
   Fix when polishing editor visuals: divide each chrome colour by 255 (or switch to `color.rgb32`,
   which does divide). ~12 call sites.
2. **`LevelEditor.position_valid()` is dead** — defined, never called. Remove or wire into a
   placement-overlap guard.
3. **`LevelEditor.current_mode = 'block'`** is set in `__init__` and never read (superseded by the
   asset tray's per-tile type and `self._tool`).
4. **`load_existing_level()` calls `_load_prefs()`** — prefs are session state, not level state;
   the coupling is fragile (carried over from the v1.2.3 audit, item #10/Tech-Debt #6).
5. **`↖` (U+2196) glyph missing from the default Ursina font** — the "↖ Move" toolbar button logs
   `:text(warning): No definition … for character U+2196` and renders the arrow as a missing glyph.
   Kept the label to match the documented design; swap to an in-font glyph if the blank bothers.
6. **`_handle_gizmo_drag` (update path) duplicates the input() gizmo pick** — both can set
   `_gizmo_drag_axis`; harmless redundancy, but a single dispatcher would be clearer.

---

*End of audit.*
