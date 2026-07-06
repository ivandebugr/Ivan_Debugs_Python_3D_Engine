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
**Source:** Repo state (`Scripts/auto_fix_loop.py`, `tests/smoke_test_harness.py` present but unused this session); [[work/archive/2026/v1.3-asset-import-pipeline]]

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
**Source:** [[work/archive/2026/v1.4-enemy-behaviour-trees]]; `Scripts/behaviour_tree.py`

## One tree per enemy, never shared or cached — licenses per-instance node state — 2026-06-30
**Decision:** Every enemy owns exactly one behaviour-tree instance, built fresh by `BehaviourTreeFactory.build()` per `Enemy.__init__`. Two enemies on the *same preset name* still get two distinct trees — same shape, separate node objects. No shared/cached tree registry is introduced anywhere. The deliberate consequence: nodes MAY hold per-instance mutable state directly on `self` (an `AttackNode`'s cooldown timestamp, a `PatrolNode`'s `current_index`), because that state is private to one enemy and there is no cross-enemy aliasing to guard against. A future step must NOT add a tree cache keyed by preset — it would silently make node state shared across enemies and corrupt every per-instance timer/cursor.
**Rationale:** Trees are cheap to build (a handful of node objects) and the alternative — caching one tree per preset to "save allocations" — would force every node to be stateless or to externalise its state into a per-enemy context object, a much larger complexity cost for a saving that does not matter at this enemy count. Documented in `Scripts/behaviour_tree.py`'s module docstring so later steps don't re-derive it.
**Source:** [[work/archive/2026/v1.4-enemy-behaviour-trees]]; `Scripts/behaviour_tree.py`

## RUNNING re-evaluation restarts from child 0 — no running-child cursor — 2026-06-30
**Decision:** When a child returns RUNNING inside a `Sequence` or `Selector`, the parent returns RUNNING immediately and ticks no later siblings that frame. On the *next* tick the parent re-evaluates **from the first child again** — compositors cache no "which child was running" cursor and carry no cross-frame state. `Parallel` ticks every child every frame regardless, so the distinction does not apply to it.
**Rationale:** The leaf nodes are written to re-derive their answer fresh every tick (`ChaseNode`/`AttackNode`/`FleeNode` re-check distance and cooldown from scratch; none resume partial execution), so stateless re-evaluation is the model the whole tree is built around. A resume-from-running cursor would contradict that and add per-compositor state for no behavioural gain. A later step that introduces a stateful "running child index" would silently change semantics for every existing tree.
**Source:** [[work/archive/2026/v1.4-enemy-behaviour-trees]]; `Scripts/behaviour_tree.py`

## Cooldown decorator: timer starts when the child resolves, not on first tick — 2026-06-30
**Decision:** The `Cooldown` decorator's window opens only when its child *resolves* (returns SUCCESS or FAILURE), never when `Cooldown` itself is ticked. While the child is RUNNING across frames, every `Cooldown.tick()` passes straight through to `child.tick()` with the timer untouched. Once the child resolves, subsequent ticks within the window return the child's *last resolved status* directly **without re-ticking the child** — applied identically to a child that last resolved FAILURE as to one that resolved SUCCESS. First tick ever is always allowed; `seconds=0` disables rate-limiting. Uses wall-clock `time.time()` comparison, matching `AttackNode`/`Weapon.last_shot`.
**Rationale:** A child mid-execution (RUNNING) is not starting a new cycle, so rate-limiting it would freeze a partially-completed action; the window only makes sense between completed cycles. Replaying the last status rather than re-ticking keeps the child from doing side-effectful work (e.g. firing) more than once per window. NOTE: as of v1.4 no shipping preset uses this decorator — see the "decorators unit-tested but never exercised in gameplay" decision below.
**Source:** [[work/archive/2026/v1.4-enemy-behaviour-trees]]; `Scripts/behaviour_tree.py`

## Repeat decorator: SUCCESS counts, FAILURE propagates-and-resets, n=-1 never succeeds — 2026-06-30
**Decision:** `Repeat(child, n)` counts a child SUCCESS as one completion; a child RUNNING does not increment the counter and yields RUNNING that tick. A child FAILURE propagates FAILURE immediately and resets the counter to 0. On the `n`th completion it returns SUCCESS and resets to 0 (so the instance is reusable from a clean slate). `n=-1` (infinite) never reaches a terminal count, so it never returns SUCCESS — RUNNING on every child SUCCESS, FAILURE on any child FAILURE, forever (the primary use case: infinite patrol loops). `n=0` returns SUCCESS immediately without ticking the child at all.
**Rationale:** Counting only resolved SUCCESSes (not ticks) makes `Repeat` compose cleanly with children that take multiple frames. Resetting on FAILURE means a partial run does not poison the next attempt. The `n=-1`/`n=0` edges are defined explicitly so they are never accidentally treated as "loop a huge number of times" or "loop zero but tick once." NOTE: no shipping v1.4 preset uses this decorator — see the decorators decision below.
**Source:** [[work/archive/2026/v1.4-enemy-behaviour-trees]]; `Scripts/behaviour_tree.py`

## FleeNode `flee_range` is a target separation distance, not a travel budget — 2026-06-30
**Decision:** `FleeNode(flee_threshold_hp, flee_range)` treats `flee_range` as the target *separation distance from the player*, not a total distance to travel from the flee-start position. `FleeNode` returns SUCCESS the instant `(enemy.position - player.position).length() > flee_range` — whether that distance opened over ten ticks of fleeing or was already true on the first tick (e.g. low HP while the player happens to be far). There is no accumulated "distance fled" counter. Step 7's `flee_when_low` preset wires `flee_range` as a separation radius accordingly.
**Rationale:** A separation radius is the property that actually matters for "am I safe?" — the enemy cares how close the threat is, not how far it has run. A travel budget would let an enemy that started far away pointlessly run further, and would need per-instance start-position state that the stateless-re-evaluation convention forbids. The alternative (travel budget) was considered and rejected as both wrong-feeling and stateful.
**Source:** [[work/archive/2026/v1.4-enemy-behaviour-trees]]; `Scripts/behaviour_nodes.py`

## Decorators (Invert/Repeat/Cooldown) ship unit-tested but unexercised in live gameplay — 2026-06-30
**Decision:** v1.4 ships the three decorators in `Scripts/behaviour_tree.py` with full unit coverage (`tests/test_behaviour_tree_decorators.py`, 17 cases; plus cases in `tests/test_behaviour_nodes.py`), but **none of the four named presets** (`default`, `patrol_then_attack`, `flee_when_low`, `aggressive`) uses any decorator. A code audit confirmed `Invert`/`Repeat`/`Cooldown` appear in production only inside docstrings/comments — never constructed in `behaviour_tree_factory.py`, `main.py`, or `enemy.py`. So in v1.4 the decorators are never ticked by an actual running enemy.
**Rationale:** The decorators were built in Step 6 as part of the complete compositor/decorator vocabulary the design called for, ahead of any preset that needs them — a deliberate "build the primitive, wire it when a preset wants it" sequencing. The gap is recorded so a future version adding a decorator-using preset knows the decorators are unit-correct but have **never run in the live frame loop** (no integration coverage against a real `Enemy`/`time.dt`/`game.state`) — that first preset should add an in-gameplay smoke check, not assume the decorator is battle-tested.
**Source:** [[work/archive/2026/v1.4-enemy-behaviour-trees]]; `Scripts/behaviour_tree.py`; `tests/test_behaviour_tree_decorators.py`

## v1.6 level editor refactor sequenced after feature work, gated on manual design review — 2026-06-26
**Decision:** The user explicitly chose to place v1.6 (splitting `level_editor.py` into smaller modules) after v1.3, v1.4, and v1.5 ship — not interleaved with that feature work — and to gate it on a manual system-design review of the other scripts that the user does personally, rather than starting from an agent-proposed module breakdown.
**Rationale:** Stated directly by the user when capturing the milestone: refactoring a file that's still absorbing new features creates a moving target, and the module boundaries should come from the user's own review of the codebase, not from an agent guessing at architecture.
**Source:** [[work/active/v1.6-level-editor-refactor]]

## v1.6 scope widened to whole-project audit, at Ivan's explicit request — 2026-07-06
**Decision:** The original v1.6 definition (split `level_editor.py` only, gated on Ivan's personal manual design review, not an agent-proposed breakdown) is widened for this session to a whole-project architecture audit plus a level-editor module-boundary *proposal* plus a forward-looking graphics/content/fix review. The "Ivan picks the module boundary, not the agent" gate from the original v1.6 decision still holds — three candidate breakdowns (A/B/C) were produced as options, no candidate was selected by the audit itself.
**Rationale:** Stated directly by Ivan when requesting the audit — the project has grown past what a single-file refactor review captures, and a periodic whole-project pass plus forward-look (graphics feasibility, content gaps, consolidated fix backlog) was wanted alongside the editor-specific work.
**Source:** [[work/active/v1.6-level-editor-refactor]]; v1.6 audit report, 2026-07-06.

## Pre-v1.6 closure pass sequenced before the editor split — 2026-07-06
**Decision:** Before any level_editor.py refactor work begins, a curated level (`levels/v1.json`) is built along with a checkpoint-consumer feature and removal of the weapon pre-grant. This closes both open v1.5 tails (§5 combined manual regression not run; `open_door`/pickups unexercised by content), gives the `patrol_then_attack`/`flee_when_low`/`aggressive` presets their first live run, and exercises `Cooldown` in the live frame loop for the first time via a new `cautious` preset.
**Rationale:** The audit's reasoning: this is the highest-leverage single move available — one piece of content closes multiple open items at once, and it produces the realistic level that v1.6's own regression pass will need to test against regardless of which module-boundary candidate is chosen.
**Source:** v1.6 audit report, Track C2, 2026-07-06; [[work/active/v1.6-fix-backlog]].
