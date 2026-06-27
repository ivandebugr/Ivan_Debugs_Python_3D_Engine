"""Unit tests for Scripts/behaviour_nodes.py (v1.4 — Step 2: IdleNode, AttackNode).

Pure unit tests: no Ursina import, no app launch, no window. The enemy and
player are mock objects exposing only the attributes the nodes read:
``enemy.position``, ``enemy.player.position`` and ``enemy.shoot()``. ``position``
is a tiny vector stub with ``__sub__`` and ``.length()`` so AttackNode's
``(enemy.position - player.position).length()`` works without Ursina's Vec3.

Wall-clock cooldown is controlled by monkeypatching the module's ``_time``
reference so tests advance time deterministically — no real sleeping.

Run with either:
    python3 -m unittest tests.test_behaviour_nodes -v
    python3 -m pytest tests/test_behaviour_nodes.py
"""

import math
import unittest

import Scripts.behaviour_nodes as bn
from Scripts.behaviour_nodes import AttackNode, IdleNode
from Scripts.behaviour_tree import Status


DT = 0.016


class Vec(tuple):
    """Minimal 3-vector stub: supports subtraction and Euclidean length.

    Subclasses tuple so equality/repr come for free; only the two operations
    AttackNode uses are implemented.
    """

    def __new__(cls, x, y, z):
        return super().__new__(cls, (x, y, z))

    def __sub__(self, other):
        return Vec(self[0] - other[0], self[1] - other[1], self[2] - other[2])

    def length(self):
        return math.sqrt(self[0] ** 2 + self[1] ** 2 + self[2] ** 2)


class MockPlayer:
    def __init__(self, position):
        self.position = position


class MockEnemy:
    """Mock enemy: position, a player ref, and a shoot() that counts calls."""

    def __init__(self, position, player):
        self.position = position
        self.player = player
        self.shoot_count = 0

    def shoot(self):
        self.shoot_count += 1


class FakeClock:
    """Deterministic monotonic clock substituted for the module's _time.time().

    Tests advance it explicitly via ``advance(seconds)``; nothing sleeps.
    """

    def __init__(self, start=1000.0):
        self.now = start

    def time(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_pair(distance):
    """Enemy at origin, player ``distance`` units away along +x."""
    player = MockPlayer(Vec(distance, 0, 0))
    enemy = MockEnemy(Vec(0, 0, 0), player)
    return enemy, player


class TestIdleNode(unittest.TestCase):
    def test_always_returns_success(self):
        node = IdleNode()
        enemy, _ = make_pair(distance=5)
        # Repeated ticks, varied dt — always SUCCESS, no side effects.
        for dt in (0.0, DT, 1.0):
            with self.subTest(dt=dt):
                self.assertIs(node.tick(enemy, dt), Status.SUCCESS)
        self.assertEqual(enemy.shoot_count, 0, "IdleNode must never shoot")

    def test_success_regardless_of_distance(self):
        node = IdleNode()
        for d in (0, 30, 1000):
            enemy, _ = make_pair(distance=d)
            self.assertIs(node.tick(enemy, DT), Status.SUCCESS)


class TestAttackNode(unittest.TestCase):
    def setUp(self):
        # Swap in a deterministic clock for every AttackNode test.
        self.clock = FakeClock()
        self._real_time = bn._time
        bn._time = self.clock

    def tearDown(self):
        bn._time = self._real_time

    def test_in_range_off_cooldown_succeeds_and_shoots_once(self):
        enemy, _ = make_pair(distance=10)          # 10 <= attack_range 30
        node = AttackNode(attack_range=30, cooldown=1.0)
        result = node.tick(enemy, DT)
        self.assertIs(result, Status.SUCCESS)
        self.assertEqual(enemy.shoot_count, 1, "must call shoot() exactly once")

    def test_in_range_on_cooldown_runs_and_does_not_shoot(self):
        enemy, _ = make_pair(distance=10)
        node = AttackNode(attack_range=30, cooldown=1.0)

        node.tick(enemy, DT)                        # first shot consumes cooldown
        self.assertEqual(enemy.shoot_count, 1)

        self.clock.advance(0.5)                     # still inside the 1.0s cooldown
        result = node.tick(enemy, DT)
        self.assertIs(result, Status.RUNNING)
        self.assertEqual(enemy.shoot_count, 1, "must NOT shoot again while on cooldown")

    def test_out_of_range_fails_and_does_not_shoot(self):
        enemy, _ = make_pair(distance=50)           # 50 > attack_range 30
        node = AttackNode(attack_range=30, cooldown=1.0)
        result = node.tick(enemy, DT)
        self.assertIs(result, Status.FAILURE)
        self.assertEqual(enemy.shoot_count, 0, "out of range must never shoot")

    def test_cooldown_resets_after_successful_attack(self):
        # The required "second in-range tick before cooldown elapses -> RUNNING,
        # not SUCCESS again" case, then proves it fires again once elapsed.
        enemy, _ = make_pair(distance=10)
        node = AttackNode(attack_range=30, cooldown=1.0)

        # Tick 1 — fires.
        self.assertIs(node.tick(enemy, DT), Status.SUCCESS)
        self.assertEqual(enemy.shoot_count, 1)

        # Tick 2 — 0.4s later, still on cooldown -> RUNNING, no second shot.
        self.clock.advance(0.4)
        self.assertIs(node.tick(enemy, DT), Status.RUNNING)
        self.assertEqual(enemy.shoot_count, 1)

        # Tick 3 — total 1.1s elapsed, cooldown done -> fires again.
        self.clock.advance(0.7)                     # 0.4 + 0.7 = 1.1 >= 1.0
        self.assertIs(node.tick(enemy, DT), Status.SUCCESS)
        self.assertEqual(enemy.shoot_count, 2, "cooldown must reset so a later tick fires again")

    def test_boundary_distance_equal_to_range_is_in_range(self):
        # distance == attack_range counts as in range (spec: "within attack_range";
        # node uses `distance > attack_range` for the FAILURE branch).
        enemy, _ = make_pair(distance=30)
        node = AttackNode(attack_range=30, cooldown=1.0)
        self.assertIs(node.tick(enemy, DT), Status.SUCCESS)
        self.assertEqual(enemy.shoot_count, 1)

    def test_first_tick_is_off_cooldown(self):
        # A freshly built node must fire on its first in-range tick (mirrors
        # today's enemy starting with can_shoot=True), not wait out a cooldown.
        enemy, _ = make_pair(distance=5)
        node = AttackNode(attack_range=30, cooldown=99.0)
        self.assertIs(node.tick(enemy, DT), Status.SUCCESS)
        self.assertEqual(enemy.shoot_count, 1)


class TestAttackNodeInSelector(unittest.TestCase):
    """The interim default tree shape — Selector([AttackNode, IdleNode]) —
    resolves the way Enemy.update() relies on."""

    def setUp(self):
        self.clock = FakeClock()
        self._real_time = bn._time
        bn._time = self.clock

    def tearDown(self):
        bn._time = self._real_time

    def test_out_of_range_falls_through_to_idle_success(self):
        from Scripts.behaviour_tree import Selector
        enemy, _ = make_pair(distance=200)          # way out of attack range
        tree = Selector([AttackNode(30, 1.0), IdleNode()])
        # AttackNode FAILS, Selector falls through to IdleNode -> SUCCESS, no shot.
        self.assertIs(tree.tick(enemy, DT), Status.SUCCESS)
        self.assertEqual(enemy.shoot_count, 0)

    def test_in_range_attack_wins_before_idle(self):
        from Scripts.behaviour_tree import Selector
        enemy, _ = make_pair(distance=10)
        tree = Selector([AttackNode(30, 1.0), IdleNode()])
        self.assertIs(tree.tick(enemy, DT), Status.SUCCESS)
        self.assertEqual(enemy.shoot_count, 1, "AttackNode fires; IdleNode not reached")


if __name__ == "__main__":
    unittest.main(verbosity=2)
