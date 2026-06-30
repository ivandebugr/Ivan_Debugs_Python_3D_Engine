"""Unit tests for the Invert/Repeat/Cooldown decorators (v1.4 — Step 6).

Pure unit tests: no Ursina import, no app launch, no window. Mirrors the style
of tests/test_behaviour_tree.py — mock leaf nodes return scripted statuses and
record their own tick_count so "child was/was not ticked" can be asserted
directly.

Cooldown is time-based (wall-clock time.time(), matching AttackNode), so its
tests monkeypatch Scripts.behaviour_tree._time.time with a controllable fake
clock instead of sleeping.

Run with either:
    python3 -m unittest tests.test_behaviour_tree_decorators -v
    python3 -m pytest tests/test_behaviour_tree_decorators.py
"""

import unittest

from Scripts.behaviour_tree import (
    Status,
    BehaviourNode,
    Sequence,
    Invert,
    Repeat,
    Cooldown,
)
import Scripts.behaviour_tree as behaviour_tree_module


class ScriptedNode(BehaviourNode):
    """Mock leaf returning a fixed status; counts how often it was ticked."""

    def __init__(self, status: Status):
        self.status = status
        self.tick_count = 0

    def tick(self, enemy: object, dt: float) -> Status:
        self.tick_count += 1
        return self.status


class SequencedNode(BehaviourNode):
    """Mock that returns the next status from a list on each successive tick."""

    def __init__(self, statuses: list[Status]):
        self.statuses = list(statuses)
        self.tick_count = 0

    def tick(self, enemy: object, dt: float) -> Status:
        status = self.statuses[self.tick_count]
        self.tick_count += 1
        return status


ENEMY = object()
DT = 0.016


class FakeClock:
    """Controllable stand-in for time.time(), installed onto the module under test."""

    def __init__(self, start=1000.0):
        self.now = start

    def time(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TestInvert(unittest.TestCase):
    def test_success_becomes_failure(self):
        invert = Invert(ScriptedNode(Status.SUCCESS))
        self.assertEqual(invert.tick(ENEMY, DT), Status.FAILURE)

    def test_failure_becomes_success(self):
        invert = Invert(ScriptedNode(Status.FAILURE))
        self.assertEqual(invert.tick(ENEMY, DT), Status.SUCCESS)

    def test_running_passes_through(self):
        invert = Invert(ScriptedNode(Status.RUNNING))
        self.assertEqual(invert.tick(ENEMY, DT), Status.RUNNING)


class TestRepeat(unittest.TestCase):
    def test_n2_two_successes_returns_success_child_ticked_twice(self):
        child = ScriptedNode(Status.SUCCESS)
        repeat = Repeat(child, 2)
        self.assertEqual(repeat.tick(ENEMY, DT), Status.RUNNING)
        self.assertEqual(repeat.tick(ENEMY, DT), Status.SUCCESS)
        self.assertEqual(child.tick_count, 2)

    def test_n2_running_then_success_then_success_counter_ignores_running(self):
        child = SequencedNode([Status.RUNNING, Status.SUCCESS, Status.SUCCESS])
        repeat = Repeat(child, 2)
        self.assertEqual(repeat.tick(ENEMY, DT), Status.RUNNING)   # child RUNNING, no count
        self.assertEqual(repeat.tick(ENEMY, DT), Status.RUNNING)   # child SUCCESS #1
        self.assertEqual(repeat.tick(ENEMY, DT), Status.SUCCESS)   # child SUCCESS #2

    def test_n2_success_then_failure_propagates_failure_and_resets_counter(self):
        child = SequencedNode([Status.SUCCESS, Status.FAILURE])
        repeat = Repeat(child, 2)
        self.assertEqual(repeat.tick(ENEMY, DT), Status.RUNNING)
        self.assertEqual(repeat.tick(ENEMY, DT), Status.FAILURE)
        self.assertEqual(repeat._count, 0)

    def test_n_minus_one_infinite_never_returns_success(self):
        child = ScriptedNode(Status.SUCCESS)
        repeat = Repeat(child, -1)
        for _ in range(100):
            self.assertEqual(repeat.tick(ENEMY, DT), Status.RUNNING)
        self.assertEqual(child.tick_count, 100)

    def test_n0_returns_success_immediately_child_never_ticked(self):
        child = ScriptedNode(Status.SUCCESS)
        repeat = Repeat(child, 0)
        self.assertEqual(repeat.tick(ENEMY, DT), Status.SUCCESS)
        self.assertEqual(child.tick_count, 0)

    def test_counter_resets_after_success_and_restarts_cleanly(self):
        child = ScriptedNode(Status.SUCCESS)
        repeat = Repeat(child, 2)
        self.assertEqual(repeat.tick(ENEMY, DT), Status.RUNNING)
        self.assertEqual(repeat.tick(ENEMY, DT), Status.SUCCESS)
        # Next cycle starts fresh: first SUCCESS after reset is RUNNING again.
        self.assertEqual(repeat.tick(ENEMY, DT), Status.RUNNING)


class TestCooldown(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self._real_time = behaviour_tree_module._time.time
        behaviour_tree_module._time.time = self.clock.time

    def tearDown(self):
        behaviour_tree_module._time.time = self._real_time

    def test_first_tick_ticks_child_and_passes_through_result(self):
        child = ScriptedNode(Status.SUCCESS)
        cooldown = Cooldown(child, 1.0)
        self.assertEqual(cooldown.tick(ENEMY, DT), Status.SUCCESS)
        self.assertEqual(child.tick_count, 1)

    def test_second_tick_before_window_elapses_skips_child(self):
        child = ScriptedNode(Status.SUCCESS)
        cooldown = Cooldown(child, 1.0)
        cooldown.tick(ENEMY, DT)
        self.clock.advance(0.5)
        result = cooldown.tick(ENEMY, DT)
        self.assertEqual(result, Status.SUCCESS)
        self.assertEqual(child.tick_count, 1)  # not re-ticked

    def test_tick_after_window_elapses_ticks_child_again(self):
        child = ScriptedNode(Status.SUCCESS)
        cooldown = Cooldown(child, 1.0)
        cooldown.tick(ENEMY, DT)
        self.clock.advance(1.5)
        cooldown.tick(ENEMY, DT)
        self.assertEqual(child.tick_count, 2)

    def test_child_running_across_ticks_passes_through_timer_not_started(self):
        child = SequencedNode([Status.RUNNING, Status.RUNNING, Status.SUCCESS])
        cooldown = Cooldown(child, 1.0)
        self.assertEqual(cooldown.tick(ENEMY, DT), Status.RUNNING)
        self.clock.advance(0.01)  # no time gate should matter while RUNNING
        self.assertEqual(cooldown.tick(ENEMY, DT), Status.RUNNING)
        self.clock.advance(0.01)
        self.assertEqual(cooldown.tick(ENEMY, DT), Status.SUCCESS)
        self.assertEqual(child.tick_count, 3)
        # Now resolved — cooldown window starts from this resolution.
        result = cooldown.tick(ENEMY, DT)
        self.assertEqual(result, Status.SUCCESS)
        self.assertEqual(child.tick_count, 3)  # rate-limited, not re-ticked

    def test_seconds_zero_ticks_child_every_time(self):
        child = ScriptedNode(Status.SUCCESS)
        cooldown = Cooldown(child, 0)
        cooldown.tick(ENEMY, DT)
        cooldown.tick(ENEMY, DT)
        cooldown.tick(ENEMY, DT)
        self.assertEqual(child.tick_count, 3)

    def test_failure_during_cooldown_window_also_rate_limited(self):
        child = ScriptedNode(Status.FAILURE)
        cooldown = Cooldown(child, 1.0)
        self.assertEqual(cooldown.tick(ENEMY, DT), Status.FAILURE)
        self.clock.advance(0.5)
        self.assertEqual(cooldown.tick(ENEMY, DT), Status.FAILURE)
        self.assertEqual(child.tick_count, 1)


class TestComposition(unittest.TestCase):
    def test_invert_of_repeat_converts_success_to_failure_after_n_completions(self):
        child = ScriptedNode(Status.SUCCESS)
        tree = Invert(Repeat(child, 2))
        self.assertEqual(tree.tick(ENEMY, DT), Status.RUNNING)
        self.assertEqual(tree.tick(ENEMY, DT), Status.FAILURE)  # Repeat SUCCESS -> Invert FAILURE

    def test_cooldown_of_sequence_both_children_tick_first_call_skip_second(self):
        clock = FakeClock()
        real_time = behaviour_tree_module._time.time
        behaviour_tree_module._time.time = clock.time
        try:
            child_a = ScriptedNode(Status.SUCCESS)
            child_b = ScriptedNode(Status.SUCCESS)
            cooldown = Cooldown(Sequence([child_a, child_b]), 1.0)

            self.assertEqual(cooldown.tick(ENEMY, DT), Status.SUCCESS)
            self.assertEqual(child_a.tick_count, 1)
            self.assertEqual(child_b.tick_count, 1)

            clock.advance(0.1)
            self.assertEqual(cooldown.tick(ENEMY, DT), Status.SUCCESS)
            self.assertEqual(child_a.tick_count, 1)  # not re-ticked, still within window
            self.assertEqual(child_b.tick_count, 1)
        finally:
            behaviour_tree_module._time.time = real_time


if __name__ == '__main__':
    unittest.main()
