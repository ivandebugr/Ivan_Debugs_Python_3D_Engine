"""Unit tests for v1.4 Step 8 — level.json "behaviour" field load/save plumbing.

Step 8 wires an optional per-enemy ``"behaviour"`` config dict through three
surfaces (no inspector UI — that's Step 9):

  1. runtime load  — main.py: load_level() stashes the raw config on the
     level_enemy placeholder; start_game() builds the tree via
     BehaviourTreeFactory and passes behaviour_tree= to Enemy().
  2. editor load   — editor_core.py load_existing_level() stores the raw
     config on the placeholder as ``.behaviour_config`` (NO tree built).
  3. editor save   — editor_core.py _build_level_data() writes a "behaviour"
     key IFF the enemy carries a non-empty ``.behaviour_config``.

All three read paths consume Scripts/level_io.load_level_data(), whose
``_normalise_entry`` is the single source of truth — it now emits
``behaviour`` (raw dict or None) for enemy entries. That central surfacing is
what keeps the editor's play-in-editor snapshot round-trip from silently
dropping the field (the mechanism behind the old block-`rotation` bug).

These are PURE tests: stdlib unittest, no Ursina, no window, no app.

  * main.py and level_editor.py both run heavy module-scope Ursina/app init on
    import, so they cannot be imported headless. Instead we exercise the EXACT
    logic each one runs, against the REAL ``level_io`` parser and the REAL
    ``BehaviourTreeFactory`` (mocked only where a test must assert the precise
    build() call args). The expressions below are copied verbatim from the
    Step-8 edits and are labelled as such; if either call site drifts from this
    logic, the matching test here should be updated in lockstep.

Run with either:
    python3 -m unittest tests.test_level_behaviour_field -v
    python3 -m pytest tests/test_level_behaviour_field.py
"""

import unittest
from unittest import mock

from Scripts.level_io import load_level_data
from Scripts.behaviour_tree_factory import BehaviourTreeFactory
from Scripts.behaviour_tree import Selector, Sequence
from Scripts.behaviour_nodes import AttackNode, ChaseNode, IdleNode, PatrolNode


# A no-Ursina waypoint stub, so patrol presets never import Vec3 in these tests.
class Vec(tuple):
    def __new__(cls, x, y, z):
        return super().__new__(cls, (x, y, z))


def _vec_waypoint(x, y, z):
    return Vec(x, y, z)


def _flatten(node):
    found = [node]
    for child in getattr(node, "children", []):
        found.extend(_flatten(child))
    child = getattr(node, "child", None)
    if child is not None:
        found.extend(_flatten(child))
    return found


def _find(node, node_type):
    return [n for n in _flatten(node) if isinstance(n, node_type)]


# --------------------------------------------------------------------------- #
# Runtime build logic — verbatim copy of main.start_game()'s Step-8 expression #
# --------------------------------------------------------------------------- #
def _runtime_build_behaviour_tree(placeholder_config, factory=BehaviourTreeFactory,
                                  waypoint_factory=None):
    """Mirror of the behaviour-tree build in main.start_game().

    main.start_game() runs, per enemy placeholder:

        behaviour_tree = None
        config = getattr(placeholder, 'behaviour_config', None)
        if config:
            behaviour_tree = BehaviourTreeFactory.build(config.get('tree','default'), config)
        enemy = Enemy(..., behaviour_tree=behaviour_tree)

    ``placeholder_config`` here is what load_level() stashed:
    entry['behaviour'] (the raw dict, or None). ``waypoint_factory`` is only
    threaded so a real-Factory patrol case can avoid importing Vec3; production
    never passes it (the Factory's build() signature defaults it to None).
    """
    behaviour_tree = None
    config = placeholder_config
    if config:
        if waypoint_factory is None:
            behaviour_tree = factory.build(config.get('tree', 'default'), config)
        else:
            behaviour_tree = factory.build(config.get('tree', 'default'), config,
                                           waypoint_factory=waypoint_factory)
    return behaviour_tree


# --------------------------------------------------------------------------- #
# Save logic — verbatim copy of _build_level_data()'s Step-8 enemy branch      #
# --------------------------------------------------------------------------- #
class _StubEnemy:
    """Minimal stand-in for an editor enemy placeholder.

    _build_level_data() reads exactly these attributes off an enemy:
    x/y/z, enemy_hp, enemy_type, rotation_y, behaviour_config. We set whatever a
    test needs; getattr() defaults in the serializer cover the rest.
    """

    def __init__(self, x=0.0, y=0.0, z=0.0, enemy_hp=100, enemy_type='default',
                 rotation_y=0.0, behaviour_config=None, set_behaviour_attr=True):
        self.x, self.y, self.z = x, y, z
        self.enemy_hp = enemy_hp
        self.enemy_type = enemy_type
        self.rotation_y = rotation_y
        # A freshly drag-placed enemy never gets behaviour_config assigned at
        # all; _build_level_data uses getattr(..., None). set_behaviour_attr
        # lets us reproduce both "attr absent" and "attr present" cases.
        if set_behaviour_attr:
            self.behaviour_config = behaviour_config


def _serialize_enemy(enemy):
    """Verbatim copy of _build_level_data()'s per-enemy serialization (Step 8).

    Kept in lockstep with Scripts/editor_core.py _build_level_data() — see the
    module docstring's "copied verbatim" note.
    """
    enemy_data = {
        'type': 'enemy',
        'position': [enemy.x, enemy.y, enemy.z],
        'hp': getattr(enemy, 'enemy_hp', 100),
        'enemy_type': getattr(enemy, 'enemy_type', 'default'),
        'rotation_y': round(enemy.rotation_y, 2),
    }
    behaviour_config = getattr(enemy, 'behaviour_config', None)
    if behaviour_config:
        enemy_data['behaviour'] = behaviour_config
    return enemy_data


# --------------------------------------------------------------------------- #
# Tests                                                                        #
# --------------------------------------------------------------------------- #
class TestNormaliseSurfacesBehaviour(unittest.TestCase):
    """The single-source-of-truth parser surfaces the field for every reader."""

    def test_enemy_with_behaviour_surfaced(self):
        out = load_level_data([{'type': 'enemy', 'position': [1, 2, 3],
                                'behaviour': {'tree': 'aggressive'}}])
        self.assertEqual(out[0]['behaviour'], {'tree': 'aggressive'})

    def test_enemy_without_behaviour_is_none_but_key_present(self):
        out = load_level_data([{'type': 'enemy', 'position': [1, 2, 3]}])
        # Key always present for enemies (so call sites can entry['behaviour']),
        # value None when absent in the source.
        self.assertIn('behaviour', out[0])
        self.assertIsNone(out[0]['behaviour'])

    def test_block_has_no_behaviour_key(self):
        out = load_level_data([{'type': 'block', 'position': [0, 0, 0]}])
        self.assertNotIn('behaviour', out[0])


class TestRuntimeLoadBuildsTree(unittest.TestCase):
    """OUTPUT items 1–4: runtime load path (main.load_level + start_game)."""

    # ---- item 1: behaviour present -> Factory.build called with right args --
    def test_behaviour_present_calls_factory_with_preset_and_config(self):
        entry = load_level_data([{'type': 'enemy', 'position': [0, 0, 0],
                                  'behaviour': {'tree': 'aggressive'}}])[0]
        fake_tree = object()
        fake_factory = mock.Mock()
        fake_factory.build.return_value = fake_tree

        tree = _runtime_build_behaviour_tree(entry['behaviour'], factory=fake_factory)

        fake_factory.build.assert_called_once_with('aggressive',
                                                   {'tree': 'aggressive'})
        self.assertIs(tree, fake_tree)

    # ---- item 2: no behaviour field -> default tree, Factory NOT called ------
    def test_no_behaviour_field_does_not_call_factory(self):
        # main.start_game() passes behaviour_tree=None; Enemy.__init__ then builds
        # the "default" preset itself. So at the start_game() layer the Factory is
        # NOT called and the kwarg is None — verified here.
        entry = load_level_data([{'type': 'enemy', 'position': [0, 0, 0]}])[0]
        fake_factory = mock.Mock()

        tree = _runtime_build_behaviour_tree(entry['behaviour'], factory=fake_factory)

        fake_factory.build.assert_not_called()
        self.assertIsNone(tree)

    def test_no_behaviour_field_enemy_constructor_would_build_default(self):
        # Companion to the above: the None we hand Enemy() is exactly what makes
        # Enemy.__init__ fall to BehaviourTreeFactory.build("default", {}). We
        # assert the real default tree shape here so item 2 is end-to-end honest.
        tree = BehaviourTreeFactory.build('default', {})
        self.assertIsInstance(tree, Selector)
        self.assertEqual([type(c).__name__ for c in tree.children],
                         ['Sequence', 'IdleNode'])

    # ---- item 3: unknown preset -> warning, default fallback -----------------
    def test_unknown_preset_falls_back_to_default(self):
        entry = load_level_data([{'type': 'enemy', 'position': [0, 0, 0],
                                  'behaviour': {'tree': 'no_such_preset'}}])[0]
        # Real Factory: its own Step-7 fallback logs and returns the default tree.
        with mock.patch('builtins.print') as mock_print:
            tree = _runtime_build_behaviour_tree(entry['behaviour'])
        # Warned about the unknown preset...
        self.assertTrue(mock_print.called)
        self.assertIn('unknown preset', ' '.join(str(c) for c in mock_print.call_args[0]).lower())
        # ...and produced the default Selector([Sequence([Chase, Attack]), Idle]).
        self.assertIsInstance(tree, Selector)
        self.assertEqual([type(c).__name__ for c in tree.children],
                         ['Sequence', 'IdleNode'])
        self.assertEqual([type(c).__name__ for c in tree.children[0].children],
                         ['ChaseNode', 'AttackNode'])

    # ---- item 4: patrol_then_attack -> waypoints passed through as raw lists -
    def test_patrol_waypoints_passed_through_as_raw_lists(self):
        raw_waypoints = [[3, 1, 0], [7, 1, 0], [7, 1, -4]]
        entry = load_level_data([{'type': 'enemy', 'position': [0, 0, 0],
                                  'behaviour': {'tree': 'patrol_then_attack',
                                                'waypoints': raw_waypoints}}])[0]

        # The config handed to the Factory must still contain RAW nested lists —
        # main.py must NOT pre-convert to Vec3 (the Factory does that internally).
        fake_factory = mock.Mock()
        _runtime_build_behaviour_tree(entry['behaviour'], factory=fake_factory)
        preset, config = fake_factory.build.call_args[0]
        self.assertEqual(preset, 'patrol_then_attack')
        self.assertEqual(config['waypoints'], raw_waypoints)
        self.assertIsInstance(config['waypoints'][0], list)  # still a list, not Vec3

        # And with the REAL Factory (Vec3 substituted), the patrol branch + node
        # is built and waypoints land in order.
        tree = _runtime_build_behaviour_tree(entry['behaviour'],
                                             waypoint_factory=_vec_waypoint)
        self.assertIsInstance(tree, Selector)
        patrols = _find(tree, PatrolNode)
        self.assertEqual(len(patrols), 1)
        self.assertEqual(list(patrols[0].waypoints),
                         [Vec(3, 1, 0), Vec(7, 1, 0), Vec(7, 1, -4)])


class TestSaveSerializesBehaviour(unittest.TestCase):
    """OUTPUT items 5–6: editor save path (_build_level_data enemy branch)."""

    # ---- item 5: enemy WITH behaviour_config -> "behaviour" key present ------
    def test_enemy_with_config_writes_behaviour_key(self):
        cfg = {'tree': 'patrol_then_attack', 'waypoints': [[3, 1, 0], [7, 1, 0]]}
        enemy = _StubEnemy(x=5.0, y=2.0, z=1.0, behaviour_config=cfg)
        out = _serialize_enemy(enemy)
        self.assertIn('behaviour', out)
        self.assertEqual(out['behaviour'], cfg)

    # ---- item 6: enemy WITHOUT behaviour_config -> key omitted ---------------
    def test_enemy_with_none_config_omits_behaviour_key(self):
        enemy = _StubEnemy(behaviour_config=None)
        out = _serialize_enemy(enemy)
        self.assertNotIn('behaviour', out)

    def test_freshly_placed_enemy_no_attr_omits_behaviour_key(self):
        # Drag-placed enemies never get behaviour_config assigned; getattr default
        # must still omit the key (no AttributeError, no null write).
        enemy = _StubEnemy(set_behaviour_attr=False)
        self.assertFalse(hasattr(enemy, 'behaviour_config'))
        out = _serialize_enemy(enemy)
        self.assertNotIn('behaviour', out)

    def test_empty_dict_config_omits_behaviour_key(self):
        # {} is falsy -> omit, so no spurious "behaviour": {} churn.
        enemy = _StubEnemy(behaviour_config={})
        out = _serialize_enemy(enemy)
        self.assertNotIn('behaviour', out)


class TestFullRoundTrip(unittest.TestCase):
    """OUTPUT item 7: serialize -> parse -> the behaviour config is preserved."""

    def test_patrol_then_attack_round_trip(self):
        cfg = {'tree': 'patrol_then_attack',
               'waypoints': [[3, 1, 0], [7, 1, 0], [7, 1, -4]]}
        enemy = _StubEnemy(x=5.0, y=2.0, z=1.0, rotation_y=90.0, enemy_hp=120,
                           behaviour_config=cfg)

        # Save side (editor) -> a level.json-shaped list of dicts.
        serialized = [_serialize_enemy(enemy)]

        # Load side (the REAL canonical parser every read path uses).
        parsed = load_level_data(serialized)
        self.assertEqual(parsed[0]['behaviour'], cfg)

        # And the runtime builder turns that config into the patrol tree.
        tree = _runtime_build_behaviour_tree(parsed[0]['behaviour'],
                                             waypoint_factory=_vec_waypoint)
        self.assertIsInstance(tree, Selector)
        self.assertEqual([type(c).__name__ for c in tree.children],
                         ['Sequence', 'PatrolNode', 'IdleNode'])

    def test_pre_v14_enemy_round_trips_without_behaviour_key(self):
        # An enemy saved with no custom behaviour must produce no behaviour key,
        # and re-parsing yields behaviour=None -> default tree (no Factory call).
        enemy = _StubEnemy(x=1.0, y=0.0, z=2.0, enemy_hp=100)
        serialized = [_serialize_enemy(enemy)]
        self.assertNotIn('behaviour', serialized[0])

        parsed = load_level_data(serialized)
        self.assertIsNone(parsed[0]['behaviour'])

        fake_factory = mock.Mock()
        tree = _runtime_build_behaviour_tree(parsed[0]['behaviour'], factory=fake_factory)
        fake_factory.build.assert_not_called()
        self.assertIsNone(tree)


if __name__ == '__main__':
    unittest.main()
