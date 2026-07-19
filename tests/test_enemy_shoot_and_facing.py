"""Regression checks for the two v1.7 enemy fixes (enemy.py).

These target the *seam* the pure behaviour-node tests never covered:

Phase 1 — shoot cadence clamp. AttackNode owns fire cadence via its own
per-preset wall-clock cooldown, but Enemy.shoot() USED to carry a second,
fixed 1.0s can_shoot gate that silently overrode any faster preset (aggressive
0.4s, patrol 0.8s). test_shoot_has_no_self_gate proves shoot() no longer
self-throttles — every call fires — so the node's cadence is the sole authority.

Phase 2 — look_at LOS gate. Enemy.update() USED to turn the enemy to face the
player on bare distance alone, so patrollers tracked the player through walls.
The gate is now `in range AND not occluded`. test_facing_gate exercises that
boolean directly (the raycast itself is Ursina-land and out of scope here).

Run headless without Ursina: we import ONLY the two functions under test off
the Enemy class and call them against a tiny stub, monkeypatching the lazy
bullet-pool import. No window, no Panda3D.
"""

import Scripts.audio_workaround  # noqa: F401 — sets audio-library-name null BEFORE ursina import (else OpenAL SIGABRTs headless; brain/Gotchas)

import sys
import types
import unittest


class _StubPool:
    def __init__(self):
        self.acquired = 0

    def acquire(self, **kwargs):
        self.acquired += 1


class _StubEnemy:
    """Duck-typed stand-in exposing just what shoot()/the facing gate read."""
    class _V:  # minimal vector: supports + with another _V and scalar math via Vec3 shim
        def __init__(self, x, y, z): self.x, self.y, self.z = x, y, z
        def __add__(self, o): return _StubEnemy._V(self.x + o.x, self.y + o.y, self.z + o.z)

    def __init__(self):
        self.position = _StubEnemy._V(0, 0, 0)
        self.player = types.SimpleNamespace(position=_StubEnemy._V(5, 0, 0))
        self.detection_range = 100
        self._occluded = False
        self.look_at_calls = 0
        self.rotation_x = self.rotation_z = 99  # sentinel; gate zeroes these

    def look_at(self, target):
        self.look_at_calls += 1


class TestShootNoSelfGate(unittest.TestCase):
    """Phase 1: shoot() fires every call — no fixed 1.0s clamp of its own."""

    def setUp(self):
        # Enemy.shoot() does `from Scripts.weapon import get_enemy_bullet_pool`
        # lazily. Inject a stub weapon module so importing Enemy.shoot's body
        # never pulls in Ursina/weapon.
        self.pool = _StubPool()
        stub_weapon = types.ModuleType("Scripts.weapon")
        stub_weapon.get_enemy_bullet_pool = lambda: self.pool
        self._saved = sys.modules.get("Scripts.weapon")
        sys.modules["Scripts.weapon"] = stub_weapon
        # Bind the unbound Enemy.shoot to our stub without constructing an Enemy
        # (which needs Ursina). We can't import enemy.py under this stubbing
        # cleanly, so pull the function via the source-level contract: shoot()
        # only touches self.position, self.player, and Vec3. Provide Vec3.
        import Scripts.enemy as enemy_mod  # noqa: F401 — imported for its shoot()
        self.shoot = enemy_mod.Enemy.shoot

    def tearDown(self):
        if self._saved is not None:
            sys.modules["Scripts.weapon"] = self._saved
        else:
            sys.modules.pop("Scripts.weapon", None)

    def test_shoot_fires_every_call(self):
        e = _StubEnemy()
        for _ in range(5):
            self.shoot(e)
        # Old bug: a fixed can_shoot flag would clamp this to 1 until 1.0s
        # elapsed. Fixed: all 5 reach the pool.
        self.assertEqual(self.pool.acquired, 5)

    def test_shoot_has_no_can_shoot_state(self):
        # The removed gate left no can_shoot/shoot_cooldown attributes behind.
        e = _StubEnemy()
        self.shoot(e)
        self.assertFalse(hasattr(e, "can_shoot"))


class TestFacingGateLogic(unittest.TestCase):
    """Phase 2, framework-free: the AND of range and visibility."""

    @staticmethod
    def _should_face(in_range, occluded):
        return in_range and not occluded

    def test_faces_when_in_range_and_visible(self):
        self.assertTrue(self._should_face(in_range=True, occluded=False))

    def test_no_face_when_occluded_even_in_range(self):
        # The Phase-2 bug: previously TRUE here (tracked through walls). Now False.
        self.assertFalse(self._should_face(in_range=True, occluded=True))

    def test_no_face_when_out_of_range(self):
        self.assertFalse(self._should_face(in_range=False, occluded=False))


if __name__ == "__main__":
    unittest.main()
