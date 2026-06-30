---
tags: [versioning, changelog, release]
date: 2026-05-20
description: "Canonical public version history and internal tag↔public version mapping for Ivan's 3D Engine — authoritative source for release numbers"
links: [[work/Index]], [[brain/North Star]]
---

# Version Map

Canonical public version history. `README.md` uses internal dev tags (`v1`–`v6`) during development; this file is the authoritative mapping to public semver numbers. At each release milestone, `README.md` is rewritten to public version numbers and this file is updated.

See [[work/Index]] for active work status. See [[brain/North Star]] for current dev focus.

## Version History

| Public Version | Status | Internal Tags | What it covers |
|---|---|---|---|
| v1.0 | Shipped | v1–v6 | Collision bitmask system, AliveEntity lifecycle, BulletPool, GLSL 1.20 shader patch, level editor, anti-aliasing, crosshair polish |
| v1.1 | Shipped | — | All 14 audit issues from [[work/audits/2026-05-20-full-audit]] — weapon accumulation, CollisionManager integration, O(N) swept ignore list, name-check rule violation, on_resize callback, Player unregister leak, HealthBar.destroy dead code, main_menu AliveEntity bypass, level.json dedup, double unregister, dead code cleanup, editor enemy scale, swept_cast note |
| v1.2 | Shipped | — | Unity-feel level editor overhaul; `Game` state-machine class; `_clear_gameplay_entities()` canonical teardown; JSON schema expansion (enemy type, HP, rotation, block colour); 8 editor features (snap, undo/redo, multi-select, inspector, hierarchy, gizmos, bookmarks, play-in-editor). See [[work/active/v1.2-level-editor-overhaul]] |
| v1.2.3 | Audit complete, fix session pending | — | Full-project re-audit 2026-05-20: 12 findings (0 CRITICAL, 2 HIGH, 6 MEDIUM, 4 LOW). v1.1 all closed; v1.2 5 HIGH closed (1 still partially open), 9 carry-overs. Two HIGH for fix session: hierarchy/inspector/tray scroll zooms EditorCamera; drag-and-drop ghost ray math wrong. See [[work/audits/2026-v1.2.3-full-audit]] |
| v1.3 | Shipped 2026-06-26 | — | Asset import pipeline (browser panel, drag-drop, texture/model pickers); asset hot-reload in level editor. See [[work/archive/2026/v1.3-asset-import-pipeline]] |
| v1.4 | Shipped 2026-06-30 | — | Pluggable enemy behaviour trees — patrol / attack / flee state composition (5 compositors+decorators, 5 leaf nodes, 4 named presets, `level.json` `behaviour` field, editor preset+waypoint UI). See [[work/archive/2026/v1.4-enemy-behaviour-trees]] |
| v1.5 | Planned | — | Trigger/zone system (doors, checkpoints, kill planes); Weapon inventory API (multi-weapon, ammo pickups, switch animations). See [[work/active/v1.5-gameplay-systems]] |
| v1.6 | Planned | — | Level editor refactor — split `level_editor.py` (4000+ lines) into smaller modules for human navigability and agent productivity. Blocked on v1.3–v1.5 shipping and a manual design review. See [[work/active/v1.6-level-editor-refactor]] |
| v2.0 | Planned — **public release milestone** | — | Python script modding system; packaged runtime via PyInstaller/Nuitka; full gamepad/controller input layer; procedural level generator. See [[work/active/v2.0-release]] |
| v2.x+ | Future | — | Networked multiplayer substrate (authoritative server, client-side prediction) — own milestone after v2.0 ships |

## Versioning Policy

Minor versions (1.x) do not break `level.json` or mod compatibility — all new fields are optional with documented defaults. Major versions (x.0) mark a public release milestone or a breaking schema/API change that requires migration. Optional field additions to `level.json` are backwards-compatible minor changes and do not bump the major version. Field removals or renames require a major bump and a migration note in this file. Once v2.0 ships, the mod API surface is frozen at the minor version level — mods written for v2.0 must work on v2.1+ without changes.

## Changelog Note

`README.md` uses internal dev tags (v1–v6) during active development and will be rewritten to public version numbers at each release milestone. This file is the authoritative mapping between the two naming systems — when in doubt, this file wins.

## Post-v2.0 Parking Lot

Features deliberately deferred past v2.0:

- **Networked multiplayer** — full architecture shift requiring authoritative server and client-side prediction; warrants its own milestone post-v2.0 public release
- **True mod sandbox** (`RestrictedPython` / `subprocess` isolation) — v2.0 ships a trust-based model; sandboxing deferred until mod ecosystem exists to justify the complexity
- **`.fbx` model import** — requires an external converter toolchain (`blender --background` batch export or similar); deferred from v1.3 asset pipeline
