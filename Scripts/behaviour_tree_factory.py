"""Named behaviour-tree presets (v1.4 — Step 7).

This is **Layer 3** of the three-layer design (see
``docs/v1.4-enemy-behaviour-trees.md``). Layer 1 (``behaviour_tree.py``) ships
the compositors/decorators; Layer 2 (``behaviour_nodes.py``) ships the leaf
nodes. This module ships the ``BehaviourTreeFactory`` — it composes those parts
into the five named enemy presets and is the single place ``Enemy.__init__``
calls to build a tree.

Why a separate file (not folded into ``behaviour_tree.py``)
----------------------------------------------------------
``behaviour_tree.py`` and ``behaviour_nodes.py`` are the **pure** layers — zero
Ursina / Panda3D imports, so they unit-test without launching the app. The
Factory necessarily reaches into game-land: ``"patrol_then_attack"`` waypoints
arrive from ``level.json`` as raw ``[x, y, z]`` lists and must become real
``Vec3`` instances for ``PatrolNode``'s duck-typed vector math (subtraction /
``.length()`` / ``.normalized()``). Keeping that ``Vec3`` import here — and out
of the two pure layers — preserves their import-purity contract (each pure
layer's docstring spells this out). The Factory is the correct boundary: it is
already the layer that knows about presets, config dicts, and the enemy's tuned
constants.

Vec3 import is **lazy** (inside :func:`_to_waypoints`), for two reasons:
1. Presets that use no waypoints (``"default"``, ``"flee_when_low"``,
   ``"aggressive"``) never touch Ursina, so ``build()`` for them — and
   importing this module at all — costs nothing and works in a windowless
   unit-test process. Only ``"patrol_then_attack"`` *with* waypoints pays the
   ``from ursina import Vec3`` cost.
2. It mirrors the lazy-import pattern ``enemy.py`` already uses to break the
   ``weapon ↔ enemy`` cycle (CLAUDE.md "Imports and Circular Import Rules").
   Tests inject their own waypoint vectors via ``waypoint_factory=`` so they
   never trigger the Ursina import either.

Default-preset values are pinned to ``enemy.py``'s tuned constants
--------------------------------------------------------------------
The ``"default"`` preset (and the chase/attack branch of ``"flee_when_low"``)
is built from ``ENEMY_DETECTION_RANGE`` / ``ENEMY_ATTACK_RANGE`` /
``ENEMY_SHOOT_COOLDOWN`` imported from ``enemy.py`` — NOT hard-coded literals —
so it always equals whatever the project has tuned those to. Today those are
100 / 30 / 1.0, which match the doc table's illustrative ``ChaseNode(100)`` /
``AttackNode(30, 1.0)``; pinning to the constants keeps engagement ranges in
lockstep with the enemy's tuning even if a future playtest retunes them. (As of
the v1.7 detection gate the default preset is no longer bit-for-bit the interim
TODO tree — it additionally requires line of sight — but its ranges/cadence are
still exactly the tuned constants.) The other three presets use the doc's
literal values, which are *intentionally* different per the spec (patrol enemies
less aggressive; aggressive enemies wider/faster) — stated inline at each preset.

ChaseNode's two-arg signature (detection_range, stop_range)
-----------------------------------------------------------
The doc table writes ``ChaseNode(100)`` (one arg), but the real Step-3
``ChaseNode.__init__(detection_range, stop_range)`` needs a second
``stop_range`` — the handoff radius where the enemy stops closing and lets the
``Sequence`` advance to ``AttackNode``. Every preset here passes
``stop_range = that preset's attack_range`` (exactly how the interim TODO tree
wired it: ``ChaseNode(ENEMY_DETECTION_RANGE, ENEMY_ATTACK_RANGE)``), so the
enemy stops closing precisely where it can start shooting.

Unknown preset names degrade gracefully — they log a warning and fall back to
``"default"`` rather than raising, per the spec ("Unknown preset name → log
warning, fall back to default").

Detection gate (v1.7 — range + line of sight)
----------------------------------------------
Every preset's chase/attack Sequence is fronted by ``DetectPlayerNode`` so the
enemy engages only when the player is within range AND has line of sight (no
wall between them) — closing the "enemies chase/shoot through walls" gap
(``brain/Gotchas.md``: ChaseNode detection was pure distance). The detector's
range is pinned to the SAME value that preset's ``ChaseNode`` uses (so detect
and chase agree on the range), and it returns only SUCCESS/FAILURE — never
RUNNING — so a lost target aborts the combat Sequence at child 0 and the
Selector falls through to the preset's own non-combat fallback (Patrol/Idle),
preserving the stateless restart-from-child-0 convention.

The ``"flee_when_low"`` flee branch is deliberately NOT gated: a low-HP enemy
flees in reaction to damage already taken, not to a fresh sighting, so it keeps
fleeing even after it rounds a corner and breaks LOS. Only that preset's
chase/attack branch carries the gate.
"""

from __future__ import annotations

from Scripts.behaviour_nodes import (
    AttackNode,
    ChaseNode,
    DetectPlayerNode,
    FleeNode,
    IdleNode,
    PatrolNode,
)
from Scripts.behaviour_tree import BehaviourNode, Cooldown, Selector, Sequence

# Tuned defaults — pinned to enemy.py's TUNE constants, not literals, so the
# "default" preset tracks whatever those are tuned to (see module docstring).
from Scripts.enemy import (
    ENEMY_ATTACK_RANGE,
    ENEMY_DETECTION_RANGE,
    ENEMY_SHOOT_COOLDOWN,
)


def _to_waypoints(raw, waypoint_factory=None):
    """Convert raw ``[[x,y,z], ...]`` config into PatrolNode-ready vectors.

    Each ``[x, y, z]`` becomes a ``Vec3(x, y, z)`` so ``PatrolNode`` can do the
    duck-typed vector math it documents (``-`` / ``.length()`` /
    ``.normalized()`` against ``enemy.position``). ``Vec3`` is imported lazily
    here so presets without waypoints never pull in Ursina (module docstring).

    ``waypoint_factory`` lets unit tests substitute a no-Ursina vector stub
    (the tests' ``Vec``) for ``Vec3`` — production passes nothing and gets the
    real ``Vec3``.

    Malformed entries are skipped, not fatal — a single bad waypoint must not
    sink the whole enemy (matches the "degrade gracefully" posture of the
    unknown-preset path).
    """
    if waypoint_factory is None:
        from ursina import Vec3  # lazy: only patrol-with-waypoints touches Ursina
        waypoint_factory = lambda x, y, z: Vec3(x, y, z)

    waypoints = []
    for point in raw:
        try:
            x, y, z = point
        except (TypeError, ValueError):
            print(f"BehaviourTreeFactory: skipped malformed waypoint {point!r}")
            continue
        waypoints.append(waypoint_factory(x, y, z))
    return waypoints


class BehaviourTreeFactory:
    """Builds a fresh behaviour tree for one enemy from a named preset.

    One tree per enemy, never shared (see ``behaviour_tree.py`` ownership
    contract): every ``build()`` call constructs brand-new node instances, so
    two enemies on the same preset each get their own per-instance node state
    (cooldown timers, patrol cursors).
    """

    # Recognised preset names — used only to decide the unknown-preset warning;
    # the actual construction lives in the if/elif chain in build().
    PRESETS = ("default", "patrol_then_attack", "flee_when_low", "aggressive",
               "cautious")

    @staticmethod
    def build(preset_name: str, config: dict, waypoint_factory=None) -> BehaviourNode:
        """Construct and return the tree for ``preset_name``.

        Args:
            preset_name: one of :attr:`PRESETS`. Anything else logs a warning
                and falls back to ``"default"`` (never raises — Step 7 spec).
            config: per-enemy options from ``level.json``. Read with ``.get()``
                throughout, so a missing key never raises ``KeyError`` — only
                ``"patrol_then_attack"`` reads anything from it today
                (``"waypoints"``, defaulting to ``[]``).
            waypoint_factory: test seam — see :func:`_to_waypoints`. Production
                callers omit it.

        Returns:
            The root :class:`BehaviourNode` of a freshly built tree.
        """
        if preset_name == "default":
            # Pinned to enemy.py's tuned constants (100 / 30 / 1.0 today).
            # ChaseNode's stop_range == attack_range: stop closing where you can
            # start shooting. DetectPlayerNode(detection_range) fronts the
            # Sequence (v1.7) so chase/attack only engage a player in range AND
            # visible — the same detection_range ChaseNode uses.
            return Selector([
                Sequence([
                    DetectPlayerNode(ENEMY_DETECTION_RANGE),
                    ChaseNode(ENEMY_DETECTION_RANGE, ENEMY_ATTACK_RANGE),
                    AttackNode(ENEMY_ATTACK_RANGE, ENEMY_SHOOT_COOLDOWN),
                ]),
                IdleNode(),
            ])

        if preset_name == "patrol_then_attack":
            # Intentionally LESS aggressive than default (doc literals 60/25/0.8,
            # not the tuned constants): shorter detection, tighter attack range,
            # faster cadence — a patrolling enemy that only engages up close.
            # When the player is outside detection the chase/attack Sequence
            # FAILs and the Selector falls through to PatrolNode (walk the route),
            # then IdleNode if there is no route.
            waypoints = _to_waypoints(config.get("waypoints", []), waypoint_factory)
            children = [
                Sequence([DetectPlayerNode(60), ChaseNode(60, 25), AttackNode(25, 0.8)]),
            ]
            # PatrolNode raises on an empty waypoint list (Step 4 contract), so a
            # config with no waypoints simply has no patrol branch — it degrades
            # to chase/attack-then-idle rather than crashing. Documented Step 4
            # empty-case behaviour: an empty route is "no patrol", not an error
            # for the *preset* (only the bare node treats [] as a misconfig).
            if waypoints:
                children.append(PatrolNode(waypoints))
            children.append(IdleNode())
            return Selector(children)

        if preset_name == "flee_when_low":
            # First branch: Sequence([FleeNode(30, 15), IdleNode()]). The
            # IdleNode here runs *after* a successful flee — it is NOT a
            # fallback. FleeNode returns SUCCESS once the enemy is far enough
            # (or starts far enough) away while HP < 30; the Sequence then
            # advances to IdleNode so the enemy sits still rather than
            # immediately re-engaging. FleeNode returns FAILURE when HP has
            # recovered to >= 30, which aborts this Sequence and lets the
            # Selector fall through to the normal chase/attack branch.
            # The chase/attack branch reuses the tuned default constants so a
            # recovered enemy fights exactly like a "default" one.
            return Selector([
                # Flee branch is intentionally UNGATED — a hurt enemy flees in
                # reaction to damage already taken, not to a fresh sighting, so
                # it keeps fleeing even without current line of sight (module
                # docstring "Detection gate"). Only the chase/attack branch below
                # carries DetectPlayerNode.
                Sequence([FleeNode(30, 15), IdleNode()]),
                Sequence([
                    DetectPlayerNode(ENEMY_DETECTION_RANGE),
                    ChaseNode(ENEMY_DETECTION_RANGE, ENEMY_ATTACK_RANGE),
                    AttackNode(ENEMY_ATTACK_RANGE, ENEMY_SHOOT_COOLDOWN),
                ]),
                IdleNode(),
            ])

        if preset_name == "aggressive":
            # Intentionally MORE aggressive than default (doc literals
            # 150/40/0.4): wider detection, longer reach, much faster fire rate.
            return Selector([
                Sequence([DetectPlayerNode(150), ChaseNode(150, 40), AttackNode(40, 0.4)]),
                IdleNode(),
            ])

        if preset_name == "cautious":
            # Same engagement envelope as "default" (tuned constants), but the
            # attack cycle is rate-limited by the Cooldown decorator: after the
            # AttackNode resolves, Cooldown replays that resolved status for
            # 3.0s WITHOUT re-ticking the node, so the enemy fires at most once
            # per 3s instead of once per ENEMY_SHOOT_COOLDOWN (1.0s today) —
            # it takes a shot, then holds fire and re-evaluates. First preset
            # to exercise a decorator in the live frame loop (pre-v1.6 closure
            # pass; see brain/Key Decisions "Decorators ship unit-tested but
            # unexercised"). Note stop_range == attack_range and both nodes
            # read the same position each tick, so the wrapped AttackNode can
            # only resolve SUCCESS here — the Cooldown FAILURE-replay case is
            # unreachable in this preset shape.
            return Selector([
                Sequence([
                    DetectPlayerNode(ENEMY_DETECTION_RANGE),
                    ChaseNode(ENEMY_DETECTION_RANGE, ENEMY_ATTACK_RANGE),
                    Cooldown(
                        AttackNode(ENEMY_ATTACK_RANGE, ENEMY_SHOOT_COOLDOWN),
                        seconds=3.0,  # cautious fire interval — 3× the default cadence
                    ),
                ]),
                IdleNode(),
            ])

        # Unknown preset — degrade gracefully (spec: warn, fall back to default,
        # do NOT raise). Plain print matches the warning style of the other pure
        # Scripts/ layer, asset_registry.py ("ModuleName: message"); SessionLogger
        # is editor-only and would break this module's no-framework import profile.
        print(
            f"BehaviourTreeFactory: unknown preset {preset_name!r}; "
            f"falling back to 'default' (known presets: {BehaviourTreeFactory.PRESETS})"
        )
        return BehaviourTreeFactory.build("default", config, waypoint_factory)
