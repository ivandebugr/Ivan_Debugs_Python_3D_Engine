"""Canonical level data loader.

Single source of truth for reading/normalising level.json. Replaces four
near-identical parsers that had drifted apart (see docs/audit_v1.2.3.md #9).
Owns parsing only — Entity construction stays at the call sites because each
site builds a different entity shape (placeholder vs editor entity vs real
Enemy/Player).
"""
import json
from pathlib import Path

DEFAULT_POSITION   = [0, 0, 0]
DEFAULT_ROTATION   = [0, 0, 0]
DEFAULT_SCALE      = [1, 1, 1]
DEFAULT_COLOUR     = [1, 1, 1]
DEFAULT_TEXTURE    = 'white_cube'
DEFAULT_MODEL      = 'cube'
DEFAULT_HP         = 100
DEFAULT_ENEMY_TYPE = 'default'


def _normalise_entry(entry):
    out = {
        'type':     entry.get('type', 'block'),
        'position': list(entry.get('position', DEFAULT_POSITION)),
        'rotation': list(entry.get('rotation', DEFAULT_ROTATION)),
        'scale':    list(entry.get('scale',    DEFAULT_SCALE)),
        'colour':   list(entry.get('colour',   DEFAULT_COLOUR)),
        'texture':  entry.get('texture', DEFAULT_TEXTURE),
        # v1.3 Step 7: optional model field. Absent → 'cube' (built-in primitive,
        # the pre-step-7 behaviour for every block). Backwards-compatible: old
        # level.json files have no 'model' key and load as cube exactly as before.
        'model':    entry.get('model', DEFAULT_MODEL),
    }
    if out['type'] == 'enemy':
        out['hp']         = entry.get('hp', DEFAULT_HP)
        out['enemy_type'] = entry.get('enemy_type', DEFAULT_ENEMY_TYPE)
        out['rotation_y'] = entry.get('rotation_y', 0)
        # v1.4 Step 8: optional per-enemy behaviour-tree config. Absent → None
        # (Enemy.__init__ then builds the "default" preset). Surfaced HERE — the
        # single source of truth — so every read path (main.load_level,
        # editor load/snapshot) sees it identically and the editor's
        # play-in-editor snapshot, which re-parses saved dicts through this
        # function, preserves it instead of silently dropping it (the bug that
        # bit block 'rotation' — see brain/Gotchas.md).
        out['behaviour']  = entry.get('behaviour', None)
    if out['type'] == 'trigger':
        # v1.5 System A: invisible enter/exit volume. on_enter/on_exit are raw
        # action-dict lists (e.g. [{"action": "kill_plane"}]) stored verbatim —
        # the editor stashes them on its placeholder and the runtime factory
        # (main.start_game / editor _spawn_gameplay_from_snapshot) turns them into
        # live callbacks via trigger_system.build_actions(). Surfaced HERE, the
        # single parser, so every read path preserves them (same discipline as the
        # enemy 'behaviour' field above). Absent → empty lists, never None, so call
        # sites can iterate without a guard.
        out['on_enter'] = list(entry.get('on_enter', []))
        out['on_exit']  = list(entry.get('on_exit', []))
    return out


def load_level_data(source):
    """Return a list of normalised entity dicts.

    Accepts a file path (str/Path) or an already-parsed list (snapshot use).
    Every entry is guaranteed to have: type, position, rotation, scale,
    colour (0–1 floats), texture. Enemy entries additionally have: hp,
    enemy_type, rotation_y, behaviour (the raw behaviour-config dict or None).

    Raises FileNotFoundError when given a path that does not exist — caller
    is responsible for handling that case.
    """
    if isinstance(source, (str, Path)):
        with open(source, 'r') as f:
            raw = json.load(f)
    else:
        raw = source
    return [_normalise_entry(entry) for entry in raw]
