---
date:
description: "Living document of goals, focus areas, and aspirations — read at session start, updated when direction shifts"
tags:
  - brain
  - north-star
aliases:
  - Goals
  - Focus
---

# North Star

A living document of goals, aspirations, and current focus areas. Both you and Claude write to this. Claude reads it at the start of meaningful work sessions and references it when making suggestions.

v1.2 audit complete — 19 findings (0 CRITICAL, 4 HIGH, 9 MEDIUM, 6 LOW); fix session pending. See [[work/audits/2026-v1.2-audit]].

v1.2.3 full audit complete — 12 findings (0 CRITICAL, 2 HIGH, 6 MEDIUM, 4 LOW); fix session pending. RICE top-3: hierarchy/inspector/tray scroll zooms EditorCamera; drag-and-drop ghost ray math wrong; enemy `origin_y` mismatch with editor placeholder. See [[work/audits/2026-v1.2.3-full-audit]].

## v1.2 Shipped — 2026-05-20
All tracks complete. Game state machine live. Canonical teardown functional. Level editor is Unity-feel with 8 features.

**Shipped in v1.2:**
- `Scripts/game.py` — `Game` state-machine class + singleton; zero module-level globals remain in `main.py`
- `_clear_gameplay_entities()` — single canonical 6-step teardown path
- JSON schema: `colour`, `rotation` on blocks; `hp`, `enemy_type`, `rotation_y` on enemies — all backwards-compatible
- `Scripts/undo_redo.py` — command-pattern undo/redo stack (depth 50)
- Level editor: grid snap (1/0.5/0.25/Off), multi-select + box-select, inspector panel, hierarchy panel, transform gizmos (X/Y/Z), camera bookmarks (1–5, persisted), play-in-editor (F5)

**Next priority:**
- v1.3 — Asset import pipeline: browser panel, drag-drop import, texture/model pickers, asset hot-reload in level editor

See: [[work/active/v1.2-level-editor-overhaul]] (Resolution section), [[work/archive/version-map]]

## v1.2.6 Shipped — 2026-06-24
Startup crash fixed (`Text(text='')` → `IndexError` in `Text.align()`), window resize + camera lens aspect ratio fix, texture thumbnail rendering fix, standalone placement tray merged into the Models tab of the asset browser. Closed out — no longer "next," fully verified per `CHANGELOG.md` [1.2.6].

## v1.3 Shipped — 2026-06-26
All 7 Implementation-Order steps complete and verified by a cross-step integration audit (re-derived from code, not session logs). The level editor now has a full asset pipeline: drop a file in and use it immediately without editing config.

**Shipped in v1.3:**
- `Scripts/asset_registry.py` — framework-free `AssetRegistry`: scans `assets/textures|models|sounds` → `{name: path}` manifests, persists `assets/manifest.json` (gitignored), mtime-cache fast path, `poll()`-driven hot-reload callbacks (Steps 1 & 3)
- Asset browser panel — Textures/Models/Sounds tabs, scrollable thumbnail cards, built-in models merged into the Models tab, drag-to-place with ghost/snap/undo (Step 2)
- Texture hot-reload — `_on_texture_changed` re-uploads to subscribed entities live; model/sound log-only per spec (Step 3)
- Texture picker (Step 4) + Model picker (Step 5) — floating inspector overlays, blocks-only model swap, each pushes its own `ChangeTextureCommand`/`ChangeModelCommand` (independent undo). `_resolve_texture`/`_resolve_model` fix the bare-path load bug.
- Asset import (Step 6) — shipped as an **Import Asset toolbar button** + native file picker (Panda3D has no OS file-drop on any release); copy-on-import, extension routing, name-collision skip-with-notice, manifest rebuild, play-mode guard
- `level.json` `model` field (Step 7) — optional, default `'cube'`, omitted-at-default for backwards-compat; resolved everywhere via `_resolve_model`

One known risk carried forward (flagged, ships per decision): bare-string texture on the load path — works via Ursina's folder glob but not routed through `_resolve_texture` like models are. See [[work/active/v1.3-asset-import-pipeline#Final Integration Audit — 2026-06-26]] and [[brain/Gotchas]].

See: [[work/active/v1.3-asset-import-pipeline]], [[work/archive/version-map]]

## Current Focus

_What am I working toward right now?_

- **v1.4 — Enemy behaviour trees** (patrol / attack / flee state composition) is next per the v1.2–v2.0 roadmap. Not yet started or designed — gated on the manual test-checklist pass for v1.3 and a design pass before any code. See [[work/active/v1.4-enemy-behaviour-trees]].

## Goals

### Short-term (This Quarter)

-

### Medium-term (This Half)

-

### Long-term (This Year+)

-

## Aspirations

_What kind of engineer/person am I becoming?_

-

## Anti-goals

_What am I explicitly NOT optimizing for?_

-

## 2026-05-20 — Full roadmap to v2.0 crystallised

Immediate: v1.1 audit fixes [[work/audits/2026-05-20-full-audit]].

Sequence to public release:
- v1.2 — Level editor overhaul + Game state machine + schema expansion [[work/active/v1.2-level-editor-overhaul]]
- v1.3 — Asset import pipeline + hot-reload [[work/active/v1.3-asset-import-pipeline]]
- v1.4 — Enemy behaviour trees (patrol/attack/flee) [[work/active/v1.4-enemy-behaviour-trees]]
- v1.5 — Trigger/zone system + Weapon inventory API [[work/active/v1.5-gameplay-systems]]
- v1.6 — Level editor refactor: split `level_editor.py` into smaller modules [[work/active/v1.6-level-editor-refactor]]
- v2.0 — Modding + packaged runtime + gamepad + procedural gen — PUBLIC RELEASE [[work/active/v2.0-release]]
- v2.x — Networked multiplayer (own milestone, post-release)

Rules: each version is a separate Claude Code session; no version starts until the previous passes the manual test checklist in CLAUDE.md; the CLAUDE.md tech debt table is updated at session end; [[work/archive/version-map]] is the authoritative public↔internal tag mapping.

## Shifts Log

Record when focus changes, with date and reason.

| Date | Shift | Reason |
|------|-------|--------|
| 2026-05-20 | v1.2 shipped — focus moves to v1.3 asset import pipeline | All 4 tracks complete; editor is now Unity-feel |
| 2026-05-20 | Full v1.2–v2.0 roadmap planned | Post-audit; engine in clean state, unblocked for feature work |
| 2026-06-24 | v1.2.6 cleanup closed out — focus moves to active v1.3 work (Steps 4–7) | Startup crash, resize/camera, texture thumbnail, and tray-merge fixes all verified; [[work/active/v1.3-asset-import-pipeline]] Steps 1–3 already done, picking up at the texture picker |
| 2026-06-26 | Captured v1.6 — level editor refactor — as a forward-looking planned milestone | Not started or designed yet; deliberately sequenced after v1.3–v1.5 feature work and gated on a manual design review. See [[work/active/v1.6-level-editor-refactor]] |
| 2026-06-26 | v1.3 asset import pipeline shipped — focus moves to v1.4 enemy behaviour trees | Cross-step integration audit passed all 7 steps; one latent texture-load risk flagged (not blocking) and carried forward. See [[work/active/v1.3-asset-import-pipeline#Final Integration Audit — 2026-06-26]] |
|      | Created North Star | Initial setup |
