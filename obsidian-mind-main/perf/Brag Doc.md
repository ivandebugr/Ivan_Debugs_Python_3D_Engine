---
description: "Index of quarterly brag notes — impact, competency evidence, technical growth, feedback per quarter"
tags:
  - perf
  - index
---

# Brag Doc

A running log of impact, wins, and growth. Each quarter is its own note — open the one you need.

## Current Year

| Quarter | Highlights | Review |
|---------|-----------|--------|
|         |           |        |

## 2026-07-06 — Shipped the pre-v1.6 closure pass ([1.5.1])
Closed both v1.5 tails in one session: authored the first curated level (`levels/v1.json` — doors, pickups, checkpoint/kill-plane gauntlet, all five behaviour presets live), built the checkpoint-respawn consumer, removed the weapon pre-grant, and ran the deferred §5 combined regression end-to-end through the smoke harness (which I also fixed — argv[0]/asset_folder had silently broken every scenario since v1.5). First decorator (`Cooldown`) exercised in the live frame loop with a measured 3-vs-8 fire-cadence proof. 113/113 unit tests; 7 clean commits. See [[brain/Key Decisions]] and CHANGELOG [1.5.1].

## 2026-05-20 — Shipped v1.2
Shipped v1.2 across four tracks in a single session: Game state machine (`Scripts/game.py`) replacing all module-level globals; `_clear_gameplay_entities()` as the single canonical 6-step teardown; `level.json` schema expanded with block colour/rotation and enemy HP/type/rotation — all backwards-compatible; 8 Unity-feel editor features (configurable grid snap, command-pattern undo/redo stack, multi-select + box-select, inspector panel, hierarchy panel, transform gizmos, camera bookmarks persisted to `editor_prefs.json`, play-in-editor F5 toggle). Two new core modules: `Scripts/game.py`, `Scripts/undo_redo.py`. See [[work/active/v1.2-level-editor-overhaul]].

## 2026-05-20 — Planned full v1.2–v2.0 roadmap

Designed and documented the complete feature roadmap from post-audit clean state to public release across five versions: level editor overhaul + Game state machine (v1.2), asset import pipeline with hot-reload (v1.3), pluggable enemy behaviour trees with patrol/attack/flee composition (v1.4), trigger/zone system + weapon inventory API (v1.5), and modding system + packaged runtime + gamepad input + procedural level generator (v2.0 public release). Each version is a separate Claude Code session blocked on the previous; CLAUDE.md tech debt table updated at session end; [[work/archive/version-map]] is the authoritative public↔internal version mapping.

## 2026-05-20 — Fixed all 14 audit issues in one session
- Closed 4 HIGH bugs: weapon entity accumulation across Play→Menu→Play cycles, CollisionManager now fully wired (spatial grid functional), O(N) ignore-list in `_swept_blocked` replaced with O(1) `query_layer()` call, enemy name-check rule violation fixed
- Closed 5 MEDIUM issues: on_resize callback, player unregister leak, HealthBar dead destroy() override, main_menu AliveEntity bypass, level.json dedup (86→65 entries, 21 duplicates removed)
- Closed 5 LOW issues: double unregister in bullet die(), dead enabled=False branch, editor enemy scale mismatch, PICKUP forward-decl comment, swept_cast note
- See [[work/audits/2026-05-20-full-audit]] for Resolution section

## 2026-05-20 — Full audit complete
- Audited 7 source files + level.json across 8 tracks
- Found 0 critical, 4 high, 5 medium, 5 low issues
- Top finding: CollisionManager spatial grid was architecturally disconnected (never populated); all spatial queries silently returned empty
- Vault updated: Gotchas (3 new), Patterns (3 new), North Star, Index, CLAUDE.md tech debt table

## How This Works

- Each quarter note has: Competency Evidence, Impact & Deliverables, Technical Growth, Collaboration, Feedback
- Competency evidence links both the competency and the work note
- Review prep: open the quarter(s) covered by the review period, follow the links
- Backlinks from quarterly notes accumulate on competency notes and work notes automatically
