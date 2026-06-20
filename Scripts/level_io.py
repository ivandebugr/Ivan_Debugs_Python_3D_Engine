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
    }
    if out['type'] == 'enemy':
        out['hp']         = entry.get('hp', DEFAULT_HP)
        out['enemy_type'] = entry.get('enemy_type', DEFAULT_ENEMY_TYPE)
        out['rotation_y'] = entry.get('rotation_y', 0)
    return out


def load_level_data(source):
    """Return a list of normalised entity dicts.

    Accepts a file path (str/Path) or an already-parsed list (snapshot use).
    Every entry is guaranteed to have: type, position, rotation, scale,
    colour (0–1 floats), texture. Enemy entries additionally have: hp,
    enemy_type, rotation_y.

    Raises FileNotFoundError when given a path that does not exist — caller
    is responsible for handling that case.
    """
    if isinstance(source, (str, Path)):
        with open(source, 'r') as f:
            raw = json.load(f)
    else:
        raw = source
    return [_normalise_entry(entry) for entry in raw]
