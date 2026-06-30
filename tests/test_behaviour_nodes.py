"""Unit tests for Scripts/behaviour_nodes.py (v1.4 — Step 2: IdleNode, AttackNode;
Step 3: ChaseNode; Step 4: PatrolNode).

Pure unit tests: no Ursina import, no app launch, no window. The enemy and
player are mock objects exposing only the attributes the nodes read:
``enemy.position``, ``enemy.player.position``, ``enemy.shoot()``,
``enemy.chase_step()`` (Step 3) and ``enemy.patrol_step()`` (Step 4).
``position`` is a tiny vector stub with the handful of operations the nodes
use (subtraction, length, normalisation) so the node math runs without
Ursina's Vec3.

Wall-clock cooldown is controlled by monkeypatching the module's ``_time``
reference so tests advance time deterministically — no real sleeping.

Run with either:
    python3 -m unittest tests.test_behaviour_nodes -v
    python3 -m pytest tests/test_behaviour_nodes.py
"""

import math
import unittest

import Scripts.behaviour_nodes as bn
from Scripts.behaviour_nodes import AttackNode, ChaseNode, IdleNode, PatrolNode
from Scripts.behaviour_tree import Status


DT = 0.016


class Vec(tuple):
    """Minimal 3-vector stub: addition, subtraction, scaling, Euclidean
    length, and normalisation.

    Subclasses tuple so equality/repr come for free; only the operations the
    leaf nodes use are implemented. ``__sub__`` + ``length`` cover AttackNode;
    ChaseNode additionally calls ``.normalized()`` on the enemy→player vector.
    ``__add__`` and ``__mul__`` (Step 4) support PatrolNode tests that
    simulate real position updates via ``direction * speed * dt`` then
    ``position + step`` — the same arithmetic the real Vec3-backed
    Enemy.patrol_step performs.
    """

    def __new__(cls, x, y, z):
        return super().__new__(cls, (x, y, z))

    def __add__(self, other):
        return Vec(self[0] + other[0], self[1] + other[1], self[2] + other[2])

    def __sub__(self, other):
        return Vec(self[0] - other[0], self[1] - other[1], self[2] - other[2])

    def __mul__(self, scalar):
        return Vec(self[0] * scalar, self[1] * scalar, self[2] * scalar)

    def length(self):
        return math.sqrt(self[0] ** 2 + self[1] ** 2 + self[2] ** 2)

    def normalized(self):
        n = self.length()
        if n == 0:
            return Vec(0, 0, 0)
        return Vec(self[0] / n, self[1] / n, self[2] / n)


class MockPlayer:
    def __init__(self, position):
        self.position = position


class MockEnemy:
    """Mock enemy: position, a player ref, shoot()/chase_step()/patrol_step() recorders.

    ``shoot_count`` counts AttackNode firings. ``chase_calls`` records every
    direction ChaseNode hands to ``chase_step`` (so tests can assert movement
    was — or was not — attempted, and inspect the direction). ``patrol_calls``
    is the same idea for PatrolNode's ``patrol_step``. The mock does NOT move
    itself by default: Chase/PatrolNode's contract is "compute direction +
    delegate"; the real Enemy.chase_step/patrol_step does the position
    update, which is Ursina-land and out of scope for these pure tests.

    ``move_on_patrol_step``, when True, makes ``patrol_step`` actually
    advance ``self.position`` by ``direction * speed * dt`` — used by the
    PatrolNode tests that need to simulate multiple frames of real travel
    (e.g. reaching a waypoint, then cycling to the next one).
    """

    def __init__(self, position, player=None, move_on_patrol_step=False):
        self.position = position
        self.player = player
        self.shoot_count = 0
        self.chase_calls = []     # list of (direction, dt) tuples
        self.patrol_calls = []    # list of (direction, speed, dt) tuples
        self.move_on_patrol_step = move_on_patrol_step

    def shoot(self):
        self.shoot_count += 1

    def chase_step(self, direction, dt):
        self.chase_calls.append((direction, dt))

    def patrol_step(self, direction, speed, dt):
        self.patrol_calls.append((direction, speed, dt))
        if self.move_on_patrol_step:
            self.position = self.position + direction * (speed * dt)


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


class TestChaseNode(unittest.TestCase):
    """ChaseNode (Step 3): chase in detection range, stop+succeed within
    stop_range, fail out of detection range. Never shoots."""

    def test_in_range_returns_running_and_does_not_shoot(self):
        # 60 units away: inside detection (100), beyond stop_range (30) -> chase.
        enemy, _ = make_pair(distance=60)
        node = ChaseNode(detection_range=100, stop_range=30)
        result = node.tick(enemy, DT)
        self.assertIs(result, Status.RUNNING)
        self.assertEqual(len(enemy.chase_calls), 1, "must step toward player once")
        self.assertEqual(enemy.shoot_count, 0,
                         "ChaseNode must NEVER shoot — that is AttackNode's job")

    def test_out_of_range_returns_failure_and_no_movement(self):
        # 150 units away: beyond detection (100) -> FAILURE, no step attempted.
        enemy, _ = make_pair(distance=150)
        node = ChaseNode(detection_range=100, stop_range=30)
        result = node.tick(enemy, DT)
        self.assertIs(result, Status.FAILURE)
        self.assertEqual(enemy.chase_calls, [],
                         "out of detection range must not attempt to move")
        self.assertEqual(enemy.shoot_count, 0)

    def test_within_stop_range_succeeds_without_moving(self):
        # 20 units away: inside stop_range (30) -> SUCCESS, no move, no shot.
        # This is the handoff: Sequence([Chase, Attack]) advances to AttackNode.
        enemy, _ = make_pair(distance=20)
        node = ChaseNode(detection_range=100, stop_range=30)
        result = node.tick(enemy, DT)
        self.assertIs(result, Status.SUCCESS)
        self.assertEqual(enemy.chase_calls, [],
                         "within stop_range the enemy stops closing — no step")
        self.assertEqual(enemy.shoot_count, 0)

    def test_movement_direction_is_normalized_toward_player_from_offset(self):
        # Arbitrary, non-axis-aligned offset: enemy at (1,0,2), player at (4,0,6).
        # enemy->player delta is (3,0,4), |delta| = 5, distance 5 < detection 100
        # and > stop 1 -> chase. Expected direction = (0.6, 0, 0.8), unit length.
        player = MockPlayer(Vec(4, 0, 6))
        enemy = MockEnemy(Vec(1, 0, 2), player)
        node = ChaseNode(detection_range=100, stop_range=1)
        result = node.tick(enemy, DT)

        self.assertIs(result, Status.RUNNING)
        self.assertEqual(len(enemy.chase_calls), 1)
        direction, passed_dt = enemy.chase_calls[0]
        self.assertAlmostEqual(direction[0], 0.6, places=6)
        self.assertAlmostEqual(direction[1], 0.0, places=6)
        self.assertAlmostEqual(direction[2], 0.8, places=6)
        self.assertAlmostEqual(direction.length(), 1.0, places=6,
                               msg="direction handed to chase_step must be unit length")
        self.assertEqual(passed_dt, DT, "dt must be forwarded to chase_step")

    def test_distance_convention_matches_attacknode(self):
        # CONFIRM (not just assert) that ChaseNode and AttackNode compute distance
        # the same way: true length of (enemy.position - player.position), with a
        # strict `>` cutoff (boundary distance == range counts as in-range).
        #
        # 1) Same vector expression: distance == range is in-range for BOTH.
        #    AttackNode at distance==attack_range fires (SUCCESS) — proven by
        #    TestAttackNode.test_boundary_distance_equal_to_range_is_in_range.
        #    ChaseNode at distance==detection_range must therefore NOT fail.
        enemy, _ = make_pair(distance=100)            # exactly detection_range
        node = ChaseNode(detection_range=100, stop_range=30)
        self.assertIsNot(node.tick(enemy, DT), Status.FAILURE,
                         "distance == detection_range must be in-range (strict > cutoff), "
                         "matching AttackNode's `distance > range` boundary")

        # 2) distance == stop_range is within stop (uses `<=`), so it SUCCEEDS —
        #    the same boundary direction AttackNode uses for attack_range.
        enemy2, _ = make_pair(distance=30)            # exactly stop_range
        node2 = ChaseNode(detection_range=100, stop_range=30)
        self.assertIs(node2.tick(enemy2, DT), Status.SUCCESS,
                      "distance == stop_range counts as within attack range")

        # 3) Direct structural confirmation: feed the SAME enemy/player to both
        #    nodes at a distance inside both ranges and confirm each reads the
        #    identical scalar via the identical vector expression. AttackNode
        #    FAILS when distance > attack_range; ChaseNode FAILS when distance >
        #    detection_range. With detection_range == attack_range they must agree
        #    on the FAILURE boundary exactly.
        chase = ChaseNode(detection_range=30, stop_range=0)
        attack = AttackNode(attack_range=30, cooldown=1.0)
        for d in (29.99, 30.0, 30.01):
            with self.subTest(distance=d):
                e_chase, _ = make_pair(distance=d)
                e_attack, _ = make_pair(distance=d)
                chase_failed = chase.tick(e_chase, DT) is Status.FAILURE
                attack_failed = attack.tick(e_attack, DT) is Status.FAILURE
                self.assertEqual(chase_failed, attack_failed,
                                 f"Chase and Attack must agree on the range boundary at d={d}")


class TestDefaultTreeShape(unittest.TestCase):
    """The interim default tree — Selector([Sequence([Chase, Attack]), Idle]) —
    resolves the way Enemy.update() relies on across the three regimes."""

    def setUp(self):
        self.clock = FakeClock()
        self._real_time = bn._time
        bn._time = self.clock

    def tearDown(self):
        bn._time = self._real_time

    def _tree(self):
        from Scripts.behaviour_tree import Selector, Sequence
        return Selector([
            Sequence([ChaseNode(detection_range=100, stop_range=30),
                      AttackNode(attack_range=30, cooldown=1.0)]),
            IdleNode(),
        ])

    def test_out_of_detection_idles_no_move_no_shot(self):
        enemy, _ = make_pair(distance=200)
        result = self._tree().tick(enemy, DT)
        self.assertIs(result, Status.SUCCESS, "falls through to IdleNode")
        self.assertEqual(enemy.chase_calls, [], "no chase outside detection range")
        self.assertEqual(enemy.shoot_count, 0, "no shot outside detection range")

    def test_in_detection_beyond_attack_chases_does_not_shoot(self):
        enemy, _ = make_pair(distance=60)   # inside 100, outside 30
        result = self._tree().tick(enemy, DT)
        self.assertIs(result, Status.RUNNING, "ChaseNode RUNNING short-circuits Sequence")
        self.assertEqual(len(enemy.chase_calls), 1, "enemy closes the gap")
        self.assertEqual(enemy.shoot_count, 0, "too far to shoot yet")

    def test_within_attack_range_stops_and_shoots(self):
        enemy, _ = make_pair(distance=20)   # inside stop_range 30
        result = self._tree().tick(enemy, DT)
        self.assertIs(result, Status.SUCCESS, "Chase SUCCEEDS -> Attack fires -> Sequence SUCCESS")
        self.assertEqual(enemy.chase_calls, [], "stops closing within attack range")
        self.assertEqual(enemy.shoot_count, 1, "AttackNode fires on the handoff")


class TestPatrolNode(unittest.TestCase):
    """PatrolNode (Step 4): walk a looping waypoint route via patrol_step."""

    def test_construction_rejects_empty_waypoints(self):
        with self.assertRaises(ValueError):
            PatrolNode(waypoints=[])

    def test_single_step_toward_first_waypoint_is_running_and_moves(self):
        # Waypoint far enough away that one step does not reach it.
        enemy = MockEnemy(Vec(0, 0, 0), move_on_patrol_step=True)
        node = PatrolNode(waypoints=[Vec(10, 0, 0)])
        result = node.tick(enemy, DT)
        self.assertIs(result, Status.RUNNING)
        self.assertEqual(len(enemy.patrol_calls), 1, "must step toward the waypoint once")
        self.assertNotEqual(enemy.position, Vec(0, 0, 0), "position must have advanced")
        self.assertEqual(node.current_index, 0, "index must not advance while still in transit")

    def test_reaching_waypoint_returns_success_and_advances_index(self):
        # Two waypoints; place the enemy already within threshold of waypoint 0.
        enemy = MockEnemy(Vec(0.1, 0, 0))
        node = PatrolNode(waypoints=[Vec(0, 0, 0), Vec(5, 0, 0)], threshold=0.3)
        result = node.tick(enemy, DT)
        self.assertIs(result, Status.SUCCESS)
        self.assertEqual(enemy.patrol_calls, [], "must not step when already within threshold")
        self.assertEqual(node.current_index, 1, "index must advance to the next waypoint")

    def test_cycles_from_last_waypoint_back_to_index_zero(self):
        # Two-waypoint path: drive the node through both SUCCESS handoffs and
        # confirm current_index wraps 0 -> 1 -> 0.
        waypoints = [Vec(0, 0, 0), Vec(0.05, 0, 0)]
        enemy = MockEnemy(Vec(0, 0, 0))
        node = PatrolNode(waypoints=waypoints, threshold=0.3)

        # Already within threshold of waypoint 0 -> SUCCESS, advance to 1.
        self.assertIs(node.tick(enemy, DT), Status.SUCCESS)
        self.assertEqual(node.current_index, 1)

        # Waypoint 1 (0.05,0,0) is also within threshold of the enemy's
        # (unmoved) position -> SUCCESS, wraps back to 0.
        self.assertIs(node.tick(enemy, DT), Status.SUCCESS)
        self.assertEqual(node.current_index, 0, "must wrap back to the first waypoint")

    def test_single_waypoint_loops_without_crash(self):
        # Enemy starts already at the only waypoint: every tick SUCCEEDs and
        # current_index stays 0 (wraps to itself) — confirm no crash, no
        # infinite-loop-in-one-call, and no movement ever attempted.
        enemy = MockEnemy(Vec(3, 1, 0))
        node = PatrolNode(waypoints=[Vec(3, 1, 0)], threshold=0.3)
        for _ in range(5):
            result = node.tick(enemy, DT)
            self.assertIs(result, Status.SUCCESS)
            self.assertEqual(node.current_index, 0)
        self.assertEqual(enemy.patrol_calls, [], "enemy already at the only waypoint, never moves")

    def test_movement_direction_is_normalized_toward_each_waypoint(self):
        # Enemy at (1,0,2), waypoint at (4,0,6): delta (3,0,4), |delta|=5,
        # expected direction (0.6, 0, 0.8), unit length — same convention
        # ChaseNode's direction test uses.
        enemy = MockEnemy(Vec(1, 0, 2))
        node = PatrolNode(waypoints=[Vec(4, 0, 6)], threshold=0.01)
        result = node.tick(enemy, DT)

        self.assertIs(result, Status.RUNNING)
        self.assertEqual(len(enemy.patrol_calls), 1)
        direction, speed, passed_dt = enemy.patrol_calls[0]
        self.assertAlmostEqual(direction[0], 0.6, places=6)
        self.assertAlmostEqual(direction[1], 0.0, places=6)
        self.assertAlmostEqual(direction[2], 0.8, places=6)
        self.assertAlmostEqual(direction.length(), 1.0, places=6,
                               msg="direction handed to patrol_step must be unit length")
        self.assertEqual(passed_dt, DT, "dt must be forwarded to patrol_step")

    def test_default_speed_matches_enemy_chase_speed(self):
        # PART 2 Decision B: PatrolNode's default speed must equal the same
        # constant enemy.py uses for chase movement (ENEMY_CHASE_SPEED = 5),
        # not an invented third value.
        enemy = MockEnemy(Vec(10, 0, 0))
        node = PatrolNode(waypoints=[Vec(0, 0, 0)])
        node.tick(enemy, DT)
        _, speed, _ = enemy.patrol_calls[0]
        self.assertEqual(speed, 5, "PatrolNode default speed must match ENEMY_CHASE_SPEED")

    def test_full_two_waypoint_cycle_with_real_movement(self):
        # End-to-end with move_on_patrol_step=True: walk to waypoint 0, then
        # to waypoint 1, then confirm the route loops back toward 0 again.
        waypoints = [Vec(1, 0, 0), Vec(1, 0, 1)]
        enemy = MockEnemy(Vec(0, 0, 0), move_on_patrol_step=True)
        node = PatrolNode(waypoints=waypoints, speed=5, threshold=0.3)

        statuses = []
        for _ in range(200):
            statuses.append(node.tick(enemy, DT))
            if node.current_index == 0 and statuses.count(Status.SUCCESS) >= 2:
                break

        self.assertIn(Status.SUCCESS, statuses, "must reach at least one waypoint")
        self.assertGreaterEqual(statuses.count(Status.SUCCESS), 2,
                                "must cycle through both waypoints and back")
        self.assertEqual(node.current_index, 0, "route must have looped back to index 0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
