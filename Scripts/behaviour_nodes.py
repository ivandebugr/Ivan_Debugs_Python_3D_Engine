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

Step 2 scope — ONLY ``IdleNode`` and ``AttackNode``. ``ChaseNode``,
``PatrolNode``, ``FleeNode`` and the decorators are later steps and are not
implemented here, not even partially.

Purity contract (same as Layer 1)
----------------------------------
No import from ``main.py``, ``enemy.py``, or any Ursina / Panda3D module at
runtime. All game-state access flows through the ``enemy`` argument passed into
``tick()``. The enemy is duck-typed: ``AttackNode`` only needs ``enemy.position``,
``enemy.player.position`` and ``enemy.shoot()``. The single stdlib dependency is
``time`` (wall-clock), used for the cooldown timer — see below.

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
