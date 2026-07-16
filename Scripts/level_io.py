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
# v1.7 Step 4: directional sun defaults. These are the values main_menu() used to
# hardcode (main.py) before the sun became level data, so a level.json with no
# 'light' entry loads with exactly the lighting it had pre-v1.7.
DEFAULT_LIGHT_DIRECTION = [-0.6, -1.0, -0.4]
DEFAULT_LIGHT_COLOUR    = [1.0, 1.0, 1.0]
DEFAULT_LIGHT_INTENSITY = 1.0


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
        # v1.5 Step 4: optional door identity for blocks. A trigger's open_door
        # action resolves its target by matching this name at fire time. Absent →
        # '' (an unnamed block, the default for every pre-v1.5 block). Surfaced
        # HERE, the single parser, so every read path preserves it (same discipline
        # as 'model'/'behaviour'). Only blocks are ever doors, but the key is set
        # uniformly so call sites never need a type guard to read it.
        'door_name': entry.get('door_name', ''),
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
    if out['type'] == 'pickup':
        # v1.5 Step 13: weapon/ammo pickup. pickup_type is 'weapon' or 'ammo';
        # weapon_type is the WEAPON_TYPES key (weapon.py) the pickup grants/tops
        # up; amount only matters for pickup_type == 'ammo'. Same config-store
        # role as trigger on_enter/on_exit — the editor stashes this dict verbatim
        # on its placeholder and the runtime factory (main.start_game / editor
        # _spawn_gameplay_from_snapshot) builds a live AmmoPickup from it.
        out['pickup_type'] = entry.get('pickup_type', 'ammo')
        out['weapon_type'] = entry.get('weapon_type', 'pistol')
        out['amount']      = entry.get('amount', 30)
    if out['type'] == 'light':
        # v1.7 Step 4: the scene's directional sun, promoted from a hardcoded
        # DirectionalLight in main_menu() to level data so the editor can select,
        # aim and persist it. Surfaced HERE, the single parser, so every read path
        # (main.load_level, editor load, F5 snapshot) sees it identically — the
        # same discipline as enemy 'behaviour' / trigger on_enter.
        #
        # 'direction' is the vector the light points ALONG (sun -> scene), matching
        # the Vec3 main_menu() passed to sun.look_at(). It is deliberately separate
        # from the shared 'rotation' key above: rotation is a display transform the
        # gizmo writes, direction is what actually drives p3d_LightSource[0], and
        # sync_light_rotation() below is the one place they are reconciled.
        #
        # light_type is parsed but only 'directional' is honoured today. A Fable
        # scoping pass may add more types + a shadow toggle; this key exists so
        # those entries at least round-trip rather than silently degrading.
        out['light_type'] = entry.get('light_type', 'directional')
        out['direction']  = list(entry.get('direction', DEFAULT_LIGHT_DIRECTION))
        out['colour']     = list(entry.get('colour', DEFAULT_LIGHT_COLOUR))
        out['intensity']  = float(entry.get('intensity', DEFAULT_LIGHT_INTENSITY))
    return out


def default_light_entry():
    """The sun entry used when a level.json carries no 'light' of its own.

    Pre-v1.7 levels (every level.json written before the sun became editable) have
    no light entry at all. Rather than special-casing "no sun" at each of the three
    load sites, they call this to materialise the historical main_menu() defaults —
    so an old level and a freshly-saved one take exactly the same code path.
    """
    return _normalise_entry({'type': 'light'})


def sync_light_rotation(direction):
    """Euler rotation (degrees) that aims a light's -Z / forward down `direction`.

    The gizmo rotates entities by writing rotation_x/y/z, but p3d_LightSource[0] is
    driven by the light's direction vector — so the two representations must be kept
    in lockstep. This is the single conversion used by both the editor (to orient the
    sun proxy from saved data) and main.py (to aim the real DirectionalLight), so the
    editor's preview can't drift from what the game renders.

    Returns (0, 0, 0) for a degenerate zero-length direction rather than raising —
    a hand-edited level.json is the likely source and a dead sun beats a crash.
    """
    import math

    x, y, z = direction
    length = math.sqrt(x * x + y * y + z * z)
    if length < 1e-6:
        return [0.0, 0.0, 0.0]
    x, y, z = x / length, y / length, z / length
    # Panda3D/Ursina convention: heading (rotation_y) about +Y, pitch (rotation_x)
    # about +X, with +Z forward. Mirrors what look_at(direction) produced before.
    heading = math.degrees(math.atan2(x, z))
    pitch   = math.degrees(math.asin(-y))
    return [pitch, heading, 0.0]


def rotation_to_direction(rotation):
    """Inverse of sync_light_rotation: the direction a light with `rotation` points along.

    The rotation gizmo is the editor's aiming affordance, so after a ring drag the
    entity's rotation is authoritative and 'direction' must be recomputed from it
    before save. Round-trips with sync_light_rotation to within float precision.
    """
    import math

    pitch, heading = math.radians(rotation[0]), math.radians(rotation[1])
    cos_pitch = math.cos(pitch)
    return [
        math.sin(heading) * cos_pitch,
        -math.sin(pitch),
        math.cos(heading) * cos_pitch,
    ]


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
