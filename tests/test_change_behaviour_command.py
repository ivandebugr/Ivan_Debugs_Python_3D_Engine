"""Unit tests for v1.4 Step 9 — ChangeBehaviourCommand + the editor's behaviour
config-mutation logic (preset switch, waypoint add/delete/edit).

Step 9 adds the level-editor inspector UI for per-enemy behaviour config. The
undo plumbing is ChangeBehaviourCommand (Scripts/undo_redo.py); the config
transforms live in LevelEditor (_on_preset_click / _on_add_waypoint /
_on_delete_waypoint / _on_waypoint_edit).

These are PURE tests: stdlib unittest, no Ursina, no window, no app.

  * Scripts/undo_redo.py imports headless (asset_registry is pure I/O), so
    ChangeBehaviourCommand is exercised DIRECTLY against fake entities + a fake
    editor (the command only calls editor._refresh_behaviour_ui(), which the fake
    counts).
  * Scripts/editor_core.py CANNOT be imported headless (heavy module-scope
    Ursina/app init). So the four config-building transforms are reproduced here
    as module-level helpers, copied VERBATIM from the LevelEditor methods. If a
    call site drifts from this logic, the matching test must be updated in
    lockstep (same contract as tests/test_level_behaviour_field.py).

Run with either:
    python3 -m unittest tests.test_change_behaviour_command -v
    python3 -m pytest tests/test_change_behaviour_command.py
"""

import unittest

from Scripts.undo_redo import ChangeBehaviourCommand, _copy_behaviour_config


# --- Test doubles ----------------------------------------------------------

class FakeEntity:
    """Stands in for an editor enemy placeholder — only behaviour_config matters."""
    def __init__(self, behaviour_config=None):
        self.behaviour_config = behaviour_config


class FakeEditor:
    """Minimal editor: ChangeBehaviourCommand only ever calls
    _refresh_behaviour_ui() on it. Count the calls so tests can assert the
    inspector is refreshed on execute/undo/redo (PART 1/PART 4)."""
    def __init__(self):
        self.refresh_calls = 0

    def _refresh_behaviour_ui(self):
        self.refresh_calls += 1


# --- Editor transforms, copied VERBATIM from LevelEditor (see module docstring) ---
# These mirror Scripts/editor_core.py exactly; they take the entity's current
# behaviour_config and a preset/waypoint intent and return the next config dict.

def _waypoints_of(cfg):
    """LevelEditor._waypoints_of body."""
    cfg = cfg or {}
    wps = cfg.get('waypoints')
    return wps if isinstance(wps, list) else []


def _new_config_for(current_cfg, tree=None, waypoints=None):
    """LevelEditor._new_config_for body (current_cfg is entity.behaviour_config)."""
    cfg = dict(current_cfg or {})
    if tree is not None:
        cfg['tree'] = tree
    if waypoints is not None:
        cfg['waypoints'] = [list(p) for p in waypoints]
    cfg.setdefault('tree', 'default')
    return cfg


def _preset_click_config(enemies_cfgs, preset):
    """LevelEditor._on_preset_click config-build (for the shared new_config)."""
    waypoints = None
    if preset == 'patrol_then_attack':
        existing = next((_waypoints_of(c) for c in enemies_cfgs if _waypoints_of(c)), None)
        waypoints = existing if existing else [[0, 1, 0]]
    return _new_config_for(enemies_cfgs[0], tree=preset, waypoints=waypoints)


def _add_waypoint_config(cfg):
    """LevelEditor._on_add_waypoint config-build."""
    waypoints = [list(p) for p in _waypoints_of(cfg)] + [[0, 1, 0]]
    return _new_config_for(cfg, tree='patrol_then_attack', waypoints=waypoints)


def _delete_waypoint_config(cfg, index):
    """LevelEditor._on_delete_waypoint config-build."""
    waypoints = [list(p) for p in _waypoints_of(cfg)]
    del waypoints[index]
    return _new_config_for(cfg, tree='patrol_then_attack', waypoints=waypoints)


# --- Tests -----------------------------------------------------------------

class TestChangeBehaviourCommand(unittest.TestCase):
    """Test 1: execute applies, undo restores, redo re-applies (single enemy)."""

    def test_execute_undo_redo_single_enemy(self):
        editor = FakeEditor()
        e = FakeEntity({'tree': 'default'})
        new = {'tree': 'aggressive'}
        cmd = ChangeBehaviourCommand(editor, [e], new)

        cmd.execute()
        self.assertEqual(e.behaviour_config, {'tree': 'aggressive'})

        cmd.undo()
        self.assertEqual(e.behaviour_config, {'tree': 'default'})

        cmd.execute()   # redo path on the stack re-runs execute()
        self.assertEqual(e.behaviour_config, {'tree': 'aggressive'})

        # Inspector refresh fired on every apply (execute + undo + redo = 3).
        self.assertEqual(editor.refresh_calls, 3)


class TestMultiEnemyIndependentSnapshots(unittest.TestCase):
    """Test 2: on multiple selected enemies each restores to its OWN prior
    config, not all to one shared snapshot."""

    def test_each_enemy_restores_independently(self):
        editor = FakeEditor()
        a = FakeEntity({'tree': 'default'})
        b = FakeEntity({'tree': 'flee_when_low', 'waypoints': [[1, 2, 3]]})
        new = {'tree': 'aggressive'}
        cmd = ChangeBehaviourCommand(editor, [a, b], new)

        cmd.execute()
        self.assertEqual(a.behaviour_config, {'tree': 'aggressive'})
        self.assertEqual(b.behaviour_config, {'tree': 'aggressive'})

        cmd.undo()
        # a goes back to default; b goes back to its OWN flee+waypoints config.
        self.assertEqual(a.behaviour_config, {'tree': 'default'})
        self.assertEqual(b.behaviour_config,
                         {'tree': 'flee_when_low', 'waypoints': [[1, 2, 3]]})

    def test_snapshot_does_not_alias_live_dict(self):
        # Mutating the live dict after construction must not corrupt the snapshot.
        editor = FakeEditor()
        live = {'tree': 'patrol_then_attack', 'waypoints': [[5, 1, 5]]}
        e = FakeEntity(live)
        cmd = ChangeBehaviourCommand(editor, [e], {'tree': 'default'})
        live['waypoints'][0][0] = 999          # mutate after snapshot taken
        cmd.execute()
        cmd.undo()
        self.assertEqual(e.behaviour_config['waypoints'], [[5, 1, 5]])


class TestPresetTransitions(unittest.TestCase):
    """Tests 3 & 4: preset switch waypoint seeding + retention rules."""

    def test_default_to_patrol_seeds_single_default_waypoint(self):
        # Test 3: default -> patrol_then_attack with no prior waypoints seeds [[0,1,0]].
        editor = FakeEditor()
        e = FakeEntity({'tree': 'default'})
        new = _preset_click_config([e.behaviour_config], 'patrol_then_attack')
        cmd = ChangeBehaviourCommand(editor, [e], new)
        cmd.execute()
        self.assertEqual(e.behaviour_config['tree'], 'patrol_then_attack')
        self.assertEqual(e.behaviour_config['waypoints'], [[0, 1, 0]])

    def test_patrol_to_default_retains_waypoints_key(self):
        # Test 4: patrol_then_attack -> default keeps the waypoints key, updates tree.
        editor = FakeEditor()
        e = FakeEntity({'tree': 'patrol_then_attack', 'waypoints': [[3, 1, 0], [7, 1, -4]]})
        new = _preset_click_config([e.behaviour_config], 'default')
        cmd = ChangeBehaviourCommand(editor, [e], new)
        cmd.execute()
        self.assertEqual(e.behaviour_config['tree'], 'default')
        self.assertIn('waypoints', e.behaviour_config)
        self.assertEqual(e.behaviour_config['waypoints'], [[3, 1, 0], [7, 1, -4]])

    def test_switch_to_patrol_preserves_existing_waypoints(self):
        # Re-selecting patrol after leaving it keeps the user's prior route.
        editor = FakeEditor()
        e = FakeEntity({'tree': 'default', 'waypoints': [[3, 1, 0], [7, 1, -4]]})
        new = _preset_click_config([e.behaviour_config], 'patrol_then_attack')
        cmd = ChangeBehaviourCommand(editor, [e], new)
        cmd.execute()
        self.assertEqual(e.behaviour_config['waypoints'], [[3, 1, 0], [7, 1, -4]])


class TestWaypointAddDelete(unittest.TestCase):
    """Tests 5 & 6: add grows by one, delete on 2-waypoint config drops to 1."""

    def test_add_waypoint_grows_by_one(self):
        editor = FakeEditor()
        e = FakeEntity({'tree': 'patrol_then_attack', 'waypoints': [[3, 1, 0]]})
        new = _add_waypoint_config(e.behaviour_config)
        cmd = ChangeBehaviourCommand(editor, [e], new)
        cmd.execute()
        self.assertEqual(len(e.behaviour_config['waypoints']), 2)
        self.assertEqual(e.behaviour_config['waypoints'][-1], [0, 1, 0])

    def test_add_waypoint_then_undo(self):
        editor = FakeEditor()
        e = FakeEntity({'tree': 'patrol_then_attack', 'waypoints': [[3, 1, 0]]})
        new = _add_waypoint_config(e.behaviour_config)
        cmd = ChangeBehaviourCommand(editor, [e], new)
        cmd.execute()
        cmd.undo()
        self.assertEqual(e.behaviour_config['waypoints'], [[3, 1, 0]])

    def test_delete_waypoint_on_two_drops_to_one(self):
        editor = FakeEditor()
        e = FakeEntity({'tree': 'patrol_then_attack', 'waypoints': [[3, 1, 0], [7, 1, -4]]})
        new = _delete_waypoint_config(e.behaviour_config, index=1)
        cmd = ChangeBehaviourCommand(editor, [e], new)
        cmd.execute()
        self.assertEqual(e.behaviour_config['waypoints'], [[3, 1, 0]])


class TestWaypointEdit(unittest.TestCase):
    """Coordinate edit applies and is undoable (one whole-dict snapshot)."""

    def test_edit_coordinate_and_undo(self):
        editor = FakeEditor()
        e = FakeEntity({'tree': 'patrol_then_attack', 'waypoints': [[3, 1, 0]]})
        # _on_waypoint_edit: copy, set [index][axis] = value, rebuild config.
        waypoints = [list(p) for p in _waypoints_of(e.behaviour_config)]
        waypoints[0][2] = -5.0
        new = _new_config_for(e.behaviour_config, tree='patrol_then_attack', waypoints=waypoints)
        cmd = ChangeBehaviourCommand(editor, [e], new)
        cmd.execute()
        self.assertEqual(e.behaviour_config['waypoints'], [[3, 1, -5.0]])
        cmd.undo()
        self.assertEqual(e.behaviour_config['waypoints'], [[3, 1, 0]])


class TestCopyHelper(unittest.TestCase):
    """_copy_behaviour_config deep-copies waypoints, passes None through."""

    def test_none_passthrough(self):
        self.assertIsNone(_copy_behaviour_config(None))

    def test_waypoints_deep_copied(self):
        src = {'tree': 'patrol_then_attack', 'waypoints': [[1, 2, 3]]}
        cp = _copy_behaviour_config(src)
        cp['waypoints'][0][0] = 99
        self.assertEqual(src['waypoints'][0][0], 1)   # original untouched


if __name__ == '__main__':
    unittest.main()
