---
description: "Architectural and workflow decisions worth recalling across sessions — each links to its source work note"
tags:
  - brain
---

# Key Decisions

Architectural or workflow decisions worth recalling. Link to the full [[Decision Record]] when one exists.

## Semi-auto fix loop over the built `auto_fix_loop.py` / `smoke_test_harness.py` system — 2026-06-24 (date approximate; based on commit timestamps around the v1.2.6 session)
**Decision:** The v1.2.6 fix session ran smoke test → paste output → human writes fix prompt → re-verify, rather than driving `Scripts/auto_fix_loop.py` + `tests/smoke_test_harness.py` autonomously, even though both scripts exist in the repo (`be7fa87` "baseline before auto-fix loop").
**Rationale:** Not stated in CLAUDE.md or commit messages — unconfirmed why the autonomous path was deferred for this session.
**Source:** Repo state (`Scripts/auto_fix_loop.py`, `tests/smoke_test_harness.py` present but unused this session); [[work/active/v1.3-asset-import-pipeline]]

## Model routing: Sonnet 4.6 default, Opus 4.8 reserved for hard/ambiguous diagnosis — 2026-06-24 (date approximate)
**Decision:** Routine fixes default to Sonnet 4.6; Opus 4.8 is reserved for hard or ambiguous diagnosis work or full audits.
**Rationale:** Not stated explicitly in CLAUDE.md — unconfirmed why this split was chosen.
**Source:** Unconfirmed beyond observed session pattern.

## Two-commit discipline: ship logically-complete-but-unverified work and the crash fix as separate commits — 2026-06-24
**Decision:** `6d46cd6` ("v1.2.6 wip: resize/camera fix, texture thumbnail fix, tray removed into Models tab — known issue: startup crash...") and `44f931c` ("v1.2.6: fix startup crash — never construct/set Text.text to an empty string") landed as two separate commits rather than one bundled commit.
**Rationale:** Not stated explicitly in CLAUDE.md or the commit messages beyond the wip commit flagging the known crash as a separate, queued fix — unconfirmed beyond that.
**Source:** Git log (`6d46cd6`, `44f931c`); CHANGELOG [1.2.6]
