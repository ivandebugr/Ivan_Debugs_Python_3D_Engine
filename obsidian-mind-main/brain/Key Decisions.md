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

## `Parallel` non-all-succeed aggregate: RUNNING while any child pending, FAILURE once all resolved — 2026-06-26
**Decision:** `Parallel.tick()` returns SUCCESS only when every child returns SUCCESS; while any child is still RUNNING it returns RUNNING; once all children have resolved and not all succeeded it returns FAILURE. This is a deliberate "wait for all children to resolve" policy, consistent with `Parallel`'s no-short-circuit contract (it ticks every child every frame regardless of individual results). The gap was filled during v1.4 Step 1 because the spec only defined the all-succeed case. Steps 2–7 should use this convention if/when a preset actually employs `Parallel` (none of the spec's four presets do yet).
**Rationale:** A parallel node that is neither done nor fully failed is still working, so RUNNING is the only coherent in-progress signal; collapsing to SUCCESS/FAILURE early would contradict the no-short-circuit semantics. Documented in `Scripts/behaviour_tree.py`'s `Parallel` docstring so later steps don't re-derive it.
**Source:** [[work/active/v1.4-enemy-behaviour-trees]]; `Scripts/behaviour_tree.py`

## v1.6 level editor refactor sequenced after feature work, gated on manual design review — 2026-06-26
**Decision:** The user explicitly chose to place v1.6 (splitting `level_editor.py` into smaller modules) after v1.3, v1.4, and v1.5 ship — not interleaved with that feature work — and to gate it on a manual system-design review of the other scripts that the user does personally, rather than starting from an agent-proposed module breakdown.
**Rationale:** Stated directly by the user when capturing the milestone: refactoring a file that's still absorbing new features creates a moving target, and the module boundaries should come from the user's own review of the codebase, not from an agent guessing at architecture.
**Source:** [[work/active/v1.6-level-editor-refactor]]
