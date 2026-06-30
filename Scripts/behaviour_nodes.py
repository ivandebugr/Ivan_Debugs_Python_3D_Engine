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

Step 2 scope — ``IdleNode`` and ``AttackNode``. Step 3 added ``ChaseNode``.
Step 4 added ``PatrolNode``. Step 5 adds ``FleeNode``. Decorators are a later
step and are not implemented here, not even partially.

Purity contract (same as Layer 1)
----------------------------------
No import from ``main.py``, ``enemy.py``, or any Ursina / Panda3D module at
runtime. All game-state access flows through the ``enemy`` argument passed into
``tick()``. The enemy is duck-typed: ``AttackNode`` needs ``enemy.position``,
``enemy.player.position`` and ``enemy.shoot()``; ``ChaseNode`` needs
``enemy.position``, ``enemy.player.position`` and ``enemy.chase_step(direction, dt)``;
``PatrolNode`` needs ``enemy.position`` and ``enemy.patrol_step(direction, dt)``.
The single stdlib dependency is ``time`` (wall-clock), used for the cooldown
timer — see below.

Vec3 type tension (PatrolNode waypoints) — chosen resolution
--------------------------------------------------------------
This file (``behaviour_nodes.py``) is held to the same no-Ursina-import purity
contract as ``behaviour_tree.py``, but ``PatrolNode`` needs vector subtraction
and ``.length()`` on its waypoints, the same operations ``ChaseNode`` already
performs on ``enemy.position`` / ``enemy.player.position``. Those objects are
real Ursina ``Vec3`` at runtime but the node never imports ``Vec3`` itself — it
relies on duck typing (the tests' ``Vec`` stub proves this works without
Ursina). ``PatrolNode`` extends the same duck-typed contract to its
``waypoints`` argument: each waypoint must support ``-``, ``.length()`` and
``.normalized()`` against an ``enemy.position``-shaped object — in practice a
``Vec3`` (or 3-tuple-like stub in tests). This is option (b) from the v1.4
Step 4 task: duck-typed ``list[Any]``, not a hard ``Vec3`` type hint, so this
file's import profile is unchanged from Steps 2–3 (still zero Ursina/Panda3D
imports — only ``Scripts.behaviour_tree`` and stdlib ``time``). Callers
(``enemy.py``, which already imports Ursina) are free to pass ``Vec3``
instances; this file never constructs or imports ``Vec3`` itself.

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

``FleeNode`` reuses ``enemy.chase_step`` for the same reason — see its class
docstring "Movement: reuses chase_step, inverted direction" for why a Step
3/4-shaped ``flee_step`` method was deliberately NOT added.

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


# Waypoint-reached threshold, in world units. Derived (not guessed): the
# enemy's per-frame patrol step is bounded by PATROL_SPEED * dt_max. Taking
# dt_max = 0.05s (a 20 FPS floor — below this the game is already considered
# unplayable, see CLAUDE.md Performance Investigation Order) and PATROL_SPEED
# = 5 (same constant as ENEMY_CHASE_SPEED, see "Why PATROL_SPEED" below):
#   step_max = 5 * 0.05 = 0.25
# The threshold must be >= step_max or a single slow frame could step the
# enemy clean past a waypoint without ever landing inside the radius,
# oscillating forever. 0.3 clears that bound with margin while staying tight
# enough that the enemy visibly arrives at each waypoint rather than
# corner-cutting early.
PATROL_WAYPOINT_THRESHOLD = 0.3

# Why PATROL_SPEED reuses ENEMY_CHASE_SPEED: the v1.4 spec gives PatrolNode no
# speed parameter, and ChaseNode's movement (via enemy.chase_step) already
# uses ENEMY_CHASE_SPEED as the single chase/patrol speed constant in
# enemy.py. Inventing a second, different default would mean a patrolling
# enemy visibly changes pace the instant ChaseNode takes over — introducing a
# third speed value has no spec justification, so PatrolNode's default below
# matches it exactly. Callers may still override per-instance.
PATROL_SPEED_DEFAULT = 5


class PatrolNode(BehaviourNode):
    """Walk a looping waypoint route; never shoots, never reacts to the player.

    Per the v1.4 spec (Step 4):

    - moves the enemy toward ``waypoints[current_index]``
    - returns ``RUNNING`` while in transit
    - returns ``SUCCESS`` the tick the waypoint is reached, and advances
      ``current_index`` (wrapping to 0 after the last waypoint — infinite
      loop by design)

    Waypoint type — see this module's docstring "Vec3 type tension" section:
    each waypoint is duck-typed (anything supporting ``-``, ``.length()`` and
    ``.normalized()`` against ``enemy.position``), matching how ``ChaseNode``
    already treats ``enemy.position`` / ``enemy.player.position``. This file
    never imports ``Vec3``.

    Reached threshold — ``PATROL_WAYPOINT_THRESHOLD`` (module constant above),
    derived from the max per-frame patrol step so the enemy cannot step clean
    over a waypoint and miss the SUCCESS tick.

    Speed — ``PATROL_SPEED_DEFAULT`` (module constant above), the same value
    ``enemy.py`` uses for chase movement (``ENEMY_CHASE_SPEED``), so patrol
    and chase read as the same gait.

    Movement is delegated to ``enemy.patrol_step(direction, dt)`` — the
    Ursina-land half (wall-avoiding swept move + ``enemy.position +=``) lives
    on the enemy, exactly as ``ChaseNode`` delegates to ``chase_step``. This
    node computes only the framework-free direction vector.

    Edge cases (explicit, not left undefined):

    - Empty ``waypoints`` ([]) — raises ``ValueError`` at construction. A
      patrol node with no route is a configuration error, not a runtime state
      to tick through silently; failing fast at construction surfaces the
      mistake immediately instead of returning a quiet ``FAILURE`` every tick
      forever.
    - Single waypoint — the enemy walks to it, returns ``SUCCESS``,
      ``current_index`` wraps back to 0 (the same waypoint), and the next
      tick starts walking to it again (distance ~0, so it immediately
      ``SUCCESS``s again). This is NOT an infinite-SUCCESS-in-one-tick storm:
      each ``SUCCESS`` still costs exactly one ``tick()`` call, so a parent
      ``Selector``/``Sequence`` ticking once per frame just sees a node that
      perpetually re-succeeds on every frame — identical in cost to
      ``IdleNode``. Covered by ``test_single_waypoint_loops_without_crash``.

    Per-instance mutable state — ``current_index`` lives on ``self``. Safe
    under the one-tree-per-enemy ownership contract in ``behaviour_tree.py``
    (each enemy's ``PatrolNode`` is a distinct instance, never shared).
    """

    def __init__(self, waypoints: list, speed: float = PATROL_SPEED_DEFAULT,
                 threshold: float = PATROL_WAYPOINT_THRESHOLD):
        if not waypoints:
            raise ValueError("PatrolNode requires at least one waypoint")
        self.waypoints = waypoints
        self.speed = speed
        self.threshold = threshold
        self.current_index = 0

    def tick(self, enemy: object, dt: float) -> Status:
        target = self.waypoints[self.current_index]
        to_target = target - enemy.position
        if to_target.length() <= self.threshold:
            self.current_index = (self.current_index + 1) % len(self.waypoints)
            return Status.SUCCESS

        direction = to_target.normalized()
        enemy.patrol_step(direction, self.speed, dt)
        return Status.RUNNING


class FleeNode(BehaviourNode):
    """Run away from the player while low on HP; yield once HP recovers or
    enough distance has opened.

    Per the v1.4 spec (Step 5), with one ambiguity resolved explicitly (see
    "flee_range semantics" below):

    - ``enemy.health >= flee_threshold_hp``  -> ``FAILURE`` immediately, NO
      movement attempted. This is the "not fleeing" case: the parent
      ``Selector`` (in the ``flee_when_low`` preset, Step 7) falls through to
      the chase/attack branch.
    - ``enemy.health <  flee_threshold_hp`` AND distance to player
      ``<= flee_range``                      -> step away from the player,
      return ``RUNNING`` (still too close, wants to be ticked again).
    - ``enemy.health <  flee_threshold_hp`` AND distance to player
      ``>  flee_range``                      -> ``SUCCESS``, NO movement
      attempted (already far enough — nothing to do this tick).

    flee_range semantics (Step 5 PART 0-A — resolved, not re-derived by
    callers): ``flee_range`` is the TARGET SEPARATION DISTANCE from the
    player, not a total travel budget measured from the enemy's flee-start
    position. Concretely: ``FleeNode`` returns ``SUCCESS`` the instant
    ``(enemy.position - player.position).length() > flee_range`` is already
    true — whether that distance opened up over ten ticks of fleeing or was
    already true on the very first tick (e.g. the player is far away but HP
    is low for some other reason). There is no accumulated "distance fled"
    counter anywhere in this node. Step 7's ``flee_when_low`` preset must
    wire ``flee_range`` as a separation radius, not a travel distance.

    Health attribute (Step 5 PART 0-B — confirmed by reading ``enemy.py``
    and ``collision_system.py``, not assumed): ``Enemy.__init__`` sets
    ``self.health = hp`` (see ``Scripts/enemy.py``); ``AliveEntity`` (the
    ``Enemy`` base class, in ``Scripts/collision_system.py``) does not
    define or shadow ``health`` itself. ``enemy.health`` is therefore the
    correct, and only, attribute to read here — matching what
    ``Enemy.update()`` already compares against ``<= 0`` for death and what
    ``HealthBar.value`` is driven from.

    Movement inversion — chosen approach and why: this node reuses
    ``enemy.chase_step(direction, dt)`` (the SAME method ``ChaseNode`` calls;
    see this module's docstring "Why ChaseNode delegates the move to
    enemy.chase_step"), passing it the INVERTED vector
    ``(enemy.position - player.position).normalized()`` — enemy-minus-player
    instead of ChaseNode's player-minus-enemy — so the same wall-avoiding
    swept-move machinery in ``Enemy.chase_step`` drives the enemy away from
    the player instead of toward it. ``chase_step`` itself takes a raw
    ``direction`` argument and has no opinion about which way it points, so
    no new ``flee_step`` method or "virtual target behind the enemy" trick is
    needed — feeding it the opposite unit vector is sufficient and reuses
    100% of the existing wall-avoidance code (no duplicated raycast loop,
    per ``brain/Patterns`` and ``docs/v1.4-enemy-behaviour-trees.md``). A
    second movement method was considered and rejected: it would duplicate
    ``chase_step``'s move-then-axis-slide body for zero behavioural
    difference, since "moving away" is just "moving toward" with the
    direction vector negated before the call.

    Speed — ``ENEMY_CHASE_SPEED`` is NOT referenced directly here (this file
    stays free of ``enemy.py`` imports per the purity contract); instead,
    speed comes for free because ``chase_step`` is the same method
    ``ChaseNode`` calls, and ``Enemy.chase_step`` hardcodes its move to
    ``ENEMY_CHASE_SPEED`` internally (see ``Scripts/enemy.py``). Reusing
    ``chase_step`` rather than ``patrol_step`` (which takes an explicit
    ``speed`` argument) is therefore also what keeps flee speed identical to
    chase speed without this node inventing a fourth speed value or passing
    one through.

    Player reference — same pattern ``ChaseNode``/``AttackNode`` already use:
    ``enemy.player.position``. No new player-reference mechanism introduced.

    Oscillation behaviour in the ``flee_when_low`` preset (Step 7) — documented
    here so it is not mistaken for a bug when observed in-game:

    - tick N:     HP low, player within ``flee_range``     -> ``RUNNING`` (fleeing)
    - tick N+k:   HP low, player now beyond ``flee_range``  -> ``SUCCESS``
    - tick N+k+1: HP still low, player still beyond range   -> ``SUCCESS``
      immediately, no movement needed (distance check alone satisfies it) ->
      the Step 7 ``Selector`` returns ``SUCCESS`` from this branch -> the
      enemy idles in place rather than re-engaging.
    - Eventually: the player closes the distance again, OR HP recovers above
      ``flee_threshold_hp`` (this node now returns ``FAILURE`` instead) ->
      the ``Selector`` falls through to the chase/attack branch and the
      enemy re-engages.

    This is the intended, correct steady state for a "kited" low-HP enemy —
    it does not chase the player down once it has successfully created
    distance; it waits (via repeated cheap ``SUCCESS``-without-movement
    ticks) for either HP to recover or the player to close in again. Re-
    engagement is entirely the parent Selector's responsibility, not this
    node's — ``FleeNode`` only ever reports its own HP/distance state.

    Per-instance state — this node holds none across ticks (no cursor, no
    "distance fled so far" accumulator); every tick re-derives its answer
    fresh from current HP and current distance, matching the stateless
    RUNNING re-evaluation convention in ``behaviour_tree.py``.
    """

    def __init__(self, flee_threshold_hp: int, flee_range: float):
        self.flee_threshold_hp = flee_threshold_hp
        self.flee_range = flee_range

    def tick(self, enemy: object, dt: float) -> Status:
        if enemy.health >= self.flee_threshold_hp:
            return Status.FAILURE

        distance = (enemy.position - enemy.player.position).length()
        if distance > self.flee_range:
            # Already far enough away — nothing to do this tick.
            return Status.SUCCESS

        # Too close: step away from the player. Inverted ChaseNode direction:
        # enemy-minus-player (away), not player-minus-enemy (toward).
        direction = (enemy.position - enemy.player.position).normalized()
        enemy.chase_step(direction, dt)
        return Status.RUNNING
