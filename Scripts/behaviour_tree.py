"""Behaviour-tree node primitives for enemy AI (v1.4 — Step 1).

This module is **Layer 1** of the three-layer behaviour-tree design (see
``docs/v1.4-enemy-behaviour-trees.md``). It defines the status enum, the
abstract node base, and the three compositors (``Sequence`` / ``Selector`` /
``Parallel``). Leaf nodes, decorators, and the ``BehaviourTreeFactory`` are
later steps and deliberately live elsewhere — Step 1 ships the skeleton only.

Purity contract
---------------
This is a pure data-structure layer with **zero** framework dependency: it
must never import from ``main.py``, ``enemy.py``, or any Ursina / Panda3D
module. All game-state access happens through the ``enemy`` argument passed
into ``tick()``; nodes never reach out to global state. This keeps the tree
unit-testable without launching the app (see ``tests/test_behaviour_tree.py``)
and breaks the circular-import risk that motivated the design. See the
"Pure Python layer for game content generators" pattern in ``brain/Patterns``.

Ownership contract (one tree per enemy — never shared)
------------------------------------------------------
A tree instance is owned by **exactly one** enemy and is never shared or
cached across enemies. ``Enemy.__init__`` calls ``BehaviourTreeFactory.build()``
per instance, so even two enemies using the *same preset name* each get their
own freshly-built tree — same shape, distinct node objects. This is a
deliberate contract, not an accident of calling order.

The consequence, relied on by Steps 3–7: **nodes may hold per-instance mutable
state directly on ``self``** (e.g. a future ``Cooldown`` decorator's timer, or
a ``PatrolNode``'s waypoint index). Because that state is scoped to one enemy's
tree, there is no cross-enemy aliasing to worry about — a node's ``self`` is
private to a single enemy. Do **not** introduce a shared/cached tree registry
in a later step; it would silently break this guarantee.

RUNNING convention (stateless re-evaluation)
--------------------------------------------
When a child returns ``RUNNING`` inside a ``Sequence`` or ``Selector``, the
parent returns ``RUNNING`` immediately and does **not** tick later siblings
this frame. On the *next* ``tick()`` the parent re-evaluates **from the first
child again** — it does NOT remember "which child was running" and resume from
there. Compositors therefore carry no cross-frame cursor.

This matches the leaf nodes as specified: ``ChaseNode`` / ``AttackNode``
re-check distance and cooldown fresh every tick rather than resuming partial
execution, so a stateless re-evaluation is the design the rest of the tree is
built around. ``Parallel`` ticks every child every frame regardless, so the
distinction does not apply to it.
"""

from __future__ import annotations

import time as _time
from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Type-only import: never executed at runtime, so it cannot pull Ursina or
    # the Enemy class into this pure layer. ``enemy`` is duck-typed at runtime —
    # any object exposing the attributes a leaf node reads (position, health,
    # shoot(), ...) works. Annotated loosely as ``object`` to keep the contract
    # honest: this layer makes no structural promise about the enemy.
    pass


class Status(Enum):
    """Result of ticking a behaviour node for one frame.

    - ``SUCCESS``  — the node finished its work successfully this tick.
    - ``FAILURE``  — the node could not run / its precondition was not met.
    - ``RUNNING``  — the node is mid-action and wants to be ticked again.
    """

    SUCCESS = "success"
    FAILURE = "failure"
    RUNNING = "running"


class BehaviourNode(ABC):
    """Abstract base for every node in an enemy's behaviour tree.

    Subclasses implement :meth:`tick`, which is called once per frame with the
    owning ``enemy`` and the frame delta ``dt``. All game-state access flows
    through ``enemy`` — nodes never import or touch global / framework state
    (see the module docstring's purity contract).

    Ownership: an instance of any ``BehaviourNode`` subtree belongs to exactly
    one enemy and is never shared. Per-instance mutable state on ``self`` is
    therefore safe (module docstring's ownership contract).

    ``tick`` is abstract: a subclass that forgets to override it cannot be
    instantiated, so the omission fails loudly at construction rather than
    silently returning ``None`` at runtime.
    """

    @abstractmethod
    def tick(self, enemy: object, dt: float) -> Status:
        """Advance this node by one frame and return its :class:`Status`.

        Args:
            enemy: The enemy that owns this tree. The sole channel to game
                state — nodes read/act on the world only through it.
            dt: Seconds elapsed since the previous frame (Ursina's ``time.dt``
                at the call site).

        Returns:
            The node's :class:`Status` for this tick.
        """
        raise NotImplementedError


class Sequence(BehaviourNode):
    """Tick children left-to-right; AND semantics.

    - Returns ``FAILURE`` on the first child that returns ``FAILURE`` (later
      siblings are not ticked this frame).
    - Returns ``RUNNING`` on the first child that returns ``RUNNING`` (later
      siblings are not ticked this frame); re-evaluated from child 0 next tick.
    - Returns ``SUCCESS`` only when every child returns ``SUCCESS``.
    - Empty sequence returns ``SUCCESS`` (the identity of an AND — the standard
      convention: a sequence of no requirements is vacuously satisfied).
    """

    def __init__(self, children: list[BehaviourNode]):
        self.children: list[BehaviourNode] = children

    def tick(self, enemy: object, dt: float) -> Status:
        for child in self.children:
            status = child.tick(enemy, dt)
            if status is not Status.SUCCESS:
                # FAILURE or RUNNING short-circuits: stop here, report it,
                # and re-evaluate from the first child on the next tick.
                return status
        return Status.SUCCESS


class Selector(BehaviourNode):
    """Tick children left-to-right; OR semantics (a.k.a. fallback / priority).

    - Returns ``SUCCESS`` on the first child that returns ``SUCCESS`` (later
      siblings are not ticked this frame).
    - Returns ``RUNNING`` on the first child that returns ``RUNNING`` (later
      siblings are not ticked this frame); re-evaluated from child 0 next tick.
    - Returns ``FAILURE`` only when every child returns ``FAILURE``.
    - Empty selector returns ``FAILURE`` (the identity of an OR — the standard
      convention: a choice among no options has nothing that can succeed).
    """

    def __init__(self, children: list[BehaviourNode]):
        self.children: list[BehaviourNode] = children

    def tick(self, enemy: object, dt: float) -> Status:
        for child in self.children:
            status = child.tick(enemy, dt)
            if status is not Status.FAILURE:
                # SUCCESS or RUNNING short-circuits: stop here, report it,
                # and re-evaluate from the first child on the next tick.
                return status
        return Status.FAILURE


class Parallel(BehaviourNode):
    """Tick ALL children every frame regardless of individual results.

    Unlike :class:`Sequence` / :class:`Selector`, ``Parallel`` never
    short-circuits — every child is ticked on every frame even after one fails
    or succeeds. Aggregate result:

    - Returns ``SUCCESS`` only when every child returns ``SUCCESS``.
    - Otherwise returns ``RUNNING`` if any child is still ``RUNNING``.
    - Otherwise returns ``FAILURE`` (at least one child failed and none is
      running).
    - Empty parallel returns ``SUCCESS`` (vacuously, like an empty AND — every
      child succeeded because there are none).
    """

    def __init__(self, children: list[BehaviourNode]):
        self.children: list[BehaviourNode] = children

    def tick(self, enemy: object, dt: float) -> Status:
        all_succeeded = True
        any_running = False
        for child in self.children:
            # No short-circuit: tick every child, every frame, by contract.
            status = child.tick(enemy, dt)
            if status is not Status.SUCCESS:
                all_succeeded = False
            if status is Status.RUNNING:
                any_running = True
        if all_succeeded:
            return Status.SUCCESS
        if any_running:
            return Status.RUNNING
        return Status.FAILURE


class Invert(BehaviourNode):
    """Flip a child's result: ``SUCCESS``<->``FAILURE``; ``RUNNING`` passes through.

    Exceptions raised by the child during ``tick()`` are **propagated**, not
    caught-and-converted — the safe default. A decorator that swallows
    exceptions to produce a status would hide real bugs (e.g. an
    ``AttributeError`` from a misconfigured ``enemy`` duck type) behind a
    plausible-looking ``SUCCESS``/``FAILURE``, which is worse than a loud crash.
    """

    def __init__(self, child: BehaviourNode):
        self.child = child

    def tick(self, enemy: object, dt: float) -> Status:
        status = self.child.tick(enemy, dt)
        if status is Status.SUCCESS:
            return Status.FAILURE
        if status is Status.FAILURE:
            return Status.SUCCESS
        return status


class Repeat(BehaviourNode):
    """Tick ``child`` until it has returned ``SUCCESS`` a total of ``n`` times.

    - What counts as "one completion": a tick where the child returns
      ``SUCCESS``. A child returning ``RUNNING`` does NOT increment the
      counter — ``Repeat`` waits for the child to resolve before counting,
      and itself returns ``RUNNING`` that tick.
    - A child returning ``FAILURE``: ``Repeat`` propagates ``FAILURE``
      immediately (it cannot complete the full ``n`` repetitions) and resets
      its completion counter to 0.
    - When the ``n``th completion is reached: ``Repeat`` returns ``SUCCESS``
      and resets its counter to 0, so a parent ``Repeat`` or a re-ticked
      ``Selector``/``Sequence`` can reuse this instance from a clean slate
      without reconstructing it.
    - ``n=-1`` (infinite): the primary use case (infinite patrol loops etc).
      The counter never reaches a terminal value, so this node never returns
      ``SUCCESS`` — it returns ``RUNNING`` for every child ``SUCCESS`` and
      ``FAILURE`` for any child ``FAILURE``, forever.
    - ``n=0``: zero repetitions requested. Returns ``SUCCESS`` immediately on
      the first tick, without ticking the child at all.

    Per-instance mutable state — the completion counter lives on ``self``,
    safe under the one-tree-per-enemy ownership contract (module docstring).
    """

    def __init__(self, child: BehaviourNode, n: int):
        self.child = child
        self.n = n
        self._count = 0

    def tick(self, enemy: object, dt: float) -> Status:
        if self.n == 0:
            return Status.SUCCESS

        status = self.child.tick(enemy, dt)
        if status is Status.RUNNING:
            return Status.RUNNING
        if status is Status.FAILURE:
            self._count = 0
            return Status.FAILURE

        # SUCCESS: one completion.
        self._count += 1
        if self.n == -1:
            # Infinite mode never resolves — always RUNNING after a success.
            return Status.RUNNING
        if self._count >= self.n:
            self._count = 0
            return Status.SUCCESS
        return Status.RUNNING


class Cooldown(BehaviourNode):
    """Rate-limit ``child`` to at most one new tick cycle per ``seconds``.

    RUNNING-interaction decision (the most non-obvious part of this node):
    the cooldown timer starts (or restarts) only when ``child`` *resolves*
    (returns ``SUCCESS`` or ``FAILURE``) — not when ``Cooldown`` itself is
    ticked. While ``child`` is returning ``RUNNING`` across multiple frames,
    every ``Cooldown.tick()`` call passes straight through to ``child.tick()``
    without consulting or restarting the timer, because the child is
    mid-execution, not starting a new cycle. Only once the child resolves
    does the cooldown window begin. The next ``Cooldown.tick()`` call inside
    that window does **not** re-tick the child at all — it returns the
    child's last resolved status (``SUCCESS`` or ``FAILURE``) directly. This
    applies identically to both resolved statuses: a child that last
    resolved ``FAILURE`` is rate-limited exactly like one that resolved
    ``SUCCESS``. Once the window elapses, the next tick invokes the child
    again and the cycle repeats.

    Time tracking matches ``AttackNode`` (``Scripts/behaviour_nodes.py``):
    wall-clock ``time.time()`` timestamp comparison, not dt-accumulation.

    Edge cases:
    - First tick ever (no prior resolution): always allowed — the cooldown
      only applies *after* the first resolution, never before it.
    - ``seconds=0``: no rate limiting — the child is ticked on every call.
    - Child still ``RUNNING``: passed through every time, timer untouched.

    Per-instance mutable state — ``self._last_resolved_time`` and
    ``self._last_status`` live on ``self``, safe under the one-tree-per-enemy
    ownership contract (module docstring).
    """

    def __init__(self, child: BehaviourNode, seconds: float):
        self.child = child
        self.seconds = seconds
        self._last_resolved_time = None
        self._last_status: Status | None = None

    def tick(self, enemy: object, dt: float) -> Status:
        now = _time.time()
        if (
            self._last_resolved_time is not None
            and self.seconds > 0
            and now - self._last_resolved_time < self.seconds
        ):
            # Within the cooldown window since the child last resolved —
            # rate-limited: do not re-tick the child, replay its last status.
            return self._last_status

        status = self.child.tick(enemy, dt)
        if status is not Status.RUNNING:
            self._last_resolved_time = now
            self._last_status = status
        return status
