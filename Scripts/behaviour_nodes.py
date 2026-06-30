"""Built-in leaf nodes for enemy behaviour trees (v1.4 — Layer 2).

This is **Layer 2** of the three-layer design (see
``docs/v1.4-enemy-behaviour-trees.md``). Layer 1 (``behaviour_tree.py``) ships
the ``Status`` enum, the ``BehaviourNode`` base, and the compositors. This
module ships the *leaf* nodes — the ones that actually read the world and act
on it through the ``enemy`` argument.

Why a separate file (and not more nodes inside ``behaviour_tree.py``):
``behaviour_tree.py``'s docstring declares it the pure compositor skeleton and
says leaf nodes "deliberately live elsewhere." Keeping leaves here preserves
that purity boundary and gives the remaining steps (ChaseNode/PatrolNode/
FleeNode in Steps 3–5, decorators in Step 6) an obvious home. Steps 3+ append
to this file.

Step 2 scope — ``IdleNode`` and ``AttackNode``. Step 3 adds ``ChaseNode``.
``PatrolNode``, ``FleeNode`` and the decorators are later steps and are not
implemented here, not even partially.

Purity contract (same as Layer 1)
----------------------------------
No import from ``main.py``, ``enemy.py``, or any Ursina / Panda3D module at
runtime. All game-state access flows through the ``enemy`` argument passed into
``tick()``. The enemy is duck-typed: ``AttackNode`` needs ``enemy.position``,
``enemy.player.position`` and ``enemy.shoot()``; ``ChaseNode`` needs
``enemy.position``, ``enemy.player.position`` and ``enemy.chase_step(direction, dt)``.
The single stdlib dependency is ``time`` (wall-clock), used for the cooldown
timer — see below.

Why ``ChaseNode`` delegates the move to ``enemy.chase_step`` (purity)
---------------------------------------------------------------------
The actual movement (a wall-avoiding swept raycast + ``enemy.position +=``) is
Ursina-land — it cannot live in this pure layer. So ``ChaseNode`` does only the
framework-free part itself (distance check + normalized direction, the same
vector ops ``AttackNode`` already uses) and hands the move to the enemy via
``enemy.chase_step(direction, dt)``, exactly as ``AttackNode`` hands firing to
``enemy.shoot()``. ``Enemy.chase_step`` (in ``enemy.py``) routes through the
shared ``swept_move_blocked`` helper so the enemy avoids walls the same way the
player does — no duplicated raycast loop (see ``brain/Patterns`` no-duplication
rule and ``docs/v1.4-enemy-behaviour-trees.md``).

Per-instance cooldown state (relies on Layer 1's ownership contract)
--------------------------------------------------------------------
One tree per enemy, never shared (``behaviour_tree.py`` ownership contract), so
``AttackNode`` may keep its cooldown timestamp directly on ``self`` with no
cross-enemy aliasing. Two enemies built from the same preset each get their own
``AttackNode`` instance with its own timer.

Cooldown convention — wall-clock ``time.time()`` timestamp comparison, matching
``Weapon.last_shot`` in ``weapon.py`` (the closest existing *shooter cooldown*),
NOT dt-accumulation and NOT Ursina's ``invoke``-based ``Enemy.can_shoot`` flag.
``dt`` is accepted to satisfy the ``tick(enemy, dt)`` signature but is not used
by ``AttackNode`` — wall-clock makes the cooldown frame-rate independent and
keeps the node testable without a running clock.
"""

from __future__ import annotations

import time as _time

from Scripts.behaviour_tree import BehaviourNode, Status


class IdleNode(BehaviourNode):
    """Do nothing; always succeed.

    Used as the final fallback in a ``Selector`` so the tree always resolves to
    a definite status instead of leaving the enemy in ``RUNNING`` indefinitely.
    Zero CPU cost — there is no computation beyond the return.
    """

    def tick(self, enemy: object, dt: float) -> Status:
        return Status.SUCCESS


class AttackNode(BehaviourNode):
    """Shoot the player when in range and off cooldown.

    Per the v1.4 spec:

    - distance to player via ``(enemy.position - player.position).length()``
    - within ``attack_range`` AND cooldown elapsed  -> ``enemy.shoot()``, reset
      the cooldown timer, return ``SUCCESS``
    - within ``attack_range`` but on cooldown        -> ``RUNNING``
    - out of ``attack_range``                        -> ``FAILURE``

    The cooldown timestamp is per-instance state on ``self`` (safe under the
    one-tree-per-enemy ownership contract). ``cooldown`` is given in seconds and
    compared against ``time.time()`` — the same wall-clock pattern as
    ``Weapon.last_shot``.

    Behaviour-parity note (Step 2): ``Enemy.shoot()`` keeps its own
    ``can_shoot`` self-gate at the same ``ENEMY_SHOOT_COOLDOWN`` interval. With
    matching durations the node's timer and the enemy's flag stay in lockstep,
    so the firing cadence is identical to the pre-tree code. Step 6's
    ``Cooldown`` decorator generalises this node-owned timer.
    """

    def __init__(self, attack_range: float, cooldown: float):
        self.attack_range = attack_range
        self.cooldown = cooldown
        # Initialise so the FIRST in-range tick is always off-cooldown and
        # fires immediately — same as today's enemy (can_shoot starts True).
        self._last_attack_time = float("-inf")

    def tick(self, enemy: object, dt: float) -> Status:
        distance = (enemy.position - enemy.player.position).length()
        if distance > self.attack_range:
            return Status.FAILURE

        now = _time.time()
        if now - self._last_attack_time < self.cooldown:
            # In range, but the cooldown has not elapsed yet — mid-action.
            return Status.RUNNING

        enemy.shoot()
        self._last_attack_time = now
        return Status.SUCCESS


class ChaseNode(BehaviourNode):
    """Close distance to the player, then yield to the attacker once in range.

    Per the v1.4 spec (Step 3), with one refinement (see "Why a stop_range"):

    - distance to player via ``(enemy.position - player.position).length()`` —
      the SAME true-length convention ``AttackNode`` uses, and both thresholds
      below use the same ``distance > range`` comparison, so a single tree never
      mixes squared- and true-distance conventions.
    - out of ``detection_range``       -> ``FAILURE``. This aborts the parent
      ``Sequence`` (default tree is ``Selector([Sequence([Chase, Attack]),
      Idle])``), so the Selector falls through to ``IdleNode`` and the enemy
      stops.
    - inside detection but beyond ``stop_range`` -> step toward the player,
      return ``RUNNING`` (still closing the gap, wants to be ticked again). The
      ``Sequence`` short-circuits on RUNNING, so ``AttackNode`` is intentionally
      NOT ticked yet — the enemy is too far to shoot.
    - within ``stop_range``            -> ``SUCCESS`` with NO movement. The
      ``Sequence`` then advances to ``AttackNode``, which fires on cooldown.

    Why a ``stop_range`` (the one refinement over the spec's literal node):
    the spec's bare ChaseNode returns RUNNING whenever in detection range, which
    — under Step 1's Sequence contract that short-circuits on RUNNING — would
    mean ``AttackNode`` never ticks and the enemy chases but never shoots. To
    deliver the spec's stated intent ("chase if in range, ATTACK if close
    enough") the node must hand off to the attacker once close. ``stop_range`` is
    that handoff radius; callers pass the enemy's ``attack_range`` so the enemy
    stops closing exactly where it can start shooting.

    Does **not** shoot — firing is ``AttackNode``'s job exclusively. There is no
    ``enemy.shoot()`` call anywhere in this node.

    Movement is delegated to ``enemy.chase_step(direction, dt)`` (see this
    module's docstring): this node computes only the normalized enemy→player
    direction (pure vector math, framework-free); the enemy performs the
    wall-avoiding swept move. Both ranges are per-instance config; the node
    holds no cross-frame state, matching the stateless RUNNING re-evaluation
    convention in ``behaviour_tree.py``.
    """

    def __init__(self, detection_range: float, stop_range: float):
        self.detection_range = detection_range
        self.stop_range = stop_range

    def tick(self, enemy: object, dt: float) -> Status:
        distance = (enemy.position - enemy.player.position).length()
        if distance > self.detection_range:
            return Status.FAILURE
        if distance <= self.stop_range:
            # Close enough to attack — stop closing and let the Sequence fall
            # through to AttackNode. No movement this tick.
            return Status.SUCCESS

        # In detection range but still too far to shoot: keep closing.
        # to_player points FROM enemy TO player, so normalizing it is the chase
        # direction directly. (Recomputed here rather than reusing the distance
        # vector above to keep the in-range path's intent obvious.)
        direction = (enemy.player.position - enemy.position).normalized()
        enemy.chase_step(direction, dt)
        return Status.RUNNING
