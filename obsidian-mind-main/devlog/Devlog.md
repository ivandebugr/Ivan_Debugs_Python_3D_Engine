---
date: 2026-07-18
description: "Public devlog for Ivan's 3D Engine — what shipped each release, what it looked like, what's next. Source material for YouTube devlog content."
tags:
  - devlog
  - index
---

# Devlog

Running log of what shipped in **Ivan's 3D Engine** — a first-person shooter
built on Ursina (Python / Panda3D). Newest first. Written to be readable by
someone who isn't me: what went in, what it looks like, what's coming.

Longer entries live in `devlog/entries/` (one note per period); this file is
the feed. Drop screenshots and clips next to an entry and link them inline —
`![[clip.mp4]]` or a plain path.

---

## What's next

The v1.7 look-and-feel pass — picking one path each for lighting/shadows,
bloom, and a particle system, all under the macOS GLSL 1.20 constraint. All
three are scoped with candidates and perf numbers, awaiting a pick:
[[work/active/v1.7-lighting-scoping]], [[work/active/v1.7-bloom-scoping]],
[[work/active/v1.7-particles-scoping]]. After that, v2.0: modding, a packaged
runtime, gamepad input, procedural levels → first public release
([[work/active/v2.0-release]]).

Full roadmap: [[brain/North Star]].

---

## 2026-07-07 — v1.6: the level editor, rebuilt from the inside

The level editor was one 4,169-line file. Split it into seven focused modules
(a core plus hierarchy / gizmo / browser / inspector / play-mode pieces and
two shared helpers) with **zero change to how it behaves** — every one of the
9 steps checked against the real engine, 113/113 tests green plus 4 smoke
scenarios re-run each step. Fixed three lurking bugs along the way, including a
crash class in the gizmo picker.

Why it matters: the editor is now something I can actually add features to
without the whole thing fighting back.

→ [[work/archive/2026/v1.6-level-editor-refactor]] · CHANGELOG [1.6.0]

## 2026-07-06 — First real level + checkpoints (v1.5.1)

Built the first hand-designed level: doors, pickups, a checkpoint-and-kill-plane
gauntlet, all five enemy behaviour presets live in one map. Added
checkpoint-respawn so death sends you back to the last flag instead of the
start. Also fixed the smoke-test harness, which had quietly been broken since
v1.5.

→ [[brain/Key Decisions]] · CHANGELOG [1.5.1]

## 2026-05-20 — v1.2: the editor gets Unity-feel controls

Big editor pass. Eight controls that make level-building feel like a real tool:
grid snapping, undo/redo, multi-select and box-select, an inspector panel, a
scene hierarchy, transform gizmos, camera bookmarks, and a play-in-editor
toggle (F5 to test, F5 to stop). Under the hood, a proper game state machine
replaced a pile of global variables, and the level file format grew room for
per-object colour, rotation, and enemy stats — old levels still load.

→ [[work/active/v1.2-level-editor-overhaul]]

## 2026-05-20 — Locked the roadmap to v2.0

Mapped the whole path from a clean codebase to public release: editor overhaul
(v1.2), asset import with hot-reload (v1.3), enemy behaviour trees (v1.4),
triggers + weapon inventory (v1.5), then modding + packaged build + gamepad +
procedural levels for the v2.0 public launch.

→ [[work/archive/version-map]]

## 2026-05-20 — Cleared the pre-v1.2 audit

Audited the whole codebase, found 14 issues (4 high, 5 medium, 5 low), fixed
all of them in one pass. The headline: the collision system's spatial grid was
wired up but never actually populated, so spatial lookups had been silently
returning nothing. Fixing it made collision detection real.

→ [[work/audits/2026-05-20-full-audit]]
