---
description: "Central map of all work notes — active projects, completed work by quarter, decisions log"
tags:
  - index
  - moc
---

# Work Notes

Central map of content. All work notes and decisions link back here. For quick navigation, use [[Home]] or open `bases/Work Dashboard.base`.

**Folder structure**: `active/` = current projects, `archive/` = completed (by year), `incidents/` = incident docs, `1-1/` = meetings.

## Incidents

Incident docs live in `work/incidents/`. See `Incidents.base` for overview.

-

## Active Projects

- [[work/active/v1.6-fix-backlog]] — 16-item priority-ordered fix list from the 2026-07-06 whole-project audit | Active | Items 1+5 (closure pass) and 4+10+15 (v1.6 split) done
- [[work/active/v2.0-release]] — Modding, packaged runtime, gamepad input, procedural level gen — PUBLIC RELEASE | Planned | Blocked on: v1.5

## Review Prep

-

## Recently Completed

- [[work/archive/2026/v1.6-level-editor-refactor]] — Shipped 2026-07-07. `level_editor.py` (4,169 lines) split into `editor_core` + five collaborator modules + `compat`/`asset_resolve`; zero behaviour change; 9 steps, 113/113 tests + 163 in-app checks.
- [[work/archive/2026/v1.5-gameplay-systems]] — Shipped 2026-07-01. Trigger/zone system + weapon inventory API; [1.5.1] closure pass 2026-07-06 (curated level, checkpoint consumer, §5 regression).
- [[work/archive/2026/v1.4-enemy-behaviour-trees]] — Shipped 2026-06-30. Per-enemy behaviour trees: compositors+decorators, 5 leaf nodes, 4 named presets, `level.json` `behaviour` field, editor preset+waypoint UI. Wrap-up audit: 9/9 steps, 110/110 tests.
- [[work/archive/2026/v1.3-asset-import-pipeline]] — Shipped 2026-06-26. Asset browser, drag-drop import, texture/model pickers, hot-reload. Cross-step audit: 7/7 steps.
- [[work/active/v1.2-level-editor-overhaul]] — Shipped 2026-05-20. Game state machine, canonical teardown, JSON schema, 8 Unity-feel editor features.

## Completed

### Current Quarter
-

### Previous Quarters
-

## Audits

- [[work/audits/2026-v1.2.3-full-audit]] — v1.2.3 full-project audit (2026-05-20), 12 findings (0 critical, 2 high, 6 medium, 4 low) — hierarchy scroll zooms EditorCamera, drag-and-drop ghost math wrong, enemy origin_y mismatch, bookmark/InputField collision
- [[work/audits/2026-v1.2-audit]] — v1.2 audit (2026-05-20), 19 findings (0 critical, 4 high, 9 medium, 6 low) — PlaceEntityCommand redo no-op, game.state stuck after editor play exit, enemy Y offset mismatch, 201 eternal debug entities per player
- [[work/audits/2026-05-20-full-audit]] — Full project audit (2026-05-20), 14 issues found (0 critical, 4 high, 5 medium, 5 low) — all 14 FIXED

## Reference

-

## Decisions Log

| Date | Decision | Status | Link |
|------|----------|--------|------|
|      |          |        |      |

## Open Questions

-

## Archive

- [[work/archive/version-map]] — Canonical version history, internal tag↔public semver mapping, versioning policy
