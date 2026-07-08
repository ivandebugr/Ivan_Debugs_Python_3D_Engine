"""Unit tests for Scripts/behaviour_nodes.py (v1.4 — Step 2: IdleNode, AttackNode;
Step 3: ChaseNode; Step 4: PatrolNode; Step 5: FleeNode).

Pure unit tests: no Ursina import, no app launch, no window. The enemy and
player are mock objects exposing only the attributes the nodes read:
``enemy.position``, ``enemy.player.position``, ``enemy.shoot()``,
``enemy.chase_step()`` (Step 3, also reused by Step 5's ``FleeNode``) and
``enemy.patrol_step()`` (Step 4). ``position`` is a tiny vector stub with the
handful of operations the nodes use (subtraction, length, normalisation) so
the node math runs without Ursina's Vec3.

Wall-clock cooldown is controlled by monkeypatching the module's ``_time``
reference so tests advance time deterministically — no real sleeping.

Run with either:
    python3 -m unittest tests.test_behaviour_nodes -v
    python3 -m pytest tests/test_behaviour_nodes.py
"""

import math
import unittest

import Scripts.behaviour_nodes as bn
from Scripts.behaviour_nodes import (
    AttackNode,
    ChaseNode,
    DetectPlayerNode,
    FleeNode,
    IdleNode,
    PatrolNode,
)
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

    ``health`` defaults to 100 (matching ``ENEMY_HP_DEFAULT`` in
    ``enemy.py``) so existing tests that don't care about HP are unaffected;
    FleeNode tests override it via the constructor or by setting it directly,
    the same way ``Enemy.health`` is a plain mutable attribute in production.

    ``move_on_chase_step``, when True, makes ``chase_step`` (Step 3, reused
    by Step 5's FleeNode with an inverted direction) actually advance
    ``self.position`` by ``direction * dt`` — mirrors ``move_on_patrol_step``,
    used by FleeNode's multi-tick flee-to-success test.
    """

    def __init__(self, position, player=None, move_on_patrol_step=False,
                 health=100, move_on_chase_step=False, sees_player=True):
        self.position = position
        self.player = player
        self.shoot_count = 0
        self.chase_calls = []     # list of (direction, dt) tuples
        self.patrol_calls = []    # list of (direction, speed, dt) tuples
        self.move_on_patrol_step = move_on_patrol_step
        self.health = health
        self.move_on_chase_step = move_on_chase_step
        # DetectPlayerNode delegates its line-of-sight raycast to
        # can_see_player() (the real Enemy.can_see_player wraps _is_occluded).
        # Defaults True so every pre-existing chase/attack/flee test — which
        # assumes the player is always visible — is unaffected. Detection tests
        # flip it to False to simulate a wall breaking LOS.
        self.sees_player = sees_player

    def shoot(self):
        self.shoot_count += 1

    def can_see_player(self):
        return self.sees_player

    def chase_step(self, direction, dt):
        self.chase_calls.append((direction, dt))
        if self.move_on_chase_step:
            self.position = self.position + direction * dt

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


class TestFleeNode(unittest.TestCase):
    """FleeNode (Step 5): flee while HP < threshold and player is within
    flee_range; reuses chase_step with an inverted direction."""

    def test_hp_at_or_above_threshold_fails_immediately_no_movement(self):
        # HP >= threshold: "not fleeing" case, FAILURE, zero movement attempted.
        enemy, _ = make_pair(distance=5)
        enemy.health = 50
        node = FleeNode(flee_threshold_hp=50, flee_range=20)
        result = node.tick(enemy, DT)
        self.assertIs(result, Status.FAILURE)
        self.assertEqual(enemy.chase_calls, [], "must not move when not fleeing")

    def test_low_hp_player_within_range_runs_and_moves_away(self):
        # HP below threshold, player close (within flee_range) -> RUNNING,
        # and the step direction must be AWAY from the player, not toward it.
        player = MockPlayer(Vec(5, 0, 0))
        enemy = MockEnemy(Vec(0, 0, 0), player, health=10)
        node = FleeNode(flee_threshold_hp=30, flee_range=20)
        result = node.tick(enemy, DT)

        self.assertIs(result, Status.RUNNING)
        self.assertEqual(len(enemy.chase_calls), 1, "must step once")
        direction, passed_dt = enemy.chase_calls[0]
        # Player is at +x from enemy; fleeing must point -x (away), the
        # opposite of what ChaseNode would compute for the same layout.
        self.assertAlmostEqual(direction[0], -1.0, places=6)
        self.assertAlmostEqual(direction[1], 0.0, places=6)
        self.assertAlmostEqual(direction[2], 0.0, places=6)
        self.assertEqual(passed_dt, DT)

    def test_low_hp_player_already_beyond_range_succeeds_no_movement(self):
        # HP below threshold but player already further than flee_range ->
        # SUCCESS immediately, no movement attempted this tick.
        enemy, _ = make_pair(distance=50)   # 50 > flee_range 20
        enemy.health = 10
        node = FleeNode(flee_threshold_hp=30, flee_range=20)
        result = node.tick(enemy, DT)
        self.assertIs(result, Status.SUCCESS)
        self.assertEqual(enemy.chase_calls, [], "already far enough — no step needed")

    def test_flees_across_multiple_ticks_until_beyond_range(self):
        # Real multi-tick flee: enemy starts close, player stationary, enemy
        # walks away each tick via move_on_chase_step until distance crosses
        # flee_range, at which point the SAME node call transitions to SUCCESS.
        player = MockPlayer(Vec(0, 0, 0))
        enemy = MockEnemy(Vec(1, 0, 0), player, health=10, move_on_chase_step=True)
        node = FleeNode(flee_threshold_hp=30, flee_range=5)

        statuses = []
        for _ in range(2000):
            status = node.tick(enemy, DT)
            statuses.append(status)
            if status is Status.SUCCESS:
                break

        self.assertIn(Status.RUNNING, statuses, "must run while still within flee_range")
        self.assertIs(statuses[-1], Status.SUCCESS, "must eventually succeed once far enough")
        final_distance = (enemy.position - player.position).length()
        self.assertGreater(final_distance, 5, "enemy must have actually opened the distance")

    def test_movement_direction_is_away_for_arbitrary_relative_position(self):
        # Enemy at (1,0,2), player at (4,0,6): ChaseNode's direction test uses
        # this exact layout and expects (0.6, 0, 0.8) toward the player.
        # FleeNode must produce the exact negation: (-0.6, 0, -0.8).
        # Actual distance is 5 (|(3,0,4)| = 5); flee_range must exceed that to
        # force RUNNING (a step) instead of an immediate SUCCESS.
        player = MockPlayer(Vec(4, 0, 6))
        enemy = MockEnemy(Vec(1, 0, 2), player, health=10)
        node = FleeNode(flee_threshold_hp=30, flee_range=10)

        result = node.tick(enemy, DT)
        self.assertIs(result, Status.RUNNING)
        direction, _ = enemy.chase_calls[0]
        self.assertAlmostEqual(direction[0], -0.6, places=6)
        self.assertAlmostEqual(direction[1], 0.0, places=6)
        self.assertAlmostEqual(direction[2], -0.8, places=6)
        self.assertAlmostEqual(direction.length(), 1.0, places=6,
                               msg="direction handed to chase_step must be unit length")

    def test_hp_recovery_mid_flee_switches_to_failure_and_stops_movement(self):
        # Start fleeing (RUNNING), then HP recovers above threshold before the
        # enemy has opened flee_range -> next tick is FAILURE, no more movement.
        player = MockPlayer(Vec(2, 0, 0))
        enemy = MockEnemy(Vec(0, 0, 0), player, health=10)
        node = FleeNode(flee_threshold_hp=30, flee_range=20)

        self.assertIs(node.tick(enemy, DT), Status.RUNNING)
        calls_before = len(enemy.chase_calls)

        enemy.health = 35   # recovers above threshold
        result = node.tick(enemy, DT)
        self.assertIs(result, Status.FAILURE)
        self.assertEqual(len(enemy.chase_calls), calls_before,
                         "no movement once HP has recovered above threshold")

    def test_oscillation_repeated_success_is_stable_no_crash(self):
        # PART 2: once far enough, repeated ticks with the same low-HP,
        # far-away state must keep returning SUCCESS with no movement and no
        # stuck/crashing state — confirms statelessness across ticks.
        enemy, _ = make_pair(distance=50)
        enemy.health = 10
        node = FleeNode(flee_threshold_hp=30, flee_range=20)
        for _ in range(5):
            result = node.tick(enemy, DT)
            self.assertIs(result, Status.SUCCESS)
        self.assertEqual(enemy.chase_calls, [], "never moves once already beyond flee_range")

    def test_speed_comes_from_shared_chase_step_not_a_new_value(self):
        # FleeNode must reuse enemy.chase_step (the SAME method ChaseNode
        # calls) rather than introduce its own speed-aware method — confirmed
        # structurally: the call recorded in chase_calls is indistinguishable
        # in shape from a ChaseNode call (direction, dt) with no speed
        # argument, proving speed is owned entirely by Enemy.chase_step
        # (ENEMY_CHASE_SPEED), not passed in or reinvented here.
        player = MockPlayer(Vec(5, 0, 0))
        enemy = MockEnemy(Vec(0, 0, 0), player, health=10)
        node = FleeNode(flee_threshold_hp=30, flee_range=20)
        node.tick(enemy, DT)
        self.assertEqual(len(enemy.chase_calls[0]), 2,
                         "chase_step call signature is (direction, dt) only — no speed arg")


class TestDetectPlayerNode(unittest.TestCase):
    """DetectPlayerNode (v1.7): condition gate — SUCCESS when the player is in
    range AND visible, FAILURE otherwise. Never RUNNING, never moves/shoots.
    Range boundary has hysteresis; LOS does not."""

    def test_in_range_and_visible_succeeds(self):
        enemy, _ = make_pair(distance=50)              # 50 <= detection 100
        enemy.sees_player = True
        node = DetectPlayerNode(detection_range=100)
        self.assertIs(node.tick(enemy, DT), Status.SUCCESS)

    def test_in_range_but_blocked_fails(self):
        # In range, but a wall breaks LOS -> FAILURE (no hysteresis on LOS).
        enemy, _ = make_pair(distance=50)
        enemy.sees_player = False
        node = DetectPlayerNode(detection_range=100)
        self.assertIs(node.tick(enemy, DT), Status.FAILURE)

    def test_out_of_range_fails_even_if_visible(self):
        enemy, _ = make_pair(distance=150)             # 150 > detection 100
        enemy.sees_player = True
        node = DetectPlayerNode(detection_range=100)
        self.assertIs(node.tick(enemy, DT), Status.FAILURE)

    def test_never_moves_or_shoots(self):
        # Condition node: must not touch chase_step/patrol_step/shoot in any state.
        for dist, sees in ((50, True), (50, False), (150, True)):
            enemy, _ = make_pair(distance=dist)
            enemy.sees_player = sees
            DetectPlayerNode(detection_range=100).tick(enemy, DT)
            self.assertEqual(enemy.chase_calls, [])
            self.assertEqual(enemy.patrol_calls, [])
            self.assertEqual(enemy.shoot_count, 0)

    def test_never_returns_running(self):
        # Whatever the state, the gate resolves immediately (SUCCESS/FAILURE) so
        # the parent Sequence never short-circuits mid-frame at the gate — this
        # is what preserves the stateless restart-from-child-0 convention.
        for dist, sees in ((10, True), (99, True), (101, True), (50, False)):
            enemy, _ = make_pair(distance=dist)
            enemy.sees_player = sees
            result = DetectPlayerNode(detection_range=100).tick(enemy, DT)
            self.assertIn(result, (Status.SUCCESS, Status.FAILURE))
            self.assertIsNot(result, Status.RUNNING)

    def test_boundary_distance_equal_to_range_is_in_range(self):
        # distance == detection_range counts as detected (`<=` cutoff), matching
        # ChaseNode/AttackNode's inclusive boundary convention.
        enemy, _ = make_pair(distance=100)
        enemy.sees_player = True
        self.assertIs(DetectPlayerNode(detection_range=100).tick(enemy, DT),
                      Status.SUCCESS)

    def test_hysteresis_keeps_detection_in_the_margin_band(self):
        # Once detected, the enemy stays detected out to detection_range *
        # lose_margin, then drops beyond it — no flicker on the acquire boundary.
        node = DetectPlayerNode(detection_range=100, lose_margin=1.15)

        # Acquire at 90 (<= 100).
        enemy, player = make_pair(distance=90)
        enemy.sees_player = True
        self.assertIs(node.tick(enemy, DT), Status.SUCCESS)
        self.assertTrue(node._detected)

        # Drift to 110: outside the ACQUIRE radius (100) but inside the widened
        # KEEP radius (115) -> still detected. A zero-margin gate would drop here.
        player.position = Vec(110, 0, 0)
        self.assertIs(node.tick(enemy, DT), Status.SUCCESS,
                      "must stay detected inside the hysteresis band")

        # Drift to 120: beyond the KEEP radius (115) -> detection drops.
        player.position = Vec(120, 0, 0)
        self.assertIs(node.tick(enemy, DT), Status.FAILURE)
        self.assertFalse(node._detected)

    def test_undetected_uses_narrow_acquire_radius_not_the_widened_one(self):
        # A fresh (never-detected) node at 110 must NOT acquire, even though 110
        # is inside the widened keep-radius — the wide radius only applies once
        # already detected. Proves the hysteresis is directional.
        enemy, _ = make_pair(distance=110)             # 110 > acquire 100
        enemy.sees_player = True
        node = DetectPlayerNode(detection_range=100, lose_margin=1.15)
        self.assertIs(node.tick(enemy, DT), Status.FAILURE,
                      "not-yet-detected must use the narrow acquire radius")

    def test_losing_los_drops_detection_immediately_no_hysteresis(self):
        # Acquire, then break LOS while still well in range -> FAILURE that same
        # tick (LOS has no hysteresis, unlike range) and _detected resets.
        node = DetectPlayerNode(detection_range=100)
        enemy, _ = make_pair(distance=30)
        enemy.sees_player = True
        self.assertIs(node.tick(enemy, DT), Status.SUCCESS)

        enemy.sees_player = False                       # wall drops in
        self.assertIs(node.tick(enemy, DT), Status.FAILURE)
        self.assertFalse(node._detected)


class TestDetectPlayerNodeInSequence(unittest.TestCase):
    """The gated combat branch — Sequence([Detect, Chase, Attack]) — must abort
    at the gate (no chase, no shot) when the player is out of range or blocked,
    and fall through to Idle in the surrounding Selector."""

    def setUp(self):
        self.clock = FakeClock()
        self._real_time = bn._time
        bn._time = self.clock

    def tearDown(self):
        bn._time = self._real_time

    def _tree(self):
        from Scripts.behaviour_tree import Selector, Sequence
        return Selector([
            Sequence([DetectPlayerNode(detection_range=100),
                      ChaseNode(detection_range=100, stop_range=30),
                      AttackNode(attack_range=30, cooldown=1.0)]),
            IdleNode(),
        ])

    def test_blocked_los_in_range_falls_through_to_idle(self):
        # In attack range but no LOS: gate FAILs -> Sequence aborts at child 0
        # -> no chase, no shot -> Selector falls through to Idle SUCCESS.
        enemy, _ = make_pair(distance=10)
        enemy.sees_player = False
        result = self._tree().tick(enemy, DT)
        self.assertIs(result, Status.SUCCESS, "falls through to IdleNode")
        self.assertEqual(enemy.chase_calls, [], "gate blocks chase when no LOS")
        self.assertEqual(enemy.shoot_count, 0, "gate blocks the shot when no LOS")

    def test_visible_and_close_still_chases_and_shoots(self):
        # Gate passes -> behaves exactly like the ungated default tree.
        enemy, _ = make_pair(distance=20)              # within stop_range 30
        enemy.sees_player = True
        result = self._tree().tick(enemy, DT)
        self.assertIs(result, Status.SUCCESS, "Detect->Chase(stop)->Attack fires")
        self.assertEqual(enemy.shoot_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
