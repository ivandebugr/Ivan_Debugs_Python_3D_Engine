"""Unit tests for Scripts/behaviour_tree.py (v1.4 — Step 1).

Pure unit tests: no Ursina import, no app launch, no window. Compositor logic
is exercised with mock leaf nodes that return scripted statuses, and each mock
records how many times it was ticked so "later siblings not ticked" can be
asserted directly (rather than inferred from the return value).

Run with either:
    python3 -m unittest tests.test_behaviour_tree -v
    python3 -m pytest tests/test_behaviour_tree.py      # if pytest is installed
"""

import unittest

from Scripts.behaviour_tree import (
    Status,
    BehaviourNode,
    Sequence,
    Selector,
    Parallel,
)


class ScriptedNode(BehaviourNode):
    """Mock leaf returning a fixed status; counts how often it was ticked.

    ``tick_count`` lets tests prove a sibling was (or was not) reached, which
    is stronger than only checking the compositor's aggregate return value.
    """

    def __init__(self, status: Status):
        self.status = status
        self.tick_count = 0

    def tick(self, enemy: object, dt: float) -> Status:
        self.tick_count += 1
        return self.status


class AlternatingNode(BehaviourNode):
    """Mock that yields a scripted sequence of statuses across successive ticks.

    Cycles through ``statuses`` one per tick; used to prove a compositor
    re-evaluates from child 0 on each fresh tick rather than resuming.
    """

    def __init__(self, statuses: list[Status]):
        self.statuses = statuses
        self.tick_count = 0

    def tick(self, enemy: object, dt: float) -> Status:
        status = self.statuses[self.tick_count % len(self.statuses)]
        self.tick_count += 1
        return status


# The enemy/dt arguments are never inspected by compositors, only forwarded.
ENEMY = object()
DT = 0.016


class TestSequence(unittest.TestCase):
    def test_all_success_returns_success(self):
        # Case 1
        a, b, c = (ScriptedNode(Status.SUCCESS) for _ in range(3))
        result = Sequence([a, b, c]).tick(ENEMY, DT)
        self.assertIs(result, Status.SUCCESS)
        self.assertEqual([a.tick_count, b.tick_count, c.tick_count], [1, 1, 1])

    def test_middle_failure_aborts_and_skips_later(self):
        # Case 2
        a = ScriptedNode(Status.SUCCESS)
        b = ScriptedNode(Status.FAILURE)
        c = ScriptedNode(Status.SUCCESS)
        result = Sequence([a, b, c]).tick(ENEMY, DT)
        self.assertIs(result, Status.FAILURE)
        self.assertEqual(a.tick_count, 1)
        self.assertEqual(b.tick_count, 1)
        self.assertEqual(c.tick_count, 0, "later sibling must not be ticked after FAILURE")

    def test_middle_running_aborts_and_skips_later(self):
        # Case 3
        a = ScriptedNode(Status.SUCCESS)
        b = ScriptedNode(Status.RUNNING)
        c = ScriptedNode(Status.SUCCESS)
        result = Sequence([a, b, c]).tick(ENEMY, DT)
        self.assertIs(result, Status.RUNNING)
        self.assertEqual(a.tick_count, 1)
        self.assertEqual(b.tick_count, 1)
        self.assertEqual(c.tick_count, 0, "later sibling must not be ticked after RUNNING")

    def test_empty_sequence_returns_success(self):
        # Case 4 — chosen empty-case value: SUCCESS (identity of AND)
        self.assertIs(Sequence([]).tick(ENEMY, DT), Status.SUCCESS)

    def test_single_child_behaves_as_child(self):
        for st in (Status.SUCCESS, Status.FAILURE, Status.RUNNING):
            with self.subTest(status=st):
                self.assertIs(Sequence([ScriptedNode(st)]).tick(ENEMY, DT), st)


class TestSelector(unittest.TestCase):
    def test_all_failure_returns_failure(self):
        # Case 5
        a, b, c = (ScriptedNode(Status.FAILURE) for _ in range(3))
        result = Selector([a, b, c]).tick(ENEMY, DT)
        self.assertIs(result, Status.FAILURE)
        self.assertEqual([a.tick_count, b.tick_count, c.tick_count], [1, 1, 1])

    def test_first_success_short_circuits(self):
        # Case 6
        a = ScriptedNode(Status.SUCCESS)
        b = ScriptedNode(Status.FAILURE)
        c = ScriptedNode(Status.SUCCESS)
        result = Selector([a, b, c]).tick(ENEMY, DT)
        self.assertIs(result, Status.SUCCESS)
        self.assertEqual(a.tick_count, 1)
        self.assertEqual(b.tick_count, 0, "later sibling must not be ticked after SUCCESS")
        self.assertEqual(c.tick_count, 0, "later sibling must not be ticked after SUCCESS")

    def test_middle_running_short_circuits(self):
        # Case 7
        a = ScriptedNode(Status.FAILURE)
        b = ScriptedNode(Status.RUNNING)
        c = ScriptedNode(Status.SUCCESS)
        result = Selector([a, b, c]).tick(ENEMY, DT)
        self.assertIs(result, Status.RUNNING)
        self.assertEqual(a.tick_count, 1)
        self.assertEqual(b.tick_count, 1)
        self.assertEqual(c.tick_count, 0, "later sibling must not be ticked after RUNNING")

    def test_empty_selector_returns_failure(self):
        # Case 8 — chosen empty-case value: FAILURE (identity of OR)
        self.assertIs(Selector([]).tick(ENEMY, DT), Status.FAILURE)

    def test_single_child_behaves_as_child(self):
        for st in (Status.SUCCESS, Status.FAILURE, Status.RUNNING):
            with self.subTest(status=st):
                self.assertIs(Selector([ScriptedNode(st)]).tick(ENEMY, DT), st)


class TestParallel(unittest.TestCase):
    def test_no_short_circuit_ticks_every_child(self):
        # Case 9a — proves Parallel ticks ALL children even past an early
        # FAILURE and an early SUCCESS (no short-circuit in any direction).
        a = ScriptedNode(Status.FAILURE)
        b = ScriptedNode(Status.SUCCESS)
        c = ScriptedNode(Status.RUNNING)
        Parallel([a, b, c]).tick(ENEMY, DT)
        self.assertEqual([a.tick_count, b.tick_count, c.tick_count], [1, 1, 1],
                         "Parallel must tick every child regardless of results")

    def test_success_only_when_all_succeed(self):
        # Case 9b
        children = [ScriptedNode(Status.SUCCESS) for _ in range(3)]
        self.assertIs(Parallel(children).tick(ENEMY, DT), Status.SUCCESS)

    def test_not_success_when_any_non_success(self):
        # Case 9b (negative) — one failure => not SUCCESS
        children = [ScriptedNode(Status.SUCCESS),
                    ScriptedNode(Status.FAILURE),
                    ScriptedNode(Status.SUCCESS)]
        self.assertIsNot(Parallel(children).tick(ENEMY, DT), Status.SUCCESS)

    def test_running_when_any_running_and_none_failed(self):
        # Documents the chosen aggregate: RUNNING dominates over plain SUCCESS.
        children = [ScriptedNode(Status.SUCCESS), ScriptedNode(Status.RUNNING)]
        self.assertIs(Parallel(children).tick(ENEMY, DT), Status.RUNNING)

    def test_failure_when_failed_and_none_running(self):
        children = [ScriptedNode(Status.SUCCESS), ScriptedNode(Status.FAILURE)]
        self.assertIs(Parallel(children).tick(ENEMY, DT), Status.FAILURE)

    def test_empty_parallel_returns_success(self):
        self.assertIs(Parallel([]).tick(ENEMY, DT), Status.SUCCESS)


class TestNested(unittest.TestCase):
    def test_selector_of_sequence_resolves_correctly(self):
        # Case 10 — Selector[ Sequence[mock, mock], mock ]
        # Inner sequence fails on its 2nd child, so the selector falls through
        # to the trailing fallback mock, which succeeds.
        seq_a = ScriptedNode(Status.SUCCESS)
        seq_b = ScriptedNode(Status.FAILURE)
        fallback = ScriptedNode(Status.SUCCESS)
        tree = Selector([Sequence([seq_a, seq_b]), fallback])
        self.assertIs(tree.tick(ENEMY, DT), Status.SUCCESS)
        self.assertEqual(seq_a.tick_count, 1)
        self.assertEqual(seq_b.tick_count, 1)
        self.assertEqual(fallback.tick_count, 1, "fallback must run after inner sequence fails")

    def test_inner_sequence_success_short_circuits_selector(self):
        # Same shape, but the inner sequence succeeds => fallback never runs.
        seq_a = ScriptedNode(Status.SUCCESS)
        seq_b = ScriptedNode(Status.SUCCESS)
        fallback = ScriptedNode(Status.SUCCESS)
        tree = Selector([Sequence([seq_a, seq_b]), fallback])
        self.assertIs(tree.tick(ENEMY, DT), Status.SUCCESS)
        self.assertEqual(fallback.tick_count, 0, "fallback must be skipped when inner sequence succeeds")

    def test_deeply_nested_parallel_inside_sequence_inside_selector(self):
        # Selector[ Sequence[ Parallel[S, S], S ] ] — no special-casing needed.
        inner = Selector([Sequence([Parallel([ScriptedNode(Status.SUCCESS),
                                              ScriptedNode(Status.SUCCESS)]),
                                    ScriptedNode(Status.SUCCESS)])])
        self.assertIs(inner.tick(ENEMY, DT), Status.SUCCESS)


class TestRunningReEvaluation(unittest.TestCase):
    def test_reevaluates_from_child_zero_each_tick(self):
        # Case 11 — a child alternates RUNNING -> SUCCESS across ticks.
        # Per the documented convention, the Sequence starts from child 0 on
        # every tick; it does NOT cache "child 1 was running" and resume there.
        first = ScriptedNode(Status.SUCCESS)
        alternating = AlternatingNode([Status.RUNNING, Status.SUCCESS])
        last = ScriptedNode(Status.SUCCESS)
        seq = Sequence([first, alternating, last])

        # Tick 1: child 0 SUCCESS, child 1 RUNNING -> Sequence RUNNING, child 2 skipped.
        self.assertIs(seq.tick(ENEMY, DT), Status.RUNNING)
        self.assertEqual(first.tick_count, 1)
        self.assertEqual(alternating.tick_count, 1)
        self.assertEqual(last.tick_count, 0)

        # Tick 2: child 0 ticked AGAIN (proves restart from 0), child 1 now
        # SUCCESS, child 2 reached -> Sequence SUCCESS.
        self.assertIs(seq.tick(ENEMY, DT), Status.SUCCESS)
        self.assertEqual(first.tick_count, 2, "child 0 must be re-ticked — no resume-from-running")
        self.assertEqual(alternating.tick_count, 2)
        self.assertEqual(last.tick_count, 1)


class TestAbstractEnforcement(unittest.TestCase):
    def test_subclass_without_tick_fails_loudly(self):
        # Case 12 — a BehaviourNode subclass that forgets to override tick()
        # must fail loudly. With ABC + @abstractmethod it cannot even be
        # instantiated, so the omission surfaces at construction, not as a
        # silent None return at runtime.
        class BrokenNode(BehaviourNode):
            pass  # no tick() override

        with self.assertRaises(TypeError):
            BrokenNode()

    def test_base_class_cannot_be_instantiated(self):
        with self.assertRaises(TypeError):
            BehaviourNode()


if __name__ == "__main__":
    unittest.main(verbosity=2)
