"""Unit tests for Scripts/behaviour_tree_factory.py (v1.4 — Step 7).

Two layers, deliberately separated:

1. ``TestFactoryPresets`` / ``TestUnknownPreset`` — PURE, no Ursina, no app,
   no window. They build each preset via ``BehaviourTreeFactory.build()`` and
   tick it against the same mock-enemy / mock-player stubs the other pure
   suites use (``tests/test_behaviour_nodes.py``). Waypoints are converted with
   an injected ``waypoint_factory`` so even ``"patrol_then_attack"`` never pulls
   in ``Vec3``. These cover OUTPUT items 1–6.

2. ``TestEnemyConstructorIntegration`` — boots a real (headless) ``Ursina()``
   once and constructs real ``Enemy`` objects, proving the ``enemy.py`` swap:
   no-arg ctor uses the Factory default (item 7), and an injected tree bypasses
   the Factory (item 8). Skipped automatically if a window/context cannot be
   created, so the pure suite above still runs everywhere.

Run with either:
    python3 -m unittest tests.test_behaviour_tree_factory -v
    python3 -m pytest tests/test_behaviour_tree_factory.py
"""

import math
import unittest

from Scripts.behaviour_tree_factory import BehaviourTreeFactory
from Scripts.behaviour_tree import Cooldown, Selector, Sequence, Status
from Scripts.behaviour_nodes import (
    AttackNode,
    ChaseNode,
    FleeNode,
    IdleNode,
    PatrolNode,
)
import Scripts.behaviour_tree as behaviour_tree_module
import Scripts.behaviour_nodes as behaviour_nodes_module


DT = 0.016


class Vec(tuple):
    """Minimal 3-vector stub (same shape as test_behaviour_nodes.Vec).

    Supports the handful of ops the leaf nodes use: +, -, *, length(),
    normalized(). Used both as the mock enemy/player position type and — via
    ``Vec`` passed as the Factory's ``waypoint_factory`` — as the patrol
    waypoint type, so these tests never import Ursina's Vec3.
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
    """Mock enemy exposing only what the leaf nodes read/call.

    ``health`` defaults to full so non-flee presets tick happily; flee tests
    lower it. ``chase_step`` / ``patrol_step`` / ``shoot`` are recorders — these
    tests only need "ticking the tree doesn't raise and routes sensibly," not
    gameplay correctness (that's the smoke test).
    """

    def __init__(self, position, player=None, health=100):
        self.position = position
        self.player = player
        self.health = health
        self.shoot_count = 0
        self.chase_calls = []
        self.patrol_calls = []

    def shoot(self):
        self.shoot_count += 1

    def chase_step(self, direction, dt):
        self.chase_calls.append((direction, dt))

    def patrol_step(self, direction, speed, dt):
        self.patrol_calls.append((direction, speed, dt))


def _vec_waypoint(x, y, z):
    """Factory seam: build patrol waypoints as no-Ursina Vec stubs."""
    return Vec(x, y, z)


def _flatten(node):
    """Depth-first list of every node in the tree, for structural assertions."""
    found = [node]
    for child in getattr(node, "children", []):
        found.extend(_flatten(child))
    child = getattr(node, "child", None)
    if child is not None:
        found.extend(_flatten(child))
    return found


def _find(node, node_type):
    return [n for n in _flatten(node) if isinstance(n, node_type)]


class TestFactoryPresets(unittest.TestCase):
    """OUTPUT items 1–5: each known preset builds a valid, tickable tree."""

    # ---- item 1: default ----------------------------------------------------

    def test_default_builds_valid_selector_and_ticks(self):
        tree = BehaviourTreeFactory.build("default", {})
        self.assertIsInstance(tree, Selector)
        # Shape: Selector([Sequence([Chase, Attack]), Idle]) — the interim tree.
        self.assertEqual([type(c).__name__ for c in tree.children],
                         ["Sequence", "IdleNode"])
        seq = tree.children[0]
        self.assertEqual([type(c).__name__ for c in seq.children],
                         ["ChaseNode", "AttackNode"])
        # Pinned to enemy.py tuned constants (100 / 30 / 1.0 today).
        chase, attack = seq.children
        self.assertEqual(chase.detection_range, 100)
        self.assertEqual(chase.stop_range, 30)           # stop_range == attack_range
        self.assertEqual(attack.attack_range, 30)
        self.assertEqual(attack.cooldown, 1.0)
        # Ticks without raising on a mock, in and out of range.
        far = MockEnemy(Vec(0, 0, 0), MockPlayer(Vec(500, 0, 0)))
        self.assertIs(tree.tick(far, DT), Status.SUCCESS)   # falls through to Idle
        near = MockEnemy(Vec(0, 0, 0), MockPlayer(Vec(10, 0, 0)))
        self.assertIs(tree.tick(near, DT), Status.SUCCESS)  # Chase->Attack fires
        self.assertEqual(near.shoot_count, 1)

    # ---- item 2: patrol_then_attack WITH waypoints --------------------------

    def test_patrol_then_attack_with_waypoints_has_correct_patrolnode(self):
        config = {"waypoints": [[3, 1, 0], [7, 1, -4]]}
        tree = BehaviourTreeFactory.build("patrol_then_attack", config,
                                          waypoint_factory=_vec_waypoint)
        self.assertIsInstance(tree, Selector)
        # Shape: Selector([Sequence([Chase60, Attack25/0.8]), Patrol, Idle]).
        self.assertEqual([type(c).__name__ for c in tree.children],
                         ["Sequence", "PatrolNode", "IdleNode"])
        chase, attack = tree.children[0].children
        self.assertEqual(chase.detection_range, 60)      # doc literal, less aggressive
        self.assertEqual(chase.stop_range, 25)
        self.assertEqual(attack.attack_range, 25)
        self.assertEqual(attack.cooldown, 0.8)

        patrols = _find(tree, PatrolNode)
        self.assertEqual(len(patrols), 1, "exactly one PatrolNode")
        patrol = patrols[0]
        # Waypoints converted [x,y,z] -> Vec in order, contents preserved.
        self.assertEqual(list(patrol.waypoints), [Vec(3, 1, 0), Vec(7, 1, -4)])
        self.assertEqual(patrol.current_index, 0)

        # Ticks cleanly: player far -> chase Sequence fails -> patrol runs.
        enemy = MockEnemy(Vec(0, 0, 0), MockPlayer(Vec(500, 0, 0)))
        result = tree.tick(enemy, DT)
        self.assertIn(result, (Status.RUNNING, Status.SUCCESS))
        self.assertEqual(len(enemy.patrol_calls), 1, "patrol branch drove a step")

    # ---- item 3: patrol_then_attack WITHOUT waypoints -----------------------

    def test_patrol_then_attack_without_waypoints_is_valid_no_patrol_branch(self):
        # Step 4: PatrolNode([]) raises; the preset must instead OMIT the patrol
        # branch (empty route == "no patrol", not a crash) and still be valid.
        tree = BehaviourTreeFactory.build("patrol_then_attack", {},
                                          waypoint_factory=_vec_waypoint)
        self.assertIsInstance(tree, Selector)
        self.assertEqual([type(c).__name__ for c in tree.children],
                         ["Sequence", "IdleNode"], "no PatrolNode when no waypoints")
        self.assertEqual(_find(tree, PatrolNode), [], "must contain no PatrolNode")
        # Still ticks: player far -> chase fails -> Idle succeeds.
        enemy = MockEnemy(Vec(0, 0, 0), MockPlayer(Vec(500, 0, 0)))
        self.assertIs(tree.tick(enemy, DT), Status.SUCCESS)

    def test_patrol_then_attack_with_no_config_key_at_all(self):
        # config.get("waypoints", []) must not KeyError on a totally empty dict.
        tree = BehaviourTreeFactory.build("patrol_then_attack", {},
                                          waypoint_factory=_vec_waypoint)
        self.assertIsInstance(tree, Selector)

    # ---- item 4: flee_when_low ----------------------------------------------

    def test_flee_when_low_builds_valid_tree_with_correct_fleenode(self):
        tree = BehaviourTreeFactory.build("flee_when_low", {})
        self.assertIsInstance(tree, Selector)
        # Shape: Selector([Sequence([Flee, Idle]), Sequence([Chase, Attack]), Idle]).
        self.assertEqual([type(c).__name__ for c in tree.children],
                         ["Sequence", "Sequence", "IdleNode"])
        # First Sequence is Flee-then-Idle (Idle runs AFTER a successful flee).
        flee_seq = tree.children[0]
        self.assertEqual([type(c).__name__ for c in flee_seq.children],
                         ["FleeNode", "IdleNode"])
        flees = _find(tree, FleeNode)
        self.assertEqual(len(flees), 1)
        self.assertEqual(flees[0].flee_threshold_hp, 30)   # preset table values
        self.assertEqual(flees[0].flee_range, 15)
        # Second Sequence reuses the tuned default chase/attack.
        chase, attack = tree.children[1].children
        self.assertEqual(chase.detection_range, 100)
        self.assertEqual(attack.attack_range, 30)
        self.assertEqual(attack.cooldown, 1.0)

        # Ticks at full HP (flee FAILs -> falls through to chase/attack branch).
        healthy = MockEnemy(Vec(0, 0, 0), MockPlayer(Vec(10, 0, 0)), health=100)
        self.assertIs(tree.tick(healthy, DT), Status.SUCCESS)
        self.assertEqual(healthy.shoot_count, 1, "healthy enemy fights like default")

        # Ticks at low HP, player close (flee branch RUNS, then Idle on success).
        hurt_close = MockEnemy(Vec(0, 0, 0), MockPlayer(Vec(5, 0, 0)), health=10)
        self.assertIs(tree.tick(hurt_close, DT), Status.RUNNING)
        self.assertEqual(len(hurt_close.chase_calls), 1, "fled one step (inverted dir)")
        self.assertEqual(hurt_close.shoot_count, 0, "fleeing enemy does not shoot")

        # Low HP but already far (flee SUCCEEDs immediately -> Idle -> SUCCESS).
        hurt_far = MockEnemy(Vec(0, 0, 0), MockPlayer(Vec(500, 0, 0)), health=10)
        self.assertIs(tree.tick(hurt_far, DT), Status.SUCCESS)
        self.assertEqual(hurt_far.chase_calls, [], "already far enough — no step")

    # ---- item 5: aggressive -------------------------------------------------

    def test_aggressive_builds_wider_detection_and_faster_attack(self):
        tree = BehaviourTreeFactory.build("aggressive", {})
        self.assertIsInstance(tree, Selector)
        self.assertEqual([type(c).__name__ for c in tree.children],
                         ["Sequence", "IdleNode"])
        chase, attack = tree.children[0].children
        self.assertEqual(chase.detection_range, 150, "wider than default's 100")
        self.assertEqual(chase.stop_range, 40)
        self.assertEqual(attack.attack_range, 40, "longer reach than default's 30")
        self.assertEqual(attack.cooldown, 0.4, "faster fire than default's 1.0")
        self.assertLess(attack.cooldown, 1.0)
        self.assertGreater(chase.detection_range, 100)

        # A player at distance 120 is OUTSIDE default detection (100) but INSIDE
        # aggressive detection (150) — proves the wider range is live, not cosmetic.
        enemy = MockEnemy(Vec(0, 0, 0), MockPlayer(Vec(120, 0, 0)))
        self.assertIs(tree.tick(enemy, DT), Status.RUNNING)
        self.assertEqual(len(enemy.chase_calls), 1, "aggressive enemy chases at 120u")
        default_tree = BehaviourTreeFactory.build("default", {})
        default_enemy = MockEnemy(Vec(0, 0, 0), MockPlayer(Vec(120, 0, 0)))
        self.assertIs(default_tree.tick(default_enemy, DT), Status.SUCCESS,
                      "default enemy ignores a player at 120u (out of its 100 range)")
        self.assertEqual(default_enemy.chase_calls, [])


class FakeClock:
    """Controllable stand-in for time.time() (same shape as the decorators
    suite's FakeClock). Installed onto BOTH time-reading modules — Cooldown
    lives in behaviour_tree, AttackNode in behaviour_nodes — so the preset's
    two wall-clock timers advance in lockstep."""

    def __init__(self, start=1000.0):
        self.now = start

    def time(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TestCautiousPreset(unittest.TestCase):
    """Pre-v1.6 closure pass: the 'cautious' preset — first preset to wire a
    decorator (Cooldown) into a shipping tree."""

    def setUp(self):
        self.clock = FakeClock()
        self._saved = (behaviour_tree_module._time, behaviour_nodes_module._time)
        behaviour_tree_module._time = self.clock
        behaviour_nodes_module._time = self.clock

    def tearDown(self):
        behaviour_tree_module._time, behaviour_nodes_module._time = self._saved

    def test_cautious_builds_cooldown_wrapped_attack(self):
        tree = BehaviourTreeFactory.build("cautious", {})
        self.assertIsInstance(tree, Selector)
        # Shape: Selector([Sequence([Chase, Cooldown(Attack)]), Idle]).
        self.assertEqual([type(c).__name__ for c in tree.children],
                         ["Sequence", "IdleNode"])
        chase, cooldown = tree.children[0].children
        self.assertIsInstance(chase, ChaseNode)
        self.assertIsInstance(cooldown, Cooldown)
        self.assertIsInstance(cooldown.child, AttackNode)
        # Engagement envelope pinned to the tuned constants (same as default).
        self.assertEqual((chase.detection_range, chase.stop_range), (100, 30))
        self.assertEqual((cooldown.child.attack_range, cooldown.child.cooldown),
                         (30, 1.0))
        self.assertEqual(cooldown.seconds, 3.0)

    def test_cautious_cooldown_gates_fire_rate(self):
        # In-range enemy: fires once, then the 3s Cooldown window replays the
        # resolved SUCCESS without re-ticking AttackNode — even after the
        # AttackNode's own 1.0s cooldown has elapsed.
        tree = BehaviourTreeFactory.build("cautious", {})
        enemy = MockEnemy(Vec(0, 0, 0), MockPlayer(Vec(10, 0, 0)))

        self.assertIs(tree.tick(enemy, DT), Status.SUCCESS)
        self.assertEqual(enemy.shoot_count, 1, "first in-range tick fires")

        self.clock.advance(1.5)   # past AttackNode's 1.0s, inside Cooldown's 3.0s
        self.assertIs(tree.tick(enemy, DT), Status.SUCCESS)
        self.assertEqual(enemy.shoot_count, 1,
                         "Cooldown window suppresses the second shot")

        self.clock.advance(2.0)   # 3.5s total — window elapsed
        self.assertIs(tree.tick(enemy, DT), Status.SUCCESS)
        self.assertEqual(enemy.shoot_count, 2, "fires again once the window elapses")

    def test_cautious_reevaluates_from_child_zero_each_tick(self):
        # Stateless re-evaluation: after a shot, if the player leaves detection
        # range, the NEXT tick must re-run ChaseNode (child 0), fail the
        # Sequence there, and fall through to Idle — without ever consulting
        # the Cooldown branch (no cursor resumes at the rate-limited attack).
        tree = BehaviourTreeFactory.build("cautious", {})
        player = MockPlayer(Vec(10, 0, 0))
        enemy = MockEnemy(Vec(0, 0, 0), player)

        tree.tick(enemy, DT)
        self.assertEqual(enemy.shoot_count, 1)

        player.position = Vec(500, 0, 0)          # leaves detection range
        self.clock.advance(10.0)                   # Cooldown window long gone
        self.assertIs(tree.tick(enemy, DT), Status.SUCCESS)  # Idle fallback
        self.assertEqual(enemy.shoot_count, 1,
                         "out-of-range tick must not reach the AttackNode")
        self.assertEqual(enemy.chase_calls, [],
                         "out of detection range — chase FAILs without stepping")


class TestUnknownPreset(unittest.TestCase):
    """OUTPUT item 6: unknown preset warns and returns the default tree."""

    def test_unknown_preset_warns_and_returns_default_structure(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            tree = BehaviourTreeFactory.build("totally_made_up", {})
        warning = buf.getvalue()
        self.assertIn("BehaviourTreeFactory", warning)
        self.assertIn("totally_made_up", warning)
        self.assertIn("default", warning.lower())

        # Confirm by STRUCTURE (not just no-raise) that it is the default tree.
        self.assertIsInstance(tree, Selector)
        self.assertEqual([type(c).__name__ for c in tree.children],
                         ["Sequence", "IdleNode"])
        chase, attack = tree.children[0].children
        self.assertEqual((chase.detection_range, chase.stop_range), (100, 30))
        self.assertEqual((attack.attack_range, attack.cooldown), (30, 1.0))
        self.assertEqual(_find(tree, PatrolNode), [], "default has no patrol")
        self.assertEqual(_find(tree, FleeNode), [], "default has no flee")

    def test_unknown_preset_does_not_raise(self):
        import io
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            # Must degrade, never raise — Step 7 spec.
            tree = BehaviourTreeFactory.build("", {})
            self.assertIsInstance(tree, Selector)

    def test_missing_config_keys_never_keyerror(self):
        import io
        import contextlib
        # Every preset with an empty config dict — none may KeyError.
        for preset in ("default", "flee_when_low", "aggressive", "cautious"):
            with self.subTest(preset=preset):
                tree = BehaviourTreeFactory.build(preset, {})
                self.assertIsInstance(tree, Selector)
        with contextlib.redirect_stdout(io.StringIO()):
            # patrol with empty config uses the no-Ursina seam.
            t = BehaviourTreeFactory.build("patrol_then_attack", {},
                                           waypoint_factory=_vec_waypoint)
            self.assertIsInstance(t, Selector)


class TestEnemyConstructorIntegration(unittest.TestCase):
    """OUTPUT items 7–8: real Enemy ctor uses the Factory default, and the
    behaviour_tree injection escape hatch bypasses it.

    Boots a headless Ursina app once. Skipped if a graphics context cannot be
    created (so the pure suites above still run on a display-less CI box).
    """

    app = None

    @classmethod
    def setUpClass(cls):
        try:
            from ursina import Ursina
            cls.app = Ursina()
        except Exception as exc:   # no display / no GL context — skip, don't fail
            raise unittest.SkipTest(f"Ursina app unavailable: {type(exc).__name__}: {exc}")

    def _dummy_player(self):
        from ursina import Vec3
        class _P:
            position = Vec3(0, 0, 0)
        return _P()

    def test_no_arg_constructor_uses_factory_default_selector(self):
        # item 7: Enemy() with no behaviour_tree -> Factory "default" -> a valid
        # Selector([Sequence([Chase, Attack]), Idle]).
        from ursina import Vec3
        from Scripts.enemy import Enemy
        enemy = Enemy(spawn_position=Vec3(5, 0, 0), player=self._dummy_player())
        try:
            self.assertIsInstance(enemy._tree, Selector)
            self.assertEqual([type(c).__name__ for c in enemy._tree.children],
                             ["Sequence", "IdleNode"])
            seq = enemy._tree.children[0]
            self.assertEqual([type(c).__name__ for c in seq.children],
                             ["ChaseNode", "AttackNode"])
            chase, attack = seq.children
            self.assertEqual((chase.detection_range, chase.stop_range), (100, 30))
            self.assertEqual((attack.attack_range, attack.cooldown), (30, 1.0))
        finally:
            enemy.die()

    def test_injected_tree_is_used_verbatim_not_factory(self):
        # item 8: Enemy(behaviour_tree=custom) -> _tree IS custom (escape hatch).
        from ursina import Vec3
        from Scripts.enemy import Enemy
        custom = IdleNode()
        enemy = Enemy(spawn_position=Vec3(8, 0, 0), player=self._dummy_player(),
                      behaviour_tree=custom)
        try:
            self.assertIs(enemy._tree, custom,
                          "injected tree must be used verbatim, not rebuilt by the Factory")
        finally:
            enemy.die()


if __name__ == "__main__":
    unittest.main(verbosity=2)
